"""Gentle, single-corruption benchmark for snapgrid (reviewer-built).

Single-corruption protocol: ONE corruption at a time, moderate levels, on the
same native sprites the harsh suite uses (tests/spritefactory.make_sprite).
Classes:
  a_bilinear_only   non-integer bilinear upscale only
  b_bicubic_only    non-integer bicubic upscale only
  c_blur04          bilinear upscale + Gaussian blur 0.4 only
  d_jpeg85          bilinear upscale, saved JPEG quality 85, reloaded
  e_nearest_integer integer 3x or 4x nearest upscale (sanity, should be ~100%)
Sprites: indices 0..11 for a-d (12 each), 0..7 for e (8) = 56 cases total.
Counting: exact / abstain / wrong; pixel-exactness on exact-grid cases.
Run:  python benchmarks/gentle_suite.py
"""
from __future__ import annotations
import io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
import numpy as np
from PIL import Image, ImageFilter
from spritefactory import make_sprite
from snapgrid.core import estimate_grid, snap, AbstainError, SnapError
from snapgrid.palette import snap_to_palette

def upscale(img, factor, resample):
    w,h = img.size
    return img.resize((max(8,round(w*factor)), max(8,round(h*factor))), resample)

def make_case(cls, i):
    orig = make_sprite(i)
    # deterministic moderate factor per index
    factor = 3.1 + (i % 5)*0.47  # 3.1 .. 4.98 non-integer
    if cls=="a_bilinear_only":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR)
    elif cls=="b_bicubic_only":
        dmg = upscale(orig, factor, Image.Resampling.BICUBIC)
    elif cls=="c_blur04":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(0.4))
    elif cls=="d_jpeg85":
        dmg = upscale(orig, factor, Image.Resampling.BILINEAR).convert("RGB")
        buf=io.BytesIO(); dmg.save(buf, format="JPEG", quality=85); buf.seek(0)
        j=Image.open(buf); j.load(); dmg=j.convert("RGBA")
    elif cls=="e_nearest_integer":
        f = 3 if i%2==0 else 4
        dmg = orig.resize((orig.size[0]*f, orig.size[1]*f), Image.Resampling.NEAREST)
    else: raise ValueError(cls)
    # PNG round-trip for non-JPEG (as files arrive)
    if cls!="d_jpeg85":
        buf=io.BytesIO(); dmg.save(buf, format="PNG"); buf.seek(0)
        d2=Image.open(buf); d2.load(); dmg=d2
    return orig, dmg

CLASSES = [("a_bilinear_only",12),("b_bicubic_only",12),("c_blur04",12),("d_jpeg85",12),("e_nearest_integer",8)]

def run():
    grand={"exact":0,"abstain":0,"wrong":0,"n":0}
    for cls,n in CLASSES:
        ex=ab=wr=0; pix=[]
        for i in range(n):
            orig,dmg = make_case(cls,i)
            arr=np.asarray(dmg)
            try: est=estimate_grid([arr])
            except (AbstainError,SnapError): ab+=1; continue
            if (est.nx,est.ny)!=orig.size: wr+=1; continue
            ex+=1
            snapped=snap(arr,est); mapped,_=snap_to_palette([snapped],256)
            pix.append(float((mapped[0]==np.asarray(orig)).all(axis=2).mean()))
        print(f"{cls}: n={n} exact={ex} ({ex/n:.1%}) abstain={ab} wrong={wr} pix_exact_on_exact={np.mean(pix) if pix else 0:.3f}")
        grand["exact"]+=ex; grand["abstain"]+=ab; grand["wrong"]+=wr; grand["n"]+=n
    print(f"TOTAL: n={grand['n']} exact={grand['exact']} ({grand['exact']/grand['n']:.1%}) abstain={grand['abstain']} wrong={grand['wrong']}")
    return grand

if __name__=="__main__":
    run()
