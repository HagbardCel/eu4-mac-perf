# R0-M measurement (implementation)

Canonical strategy: [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md).

## Stopping rule

- **PR A** (this branch): validated tooling; merge after offline gates pass.
- **PR B**: single live Venice evidence session only.

## Evidence levels

| Level | Establishes |
|---|---|
| Synthetic FBO harness | Shared `eu4_r0_readback_capture()` on explicit FBO |
| Probe offline preflight | Injection, atomic control, ACK, synthetic census |
| Live integrated session | Production drawable + generation-bound captures |

## Control and clock

- **64-bit atomic** mmap control word (`generation`, `scenario_id`, `mode`, `version`).
- **Clock:** `CLOCK_UPTIME_RAW` for probe `uptime_ns` and session `event_uptime_ns`.
- **Calibration:** interval containment \(t_{\mathrm{before}} \le t_{\mathrm{ack}} \le t_{\mathrm{after}}\) with bounded RTT; no offset estimation.

## F24 positive control

Launch-time `EU4_R0_CAPTURE_DIR`. RGBA files are written only for **`CENSUS_CAPTURE`** generations (`gen_pre` / `gen_post`); ordinary **`CENSUS`** performs readback without disk capture.

Operator order: stabilize (UI closed) → publish pre generation → verify capture → `pre_window_end` → operator opens panel → discard → `post_window_start` → publish post generation → verify capture.

Panel ROI from [`fixtures/venice_panel_roi.json`](../fixtures/venice_panel_roi.json) (OpenGL bottom-left). Offline gate requires capture files + localized ROI change.

## Readback failure codes

Logged on each `F` record (`readback_failure_code`). Only `UNSUPPORTED_DRAWABLE` enables `partial_complete` without epoch C.

## Flush ownership

The R0 probe claims a single nonzero `pthread` thread ID on first `CGLFlushDrawable` interposition. Any other thread bypasses census/capture and logs one `T` violation record per session. Integrated acceptance requires `flush_single_owner` (unanimous nonzero `F` thread IDs, no `T` records).

## Epoch A thread CPU (libproc)

Per-thread CPU for epoch A uses **`libproc`** by default (`PROC_PIDLISTTHREADIDS` + `PROC_PIDTHREADID64INFO`). Set `EU4_THREAD_CPU_BACKEND=mach` only for diagnostic comparisons with the legacy `task_for_pid` path.

Offline qualification (no EU IV):

```sh
PYTHONPATH=benchmark python3 benchmark/thread_cpu_sampler.py --preflight-offline
PYTHONPATH=benchmark python3 -m unittest tests.test_thread_cpu_libproc_qualification -v
```

Integrated sessions run **`preflight_offline()`** before fixture install / EU IV launch, then **target `preflight(game.pid)`** after the game PID is known. Dry-run manifests skip CPU preflight.

### Process CPU finalization (epoch A)

Thread CPU from libproc is checkpointed after the epoch-A poll window with `cpu_reconciliation_phase: provisional` and `reconcile_ok: false`. Process CPU from powermetrics is reconciled only after intentional helper shutdown: `PowerTail.finish()` must report a clean drain (no `incomplete` status, zero `trailing_partial_bytes`). New integrated manifests set `cpu_accounting_contract: r0_cpu_finalize_v1`; acceptance requires `cpu_reconciliation_phase: finalized` and a passing reconcile residual.

The committed Oct 12 Venice capsule (`20261012T210806Z-r0-measurement`) remains on the legacy gate (`reconcile_ok` only). Offline retrospective process-CPU means (27 vs 29 plist samples) use `benchmark/cpu_reconcile.py` against `epoch_a_process_cpu_samples.json` without mutating acquisition `epoch_a_thread_cpu.json`.

Requalify copies must patch **copy-only** `manifest.json` `probe_log_path` to the working copy; relative paths also resolve under the run directory when the canonical path is absent.

## Build and test

```sh
PYTHONPATH=benchmark python3 benchmark/r0_probe.py build
PYTHONPATH=benchmark python3 benchmark/r0_probe.py preflight
python -m compileall -q benchmark analysis
PYTHONPATH=benchmark python3 -m unittest discover -s tests -p 'test_r0*.py'
```

## Integrated session

```sh
PYTHONPATH=benchmark python3 benchmark/r0_measurement_session.py results/<run> --run-integrated
```

Launch uses `--continuelastsave` and fixture save via [`FixtureManager`](../benchmark/fixture_manager.py).
