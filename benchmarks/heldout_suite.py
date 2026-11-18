"""Held-out benchmark for snapgrid's v2 detector (frozen-split protocol).

The v2 detector (description-length + phase-evidence arbitration) was
developed on a DEV split only:

  * gentle suite sprite indices 0-5 (all classes), and
  * harsh suite indices 0-17 (`tests/spritefactory.build_suite(18)`).

Everything in THIS file was measured only after the detector and its
abstention thresholds were frozen:

  * gentle-style cases on sprites `make_sprite(2000 + j)` -- different
    RNG seeds, and a shifted size/generator alignment (2000 % 9 = 2),
    damaged with the gentle suite's single-corruption protocol;
  * harsh indices 18-35 (the half of the harsh suite never tuned on);
  * a fresh harsh-style set: sprites `make_sprite(3000 + j)` damaged
    with `corrupt(img, 3000 + j)` (different damage seeds and a
    shifted resample cycle).

Counting is identical to the gentle suite: exact / abstain / wrong,
plus pixel-exactness on exact-grid cases.

Run:  python benchmarks/heldout_suite.py
"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

import numpy as np
from PIL import Image, ImageFilter

from spritefactory import build_suite, corrupt, make_sprite  # noqa: E402
from snapgrid.core import estimate_grid, snap, AbstainError, SnapError  # noqa: E402
from snapgrid.palette import snap_to_palette  # noqa: E402


def upscale(img, factor, resample):
    w, h = img.size
    return img.resize((max(8, round(w * factor)), max(8, round(h * factor))), resample)


def make_case_heldout(cls, j):
    """Gentle protocol, held-out sprite (seed offset 2000)."""
    orig = make_sprite(2000 + j)
    factor = 3.1 + (j % 5) * 0.47
    if cls == "a_bilinear_only":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR)
    elif cls == "b_bicubic_only":
        dmg = upscale(orig, factor, Image.Resampling.BICUBIC)
    elif cls == "c_blur04":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR).filter(
            ImageFilter.GaussianBlur(0.4))
    elif cls == "d_jpeg85":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR).convert("RGB")
        buf = io.BytesIO()
        dmg.save(buf, format="JPEG", quality=85)
        buf.seek(0)
        jj = Image.open(buf)
        jj.load()
        dmg = jj.convert("RGBA")
    elif cls == "e_nearest_integer":
        f = 3 if j % 2 == 0 else 4
        dmg = orig.resize((orig.size[0] * f, orig.size[1] * f),
                          Image.Resampling.NEAREST)
    else:
        raise ValueError(cls)
    if cls != "d_jpeg85":
        buf = io.BytesIO()
        dmg.save(buf, format="PNG")
        buf.seek(0)
        d2 = Image.open(buf)
        d2.load()
        dmg = d2
    return orig, dmg


def count(cases, label):
    ex = ab = wr = 0
    pix = []
    for orig, dmg in cases:
        arr = np.asarray(dmg)
        try:
            est = estimate_grid([arr])
        except (AbstainError, SnapError):
            ab += 1
            continue
        if (est.nx, est.ny) != orig.size:
            wr += 1
            continue
        ex += 1
        snapped = snap(arr, est)
        mapped, _pal = snap_to_palette([snapped], 256)
        pix.append(float((mapped[0] == np.asarray(orig)).all(axis=2).mean()))
    n = len(cases)
    print(f"{label}: n={n} exact={ex} ({ex / n:.1%}) abstain={ab} wrong={wr} "
          f"pix_exact_on_exact={np.mean(pix) if pix else 0:.3f}")
    return {"n": n, "exact": ex, "abstain": ab, "wrong": wr}


def run():
    total = {"n": 0, "exact": 0, "abstain": 0, "wrong": 0}

    def add(res):
        for k in total:
            total[k] += res[k]

    classes = [("a_bilinear_only", 12), ("b_bicubic_only", 12),
               ("c_blur04", 12), ("d_jpeg85", 12), ("e_nearest_integer", 8)]
    for cls, n in classes:
        add(count([make_case_heldout(cls, j) for j in range(n)],
                  f"heldout gentle {cls}"))
    add(count(build_suite(36)[18:], "heldout harsh indices 18-35"))
    fresh = []
    for j in range(18):
        orig = make_sprite(3000 + j)
        fresh.append((orig, corrupt(orig, 3000 + j)))
    add(count(fresh, "heldout harsh fresh seeds 3000+"))
    print(f"HELD-OUT TOTAL: n={total['n']} exact={total['exact']} "
          f"({total['exact'] / total['n']:.1%}) abstain={total['abstain']} "
          f"wrong={total['wrong']}")
    return total


if __name__ == "__main__":
    run()
