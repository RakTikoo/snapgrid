"""Build examples/demo_compare.png: original | damaged | recovered.

A 24x24 sprite with big flat regions is damaged the way 'fake pixel
art' usually is — upscaled 4.3x with bilinear smoothing, a touch of
blur, mild noise, PNG re-save — then recovered with snapgrid itself.
The three panels are shown at the same large magnification so the
smoothing damage and the recovery are both plain to see.

This demo uses gentler damage than the ground-truth test suite (which
adds bicubic cases, heavier blur and stronger noise); the suite's
measured numbers are in the README.
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from snapgrid.core import estimate_grid, snap  # noqa: E402
from snapgrid.palette import snap_to_palette  # noqa: E402

PALETTE = [
    (24, 28, 48), (240, 240, 232), (228, 56, 56), (56, 96, 228),
    (240, 208, 64), (56, 200, 84), (240, 140, 48), (160, 84, 200),
]


def draw_sprite() -> Image.Image:
    img = Image.new("RGBA", (24, 24), PALETTE[0] + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([2, 14, 21, 22], fill=PALETTE[4] + (255,))       # ground
    d.rectangle([4, 6, 11, 13], fill=PALETTE[1] + (255,))        # house
    d.polygon([(3, 6), (12, 6), (7, 2)], fill=PALETTE[2] + (255,))  # roof
    d.rectangle([6, 9, 8, 13], fill=PALETTE[3] + (255,))         # door
    d.ellipse([15, 4, 21, 10], fill=PALETTE[5] + (255,))         # tree crown
    d.rectangle([17, 10, 18, 14], fill=PALETTE[6] + (255,))      # trunk
    d.rectangle([19, 17, 20, 18], fill=PALETTE[7] + (255,))      # flower
    d.rectangle([13, 18, 14, 19], fill=PALETTE[2] + (255,))      # flower
    return img


def damage(img: Image.Image) -> Image.Image:
    rng = np.random.default_rng(42)
    up = img.resize((round(24 * 4.3), round(24 * 4.3)), Image.Resampling.BILINEAR)
    from PIL import ImageFilter
    up = up.filter(ImageFilter.GaussianBlur(0.5))
    arr = np.asarray(up).astype(np.float64)
    noise = rng.normal(0.0, 2.5, arr.shape[:2])[..., None]
    arr[..., :3] = np.clip(arr[..., :3] + noise, 0, 255)
    out = Image.fromarray(arr.astype(np.uint8), mode="RGBA")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    buf.seek(0)
    res = Image.open(buf)
    res.load()
    return res


def main() -> None:
    orig = draw_sprite()
    dmg = damage(orig)
    arr = np.asarray(dmg)
    est = estimate_grid([arr])
    snapped = snap(arr, est)
    mapped, palette = snap_to_palette([snapped], 256)
    rec = Image.fromarray(mapped[0], mode="RGBA")
    exact = float((mapped[0] == np.asarray(orig)).all(axis=2).mean())
    print(f"demo: true grid 24x24, recovered {est.nx}x{est.ny}, "
          f"confidence {est.confidence:.2f}, palette {len(palette)} colours, "
          f"pixels exactly matching the original: {exact:.1%}")

    scale = 10
    panels = []
    for im in (orig, dmg.resize(orig.size, Image.Resampling.BILINEAR), rec):
        panels.append(im.resize((24 * scale, 24 * scale), Image.Resampling.NEAREST))
    sheet = Image.new("RGBA", (24 * scale * 3 + 16, 24 * scale), (32, 32, 32, 255))
    for i, p in enumerate(panels):
        sheet.paste(p, (i * (24 * scale + 8), 0))
    out = os.path.join(os.path.dirname(__file__), "demo_compare.png")
    sheet.save(out)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
