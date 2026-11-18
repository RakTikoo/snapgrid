"""Non-uniform-resize benchmark for snapgrid.

Sprites are resized by DIFFERENT factors in x and y, so no square
grid exists: the only correct outcome is abstention.  Any emitted
grid is a wrong grid -- the failure mode the v2 re-review blocked
publication over (see REVIEW.md).

Protocol: sprites `make_sprite(9000 + j)` -- an index range used by
no other suite.  These seeds were NOT used to calibrate the
verification thresholds (calibration used a separate dev set at
8000+ plus the genuine suites); this set is the acceptance
measurement, frozen with the fix.  24 cases: both stretch
directions, bilinear/bicubic/nearest, with and without blur 0.4.

Run:  python benchmarks/nonuniform_suite.py
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import numpy as np
from PIL import Image, ImageFilter

from spritefactory import make_sprite  # noqa: E402
from snapgrid.core import estimate_grid, AbstainError, SnapError  # noqa: E402

FACTOR_PAIRS = [(3.0, 5.0), (5.0, 3.0), (2.6, 4.2), (4.2, 2.6),
                (2.0, 4.0), (4.0, 2.0), (3.8, 2.4), (2.4, 3.8),
                (3.0, 4.5), (4.5, 3.0), (2.5, 5.0), (5.0, 2.5)]
RESAMPLES = [Image.Resampling.BILINEAR, Image.Resampling.BICUBIC,
             Image.Resampling.NEAREST]
RESAMPLE_NAMES = ["bilinear", "bicubic", "nearest"]


def make_case(j: int):
    orig = make_sprite(9000 + j)
    fx, fy = FACTOR_PAIRS[j % len(FACTOR_PAIRS)]
    resample = RESAMPLES[j % 3]
    w, h = orig.size
    dmg = orig.resize((max(8, round(w * fx)), max(8, round(h * fy))),
                      resample)
    blur = resample != Image.Resampling.NEAREST and j % 2 == 0
    if blur:
        dmg = dmg.filter(ImageFilter.GaussianBlur(0.4))
    buf = io.BytesIO()
    dmg.save(buf, format="PNG")
    buf.seek(0)
    d2 = Image.open(buf)
    d2.load()
    return orig, d2, fx, fy, RESAMPLE_NAMES[j % 3], blur


def run(n: int = 24):
    abstain = wrong = 0
    for j in range(n):
        orig, dmg, fx, fy, rname, blur = make_case(j)
        arr = np.asarray(dmg)
        try:
            est = estimate_grid([arr])
        except (AbstainError, SnapError):
            abstain += 1
            continue
        wrong += 1
        print(f"case {j:2d}: true={orig.size} f=({fx},{fy}) {rname} "
              f"blur={blur} -> WRONG grid {est.nx}x{est.ny} emitted")
    print(f"NON-UNIFORM TOTAL: n={n} abstain={abstain} wrong={wrong}")
    return {"n": n, "abstain": abstain, "wrong": wrong}


if __name__ == "__main__":
    run()
