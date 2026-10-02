# Stage 2 — Tier-1 admission + offline gate schema (implementation plan)

**Scope:** [profiler-roadmap.md](profiler-roadmap.md) Stage 2 (not the unrelated “Stage 2 sampler-uniform” track in [plan.md](../plan.md)).

**Prerequisites (done or in flight):**

- Stage 1 merged: immutable archives, pointer, Mac envelope at source **`50643ce`** ([PR #2](https://github.com/HagbardCel/eu4-mac-perf/pull/2)).
- Frozen historical failures: `analysis/evidence/frame-model-offline-legacy-373f3ff-20261001T204509Z.json` + canonical `…50643ce-20261002T180048.506022Z-549ac568.json`.
- **Checkpoint decision** (Fabian): demote forensic to non-blocking for calibration-only *or* fix forensic first — record in project notes before locking Tier-1 numbers.

**Success criterion (roadmap):** Split offline gates; no-peeking held-out validation executed. **Outcome (2026-10-02):** Tier-1 v2 **failed** — [stage-2-validation-outcome.md](stage-2-validation-outcome.md). Stage 3 blocked.

---

## 1. Problem statement

Today a single monolithic flag drives blocking behavior:

```text
representative_workloads.status  (all six 3%/5% gates on mesh/borders/text_ui)
        ↓
preflight.status / overhead_gate
        ↓
BenchmarkError → blocks all live work
        ↓
manifest gate "offline_overhead"
```

Stage 1 preserved **raw trials** so a new rule can be applied without rewriting history. Stage 2 must:

1. **Split** causal admission vs forensic suitability.
2. Replace ad-hoc 3%/5% tuning with an **a priori Tier-1 error budget** (relative + absolute per frame).
3. **Validate** on fresh seven-pair runs **and** a **held-out recipe** chosen and hash-frozen **before** its first measurement.
4. Wire **report-kind-specific `REQUIRED` gates** in [frame_model_gates.py](../benchmark/frame_model_gates.py) (calibration-only vs residual vs full causal).

---

## 2. Policy work (do first — no code peeking)

| Step | Output | Owner |
|------|--------|--------|
| 2.1 | Written **Tier-1 policy document** (relative cap, absolute µs/frame cap, bootstrap rule) derived from error budget, **not** from mesh/borders/text_ui medians | Human + agent review |
| 2.2 | New **`tier1_causal_policy_version`** string (distinct from `reference_counters_3pct_sampled_5pct_v1`) | Code constant |
| 2.3 | **Held-out recipe spec** (e.g. terrain-heavy / UI-light / mixed paused frame) — structure, draw budget, trace source — **before** any timing | Design doc + `frame_model_workload.py` stub |
| 2.4 | Hash-freeze held-out `.recipe` + record `recipe_sha256` in a **policy-freeze commit** before first held-out benchmark | Git + evidence archive |
| 2.5 | Re-evaluate **legacy + 50643ce archives** under *old* policy (replay) and *new* policy (offline analyzer) for audit only — no retroactive edits to archives | Script or test |

**Anti-overfitting rule (non-negotiable):** A–F diagnostics may motivate policy revision but **must not** be the sole validation set for thresholds chosen using the same measurements.

---

## 3. Code architecture

### 3.1 Split preflight outcomes

Target shape in [eu4_frame_model.py](../benchmark/eu4_frame_model.py) `preflight()` / evidence JSON:

```text
offline_causal_admission
    stages: bare → reference → counters (seven-pair on training recipes)
    status: passed | failed | unavailable
    policy: tier1_causal_policy_version

offline_forensic_suitability
    stages: sampled + ablations + A–F matrix
    status: passed | failed | diagnostic (never silently merged into causal)
    policy: existing diagnostic policy versions

preflight.status
    blocked only if offline_causal_admission failed (initial default)
    OR explicit future policy flags forensic as required for specific run kinds
```

**Remove:** `representative["status"]` as a single AND over all gates for blocking.

**Keep:** Full nested `representative_workloads` payload (trials, gates, A–F) unchanged for immutability.

**Pointer / archive:** Add top-level `offline_causal_admission` and `offline_forensic_suitability`; deprecate generic `overhead_gate` in pointer summary (keep alias one release for consumers).

### 3.2 Gate computation modules

| Module | Responsibility |
|--------|----------------|
| [frame_model_workload.py](../benchmark/frame_model_workload.py) | Recipes (training trio + **held-out**); `paired_summary()`; optional **replay** helper reading frozen trial JSON |
| New `frame_model_tier1_policy.py` (or section in workload) | Pure functions: given trials + policy version → causal gate results (relative + absolute) |
| [eu4_frame_model.py](../benchmark/eu4_frame_model.py) | Orchestrate stages, call policy, assemble evidence, **do not** raise on forensic-only failure unless run kind requires it |
| [frame_model_gates.py](../benchmark/frame_model_gates.py) | `REQUIRED` sets per report kind; rename `offline_overhead` → `offline_causal_admission` |

### 3.3 Live manifest integration

In `run` / calibration path (~line 2115 in `eu4_frame_model.py`):

```python
gates.record("offline_causal_admission", ...)
gates.record("offline_forensic_suitability", ...)  # informational or required per kind
```

Implement table from roadmap § “Report-kind-specific required gates”:

- **`calibration_only`:** require causal pass; forensic **not** required.
- **`residual_discovery` / `causal`:** extend sets per roadmap (Stage 2 lays hooks; Stage 3 fills integrity gates).

Add tests: `calibration_only` proceeds when causal passes + forensic fails; forensic pass alone does **not** satisfy causal.

---

## 4. Held-out recipe workflow (mandatory sequence)

```text
1. Publish recipe design + acceptance criteria (no timings)
2. Implement generator in frame_model_workload.recipes() behind HELD_OUT_RECIPE flag or separate function
3. Commit recipe bytes + sha256 in analysis/ or tests/fixtures/
4. Run seven-pair + held-out under frozen Tier-1 policy on Mac → new immutable archive
5. Evaluate: training recipes + held-out must pass proposed causal criterion
```

If held-out fails: **policy or implementation fix** — not threshold tweak on held-out alone.

---

## 5. Re-evaluation without re-running harness (CI)

Add **`tests/test_frame_model_tier1_replay.py`** (and optional CLI):

- Load `analysis/evidence/frame-model-offline-legacy-….json` and `…50643ce-….json`.
- Apply `tier1_causal_policy_v2` (name TBD) to embedded trials only.
- Assert deterministic status transitions documented in test (legacy fails old rule; document expected under new rule).

This proves Stage 1 immutability contract before Mac re-runs.

---

## 6. Implementation phases

### Phase A — Schema + split (portable, no new policy numbers)

- [ ] Introduce `offline_causal_admission` / `offline_forensic_suitability` in preflight dict + pointer schema version bump (`stage2-split-v1`).
- [ ] Classify existing gates into causal vs forensic in `offline_workloads()`.
- [ ] Stop raising `BenchmarkError` on forensic-only failure; raise on causal failure only (match checkpoint default: forensic demoted).
- [ ] Tests for split statuses and manifest `REQUIRED` for `calibration_only`.
- [ ] CI green; **no** new Mac run required if behavior unchanged for causal-only failure case.

### Phase B — Tier-1 policy + constants

- [ ] Document numeric policy; add `tier1_causal_policy_version`.
- [ ] Implement evaluator (relative + absolute per frame) in pure Python.
- [ ] Replay tests on frozen archives.

### Phase C — Held-out recipe

- [ ] Design sign-off.
- [ ] Implement + hash-freeze recipe (commit before benchmark).
- [ ] Extend `offline_workloads()` to run held-out seven-pair (or dedicated function).
- [ ] Mac preflight → new evidence archive; pointer update; commit evidence.

### Phase D — Release gates cleanup

- [ ] Replace global `REQUIRED` monolith with per-report-kind sets.
- [ ] Update [analysis/frame-model-protocol.md](../analysis/frame-model-protocol.md) (short § on admission vs forensic).
- [ ] Stage 2 checkpoint: causal admission passes on held-out validation archive; forensic reported separately.

---

## 7. Files likely touched

| File | Change |
|------|--------|
| `benchmark/eu4_frame_model.py` | Split statuses, preflight raise policy, manifest records, policy_versions |
| `benchmark/frame_model_workload.py` | Held-out recipe; optional gate grouping helpers |
| `benchmark/frame_model_tier1_policy.py` | **New** — policy evaluation |
| `benchmark/frame_model_gates.py` | Report-kind `REQUIRED` maps |
| `tests/test_frame_model_offline_evidence.py` | Schema fields |
| `tests/test_frame_model_tier1_*.py` | **New** — replay + split + calibration-only |
| `docs/profiler-roadmap.md` | Link to this plan; tick Stage 2 when done |
| `analysis/evidence/` | New Mac archives only (never rewrite legacy) |

---

## 8. Verification matrix

| Check | Phase |
|-------|--------|
| Legacy archive bytes unchanged | Always |
| Replay old trials under new policy without re-run | B |
| Causal fail / forensic pass → preflight blocked vs unblocked per policy | A |
| Causal pass / forensic fail → `calibration_only` can proceed | A |
| Held-out sha256 stable before first timing commit | C |
| Mac archive: `git_provenance_verified`, artifact bracket, `offline_causal_admission` explicit | C |
| Portable CI 112 tests (2 skipped) green | A–D |

---

## 9. Dependencies on Stage 3

Stage 2 does **not** implement Tier 2a/2b live gates or `calibration_integrity` — only offline split + Tier-1 schema. Stage 3 consumes `offline_causal_admission` as a hard prerequisite for `run --calibration-only`.

---

## 10. Suggested branch / PR sequence

1. `cursor/stage-2-split-offline-gates-0759` — Phase A (+ tests).
2. `cursor/stage-2-tier1-policy-replay-0759` — Phase B.
3. `cursor/stage-2-held-out-recipe-0759` — Phase C (+ Mac evidence commit).
4. `cursor/stage-2-release-gate-kinds-0759` — Phase D.

Merge order: A → B → C → D (or A+B one PR if small, then C with Mac, then D).

---

## 11. Open decisions (resolved in PR #3 — see docs/tier1-causal-policy.md)

1. **Checkpoint:** forensic demoted for calibration-only and full causal release blockers; remains in `release_gates` for audit.
2. **Absolute per-frame cap (µs):** frozen at 50 µs/frame under `tier1_causal_rel3pct_abs50us_v2` (evaluator `stage2-split-v2`).
3. **Held-out recipe identity:** `terrain_surrogate` candidate documented; mandatory validation requires `analysis/held-out/` fixture committed before first timing (exploratory `ab00679` archive preserved).
4. **Legacy 3%/5% policy:** retained as `reference_counters_3pct_sampled_5pct_v1` for forensic replay only.
