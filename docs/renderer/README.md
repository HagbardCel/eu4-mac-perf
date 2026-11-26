# Renderer knowledge

Start with [project status](../status.md) for current capabilities,
[harness operations](../harness.md) for commands, and
[protected evidence](../evidence-retention.md#protected-collections-and-recovery-status)
for recovery status.

## Components and backends

- [Original EU4 renderer](eu4_renderer.md) covers original OpenGL observation, draw and resource lifecycles, and the limits of static and captured evidence.
- **Faithful OpenGL** is a reviewed selected-draw control for the supported
  default-framebuffer route. Qualification requires same-seal inputs,
  live-drawable equivalence and exact output. M7 event 1 uses offscreen
  framebuffer 6 and remains outside this route.
  [Driver](../../automation/gfx_gl_control_renderer.py) ·
  [Native executor](../../renderer/gl/eu4_gfx_draw_control.c) ·
  [Qualification and limitations](metal.md#selected-draw-opengl-control)
- [Faithful Metal](metal.md) covers captured-state replay and exact attachment comparison.
- [Optimized Metal](metal_optimized.md) records the proposed workload, measurement and qualification requirements.

## Functions

Use the [pinned GOG EU IV executable identity](../../reverse_engineering/target.json),
the [static RE index and queries](../../reverse_engineering/README.md), and the
[semantic dossier inventory](../../analysis/reconstruction/gfx-semantic/v1/inventory.md).
The six dossier families describe 92 functions at the static semantic level.

| Family | Maintained implementation and evidence |
| --- | --- |
| [Init, context and presentation](../../analysis/reconstruction/gfx-semantic/v1/init/) | [Native lifecycle hooks](../../renderer/integration/eu4_gfx_init.mm) · [Entry registry](../../renderer/integration/eu4_gfx_entries.def) · [Integration tests](../../tests/test_gfx_init.py) |
| [Buffers](../../analysis/reconstruction/gfx-semantic/v1/buffers/) | [Native buffer code](../../renderer/metal/eu4_gfx_buffers.mm) |
| [Shaders and uniforms](../../analysis/reconstruction/gfx-semantic/v1/shaders/) | [Shader lifecycle](../../renderer/metal/eu4_gfx_shader.mm) · [Translation](../../automation/gfx_shader_translate.py) |
| [Textures, targets and resolves](../../analysis/reconstruction/gfx-semantic/v1/textures/) | [Native texture code](../../renderer/metal/eu4_gfx_texture.mm) |
| [Render state](../../analysis/reconstruction/gfx-semantic/v1/state/) | [Faithful replay state](../../renderer/metal/eu4_gfx_frame_replay.mm) |
| [Draw and submission](../../analysis/reconstruction/gfx-semantic/v1/draw/) | [Metal encoder](../../renderer/metal/eu4_gfx_draw_encode.mm) · [Independent replay](../../automation/gfx_independent_replay.py) |

### Initial function entries

- **`_GfxSetConstantBuffer` · RVA `0x15ec36a`.** With `ctx = *RDI` and
  the slot in `ECX`, the 75-byte body writes a four-byte word count at
  `ctx + 0xc8 + 16 × slot`, stores the eight-byte buffer pointer from
  `RSI` at `ctx + 0xd0 + 16 × slot`, and writes the dirty byte `1` at
  `ctx + 0x128`. With `R9D == -1`, it reads the first dword of the buffer,
  adds 3 with 32-bit wrap and shifts right logically by 2. Otherwise it adds
  3 to `R9D` with 32-bit wrap, interprets the sum as signed, and divides
  by 4 toward zero. The observed post-call `RAX` holds `ctx`. Caller
  liveness, the declared return type, and slot use beyond 0–5 remain open.
  The C++ reconstruction and the 414-case P-code differential cover slots 0–5.
  The separate recorded-original certificate reports 64/64 matches for
  `gfx-live-20261025T155406Z`, with scoped qualification. It observed slots
  0–2, lacked slots 3–5 and zero input, and measured 10 distinct behaviors
  against a floor of 12.
  [Static ABI](../../analysis/reconstruction/_GfxSetConstantBuffer/contract.md) ·
  [C++ source](../../renderer/engine/gfx_set_constant_buffer.cpp) ·
  [Native and ABI tests](../../tests/test_reconstruction_faithful.py) ·
  [P-code differential](../../analysis/reconstruction/_GfxSetConstantBuffer/differential/README.md) ·
  [Recorded-original certificate](../../results/20261025T155406Z-gfx-live-cert/tier1-certificate.json) ·
  [Coverage audit](../../results/20261025T155406Z-gfx-live-analysis/e1-coverage-audit.md)

  All 64 raw transcripts remain outstanding from operator archive
  `pr-d-gfx-live-20261025T155406Z`.
  [Recovery status](../evidence-retention.md#protected-collections-and-recovery-status) ·
  [Archive identity](../../results/20261025T155406Z-gfx-live-publication/publication-summary.md#operator-archive)

- **`_GfxForceWireframe` · RVA `0x15ed175`.** The static contract establishes
  the single store of `SIL` to `[RDI + 0x194]` and preservation of `RAX`.
  Caller liveness remains open. The implementation handoff is prospective,
  and qualification is static-only.
  [Pinned static contract](../../analysis/reconstruction/_GfxForceWireframe/contract.md) ·
  [Implementation handoff](../../analysis/reconstruction/_GfxForceWireframe/package.md)

## Findings

| Component and case | Established conclusion and scope | Evidence |
| --- | --- | --- |
| Submission · paused-Venice observer | The completed run found zero eligible buffer-bind pairs in every phase. The observer and engagement gates failed for the tested workload and predicate. | [Observer smoke and registered run](../../analysis/gfx-subrecord-pr1-gonogo.md#pr2-observer-smoke-2026-10-04-complete--engagement-failed) |
| Textures · present 904 | At checkpoint 15, original compressed blocks yielded 4 differing pixels versus 858 from the decoded variant, both compared with the same sealed original. The decoded variant used a transformed scratch copy. The four-pixel residual remains unexplained. | [Same-capture texture comparison](../../analysis/harness-remediation/compressed-904.md#results) |
| Original GL and Metal · M4 Simple draw | The fixed logical comparison of `gl-reference-07` and the recorded Metal output matched all 1,930,176 pixels. Window visibility was verified separately. | [Qualification and evidence](../../analysis/m4/qualification-report.md) |
| Metal replay · M6 `menu-occ300` | Captured-state replay encoded 326 events and matched all 1,930,176 pixels including alpha against `ff2f4e2b…`. Upstream terrain re-rendering falls outside this result. | [Captured-state replay and reference identity](../../analysis/m6/qualification-report.md#offline-captured-state-replay) |
| Metal replay · M7 exact prefix | The trusted R0 replay of seal `47590918…` matched through event 0. Event 1 on RGB8 `tex:433` differed at 23 RGB pixels, with maximum channel error 1. Depth is diagnostic and the cause is open. | [R0 replay and event-1 coverage](../../plans/repository-refactor/r0-audit.md#event-1-observable-matrix) · [Independent prefix checker](../../automation/gfx_prefix_check.py) |
| M7 event 1 · axial displacement | All four one-pixel axial directions fell below the matched null for the historical 20-pixel candidate on `47590918…`. The falsification covers those directions on that candidate. | [Matched-null experiment](../../analysis/m7/event1-investigation.md#experiments) |
| M7 event 1 · sparse observation | Later seal `1efc8880…` measured 32-bit unsigned-normalized offscreen depth on framebuffer 6, refused the 24-bit control and produced zero UV2 pixels. Fragment correspondence remains unresolved. | [Offscreen depth and UV2 results](../../plans/repository-refactor/r0-audit.md#event-1-observable-matrix) · [Sparse investigation](../../analysis/m7/event1-investigation.md#one-use-capture-after-review) |
