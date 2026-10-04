# Gfx subrecord observer smoke (capability 1) — incomplete

**Run:** `20261004T111812Z-submission-experiment`  
**Git:** `main` @ merge `2211e08` (PR #31)  
**Command:** `python3 benchmark/submission_experiment.py --output results --engagement-smoke`

## Outcome

**Incomplete** — harness aborted after all three measurement windows finished, during per-phase metric summarization.

```text
Error: Insufficient fully paused swap samples in the 30-second phase
```

## What completed

| Step | Status |
|------|--------|
| Preflight + fixture install | OK |
| EU IV launch + readiness | OK (`swap_rate` 55.3, `cpu_ms_s` 1113.9) |
| Powermetrics tail | OK (`sample_count` 174) |
| Phases a1 / b1 / a2 (10 s each, 3 s settle) | Timestamps recorded in `events.jsonl` |
| Per-phase auto-probe | ~10 fully paused rows per 10 s window |
| `observer_gate` / `engagement_gate` / `validation.json` | **Not emitted** (failure before attach) |

## Root cause (harness)

Observer smoke uses **10 s** phases (`OBSERVER_PHASE_SECONDS`), but `autonomous_runner.summarize()` required **25** swap/power samples (written for **30 s** autonomous baselines). At ~1 Hz probe cadence, each 10 s window yields ~10 samples, so the first phase (`a1`) always fails summarization.

**Fix (post-run):** `submission_experiment._attach_phase_summaries()` passes phase-scaled `min_swap_samples` / `min_power_samples` into `summarize()`.

## Forensic mmap totals (end of run)

Final `submission_control.bin` snapshot (cumulative, not per-phase deltas):

| Counter | Value |
|---------|------:|
| `control_ticks` | 8050 |
| `candidate_site_entries` | 19202193 |
| `eligible_pair_hits` | 0 |
| `candidate_effective_actions` | 0 |

Mesh hook traffic is present globally; **no buffer-bind equivalence hits** were recorded for this session. Per-phase control validation was not sealed — **re-run required** after the summarize fix to judge engagement gates and scene checks.

## Artifacts

- `manifest.json` — `status: incomplete`, error string above
- `events.jsonl`, `auto-probe.csv` — phase timing and probe rows
- Scene PNGs excluded from git (see `results/README.md`)

Registered summary: [`analysis/evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json`](../../analysis/evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json).
