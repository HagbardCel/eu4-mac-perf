# Border multidraw ABABA (capability 2)

Profiler-off harness for border draw observation. Static gates: [border-multidraw-go-nogo.md](../analysis/border-multidraw-go-nogo.md) — **mutation NO-GO** for GL interpose; **no manual Venice ABABA** is authorized until loop-head mutation and a new PR B GO.

## Build

```bash
PYTHONPATH=benchmark python3 benchmark/codegen_submission_counters.py
PYTHONPATH=benchmark python3 -c "import submission_control as c; c.build_border_multidraw()"
```

## Run (CI / local dylib validation only)

```bash
python3 benchmark/submission_experiment.py --output results --border-multidraw-ababa
```

Manifest sets `venice_run_authorized: false`. Prefer offline loop-head RE at `0x1010cbe55` before spending a manual EU IV session.

Schedule if invoked: **N–A–B–A–B–A–N** (10 s phases). B phases set the mutate flag; the dylib does **not** multidraw (`border_fallback_mutate_disabled` is TLS-aggregated).

## Engagement gate (harness self-test)

Per phase `border_validation` when the command is run locally. Not a substitute for a Venice decision.

## Gate 0 (recorded)

On first border-site draw with gameplay context current: `glGetString(GL_VERSION)` parsed for **core ≥ 3.2** (not `dlsym` alone). Results:

- `border_runtime_context_checked`
- `border_runtime_context_multidraw_supported`

surfaced in manifest `border_gate0` at run end.

## Endpoints

Do **not** interpret B−A CPU until loop-head mutation exists.
