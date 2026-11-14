# Frame alignment and differential comparison (PR 3)

**Status: implemented and tested offline; the real campaign exposed a
correspondence bug that is now a regression.** Pixel verdicts are only issued
for pairs whose correspondence is corroborated by rendering evidence.

## Alignment (`automation/gfx_align.py`)

A pair is aligned using, in combination:

1. phase/scene identity when the observation provides one;
2. the unique caller-composition signature (the same FNV algorithm in both
   native observers);
3. draw count;
4. the logical/physical extent relation;
5. a bounded ordinal prior from the post-readiness anchor.

Outputs are `aligned`, `ambiguous`, `not_aligned` or `missing`.  A monotonic
sequence alignment tolerates gaps and preserves unmatched indices.  A weak
match (e.g. same draw count but different caller composition) stays
`ambiguous`; an image difference is never reported as a rendering failure for
an ambiguous pair.

### The loading-phase false positive (found in a real run)

Run `20261114T134854Z-a56d76bf` paired loading frames that had:

- the same caller signature and six draws on both sides;
- a 2x extent relation;
- 0.983 coarse content correlation (same background art).

They were nevertheless **different loading stages** (`Initializing
Maplogic...` with the bar at one position versus `Loading Databases...` with a
different tip and bar position).  The coarse content check could not veto it
because both frames share the same background painting.  The comparator
reported a 13.9% difference, which was a false rendering verdict.

Mitigations implemented:

- `settle.min_draws` gates comparison until both sides reach the scene's draw
  threshold (loading frames are explicitly excluded from the comparison, not
  silently trusted).
- The case is retained as a regression: an ambiguous/load-progress pair must
  produce `not_compared`, not `fail`.
- The remaining gap is explicit: a per-frame **update-state fingerprint** is
  still needed for a general solution.  The current native observation carries
  caller composition and draw count, which are not unique to a loading stage.

## Comparison (`automation/gfx_frame_compare.py`)

Normalization is explicit and recorded per side:

- BGRA8/RGBA8 decode;
- declared row origin (`lower_left`/`top_left`);
- integer HiDPI scale (2/3/4) by area-average downscale, only when the policy
  allows it.

Metrics recorded for every pair, pass or fail:

- differing pixels/fraction, maximum channel error, mean absolute error;
- per-channel max/mean/mismatched counts, alpha differences;
- difference bounding box and worst region;
- per-region statistics for map, UI/text and arbitrary configured regions;
- structural (edge) correlation per region;
- horizontal-mirror test per region: candidate vs the horizontally flipped
  reference; a strongly mirrored region is a failure even when its area is
  small.

Policies:

- `exact`: bit-identical required; a scale-normalized candidate is
  `unverified`, not a bit-identical pass.
- `tolerant`: thresholds derived from measured reference stability and known
  GPU numeric differences; scale normalization is recorded.
- `diagnostic`: report all metrics and `would_fail`, never fail the run.

## Comparison evidence

| Case | Result |
| --- | --- |
| Identical frames | `exact` pass |
| One-pixel change | `exact` fail with bbox |
| Small numeric change under declared tolerance | `tolerant` pass |
| Mirrored UI text | fail with `mirrored_regions` (area-independent) |
| 2x HiDPI candidate | normalized, scale and method recorded |
| Exact policy + 2x candidate | `unverified`, not pass |
| Alpha-only change | visible and counted |
| Different scene/update frame | `ambiguous`/`not_aligned`, never a pixel failure |
| Below settle draw threshold | `not_compared` (loading-phase regression) |
| Genuine API failure | health gate stops before visual comparison |

Tests: `tests/test_gfx_frame_compare.py`, `tests/test_gfx_dual_run.py`.

## Consistency experiments (PR 3.1)

The scenario now supports `experiment: consistency`, which runs the same
backend on both sides (OpenGL/OpenGL or Metal/Metal) under the same lockstep
and comparison policy.  These experiments establish whether startup,
animation, loading progress or game-state updates make a scene
non-deterministic before any GL-vs-Metal difference is attributed to the
renderer.  A run that fails consistency is reported as reference instability,
not as a rendering defect.

The real GL/GL and Metal/Metal campaigns remain to be executed; the synthetic
consistency cases in `test_same_composition_is_a_consistency_case` pin the
alignment/comparison contract until then.

## First-difference preservation

On a confirmed difference the coordinator stops, preserves the aligned pair's
raw frames in the content-addressed store (preserved objects are never evicted),
records the exact metrics and alignment evidence, and writes `result.json`.
The PR 4 case builder packages the same evidence for offline inspection.
