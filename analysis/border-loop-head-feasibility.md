# Border loop-head feasibility (offline charter)

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`)  
**Hook candidate:** `0x1010cbe55` (`testb %r13b, 0x1(%rcx)`)

## Purpose

Close static Gates 2/3a/3b design for **prefix-batch** loop-head mutation before any Venice launch. This PR does **not** authorize mutation (`mutation_authorized: false`).

## Related artifacts

| Doc | Role |
|-----|------|
| [border-loop-head-data-model.md](border-loop-head-data-model.md) | LoopContext / RecordView / index-table entries |
| [border-loop-head-classifier.md](border-loop-head-classifier.md) | Prefix classifier + barrier masks |
| [border-gfxdrawindexed-helper-audit.md](border-gfxdrawindexed-helper-audit.md) | Gate 3a helper equivalence |
| [border-loop-head-gate3b-state.md](border-loop-head-gate3b-state.md) | Continuation + CPU/GL state |
| [border-loop-head-patch-plan.md](border-loop-head-patch-plan.md) | Detour / non-recursive trampoline |
| [evidence/border-loop-head-feasibility-20261004.json](evidence/border-loop-head-feasibility-20261004.json) | Static verdict |

## Numeric contract (no invented histogram)

| Quantity | Value |
|----------|--------|
| `structural_upper_bound_eliminations_per_frame` | ~1562 |
| `structural_upper_bound_eliminations_per_s_at_55Hz_scenario` | ~85910 |
| `semantic_eliminations_per_frame` | **null / unresolved** |
| `implementable_eliminations_per_frame` | **null / unresolved** |
| `eligible_run_length_histogram` | **null / unavailable** |

**~943:** descriptive structural run shape only ([border-draw-inner-loop-re.md](border-draw-inner-loop-re.md)); not an implementable batch length.

Derivation (when record stream exists): repeated `classify_batchable_prefix` at each hook; \(E = \sum \max(0, n_i - 1)\) over prefixes with \(n_i \ge 2\).

## Static GO criteria (ex ante)

Static **GO** requires all of:

1. Classifier inputs (incl. `%r13b`, per-index `%rcx`) have proven storage/access paths.
2. Semantically realizable prefix length \(\ge 2\) exists structurally (frequency unknown).
3. Prefix homogeneity **and** `entry_state_matches_batch_key` for record 0, or v1 fall-through one original record.
4. Skip / unsupported paths fail closed (execute normally).
5. `GfxDrawIndexed` audit: \(N \times\) helper \(\equiv\) 1× multidraw GL path or reproduce side effects.
6. CPU continuation specified (GPRs, stack, RFLAGS, XMM, callee-saved).
7. Engine/GL continuation equivalent for batchable subset.
8. Non-recursive continuation into first-unconsumed record path.
9. Detour mechanically feasible.
10. Uncertainty → original loop.

**`GO` (unconditional) is not an expected outcome of this offline PR** while Gate 0 remains PENDING in the active gameplay context.

## Verdict dimensions (orthogonal)

| Dimension | This PR |
|-----------|---------|
| Static feasibility | `GO_CONDITIONAL_RUNTIME_GATES` \| `NO-GO` |
| Runtime Gate 0 | `PENDING` |
| ROI | `PENDING_NUMERIC_EVIDENCE` |
| Mutation authorized | `NO` |

## Future single launch: N–A–B–A–B–A–N

| Phase | Role |
|-------|------|
| **N** | Minimal loop-head detour / pass-through (infrastructure) |
| **A** | Full classifier, mutation off → `eligible_*` opportunity |
| **B** | Same classifier + mutation → `eligible_*` (same accounting as A) plus `actual_multidraw_calls` / `actual_draw_calls_eliminated` |

**Hard invariant (phase-local):** \(E^{B}_{actual} \le E^{B}_{eligible}\). Do **not** compare raw B eliminations to paired A counts (cadence differs).

**Rates (consistency):** \(E_A / frames_A\) vs \(E^{B}_{eligible} / frames_B\).

**Interpretation:**

- `A-N` = incremental classifier cost over minimal detour
- `B-A` = mutation effect conditional on classifier
- `B-N` = net candidate vs minimal-detour control (**not** untouched binary)

Live ROI thresholds (85k / 40k) use **measured phase seconds**, not the static 55 Hz scenario.

## Classifier implementation

Required: [tools/border_loop_classifier.py](tools/border_loop_classifier.py) + `tests/test_border_loop_classifier.py` (synthetic only; no length 943 in fixtures).
