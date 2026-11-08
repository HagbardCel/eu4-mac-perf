# Agent defaults

## EU4 reconstruction and migration

For cohesive reconstruction or migration of EU4 functionality—especially `_Gfx*` OpenGL→Metal work (shaders, render state, draws, lifecycle remediations)—**do not** implement the migration body by hand in an interactive session.

**Default path:** run an OpenCode campaign through `python3 -m automation.cli` with the appropriate skill (load `opengl-metal-reconstruction` when replacing OpenGL graphics calls) and an independent campaign checker under `campaigns/`.

**Interactive / human focus:** semantic constraints, acceptance criteria, evidence review, and deciding genuine ambiguities or `BLOCKER:` lines.

See [automation/README.md](automation/README.md) for the runner, worktree pin, and session contract. Campaign layout lives under `campaigns/`. A checker `pass` is offline qualification only; independent PR review remains the merge gate.

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
