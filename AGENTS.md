# Agent defaults

## EU4 reconstruction and migration

For cohesive reconstruction or migration of EU4 functionality—especially `_Gfx*` OpenGL→Metal work (shaders, render state, draws, lifecycle remediations)—**do not** implement the migration body by hand in an interactive session.

**Default path:** run an OpenCode campaign through `python3 -m automation.cli` with the appropriate skill (load `opengl-metal-reconstruction` when replacing OpenGL graphics calls) and an independent campaign checker under `campaigns/`.

**Interactive / human focus:** semantic constraints, acceptance criteria, evidence review, and deciding genuine ambiguities or `BLOCKER:` lines.

See [automation/README.md](automation/README.md) for the runner, worktree pin, and session contract. Campaign layout lives under `campaigns/`. A checker `pass` is offline qualification only; independent PR review remains the merge gate.
