# Execution observation — `execution-observation/v1`

**Status:** frozen contract for the synthetic trace-and-fixture stage. It does
not authorize a EU IV launch, injection, or live trace. It does not amend
`FUNCTION-CAPTURE v1`, E1 admission, or the RE0–RE2 artifacts.

**Schema name:** `execution-observation/v1`. Additional keys, evidence values,
or event kinds are a contract amendment, not a branch-local extension.

Two producers and one consumer. The package is `reconstruction_trace/`, outside
`oracle/`. Tests may label a fixture `handwritten-synthetic`. That label is a
test double. It is not a third producer, and it cannot satisfy TR0, the
QBDI-to-fixture integration, or the P-code importer.

## Evidence

| `evidence` | Meaning |
| --- | --- |
| `qbdi-rosetta` | Native x86-64 observation under QBDI on Rosetta |
| `ghidra-pcode` | Import of an already frozen P-code receipt |
| `handwritten-synthetic` | Test double only |

QBDI and P-code share this file shape. They are not interchangeable, and E1
does not admit either.

## Trace

Required fields:

- `schema`: `execution-observation/v1`
- `function_id`
- `binary`: required for an EU IV subject (`sha256`, `image_base`, `entry_rva`); `null` for a synthetic subject
- `evidence`
- `instrumentation`: producer name and version
- `entry_state.registers`
- `entry_state.regions`
- `events`: ordered
- `exit_state`
- `completeness`: `complete` or `incomplete`
- `incomplete_reasons`: required when incomplete, otherwise empty
- `unresolved_dependencies`: each names `address`, `size`, and `reason`

A trace is `incomplete` when any value, size, or instruction is unknown, any
access in the declared instrumentation scope is unclassified or unsupported,
any event was dropped, or any dependency is unresolved. Incomplete traces do
not become fixtures. This milestone fails closed. It does not recover a partial
trace.

## Three results

These are separate. None implies the next.

| Result | Meaning |
| --- | --- |
| `expected_accesses_match` | The declared known-answer set matches. Compiler-generated accesses, including unnamed stack traffic, are counted apart and are not required to match that set. |
| `instrumentation_complete` | Every memory operation inside the declared instrumentation scope was accounted for. |
| `fixture_qualified` | State and effects are complete for the declared replay boundary. |

`expected_accesses_match` is a TR0 receipt result. It does not set
`instrumentation_complete` and it does not qualify a fixture.

## Values

A present `value` carries `value_source`:

| `value_source` | Meaning |
| --- | --- |
| `observed` | The value was read by native instrumentation. |
| `derived-scenario` | The value was reconstructed from an authenticated historical scenario. The event names the scenario field or memory window (`provenance`). |

Both are legitimate. They are not the same evidence. A missing value, an
ambiguous derivation, or a missing `value_source` makes the trace incomplete.
The builder does not invent a value.

QBDI events retain the QBDI access flags. They are not collapsed into a boolean.

## Entry state

Synthetic entry-state acquisition is generic to the test harness and
independent of the function's expected memory-access pattern. Function-specific
memory dependencies are derived from the observed trace, not supplied as a
handwritten fixture map.

The harness records:

- each heap allocation's identity, base, and size
- the stack region's base and size
- raw initial contents
- initial registers
- stack and return setup

The tracer and the fixture builder discover which addresses were accessed,
which bytes are dependencies, which branches ran, which changes are observable,
and which ranges and aliases must be retained. A test must not predeclare which
members of an object a function reads or writes.

Each region has `id`, `kind` (`heap` or `stack`), `base`, `size`, and either
`bytes` or `bytes_absent: true`. `preservation` is `required` (default) or
`excluded`. A preservation check that needs initial bytes fails when those
bytes are absent and the region is not `excluded`.

Alias identity is the harness allocation id when two arguments reference the
same allocation. Numeric equality of raw bytes is not a pointer type and is
not an alias.

## Events

Memory events are `read` and `write`: `pc`, `address`, `size`, `value`,
`value_source`, and `provenance` when the source is `derived-scenario`.

A call is a boundary:

- `call_enter`: `pc` (the call site) and `target` (the resolved target). An indirect call records the resolved target, not only the call site. Argument values are included only when the declared contract observes them.
- nested `read`, `write`, and `call_*` events while that call is active
- `call_exit`: the same `target`, the result values the contract observes, and `effects_complete`

`effects_complete: true` may replace the nested events with an `effects` list
only when that list keeps every effect the caller's later execution and final
observations depend on. If `effects_complete` is absent or false, the nested
events are required for qualification.

## Fixture consumer

`reconstruction_trace/fixture.py` derives initial read dependencies and
observable write effects from the ordered trace, over the generic entry
inventory. A later read of a byte this invocation already wrote is not an
additional initial dependency. Unresolved dependencies fail qualification and
name the missing address and size. The consumer does not emit
`FUNCTION-CAPTURE v1`.

## P-code importer

The importer reads the frozen receipt
`analysis/reconstruction/_GfxSetConstantBuffer/pilot/observations-2026-10-18.json`
(`eu4-re0-pilot-receipt/v1`). Load rows are address and width only. Stores
carry written values. Context and stack windows carry initial and final bytes.

A read value may be reconstructed only from the authenticated frozen
`scenarios.json`, the ordered trace, and the recorded memory state. The
receipt's scenario SHA binds the scenario file. Every reconstructed value is
`derived-scenario` and names its source. An ambiguous value makes that trace
incomplete. Stores and window bytes that the receipt itself contains are
`observed`.

Conversion is not a new P-code run and not a native execution. It is not
evidence of automatic memory-dependency discovery. That demonstration is the
synthetic path, including the QBDI integration in
[TR2](work-orders/TR2.md).

## Native replay

One reusable x86-64 invocation harness covers supported, self-contained
synthetic functions. It restores the declared machine state, including stack
layout, registers, the return boundary, and the flags or SIMD state the
contract declares, then calls the original compiled function and records the
declared exit observations. An ordinary C function pointer is not that harness.

The reference run executes original synthetic code. Native replay of an EU IV
function extracted from the Mach-O, including its imports, globals, and
callees, is outside this stage.

## Validation identity

When a reconstructed caller links a reconstructed callee, the validation
identity is the hash of:

- the caller candidate
- the callee implementation digests
- the callee qualification record
- the comparator version
- the fixtures
- the harness

The qualification record is a small immutable note that the callee was
validated for the declared behavioral scope. A source digest alone is not that
record. Changing any bound input invalidates the validation result. It does
not invalidate the original input fixture.
