# Border loop-head feasibility (offline charter)

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`)  
**Hook candidate:** `0x1010cbe55` (`testb %r13b, 0x1(%rcx)`)

## Purpose

Close static Gates 2/3a/3b design for **prefix-batch** loop-head mutation before any Venice launch. This PR does **not** authorize mutation (`mutation_authorized: false`).

## Evidence (mechanical)

Verdict derived from [tools/border_loop_evidence.py](tools/border_loop_evidence.py):

```text
any static_criteria == FAIL  → NO_GO
all static_criteria == PASS  → GO_CONDITIONAL_RUNTIME_GATES
otherwise                    → PENDING_STATIC_RE
```

Authoritative payload: [evidence/border-loop-head-feasibility-20261004.json](evidence/border-loop-head-feasibility-20261004.json).  
Tests: `tests/test_border_loop_evidence.py`, `tests/test_border_loop_classifier.py`.

## Numeric contract (no invented histogram)

| Quantity | Value |
|----------|--------|
| `structural_upper_bound_eliminations_per_frame` | ~1562 |
| `structural_upper_bound_eliminations_per_s_at_55Hz_scenario` | ~85910 |
| `semantic_eliminations_per_frame` | **null** |
| `implementable_eliminations_per_frame` | **null** |
| `eligible_run_length_histogram` | **null** |

**Criterion 2:** control-flow/data-layout **permits** eligible prefix length ≥2; frequency → `roi_gate` only.

## Static criteria map

| Key | Topic |
|-----|--------|
| `1_classifier_inputs` | `%r13b`, walk order, IBO identity/argument |
| `2_control_flow_prefix_realizability` | Structural prefix ≥2 possible |
| `3_entry_state` | ONE_BIND entry model |
| `4_fail_closed_paths` | SKIP-before-deref, OOB, unsupported mode |
| `5_helper_side_effect_equivalence` | GfxDrawIndexed + GfxSetIndexBuffer audit |
| `6_cpu_continuation` | CFG post-state equivalence (A/B, RFLAGS, stack) — [gate3b](border-loop-head-gate3b-state.md), [disasm](evidence/drawborders-drawborders-fn-disasm.txt), [hook-site JSON](border-hook-site.json) |
| `7_engine_gl_equivalence` | ONE_BIND + `glMultiDrawElements` (mode-0 ≡ N×`glDrawElements`) |
| `8_continuation_mechanics` | Non-recursive trampoline + WALK_END |
| `9_detour_relocation` | 14-byte RIP-indirect jmp (static encoding) |
| `10_uncertainty_falls_through` | Classifier + patch fail-closed |

`GO_CONDITIONAL_RUNTIME_GATES` means criteria **1–10 PASS**; `runtime_gate_0` and ROI remain pending. Unconditional `GO` is not issued in this artifact.

## Related artifacts

| Doc | Role |
|-----|------|
| [border-loop-head-data-model.md](border-loop-head-data-model.md) | LoopContext / walk |
| [border-loop-head-classifier.md](border-loop-head-classifier.md) | Prefix classifier |
| [border-gfxdrawindexed-helper-audit.md](border-gfxdrawindexed-helper-audit.md) | Gate 3a |
| [border-loop-head-gate3b-state.md](border-loop-head-gate3b-state.md) | Gate 3b |
| [border-loop-head-patch-plan.md](border-loop-head-patch-plan.md) | Detour design |
| [border-hook-site.json](border-hook-site.json) | Bytes + continuation addresses |

## Future launch: N–A–B–A–B–A–N

See prior charter: phase-local `eligible_*` vs `actual_*` invariant; rates not raw cross-phase counts; live ROI uses measured seconds.
