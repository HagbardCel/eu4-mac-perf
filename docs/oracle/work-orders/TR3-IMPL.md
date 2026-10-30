---
package: TR3-IMPL
upstream: TR3 preflight satisfied at 0a24c90
contracts:
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/tr3/**
  - tests/test_trace_tr3.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
  - reconstruction_trace/fixture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# TR3 — one `_GfxSetConstantBuffer` invocation

## Deliverable

The one-invocation producer, the offline test, and the launch entry named
below. Merging this work order authorizes a later implementation PR to edit
only those paths. It does not authorize launching or attaching to EU4. That
execution remains a separate operator authorization, and only after the
implementation has been reviewed.

A tested implementation may still find that `execution-observation/v1` cannot
represent the live pointer graph. That result stops for a contract decision.
It is not permission to qualify around the gap. Implementation success does
not guarantee that a later EU4 run produces a qualified trace.

## Experiment

One preselected `_GfxSetConstantBuffer` invocation, entry RVA `0x15ec36a`, as
recorded in the frozen
[contract](../../../analysis/reconstruction/_GfxSetConstantBuffer/contract.md):
21 instructions and zero selected direct callees. No caller trace, no second
function, and no caller–callee target.

The contract remains [TRACE_V1.md](../TRACE_V1.md). No schema change. A
missing event field stops for an amendment. The trace evidence is not
E1-admissible. This work order sets no slowdown threshold.

The producer reuses the qualified TR0 QBDI/Rosetta method. It selects exactly
one invocation before examining its trace outcome, then stops, whether the
observation is complete, incomplete, or failed. An incomplete observation is
retained diagnostically and cannot qualify. No automatic retry, reselection,
or later invocation is authorized.

Instruction, memory-access, and dropped-event accounting follows the TR0
methodology. A discrepancy prevents qualification. It is not a warning.

## Pointer provenance

The live producer may use only pointer relationships directly supported by the
frozen RE0 contract and authenticated entry-state observations. It must not
infer relocation edges by scanning memory or interpreting arbitrary
address-like values. Any relationship or memory boundary that cannot be
established under `execution-observation/v1` stays unresolved, and the trace
cannot qualify. If the frozen contract cannot represent the necessary
provenance, implementation stops for an amendment.

`*RDI` leads to the context object, and `RSI` is stored into that object even
when the explicit-length branch never dereferences the buffer. Those
relationships still need this provenance. They are not harness-constructed
edges.

The fixture comes from the existing `reconstruction_trace/fixture.py`. That
file is not an owned path. Successful `fixture.qualify()` does not prove that
a pointer written during execution can be relocated or normalized.

## Replay

The adapter may translate a qualified fixture into the existing RE1 `scenario`
protocol under
[reconstruction/](../../../analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/).
It may not extend that input domain, substitute guessed inputs, or alter the
comparator. Unsupported states and unrepresentable pointer relationships are
a refusal, not a best-effort replay. No change to `oracle/function/replay.py`
or any other file under `oracle/` or `analysis/reconstruction/`.

Replay only when the captured slot is in RE1's declared domain, `[0, 6)`.
For `R9D == -1`, supply the captured first dword of the buffer. Otherwise
preserve the existing no-read guarantee. Compare normalized pointer roles and
`RAX`, not unrelated virtual addresses from different processes. Also compare
the three observed context effects and preservation of the declared
observable region.

## Launch entry

`reconstruction_trace/tr3/run.py` is the only launch entry. It sits inside
`reconstruction_trace/tr3/**` and is named here so that restriction is
explicit. Tests may call it for an offline refusal or a mocked child, and must not launch EU4.

Before it installs instrumentation or attaches to a process, it verifies the
pin already recorded in read-only `reverse_engineering/target.json`:

- binary SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`
- entry RVA `0x15ec36a`
- the 75-byte window `0x15ec36a`–`0x15ec3b5`, SHA-256 `8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943`

A mismatch refuses execution. The runtime address comes from that verified
Mach-O image and its actual load address, not from the preferred image base.
The receipt field `code_window_sha256` (`39e4133f…`) is not this pin.

## Offline tests

`tests/test_trace_tr3.py` uses synthetic or controlled observations and
labels them as such. It does not claim that an EU4 invocation has been
qualified. It must not launch EU4, and a skip is not a pass. It does not
modify `reconstruction_trace/fixture.py`.

Three acceptance cases:

1. **Dropped event.** Introduce an accounting discrepancy and show that the
   trace is incomplete and `fixture.qualify()` refuses it. Setting
   `completeness: "incomplete"` on the input is not that test.
2. **Written pointer.** The explicit-length branch stores `RSI` and never
   dereferences it. The adapter rejects a missing or unauthenticated
   buffer-pointer role even when `fixture.qualify()` succeeds. Pointer-role
   normalization is not inferred from coincidentally equal numeric addresses.
   Qualification of the byte fixture is not validation of that role.
3. **One shot.** The selected invocation is incomplete, another eligible
   invocation is simulated, and the producer does not select or trace it.

## Non-goals

- No second function, no caller trace, and no caller–callee target.
- No edit to `TRACE_V1.md`, `oracle/**`, `analysis/reconstruction/**`,
  `reverse_engineering/target.json`, `benchmark/eu4_gfx_live_capture.py`,
  or `reconstruction_trace/fixture.py`.
- No EU4 process in tests or CI.

## Stopping rules

- **Contract conflict:** a new event kind, a schema change, or a provenance
  the frozen contract cannot represent stops for an amendment.
- **Scope expansion:** a second function, a caller–callee target, another
  invocation after the selected one, a memory scan for relocation edges, or
  treating this work order as permission to run EU4, stops.
- **Evidence limitation:** an incomplete observation, a dropped-event
  discrepancy, or an unauthenticated pointer role stays unqualified. Do not
  relabel it as a pass.

## Merge gate

The offline test passes, including the three acceptance cases, with zero
skips. `run.py` is not executed against EU4. A green implementation does not authorize
the EU4 run and does not by itself show that a live trace can qualify.
