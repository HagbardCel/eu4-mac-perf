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

Experiment manifest sets `venice_observer_run_authorized: true` with `render_mutation_authorized: false` for observer-only ROI runs.

Schedule if invoked: **N–A–B–A–B–A–N** (10 s phases). B phases set the mutate flag; the dylib does **not** multidraw (`border_fallback_mutate_disabled` is TLS-aggregated).

## Engagement gate (harness self-test)

Per phase `border_validation` when the command is run locally. Not a substitute for a Venice decision.

## Gate 0 (recorded)

On the **first armed loop-head detour hit** (not GL interpose alone): `CGLGetCurrentContext() != NULL` and `glMultiDrawElements` resolved via `dlsym` — **no test multidraw call**. Results:

- `border_runtime_context_checked`
- `border_runtime_context_multidraw_supported`

surfaced in manifest `border_gate0` at run end.

## ROI observer (READY_OFFLINE)

Capability-2 ACK requires the **14-byte loop-head detour** installed (`hooks_installed` chain), not MDEBV interpose alone. ROI counters are **observation-arm gated** (schema v4 `requires_armed`); `observer_timing_usable` is false — per-frame rates use `candidate_swaps`; production `/s` estimates use profiler-off Venice FPS in `analysis/tools/border_loop_roi.py`.

## Endpoints

Do **not** interpret B−A CPU until loop-head mutation exists.
