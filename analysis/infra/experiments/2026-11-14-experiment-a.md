# Experiment A — OpenGL/OpenGL consistency (2026-11-14)

Scenario `analysis/infra/scenarios/opengl-save-consistency.json`; session
`20261114T181230Z-d28f6fee` (local `.local/infra/live-a6`).

## Execution

- Both authentic OpenGL processes loaded the same last save.
- The native single pause request was posted successfully on both sides
  (`pause-requested.json`: `status=requested`, `draws=506`, down=1, up=1).
- Both screens show the `Paused` banner and `11 November 1444`.
- GPU health: `healthy`; no draw refusal or instrumentation failure.

## First aligned comparison

- Frame 74/74; identity equal; caller signature equal; draws 370/370; extent
  1728×1117 (scale 1).
- 26,042 of 1,930,176 pixels differed (1.3492%); max channel error 228; mean
  absolute error 0.564.
- Map region: 1.56% differing, structural correlation 0.9889.
- UI region: 1.00% differing, structural correlation 0.9880.
- No mirroring.
- Verdict: `rendering_difference` (tolerant thresholds 8 / 1% / 1.5).

## Interpretation

This is a **same-backend consistency failure**, not a Metal defect. The images
are visually the same map; the differences follow province-border/thin geometry
edges across the map. The pause is effective (date and banner identical), but
the two captured frames still differ slightly. Candidate explanations to test
next:

1. GL line/raster sample nondeterminism between the two contexts;
2. the capture occurring on slightly different render-thread frames despite the
   paused simulation;
3. another still-animated map/UI layer.

Experiment A therefore fails its strict gate and Experiment B/C must not be
claimed until this is explained or the tolerance is justified by repeated
same-backend measurements.

The diagnostic case (raw frames, difference image, statistics, analysis) was
built at the session's `case/` directory; raw artifacts stay local.


## Follow-up: pause timing is not synchronized

A diagnostic run (`20261114T181705Z-c19ac0c9`) requested the same pause but the
two processes froze at different game days (`56457144` vs `56457216`). Nine of
ten pairs were not aligned; no comparable frame beyond the first was produced.
The pause event itself is accepted, but pausing at a draw threshold does not
guarantee the same simulation moment.

Next requirement: pause before the simulation advances (or coordinate the pause
on the engine update identity), not merely at a render-workload threshold.


## Follow-up: first-game-day pause is too early

Requesting the pause on the first non-zero game day posted the SDL event at only
6 draws, while the game was still loading. The event did not produce a paused
in-game state; the processes later rendered different scenes (loading vs map)
on the same game day. The day-level identity plus a comparable draw count was
not sufficient.

A weak-identity guard now requires coarse content corroboration when identities
are day-only and caller signatures differ. In that follow-up the guard would
have refused the comparison instead of reporting an 85% rendering difference.
This deliberately trades away detection of a catastrophically corrupted image
until a stronger tick identity exists.
