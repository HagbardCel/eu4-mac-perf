# WP6 — REFERENCE CPU-path decomposition

**Status:** planned (branch `wp6-reference-cpu-decomposition`).  
**Prerequisite:** [WP5b completion diagnosis](wp5b-completion-diagnosis.md) merged (archive `f7cbf346` / `6e5d53c`).

## Problem statement

After WP5 (reference fast path) and WP5b (submission vs `glFinish` drain):

| Recipe | WP5b takeaway (vs bare) |
|--------|-------------------------|
| **mesh / borders** | Submission-window wall gap largely collapses after drain; **bare → REFERENCE thread CPU** remains. |
| **text_ui** | Noisier on wall time; **REFERENCE CPU anomaly** still clear. |

Tier-1 still fails on `reference_cpu_ns` and the counters↔REFERENCE pairing on borders/text_ui. WP5b ruled out “REFERENCE is only slow because GL work finishes after the submission window” as the **sole** explanation for mesh/borders. The next question is **which profiler operations still run in REFERENCE** and how much of the bare → REFERENCE CPU delta they explain.

## Goal

Decompose **REFERENCE-mode thread CPU** on training recipes (`mesh`, `borders`, `text_ui`) into attributable components, **without** changing Tier-1 acceptance gates, held-out fixture, or live EU IV calibration.

**Success criteria (diagnostic, not admission):**

1. A capture documents per-component CPU deltas (REFERENCE vs a declared baseline stage) with the same seven-pair / recipe provenance as other offline evidence.
2. At least one component accounts for a **material** share of the WP5b `reference_cpu_ns` gap vs bare on mesh or borders.
3. Results yield a **ranked** list of REFERENCE-only hooks to optimize in a follow-up implementation WP (not part of WP6).

## Non-goals

- Held-out re-run or policy/threshold changes.
- `run --calibration-only` / live Tier 2.
- Rewriting historical archives (`f7cbf346`, WP1 `9a063f0`, post-WP5 requalification `37f57796`).
- Another completion-timing (`glFinish`) sweep unless decomposition implicates async boundaries.

## Hypothesis space (initial)

REFERENCE already skips GL shadow, forensic GPU prep, and the heavy render wrapper ([WP5](wp5-reference-fast-path.md)). Residual CPU likely includes:

| Area | Where to look |
|------|----------------|
| Scope / frame accounting | `scope_begin` / `scope_end` on UPDATE, LOOP, RENDER, PRESENT; `measurement_active()` bookkeeping |
| Event / timestamp path | `timestamp_event`, `event()`, CSV writer on hot hooks |
| Control refresh | `refresh_unowned_control`, mode transitions, `invalidate_shadow_cache_quiet` on REFERENCE entry/exit |
| Context hooks | REFERENCE `CGLSetCurrentContext` path (`context_stamp` without shadow save/seed) |
| Detour baseline | Dylib present with `MODE_REFERENCE` vs true bare (dylib absent) — quantify “REFERENCE instrumentation tax” |

WP1 A–F matrix isolated **counters-stage** forensic features; WP6 targets **REFERENCE-specific** paths that remain after WP5.

## Proposed approach

### Phase A — Inventory (code, no new capture)

1. Enumerate every hook/callback that runs when `frame_control.mode == REFERENCE` and `measurement_active()`.
2. Map each to an existing test knob (`EU4_TEST_*`, `test_ablation`, sampled stages) or flag a **new** ablation bit.
3. Document expected vs measured attribution gaps (e.g. shared `event()` path used by multiple hooks).

### Phase B — Harness / profiler ablations

Extend offline harness pattern (same as `ablation_accounting` / `ablation_writer` / `ablation_preparation` on sampled path):

- Add **REFERENCE-scoped** ablation env values (names TBD) that disable one subsystem at a time while keeping Tier-1 stage identity `reference`.
- Optional: lightweight per-scope CPU counters in harness stdout for REFERENCE trials (diagnostic only).

**Constraints:**

- Default workload harness and Tier-1 `reference` stage behavior unchanged when ablation env unset.
- Ablations must not silently change `counters` or `bare` stages.

### Phase C — Offline capture

New subcommand (working name):

```bash
python3 benchmark/eu4_frame_model.py reference-cpu-decomposition --registry-json
```

| Field | Value |
|-------|--------|
| Recipes | `mesh`, `borders`, `text_ui` (training only) |
| Trials | Seven paired (match Tier-1) |
| Stages | `bare`, `reference` (baseline), plus one stage per ablation variant **or** matrix interleave per trial |
| Purpose string | `wp6_reference_cpu_decomposition_v1` (constant in `eu4_frame_model.py`) |
| Acceptance | **None** — comparisons `status: diagnostic`, `acceptance_gate: false` |

Reuse `_offline_workloads_payload` / `build_offline_evidence_metadata` with a new preflight key (e.g. `reference_cpu_workloads`) **or** extend representative payload with `reference_cpu_decomposition: true` — decide in implementation to avoid a third metadata hole.

### Phase D — Analysis memo

Short memo (like [WP5 requalification timing memo](wp5-requalification-timing-memo-20261003.md)):

- Table: component → median CPU delta (REFERENCE vs ablated REFERENCE) per recipe.
- Explicit link to WP5b: components that **do not** explain submission-window wall time but **do** explain CPU.
- Recommended WP7 implementation target(s).

## Branch / issue checklist

- [ ] Branch: `wp6-reference-cpu-decomposition` from `main` @ post–PR #10 merge (`602ca4b`).
- [ ] Phase A inventory PR (docs + comments only acceptable).
- [ ] Phase B ablation hooks + unit tests (portable).
- [ ] Phase C capture + provenance tests (mirror WP5b patterns).
- [ ] Mac capture → immutable evidence + manifest registration.
- [ ] Phase D memo; no Tier-1 gate changes.

## Related artifacts

| Archive / doc | Role |
|---------------|------|
| `f7cbf346` | WP5b completion timing (historical metadata caveats documented) |
| `37f57796` | Post-WP5 Tier-1 requalification |
| `9a063f0` | WP1 v3 A–F diagnostic |
| [profiler-overhead-diagnosis-plan.md](profiler-overhead-diagnosis-plan.md) | Parent program |
