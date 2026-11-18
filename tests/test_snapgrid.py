"""snapgrid test suite (stdlib unittest; run via run_tests.py).

The centrepiece is a generated ground-truth suite: synthetic sprites
on KNOWN grids with KNOWN palettes, damaged the way real 'fake pixel
art' is damaged, then measured — exact grid-size recovery rate and
pixel-exactness of the output.  The asserted floors are set BELOW the
numbers measured during development (published in the README) so the
tests catch regressions without pretending the tool is better than
it is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from snapgrid.core import (  # noqa: E402
    AbstainError,
    GridEstimate,
    SnapError,
    estimate_grid,
    snap,
)
from snapgrid.palette import snap_to_palette  # noqa: E402
from spritefactory import build_suite, corrupt, make_sprite  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.abspath(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "snapgrid", *args],
        capture_output=True, text=True, env=env, timeout=300,
    )


def gradient_image() -> np.ndarray:
    xs, ys = np.meshgrid(np.linspace(0, 1, 200), np.linspace(0, 1, 150))
    return np.dstack([xs * 200 + 30, ys * 180 + 20, (1 - xs) * 150 + 60,
                      np.full((150, 200), 255)]).astype(np.uint8)


def photo_noise_image() -> np.ndarray:
    rng = np.random.default_rng(7)
    noise = Image.fromarray(
        rng.normal(128, 40, (150, 200)).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(3))
    na = np.asarray(noise).astype(np.float64)
    photo = np.dstack([na, na * 0.9 + 10, na * 0.8 + 20,
                       np.full((150, 200), 255)]).clip(0, 255).astype(np.uint8)
    return (photo.astype(np.float64)
            + rng.normal(0, 6, photo.shape)).clip(0, 255).astype(np.uint8)


class GroundTruthSuiteTest(unittest.TestCase):
    """Measured recovery on the generated ground-truth suite."""

    @classmethod
    def setUpClass(cls):
        cls.results = []  # (index, outcome, pix) outcome in exact/abstain/wrong
        for i, (orig, dmg) in enumerate(build_suite(36)):
            ow, oh = orig.size
            arr = np.asarray(dmg)
            try:
                est = estimate_grid([arr])
            except (AbstainError, SnapError):
                cls.results.append((i, "abstain", 0.0))
                continue
            if (est.nx, est.ny) != (ow, oh):
                cls.results.append((i, "wrong", 0.0))
                continue
            snapped = snap(arr, est)
            mapped, _pal = snap_to_palette([snapped], 256)
            pix = float((mapped[0] == np.asarray(orig)).all(axis=2).mean())
            cls.results.append((i, "exact", pix))

    def test_exact_grid_recovery_rate(self):
        # v2 detector, re-measured 2026-11-17: 34/36 (94.4%).
        # (v1 measured 20/36; the floor is set below the v2 number so
        # the test catches regressions without overstating the tool.)
        exact = sum(1 for _i, o, _p in self.results if o == "exact")
        self.assertGreaterEqual(exact, 30,
                                f"only {exact}/36 exact grids recovered")

    def test_wrong_grids_are_rare(self):
        # v2 re-measured: 1/36 emitted a wrong grid (index 24: an 8x8
        # nearest-upscaled blob whose damaged image is also explained
        # exactly by a 20x20 grid -- a documented aliasing limit).
        wrong = sum(1 for _i, o, _p in self.results if o == "wrong")
        self.assertLessEqual(wrong, 3, f"{wrong}/36 wrong grids emitted")

    def test_abstention_is_used(self):
        # v2 re-measured: only 1/36 abstains now (v1 abstained on
        # 13/36 because it could not find those grids at all).  The
        # safety valve is exercised by the adversarial tests and the
        # B1/B2/B3 regression tests below; here we assert it still
        # fires on this suite at least once.
        abst = sum(1 for _i, o, _p in self.results if o == "abstain")
        self.assertGreaterEqual(abst, 1)

    def test_pixel_exactness_on_exact_grids(self):
        # v2 re-measured: mean 0.273 over exact-grid cases.  (v1's
        # 0.327 was averaged over far fewer, easier exact cases; v2
        # recovers many more smooth-damage grids, whose colours are
        # information-limited, so the mean over a larger exact set is
        # lower even though per-class recovery improved -- within-8
        # per channel on exact cases rose 47.7% -> 67.6%.)
        pix = [p for _i, o, p in self.results if o == "exact"]
        self.assertTrue(pix)
        self.assertGreaterEqual(float(np.mean(pix)), 0.25)

    def test_nearest_damage_recovers_pixels_almost_exactly(self):
        # Suite indices with i % 5 == 4 took the NEAREST corruption
        # path.  v2 re-measured: 6/7 exact grids (index 24 is the
        # documented aliasing case), 100% pixel-exactness on them.
        pix = [p for i, o, p in self.results if o == "exact" and i % 5 == 4]
        self.assertGreaterEqual(len(pix), 5)
        self.assertGreaterEqual(float(np.mean(pix)), 0.9)


class InvariantsTest(unittest.TestCase):
    def test_output_dimensions_equal_recovered_grid(self):
        orig, dmg = build_suite(36)[29]  # reliable recovery
        arr = np.asarray(dmg)
        est = estimate_grid([arr])
        out = snap(arr, est)
        self.assertEqual(out.shape, (est.ny, est.nx, 4))
        self.assertEqual((est.nx, est.ny), orig.size)

    def test_palette_never_exceeds_cap(self):
        orig, dmg = build_suite(36)[19]
        arr = np.asarray(dmg)
        est = estimate_grid([arr])
        snapped = snap(arr, est)
        _mapped, palette = snap_to_palette([snapped], 8)
        self.assertLessEqual(len(palette), 8)

    def test_binary_alpha_stays_binary(self):
        # Character sprites (odd indices 3 mod 4) have transparent,
        # binary-alpha backgrounds.
        orig, dmg = build_suite(36)[30]
        self.assertTrue(set(np.unique(np.asarray(orig)[..., 3])) <= {0, 255})
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.png")
            out = os.path.join(td, "out.png")
            dmg.save(src)
            proc = run_cli("fix", src, "--out", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            alpha = np.asarray(Image.open(out))[..., 3]
            self.assertTrue(set(np.unique(alpha)) <= {0, 255},
                            f"non-binary alpha values: {np.unique(alpha)}")


class SheetModeTest(unittest.TestCase):
    def test_folder_of_frames_shares_one_grid_and_palette(self):
        base = make_sprite(29)  # 24x16 shape sprite
        with tempfile.TemporaryDirectory() as td:
            src_dir = os.path.join(td, "frames")
            os.makedirs(src_dir)
            for k in range(3):
                rolled = Image.fromarray(
                    np.roll(np.asarray(base), k, axis=1), mode="RGBA")
                corrupt(rolled, 29).save(os.path.join(src_dir, f"f{k}.png"))
            out_dir = os.path.join(td, "out")
            proc = run_cli("sheet", src_dir, "--out", out_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            outputs = sorted(f for f in os.listdir(out_dir)
                             if f.startswith("f") and f.endswith(".png"))
            self.assertEqual(len(outputs), 3)
            shapes = {Image.open(os.path.join(out_dir, f)).size
                      for f in outputs}
            # ONE grid for all frames: identical output dimensions,
            # and they are the true sprite dimensions.
            self.assertEqual(shapes, {(24, 16)})
            with open(os.path.join(out_dir, "palette.palette.json")) as fh:
                pal = json.load(fh)
            self.assertLessEqual(pal["count"], 256)

    def test_sheet_image_split(self):
        base = make_sprite(29)
        # Tile individually damaged frames into one sheet image whose
        # dimensions divide exactly by the 2x2 frame grid.
        dmg_frames = []
        for k in range(4):
            rolled = Image.fromarray(
                np.roll(np.asarray(base), k, axis=1), mode="RGBA")
            dmg_frames.append(np.asarray(corrupt(rolled, 29)))
        fh, fw = dmg_frames[0].shape[:2]
        sheet = np.zeros((2 * fh, 2 * fw, 4), dtype=np.uint8)
        for idx, fr in enumerate(dmg_frames):
            r, c = divmod(idx, 2)
            sheet[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw] = fr
        dmg_sheet = Image.fromarray(sheet, mode="RGBA")
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "sheet.png")
            dmg_sheet.save(src)
            out_dir = os.path.join(td, "out")
            proc = run_cli("sheet", src, "--rows", "2", "--cols", "2",
                           "--out", out_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(os.path.join(out_dir, "sheet.png")))
            frames_out = [f for f in os.listdir(out_dir)
                          if f.startswith("frame_")]
            self.assertEqual(len(frames_out), 4)
            shapes = {Image.open(os.path.join(out_dir, f)).size
                      for f in frames_out}
            self.assertEqual(len(shapes), 1)  # one shared grid


class AdversarialTest(unittest.TestCase):
    def test_smooth_gradient_abstains(self):
        with self.assertRaises(AbstainError):
            estimate_grid([gradient_image()])

    def test_photo_noise_abstains(self):
        with self.assertRaises(AbstainError):
            estimate_grid([photo_noise_image()])

    def test_adversarial_cli_exit_3_and_no_output(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "grad.png")
            Image.fromarray(gradient_image(), mode="RGBA").save(src)
            out = os.path.join(td, "out.png")
            proc = run_cli("fix", src, "--out", out)
            self.assertEqual(proc.returncode, 3, proc.stderr)
            self.assertFalse(os.path.exists(out))
            self.assertNotIn("Traceback", proc.stderr)


class ErrorHandlingTest(unittest.TestCase):
    def test_tiny_image_clean_error(self):
        tiny = np.zeros((4, 4, 4), dtype=np.uint8)
        tiny[..., 3] = 255
        with self.assertRaises(SnapError):
            estimate_grid([tiny])

    def test_fully_transparent_clean_error(self):
        with self.assertRaises(SnapError):
            estimate_grid([np.zeros((64, 64, 4), dtype=np.uint8)])

    def test_non_image_file_clean_error_no_traceback(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "notes.txt")
            with open(src, "w") as fh:
                fh.write("this is not an image\n")
            proc = run_cli("fix", src, "--out", os.path.join(td, "o.png"))
            self.assertEqual(proc.returncode, 2)
            self.assertIn("error:", proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_missing_sheet_split_args_clean_error(self):
        orig, dmg = build_suite(36)[29]
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "sheet.png")
            dmg.save(src)
            proc = run_cli("sheet", src, "--out", os.path.join(td, "out"))
            self.assertEqual(proc.returncode, 2)
            self.assertNotIn("Traceback", proc.stderr)


class ReviewRegressionTest(unittest.TestCase):
    """Regressions for the independent review's findings (REVIEW.md)."""

    def test_b1_mixed_grid_folder_abstains(self):
        # B1: frames from different grids in one folder must not be
        # silently forced onto the dominant frame's grid.
        base = make_sprite(2)  # 24x16
        dmg_a = corrupt(base, 2)
        other = make_sprite(0)  # 12x12, different grid
        dmg_b = other.resize(dmg_a.size, Image.Resampling.BILINEAR).filter(
            ImageFilter.GaussianBlur(0.5))
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([np.asarray(dmg_a), np.asarray(dmg_b)])
        self.assertIn("frames disagree", str(ctx.exception))

    def test_b2_large_sprite_not_rejected_by_edge_gate(self):
        # B2: a 64x64 sprite upscaled 3.7x + blur 0.5 is in scope; the
        # old fixed edge-density gate falsely abstained on it.
        import random

        from spritefactory import GENERATORS
        rng = random.Random(778)
        big = GENERATORS[1](rng, 64, 64, 12)  # shape sprite
        up = big.resize((round(64 * 3.7), round(64 * 3.7)),
                        Image.Resampling.BILINEAR).filter(
            ImageFilter.GaussianBlur(0.5))
        est = estimate_grid([np.asarray(up)])
        self.assertEqual((est.nx, est.ny), (64, 64))

    def test_b3_clean_pixel_art_abstains_with_honest_message(self):
        # B3: clean native pixel art abstains (safe), and the message
        # must say it may already be clean instead of calling it a
        # photo.
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([np.asarray(make_sprite(2))])
        self.assertIn("already be clean pixel art", str(ctx.exception))

    def test_nonuniform_resize_abstains(self):
        # A sprite stretched 3x in x and 5x in y has no square grid;
        # the v2 detector must abstain, not fit a square approximation.
        orig = make_sprite(1)
        irr = orig.resize((orig.size[0] * 3, orig.size[1] * 5),
                          Image.Resampling.BILINEAR)
        with self.assertRaises(AbstainError):
            estimate_grid([np.asarray(irr)])


class NonUniformRegressionTest(unittest.TestCase):
    """Non-uniform resize must abstain, never emit a square fit.

    Regression for the v2 re-review's blocking defect (REVIEW.md):
    sprites stretched by different factors per axis used to receive
    confident wrong grids (8 of 13 fresh trials) because the winner
    kept one axis's true count, forced the other axis to a pitch
    matching it, and faked the per-axis phase check.  The winner
    verification now cross-checks each axis's pitch against that
    axis's own breakpoint lattice and against coarser same-axis
    challengers.  Sprites 7000+ are used by no other suite.
    """

    CASES = [
        # (sprite index, fx, fy, resample, blur) -- both stretch
        # directions, all three resample modes, with and without blur
        (7000, 3.0, 5.0, Image.Resampling.BILINEAR, True),
        (7001, 5.0, 3.0, Image.Resampling.BICUBIC, False),
        (7002, 2.6, 4.2, Image.Resampling.NEAREST, False),
        (7003, 4.2, 2.6, Image.Resampling.BILINEAR, False),
        (7005, 5.0, 2.5, Image.Resampling.NEAREST, False),
        (7007, 2.4, 3.8, Image.Resampling.BICUBIC, False),
    ]

    @staticmethod
    def _damaged(idx, fx, fy, resample, blur):
        orig = make_sprite(idx)
        w, h = orig.size
        dmg = orig.resize((max(8, round(w * fx)), max(8, round(h * fy))),
                          resample)
        if blur:
            dmg = dmg.filter(ImageFilter.GaussianBlur(0.4))
        return np.asarray(dmg)

    def test_nonuniform_resizes_abstain(self):
        for idx, fx, fy, resample, blur in self.CASES:
            with self.subTest(sprite=idx, fx=fx, fy=fy):
                arr = self._damaged(idx, fx, fy, resample, blur)
                with self.assertRaises(AbstainError):
                    estimate_grid([arr])

    def test_nonuniform_abstention_message_is_plain(self):
        # The user-facing message must say, in plain words, that the
        # image appears stretched differently in each direction.
        arr = self._damaged(7002, 2.6, 4.2, Image.Resampling.NEAREST, False)
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([arr])
        self.assertIn("stretched differently in each direction",
                      str(ctx.exception))

    @staticmethod
    def _benchmark_case(base: int, j: int) -> np.ndarray:
        # Mirrors benchmarks/nonuniform_suite.make_case on an
        # arbitrary seed base: sprite base+j, FACTOR_PAIRS[j % 12],
        # RESAMPLES[j % 3], blur on even non-nearest cases, PNG
        # round-trip.
        import io

        factor_pairs = [(3.0, 5.0), (5.0, 3.0), (2.6, 4.2), (4.2, 2.6),
                        (2.0, 4.0), (4.0, 2.0), (3.8, 2.4), (2.4, 3.8),
                        (3.0, 4.5), (4.5, 3.0), (2.5, 5.0), (5.0, 2.5)]
        resamples = [Image.Resampling.BILINEAR, Image.Resampling.BICUBIC,
                     Image.Resampling.NEAREST]
        orig = make_sprite(base + j)
        fx, fy = factor_pairs[j % len(factor_pairs)]
        resample = resamples[j % 3]
        w, h = orig.size
        dmg = orig.resize((max(8, round(w * fx)), max(8, round(h * fy))),
                          resample)
        if resample != Image.Resampling.NEAREST and j % 2 == 0:
            dmg = dmg.filter(ImageFilter.GaussianBlur(0.4))
        buf = io.BytesIO()
        dmg.save(buf, format="PNG")
        buf.seek(0)
        d2 = Image.open(buf)
        d2.load()
        return np.asarray(d2)

    def test_crosspool_nonuniform_abstains(self):
        # Deep-fix round, fresh-seed case (20000+ set, case 6): an
        # 18x14 sprite stretched 3.8x/2.4x (bilinear + blur) was
        # emitted as 18x10.  Each single-pool lattice test just
        # missed it (5 profile breakpoints; pooled fundamental
        # concentration 0.81), but BOTH pools' fundamental periods
        # were the y axis's true pitch -- the cross-pool agreement
        # refutation now abstains.
        arr = self._benchmark_case(20000, 6)
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([arr])
        self.assertIn("stretched differently in each direction",
                      str(ctx.exception))

    def test_tiny_bicubic_nonuniform_abstains(self):
        # Deep-fix round, fresh-seed case (20000+ set, case 13): an
        # 8x8 sprite stretched 5.0x/3.0 (bicubic, damaged 40x24) was
        # emitted as 14x8 with confidence 0.83 -- inside the
        # small-image guard's scope, its x axis "confirmed" by a
        # pooled fundamental 17% off the winner's pitch, which the
        # refutation's 20% tolerance had let count as agreement.
        # The guard's confirmation tolerance is now 8%: the axis no
        # longer confirms, and the guard refuses the grid.
        arr = self._benchmark_case(20000, 13)
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([arr])
        msg = str(ctx.exception)
        self.assertIn("very small", msg)
        self.assertIn("cannot be recovered safely", msg)

    def test_merged_axis_nonuniform_abstains(self):
        # The deep-fix round's case (REVIEW.md): a 16x24 sprite
        # stretched 2.0x in x and 4.0x in y (bicubic + blur 0.4, PNG
        # round-trip -- the non-uniform benchmark's protocol on the
        # 8000+ dev indices, case 4).  The square sweep won 8x24 by
        # MERGING pairs of true x cells: at the smeared 2.0 px true
        # pitch the breakpoint lattices stay silent and no coarser
        # challenger applies, so the case emitted a confident wrong
        # grid (0.45) outside the small-image guard's scope (damaged
        # short side 32 px).  The finer-harmonic verification now
        # refutes it: doubling the x count cuts the exact error to
        # 0.73x with real phase sensitivity of its own.
        import io

        orig = make_sprite(8004)
        w, h = orig.size
        dmg = orig.resize((round(w * 2.0), round(h * 4.0)),
                          Image.Resampling.BICUBIC)
        dmg = dmg.filter(ImageFilter.GaussianBlur(0.4))
        buf = io.BytesIO()
        dmg.save(buf, format="PNG")
        buf.seek(0)
        d2 = Image.open(buf)
        d2.load()
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([np.asarray(d2)])
        self.assertIn("stretched differently in each direction",
                      str(ctx.exception))


class SmallImageGuardTest(unittest.TestCase):
    """The small-image guard: tiny non-uniform sprites must abstain.

    After the winner-verification fix, the only wrong grids left on
    the 24-case non-uniform acceptance set
    (benchmarks/nonuniform_suite.py) were two 8x8 sprites whose
    forced axis is ~20 px long and yields 3-5 usable breakpoints --
    too few for the lattice refutations to fire, and statistically
    indistinguishable from genuine axes there.  The guard therefore
    requires, on very small images only (short side <= 24 px), that
    BOTH axes' own breakpoint lattices positively confirm the
    winner's pitch.  These tests pin the two acceptance cases
    (built exactly as the benchmark builds them, PNG round-trip
    included) and the genuine tiny recoveries that must survive.
    """

    @staticmethod
    def _acceptance_case(j: int) -> np.ndarray:
        # Mirrors benchmarks/nonuniform_suite.make_case for the two
        # residual indices: sprite 9000+j, FACTOR_PAIRS[j % 12],
        # RESAMPLES[j % 3], blur on even non-nearest cases.
        import io

        factor_pairs = [(3.0, 5.0), (5.0, 3.0), (2.6, 4.2), (4.2, 2.6),
                        (2.0, 4.0), (4.0, 2.0), (3.8, 2.4), (2.4, 3.8),
                        (3.0, 4.5), (4.5, 3.0), (2.5, 5.0), (5.0, 2.5)]
        resamples = [Image.Resampling.BILINEAR, Image.Resampling.BICUBIC,
                     Image.Resampling.NEAREST]
        orig = make_sprite(9000 + j)
        fx, fy = factor_pairs[j % len(factor_pairs)]
        resample = resamples[j % 3]
        w, h = orig.size
        dmg = orig.resize((max(8, round(w * fx)), max(8, round(h * fy))),
                          resample)
        if resample != Image.Resampling.NEAREST and j % 2 == 0:
            dmg = dmg.filter(ImageFilter.GaussianBlur(0.4))
        buf = io.BytesIO()
        dmg.save(buf, format="PNG")
        buf.seek(0)
        d2 = Image.open(buf)
        d2.load()
        return np.asarray(d2)

    def test_tiny_nonuniform_sprites_abstain(self):
        # Acceptance cases 6 and 15: before the guard these emitted
        # a confident wrong 8x6 grid; now they must abstain.
        for j in (6, 15):
            with self.subTest(case=j):
                with self.assertRaises(AbstainError):
                    estimate_grid([self._acceptance_case(j)])

    def test_tiny_nonuniform_guard_message_is_plain(self):
        # The guard's message must say, in plain words, that the
        # image is very small, appears stretched differently in each
        # direction, and cannot be recovered safely.
        with self.assertRaises(AbstainError) as ctx:
            estimate_grid([self._acceptance_case(6)])
        msg = str(ctx.exception)
        self.assertIn("very small", msg)
        self.assertIn("stretched differently in each direction", msg)
        self.assertIn("cannot be recovered safely", msg)

    def test_tiny_uniform_sprite_still_recovers(self):
        # A tiny sprite resized UNIFORMLY must still recover: the
        # measured in-scope genuine cases are 8x8 sprites upscaled
        # 3x with nearest resampling (24x24 damaged), whose lattice
        # support is exact.  Sprites 6 and 2004 are the gentle /
        # held-out instances of that construction.
        for idx in (6, 2004):
            with self.subTest(sprite=idx):
                orig = make_sprite(idx)
                dmg = orig.resize((orig.size[0] * 3, orig.size[1] * 3),
                                  Image.Resampling.NEAREST)
                est = estimate_grid([np.asarray(dmg)])
                self.assertEqual((est.nx, est.ny), orig.size)


class CliFixTest(unittest.TestCase):
    def test_fix_writes_output_and_palette_sidecars(self):
        orig, dmg = build_suite(36)[29]
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "in.png")
            out = os.path.join(td, "fixed.png")
            dmg.save(src)
            proc = run_cli("fix", src, "--out", out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("recovered grid: 24x16", proc.stdout)
            self.assertEqual(Image.open(out).size, (24, 16))
            gpl = os.path.join(td, "fixed.palette.gpl")
            js = os.path.join(td, "fixed.palette.json")
            self.assertTrue(os.path.exists(gpl))
            self.assertTrue(os.path.exists(js))
            with open(js) as fh:
                pal = json.load(fh)
            self.assertEqual(pal["count"], len(pal["colors"]))
            self.assertLessEqual(pal["count"], 256)


if __name__ == "__main__":
    unittest.main()
