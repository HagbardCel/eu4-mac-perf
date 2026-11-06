# gfx-startup comparison contract (v0)

Semantic gates for replaying a `gfx-startup-ref/v0` fixture through the existing Metal shadow worker (`metal-shadow/v0`). Written before trusting live Metal results.

Width and height equality is `conditioned_on_reference`: the worker receives the original observed dimensions via `configure_window`, so a match proves preservation, not equivalent SDL discovery.

## Strict gates (`contract_pass` requires `matched`)

| Field | Rule |
| --- | --- |
| Settings `byte_10` | Exact equality |
| Settings `span` | Exact equality |
| Settings `constant` | Exact equality |
| Init success | Both sides equal 1 |
| Initialized structure | Original `master_non_null` and Metal `state.initialized` + `backend_present` |
| Width, height | Exact equality, tagged `conditioned_on_reference` |
| Init output `source` | Exact equality (input `source=0` must remain 0 in the fixture so Metal’s zero→1 defaulting is exercised) |
| Primitive | Exact equality |
| Viewport (6) | Exact equality |
| Dirty | Exact equality |
| Metal MSAA selection | For each sample, Metal `flags[i]` equals the expected flag from `device_supports[i]` and the threshold-doubling logic in `eu4_gfx_init.mm` / `flags_for` |

## Outside the strict gate

| Field | Classification |
| --- | --- |
| Settings `return` | Preserve original rax int64 and Metal int; `abi_unresolved` until callers prove a meaningful return |
| Anisotropy | Diagnostic / capability path |
| GL sample flag vs Metal flag | `matched` or `capability_difference` (separate from the strict algorithm check) |
| `texture_lod` | `capability_difference` with unresolved semantic impact (original often 1 from GLEW; Metal sets 0) |
| `max_texture` | Capability classification; Metal value must still be present and usable |
| Default blend / depth / rasterizer | Presence vs Metal snap existence only; snap bytes `not_comparable` |
| GL context vs Metal backend | Structural both-present only |

## Overall result

`contract_pass` only when every strict gate is `matched`, Metal init succeeded, reference provenance checks passed, and every compared worker response passed envelope validation.

Capability, `not_comparable`, `abi_unresolved`, and diagnostic differences remain visible and do not alone fail the contract.
