# EU IV live diagnostic roadmap (post-WP11)

WP11 ended the Tier-1 qualification program. Next work is **short and outcome-oriented**: calibrate observer bias offline, run **one** bias-aware live attribution session, then optimize with the profiler **off**.

See [`live-diagnostic-measurement-contract.md`](live-diagnostic-measurement-contract.md) for qualified vs diagnostic modes.

## Phases

| Phase | Goal | EU IV runs | Deliverable |
|-------|------|------------|-------------|
| **A** | Reframe profiler as diagnostic instrument | 0 | `preflight --intrusive-diagnostic-contract` + gate policy (no live launch yet) |
| **B** | Observer-bias calibration | 0 | Per-primitive overhead slopes (offline) |
| **C** | One live attribution run | **1** | R–C–R–C–R + external CPU/power/swap |
| **D** | Optimize top bottleneck(s) | 1 launch per candidate | Uninstrumented causal A/B |
| **E** | Durable fix | as needed | Minimal binary/source patch |

## PR sequence (infrastructure)

1. **Diagnostic-mode contract** (Phase A) — policy/docs + `run --diagnostic-only`; no EU IV run.
2. **Observer-bias calibration + reporting** (Phase B) — offline harness, bias-aware tables; tests only.
3. **Single-launch live protocol** (Phase C) — alternating reference/counters phases, one ranked report.

**Stopping rule:** after Phase C, pick the strongest actionable bottleneck and ship an **optimization PR**, not another profiler infrastructure PR.

## Phase C sketch (not yet implemented)

```text
90 s warm-up
R1 20 s → C1 20 s → R2 20 s → C2 20 s → R3 20 s
```

External metrics throughout: EU IV CPU, swap rate, power, cadence. Compare R vs C for live observer effect; use C1/C2 for attribution and ranking stability.

## Phase D rule

Validation runs with **profiler off**; measure CPU, power, swap, cadence, visuals externally. Sampler-uniform cache remains low priority (worked in isolation, no reproducible live benefit).
