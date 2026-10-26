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

`completeness: complete` requires `incomplete_reasons` and
`unresolved_dependencies` to be empty, every required access in the declared
scope to be known, and every present value to carry `value_source`.

## Encoding

`reconstruction_trace/schema.py` enforces these rules. No separate schema
generator is required.

- Addresses, PCs, bases, targets, and register values are strings: `0x` plus
  lowercase hex, with no leading zeros except `0x0`. They are not JSON numbers.
- `size` and an edge `offset` are JSON integers, in bytes.
- Region `bytes` and a memory event `value` are lowercase hex in address order,
  exactly `2 * size` digits. The first byte is the byte at the lowest address.
- Events are one array in execution order. A `call_enter` opens a frame. A
  `call_exit` closes the most recent open frame, and its `target` equals that
  frame's `target`. Frames are not interleaved.
- If two regions cover the same byte, the recorded bytes at that offset are
  equal. A disagreement makes the trace incomplete.

```json
{
  "schema": "execution-observation/v1",
  "function_id": "synth:chase",
  "binary": null,
  "evidence": "handwritten-synthetic",
  "instrumentation": {"name": "handwritten", "version": "v1"},
  "entry_state": {
    "registers": {"rdi": "0x100000"},
    "regions": [
      {"id": "a", "kind": "heap", "base": "0x100000", "size": 16, "bytes": "00002000000000002a00000000000000", "preservation": "required"},
      {"id": "b", "kind": "heap", "base": "0x200000", "size": 4, "bytes": "2a000000", "preservation": "required"}
    ],
    "pointers": [
      {"from": {"region": "a", "offset": 0}, "to": {"region": "b", "offset": 0}},
      {"from": {"register": "rdi"}, "to": {"region": "a", "offset": 0}}
    ]
  },
  "events": [
    {"kind": "read", "pc": "0x1000", "address": "0x100000", "size": 8, "value": "0000200000000000", "value_source": "observed"}
  ],
  "exit_state": {"registers": {"rax": "0x2a"}},
  "completeness": "complete",
  "incomplete_reasons": [],
  "unresolved_dependencies": []
}
```

The eight-byte value `0000200000000000` is the little-endian bytes of
`0x200000` as they sit in memory. Replay does not treat that numeric value as
a discovered pointer. It relocates the byte range only because `pointers`
names it.

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
| `observed` | The identified `evidence` producer recorded the value directly. |
| `derived-scenario` | The value was reconstructed from authenticated historical scenario state. The event names the scenario field or memory window (`provenance`). |

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

## Pointer relocation

Fresh-process replay does not reserve the original virtual addresses. The
synthetic harness records each pointer it constructs. An edge is
`from` a region offset or a register, and `to` a region offset. The harness
knows these relationships because it built them. The fixture builder does not
infer them by scanning bytes for addresses.

The builder keeps an edge only when the observed trace reads or writes that
stored pointer, or a later access depends on it. Replay allocates each region
at a new base and rewrites each kept edge to the new `to` address. Bytes that
are not a kept edge are copied unchanged. A stored address with no harness edge
is not relocated. If the trace dereferences it, qualification fails and names
that address and size.

TR1 must replay one case where a pointer stored in one heap allocation refers
to another allocation, and both allocations receive different bases in the
fresh process. Copying the original pointer bytes without relocation is not a
pass.

## Events

Memory events are `read` and `write`: `pc`, `address`, `size`, `value`,
`value_source`, and `provenance` when the source is `derived-scenario`.

A call is a boundary:

- `call_enter`: `pc` (the call site) and `target` (the resolved target). An indirect call records the resolved target, not only the call site. Argument values are included only when the declared contract observes them.
- nested `read`, `write`, and `call_*` events while that call is active
- `call_exit`: the same `target` as the open frame, the result values the contract observes, and `effects_complete`

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

The reference run executes original synthetic code. Replay relocates
harness-recorded pointer edges as specified above. Native replay of an EU IV
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
