"""Command-line interface for snapgrid.

Exit codes: 0 = success, 2 = user error (clean one-line message),
3 = abstained (no clean grid found; nothing written).
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image

from . import __version__
from .core import AbstainError, SnapError, alpha_is_binary, estimate_grid, load_rgba, snap
from .palette import snap_to_palette, write_gpl, write_json

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


def _save(arr: np.ndarray, path: str) -> None:
    Image.fromarray(arr, mode="RGBA").save(path)


def _palette_sidecars(out_stem: str, palette: np.ndarray) -> tuple[str, str]:
    gpl = out_stem + ".palette.gpl"
    js = out_stem + ".palette.json"
    write_gpl(gpl, palette)
    write_json(js, palette)
    return gpl, js


def _snap_frames(arrays, est, max_colors):
    """Snap every frame on the shared grid and map to the shared palette."""
    binary = all(alpha_is_binary(a) for a in arrays)
    snapped = [snap(a, est) for a in arrays]
    mapped, palette = snap_to_palette(snapped, max_colors)
    if binary:
        # Invariant: binary alpha in -> binary alpha out.
        fixed = []
        for m in mapped:
            m = m.copy()
            hi = m[..., 3] >= 128
            m[..., 3] = np.where(hi, 255, 0)
            m[..., :3] = np.where(hi[..., None], m[..., :3], 0)
            fixed.append(m)
        mapped = fixed
    return mapped, palette


def _grid_summary(est, w: int, h: int) -> str:
    return (
        f"recovered grid: {est.nx}x{est.ny} "
        f"(cell ~{w / est.nx:.2f}x{h / est.ny:.2f}px, "
        f"offset {est.x.offset},{est.y.offset})\n"
        f"confidence: {est.confidence:.2f} "
        f"(resynthesis fit error {est.fit_error:.2f}/255)"
    )


def cmd_fix(args) -> int:
    arr = load_rgba(args.image)
    h, w = arr.shape[:2]
    est = estimate_grid([arr])
    frames, palette = _snap_frames([arr], est, args.max_colors)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    _save(frames[0], args.out)
    stem = os.path.splitext(args.out)[0]
    gpl, js = _palette_sidecars(stem, palette)
    print(f"input: {args.image} ({w}x{h})")
    print(_grid_summary(est, w, h))
    print(f"palette: {len(palette)} colors -> {gpl} / {js}")
    print(f"wrote: {args.out}")
    return 0


def _load_sheet_frames(args) -> tuple[list[np.ndarray], list[str], tuple[int, int] | None]:
    """Return (frames, names, sheet_shape) for a sheet image or a folder."""
    if os.path.isdir(args.image):
        if args.rows or args.cols:
            raise SnapError("--rows/--cols only apply to a single sheet image")
        names = sorted(
            f for f in os.listdir(args.image)
            if os.path.splitext(f)[1].lower() in IMAGE_SUFFIXES
        )
        if not names:
            raise SnapError(f"no image files found in folder: {args.image}")
        frames = [load_rgba(os.path.join(args.image, n)) for n in names]
        return frames, names, None
    if args.rows is None or args.cols is None:
        raise SnapError(
            "sheet image input needs --rows and --cols so frames can be split"
        )
    arr = load_rgba(args.image)
    h, w = arr.shape[:2]
    if h % args.rows or w % args.cols:
        raise SnapError(
            f"sheet size {w}x{h} is not divisible by {args.cols}x{args.rows} frames"
        )
    fh, fw = h // args.rows, w // args.cols
    frames, names = [], []
    for r in range(args.rows):
        for c in range(args.cols):
            frames.append(arr[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw].copy())
            names.append(f"frame_{r * args.cols + c:03d}.png")
    return frames, names, (args.rows, args.cols)


def cmd_sheet(args) -> int:
    frames, names, sheet_shape = _load_sheet_frames(args)
    h, w = frames[0].shape[:2]
    est = estimate_grid(frames)  # ONE grid, estimated jointly over all frames
    snapped, palette = _snap_frames(frames, est, args.max_colors)
    os.makedirs(args.out, exist_ok=True)
    for name, img in zip(names, snapped):
        _save(img, os.path.join(args.out, name))
    gpl, js = _palette_sidecars(os.path.join(args.out, "palette"), palette)
    if sheet_shape is not None:
        rows, cols = sheet_shape
        sheet = np.zeros((est.ny * rows, est.nx * cols, 4), dtype=np.uint8)
        for idx, img in enumerate(snapped):
            r, c = divmod(idx, cols)
            sheet[r * est.ny:(r + 1) * est.ny, c * est.nx:(c + 1) * est.nx] = img
        _save(sheet, os.path.join(args.out, "sheet.png"))
    print(f"frames: {len(frames)} ({w}x{h} each)")
    print(_grid_summary(est, w, h))
    print(f"palette: {len(palette)} colors (shared) -> {gpl} / {js}")
    print(f"wrote: {len(snapped)} frames to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="snapgrid",
        description="Recover the true pixel grid from damaged 'fake pixel "
                    "art' and snap it back to clean pixel art.",
    )
    p.add_argument("--version", action="version", version=f"snapgrid {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    f = sub.add_parser("fix", help="snap a single image")
    f.add_argument("image")
    f.add_argument("--out", required=True, help="output PNG path")
    f.add_argument("--max-colors", type=int, default=256,
                   help="palette size cap (default 256)")
    f.set_defaults(func=cmd_fix)

    s = sub.add_parser("sheet", help="snap a sprite sheet or folder of frames "
                                     "with ONE shared grid and palette")
    s.add_argument("image", help="sheet image, or folder of frame images")
    s.add_argument("--out", required=True, help="output folder")
    s.add_argument("--rows", type=int, default=None)
    s.add_argument("--cols", type=int, default=None)
    s.add_argument("--max-colors", type=int, default=256,
                   help="palette size cap (default 256)")
    s.set_defaults(func=cmd_sheet)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SnapError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except AbstainError as e:
        print(f"abstained: {e}", file=sys.stderr)
        return 3
    except BrokenPipeError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
