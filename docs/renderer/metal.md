# Faithful Metal backend

The correctness backend executes captured OpenGL work through Metal and
compares it with the original sealed frame. Qualification covers the named
workload or checkpoint prefix.

## Pipeline

```
EU4 (original OpenGL, authoritative)
  → in-process hook captures ordered events/resources + reference pixels
  → frame seal (session/frame/stream/reference digests)
  → shadow coordinator translates shaders and releases the Metal bridge
  → Metal replay consumes sealed state/resources in the same process
  → readback + checkpoint reconciliation + exact attachment comparison
  → optional offline independent replay of the same seal
```

Entry points: [frame trace](../../benchmark/eu4_gfx_frame_trace.c),
[shadow bridge](../../renderer/integration/eu4_gfx_shadow_bridge.mm),
[replay core](../../renderer/metal/eu4_gfx_frame_replay.mm),
[independent replay](../../automation/gfx_independent_replay.py),
[continuous supervisor](../../automation/m7_continuous.py).
The [native build closure](../../automation/replay_host_dependencies.py) includes
both replay translation units. [Build tests](../../tests/test_replay_host_dependencies.py)
and [ABI and replay tests](../../tests/test_gfx_metal_replay.py) exercise this path.
The trusted checker selects the two replay translation units from the authorized
candidate baseline. The retained M7 candidates use their original `benchmark/`
layout. The current renderer uses `renderer/metal/` for both units.
[Trusted checker tests](../../tests/test_m7_event20_prefix.py).

[Live draw routing](../../renderer/integration/eu4_gfx_draw_route.mm) and
[initialization](../../renderer/integration/eu4_gfx_init.mm) also manage live
Metal state and call capture/profiling code in `benchmark/`. The live Metal
resource and draw files use that context. The standalone replay closure uses
engine clear flags and the shared UV2 diagnostic format.

## Contracts

- **Seal binding.** Session, capture frame, event-stream digest, reference
  digest and manifest digest are bound. A receipt without a seal is
  `unverified_session`, not qualified.
- **Exactness.** RGBA attachments require equality on all four channels,
  including alpha. M7 event 1 writes RGB8, so the comparison uses RGB even
  though the transport is BGRA8. Depth and stencil snapshots are separate
  diagnostics. A float32 depth readback does not establish storage equality.
  Policy thresholds have their own verdict. See the
  [frame pixel comparator](../../automation/gfx_frame_pixels.py) and the
  [M7 exactness contract](../../analysis/m7/m7-exact-prefix-report.md#exactness-contract).
- **Checkpoints.** Request and result are reconciled one-to-one; missing,
  duplicate, substituted or cross-capture evidence fails closed. Writers are
  derived from the resource graph, not by `event - 1`.
- **Coverage.** Operation coverage and rendering-state coverage are separate
  axes. Unsupported state is `unsupported_state`, not a difference.
- **Execution.** `status=ok`, `command_status=4`, encoded==events, failed==0
  and a real readback are required. A completed command buffer is not pixel
  correctness.
- **Producer receipt.** Binds executable/build/capture/result and now the
  effective recipe: consumed `EU4_FRAME_REPLAY_*` overrides, faults, shader
  artifact identity and policy digest. A diagnostic recipe is not correction
  evidence.

## State coverage

Supported and explicitly rejected values are projected through
`state_coverage`. Important current limits:

- dither, multisample, depth/stencil and some blend/format combinations remain
  partial;
- DXT1 RGB (`0x83F0`) uses the decoded representation because Metal has no BC1
  RGB format; DXT1_ALPHA/DXT3/DXT5 map to BC1/BC2/BC3;
- `GL_FRONT_AND_BACK` culling is emulated with an empty scissor;
- sampler identity includes min/mag, S/T/R wrap, LOD clamps, bias and compare
  state;
- signed scissor boxes are intersected from both endpoints.

See [render state](../renderer-reconstruction-and-differential-validation.md) and
the [review matrix](../../plans/closed/harness-review-20261120/implementation.md).

The direct, independent and trusted single-frame tools share Metal host
invocation and shader translation in [frame execution](../../automation/gfx_frame.py).
The ordered replay supervisor uses the same host with its sequence inputs.
[Portable case tools](../../automation/gfx_replay_case.py) verify Strategy B
v1/v2 and compare legacy dual-run packages through `eu4_replay case`.
Historic absolute frame paths resolve to the copied payloads under `frames/`
after relocation. The reader verifies both packaged digests before reporting
a valid pixel comparison. Independent renderer qualification requires the
Strategy B or signed OpenGL evidence gate. The legacy builder remains available
for dual-run results.

## Qualification

`qualified_match` requires verified identity, complete capture/provenance,
complete operation and state coverage, `ok` checkpoint reconciliation, exact
pixels and no fault experiment. Anything weaker is labelled:

- `qualified_difference` — complete evidence, confirmed difference;
- `difference_with_partial_state_coverage`;
- `unsupported_state`;
- `incomplete_capture` / `incomplete_diagnostics` / `invalid_provenance`;
- `fault_experiment`.

The independent correction checker
([correction_accept.py](../../automation/correction_accept.py)) recomputes
before/after and protected controls. It is separate from PR review and from the
campaign checker.

## Selected-draw OpenGL control

The [authority registry](../../automation/gfx_gl_control_authorities.py) pins
the UUID-linked driver `153fd964…`, native executor `092a3cdb…`, restricted
input verifier and receipt signer for the default-framebuffer route. Run
`python3.14 -m automation.native_build gl-control` to deploy the pinned pair.
The [operator invocation](../agent-tools.md#eu4_capture) uses both reported
paths and the authorized signing-key file. The [native review record](https://github.com/HagbardCel/eu4-mac-perf/pull/304)
records the toolchain and tested signed synthetic draw. The [authority evidence inventory](../evidence-retention.md#r2b-opengl-authority)
locates the retained native review, exact executables, and the synthetic and
production receipts.

Historical driver `19f819b4…`, executor `254618ef…` and their component
bindings remain available for validating older receipts with the recorded
files. The executor's original link recipe lacks `LC_UUID`. Preserve the
historic files at their recorded paths when rechecking an archived receipt.
[Historical independent review](../../analysis/m7/final-review-279.md)

The Ed25519 receipt authenticates the registered execution identity, challenge,
capture and selected draw, effective input package, output digest and geometry,
and completed status. The [qualifier](../../automation/gfx_gl_control_qualification.py)
separately verifies same-seal input and checkpoint identity, live-drawable
equivalence, and exact output against the original. The historical
[`90825686…` control](../../analysis/m7/gl-control-qualification-90825686.md)
remains unqualified. Drawable equivalence is unproven and the earlier comparison
differed at 347 pixels. M7 event 1 targets offscreen framebuffer 6
(`tex:433`) and has no qualified same-target OpenGL execution control.
[Native fixture and receipt tests](../../tests/test_gfx_gl_draw_control.py) and
[qualification tests](../../tests/test_gfx_gl_control_qualification.py) cover
the supported route and authority checks.

## Current verification boundary

- [M6 `menu-occ300`](../../analysis/m6/qualification-report.md#offline-captured-state-replay)
  replay encoded 326 events with zero differences across 1,930,176 pixels,
  including alpha, against reference `ff2f4e2b…`. Its captured-state result
  leaves upstream terrain re-rendering outside the verified workload.
- [R0 replay of M7 seal `47590918…`](../../plans/repository-refactor/r0-audit.md#event-1-observable-matrix)
  verified the prefix through event 0 and found 23 RGB differences at event 1,
  with maximum channel error 1. The [historical precise-default candidate](../../plans/repository-refactor/r0-evidence.md#event-1-numerical-identity)
  had 20 differences on that seal under a distinct build and recipe. The
  [later sparse seals](../../analysis/m7/event1-investigation.md) are separate
  observations. They leave contributing-fragment correspondence unresolved.
- Authentic presents 616 and 904 localized the first divergence to the 14→15
  interval. Present 615 remains [14, 17]. The cause remains unresolved.

See [current project status](../status.md) and [shadow worker](../metal-shadow-worker.md).
