# R0-M measurement (implementation)

Canonical strategy: [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md). This document describes the **implemented** R0-M tooling.

## Evidence levels

| Level | Establishes |
|---|---|
| Synthetic FBO harness (`r0_readback_harness`) | Shared `eu4_r0_readback_capture()` on explicit FBO |
| Probe offline preflight | Injection, seqlock control, ACK, synthetic census |
| Live integrated / positive control | Default back-buffer content with operator UI bracket |

Preflight does **not** claim production drawable verification.

## Build and offline checks

```sh
PYTHONPATH=benchmark python3 benchmark/r0_probe.py build
PYTHONPATH=benchmark python3 benchmark/r0_probe.py preflight
benchmark/.build/r0_readback_harness
python3 -m unittest discover -s tests -p 'test_r0*.py'
```

## One launch protocol

[`benchmark/r0_measurement_session.py`](../benchmark/r0_measurement_session.py) runs epochs **A** (thread CPU + powermetrics reconcile), **B** (`sample`), **C** (census readback) with a single `libeu4_r0_probe.dylib` owner.

Runtime mode changes use a **seqlock** mmap control page (`eu4_r0_control_publish`); acknowledgments are `ACK,generation,frame_index,uptime_ns,mode,scenario_id`.

Frame records (`F,...`) include `uptime_ns`, `gl_error_observed`, `flush_ok`, and `pthread_tid`. Analyzer excludes failed flushes and observed GL errors.

### Integrated Venice session

```sh
PYTHONPATH=benchmark python3 benchmark/r0_measurement_session.py results/$(date -u +%Y%m%dT%H%M%SZ)-r0-integrated --run-integrated
```

Uses [`FixtureManager`](../benchmark/fixture_manager.py), [`wait_until_ready`](../benchmark/autonomous_runner.py) / `warm_up` with `probe_parser=r0_probe_rows`, and fixture save recovery on cleanup.

### Attach mode

```sh
export EU4_R0_ATTACH_PID=<eu4_pid>
PYTHONPATH=benchmark python3 benchmark/r0_measurement_session.py results/$(date -u +%Y%m%dT%H%M%SZ)-r0-measurement
```

Dry-run manifest only:

```sh
PYTHONPATH=benchmark python3 benchmark/r0_measurement_session.py results/r0-dry-run --dry-run
```

## Analysis

```sh
python3 analysis/tools/frame_evolution_analyze.py results/<run>/r0_probe.log \
  --events results/<run>/events.jsonl \
  --clock-offset-ns <from manifest> \
  --output analysis/evidence/r0-frame-evolution-<run>.json

python3 analysis/tools/r0_stack_analyze.py results/<run>/epoch_b.sample.txt \
  --output analysis/evidence/r0-main-thread-cost-model.json
```

Stack protocol: [`analysis/r0-stack-attribution-protocol.md`](../analysis/r0-stack-attribution-protocol.md).

Gate memo template: [`analysis/r0-gate-memo.md`](../analysis/r0-gate-memo.md).
