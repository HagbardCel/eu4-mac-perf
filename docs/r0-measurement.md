# R0-M measurement (implementation)

Canonical strategy: [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md). This document describes the **implemented** R0-M tooling.

## Build and offline checks

```sh
PYTHONPATH=benchmark python3 benchmark/r0_probe.py build
PYTHONPATH=benchmark python3 benchmark/r0_probe.py preflight
benchmark/.build/r0_readback_harness
```

## One launch protocol

[`benchmark/r0_measurement_session.py`](../benchmark/r0_measurement_session.py) runs epochs **A** (thread CPU), **B** (`sample`), **C** (census readback) with a single `libeu4_r0_probe.dylib` owner.

Runtime mode changes use a **read-only** mmap control page; acknowledgments are written to the append-only log (`ACK,generation,frame_index,uptime_ns`).

For attach-mode testing (game already running with probe injected):

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
  --output analysis/evidence/r0-frame-evolution-<run>.json

python3 analysis/tools/r0_stack_analyze.py results/<run>/epoch_b.sample.txt \
  --output analysis/evidence/r0-main-thread-cost-model.json
```

Stack protocol: [`analysis/r0-stack-attribution-protocol.md`](../analysis/r0-stack-attribution-protocol.md).

Gate memo template: [`analysis/r0-gate-memo.md`](../analysis/r0-gate-memo.md).
