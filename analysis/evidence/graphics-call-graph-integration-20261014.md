# Graphics call-graph integration (2026-10-14)

Full-inventory run on pinned GOG binary corpus `auto-v1-20261013` after seed resolution fix for Ghidra short `name_mangled` / `name_demangled` rows.

## Commands

```bash
python3 -m reverse_engineering.graph_analysis audit --baseline-revision auto-v1-20261013
python3 -m reverse_engineering.graph_analysis extract --baseline-revision auto-v1-20261013 --write-report
```

## Provenance

| Field | Value |
|-------|-------|
| `baseline_revision` | `auto-v1-20261013` |
| `binary_sha256` | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| `inventory_provenance.digest_match` | `true` |
| `authoritative` | `true` |
| `unknown_target_risk` | `false` |

## Boundary verdicts

| Verdict | Value |
|---------|-------|
| `structural_boundary_verdict` | `insufficient_evidence` |
| `overall_boundary_verdict` | `insufficient_evidence` |

`overall` stays `insufficient_evidence` because only the **draw** Gfx family has cut members; `state`, `buffers_textures`, `render_targets`, and `presentation` cuts are empty (`not_characterized`).

### Per-family (`family_verdicts`)

| Family | Verdict | Notes |
|--------|---------|-------|
| draw | `candidate_boundary_supported` | 3 endpoints `intercepted_in_recovered_graph`, 1 `not_observed_before`; `qualified: false` |
| state | `not_characterized` | empty cut |
| buffers_textures | `not_characterized` | empty cut |
| render_targets | `not_characterized` | empty cut |
| presentation | `not_characterized` | empty cut |

## Mandatory seed resolution (inventory)

| Seed (Itanium) | Resolved RVA |
|----------------|--------------|
| `__ZN13SShaderOpenGL6SetAllEv` | `0x15ebc40` |
| `_ZN14CPdxMeshObject13RenderBuckets…` | `0x14c7da4` |
| `__ZN16CEU3GraphicalMap6Render…` | `0xed4ac8` |

Matches [PR #48 anchor RVAs](pr48-ghidra-corpus-integration-20261013.md).

## Sample Gfx cut witnesses (draw family, overall cut)

One intercepted OpenGL draw endpoint (`_glDrawElements`):

- **Before cut:** path includes renderer subgraph nodes from `CEU3GraphicalMap::Render` toward `_GfxDrawIndexed` and the GL import.
- **After cut:** witness shows routing through `_GfxDrawIndexed` (Gfx abstraction member) rather than a direct bypass from game code to `_glDrawElements` in the recovered static graph.

(Detailed step lists live in local `graphics-subgraph.json` under `cuts.gfx_abstraction.overall_gfx.endpoint_coverage`; not copied verbatim here.)

## Top 10 decompile priorities

| Score | RVA | Symbol (inventory) | Reasons |
|------:|-----|-------------------|---------|
| 15 | `0x10cbb00` | `DrawBorders` | measured_hot_path, s_core_intersection |
| 15 | `0x14c7da4` | `RenderBuckets` | measured_hot_path, s_core_intersection |
| 10 | `0x15ea789` | `_GfxDraw` | gfx_abstraction_cut_member, s_core_intersection |
| 10 | `0x15eaa77` | `_GfxDrawIndexed` | gfx_abstraction_cut_member, s_core_intersection |
| 10 | `0x15eac96` | `_GfxDrawInstanced` | gfx_abstraction_cut_member |
| 10 | `0x15eac9c` | `_GfxDrawIndexedInstanced` | gfx_abstraction_cut_member |
| 10 | `0x15ead87` | `_GfxDraw2dLines` | gfx_abstraction_cut_member |
| 8 | `0x15ebc40` | `SetAll` | backend_implementation_cut_member |
| 5 | `0x0e11136` | `RenderNames` | s_core_intersection |
| 5 | `0x0ed48aa` | `RenderShadows` | s_core_intersection |

## Code change

[`reverse_engineering/symbol_resolve.py`](../../reverse_engineering/symbol_resolve.py): resolve seeds by exact `name_mangled`, inventory demangled/namespace aliases, and `c++filt` for Itanium hook strings (`__Z` / `_Z`).
