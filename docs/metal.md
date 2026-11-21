# Faithful Metal backend

The correctness backend executes captured OpenGL work through Metal and
compares it with the original sealed frame. It is **not** a Metal-only game
session and does not claim event-16 or full-frame parity.

## Pipeline

```
EU4 (original OpenGL, authoritative)
  → in-process hook captures ordered events/resources + reference pixels
  → frame seal (session/frame/stream/reference digests)
  → shadow coordinator translates shaders and releases the Metal bridge
  → Metal replay consumes sealed state/resources in the same process
  → readback + checkpoint reconciliation + exact four-channel comparison
  → optional offline independent replay of the same seal
```

Entry points: [frame trace](../benchmark/eu4_gfx_frame_trace.c),
[shadow bridge](../benchmark/eu4_gfx_shadow_bridge.mm),
[replay core](../benchmark/eu4_gfx_frame_replay.mm),
[independent replay](../automation/gfx_independent_replay.py),
[continuous supervisor](../automation/m7_continuous.py).

## Contracts

- **Seal binding.** Session, capture frame, event-stream digest, reference
  digest and manifest digest are bound. A receipt without a seal is
  `unverified_session`, not qualified.
- **Exactness.** Raw equality requires zero mismatched pixels, zero alpha
  differences and zero maximum error on all four BGRA channels. Policy
  thresholds are reported separately and never become exactness. See
  [comparator](../automation/m6_compare.py).
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

See [render state](renderer-reconstruction-and-differential-validation.md) and
the [review matrix](../plans/closed/harness-review-20261120/implementation.md).

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
([correction_accept.py](../automation/correction_accept.py)) recomputes
before/after and protected controls. It is separate from PR review and from the
campaign checker.

## Current verification boundary

The authentic 616/904 frames localized the first divergence to the 14→15
interval; present 615 remains [14, 17]. No renderer cause is established. The offline GL control
still disagrees with live GL, so it cannot assign blame. Do not promote a
control result to parity or an accepted correction. Current capability state:
[status](status.md). Historical evidence:
[analysis/m7](../analysis/m7/m7-readiness.md), [metal shadow worker](metal-shadow-worker.md).
