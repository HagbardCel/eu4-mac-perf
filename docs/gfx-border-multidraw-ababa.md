# Border multidraw ABABA (capability 2)

Profiler-off harness for border draw observation and (future) loop-head multidraw mutation. Static gates: [border-multidraw-go-nogo.md](../analysis/border-multidraw-go-nogo.md) — **mutation NO-GO** for GL interpose; **observer ABABA only**.

## Build

```bash
PYTHONPATH=benchmark python3 benchmark/codegen_submission_counters.py
PYTHONPATH=benchmark python3 -c "import submission_control as c; c.build_border_multidraw()"
```

## Run

```bash
python3 benchmark/submission_experiment.py --output results --border-multidraw-ababa
```

Schedule: **N–A–B–A–B–A–N** (10 s phases). B phases set the mutate flag but the dylib **does not multidraw** until loop-head patch lands (`border_fallback_mutate_disabled` may increment).

## Engagement gate (required)

Per phase `border_validation`:

- **N (minimal):** `border_multidraw_calls == 0`
- **A:** `border_candidate_draws > 0`, no multidraw counters
- **B:** same as A until mutation ships (no `draw_calls_eliminated`)

Experiment fails if no A-phase draws or any phase fails validation.

## Gate 6

First border interpose with gameplay context current checks `glGetString(GL_VERSION)`; `dlsym` alone is insufficient for mutation authorization.

## Endpoints (when mutation exists)

Until then, use observer run to prove interpose engagement only. Do **not** interpret B−A CPU as batching benefit.
