# Renderer knowledge

Start with [project status](../status.md) for current capabilities and with
[harness operations](../harness.md) for commands and recovery.

## Components and backends

- [Original EU4 renderer](eu4_renderer.md) covers original OpenGL observation, draw and resource lifecycles, and the limits of static and captured evidence.
- [Faithful Metal](metal.md) covers captured-state replay, exact attachment comparison, and the reviewed selected-draw OpenGL control.
- [Optimized Metal](metal_optimized.md) records the proposed workload, measurement and qualification requirements.

## Functions

Use the [pinned GOG EU IV executable identity](../../reverse_engineering/target.json),
the [static RE index and queries](../../reverse_engineering/README.md), and the
[semantic dossier inventory](../../analysis/reconstruction/gfx-semantic/v1/inventory.md).
The six dossier families describe 92 functions at the static semantic level.

| Family | Maintained implementation and evidence |
| --- | --- |
| [Init, context and presentation](../../analysis/reconstruction/gfx-semantic/v1/init/) | [Native lifecycle hooks](../../benchmark/eu4_gfx_init.mm) |
| [Buffers](../../analysis/reconstruction/gfx-semantic/v1/buffers/) | [Native buffer code](../../benchmark/eu4_gfx_buffers.mm) |
| [Shaders and uniforms](../../analysis/reconstruction/gfx-semantic/v1/shaders/) | [Shader lifecycle](../../benchmark/eu4_gfx_shader.mm) · [Translation](../../automation/gfx_shader_translate.py) |
| [Textures, targets and resolves](../../analysis/reconstruction/gfx-semantic/v1/textures/) | [Native texture code](../../benchmark/eu4_gfx_texture.mm) |
| [Render state](../../analysis/reconstruction/gfx-semantic/v1/state/) | [Faithful replay state](../../benchmark/eu4_gfx_frame_replay.mm) |
| [Draw and submission](../../analysis/reconstruction/gfx-semantic/v1/draw/) | [Metal encoder](../../benchmark/eu4_gfx_draw_encode.mm) · [Independent replay](../../automation/gfx_independent_replay.py) |

### Initial function entries

- **`_GfxSetConstantBuffer` · RVA `0x15ec36a`.** The 75-byte original leaves
  the context in `RAX`. Caller liveness and slot use beyond 0–5 remain open.
  The C++ reconstruction and the 414-case P-code differential cover slots 0–5.
  The separate recorded-original certificate reports 64/64 matches for
  `gfx-live-20261025T155406Z`, with scoped qualification. It observed slots
  0–2, lacked slots 3–5 and zero input, and measured 10 distinct behaviors
  against a floor of 12.
  [Static ABI](../../analysis/reconstruction/_GfxSetConstantBuffer/contract.md) ·
  [C++ source](../../analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.cpp) ·
  [P-code differential](../../analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md) ·
  [Recorded-original certificate](../../results/20261025T155406Z-gfx-live-cert/tier1-certificate.json) ·
  [Coverage audit](../../results/20261025T155406Z-gfx-live-analysis/e1-coverage-audit.md)
- **`_GfxForceWireframe` · RVA `0x15ed175`.** The static contract establishes
  the single store of `SIL` to `[RDI + 0x194]` and preservation of `RAX`.
  Caller liveness remains open. The implementation handoff is prospective
  and qualification ends at the static contract.
  [Pinned static contract](../../analysis/reconstruction/_GfxForceWireframe/contract.md) ·
  [Implementation handoff](../../analysis/reconstruction/_GfxForceWireframe/package.md)

## Findings

| Question | Evidence |
| --- | --- |
| Did the completed paused-Venice observer find eligible buffer-bind pairs? | [Observer smoke and registered run](../../analysis/gfx-subrecord-pr1-gonogo.md#pr2-observer-smoke-2026-10-04-complete--engagement-failed) |
| How did compressed blocks change present 904? | [Same-capture texture comparison](../../analysis/harness-remediation/compressed-904.md#results) |
| What did the later M7 event-1 sparse capture establish? | [Offscreen depth and UV2 results](../../plans/repository-refactor/r0-audit.md#event-1-observable-matrix) · [Sparse investigation](../../analysis/m7/event1-investigation.md#one-use-capture-after-review) |
| What did M6 `menu-occ300` qualify? | [Captured-state replay and reference identity](../../analysis/m6/qualification-report.md#offline-captured-state-replay) |
| Did one-pixel axial displacement explain the 20-pixel candidate? | [Event-1 matched-null experiment](../../analysis/m7/event1-investigation.md#experiments) |
| Where did the trusted M7 prefix diverge? | [R0 replay and event-1 coverage](../../plans/repository-refactor/r0-audit.md#event-1-observable-matrix) · [Independent prefix checker](../../automation/gfx_prefix_check.py) |
