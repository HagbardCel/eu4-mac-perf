# gfx operation comparison contract (v0)

This contract covers `analysis/gfx-ops/v0/capture/`, the first live
`_GfxSetViewPort` capture, replayed through `metal-shadow/v0`.

## Scope

A `pass` establishes **`function_cpu_state`**: the six CPU-visible viewport
words produced by the reconstructed Metal path equal the words the original
engine left at `[master+0xac]` for the captured invocations.

It does not establish:

- GPU viewport or scissor state;
- pixels or render-pass output;
- complete engine-path equivalence;
- equivalence for inputs or resource lifetimes not present in the capture.

The adapter declares this scope. A future adapter must declare a different
scope if it establishes more.

## Startup preconditions

The replay first executes the captured initialization through the existing
shadow worker. Only the fields the operation replay depends on are strict:

| Field | Rule |
| --- | --- |
| `configure_window.window_id` | exact echo |
| `configure_window.source` | exact echo |
| `configure_window.width` | exact echo, conditioned on the capture |
| `configure_window.height` | exact echo, conditioned on the capture |
| `init.success` | original and worker both 1 |
| `init.initialized` | original `master_non_null`; worker `initialized` and `backend_present` |
| `init.width` | exact, conditioned on the capture |
| `init.height` | exact, conditioned on the capture |
| `init.source` | exact |
| `init.primitive` | exact |
| `init.viewport` | six words exact |
| `init.dirty` | exact |

MSAA, anisotropy, texture LOD, maximum texture size, and snapshot bytes are
outside this contract. The existing `gfx-startup/v0` comparison contract
remains their home.

A missing or mistyped worker field is `unavailable`, not a mismatch. A
successfully returned, typed, differing value is a mismatch.

## Operation comparison

`_GfxSetViewPort` maps to the worker's `set_viewport` with the captured
24-byte image as six signed 32-bit words. The comparison is exact for
`viewport[0]` through `viewport[5]`.

No raw pointer, GL object name, Metal resource address, counter, or snapshot
byte is compared.

## Ordering and qualification

- Records are replayed in captured `call_id` (invocation) order.
- `overlaps > 0` makes serial ordering unprovable; the verdict is `unsupported`
  with reason `ordering_overlap`, and the worker is not asked to guess.
- The viewport adapter qualifies only when the startup precondition matched and
  the record carries six input and six output words.
- An adapter that cannot establish its prerequisite declines; the operation is
  `unsupported` and cannot contribute to `pass`.
- Dependencies are adapter-owned. There is no function-name dependency graph.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `pass` | every declared field matched for the captured invocations |
| `mismatch` | a successfully executed, qualified comparison differed |
| `unsupported` | the adapter/worker lacks the operation, ordering is unprovable, or qualification declined |
| `incomplete` | the capture cannot establish expected behavior |
| `unavailable` | the worker or environment failed; do not change Metal blindly |

A worker failure, missing state, or protocol error never becomes a mismatch.
