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
