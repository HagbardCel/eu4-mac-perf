# Border multidraw ABABA (capability 2)

Profiler-off experiment for `glMultiDrawElementsBaseVertex` batching on border draws. Static gate chain: [border-multidraw-go-nogo.md](../analysis/border-multidraw-go-nogo.md).

## Build

```bash
PYTHONPATH=benchmark python3 benchmark/codegen_submission_counters.py
PYTHONPATH=benchmark python3 -c "import submission_control as c; c.build_border_multidraw()"
```

## Run

```bash
python3 benchmark/submission_experiment.py --output results --border-multidraw-ababa
```

Schedule: **N–A–B–A–B–A–N** (10 s phases, 3 s settle).

| Phase | Mode | Border flags |
|-------|------|----------------|
| N | reference | minimal hook |
| A | candidate | pass-through + telemetry |
| B | candidate | mutate (multidraw) |

Flags live in mmap offset **56** (`set_border_flags`); dylib reads them each `CGLFlushDrawable` poll.

## Endpoints

- **Primary mechanistic:** **B − A** on CPU-ms/s when swap cadence within ±2%; else joint CPU-ms/frame.
- **Net deployable:** **B − N** when N bookends used.
- **Visual:** A↔A envelope on `measure-scene-a*.png`; A↔B must not exceed on border ROIs (manual / validation tooling).
- **Counters:** schema v3 slots `border_*` (per-batch updates, no hot-loop atomics).

## Gate 6

If PR B = RUNTIME-ASSERT, manifest sets `border_api_runtime_assert: true`; mutation disabled when `glMultiDrawElementsBaseVertex` fails to resolve at load.
