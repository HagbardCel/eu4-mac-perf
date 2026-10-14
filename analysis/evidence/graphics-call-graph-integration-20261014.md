# Graphics call-graph integration (2026-10-14)

Full-inventory run on pinned GOG export `auto-v1-20261013` after PR #50 review remediation (Mach-O demangle, draw-endpoint coverage, unassessed-draw safeguards, backend-family scoping, interception counting).

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

## Gfx draw-site reconciliation (48 LLVM direct-call sites)

From `call-graph-audit.json` → `gfx_draw_reconcile`:

| Bucket | Count |
|--------|------:|
| `draw_callers_count` | 48 |
| `matched` | 0 |
| `target_mismatch` | 0 |
| `missing_callsite` | 0 |
| `unresolved` | 0 |
| `owner_mismatch` | 48 |

All 48 sites are labeled `owner_mismatch` because `draw-callers.json` records LLVM Itanium `owner_symbol` strings while Ghidra inventory uses short `name_mangled` plus `namespace` (for example `RenderGuiBoundsDebug` vs `__ZN9CEU3Idler20RenderGuiBoundsDebugEv`). Each site still resolves the expected `_GfxDraw*` helper RVA at the callsite; this is a naming reconciliation artifact, not evidence of wrong call targets in the recovered graph. No `target_mismatch` or `missing_callsite` entries were observed on this export.

## Boundary verdicts

| Verdict | Value |
|---------|-------|
| `structural_boundary_verdict` | `insufficient_evidence` |
| `overall_boundary_verdict` | `insufficient_evidence` |

Overall remains `insufficient_evidence` because Gfx cuts for `state`, `buffers_textures`, `render_targets`, and `presentation` are empty (`not_characterized`).

### Draw family (`family_verdicts.draw`)

Draw-cut members: `_GfxDraw`, `_GfxDrawIndexed`, `_GfxDrawInstanced`, `_GfxDrawIndexedInstanced`, `_GfxDraw2dLines`.

| Field | Value |
|-------|-------|
| `verdict` | `candidate_boundary_supported` |
| `intercepted_root_endpoint_pair_count` | 3 |
| `distinct_intercepted_endpoint_count` | 2 |
| `distinct_intercepted_endpoint_symbols` | `_glDrawArrays`, `_glDrawElements` |
| `has_indeterminate_survivors` | `false` |
| `status_counts` | 3× `intercepted_in_recovered_graph`, 1× `not_observed_before` |
| `unassessed_draw_candidate_count` | (none observed on this export) |

Interception counts are per **(renderer root, OpenGL endpoint)** pair; distinct symbols are listed separately.

Evidence path: `cuts.gfx_abstraction.family_coverage.draw.endpoint_coverage` (not overall cut alone).

### Backend shaders family (`cuts.backend_implementation.family_verdicts.shaders`)

| Field | Value |
|-------|-------|
| `verdict` | `insufficient_evidence` |
| `note` | no explicit endpoint classification for backend family `shaders` |

Backend verdicts are scoped to explicitly classified endpoints in each backend family; draw bypasses do not affect the shader cut conclusion until shader/program GL imports are classified.

### Witness semantics (`_glDrawElements`, draw-family cut)

Status: `intercepted_in_recovered_graph` — endpoint reachable before removing draw-cut members; **no** recovered path after cut (`witness_after_cut` absent).

**Before cut** (root `CEU3GraphicalMap::Render` @ `0xed4ac8`):

| Step | Node | Call site | Resolution |
|------|------|-----------|------------|
| 1 | `0xed4ac8` Render | — | — |
| 2 | `0xe11136` RenderNames | `0xed4df0` | direct_internal |
| 3 | `0x15eaa77` _GfxDrawIndexed | `0xe11647` | direct_internal |
| 4 | `ext:_glDrawElements` | `0x15eac6a` | thunk (collapsed) |

**After cut:** no recovered path to `_glDrawElements` (Gfx draw helpers removed from traversal).

## Mandatory seed resolution

| Seed (Itanium) | Resolved RVA |
|----------------|--------------|
| `__ZN13SShaderOpenGL6SetAllEv` | `0x15ebc40` |
| `_ZN14CPdxMeshObject13RenderBuckets…` | `0x14c7da4` |
| `__ZN16CEU3GraphicalMap6Render…` | `0xed4ac8` |

## Top 10 decompile priorities

| Score | RVA | Symbol (inventory) | Reasons |
|------:|-----|-------------------|---------|
| 15 | `0x10cbb00` | DrawBorders | measured_hot_path, s_core_intersection |
| 15 | `0x14c7da4` | RenderBuckets | measured_hot_path, s_core_intersection |
| 10 | `0x15ea789` | _GfxDraw | gfx_abstraction_cut_member, s_core_intersection |
| 10 | `0x15eaa77` | _GfxDrawIndexed | gfx_abstraction_cut_member, s_core_intersection |
| 10 | `0x15eac96` | _GfxDrawInstanced | gfx_abstraction_cut_member |
| 10 | `0x15eac9c` | _GfxDrawIndexedInstanced | gfx_abstraction_cut_member |
| 10 | `0x15ead87` | _GfxDraw2dLines | gfx_abstraction_cut_member |
| 8 | `0x15ebc40` | SetAll | backend_implementation_cut_member |
| 5 | `0x0e11136` | RenderNames | s_core_intersection |
| 5 | `0x0ed48aa` | RenderShadows | s_core_intersection |

## Code changes (this remediation)

- [`reverse_engineering/symbol_resolve.py`](../../reverse_engineering/symbol_resolve.py): `mach_o_demangle_input`, GNU `c++filt` compatibility.
- [`reverse_engineering/gfx_subgraph.py`](../../reverse_engineering/gfx_subgraph.py): draw-scope unassessed endpoints, verdict precedence, `has_indeterminate_survivors`.
- [`reverse_engineering/graph_analysis.py`](../../reverse_engineering/graph_analysis.py): witness path rendering in human report.
