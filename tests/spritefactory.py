"""Deterministic synthetic-sprite factory for snapgrid's ground-truth suite.

Every sprite is drawn programmatically on a KNOWN grid with a KNOWN
palette, then damaged the way real 'fake pixel art' is damaged:
non-integer upscale with a smoothing filter, slight blur, mild noise,
and a PNG re-save. Recovery is then measured against the original —
exact grid-size recovery rate and pixel-exactness of the output.
"""

from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BASE_COLORS = [
    (0, 0, 0), (255, 255, 255), (228, 56, 56), (56, 200, 84),
    (56, 96, 228), (240, 208, 64), (160, 84, 200), (240, 140, 48),
    (64, 208, 216), (136, 88, 56), (88, 160, 88), (216, 120, 160),
    (48, 48, 72), (120, 120, 136), (184, 200, 216), (96, 64, 40),
]


def _random_palette(rng: random.Random, k: int) -> list[tuple[int, int, int]]:
    """Draw k palette colours, pairwise L1 distance >= 60.

    A palette is a set of DISTINCT colours — real sprite palettes are
    well separated.  (An earlier version drew unconstrained random
    colours, which produced palette entries a few levels apart; after
    damage those are indistinguishable in principle, so the suite
    would have measured eyesight, not recovery.)
    """
    pal: list[tuple[int, int, int]] = []
    attempts = 0
    while len(pal) < k and attempts < 500:
        attempts += 1
        if rng.random() < 0.5:
            cand = rng.choice(BASE_COLORS)
        else:
            cand = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
        if all(abs(cand[0] - c[0]) + abs(cand[1] - c[1]) + abs(cand[2] - c[2]) >= 60
               for c in pal):
            pal.append(cand)
    return pal


def _to_image(cells: np.ndarray, palette: list) -> Image.Image:
    h, w = cells.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    for idx, (r, g, b) in enumerate(palette):
        arr[cells == idx] = (r, g, b, 255)
    return Image.fromarray(arr, mode="RGBA")


def _mode_smooth(cells: np.ndarray, passes: int = 1) -> np.ndarray:
    """Majority-filter a palette-INDEX grid (3x3 mode, ties -> centre).

    Smoothing indices — not image bands — keeps the sprite strictly
    palette-limited.  (PIL's ModeFilter works per band, so the mode of
    R, G and B can come from three different source pixels and invent
    hybrid colours that were never in the palette.)
    """
    out = cells.copy()
    for _ in range(passes):
        src = out
        nxt = src.copy()
        h, w = src.shape
        for y in range(h):
            for x in range(w):
                vals, counts = np.unique(
                    src[max(0, y - 1):y + 2, max(0, x - 1):x + 2],
                    return_counts=True)
                best = vals[int(np.argmax(counts))]
                # Keep the centre value on a tie (np.argmax takes the
                # smallest value, which would bias toward index 0).
                centre_count = counts[vals == src[y, x]]
                if len(centre_count) and centre_count[0] == counts.max():
                    best = src[y, x]
                nxt[y, x] = best
        out = nxt
    return out


def blob_sprite(rng: random.Random, w: int, h: int, k: int) -> Image.Image:
    """Random cells, majority-smoothed: organic blobs with flat runs."""
    palette = _random_palette(rng, k)
    cells = np.array([[rng.randrange(len(palette)) for _ in range(w)]
                      for _ in range(h)], dtype=np.int32)
    cells = _mode_smooth(cells, passes=2 if rng.random() < 0.7 else 1)
    return _to_image(cells, palette)


def shape_sprite(rng: random.Random, w: int, h: int, k: int) -> Image.Image:
    """Background + random rectangles, ellipses and lines."""
    palette = _random_palette(rng, k)
    img = Image.new("RGBA", (w, h), palette[0] + (255,))
    d = ImageDraw.Draw(img)
    for _ in range(rng.randrange(2, 6)):
        color = rng.choice(palette) + (255,)
        x0, y0 = rng.randrange(w), rng.randrange(h)
        x1, y1 = rng.randrange(w), rng.randrange(h)
        kind = rng.random()
        if kind < 0.4:
            d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                        fill=color)
        elif kind < 0.7:
            d.ellipse([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                      fill=color)
        else:
            d.line([x0, y0, x1, y1], fill=color, width=1)
    return img


def character_sprite(rng: random.Random, w: int, h: int, k: int) -> Image.Image:
    """Left-right symmetric random character on a transparent background."""
    palette = _random_palette(rng, k)
    half = (w + 1) // 2
    cells = np.zeros((h, w), dtype=np.int32)  # 0 = transparent
    body = np.array([[rng.randrange(len(palette) + 1) for _ in range(half)]
                     for _ in range(h)], dtype=np.int32)
    cells[:, :half] = body
    cells[:, half:] = body[:, ::-1][:, : w - half]
    cells = _mode_smooth(cells, passes=1)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    arr = np.asarray(img).copy()
    for idx, (r, g, b) in enumerate(palette, start=1):
        arr[cells == idx] = (r, g, b, 255)
    return Image.fromarray(arr, mode="RGBA")


def detail_sprite(rng: random.Random, w: int, h: int, k: int) -> Image.Image:
    """High-detail noise sprite: the hard case (few flat runs)."""
    palette = _random_palette(rng, k)
    cells = np.array([[rng.randrange(len(palette)) for _ in range(w)]
                      for _ in range(h)], dtype=np.int32)
    return _to_image(cells, palette)


GENERATORS = [blob_sprite, shape_sprite, character_sprite, detail_sprite]

SIZES = [(12, 12), (16, 16), (24, 16), (16, 24), (32, 32), (20, 20),
         (8, 8), (28, 20), (18, 14)]


def make_sprite(i: int) -> Image.Image:
    """Suite sprite #i (deterministic)."""
    rng = random.Random(10_000 + i)
    w, h = SIZES[i % len(SIZES)]
    gen = GENERATORS[i % len(GENERATORS)]
    k = [4, 6, 9, 12, 16][i % 5]
    return gen(rng, w, h, k)


def corrupt(img: Image.Image, i: int) -> Image.Image:
    """Damage sprite #i the standard way; deterministic per index."""
    rng = random.Random(20_000 + i)
    nrng = np.random.default_rng(30_000 + i)
    factor = rng.uniform(2.3, 5.7)
    w, h = img.size
    nw, nh = max(8, round(w * factor)), max(8, round(h * factor))
    resample = [Image.Resampling.BILINEAR, Image.Resampling.BICUBIC,
                Image.Resampling.BILINEAR, Image.Resampling.BICUBIC,
                Image.Resampling.NEAREST][i % 5]
    up = img.resize((nw, nh), resample)
    if resample != Image.Resampling.NEAREST:
        up = up.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.9)))
        arr = np.asarray(up).astype(np.float64)
        # Noise on the colour channels only.  (An earlier version added
        # the same noise to alpha, which made exact recovery impossible
        # in principle — real damage does not jitter a sprite's alpha
        # by +-4 levels, and no estimator can undo it exactly.)
        noise = nrng.normal(0.0, rng.uniform(1.5, 4.5), arr.shape[:2])[..., None]
        arr[..., :3] = np.clip(arr[..., :3] + noise, 0, 255)
        arr = arr.astype(np.uint8)
        up = Image.fromarray(arr, mode="RGBA")
    # PNG re-save round-trip, as in a file that has been passed around.
    buf = io.BytesIO()
    up.save(buf, format="PNG")
    buf.seek(0)
    out = Image.open(buf)
    out.load()
    return out


def build_suite(n: int = 36) -> list[tuple[Image.Image, Image.Image]]:
    """Return [(original, corrupted), ...] for the first n suite sprites."""
    suite = []
    for i in range(n):
        orig = make_sprite(i)
        suite.append((orig, corrupt(orig, i)))
    return suite
