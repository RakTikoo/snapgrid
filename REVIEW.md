# SnapGrid — Independent Adversarial Review

Reviewer did not build this tool. All numbers below were measured by the
reviewer in this session, from the code as shipped to review. Rival
comparisons live in an internal record that is not included in this
repository (per the project's standing rule); this file contains only
snapgrid's own numbers, methods, and limits.

## Verdict: DO NOT PUBLISH YET

The tool is honest and safe — it almost never emits a wrong grid — but
its recovery rate is too weak to publish under a "best open source"
claim:

- Harsh stacked-damage suite: **55.6%** exact grid (builder's claim
  reproduced exactly).
- Gentler single-corruption suite (below): **60.7%** exact grid overall,
  **58%** on plain bilinear/bicubic upscale with no other damage.
  A tool that misses the grid on 2 of 5 *undamaged-except-upscale*
  images is not ready. The failure mode is nearly always abstention
  (safe), but at this rate users would conclude it doesn't work.
- What must improve (specifics in "Root causes" below): candidate
  nomination must stop depending on the profile-fit gate (the true
  count is often eliminated before ranking), and grid selection needs
  a stronger arbiter than the current 1-D comb/prominence statistics
  (an edge-following / multi-detector consensus of the kind the
  internal benchmark shows is achievable on identical inputs).
  Target to re-review: ≥75–80% exact on the gentle suite with
  wrong-grid staying ≤ ~5%, and no regression on the harsh suite
  (exact ≥20/36, wrong ≤3/36) or the adversarial abstentions.

## What was checked

### 1. Test suite (reviewer-run)
`python run_tests.py` → **18/18 tests pass** (14.7 s). Builder's claim
confirmed.

### 2. Fresh-install proof
New venv (`python3 -m venv`), `pip install <local folder>` succeeded,
`snapgrid --version` → 0.1.0, CLI `fix` on the demo damaged sprite
(103×103) → **recovered grid 24×24**, confidence 0.48, 9-colour palette,
exit 0, sidecars written. Demo pixel-exactness (reviewer-measured):
52.3% of pixels exactly match the original sprite.

### 3. Harsh ground-truth suite — reviewer's own measurement
Reviewer wrote an independent counting script over
`tests/spritefactory.build_suite(36)` (same fixtures, own counting,
own pixel scoring). Factory audit first:

- Palette separation is real: sprites use ≤16 opaque colours; minimum
  pairwise L1 distance among colours present was ≥62 in sampled
  sprites (factory enforces ≥60 by construction).
- Majority smoothing operates on palette *indices* (`_mode_smooth`,
  ties keep the centre value) — no hybrid colours invented. Confirmed
  by reading the code and by the ≤16-colour check.
- Damage is as documented: non-integer upscale 2.3–5.7×, resample
  cycles bilinear/bicubic/nearest by index, blur 0.3–0.9 and noise
  σ1.5–4.5 on RGB only (smooth paths), PNG re-save. Not rigged.

Reviewer's results — **identical to the builder's claims**:

| | result |
|---|---|
| Exact grid | **20/36 = 55.6%** |
| — nearest damage | 6/7 = 85.7%, pixel-exactness on exact **99.5%** |
| — smooth damage | 14/29 = 48.3%, pixel-exactness on exact **4.0%** |
| Abstained | 13/36 = 36.1% |
| Wrong grid | 3/36 = 8.3% |
| Within ±8/channel on exact smooth cases | **47.7%** (matches claim) |
| Pixel-exactness overall (abstain/wrong = 0) | 18.1% (matches claim) |

### 4. Gentle single-corruption suite (reviewer-built, new)
`benchmarks/gentle_suite.py` (added by this review, re-runnable):
same native sprites, ONE corruption at a time at moderate levels —
the fair-comparison protocol the harsh suite is not.

| class | n | exact | abstain | wrong | pix-exact on exact |
|---|---|---|---|---|---|
| bilinear upscale only | 12 | 7 (58.3%) | 5 | 0 | 0.407 |
| bicubic upscale only | 12 | 7 (58.3%) | 5 | 0 | 0.360 |
| upscale + blur 0.4 only | 12 | 7 (58.3%) | 5 | 0 | 0.342 |
| upscale + JPEG q85 | 12 | 5 (41.7%) | 7 | 0 | 0.000 |
| integer 3×/4× nearest (sanity) | 8 | 8 (100%) | 0 | 0 | 1.000 |
| **total** | **56** | **34 (60.7%)** | **22** | **0** | — |

Wrong-grid is 0 on the gentle suite: every failure is an abstention.
JPEG is the weakest class. Pixel-exactness under JPEG damage is 0%
even on exact grids (colours shift; grid is right, palette values are
not) — consistent with the README's colour caveat.

### 5. Adversarial probes (reviewer-run, beyond the test suite)
- **Already-clean pixel art at native size** (24×16 and 32×32,
  undamaged): **abstains on both — no mangling, no fake "recovery"**.
  Safe behaviour. (One abstain message misleadingly says the image
  "looks like a photo"; the outcome is correct, the wording is not.)
- **Sprite sheet, 3 frames sharing a grid:** joint estimate exact
  (24×16), all outputs one size, ONE shared palette — verified.
- **Sheet with one fully transparent frame:** still exact. Blank
  frames do not poison joint estimation.
- **Frames with genuinely different grids in one folder:** does NOT
  fail cleanly — it silently accepts the dominant frame's grid
  (24×16, conf 0.44) and would force the other frame onto it.
  **Bug/limitation B1** (see below).
- **Irregular / non-uniform grid image:** abstains. Correct.
- **JPEG q70 single sprite:** abstains (safe, but weak — see gentle
  JPEG class).
- **Large sprite, 64×64 upscaled 3.7× + blur 0.5:** **abstains via
  the edge-density gate** ("almost no hard edges") — a false
  rejection of an in-scope image (cells ≈3.7 px). **Bug B2.**
- **Malformed inputs:** tiny (4×4), fully transparent, empty frame
  list, mismatched frame sizes → all clean one-line `SnapError`s, no
  tracebacks. Flat single-colour image → abstains. Text/corrupt
  files → exit 2 with clean error (also covered by the test suite).

## Bugs / design issues found

- **B1 — Sheet mode validates only frame 0.** The forward-model error
  and confidence are computed from the first frame only
  (`pmj = pmj_frames[0]` in `estimate_grid`). Frames that disagree
  with the shared grid are never checked, so a mixed-grid folder is
  silently forced onto one grid instead of abstaining or warning.
  Fix direction: score every frame (or a sample) under the shared
  grid and abstain/warn when a frame's error is far above the rest.
- **B2 — Edge-density gate rejects large smooth-upscaled sprites.**
  The `mean gradient / profile std < 0.015` gate fires on a 64×64
  sprite at 3.7× upscale, which is squarely in scope. The gate is
  load-bearing for photo/gradient abstention, so it needs a
  scale-aware form, not removal.
- **B3 (minor) — Misleading abstain wording** when clean native pixel
  art trips the resynthesis veto: the message claims the image looks
  like a photo. Outcome is safe; message should say the image may
  already be clean pixel art.

No bugs were fixed by the reviewer: the attempted algorithmic
improvements (below) did not hold up, and the reviewer does not ship
unproven changes. B1–B3 are documented for the builder.

## Root causes of the recovery gap (diagnosed, technique level)

Measured on the gentle-suite failures:

1. **The true count is often eliminated before ranking.** `scan()`
   gates candidate counts by an unpenalised 1-D profile fit
   (`fit ≤ best×1.6 + 1.2`); finer spurious counts fit a profile
   better almost by definition. In probed failures the true count
   was absent from the candidate pool (e.g. bilinear case 2: pool
   {37, 30, 47, 32, 40, 36}, truth 24; case 6: no 8; bicubic case 2:
   no 24 or 16).
2. **The 1-D statistics are weak where it matters.** For many true
   grids, comb ≈ 1.1–1.3 and FFT prominence ≈ 0–0.3 (small sprites,
   blobby content), indistinguishable from wrong candidates; several
   correct winners abstain at confidence 0.26–0.33 vs the 0.38
   threshold, while several wrong candidates sit in the same band —
   so the threshold cannot simply be lowered.
3. **Forward-model error alone cannot arbitrate at the needed
   resolution.** Exact resynthesis errors of the true grid vs ±1,
   doubled, or slightly-off grids routinely differ by <5% (e.g.
   JPEG case: true 2.37 vs wrong 18×16 at 2.30), and the offset/
   bounds variant chosen for a count can matter more than the count.

## Improvements attempted by the reviewer — all rejected on evidence

Developed in a scratch copy, never applied to the repo; each was
measured on the gentle suite (dev) with the harsh suite as the
regression check:

1. **Confidence re-weighting** (forward error 0.30 → 0.60 weight):
   gentle exact 34 → 41, but wrong 0 → **9**. Rejected: it converts
   abstentions into wrong grids, violating the tool's core promise.
2. **Fast forward-model pool search + parsimony** (integral-image
   cell means, smallest grid within 8% + 0.4 of best error):
   wrong 0 → **14**. Rejected: coarser grids resynthesise "almost as
   well" at mean level and steal real images.
3. **Autocorrelation candidate nomination + exact pair search +
   tight parsimony (3% + 0.15):** gentle exact 33/56, wrong 2.
   No gain. Rejected.

Conclusion: closing this gap needs a genuinely stronger detector
family (edge-following cut placement and/or multi-detector consensus
with a distillability arbiter), built and measured as a development
effort — not a review patch, and not threshold tuning (which the
measurements above show trades exactness for wrong grids).

## Remaining limits (in addition to the README's)

- Gentle-suite recovery is 60.7%, JPEG 41.7% — see verdict.
- Mixed-grid folders are silently forced onto one grid (B1).
- Large sprites (≥64×64 cells) can be falsely abstained (B2).
- Clean native pixel art abstains rather than passing through as an
  identity result; safe, but users should know "abstained" can mean
  "already clean".

## Files changed by this review
- Added `benchmarks/gentle_suite.py` (the gentle benchmark, runnable).
- Added this `REVIEW.md`. Rival data: internal record, not in this repo.
- No algorithm changes; README unchanged (its published harsh-suite
  numbers were reproduced exactly and remain accurate).
- Full test suite re-run after all review work: **18/18 pass**.

---

## Follow-up addendum (builder, 2026-11-17 — reviewer's text above unchanged)

The DO NOT PUBLISH verdict was addressed with a technique-level
redesign, developed on a DEV split (gentle indices 0–5, harsh 0–17)
and frozen before held-out measurement:

- **Nomination:** ungated square-pitch sweep — every (nx, ny) whose
  pitches agree is scored; the profile-fit gate is gone. Non-square
  candidate pairs are NOT admitted: measured on dev, coarse
  non-square grids beat the true grid on sparse shape sprites by
  faking phase evidence (shifting a coarse grid displaces features).
- **Arbiter:** description length (Laplace residual bits of the
  forward model over ALL pixels + per-cell colour cost) MINUS phase
  evidence (cost increase when all cuts shift by half a cell), with
  an exact-median re-score + palette-group term for finalists.
  Rejected during development, on dev evidence: crisp-only ranking
  (true grid often outside top-12), a single even-width box-blur
  forward model (its half-pixel shift invented phase evidence and
  flipped rankings to finer grids), and a fully-exact stage-2
  re-score (flipped correct sweep winners to coarser grids).
- **Verification:** per-axis phase check (winner must show phase
  sensitivity on EACH axis; measured fake-axis gain 0.07 vs ≥0.24 for
  true axes) — this abstains on non-uniformly resized images.
  Near-tie margins abstain (threshold 0.03 bits/entry, set inside
  the dev gap 0.0115–0.0415).
- **B1 fixed:** per-frame agreement in sheet mode — both a forward-
  error outlier check and a per-frame own-best-grid check (a coarser
  frame fits a finer shared grid deceptively well, so error alone
  was not enough). Mixed-grid folders now abstain with an
  explanation. Regression test added.
- **B2 fixed:** edge-density gate is scale-aware
  (threshold ∝ sqrt(40/axis length)) and no longer vetoes outright
  when detector evidence is strong; low-error winners use a relative
  phase test (error ≥1.8× under shift). The reviewer's case (64×64
  at 3.7× + blur 0.5) now recovers 64×64 exactly. Regression test.
- **B3 fixed:** the resynthesis-veto message now says the image "may
  already be clean pixel art at its true size". Regression test.

Re-measured results (frozen detector):

| suite | before (reviewer) | after |
|---|---|---|
| gentle (56) | 34 exact / 22 abst / 0 wrong | **54 / 1 / 1 (96.4% exact, 1.8% wrong)** |
| harsh (36) | 20 / 13 / 3 | **34 / 1 / 1 (94.4% exact, 2.8% wrong)** |
| held-out (92, new) | — | **83 / 6 / 3 (90.2% exact)** |
| dev gentle (30) | — | 29 / 1 abst / 0 wrong |
| dev harsh (18) | — | 18 / 0 / 0 |

Pixel-exactness on exact grids did not regress: harsh nearest
99.5% → 100%, smooth 4.0% → 11.7%, within-±8 47.7% → 67.6%;
gentle per-class all improved or equal. Adversarial abstentions
intact (gradient, photo-noise, clean native art, non-uniform
resize, mixed-grid folder). Test suite: 22/22 (18 original — three
rate-encoding tests updated to the honestly re-measured numbers,
with the changes documented in the test comments — plus 4 new
regression tests). Runtime cost: ~0.1 s (small) to ~4 s (180×180)
per image.

Remaining known failures (documented in README): one harsh aliasing
case (8×8 nearest blob also explained exactly by a 20×20 grid);
held-out sparse 4-colour shape sprite (seed 2005) read as a coarser
grid under two damage types; one gentle JPEG case (index 10) read
as a wrong grid with a clear margin. Ready for re-review.

---

## Re-review of v2 (independent reviewer, 2026-11-17 — original review and builder's addendum above unchanged)

Reviewer did not build v2 and did not trust any claimed number:
everything below was re-measured in this session, including a
fresh-seed suite (sprite indices 5000+) whose seeds the builder never
used on any split.

### Verdict: DO NOT PUBLISH YET — one systematic defect

Every recovery claim reproduces exactly, and the fresh-seed results
rule out fixture overfitting. But one documented safety behaviour
does **not** hold on fresh content: non-uniformly resized sprites
(stretched by different factors in x and y) frequently receive a
confident **wrong grid** instead of the documented abstention
(8 of 13 fresh trials — see "Blocking defect" below). Emitting a
wrong grid is the one failure this tool promises never to have, and
the README states the opposite behaviour ("detected by the per-axis
phase check and abstained on, not recovered"). That claim is
contradicted by measurement. Everything else passes, most of it
with a wide margin, so this is a narrow hold: fix the non-uniform
verification, re-run the batteries, and this is publishable.

### Claims re-measured (reviewer's own runs and counting)

| claim | builder | reviewer | |
|---|---|---|---|
| test suite | 22/22 | **22/22** (54.2 s, reviewer-run) | ✓ |
| gentle suite (56) | 54 exact / 1 abst / 1 wrong | **54 / 1 / 1 (96.4%)**, per-class identical, incl. JPEG 10/1/1 and pix-exact 0.500/0.432/0.412/0.000/1.000 | ✓ |
| harsh suite (36) | 34 / 1 / 1 | **34 / 1 / 1 (94.4%)** — wrong is idx 24 (documented 8×8→20×20 aliasing), abstain is idx 25 (28×20), exactly as documented | ✓ |
| harsh pixel numbers | nearest 100%, smooth 11.7%, overall 25.8%, within-±8 67.6% | nearest 6/7 at **1.000**, smooth **0.117**, overall **0.258**; reviewer's smooth-only within-±8 is 60.6%, which reconciles exactly with 67.6% over all exact cases ((28×0.606 + 6×1.0)/34) | ✓ |
| held-out suite (92) | 83 / 6 / 3 (90.2%) | **83 / 6 / 3 (90.2%)**, per-class identical | ✓ |
| fresh install | — | new venv, `pip install` of the local folder OK, `snapgrid --version` 0.1.0; CLI on a damaged sprite → 24×16 exact, exit 0, palette sidecars written; CLI on a gradient → abstain, exit 3, nothing written | ✓ |

**README numbers: every published number was checked against the
reviewer's measurements and none is contradicted.** No README
correction was needed or made. (One README *claim* — not a number —
is contradicted; see the blocking defect.)

### Reviewer's own fresh-seed suite (indices 5000+, 60 cases)

Sprites from `make_sprite(5000 + j)` — a seed range absent from the
builder's dev split (gentle 0–5, harsh 0–17) and held-out split
(sprites 2000+/3000+, harsh 18–35). Gentle single-corruption
protocol plus stacked harsh damage via `corrupt` at fresh indices.

| class | n | exact | abstain | wrong | pix-exact on exact |
|---|---|---|---|---|---|
| bilinear only | 10 | 9 (90%) | 1 | 0 | 0.470 |
| bicubic only | 10 | 10 (100%) | 0 | 0 | 0.491 |
| upscale + blur 0.4 | 10 | 10 (100%) | 0 | 0 | 0.485 |
| upscale + JPEG q85 | 10 | 9 (90%) | 1 | 0 | 0.100 |
| integer nearest 3×/4× | 8 | 8 (100%) | 0 | 0 | 1.000 |
| harsh stacked (fresh) | 12 | 12 (100%) | 0 | 0 | 0.248 |
| **total** | **60** | **58 (96.7%)** | **2** | **0 (0.0%)** | — |

No collapse versus the builder's held-out 90.2% — the fresh-seed
rate is *higher*, and the wrong rate is 0%. The dev-calibrated
thresholds (e.g. the 0.03 bits/entry near-tie margin, documented in
`core.py` as set inside a dev gap) generalise to unseen seeds.
**No evidence of fixture overfitting.**

### Adversarial probes (reviewer-run, fresh content where applicable)

- **Smooth gradient:** abstains. ✓
- **Photo-noise:** abstains. ✓
- **Clean native pixel art** (fresh sprites 20×20, 28×20, 24×16,
  32×32): abstains on all — never mangled, never given a wrong
  grid. ✓ (Minor: a clean sprite whose *whole image* is below the
  scan's minimum size, e.g. native 8×8 or 12×12, gets a clean
  `SnapError` "image too small" — exit 2 rather than abstain
  exit 3. Safe, nothing written; wording/exit-code quirk only.)
- **Irregular grid** (varying block widths/heights, blurred):
  abstains. ✓
- **Mixed-grid sheet (B1):** abstains with the frames-disagree
  explanation naming the conflicting grid — verified on fresh
  sprites (18×14 vs 28×20), not just the regression fixture. ✓
- **Blank frame in a sheet:** joint estimate still exact; shared-
  grid sheet (3 frames) exact. ✓
- **B2, 64×64 at 3.7× + blur 0.5:** the builder's regression case
  recovers exactly, as do 4 of 5 additional fresh 64×64 sprites
  (shape/blob/character/detail generators). Two shape sprites with
  other seeds still **abstain** via the edge-density gate. So B2 is
  substantially but not fully fixed — and the residual failure is a
  safe abstention, not a wrong answer. Documented here as a
  remaining limit, not a blocker.
- **Non-uniform resize:** **FAILS — see below.**

### Blocking defect: non-uniform resize emits wrong grids

The builder's regression test (one sprite, 3× in x / 5× in y)
abstains and passes. On fresh content the per-axis phase check does
not generalise. Reviewer's 13 trials (fresh sprites, both stretch
directions, bilinear/bicubic/nearest, with and without blur):

- **Emitted a wrong grid in 8/13** (confidence 0.50–0.98):
  24×16 @3/5 → emitted 24×28; 32×32 @3/5 → 32×56; 24×16 @2.6/4.2
  → 24×26; 8×8 @3/5 → 8×14; 20×20 @3/5 → 20×35; 18×14 @3/5 →
  18×22; 24×16 @5/3 nearest → 39×16; 8×8 @3.8/2.4 bicubic → 8×6.
- Abstained in 5/13 (incl. the builder's own fixture configuration
  on other sprites, and 2×/4× and 5×/3× bilinear cases).

Pattern: the emitted grid keeps the less-stretched axis's true
count and inflates the other axis to a count whose pitch nearly
matches (≈5% off, e.g. pitch 2.857 fitted against a true 3.0),
whose cut positions coincide with true cell boundaries at regular
intervals — enough to fake per-axis phase evidence. The square-
pitch agreement tolerance plus the phase check, as built, cannot
distinguish this on most content.

Why this blocks: the README's Known limits and adversarial-checks
paragraph promise abstention on exactly this input class, the
builder's addendum claims "adversarial abstentions intact
(... non-uniform resize ...)", and the tool's core contract is
that a wrong grid is worse than no answer. A user who non-uniformly
stretches a sprite (a common accident) currently gets a confident,
wrong "recovery" with no warning in the majority of fresh cases
tried.

**What must improve before re-review:** the winner verification
must catch per-axis pitch disagreement — e.g. compare the winning
grid's pitch against each axis's independently estimated pitch /
phase evidence at the *axis's own* best count, and abstain when an
axis only fits at a pitch forced to match the other axis. Re-review
bar: abstention on a fresh non-uniform set (both directions,
multiple ratios and resample modes — the reviewer's 13-case set
above is a starting point, new seeds for the re-check), with no
regression on gentle (≥75–80% exact, ≤5% wrong), harsh (≥20/36
exact, ≤3 wrong), the fresh-seed suite, or the other adversarial
abstentions. The B2 residual abstentions (large smooth sprites)
should also be added to the README's Known limits at the same time.

### Score against the publish bars

| bar | result |
|---|---|
| gentle exact ≥75–80% | **96.4%** ✓ |
| gentle wrong ≤5% | **1.8%** ✓ |
| harsh ≥20/36 exact, ≤3 wrong | **34 exact, 1 wrong** ✓ |
| fresh-seed held-out consistent, no collapse | **96.7% exact, 0% wrong** ✓ |
| adversarial abstentions intact | gradient/photo/clean/irregular/mixed-sheet ✓ — **non-uniform ✗ (8/13 wrong grids)** |

One bar failed, on the tool's most important promise. **Verdict:
DO NOT PUBLISH YET**, narrowly — the recovery engine is proven;
the non-uniform verification is not.

---

## Builder response to the re-review (2026-11-17 — reviewer's sections above unchanged)

The blocking defect is addressed at the verification stage only;
the nomination/arbiter scoring that produced the verified recovery
numbers is untouched.

**Baseline, measured before any change** on a fresh non-uniform set
built for this fix (sprites `make_sprite(9000 + j)`, both stretch
directions, bilinear/bicubic/nearest, with and without blur 0.4,
24 cases): **13 of 24 emitted a wrong grid** (confidences up to
1.00), 11 abstained — consistent with the reviewer's 8/13. A
parallel dev set (indices 8000+) showed 14 emissions, all wrong.

**Fix — winner verification, three per-axis refutations** (any one
abstains, with a message saying the image appears stretched
differently in each direction):

1. **Profile breakpoint lattice.** The axis profile's breakpoints
   (first+second-difference spikes) must concentrate on a lattice
   whose period IS the winner's pitch (Rayleigh phase
   concentration). A forced axis's breakpoints lie on its own true
   pitch's lattice instead.
2. **Pooled per-line breakpoint lattice.** The same test with
   breakpoint evidence pooled per line (sums of |second
   differences| over all lines/channels — the averaged profile
   washes out breakpoints whose sign varies per line), selecting
   the FUNDAMENTAL period (largest period reaching 95% of the best
   concentration; lattices also concentrate at submultiples, which
   is how a 5.0-pitch axis first appeared to support a 2.5 lattice).
3. **Coarser same-axis challenger.** Freeing one axis to a coarser
   count (other axis fixed at the winner) must not beat the
   winner's full arbiter score (fast MDL-minus-phase + palette) by
   more than 0.60 bits/entry — the signature of an inflated count
   (e.g. the ratio-2 nearest refinements, where the breakpoint
   lattice is refinement-blind because the true lattice is a
   sub-lattice of the winner's).

Thresholds were calibrated on dev evidence only (the 8000+ dev set
plus the genuine suites): over 342 genuine emitted axes the kink
preconditions' best concentration reached 0.87 (profile) / 0.83
(pooled) and the largest coarser-challenger margin was 0.52
bits/entry; dev fake axes caught scored ≥ 0.89 concentration /
≥ 0.68 bits/entry. Thresholds (0.89 / 0.85 / 0.60) sit inside those
gaps. Several candidate statistics were measured and rejected on
the same dev data: pure-MDL per-axis sweeps, comb/FFT/autocorrelation
pitch ratios on the gradient signal (all fire on genuine axes or
miss fakes), and a finer-challenger margin (genuine axes reach
0.84 bits/entry there).

**Results after the fix** (all re-measured with the shipped code):

| measurement | before | after |
|---|---|---|
| fresh non-uniform (9000+, 24) | 13 wrong / 11 abstain | **2 wrong / 22 abstain** |
| dev non-uniform (8000+, 24) | 14 wrong / 10 abstain | **1 wrong / 23 abstain** |
| gentle suite (56) | 54 / 1 / 1 | **54 / 1 / 1 (unchanged)** |
| harsh suite (36) | 34 / 1 / 1 | **34 exact / 2 abstain / 0 wrong** |
| held-out (92) | 83 / 6 / 3 | **83 exact / 7 abstain / 2 wrong** |
| test suite | 22/22 | **24/24** (2 non-uniform regression tests added, sprites 7000+) |

The harsh/held-out deltas are the documented aliasing case (harsh
index 24) moving from wrong to abstain under the new verification;
exact counts are unchanged everywhere, and pixel-exactness on exact
grids is unchanged (gentle per-class identical). Adversarial
abstentions re-verified: gradient, photo-noise, clean native art,
irregular grid, mixed-grid sheet (B1), blank-frame sheet, and the
B2/B3 regression cases all behave as before.

**Remaining failures, honestly stated.** Three non-uniform cases
still emit a wrong grid, all in one family — the forced axis is
~20 px long and offers too few breakpoints to refute the fit:

- acceptance cases 6 and 15: 8×8 sprites (bilinear, factors
  3.8×/2.4× and 4.2×/2.6×); the forced axis yields 3 and 5 usable
  breakpoints, a regime where genuine axes produce identical
  statistics (a genuine 5-breakpoint axis reaches concentration
  1.0 at a disagreeing period), so no threshold separates them;
- dev case 4: 16×24 sprite at 2.0×/4.0 bicubic + blur, winner
  halved the x count; all three checks are weak at a smeared
  2.0 px pitch.

These are now documented in the README's Known limits, and the
non-uniform set is a runnable benchmark
(`benchmarks/nonuniform_suite.py`). The README's non-uniform claim
is worded to match the measurement (abstention, with the small-
sprite exception stated), and the B2 residual limit the re-review
asked for (large sparse-edge sprites may still abstain via the
edge gate) is documented alongside it.

---

## Builder note — small-image guard (2026-11-17, Rakshit's decision; all sections above unchanged)

Rakshit chose the conservative option for the remaining family:
add the guard now, publish with the limit documented, and leave a
deeper fix for later. The two acceptance-set misses (cases 6 and
15) are now refused by design.

**Scope, measured before implementing.** A diagnostic run over
all four suites (208 cases) recorded, for every emitted winner,
each axis's breakpoint counts and lattice concentrations. The
complete emitted population with damaged short side ≤ 24 px was:
the two residual wrong grids (short sides 19 and 21 px; forced
axes with 3 and 5 usable breakpoints, below the refutations'
KINK_PEAKS_MIN of 6, so no refutation could fire) and two genuine
nearest-integer recoveries at exactly 24×24 whose lattice support
is perfect (concentration 1.0, fundamental period equal to the
winner pitch). The next genuine recoveries sit at 27–29 px. A
wider support requirement is not viable: across all sizes, 62
genuine exact-case axes fail every positive lattice-support
predicate — the forward model, not the lattice, carries those
axes — so positive confirmation is required only inside the
small scope, where the evidence says it separates cleanly.

**Guard.** In `estimate_grid`, as the last gate before emission
(so every existing abstention keeps its current message, and
behaviour outside the scope is unchanged): when the damaged
image's short side is ≤ `GUARD_MAX_SHORT_AXIS` (24 px), the
winner is emitted only if BOTH axes are positively confirmed by
`_axis_pitch_supported` — in either breakpoint pool (profile or
pooled per-line), enough breakpoints concentrating on the
winner's pitch (> KINK_WINNER_MAX with ≥ KINK_PEAKS_MIN peaks),
or the lattice's fundamental period agreeing with the winner's
pitch (within QKINK_DISAGREE_MIN at ≥ QKINK_FUND_MIN
concentration, from ≥ GUARD_PEAKS_MIN = 3 peaks). Otherwise it
abstains: "the image is very small and appears stretched
differently in each direction, so it cannot be recovered
safely". All thresholds are the verification stage's existing
calibrated constants; the only new constants are the scope
(24 px) and the 3-peak floor for the fundamental test. The
pooled-peaks and fundamental-period computations were extracted
into shared helpers (`_qpool_peaks`, `_fundamental_period`);
`_qpool_kink_rejects` uses them with an identical recipe.

**Results after the guard** (all re-measured with the shipped
code):

| measurement | before guard | after guard |
|---|---|---|
| non-uniform acceptance (24) | 22 abstain / 2 wrong | **24 abstain / 0 wrong** |
| gentle suite (56) | 54 / 1 / 1 | **54 / 1 / 1 (unchanged)** |
| harsh suite (36) | 34 exact / 2 abstain / 0 wrong | **34 / 2 / 0 (unchanged)** |
| held-out (92) | 83 / 7 / 2 | **83 / 7 / 2 (unchanged)** |
| test suite | 24/24 | **27/27** (3 guard regression tests added) |

Cost: **zero genuine exact cases flipped on any suite.** The two
in-scope genuine recoveries (gentle and held-out nearest-integer
cases at 24×24, sprites 6 and 2004) still recover exactly and
are pinned by a regression test, as are the two acceptance cases
and the guard's plain message. Adversarial abstentions
(gradient, photo-noise, clean art, irregular grid, mixed-grid
sheet, B1/B2/B3 regressions) all still pass inside the suite.

**Remaining residual, honestly stated.** The guard's scope is
the measured acceptance regime. The dev-set case documented in
the builder response above (16×24 at 2.0×/4.0 bicubic + blur,
damaged 32×96 — short side 32 px, outside the scope) was
reconstructed and re-measured after the guard: it still emits a
wrong 8×24 grid (confidence 0.45). It is not covered by the
guard and remains, with the general problem of positively
verifying tiny/thin-evidence sprites, the deeper fix round's
job. The README's Known limits now state the guard's rule and
its zero measured cost, and name that deeper fix as future
work.

---

## Builder note — deep-fix round (2026-11-17, Rakshit's option 3; all sections above unchanged)

The guard note above left two jobs: the dev-set case outside the
guard's scope (16×24 at 2.0×/4.0 bicubic + blur, damaged 32×96,
emitting a wrong 8×24 at confidence 0.45), and the general problem
of the guarded tiny regime. This round addressed both, then kept
going: validation on fresh seed sets found two more wrong grids of
the same non-uniform family, and those were fixed too. All changes
are in the verification stage and the guard; nomination and the
arbiter are untouched, so every recovery number below moved only
where a wrong emission became an abstention.

**Fix 1 — finer-harmonic challenger (the dev case).** The winner
had MERGED pairs of true x cells: its pitch (4.0) is exactly twice
the axis's own (2.0). At a smeared 2.0 px pitch the breakpoint
lattices wash out (profile fundamental 4.45 at concentration 0.71;
pooled 2.55 at 0.71 — no clean lattice anywhere), and the coarser
challenger looks the wrong way. The signature that survives is in
the forward model: doubling the axis's count (other axis fixed)
cuts the exact error to 0.732× the winner's AND the doubled grid
is itself phase-sensitive on that axis (gain 2.38). A mere
refinement of a genuine axis does neither. Refutation rule (exact
median-sampled errors under the winner's damage model, winner
error ≥ 2.0, fast-ratio pre-filter 1.05): error ratio ≤ 0.85 AND
doubled-grid phase gain ≥ 1.0. Calibration over the 342 genuine
emitted axes of the three suites (exact stats for every axis
within reach of the threshold — 71 axes; the rest sit behind the
pre-filter with ≥ 0.5 slack against the largest fast-vs-exact
ratio discrepancy observed anywhere, 0.24): genuine minimum ratio
0.963, genuine maximum gain 0.08. Thresholds sit inside the gaps.
Rejected on the same data, again: free-axis MDL margins (genuine
axes reach 0.86 bits/entry — the winner is not the per-axis MDL
optimum on genuine images either) and the doubled-count FAST-score
margin (one genuine axis, a 12-cell character sprite at pitch
5.08, reaches +0.055 against the fake's +0.057 — no gap; its exact
ratio is 1.088 and its doubled gain 0.08, which is why the rule
uses exact errors).

**Fix 2 — cross-pool lattice agreement (fresh-seed find).**
Validation of Fix 1 on an untouched seed range (sprites 20000+,
same non-uniform protocol) found 2 wrong in 24 — both pre-existing
under v0.1.0 (verified against a reconstructed v0.1.0 tree; the
set's other 22 cases behaved identically under both versions).
Case 6 (18×14 at 3.8×/2.4 bilinear + blur, emitted 18×10): each
single-pool test just missed — 5 profile breakpoints (floor 6),
profile winner-concentration 0.59 (ceiling 0.50), pooled
fundamental 0.81 (floor 0.85) — yet BOTH pools' fundamental
periods were the true pitch 2.45 against the winner's 3.40.
New refutation: both pools offer ≥ 5 breakpoints, their
fundamentals agree within 12%, the agreed period disagrees with
the winner's pitch by > 20%, fundamentals concentrate ≥ 0.90
(profile) / ≥ 0.75 (pooled), and the pooled breakpoints do not
themselves support the winner (concentration ≤ 0.50). Zero fires
over 442 genuine emitted axes across a broad threshold plateau
(at a peaks floor of 4 one genuine axis fires; at 6 the target is
lost). Nearest genuine approach: one JPEG axis whose pools
disagree with each other and sit only 0.13 from the winner —
saved by the 0.20 disagreement condition, slack 0.07.

**Fix 3 — guard confirmation tolerance (the second fresh find).**
Case 13 (8×8 at 5.0×/3.0 bicubic, damaged 40×24, emitted 14×8 at
confidence 0.83) passed the guard because 3–4 pooled content edges
concentrated at a period 17% / 13% off the winner's pitch, inside
the 20% tolerance the guard had borrowed from the refutation.
Confirmation must be stricter than refutation: the guard's
fundamental-agreement tolerance is now 8% (its own constant; the
refutations keep 20%). Measured in-scope genuine agreements are
≤ 0.032; the fake's were 0.130 / 0.166. Raising the peak floor
instead was measured and rejected: a genuine 24×24 recovery's
y axis confirms through exactly 3 peaks.

**The tiny regime — recovery attempted, measured impossible.**
The guarded cases were tested for recoverability with an
unrestricted (non-square) search over all count pairs, scored by
the same forward model. The evidence contradicts itself across
cases: for acceptance case 15 the true pair ranks 1st of 170 and
its exact error is 0.69× the winner's — identifiable; for case 6
the true pair ranks 38th of 135 — not identifiable; for fresh
case 13 the wrong grid's exact error (1.97) BEATS the true grid's
(3.08) — the image genuinely contains more evidence for the wrong
answer. No verification can separate "identifiable" from
"misleading" at 3–5 breakpoints per axis, so the guard's refusal
stands as the correct behaviour for the regime, now with the
stricter confirmation of Fix 3. This closes the guard note's
"deeper fix" item as a measured negative result, not an omission.

**Results after all three fixes** (all re-measured with the
shipped code; before = v0.1.0 as published):

| measurement | before | after |
|---|---|---|
| test suite | 27/27 | **30/30** (3 regression tests added) |
| gentle suite (56) | 54 / 1 / 1 | **54 / 1 / 1 (unchanged, per-class identical)** |
| harsh suite (36) | 34 / 2 / 0 | **34 / 2 / 0 (unchanged)** |
| held-out (92) | 83 / 7 / 2 | **83 / 8 / 1** (the sparse-sprite blur case now abstains) |
| non-uniform acceptance (9000+, 24) | 24 abstain / 0 wrong | **24 abstain / 0 wrong** |
| non-uniform dev (8000+, 24) | 23 abstain / 1 wrong | **24 abstain / 0 wrong** |
| non-uniform fresh (20000+, 24) | 22 abstain / 2 wrong | **24 abstain / 0 wrong** |
| gentle-style fresh (21000+, 56) | 50 / 5 / 1 | **50 / 5 / 1 (unchanged)** |
| non-uniform validation (30000+, 24) | — | **24 abstain / 0 wrong** |
| gentle-style validation (31000+, 56) | — | 48 / 6 / 2 (see residuals) |

Method note: the 20000+/21000+ sets were untouched validation for
Fix 1, then served as dev evidence for Fixes 2–3; the
30000+/31000+ sets were generated after all thresholds froze and
are the clean validation for the round as a whole. Runtime is
unchanged in character (the new checks run only on would-be
emissions; the expensive one is fast-ratio pre-filtered).

**Remaining residuals, honestly stated.** (i) The gentle JPEG
case (index 10) still emits a wrong grid, as before — an inflated
count on a sparse sprite at pitch ~2.3, a family no measured
statistic separates (its free-axis margins are negative; the
truth scores worse than the winner under the arbiter). (ii) The
held-out sparse shape sprite (seed 2005) still emits under bicubic
damage; its blur-damage twin now abstains. (iii) The 31000+
validation set contains two wrong grids from the same known
families — a JPEG case and a 4-colour shape sprite under integer
nearest upscale read as a coarser grid (12×12 → 9×9); the latter
was verified to emit identically under v0.1.0, so it is a
pre-existing aliasing limit, not a regression of this round.
(iv) The cross-pool rule's nearest genuine approach has slack
0.07 on one condition (see Fix 2) — recorded here so a future
reviewer can watch it.

**Builder's verdict:** the round's targets are met — every
measured non-uniform wrong grid is now an abstention, with zero
genuine recoveries lost on any suite (gentle and harsh counts are
identical to v0.1.0; held-out wrong fell 2 → 1 at equal exact).
Ready for independent re-review as a v0.1.1 candidate.
