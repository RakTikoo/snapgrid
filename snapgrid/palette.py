"""Unified palette handling for snapgrid.

All frames of a sheet/animation share ONE palette, built jointly from
every snapped cell colour. If the joint colour count fits the cap the
palette is exact (lossless). Otherwise a deterministic median-cut
quantisation reduces it to the cap and each cell is mapped to its
nearest palette colour. Fully transparent is always its own entry.
"""

from __future__ import annotations

import json

import numpy as np

TRANSPARENT = (0, 0, 0, 0)


def _median_cut(colors: np.ndarray, counts: np.ndarray, cap: int) -> np.ndarray:
    """Median-cut quantisation. colors: (N,3) uint8 unique opaque colours.

    Returns at most `cap` representative colours (count-weighted means).
    Fully deterministic: ties are broken by channel order and position.
    """
    boxes: list[tuple[np.ndarray, np.ndarray]] = [(colors, counts)]
    while len(boxes) < cap:
        # Pick the splittable box with the largest (range x population).
        best_idx, best_score = -1, -1.0
        for idx, (cols, cts) in enumerate(boxes):
            if len(cols) < 2:
                continue
            rng = cols.max(axis=0).astype(int) - cols.min(axis=0).astype(int)
            score = float(rng.max()) * float(cts.sum())
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx < 0:
            break
        cols, cts = boxes.pop(best_idx)
        rng = cols.max(axis=0).astype(int) - cols.min(axis=0).astype(int)
        ch = int(np.argmax(rng))
        order = np.argsort(cols[:, ch], kind="stable")
        cols, cts = cols[order], cts[order]
        total = int(cts.sum())
        cum = np.cumsum(cts)
        split = int(np.searchsorted(cum, (total + 1) // 2))
        split = min(max(split, 1), len(cols) - 1)
        boxes.append((cols[:split], cts[:split]))
        boxes.append((cols[split:], cts[split:]))
    reps = []
    for cols, cts in boxes:
        w = cts.astype(np.float64)
        mean = (cols.astype(np.float64) * w[:, None]).sum(axis=0) / w.sum()
        reps.append(tuple(int(round(v)) for v in mean))
    return np.array(reps, dtype=np.uint8)


def build_palette(cell_arrays: list[np.ndarray], max_colors: int) -> np.ndarray:
    """Build one shared palette (K, 4) uint8 from snapped cell arrays."""
    if max_colors < 1:
        raise ValueError("max_colors must be >= 1")
    flat = np.concatenate([c.reshape(-1, 4) for c in cell_arrays], axis=0)
    has_transparent = bool((flat[:, 3] == 0).any())
    opaque = flat[flat[:, 3] > 0]
    budget = max_colors - (1 if has_transparent else 0)
    entries: list[tuple[int, int, int, int]] = []
    if has_transparent:
        entries.append(TRANSPARENT)
    if len(opaque) and budget > 0:
        uniq, counts = np.unique(opaque[:, :3], axis=0, return_counts=True)
        if len(uniq) <= budget:
            # Exact palette; order by frequency (then value) for stability.
            order = sorted(range(len(uniq)),
                           key=lambda i: (-int(counts[i]), tuple(uniq[i])))
            reps = [tuple(int(v) for v in uniq[i]) for i in order]
        else:
            reps = [tuple(int(v) for v in row)
                    for row in _median_cut(uniq, counts, budget)]
        entries.extend((r, g, b, 255) for (r, g, b) in reps)
    if not entries:  # every cell transparent (shouldn't happen post-load)
        entries.append(TRANSPARENT)
    return np.array(entries, dtype=np.uint8)


def _group_reps(
    uniq: np.ndarray, counts: np.ndarray, tol: int = 36
) -> list[tuple[tuple[int, int, int], int]]:
    """Group unique RGB colours within L1 `tol` of the group's seed.

    Returns [(representative, support)] where support is the number of
    sampled cells in the group.  The representative is the
    count-weighted per-channel MEDIAN of the group: individual samples
    are noisy (each is off the true colour by a level or two), but the
    median over many samples of one true colour converges to the exact
    value, while a single most-frequent sample is a coin flip among
    the values adjacent to the truth.
    """
    order = np.argsort(-counts, kind="stable")
    uniq_i = uniq.astype(np.int32)
    assigned = np.full(len(uniq), -1, dtype=int)
    reps: list[tuple[tuple[int, int, int], int]] = []
    for idx in order:
        if assigned[idx] >= 0:
            continue
        dist = np.abs(uniq_i - uniq_i[idx]).sum(axis=1)
        members = (assigned < 0) & (dist <= tol)
        assigned[members] = len(reps)
        cols = uniq_i[members].astype(np.float64)
        w = counts[members].astype(np.float64)
        rep = []
        for ch in range(3):
            o = np.argsort(cols[:, ch], kind="stable")
            cw = np.cumsum(w[o])
            pick = int(np.searchsorted(cw, (w.sum() + 1) / 2))
            rep.append(int(cols[o[min(pick, len(o) - 1)], ch]))
        reps.append((tuple(rep), int(counts[members].sum())))
    return reps


def _confident_colors(cell_arrays: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Sampled colours from cells whose neighbours agree (flat regions).

    A cell surrounded by cells of (nearly) the same sampled colour sat
    in a flat run of the original, so the damage had nothing to blend
    in: its sampled colour is the true colour, not a smear.
    """
    found: dict[tuple[int, int, int], int] = {}
    for cells in cell_arrays:
        rgb = cells[..., :3].astype(np.int32)
        a = cells[..., 3]
        h, w = a.shape
        if h < 3 or w < 3:
            continue
        centre = rgb[1:-1, 1:-1]
        agree = np.zeros(centre.shape[:2], dtype=int)
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb = rgb[1 + dy:h - 1 + dy, 1 + dx:w - 1 + dx]
            agree += (np.abs(nb - centre).sum(axis=2) <= 24)
        mask = (agree >= 3) & (a[1:-1, 1:-1] > 0)
        for px in centre[mask]:
            key = (int(px[0]), int(px[1]), int(px[2]))
            found[key] = found.get(key, 0) + 1
    if not found:
        return np.zeros((0, 3), dtype=np.uint8), np.zeros(0, dtype=np.int64)
    uniq = np.array(sorted(found), dtype=np.uint8)
    counts = np.array([found[tuple(int(v) for v in row)] for row in uniq],
                      dtype=np.int64)
    return uniq, counts


def snap_to_palette(
    cell_arrays: list[np.ndarray], max_colors: int
) -> tuple[list[np.ndarray], np.ndarray]:
    """Map snapped cells to one shared palette.

    Snapped cell colours are smeared versions of the true palette
    colours (the damage blends each cell toward its neighbours).  The
    palette is therefore built from CONFIDENT colours — samples from
    flat regions, which the damage could not contaminate — grouped so
    near-identical values merge, keeping the most frequent exact
    values, with each group's representative the count-weighted median
    of its samples (which converges to the exact true value).  Every
    cell is then mapped to its nearest palette colour, so smeared
    samples are pulled back to a true colour instead of surviving as
    palette entries of their own.  Falls back to plain grouping /
    median-cut when no confident colours exist.
    """
    if max_colors < 1:
        raise ValueError("max_colors must be >= 1")
    flat = np.concatenate([c.reshape(-1, 4) for c in cell_arrays], axis=0)
    has_transparent = bool((flat[:, 3] == 0).any())
    opaque = flat[flat[:, 3] > 0]
    budget = max_colors - (1 if has_transparent else 0)
    palette: np.ndarray | None = None
    if len(opaque) and budget > 0:
        conf_uniq, conf_counts = _confident_colors(cell_arrays)
        conf_reps = _group_reps(conf_uniq, conf_counts) if len(conf_uniq) else []
        uniq, counts = np.unique(opaque[:, :3], axis=0, return_counts=True)
        all_groups = _group_reps(uniq, counts)
        reps: list[tuple[int, int, int]] = []
        if len(conf_reps) >= 2 and len(conf_reps) <= budget:
            # Confident colours are the trustworthy core.  Add sample
            # groups that are far from every confident colour and have
            # real support — thin features and crisp-damage colours
            # have no flat region to be "confident" in.
            reps = [rep for rep, _s in conf_reps]
            rep_arr = np.array(reps, dtype=np.int32)
            additions = []
            for rep, support in all_groups:
                if support < 2:
                    continue
                d = np.abs(rep_arr - np.array(rep, dtype=np.int32)).sum(axis=1)
                if len(d) and int(d.min()) > 48:
                    additions.append((rep, support))
            additions.sort(key=lambda t: -t[1])
            for rep, _s in additions:
                if len(reps) >= budget:
                    break
                reps.append(rep)
        elif len(all_groups) <= budget:
            reps = [rep for rep, _s in all_groups]
        if reps:
            entries = ([TRANSPARENT] if has_transparent else [])
            entries += [(r, g, b, 255) for (r, g, b) in reps]
            palette = np.array(entries, dtype=np.uint8)
    if palette is None:
        palette = build_palette(cell_arrays, max_colors)
    return [apply_palette(c, palette) for c in cell_arrays], palette


def apply_palette(cells: np.ndarray, palette: np.ndarray) -> np.ndarray:
    """Map every cell to its nearest palette colour (RGBA euclidean)."""
    h, w = cells.shape[:2]
    flat = cells.reshape(-1, 4).astype(np.float64)
    pal = palette.astype(np.float64)
    out = np.empty_like(flat, dtype=np.uint8)
    chunk = 8192
    for s in range(0, len(flat), chunk):
        part = flat[s:s + chunk]
        d = ((part[:, None, :] - pal[None, :, :]) ** 2).sum(axis=2)
        out[s:s + chunk] = palette[np.argmin(d, axis=1)]
    return out.reshape(h, w, 4)


def palette_hexes(palette: np.ndarray) -> list[str]:
    out = []
    for r, g, b, a in palette:
        if a == 255:
            out.append(f"#{r:02X}{g:02X}{b:02X}")
        else:
            out.append(f"#{r:02X}{g:02X}{b:02X}{a:02X}")
    return out


def write_gpl(path: str, palette: np.ndarray, name: str = "snapgrid") -> None:
    lines = ["GIMP Palette", f"Name: {name}", "Columns: 8", "#"]
    for i, (r, g, b, a) in enumerate(palette):
        label = "transparent" if a == 0 else f"color {i}"
        lines.append(f"{r:3d} {g:3d} {b:3d} {label}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_json(path: str, palette: np.ndarray) -> None:
    data = {
        "name": "snapgrid palette",
        "count": int(len(palette)),
        "colors": palette_hexes(palette),
        "rgba": [[int(v) for v in row] for row in palette],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
