# Live differential experiment runbook

Run these only after the offline/native suites pass and `eu4_session lease` says
`free`. Never terminate another worktree's process. Record for each launch:
hypothesis, required observation, expected termination, maximum runtime, and
preserved evidence.

## 1. Reference identity probe (one process)

Scenario: `analysis/infra/scenarios/opengl-save-consistency.json` (change
`observation.max_frames` to 5 for a probe). Confirm every emitted record has a
non-zero `game_day` and the same `update_identity` for the same day. A zero
identity means the singleton was not live; do not proceed.

## 2. Experiment A — OpenGL/OpenGL consistency

Scenario: `analysis/infra/scenarios/opengl-save-consistency.json`

- Both processes load the same last save and request one pause at the target draw
  count.
- Require paired, aligned, **validly compared** frames, not merely observed.
- If alignment fails, stop; do not run GL/Metal.
- If the images differ, that is a scenario/nondeterminism result; record it
  without calling it a Metal defect.

## 3. Experiment B — Metal/Metal consistency

Scenario: `analysis/infra/scenarios/metal-frontend-consistency.json`

- Both processes use the reconstructed Metal backend on the frontend.
- If the frontend does not reach the target phase, record
  `reference_phase_timeout`; do not compare loading screens.
- If one process crashes, classify `process_failure` and preserve the case.

## 4. Experiment C — GL/Metal differential

Scenario: `analysis/infra/scenarios/mixed-frontend-differential.json`

- Use only after A and B establish correspondence.
- Initial acceptance is at least 20 actually compared consecutive frames with
  no GPU execution errors and valid provenance.
- A confirmed difference must preserve the first failing pair and build a case.

## 5. Experiment D — controlled defect

Use the isolated `tex7` fixture or a disposable test build; never inject the
defect into the production M6 renderer. The offline regression must detect the
first affected frame before a live confirmation.

## Commands

```sh
# Profile and lease (does not launch)
python3.14 -m automation.agent_tools eu4_session lease \
  --executable /Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4

# Run one authorized campaign (ledger must be authorized first)
python3.14 -m automation.gfx_dual_run run \
  --scenario analysis/infra/scenarios/opengl-save-consistency.json \
  --run-root .local/infra/live \
  --ledger .local/infra/live/authority.json \
  --mode streaming --profile targeted

# Read outcomes without launching another game
python3.14 -m automation.agent_tools eu4_diff_inspect --run <run-dir>
python3.14 -m automation.agent_tools eu4_capture health --run <run-dir>
```

`classifications`: `match`, `rendering_difference`, `gpu_execution_failure`,
`alignment_failure`, `incomplete`, `infrastructure_failure`, `process_failure`.
Only `match` qualifies equivalence.
