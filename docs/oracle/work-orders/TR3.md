---
package: TR3
upstream: TR2 QBDI-to-fixture integration (satisfied at 0a24c90)
contracts:
  - execution-observation/v1
owned_paths: []
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# TR3 — preflight only

## Deliverable

This document. It records prerequisites and a proposed later experiment. It
authorizes no implementation, EU4 launch, injection, live trace, or
replacement.

`owned_paths` is empty. Instrumentation, fixture integration, replay, and any
launch path need a separately approved scope amendment or execution work order
that names those paths. Recording the experiment here is not that amendment.
The implementation scope is [TR3-IMPL.md](TR3-IMPL.md). Writing its named
paths waits for that work order to merge. Running EU4 remains unauthorized.

Passing TR2 and passing the checks below grant neither later permission:

1. **Implementation authorization.** Instrumentation, fixture integration,
   replay, and any launch path, only after an approved scope amendment names
   the owned paths.
2. **Operator authorization.** One bounded EU4 execution, only after that
   implementation has been reviewed.

## Upstream

The block below exited 0 on 2026-10-29. `HEAD` and `origin/main` were both
`0a24c90`, the TR2 merge. Both TR2 modules ran with zero skips. That pass
satisfies the TR2 upstream. It does not authorize implementation or an EU4
execution.

Re-run the block from the repository root on a checkout whose `HEAD` is
`origin/main`. A missing file, a skip, a loader error, or any other failed
check exits nonzero. Do not add a test module or a script to hold these
checks.

`tests/test_trace_composition.py` must collect a test id containing
`defective`: the defective-callee mismatch. `tests/test_trace_qbdi_fixture.py`
must collect a test id containing `replay`: a trace from the TR0 QBDI
producer, passed through `reconstruction_trace/fixture.py`, replaying the
original synthetic function against the independent expected observations. A
Ghidra trace does not count. The blocked-because-unsupported outcome does not
count either.

```bash
set -euo pipefail

git fetch origin main
test "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)"
git cat-file -e origin/main:tests/test_trace_composition.py
git cat-file -e origin/main:tests/test_trace_qbdi_fixture.py
git diff --exit-code HEAD -- \
  tests/test_trace_composition.py \
  tests/test_trace_qbdi_fixture.py

python3 - <<'PY'
import json
with open("reconstruction_trace/qbdi/receipt.json") as f:
    receipt = json.load(f)
if not (
    receipt["decision"]
    == "Reliable Rosetta traces with explicit gaps: continue with QBDI."
    and receipt["interception"]["result"] == "supported"
    and receipt["measurements"]["slowdown_threshold"] is None
):
    raise SystemExit(1)
PY

tr2_base="$(
  git log --first-parent --reverse \
    --format=%H origin/main \
    -- tests/test_trace_qbdi_fixture.py |
    sed -n '1p'
)"
test -n "$tr2_base"
git diff --exit-code "$tr2_base" origin/main -- \
  docs/oracle/TRACE_V1.md \
  oracle \
  analysis/reconstruction \
  reverse_engineering/target.json \
  benchmark/eu4_gfx_live_capture.py
git diff --exit-code HEAD -- \
  docs/oracle/TRACE_V1.md \
  oracle \
  analysis/reconstruction \
  reverse_engineering/target.json \
  benchmark/eu4_gfx_live_capture.py

python3 - <<'PY'
import sys
import unittest

def collect(pattern):
    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern=pattern)
    if loader.errors:
        sys.exit(1)
    ids = []
    def walk(node):
        if isinstance(node, unittest.TestSuite):
            for child in node:
                walk(child)
        else:
            ids.append(node.id())
    walk(suite)
    if not ids:
        sys.exit(1)
    return ids

composition = collect("test_trace_composition.py")
qbdi = collect("test_trace_qbdi_fixture.py")
if not any("defective" in test_id for test_id in composition):
    sys.exit(1)
if not any("replay" in test_id for test_id in qbdi):
    sys.exit(1)
PY

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
discover_tests() {
  python3 -m unittest discover -s tests -p "$1" -v 2>&1 | tee "$log"
  if grep -Eq 'skipped=|\.\.\. skipped' "$log"; then
    exit 1
  fi
}
discover_tests test_trace_composition.py
discover_tests test_trace_qbdi_fixture.py
```

A passing block does not authorize implementation or an EU4 execution.

## Frozen paths

These stay read-only for a later implementation stage:

- `docs/oracle/TRACE_V1.md`
- `oracle/**`, including E1/F1 certification policy and the oracle trust model
- `benchmark/eu4_gfx_live_capture.py`, with no new launch path
- `reverse_engineering/target.json`
- `analysis/reconstruction/**`

No new memory or calling-convention inference. Evidence from the later
experiment is not E1-admissible.

## Proposed later experiment

Not authorized by this document.

Exactly one preselected `_GfxSetConstantBuffer` invocation. Completeness
follows [TRACE_V1.md](../TRACE_V1.md): entry and exit state, memory events, and
no dropped events. One fixture is built from that trace and replayed through
the existing qualified reconstruction. The frozen RE0 contract records 21
instructions and zero selected direct callees, so this experiment does not add
a caller–callee target. Scaling to other graphics functions is a later stage.

## Overhead reference

The TR0 receipt is a measurement reference and a methodology. It is not a
numeric limit. `measurements.slowdown_threshold` stays `null`. The synthetic
probe recorded a native call of 83 ns, a QBDI VM call of 1,536,375 ns, and an
intercepted call of 7,708,375 ns. Those times do not bound
`_GfxSetConstantBuffer`. This preflight sets no performance threshold. A later
performance acceptance threshold needs its own authorization.

## Non-goals

- No instrumentation, no fixture bridge, no replay driver, and no launch path.
- No edit to `TRACE_V1.md`, E1/F1 policy, or the existing reconstruction.
- No EU4 process.

## Stopping rules

- **Contract conflict:** a required schema change stops for an amendment.
- **Scope expansion:** a second function, a caller–callee target, or treating
  this document as permission to implement stops.
- **Evidence limitation:** an unsatisfied TR2 upstream stays unsatisfied. Do
  not relabel a Ghidra trace or an unsupported TR0 outcome as a pass.

## Merge gate

This file landed while TR2 was unsatisfied. The preflight passed at `0a24c90`
on 2026-10-29. A passing preflight stops. It does not authorize
implementation or an EU4 execution.
