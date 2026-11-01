# Semantic reading of the frozen `_Gfx*` surface

This tree is a reading of the pinned GOG EU IV 1.37.5 backend. It is the artifact of [decision 0001](../../../../docs/decisions/0001-semantic-gfx-then-metal.md). It is not a compilable replacement and not an equivalence test.

## Provenance

| Fact | Value |
| --- | --- |
| Binary SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Baseline | `auto-v1-20261013` |
| Inventory | [renderer-functions.tsv](../../../../analysis/renderer-corpus/scope/v1/renderer-functions.tsv), the 92 rows whose name starts with `_Gfx` |
| Packages | `gfx-batch-00042.zip`, `gfx-batch-00043.zip`, `gfx-batch-00044.zip` |

Those three packages contain every one of the 92 functions. Before the dossiers were written they were loaded with `load_frozen_context`, `load_inventory_index`, and `load_callsites_by_caller`, then checked with `read_zip_package` and `validate_batch_package`. The packages are gitignored under `.local/`. This tree does not copy them.

`static-verified` means a decoded instruction or another qualified static record. `inferred` means a reading of the decompilation or of the surrounding calls. `runtime-observed` means an existing execution record. `unknown` means the static body does not establish it. A store in the instruction stream is not proof the game takes that path.

## How to read a dossier

Identity, batch, and the two evidence statuses are the header. Behavior is the reading. `semantic_status` is `reconstructed`, `partial`, or `blocked`. `reconstructed` means the function's material behavior, state changes, and relevant control-flow branches are understood at the semantic level. Unresolved ABI details stay written down and may still have to be resolved before an executable replacement. A material uncertainty in the behavior itself is `partial`. Successful decompilation alone is not reconstruction. A file existing does not make the status `reconstructed`. `migration_role` is provisional:

- `retain-original`: no demonstrated need to replace the body, and only while its data layout and calling contract stay valid.
- `replace-metal`: the body performs a graphics or presentation effect the Metal backend has to provide.
- `undetermined`: the static body does not decide.

No direct GL call is not proof of GL independence.

## Map

- [inventory.md](inventory.md) counts the three statuses.
- [architecture.md](architecture.md) is the synthesis, including what `_GfxInit` must leave behind.
- [types.md](types.md) is partial layouts only.
- [dependencies.md](dependencies.md) is the callee list outside the 92.
- [unknowns.md](unknowns.md) splits blockers from the rest.

The existing [_GfxSetConstantBuffer](../../_GfxSetConstantBuffer/contract.md) contract and [_GfxForceWireframe](../../_GfxForceWireframe/contract.md) contract stay the reviewed ABI for those two functions.

## Acceptance

The mechanical test checks identity against the frozen TSV, required header fields, the six-way partition, and a fence guard. The guard rejects fenced decompiler or instruction dumps. It does not prove that a sentence was written independently or that a claim is faithful. Those are review checks. There is no percentage gate.

## Subsystems

| Directory | Functions |
| --- | --- |
| [init](init/) | 27 |
| [buffers](buffers/) | 14 |
| [shaders](shaders/) | 12 |
| [textures](textures/) | 19 |
| [state](state/) | 15 |
| [draw](draw/) | 5 |
