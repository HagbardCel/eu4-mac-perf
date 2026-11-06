# gfx-startup differential — first Metal replay

Committed reference: [`reference.json`](reference.json) (`gfx-startup-ref/v0`), normalized from local `results/20261105T225108Z-gfx-startup/` with hashes matching [`note.md`](note.md). Init input `source` preserved as `0`.

Local raw run (not committed): `results/20261106T085557Z-gfx-startup-diff/`

| Result | Value |
| --- | --- |
| `contract_pass` | `true` |
| Metal init | succeeded; fixture `source=0` → Metal master `source=1` |
| Strict gates | all `matched` (including MSAA algorithm vs `device_supports`) |
| Settings `return` | `abi_unresolved` (rax int64 vs worker int) |
| Anisotropy | `capability_difference` |
| `texture_lod` | `capability_difference` (original `1`, Metal `0`; unresolved semantic impact) |
| Default blend/depth/raster snaps | `not_comparable` |
| GL vs Metal MSAA flags | all `matched` on this device |

Width/height equality is `conditioned_on_reference`. This note does not claim full OpenGL/Metal equivalence.
