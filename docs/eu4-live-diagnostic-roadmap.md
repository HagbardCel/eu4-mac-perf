# EU IV live diagnostic roadmap (post-WP11)

WP11 ended the Tier-1 qualification program. Next work is **short and outcome-oriented**: calibrate observer bias offline, run **one** bias-aware live attribution session, then optimize with the profiler **off**.

See [`live-diagnostic-measurement-contract.md`](live-diagnostic-measurement-contract.md) for qualified vs diagnostic modes.

## Phases

| Phase | Goal | EU IV runs | Deliverable |
|-------|------|------------|-------------|
| **A** | Reframe profiler as diagnostic instrument | 0 | `preflight --intrusive-diagnostic-contract` + reserved `run --diagnostic-only` (live schedule in Phase C) |
| **B** | Observer-bias calibration | 0 | **Done** — `e86e33b3` @ `26af021`; [`memo`](observer-bias-calibration-memo-20261003.md) |
| **C** | One live attribution run | **1** | [`live-diagnostic-phase-c.md`](live-diagnostic-phase-c.md) — R–C–R–C–R; ranking only (no component bias subtract) |
| **D** | Optimize top bottleneck(s) | 1 launch per candidate | Uninstrumented causal A/B |
| **E** | Durable fix | as needed | Minimal binary/source patch |

## PR sequence (infrastructure)

1. **Diagnostic-mode contract** (Phase A) — policy/docs + `run --diagnostic-only`; no EU IV run.
2. **Observer-bias calibration + reporting** (Phase B) — offline harness, bias-aware tables; tests only.
3. **Single-launch live protocol** (Phase C) — alternating reference/counters phases, one ranked report.

**Stopping rule:** after Phase C, pick the strongest actionable bottleneck and ship an **optimization PR**, not another profiler infrastructure PR.

## Phase C (not yet implemented)

Protocol and interpretation rules: [`live-diagnostic-phase-c.md`](live-diagnostic-phase-c.md). Offline Phase B fixed aggregate COUNTERS tax ~9–10 µs/frame on mesh; **component correction unavailable** on `e86e33b3`.

## Phase D rule

Validation runs with **profiler off**; measure CPU, power, swap, cadence, visuals externally. Sampler-uniform cache remains low priority (worked in isolation, no reproducible live benefit).
