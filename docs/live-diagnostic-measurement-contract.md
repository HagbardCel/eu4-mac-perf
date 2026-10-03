# Live measurement contract — qualified vs diagnostic

WP11 (`33339073`) is the **terminal Tier-1 qualification** outcome: v4 training admission **failed**. The profiler remains useful as an **intrusive diagnostic** on live paused EU IV.

## Qualified measurement (`qualified_v1`)

- Offline Tier-1 causal admission **passed** on the current build (training + held-out per policy).
- Live `eu4_frame_model.py run` (default) requires passing offline admission before hooks install.
- Absolute per-frame timings and overhead gates may be interpreted quantitatively.
- Held-out and qualification claims are allowed only under the frozen policy that passed.

## Diagnostic measurement (`intrusive_diagnostic_v1`)

- Selected with `eu4_frame_model.py preflight --intrusive-diagnostic-contract` (build/scene integrity + registered WP11 terminal evidence check only; **no** `offline_workloads`, held-out, immutable archive, or rolling-pointer update).
- Live capture uses `run --diagnostic-only` once **Phase C** (R–C–R–C–R) is implemented; that command currently refuses to launch EU IV.
- Retains build integrity, scene/fixture checks, provenance, and the same profiler implementation.
- **Does not** require passing offline causal admission; failed v4 (or v2/v3) gates are recorded but non-blocking.
- Output is **intrusive / quantitatively unqualified**:
  - no held-out qualification claims;
  - no interpretation of absolute profiler microseconds as uninstrumented EU IV truth;
  - relative attribution is allowed only with explicit observer-effect estimates (see roadmap).
- Residual/semantic attribution machinery may run.

## Terminal qualification record

| Field | Value |
|-------|--------|
| Evidence | `20261003T173210.094480Z-33339073` |
| Policy | `tier1_causal_steady_state_hybrid_v4` |
| `overhead_gate` | `failed` |

No v5, no further Mac qualification captures unless policy is deliberately reopened in a new work package.
