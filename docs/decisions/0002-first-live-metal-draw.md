# 0002 — First live Metal draw

**Status:** accepted 2026-11-02. Immutable. Supersede with a later record.

**Scope:** pinned GOG EU IV 1.37.5 x86-64. This decision does not authorize a live process modification, an EU4 launch, or a new export.

## Decision

[0001](0001-semantic-gfx-then-metal.md) stays the architectural choice: one Metal backend, OpenGL only as a separate reference run, replacements in execution order. This record changes only the next runtime target.

The next target is D1, the first eligible game-owned draw. It is not a presented frame. `_GfxDraw`, `_GfxDrawIndexed`, and `_GfxDrawIndexedInstanced` are eligible. The empty `_GfxDrawInstanced` stays original. `_GfxDraw2dLines` is not a target unless a stop says it blocks the path.

D1 has three gates on one invocation, one pid, and the pinned executable: the game entered that draw (`draw_reached`), the inputs needed to replay it are complete (`packet_ready`), and Metal finished with saved offscreen pixels that differ from the clear (`draw_executed`). A command buffer that completes without those pixels is not `draw_executed`.

`draw_reached` requires verified provenance from original EU4 execution. A trampoline, instrumentation entry, or synthetic harness does not qualify. If the immediate caller is a wrapper, the receipt names that game-owned call site.

If the selected draw intrinsically cannot produce non-clear color pixels, report `draw_nonvisible` and stop. That is a diagnostic reason, not another D1 state. Changing the D1 target requires a separate decision.

`gl_diagnostic` remains the init-loop classification. It is not a D1 state. `blocked_at` is a valid terminal for a preceding dependency and does not satisfy init success or D1 execution.

## What this does not change

0001's reading, the Metal-only rule, and the refusal to translate OpenGL in process. A complete frame stays later. This record does not authorize a launch.
