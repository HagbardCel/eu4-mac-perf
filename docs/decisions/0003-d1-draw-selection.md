# 0003 — Select a primitive-producing draw

**Status:** accepted 2026-11-02. Immutable. Supersede with a later record.

**Scope:** pinned GOG EU IV 1.37.5 x86-64. This decision does not authorize a live process modification, an EU4 launch, or a new export.

## Decision

[0002](0002-first-live-metal-draw.md) stays in force, including its three D1 gates. `draw_nonvisible` stays a diagnostic reason, not a D1 state. This record changes only which `_GfxDraw` invocation is the selected target.

The selected invocation is the first `_GfxDraw` at caller `0xedfcd1` with game provenance, a complete master read, a non-null shader pointer, mode 5 (`GL_TRIANGLE_STRIP`), and an effective vertex count of at least 3. Counts 0, 1, and 2 submit no triangle-strip primitives.

A later change of target requires another decision.

## What this does not change

0001, 0002's gates, the Metal-only rule, and the refusal to treat a candidate as `packet_ready`. This record does not authorize a launch.
