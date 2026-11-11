# Agent defaults

## EU4 reconstruction and migration

For cohesive reconstruction or migration of EU4 functionality—especially `_Gfx*` OpenGL→Metal work (shaders, render state, draws, lifecycle remediations)—**do not** implement the migration body by hand in an interactive session.

**Default path:** run an OpenCode campaign through `python3 -m automation.cli` with the appropriate skill (load `opengl-metal-reconstruction` when replacing OpenGL graphics calls) and an independent campaign checker under `campaigns/`.

**Native DSH exception:** when the operator starts a `/goal` reconstruction experiment through DSH Web, DSH owns orchestration. Work directly in the session's authorized worktree; do not launch the OpenCode runner and do not build a replacement agent loop. OpenCode remains the supported default for campaign-run work.

**Interactive / human focus:** semantic constraints, acceptance criteria, evidence review, and deciding genuine ambiguities or `BLOCKER:` lines.

See [automation/README.md](automation/README.md) for the runner, worktree pin, and session contract. Campaign layout lives under `campaigns/`. A checker `pass` is offline qualification only; independent PR review remains the merge gate.

### Reusable reconstruction capabilities

All new graphics reconstruction and migration work should strengthen the autonomous investigation, implementation and validation workflow.

Prefer functionality that is independently usable by DSH and OpenCode through existing command-line tools.

When implementing a new rendering capability:

- Reuse existing graphics-state reconstruction, backend lifecycle, evidence capture, replay and validation.
- Separate game-independent mechanisms from function-specific semantic knowledge.
- Make new observation or validation functionality reusable across graphics operations.
- Demonstrate reuse with an existing second case when practical.
- Avoid one-off launchers, capture scripts, agent orchestration and duplicate rendering implementations.
- Do not generalize speculative future requirements. Extend shared abstractions only where a real use case requires them.
- Preserve explicit evidence provenance, unsupported-state diagnostics and independently reviewable outcomes.

The objective is not to maximize tooling. It is to minimize the engineering required for each subsequent authentic graphics reconstruction.

## L3.2b completion

For the L3.2b-II draw-packet completion campaign, do not pre-assign `_Gfx*`
functions or predefine shader → texture → state contract versions. Use the
dynamic coordinator ([automation/gfx_l32b_complete.py](automation/gfx_l32b_complete.py))
and let the agent inventory the unresolved fields, choose draw-time reads or
minimal entry observers, and produce provisional checkpoints. Contract
qualification is a separate campaign with an independent reviewer; a
`qualified` review outcome is required. The v1 checker
`automation/gfx_draw_accept.py` is frozen by the completed L3.2b-I manifest; new
campaigns use `automation/gfx_draw_accept_v2.py`. Original shader and texture
bytes stay under `.local`; only hashes, lengths and provenance are tracked.
