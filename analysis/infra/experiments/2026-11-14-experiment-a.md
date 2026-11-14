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


## Follow-up: game-speed-zero pause freezes simulation

An opt-in `pause_mode=game_speed_zero` writes the game speed field to 0 at the
configured draw threshold. In session `20261114T183243Z-191dd168` both observers
recorded `method=game_speed_zero` at 506 draws, and both streams stayed on
`game_day=56456976`.

- 20 paired attempts, **15 aligned, 5 not aligned**
- **0 valid comparisons**: the raw dumps were missing. The only capture request
  left in each observe directory was the stop-time two-frame request; the
  phase-entry window had already been pruned.
- health healthy; classification `alignment_failure`.

This is the first configuration that froze the simulation deterministically
without an input event, but the raw-payload window is now the blocking bug.


## Follow-up: speed-zero freezes the day, not the render stage

Several attempts with `game_speed_zero` at 500-506 draws froze both streams on
the same game day, but at different render stages (for example 374 vs 218 draws,
or loading 219 vs map 380). Alignment then refused the pairs. A signature gate
was tested as an extra condition, but the first map signature varied between
loads, so a fixed signature is not a stable scene selector.

The blocking requirement is therefore not just freezing simulation time; it is
freezing both processes at the **same render stage**. That remains open.


## Follow-up: workload-window pause is not stable either

A 350-450 draw pause window was intended to select the map frame and exclude
loading (219/506 draws), but one run never entered the window and produced no
pause record. The loading path does not expose a stable draw-count selector.

The committed scenario therefore remains at the 100-draw threshold with
`game_speed_zero`; it freezes the day but not always the render stage.


## Latest: same day, different render stages

Session `20261114T184826Z-643bc5b0`: speed-zero at the 100-draw threshold froze
both streams on `game_day=56456976`, but the reference was on the map
(`0xfaced05509c2a707`, 374 draws) while the candidate was still loading
(`0x038458cf31968881`, 218 draws). All 20 pairs were `ambiguous`; no raw payload
was comparable.

This confirms that game-day identity and simulation freeze are not sufficient.
A deterministic **render-stage** selector or a genuinely paused save is required.
