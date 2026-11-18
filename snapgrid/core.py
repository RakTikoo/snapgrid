"""Core grid-recovery logic for snapgrid.

The damaged image is assumed to be a clean pixel-art sprite (constant
colour per cell on a uniform grid) that went through a smooth upscale
(bilinear/bicubic), a nearest upscale, or mild blur/noise.  Recovery
(v2 detector):

1.  **Ungated nomination from the edges.**  Every cell-count pair whose
    x/y pitches agree (damaged pixel art has square cells: one upscale
    factor applied to both axes) is a candidate, plus the pairs the
    legacy per-axis comb/FFT/profile scan favours (for non-square
    pitches).  No profile-fit gate eliminates counts before ranking --
    that gate was the v1 detector's main failure mode.
2.  **Description-length arbitration with phase evidence.**  Each
    candidate is scored by how well the whole image is explained as
    snapped cells re-damaged by a forward model (crisp = identity,
    smooth = triangle blur), expressed as a description length:
    residual bits (Laplace entropy of the forward-model error) plus a
    per-cell colour cost, MINUS the phase evidence -- how much worse
    the explanation gets when every cut is shifted by half a cell.
    A true grid is uniquely sensitive to that shift; finer, coarser
    and off-by-one grids are not.  Finalists are re-scored exactly
    (median sampling instead of integral-image means) with an extra
    palette term: a true grid's snapped cells need the fewest distinct
    colour groups.
3.  **Abstention.**  Weak phase evidence, a near-tie with a different
    grid, a high forward-model error, a winner whose pitch an axis's
    own breakpoint lattice or a coarser same-axis challenger refutes
    (the non-uniform-resize verification), a winner on a very small
    image whose pitch an axis's own breakpoint lattice cannot
    positively confirm (the small-image guard), or a frame that
    disagrees with the shared grid (sheet mode) all raise
    :class:`AbstainError` instead of emitting a wrong grid.

The legacy per-axis machinery (profile fits, comb, FFT prominence) is
kept for populating estimate metadata for the winning count.

Everything is classical signal processing on the CPU -- no ML.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PIL import Image


class SnapError(Exception):
    """Clean, user-facing error (bad input). Exit code 2."""


class AbstainError(Exception):
    """No trustworthy grid found. Exit code 3."""


MIN_DIM = 8
MIN_CELL_PX = 1.8
GATE_REL = 1.6
GATE_ABS = 1.2
CONF_THRESHOLD = 0.38
# Detail-heavy sprites (every cell a different colour) legitimately sit
# at 11-21 under the forward model — their ramps are the image — so the
# veto only fires on errors no damaged sprite in the suite produces.
FIT_VETO = 25.0


@dataclass
class AxisEstimate:
    cells: int
    offset: int
    score: float          # comb score + 0.05 * FFT prominence
    fit: float            # profile fit error (lower is better)
    prominence: float     # FFT prominence of the winning count
    comb: float           # max(c1, c2) comb concentration
    bounds: tuple[int, ...]


@dataclass
class GridEstimate:
    nx: int
    ny: int
    confidence: float
    fit_error: float      # 2-D forward-model resynthesis error
    x: AxisEstimate
    y: AxisEstimate
    crisp: bool = False   # True when the crisp (nearest) damage model won


def load_rgba(path: str) -> np.ndarray:
    """Load an image file as an (H, W, 4) uint8 RGBA array."""
    try:
        with Image.open(path) as im:
            im.load()
            rgba = im.convert("RGBA")
    except Exception as exc:  # unreadable / not an image
        raise SnapError(f"cannot read image: {path} ({exc.__class__.__name__})") from exc
    arr = np.asarray(rgba, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[2] != 4:
        raise SnapError(f"cannot read image: {path}")
    return arr


def alpha_is_binary(arr: np.ndarray) -> bool:
    a = arr[..., 3]
    return bool(((a == 0) | (a == 255)).all())


def _premultiplied(arr: np.ndarray) -> np.ndarray:
    """RGBA -> float64 premultiplied RGB + alpha channel (H, W, 4)."""
    rgb = arr[..., :3].astype(np.float64)
    a = arr[..., 3:4].astype(np.float64) / 255.0
    return np.concatenate([rgb * a, arr[..., 3:4].astype(np.float64)], axis=2)


def _boundaries(length: int, cells: int, offset: int) -> tuple[int, ...]:
    """Cell boundary pixel indices for `cells` cells over `length` px.

    Offset 0 uses the resize mapping round(i * L / n), which matches how
    image resizers distribute cells.  Offset > 0 places grid lines at
    offset + i * p (a phase-shifted grid over a cropped/padded canvas).
    """
    if offset <= 0:
        return tuple(int(round(i * length / cells)) for i in range(cells + 1))
    p = length / cells
    pts = [0]
    k = 0
    while True:
        v = offset + k * p
        if v >= length - 0.5:
            break
        pts.append(int(round(v)))
        k += 1
    pts.append(length)
    out = [pts[0]]
    for v in pts[1:]:
        if v > out[-1]:
            out.append(v)
    return tuple(out)


def _inner(lo: int, hi: int) -> tuple[int, int]:
    """Cell interior with a 1px band shaved off each side (if room)."""
    if hi - lo >= 4:
        return lo + 1, hi - 1
    mid = (lo + hi) // 2
    return mid, mid + 1


def _pwlin_error(profile: np.ndarray, bounds: tuple[int, ...]) -> float:
    """Mean residual of a piecewise-linear fit of the 1-D profile.

    For bilinear damage the profile is linear between neighbouring cell
    centres, so the pieces span centre to centre (image edges are
    half-pieces anchored at the first/last centre value).
    """
    length = profile.shape[0]
    centres = [0] + [(bounds[i] + bounds[i + 1]) // 2 for i in range(len(bounds) - 1)] + [length - 1]
    total = 0.0
    for a, b in zip(centres, centres[1:]):
        if b - a < 2:
            continue
        seg = profile[a:b + 1]
        t = np.linspace(0.0, 1.0, len(seg))[:, None]
        recon = seg[0] * (1 - t) + seg[-1] * t
        total += float(np.abs(seg - recon).mean())
    return total / max(1, len(centres) - 1)


def _pconst_error(profile: np.ndarray, bounds: tuple[int, ...]) -> float:
    """Mean band-excluded residual of a piecewise-constant profile fit.

    The right model for crisp (nearest) damage, whose steps sit exactly
    on cell boundaries; pixels adjacent to boundaries are excluded.
    """
    length = profile.shape[0]
    recon = np.zeros_like(profile)
    for i in range(len(bounds) - 1):
        lo, hi = _inner(bounds[i], bounds[i + 1])
        if hi > lo:
            recon[bounds[i]:bounds[i + 1]] = profile[lo:hi].mean(axis=0)
    err = np.abs(profile - recon).mean(axis=1)
    near = np.zeros(length, dtype=bool)
    for pos in bounds[1:-1]:
        for r in (pos - 1, pos):
            if 0 <= r < length:
                near[r] = True
    if (~near).any():
        return float(err[~near].mean())
    return float(err.mean())


def _profile_fit(profile: np.ndarray, bounds: tuple[int, ...]) -> float:
    return min(_pwlin_error(profile, bounds), _pconst_error(profile, bounds))


def _comb(sig: np.ndarray, positions: list[int], idxs: tuple[int, ...]) -> float:
    """Mean signal near `positions`, relative to the overall mean."""
    mean = float(sig.mean())
    if mean <= 1e-12 or not positions:
        return 0.0
    vals = []
    for pos in positions:
        tot = 0.0
        for d in idxs:
            j = pos + d
            if 0 <= j < len(sig):
                tot += sig[j]
        vals.append(tot / len(idxs))
    return float(np.mean(vals) / mean)


def _axis_signals(pmj_frames: list[np.ndarray], axis: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Pooled per-axis evidence over all frames: (gradient, profile, length).

    The gradient is the mean of the per-frame ABSOLUTE gradients.  It
    must not be the gradient of the mean image: when animation content
    moves, per-frame steps at the shared boundaries have unrelated
    signs and cancel in the mean image, while their magnitudes — the
    actual grid evidence — add up.
    """
    gs, profiles = [], []
    for pmj in pmj_frames:
        if axis == 1:
            gs.append(np.abs(np.diff(pmj, axis=1)).mean(axis=(0, 2)))
            profiles.append(pmj.mean(axis=0))
        else:
            gs.append(np.abs(np.diff(pmj, axis=0)).mean(axis=(1, 2)))
            profiles.append(pmj.mean(axis=1))
    length = pmj_frames[0].shape[1] if axis == 1 else pmj_frames[0].shape[0]
    return np.mean(gs, axis=0), np.mean(profiles, axis=0), length


class _AxisData:
    """Per-axis signals and a per-count estimate builder (computed once)."""

    def __init__(self, g: np.ndarray, profile: np.ndarray, length: int):
        self.g = g
        self.profile = profile
        self.length = length
        self.dg = np.abs(np.diff(self.g))
        self.max_cells = max(2, int(self.length / MIN_CELL_PX))
        sig = self.g - self.g.mean()
        self.mag = np.abs(np.fft.rfft(sig)) if len(sig) > 4 else np.zeros(3)
        hi = max(3, min(len(self.g) // 2, self.length // 2) + 1)
        self.med = float(np.median(self.mag[2:hi])) + 1e-12 if hi > 2 else 1.0
        # Energy gate for prominence: on a near-constant gradient
        # signal (a smooth ramp) the demeaned FFT is quantisation
        # noise, and dividing by its tiny median manufactures huge
        # "prominence" from nothing.  Scale by the signal's RMS so
        # prominence only counts when there is real periodic energy.
        self.sig_rms = float(np.sqrt(np.mean(sig ** 2))) if len(sig) else 0.0
        self._fit_cache: dict[tuple[int, int], float] = {}

    def prominence(self, n: int) -> float:
        raw = float(self.mag[n] / self.med) if n < len(self.mag) else 0.0
        return raw * min(1.0, self.sig_rms / 0.75)

    def fit(self, n: int, offset: int) -> float:
        key = (n, offset)
        if key not in self._fit_cache:
            self._fit_cache[key] = _profile_fit(self.profile, _boundaries(self.length, n, offset))
        return self._fit_cache[key]

    def best_offset(self, n: int) -> tuple[float, int]:
        # Offset 0 (the resizer's own anchoring) is the strong default:
        # a shifted grid must fit clearly better, not by a hair, or the
        # 1-D projection is being fooled.
        best = (self.fit(n, 0), 0)
        max_off = max(1, int(round(self.length / n)) - 1)
        for off in range(1, max_off + 1):
            e = self.fit(n, off)
            if e < best[0] * 0.97:
                best = (e, off)
        return best

    def estimate(self, n: int, offset: int, fit: float) -> AxisEstimate:
        b = _boundaries(self.length, n, offset)
        centres = [(b[k] + b[k + 1]) // 2 for k in range(len(b) - 1)]
        c1 = _comb(self.g, list(b[1:-1]), (-1, 0))
        c2 = _comb(self.dg, [c - 1 for c in centres], (-1, 0))
        prom = self.prominence(n)
        comb = max(c1, c2)
        return AxisEstimate(cells=len(b) - 1, offset=offset, score=comb + 0.05 * prom,
                            fit=fit, prominence=prom, comb=comb, bounds=b)

    def scan(self) -> list[AxisEstimate]:
        """Gate by profile fit; rank survivors by comb + prominence."""
        counts = list(range(2, self.max_cells + 1))
        fit0 = {n: self.fit(n, 0) for n in counts}
        prelim = sorted(counts, key=lambda n: fit0[n])[:10]
        fft_best = max(counts, key=self.prominence)
        if fft_best not in prelim:
            prelim.append(fft_best)
        fits: dict[int, tuple[float, int]] = {}
        for n in counts:
            fits[n] = self.best_offset(n) if n in prelim else (fit0[n], 0)
        best_fit = min(f for f, _o in fits.values())
        gate = best_fit * GATE_REL + GATE_ABS
        out = [self.estimate(n, off, fit) for n, (fit, off) in fits.items() if fit <= gate]
        out.sort(key=lambda a: -a.score)
        return out


def _blur(img: np.ndarray, radius: float) -> np.ndarray:
    """Separable triangle-kernel blur (the smooth-upscale damage model)."""
    if radius < 0.75:
        return img
    r = int(np.ceil(radius))
    taps = np.arange(-r, r + 1)
    wts = np.maximum(0.0, 1.0 - np.abs(taps) / radius)
    wts /= wts.sum()
    out = img
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r, r)
        padded = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        for t, wgt in zip(taps, wts):
            sl = [slice(None)] * out.ndim
            sl[axis] = slice(r + t, r + t + out.shape[axis])
            acc += wgt * padded[tuple(sl)]
        out = acc
    return out


def _forward_errors(pmj: np.ndarray, xb: tuple[int, ...], yb: tuple[int, ...],
                    snapped_pm: np.ndarray) -> tuple[float, float]:
    """(crisp, smooth) forward-model errors of a snapped grid.

    Rebuild the constant-cell image, re-apply the damage under two
    models — crisp = identity (nearest upscale), smooth = triangle
    blur ~0.6 of a cell wide (bilinear/bicubic) — and take the mean
    absolute difference to the damaged input over ALL pixels.  A
    correct grid explains the image up to noise under at least one
    model.  (Scoring only cell interiors would be circular — the
    snapped colours are drawn from those very pixels.)
    """
    recon = np.repeat(np.repeat(snapped_pm, np.diff(yb), axis=0), np.diff(xb), axis=1)
    cell = min(float(np.mean(np.diff(xb))), float(np.mean(np.diff(yb))))
    err_crisp = float(np.abs(pmj - recon).mean())
    err_smooth = float(np.abs(pmj - _blur(recon, 0.6 * cell)).mean())
    return err_crisp, err_smooth


def _resynthesis_error(pmj: np.ndarray, xb: tuple[int, ...], yb: tuple[int, ...],
                       snapped_pm: np.ndarray) -> float:
    return min(_forward_errors(pmj, xb, yb, snapped_pm))


def snap(arr: np.ndarray, est: GridEstimate) -> np.ndarray:
    """Snap an RGBA image to the estimated grid: one colour per cell."""
    pmj = _premultiplied(arr)
    out_pm = np.zeros((est.ny, est.nx, 4), dtype=np.float64)
    for j in range(est.ny):
        y0, y1 = _inner(est.y.bounds[j], est.y.bounds[j + 1])
        for i in range(est.nx):
            x0, x1 = _inner(est.x.bounds[i], est.x.bounds[i + 1])
            block = pmj[y0:y1, x0:x1].reshape(-1, 4)
            out_pm[j, i] = np.median(block, axis=0)
    out = np.zeros((est.ny, est.nx, 4), dtype=np.uint8)
    alpha = out_pm[..., 3]
    out[..., 3] = np.clip(np.round(alpha), 0, 255).astype(np.uint8)
    opaque = alpha > 0
    rgb = np.zeros((est.ny, est.nx, 3), dtype=np.float64)
    a = (alpha / 255.0)[..., None]
    np.divide(out_pm[..., :3], np.where(a == 0, 1.0, a), out=rgb, where=opaque[..., None])
    rgb = np.where(opaque[..., None], rgb, 0.0)
    # Keep cells that are essentially fully transparent fully transparent.
    rgb = np.where((alpha < 8)[..., None], 0.0, rgb)
    out[..., :3] = np.clip(np.round(rgb), 0, 255).astype(np.uint8)
    out[..., 3] = np.where(alpha < 8, 0, out[..., 3])
    return out


# ------------------------------------------------------- v2 detector
#
# Fast candidate scoring uses integral-image cell MEANS on float32;
# finalists are re-scored with the exact median sampler.  The blur is
# the same triangle kernel as _blur, evaluated as two box blurs via
# cumulative sums (an even kernel width is forced odd -- an even box
# would shift the image by half a pixel and invent phase evidence).

CELL_COST_BITS = 16.0
PALETTE_ENTRY_BITS = 24.0
ERR_FLOOR = 0.35
# Abstention calibration (set on the DEV split -- gentle sprite indices
# 0-5 + harsh indices 0-17 -- and frozen before held-out measurement;
# see benchmarks/heldout_suite.py and REVIEW.md follow-up notes).
PHASE_MIN_ABS = 0.55      # half-cell shift must cost at least this much error
PHASE_MIN_REL = 0.16      # ... or this fraction of the winner's error
MARGIN_MIN_BITS_PER_ENTRY = 0.03  # winner must beat a different grid by this
# (DEV-split calibration: the dev near-tie abstention sat at 0.0115
# bits/entry and the weakest correct dev emission at 0.0415; 0.03 sits
# inside that gap.  The initial 0.012 guess and an intermediate 0.05 --
# which cost a dev correct case at 0.0415 -- were both superseded on
# dev evidence alone, before held-out re-measurement.)
#
# Non-uniform-resize verification (added after the v2 re-review found
# the per-axis phase check alone lets square fits to non-uniformly
# resized sprites through).  Two independent per-axis refutations,
# either of which abstains:
#   * Kink lattice: the axis profile's breakpoints lie on a lattice
#     whose period is the axis's OWN pitch.  Fire when a strong
#     lattice (>= KINK_BEST_MIN phase concentration over >=
#     KINK_PEAKS_MIN breakpoints) sits at a period disagreeing with
#     the winner's pitch by > KINK_DISAGREE_MIN while the winner's
#     own pitch concentrates weakly (<= KINK_WINNER_MAX).
#   * Coarser challenger: freeing this axis to a COARSER count (the
#     other axis fixed at the winner) beats the winner's full
#     arbiter score by > COARSER_MARGIN_MIN_BITS_PER_ENTRY -- the
#     signature of an inflated count whose pitch was forced to match
#     the other axis (a refinement/coarsening of the true grid).
# Calibration (DEV evidence, measured before the fresh acceptance
# set was run): over 342 genuine emitted axes (gentle + harsh +
# held-out suites) the kink preconditions' best concentration
# reached 0.87 and the largest coarser-challenger margin was 0.52
# bits/entry; on a dev non-uniform set the kink-caught fake axes
# scored >= 0.90 concentration and the margin-caught fakes >= 0.68
# bits/entry.  The thresholds sit inside those gaps.
KINK_PEAKS_MIN = 6
KINK_BEST_MIN = 0.89
KINK_WINNER_MAX = 0.50
KINK_DISAGREE_MIN = 0.20
COARSER_MARGIN_MIN_BITS_PER_ENTRY = 0.60
COARSER_PITCH_TOL = 0.12  # challenger pitch must differ from winner's
# Pooled-kink variant of the lattice check: breakpoint evidence is
# pooled per line (sum of |second differences| over all lines and
# channels) instead of differencing the line-averaged profile, which
# washes out breakpoints whose sign varies per line -- the small /
# sparse sprites the profile check is weakest on.  The winning
# lattice is the FUNDAMENTAL period (largest period reaching 95% of
# the best concentration; a lattice is also concentrated at its
# submultiples).  Calibration (same DEV data): genuine axes' best
# fundamental concentration under the preconditions reached 0.83;
# the dev fake axes this check catches scored >= 0.89.
QKINK_PEAKS_MIN = 6
QKINK_FUND_MIN = 0.85
QKINK_WINNER_MAX = 0.50
QKINK_DISAGREE_MIN = 0.20
# Small-image guard (Rakshit's conservative-guard decision after the
# verification fix above): the refutations need >= KINK_PEAKS_MIN
# breakpoints to fire at all, and below that count a forced axis is
# statistically indistinguishable from a genuine one -- measured
# after the fix, the only wrong grids left on the 24-case
# non-uniform acceptance set were two 8x8 sprites whose forced axis
# is 19 / 21 px long and yields 3 / 5 usable breakpoints, while the
# refutations stay silent.  So on very small images (short side of
# the DAMAGED image <= GUARD_MAX_SHORT_AXIS px) a winner is emitted
# only when BOTH axes' own breakpoint lattices positively confirm
# its pitch (see _axis_pitch_supported); otherwise it abstains.
# Scope measured on all four suites (208 cases): the emitted
# population at short side <= 24 px is exactly those two wrong grids
# plus two genuine nearest-integer recoveries at 24x24 whose lattice
# support is perfect (concentration 1.0, fundamental period equal to
# the pitch); the next genuine smooth recoveries sit at 27-29 px.
# The guard runs last, on would-be emissions only, so every existing
# abstention keeps its current message and behaviour outside this
# scope is unchanged.
GUARD_MAX_SHORT_AXIS = 24
GUARD_PEAKS_MIN = 3


def _sat(pmj: np.ndarray) -> np.ndarray:
    sat = np.zeros((pmj.shape[0] + 1, pmj.shape[1] + 1, pmj.shape[2]))
    sat[1:, 1:] = np.cumsum(np.cumsum(pmj, axis=0), axis=1)
    return sat


def _inner_arr(b: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = [], []
    for a, c in zip(b, b[1:]):
        if c - a >= 4:
            lo.append(a + 1)
            hi.append(c - 1)
        else:
            m = (a + c) // 2
            lo.append(m)
            hi.append(m + 1)
    return np.array(lo), np.array(hi)


def _means_sat(sat: np.ndarray, xb: tuple[int, ...], yb: tuple[int, ...]) -> np.ndarray:
    x0, x1 = _inner_arr(xb)
    y0, y1 = _inner_arr(yb)
    s = (sat[y1[:, None], x1[None, :]] - sat[y0[:, None], x1[None, :]]
         - sat[y1[:, None], x0[None, :]] + sat[y0[:, None], x0[None, :]])
    area = ((y1 - y0)[:, None] * (x1 - x0)[None, :]).astype(float)
    return s / area[..., None]


def _recon(means: np.ndarray, xb: tuple[int, ...], yb: tuple[int, ...]) -> np.ndarray:
    return np.repeat(np.repeat(means, np.diff(yb), axis=0), np.diff(xb), axis=1)


def _box_axis(img: np.ndarray, w: int, axis: int) -> np.ndarray:
    if w < 2:
        return img
    pad = [(0, 0)] * img.ndim
    pad[axis] = (w // 2, w // 2)
    padded = np.pad(img, pad, mode="edge")
    c = np.cumsum(padded, axis=axis, dtype=np.float32)
    z = np.zeros_like(np.take(c, [0], axis=axis))
    c = np.concatenate([z, c], axis=axis)
    hi = [slice(None)] * img.ndim
    lo = [slice(None)] * img.ndim
    hi[axis] = slice(w, w + img.shape[axis])
    lo[axis] = slice(0, img.shape[axis])
    return (c[tuple(hi)] - c[tuple(lo)]) / w


def _tri_blur32(img: np.ndarray, cell: float) -> np.ndarray:
    """Triangle blur (radius ~0.6 cell) as two odd-width box blurs."""
    k = int(round(0.6 * cell))
    if k < 1:
        return img
    if k % 2 == 0:
        k += 1
    out = img
    for axis in (0, 1):
        out = _box_axis(out, k, axis)
        out = _box_axis(out, k, axis)
    return out


def _resid_bits(err: float, n_entries: int) -> float:
    return n_entries * math.log2(2.0 * math.e * max(err, ERR_FLOOR))


class _Scorer:
    """Forward-model scoring of candidate grids for one reference frame."""

    def __init__(self, pmj: np.ndarray):
        self.pmj = pmj
        self.pmj32 = pmj.astype(np.float32)
        self.sat = _sat(pmj)
        self.n_entries = int(pmj.size)
        self.h, self.w = pmj.shape[:2]

    def _forward32(self, xb, yb, model=None):
        means = _means_sat(self.sat, xb, yb).astype(np.float32)
        recon = _recon(means, xb, yb)
        cell = min(float(np.mean(np.diff(xb))), float(np.mean(np.diff(yb))))
        ec = float(np.abs(self.pmj32 - recon).mean())
        if model == 0:
            return ec, 0
        es = float(np.abs(self.pmj32 - _tri_blur32(recon, cell)).mean())
        if model == 1:
            return es, 1
        return (ec, 0) if ec <= es else (es, 1)

    def _shifted(self, b, length, n):
        return _boundaries(length, n, int(round((length / n) / 2)))

    def fast_score(self, xb, yb):
        """(score, err, err_shifted, model) with mean-sampled cells."""
        err, model = self._forward32(xb, yb)
        xs = self._shifted(xb, self.w, len(xb) - 1)
        ys = self._shifted(yb, self.h, len(yb) - 1)
        err_sh, _ = self._forward32(xs, ys, model)
        ncells = (len(xb) - 1) * (len(yb) - 1)
        bits = _resid_bits(err, self.n_entries) + ncells * CELL_COST_BITS
        bits_sh = _resid_bits(err_sh, self.n_entries) + ncells * CELL_COST_BITS
        return bits - (bits_sh - bits), err, err_sh, model

    def frame_error(self, pmj_frame, xb, yb, model):
        """Forward-model error of ANOTHER frame under this grid (B1)."""
        sat = _sat(pmj_frame)
        means = _means_sat(sat, xb, yb).astype(np.float32)
        recon = _recon(means, xb, yb)
        pmj32 = pmj_frame.astype(np.float32)
        if model == 0:
            return float(np.abs(pmj32 - recon).mean())
        cell = min(float(np.mean(np.diff(xb))), float(np.mean(np.diff(yb))))
        return float(np.abs(pmj32 - _tri_blur32(recon, cell)).mean())


def _palette_group_count(cells_rgba: np.ndarray) -> int:
    """Distinct colour groups (tol-36 grouping) among opaque snapped cells."""
    from .palette import _group_reps

    opaque = cells_rgba[cells_rgba[..., 3] > 0][:, :3]
    if len(opaque) == 0:
        return 0
    uniq, counts = np.unique(opaque, axis=0, return_counts=True)
    return len(_group_reps(uniq, counts))


def _pick_peaks(q: np.ndarray) -> np.ndarray:
    """Breakpoint positions from a kink-evidence signal.

    Local maxima above 25% of the strongest spike, merged within
    2 px (keeping the strongest).
    """
    length = q.shape[0]
    if length == 0 or q.max() <= 1e-9:
        return np.array([])
    thr = 0.25 * q.max()
    peaks: list[int] = []
    j = 1
    while j < length - 1:
        if q[j] >= thr and q[j] >= q[j - 1] and q[j] >= q[j + 1]:
            if peaks and j - peaks[-1] <= 2:
                if q[j] > q[peaks[-1]]:
                    peaks[-1] = j
            else:
                peaks.append(j)
            j += 1
        else:
            j += 1
    return np.array(peaks, dtype=float)


def _kink_positions(profile: np.ndarray) -> np.ndarray:
    """Breakpoint positions of a per-axis profile.

    First+second differences of the profile spike where the axis's
    cell structure breaks (cell boundaries / centres under the
    resizer's mapping) and nowhere else.
    """
    d1 = np.abs(np.diff(profile, axis=0)).sum(axis=1)
    d2 = np.abs(np.diff(profile, n=2, axis=0)).sum(axis=1)
    length = profile.shape[0]
    q = np.zeros(length)
    q[1:length - 1] += d2
    q[0:length - 1] += 0.5 * d1
    q[1:length] += 0.5 * d1
    return _pick_peaks(q)


def _phase_concentration(peaks: np.ndarray, period: float) -> float:
    """Rayleigh concentration of breakpoint phases at `period`.

    ~1 when every breakpoint lies on ONE lattice of that period
    (the lattice's phase offset is irrelevant), low when they do
    not.  This is the axis's own pitch evidence: it involves no
    forward model and no other axis.
    """
    if len(peaks) == 0 or period <= 0:
        return 0.0
    ph = np.exp(2j * np.pi * (np.mod(peaks, period) / period))
    return float(np.abs(ph.mean()))


def _axis_kink_rejects(profile: np.ndarray, length: int, n_w: int) -> bool:
    """True when the axis's own breakpoint lattice refutes the winner.

    The winner's pitch must be the period of the axis's breakpoints.
    If the breakpoints instead concentrate strongly on a lattice at
    a clearly different period, this axis was resized by a different
    factor than the winner assumes.
    """
    peaks = _kink_positions(profile)
    if len(peaks) < KINK_PEAKS_MIN:
        return False
    p_w = length / n_w
    c_w = _phase_concentration(peaks, p_w)
    if c_w > KINK_WINNER_MAX:
        return False
    cands: set[float] = set()
    for v in np.diff(peaks):
        if v >= MIN_CELL_PX:
            cands.add(round(float(v), 2))
    grid = np.arange(max(1.8, p_w * 0.4), length / 2, 0.05)
    best_s, best_c = p_w, c_w
    for s in list(cands) + list(grid):
        c = _phase_concentration(peaks, float(s))
        if c > best_c:
            best_c, best_s = c, float(s)
    return (best_c >= KINK_BEST_MIN
            and abs(best_s - p_w) / best_s > KINK_DISAGREE_MIN)


def _qpool_peaks(pmj: np.ndarray, axis: int) -> np.ndarray:
    """Breakpoint positions from the pooled per-line kink evidence.

    The evidence signal is the sum, over every line and channel, of
    |second difference| (+ a first-difference term) along the axis --
    the pooled counterpart of :func:`_kink_positions` (which
    differences the averaged profile instead).
    """
    a = np.moveaxis(pmj, axis, 0)  # (length, other, 4)
    length = a.shape[0]
    d2 = np.abs(a[2:] - 2 * a[1:-1] + a[:-2]).sum(axis=(1, 2))
    d1 = np.abs(np.diff(a, axis=0)).sum(axis=(1, 2))
    q = np.zeros(length)
    q[1:length - 1] += d2
    q[0:length - 1] += 0.5 * d1
    q[1:length] += 0.5 * d1
    return _pick_peaks(q)


def _fundamental_period(peaks: np.ndarray, p_w: float,
                        length: int) -> tuple[float | None, float]:
    """Fundamental period of a breakpoint lattice, and its concentration.

    A breakpoint lattice concentrates at its own submultiples too, so
    the period is selected as the FUNDAMENTAL one: the largest period
    whose phase concentration reaches 95% of the best over the
    candidate scan (peak spacings + a fine grid).  Returns
    (None, 0.0) when there are no peaks.
    """
    if len(peaks) == 0:
        return None, 0.0
    cands: set[float] = set()
    for v in np.diff(peaks):
        if v >= MIN_CELL_PX:
            cands.add(round(float(v), 2))
    grid = np.arange(max(1.8, p_w * 0.4), length / 2, 0.05)
    vals = [(float(s), _phase_concentration(peaks, float(s)))
            for s in list(cands) + list(grid)]
    c_max = max(c for _s, c in vals)
    s_fund = max(s for s, c in vals if c >= 0.95 * c_max)
    return s_fund, _phase_concentration(peaks, s_fund)


def _qpool_kink_rejects(pmj: np.ndarray, axis: int, n_w: int) -> bool:
    """Pooled per-line breakpoint-lattice refutation of the winner.

    Same lattice test as :func:`_axis_kink_rejects`, but the
    breakpoint evidence is pooled per line (see :func:`_qpool_peaks`)
    instead of differencing the averaged profile.  The period is
    selected as the FUNDAMENTAL one (see :func:`_fundamental_period`).
    """
    length = pmj.shape[1] if axis == 1 else pmj.shape[0]
    peaks = _qpool_peaks(pmj, axis)
    if len(peaks) < QKINK_PEAKS_MIN:
        return False
    p_w = length / n_w
    c_w = _phase_concentration(peaks, p_w)
    if c_w > QKINK_WINNER_MAX:
        return False
    s_fund, c_fund = _fundamental_period(peaks, p_w, length)
    return (c_fund >= QKINK_FUND_MIN
            and abs(s_fund - p_w) / s_fund > QKINK_DISAGREE_MIN)


def _axis_pitch_supported(profile: np.ndarray, pmj: np.ndarray, axis: int,
                          length: int, n_w: int) -> bool:
    """True when an axis's OWN breakpoint lattice confirms the winner.

    Positive counterpart of the kink refutations, used only by the
    small-image guard: in either evidence pool (profile breakpoints,
    pooled per-line breakpoints) the winner's pitch is supported when
    enough breakpoints concentrate on it directly (>= KINK_PEAKS_MIN
    peaks at concentration > KINK_WINNER_MAX), or when the lattice's
    fundamental period agrees with it (within QKINK_DISAGREE_MIN at
    concentration >= QKINK_FUND_MIN, from >= GUARD_PEAKS_MIN peaks).
    """
    p_w = length / n_w
    for peaks in (_kink_positions(profile), _qpool_peaks(pmj, axis)):
        if len(peaks) >= KINK_PEAKS_MIN \
                and _phase_concentration(peaks, p_w) > KINK_WINNER_MAX:
            return True
        if len(peaks) >= GUARD_PEAKS_MIN:
            s_fund, c_fund = _fundamental_period(peaks, p_w, length)
            if s_fund is not None and c_fund >= QKINK_FUND_MIN \
                    and abs(s_fund - p_w) / s_fund <= QKINK_DISAGREE_MIN:
                return True
    return False


def _aug_score(scorer: _Scorer, img: np.ndarray,
               xb: tuple[int, ...], yb: tuple[int, ...]) -> float:
    """The stage-2 arbiter score: fast MDL-minus-phase + palette bits."""
    fs = scorer.fast_score(xb, yb)[0]
    xa = AxisEstimate(cells=len(xb) - 1, offset=0, score=0.0, fit=0.0,
                      prominence=0.0, comb=0.0, bounds=xb)
    ya = AxisEstimate(cells=len(yb) - 1, offset=0, score=0.0, fit=0.0,
                      prominence=0.0, comb=0.0, bounds=yb)
    est_t = GridEstimate(nx=xa.cells, ny=ya.cells, confidence=0.0,
                         fit_error=0.0, x=xa, y=ya)
    groups = _palette_group_count(snap(img, est_t))
    ncells = xa.cells * ya.cells
    return fs + ncells * math.log2(max(groups, 2)) + groups * PALETTE_ENTRY_BITS


def _coarser_margin(scorer: _Scorer, img: np.ndarray, est: GridEstimate,
                    axis_is_x: bool, a_w: float) -> float:
    """Best coarser same-axis challenger's margin over the winner.

    Frees ONE axis to every coarser count whose pitch clearly
    differs from the winner's (the other axis stays at the winner),
    scores challengers with the full arbiter score, and returns
    (winner - best challenger) in bits per entry.  A positive margin
    means this axis's own evidence prefers a different, coarser
    grid -- the winner's count was forced to match the other axis.
    """
    if axis_is_x:
        fixed_b, length, n_w = est.y.bounds, scorer.w, est.nx
    else:
        fixed_b, length, n_w = est.x.bounds, scorer.h, est.ny
    p_w = length / n_w
    max_cells = max(2, int(length / MIN_CELL_PX))
    fast: dict[int, float] = {}
    for n in range(2, max_cells + 1):
        if length / n < MIN_CELL_PX:
            break
        if n >= n_w:
            continue
        if abs(length / n - p_w) / (length / n) <= COARSER_PITCH_TOL:
            continue
        b = _boundaries(length, n, 0)
        xb, yb = (b, fixed_b) if axis_is_x else (fixed_b, b)
        fast[n] = scorer.fast_score(xb, yb)[0]
    if not fast:
        return -math.inf
    best = math.inf
    for n in sorted(fast, key=fast.get)[:4]:
        b = _boundaries(length, n, 0)
        xb, yb = (b, fixed_b) if axis_is_x else (fixed_b, b)
        best = min(best, _aug_score(scorer, img, xb, yb))
    return (a_w - best) / scorer.n_entries


def _square_pitch_pairs(w: int, h: int, max_cx: int, max_cy: int):
    """All (nx, ny) whose pitches agree within resizer rounding.

    A uniform upscale by factor f gives pitch_x = round(w*f)/w-ish and
    pitch_y likewise; the two differ by at most ~0.5/w + 0.5/h plus a
    small relative slack for non-uniform resizes.
    """
    pairs = []
    nx = 2
    while nx <= max_cx and w / nx >= MIN_CELL_PX:
        px = w / nx
        ny = 2
        while ny <= max_cy and h / ny >= MIN_CELL_PX:
            py = h / ny
            if abs(px - py) <= max(0.20, 0.05 * max(px, py)):
                pairs.append((nx, ny))
            ny += 1
        nx += 1
    return pairs


# Diagnostics of the most recent estimate_grid call (winner grid,
# errors, margins).  Written before any abstention decision so callers
# and benchmarks can inspect WHY a call abstained.
LAST_STATS: dict = {}


def estimate_grid(images: list[np.ndarray]) -> GridEstimate:
    """Estimate one shared grid for one or more same-size RGBA frames."""
    if not images:
        raise SnapError("no frames to analyse")
    h, w = images[0].shape[:2]
    for im in images:
        if im.shape[:2] != (h, w):
            raise SnapError("all frames must have the same size")
    if min(h, w) < MIN_DIM or max(h, w) < 2 * MIN_DIM:
        raise SnapError(f"image too small to contain a pixel grid ({w}x{h})")
    pmj_frames = [_premultiplied(im) for im in images]
    pmj = pmj_frames[0]  # resynthesis/snap reference frame
    if float(max(float(f[..., 3].max()) for f in pmj_frames)) <= 0.0:
        raise SnapError("image is fully transparent \u2014 nothing to recover")
    signals = {axis: _axis_signals(pmj_frames, axis) for axis in (1, 0)}

    # Edge-density gate (B2-fixed): damaged pixel art is full of hard
    # edges, but the pooled gradient / profile-spread ratio FALLS with
    # image size (more cells average into each profile entry), so the
    # threshold scales as sqrt(40 / axis_length) instead of sitting at
    # a fixed 0.015 that falsely rejected a 64x64 sprite at 3.7x.  The
    # gate no longer vetoes outright: a candidate with strong detector
    # evidence (below) overrides it, because a smooth gradient -- the
    # gate's real target -- has almost no phase evidence.
    gate_failed = False
    for axis in (1, 0):
        g, profile, length = signals[axis]
        spread = float(profile.std())
        thr = 0.015 * min(1.0, math.sqrt(40.0 / max(1, length)))
        if spread > 1e-9 and float(g.mean()) / spread < thr:
            gate_failed = True

    xdata = _AxisData(*signals[1])
    ydata = _AxisData(*signals[0])
    scorer = _Scorer(pmj)

    # ---- Stage 1: ungated square-pitch sweep ------------------------
    # Candidates come from the edges themselves via the forward model,
    # not from the legacy profile-fit gate (which eliminated the true
    # count before ranking in v1).  Only square-pitch pairs are swept:
    # a uniform upscale -- the damage this tool repairs -- applies one
    # factor to both axes.  Non-square pairs are NOT admitted: measured
    # on the dev split, coarse non-square grids (e.g. one axis halved)
    # beat the true grid on sparse shape sprites, because shifting a
    # coarse grid by half a cell displaces whole features and fakes
    # phase evidence.  Admitting them traded abstentions for wrong
    # grids, which this tool does not do.
    cand: dict[tuple[int, int], tuple[float, float, float, int]] = {}
    for nx, ny in _square_pitch_pairs(w, h, xdata.max_cells, ydata.max_cells):
        xb = _boundaries(w, nx, 0)
        yb = _boundaries(h, ny, 0)
        cand[(nx, ny)] = scorer.fast_score(xb, yb)
    if not cand:
        raise AbstainError("no clean pixel grid found (no candidate grids) "
                           "\u2014 leaving the image unchanged")
    ranked = sorted(cand.items(), key=lambda kv: kv[1][0])

    # ---- Stage 2: offset variants + exact re-scoring ----------------
    def axis_est(data: _AxisData, n: int, off: int) -> AxisEstimate:
        return data.estimate(n, off, data.fit(n, off))

    exact = []  # (score, (nx,ny,offx,offy), err, err_sh, groups, est, model)
    for (nx, ny), _fs in ranked[:6]:
        # Phase-offset variants (cropped sprites): the profile-best
        # offset per axis, alone and combined, judged by the SAME fast
        # score -- the forward model, not the profile, arbitrates.
        off_cands = {(0, 0)}
        bx = xdata.best_offset(nx)[1]
        by = ydata.best_offset(ny)[1]
        if bx:
            off_cands.add((bx, 0))
        if by:
            off_cands.add((0, by))
        if bx and by:
            off_cands.add((bx, by))
        best_var = None
        for offx, offy in off_cands:
            s_v = scorer.fast_score(_boundaries(w, nx, offx),
                                    _boundaries(h, ny, offy))[0]
            if best_var is None or s_v < best_var[0]:
                best_var = (s_v, offx, offy)
        _sv, offx, offy = best_var
        xa = axis_est(xdata, nx, offx)
        ya = axis_est(ydata, ny, offy)
        est_t = GridEstimate(nx=xa.cells, ny=ya.cells, confidence=0.0,
                             fit_error=0.0, x=xa, y=ya)
        snapped = snap(images[0], est_t)
        ec, es = _forward_errors(pmj, xa.bounds, ya.bounds,
                                 _premultiplied(snapped))
        model = 0 if ec <= es else 1
        err = ec if model == 0 else es
        # Shifted grid: every cut moved by half a cell from the winner's
        # own anchoring (wrapping within one pitch at the edge).
        def shifted_bounds(data_len, n, off):
            pitch = data_len / n
            sh = off + int(round(pitch / 2))
            if sh >= pitch:
                sh -= int(round(pitch))
            return _boundaries(data_len, n, max(0, sh))
        xs_b = shifted_bounds(w, nx, offx)
        ys_b = shifted_bounds(h, ny, offy)
        xa_s = AxisEstimate(cells=len(xs_b) - 1, offset=0, score=0.0, fit=0.0,
                            prominence=0.0, comb=0.0, bounds=xs_b)
        ya_s = AxisEstimate(cells=len(ys_b) - 1, offset=0, score=0.0, fit=0.0,
                            prominence=0.0, comb=0.0, bounds=ys_b)
        est_s = GridEstimate(nx=xa_s.cells, ny=ya_s.cells, confidence=0.0,
                             fit_error=0.0, x=xa_s, y=ya_s)
        snapped_s = snap(images[0], est_s)
        ec_s, es_s = _forward_errors(pmj, xs_b, ys_b, _premultiplied(snapped_s))
        err_sh = ec_s if model == 0 else es_s
        groups = _palette_group_count(snapped)
        ncells = xa.cells * ya.cells
        palette_bits = ncells * math.log2(max(groups, 2)) + groups * PALETTE_ENTRY_BITS
        # Ranking score = the sweep's fast MDL-minus-phase score for
        # this variant + the palette term.  The exact (median) error is
        # NOT re-derived into the ranking: measured on the dev split,
        # a fully exact re-score flipped correct sweep winners to
        # coarser grids (median sampling + palette cost together
        # over-reward fewer cells).  Exact errors are still measured
        # below and drive the veto / phase / confidence decisions.
        score = best_var[0] + palette_bits
        exact.append((score, (nx, ny, offx, offy), err, err_sh, groups, est_t, model))
    exact.sort(key=lambda t: t[0])
    score1, key1, err1, err_sh1, groups1, est_win, model1 = exact[0]
    # Margin against the best DIFFERENT grid among finalists; if all
    # finalists share the winner's counts, use the fast-sweep margin.
    margin = math.inf
    for s2, k2, *_rest in exact[1:]:
        if k2[:2] != key1[:2]:
            margin = s2 - score1
            break
    if margin is math.inf:
        for (n2, fs2) in ranked[1:]:
            if n2 != key1[:2]:
                margin = fs2[0] - cand[key1[:2]][0]
                break

    phase_abs = err_sh1 - err1
    LAST_STATS.update(dict(grid=(est_win.nx, est_win.ny), err=err1,
                           err_sh=err_sh1, phase_abs=phase_abs,
                           margin_bits=margin, score=score1,
                           groups=groups1, gate_failed=gate_failed,
                           model=model1))
    phase_rel = phase_abs / max(err1, 1.0)
    margin_rel = margin / scorer.n_entries
    err_t = max(0.0, 1.0 - err1 / 14.0)
    confidence = (0.30 * err_t
                  + 0.35 * min(1.0, phase_rel / 0.9)
                  + 0.35 * min(1.0, margin_rel / 0.20))
    est = GridEstimate(nx=est_win.nx, ny=est_win.ny, confidence=confidence,
                       fit_error=err1, x=est_win.x, y=est_win.y,
                       crisp=bool(model1 == 0))

    # Per-axis phase verification (winner only): each axis must
    # independently show phase sensitivity.  A square-pitch fit to a
    # NON-uniformly resized image wins on its true axis and fakes the
    # other (measured: fake axis gain 0.07 vs >= 0.24 for every true
    # axis in the dev split) -- abstain instead of emitting it.
    def _shifted_axis_bounds(data_len, n, off):
        pitch = data_len / n
        sh = off + int(round(pitch / 2))
        if sh >= pitch:
            sh -= int(round(pitch))
        return _boundaries(data_len, n, max(0, sh))

    def _err_with(xb, yb):
        xa_t = AxisEstimate(cells=len(xb) - 1, offset=0, score=0.0, fit=0.0,
                            prominence=0.0, comb=0.0, bounds=xb)
        ya_t = AxisEstimate(cells=len(yb) - 1, offset=0, score=0.0, fit=0.0,
                            prominence=0.0, comb=0.0, bounds=yb)
        est_t = GridEstimate(nx=xa_t.cells, ny=ya_t.cells, confidence=0.0,
                             fit_error=0.0, x=xa_t, y=ya_t)
        ec_t, es_t = _forward_errors(pmj, xb, yb,
                                     _premultiplied(snap(images[0], est_t)))
        return ec_t if model1 == 0 else es_t

    xb_w, yb_w = est_win.x.bounds, est_win.y.bounds
    err_xonly = _err_with(_shifted_axis_bounds(w, est_win.nx, est_win.x.offset), yb_w)
    err_yonly = _err_with(xb_w, _shifted_axis_bounds(h, est_win.ny, est_win.y.offset))
    axis_gain_min = min(err_xonly - err1, err_yonly - err1)
    LAST_STATS.update(dict(axis_gain_min=axis_gain_min))

    # ---- Abstention decisions --------------------------------------
    if err1 > FIT_VETO:
        raise AbstainError(
            f"no clean pixel grid found (best resynthesis error {err1:.1f} is "
            "too high \u2014 this looks like a photo or a smooth render, or it "
            "may already be clean pixel art at its true size) \u2014 leaving "
            "the image unchanged")
    strong = phase_abs >= PHASE_MIN_ABS and phase_rel >= PHASE_MIN_REL
    # Low-error winners (very flat sprites, e.g. the 64x64 B2 case at
    # err 0.39) cannot reach an absolute phase cost of 0.55 -- their
    # whole error budget is smaller than that.  For them a near-doubling
    # of the error under a half-cell shift is the strong evidence.
    if err1 <= 1.2 and phase_abs >= 0.25 and err_sh1 >= 1.8 * err1:
        strong = True
    if gate_failed and not (strong and err1 <= 10.0 and margin_rel >= 0.05):
        raise AbstainError("no clean pixel grid found (the image has "
                           "almost no hard edges \u2014 it looks like a smooth "
                           "gradient or photo, not damaged pixel art) \u2014 "
                           "leaving the image unchanged")
    if not strong:
        raise AbstainError(
            f"no clean pixel grid found (best candidate {est.nx}x{est.ny} "
            f"has weak phase evidence: shifting its cuts by half a cell "
            f"costs only {phase_abs:.2f}) \u2014 leaving the image unchanged")
    if axis_gain_min < 0.12:
        raise AbstainError(
            f"no clean pixel grid found (candidate {est.nx}x{est.ny} checks "
            "out on one axis only -- the image may have been resized "
            "non-uniformly, so no square grid fits both axes) \u2014 leaving "
            "the image unchanged")
    if margin_rel < MARGIN_MIN_BITS_PER_ENTRY:
        raise AbstainError(
            f"no clean pixel grid found (candidate {est.nx}x{est.ny} is in a "
            "near-tie with a different grid, so picking one would be a "
            "guess) \u2014 leaving the image unchanged")

    # ---- Non-uniform resize verification ---------------------------
    # The winner is a SQUARE-pitch pair by construction.  On an image
    # resized by different factors per axis, the true grid is not
    # square-pitch and never enters the sweep; the winner keeps one
    # axis's true count and forces the other axis to a count whose
    # pitch matches -- and that forced axis can fake the phase check
    # above (its cuts coincide with true boundaries at regular
    # intervals).  So verify each axis against its OWN evidence,
    # independent of the square assumption: (a) the axis profile's
    # breakpoint lattice must have the winner's pitch as its period;
    # (b) no coarser count on this axis (other axis fixed) may beat
    # the winner's full arbiter score by a clear margin.  Either
    # refutation means the axes were stretched differently.  The
    # lattice test runs on two independent evidence pools: the axis
    # profile's breakpoints, and the per-line breakpoint evidence
    # pooled over the whole image (fundamental-period selection).
    a_w = _aug_score(scorer, images[0], est.x.bounds, est.y.bounds)
    for axis_is_x, n_w in ((True, est.nx), (False, est.ny)):
        _g_a, profile_a, length_a = signals[1 if axis_is_x else 0]
        kink_reject = _axis_kink_rejects(profile_a, length_a, n_w)
        qkink_reject = (not kink_reject) and _qpool_kink_rejects(
            pmj, 1 if axis_is_x else 0, n_w)
        cm = -math.inf
        if not (kink_reject or qkink_reject):
            cm = _coarser_margin(scorer, images[0], est, axis_is_x, a_w)
        LAST_STATS.update({
            f"nu_kink_{'x' if axis_is_x else 'y'}": kink_reject,
            f"nu_qkink_{'x' if axis_is_x else 'y'}": qkink_reject,
            f"nu_coarser_{'x' if axis_is_x else 'y'}": cm})
        if kink_reject or qkink_reject or cm > COARSER_MARGIN_MIN_BITS_PER_ENTRY:
            raise AbstainError(
                f"no clean pixel grid found (candidate {est.nx}x{est.ny} "
                "does not check out on both axes \u2014 the image appears "
                "stretched differently in each direction, so no single "
                "square grid fits it) \u2014 leaving the image unchanged")

    # ---- B1: every frame must agree with the shared grid ----------
    if len(images) > 1:
        errs = [scorer.frame_error(pf, est.x.bounds, est.y.bounds, model1)
                for pf in pmj_frames]
        med = float(np.median(errs))
        for i, e_i in enumerate(errs):
            if e_i > max(2.5 * med, med + 6.0) and e_i > 8.0:
                raise AbstainError(
                    f"frames disagree about the grid: frame {i} fits the "
                    f"shared {est.nx}x{est.ny} grid far worse than the rest "
                    f"(error {e_i:.1f} vs median {med:.1f}) \u2014 the folder "
                    "may mix sprites from different grids; leaving the "
                    "images unchanged")
        # A frame on a COARSER grid fits a finer shared grid deceptively
        # well (its cells are unions of shared cells), so the error
        # check above cannot catch it.  Compare each frame's OWN best
        # grid (same sweep) against the shared one: a frame that
        # strongly prefers a different grid, with real phase evidence
        # of its own, does not belong to this sheet.
        for i, pf in enumerate(pmj_frames):
            if i == 0 or float(pf[..., 3].max()) <= 0.0:
                continue  # reference frame / transparent frame: no evidence
            sc_f = _Scorer(pf)
            s_shared = sc_f.fast_score(est.x.bounds, est.y.bounds)[0]
            best_f = None
            for nx, ny in _square_pitch_pairs(w, h, xdata.max_cells,
                                              ydata.max_cells):
                s_f = sc_f.fast_score(_boundaries(w, nx, 0),
                                      _boundaries(h, ny, 0))
                if best_f is None or s_f[0] < best_f[0][0]:
                    best_f = (s_f, (nx, ny))
            if best_f is None:
                continue
            (s_own, e_own, e_own_sh, _m), grid_own = best_f
            if grid_own == (est.nx, est.ny):
                continue
            if (s_shared - s_own) / scorer.n_entries > 0.08 \
                    and (e_own_sh - e_own) >= PHASE_MIN_ABS:
                raise AbstainError(
                    f"frames disagree about the grid: frame {i} fits a "
                    f"{grid_own[0]}x{grid_own[1]} grid far better than the "
                    f"shared {est.nx}x{est.ny} grid \u2014 the folder may mix "
                    "sprites from different grids; leaving the images "
                    "unchanged")

    # ---- Small-image guard -------------------------------------------
    # Last gate, on would-be emissions only (every abstention above
    # keeps its current message).  The non-uniform refutations are
    # one-sided: they fire on strong contrary evidence but stay
    # silent when an axis offers too few breakpoints to judge -- and
    # on a very small image that silence is not confirmation.  The
    # measured residue of the verification fix was exactly there:
    # tiny sprites stretched differently per axis whose forced axis
    # (~20 px, 3-5 breakpoints) could not be refuted.  So in this
    # scope the winner must be positively confirmed by BOTH axes'
    # own breakpoint lattices; an axis that cannot confirm its pitch
    # abstains instead of emitting an unverifiable grid.
    if min(w, h) <= GUARD_MAX_SHORT_AXIS:
        for axis_is_x, n_w in ((True, est.nx), (False, est.ny)):
            _g_a, profile_a, length_a = signals[1 if axis_is_x else 0]
            supported = _axis_pitch_supported(
                profile_a, pmj, 1 if axis_is_x else 0, length_a, n_w)
            LAST_STATS.update({
                f"guard_supported_{'x' if axis_is_x else 'y'}": supported})
            if not supported:
                raise AbstainError(
                    f"no clean pixel grid found (candidate {est.nx}x"
                    f"{est.ny} could not be confirmed on both axes \u2014 "
                    "the image is very small and appears stretched "
                    "differently in each direction, so it cannot be "
                    "recovered safely) \u2014 leaving the image unchanged")
    return est
