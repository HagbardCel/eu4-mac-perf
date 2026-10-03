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

WP5b `cpu_ns` is **measured-thread** CPU (`CLOCK_THREAD_CPUTIME_ID` on the harness main thread around four frames). Decomposition must stay consistent with that metric.

## Goal

Decompose **REFERENCE-mode measured-thread CPU** on training recipes (`mesh`, `borders`, `text_ui`) into well-defined layers, **without** changing Tier-1 acceptance gates, held-out fixture, or live EU IV calibration.

**Success criteria (diagnostic, not admission):**

1. A capture documents the stage ladder below (including `loaded-disabled` and a **minimal-REFERENCE** reconciliation stage) with seven-pair / recipe provenance matching other offline evidence.
2. Report **fixed instrumentation tax**, **active REFERENCE tax**, **marginal** per-component effects, and **unexplained residual** (see [Interpretation](#interpretation)) for mesh or borders.
3. Results yield a **ranked** list of REFERENCE-only hooks to optimize in a follow-up implementation WP (not part of WP6). Ranking uses marginal effects; reconciliation uses minimal-REFERENCE vs `loaded-disabled`.

## Non-goals

- Held-out re-run or policy/threshold changes.
- `run --calibration-only` / live Tier 2.
- Rewriting historical archives (`f7cbf346`, WP1 `9a063f0`, post-WP5 requalification `37f57796`).
- Another completion-timing (`glFinish`) sweep unless decomposition implicates async boundaries.
- Attributing **writer-thread** CSV formatting CPU to WP5b `cpu_ns` (out of scope for the current harness metric).

## Capture stages (four concepts)

Phase C must include all of these; one-at-a-time ablations alone cannot reconcile bare → REFERENCE.

| Stage | Meaning |
|-------|---------|
| **bare** | No profiler dylib; true uninstrumented baseline. |
| **loaded-disabled** | Profiler dylib and interpositions present; measurement **disabled** (control off or equivalent). |
| **reference** | Current production REFERENCE (Tier-1 stage identity unchanged when ablation env unset). |
| **reference ablations** | REFERENCE with **one** subsystem disabled at a time (marginal effects). |
| **minimal-reference** | REFERENCE with **all** REFERENCE accounting paths disabled that WP6 intends to attribute (single reconciliation stage), or an explicit cumulative-disable sequence documented in the memo. |

Implementation names for `loaded-disabled` and `minimal-reference` are TBD in Phase B; they are first-class capture stages, not informal comparisons.

### Interpretation

Define medians on `cpu_ns` (and optionally `elapsed_ns` for context):

```text
fixed instrumentation tax     = loaded-disabled − bare
active REFERENCE tax            = reference − loaded-disabled
marginal component effect       = reference − reference_without_<component>
unexplained residual            = minimal-reference − loaded-disabled
```

**Marginal ablation effects are not assumed additive.** Paths overlap (e.g. `scope_begin()` calls `measurement_active()`, which may refresh control). Summing marginal deltas may exceed or under-shoot the active REFERENCE tax. Use **minimal-reference** (or cumulative disables) to bound what REFERENCE accounting still costs above dylib presence alone.

The WP5b gap **bare → reference** decomposes as:

```text
(reference − bare) = (loaded-disabled − bare) + (reference − loaded-disabled)
```

Component ablations explain **slices of** `(reference − loaded-disabled)`; minimal-reference explains **how much of that slice remains** after disabling the attributed stack together.

## Hypothesis space (measured-thread, initial)

REFERENCE already skips GL shadow, forensic GPU prep, and the heavy render wrapper ([WP5](wp5-reference-fast-path.md)). Residual **measured-thread** CPU in the synthetic harness likely includes:

| Area | Where to look |
|------|----------------|
| Scope / frame accounting | `scope_begin` / `scope_end` on UPDATE, LOOP, RENDER, PRESENT; nested `measurement_active()` |
| Hot-thread event path | `event()`, producer lookup, counter aggregation, `flush_counters()`, SPSC reservation/publication, frame copying |
| Timestamp guards | `timestamp_event()` — mostly no-op when forensic detail is inactive, but still pays checks and another `measurement_active()` |
| Control refresh | `refresh_unowned_control`, `snapshot_control`, mode transitions; `invalidate_shadow_cache_quiet` on REFERENCE entry/exit (mostly outside the four-frame window unless transitions occur there) |
| Clock calls | `clock_gettime(CLOCK_UPTIME_RAW)` and `clock_gettime(CLOCK_THREAD_CPUTIME_ID)` inside scopes and hooks |

WP1 A–F matrix isolated **counters-stage** forensic features; WP6 targets **REFERENCE-specific** paths that remain after WP5.

### Not active in current Tier-1 synthetic measurement

Retain in the **live-path inventory** (Phase A), but do not expect these to explain WP5b `cpu_ns` on mesh/borders/text_ui unless the harness or metric changes:

| Item | Why |
|------|-----|
| **`CGLSetCurrentContext` / `context_stamp()`** | Harness sets context before warm-up/`glFinish`, starts the CPU timer, runs four frames, then clears context. No context switch **inside** the measured window. |
| **CSV writer formatting / I/O** | Runs on the profiler writer thread; not included in harness `CLOCK_THREAD_CPUTIME_ID` on the main thread. Main thread still pays queueing/publication toward the writer. |

## Proposed approach

### Phase A — Inventory (code, no new capture)

1. Enumerate every hook/callback that runs when `frame_control.mode == REFERENCE` and `measurement_active()` during the **four measured frames**.
2. Map each to an existing test knob (`EU4_TEST_*`, `test_ablation`, sampled stages) or flag a **new** ablation bit; design **`loaded-disabled`** and **`minimal-reference`** stage semantics.
3. Document overlap between ablation targets (shared `event()`, nested `measurement_active()`).
4. Build a **per-measured-frame call-count model** (static or from lightweight tracing) for at least:

   ```text
   measurement_active()
   refresh_unowned_control()
   clock_gettime(CLOCK_UPTIME_RAW)
   clock_gettime(CLOCK_THREAD_CPUTIME_ID)
   scope_begin / scope_end
   event()
   publish_frame()   (or equivalent frame publication)
   snapshot_control()
   ```

   This may identify dominant cost before any ablation lands.

### Phase B — Harness / profiler ablations

Extend offline harness pattern (same spirit as `ablation_accounting` / `ablation_writer` / `ablation_preparation` on the sampled path):

- **`loaded-disabled`** stage: dylib loaded, measurement off — separates interposition tax from active REFERENCE work.
- **REFERENCE-scoped** ablation env values: disable one subsystem at a time while keeping Tier-1 `reference` identity when env unset.
- **`minimal-reference`** stage: disable the full attributed REFERENCE accounting stack for reconciliation.

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
| Stages | `bare`, `loaded-disabled`, `reference`, `minimal-reference`, plus one stage per marginal ablation variant (matrix interleave per trial if needed) |
| Purpose string | `wp6_reference_cpu_decomposition_v1` (constant in `eu4_frame_model.py`) |
| Acceptance | **None** — comparisons `status: diagnostic`, `acceptance_gate: false` |

Reuse `_offline_workloads_payload` / `build_offline_evidence_metadata` with a new preflight key (e.g. `reference_cpu_workloads`) **or** extend representative payload with `reference_cpu_decomposition: true` — decide in implementation to avoid a third metadata hole.

### Phase D — Analysis memo

Short memo (like [WP5 requalification timing memo](wp5-requalification-timing-memo-20261003.md)):

- Table: fixed tax, active REFERENCE tax, marginal component deltas, minimal-reference residual — per recipe.
- Explicit note: marginals are not summed as a reconciliation; residual line uses minimal-reference.
- Link to WP5b: components that **do not** explain submission-window wall time but **do** explain measured-thread CPU.
- Recommended WP7 implementation target(s).

## Branch / issue checklist

- [x] Branch: `wp6-reference-cpu-decomposition` from `main` @ post–PR #10 merge (`602ca4b`).
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
