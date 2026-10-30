# Generic trace target — `trace-target/v1`

**Status:** input contract for one preselected function. It authorizes no EU IV
launch, injection, live trace, or code replacement. It does not extend
[`execution-observation/v1`](TRACE_V1.md), and it does not define a raw-trace
schema. The existing TR3 native JSON remains the raw acquisition artifact.
Generic observation processing is GEN2.

**Schema name:** `trace-target/v1`. Additional keys or trigger modes are a
contract amendment, not a branch-local extension.

The descriptor is data. The engine must not know that a register is a context,
buffer, slot, or length. Function-specific meaning stays in a later consumer.
The existing TR3 projection and the RE1 adapter remain unchanged.

## Encoding

Addresses, bases, and RVAs are strings: `0x` plus lowercase hex, with no
leading zeros except `0x0`. They are not JSON numbers. Budget fields are JSON
integers, in the units named below. This matches the address encoding in
[`TRACE_V1.md`](TRACE_V1.md).

## Descriptor

Required fields:

- `schema`: `trace-target/v1`
- `binary.sha256`: lowercase hex SHA-256 of the executable file
- `binary.image_base`: preferred image base, not the runtime slide
- `binary.window_sha256`: lowercase hex SHA-256 of the file bytes covering the single range
- `function.name`: label for the record
- `function.entry_rva`
- `function.ranges`: one pair, `[start, exclusive_end]`
- `trigger.mode`: `first-invocation`
- `capture.max_instructions`, `capture.max_memory_accesses`, `capture.max_bytes`

There is no `function.kind` field. Leaf-only operation is a restriction of this
contract: one bounded invocation, and an observed call is rejected.

`function.ranges` has length 1. `start` equals `function.entry_rva`.
`exclusive_end` is greater than `start`. The second address is the first byte
after the window, matching the frozen pin in
[`reverse_engineering/target.json`](../../reverse_engineering/target.json)
(`0x15ec36a`–`0x15ec3b5`). A static range is an input, not a runtime proof.

A mismatch of file hash, window hash, entry, or range refuses before
instrumentation. The runtime site comes from the loaded image and its actual
load address, as [TR3-IMPL](work-orders/TR3-IMPL.md) already requires.
`trigger.mode` other than `first-invocation`, or any range shape other than
the one pair above, refuses before capture. That refusal is not a partial
trace.

```json
{
  "schema": "trace-target/v1",
  "binary": {
    "sha256": "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
    "image_base": "0x100000000",
    "window_sha256": "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"
  },
  "function": {
    "name": "_GfxSetConstantBuffer",
    "entry_rva": "0x15ec36a",
    "ranges": [["0x15ec36a", "0x15ec3b5"]]
  },
  "trigger": {"mode": "first-invocation"},
  "capture": {
    "max_instructions": 4096,
    "max_memory_accesses": 8192,
    "max_bytes": 4194304
  }
}
```

The pins above repeat
[`reverse_engineering/target.json`](../../reverse_engineering/target.json).
They are not a second source of truth, and this file is not loaded by code.
The three budget integers are an illustration of required fields. They are not
defaults and they are not an authorized ceiling. A missing budget refuses.
Each budget is a positive integer supplied by the descriptor.

## Budgets

| Field | Unit |
| --- | --- |
| `max_instructions` | Instructions retained for the selected invocation |
| `max_memory_accesses` | Memory operations retained for that invocation |
| `max_bytes` | Bytes retained in the raw capture payload, excluding serialization overhead |

Overflow keeps the raw record, records a drop count, and makes the trace
execution-incomplete. A silent drop is not a complete trace. GEN1 enforces an
implementation safety ceiling before allocation. This contract adds no
budgeting subsystem and no resource-accounting schema. A positive `max_bytes`
smaller than the mandatory capture envelope — entry and exit GPR state, the
two FXSAVE images, and the mapped code window — refuses before the hook, and
the diagnostic names that minimum.

## Control flow

Intra-range conditional and unconditional branches are supported. Calls,
recursion, exceptions, execution outside the authenticated function range, or
an exit other than the expected return make the trace execution-incomplete.
The raw record is retained. The tool does not follow callees or extend the
instrumentation scope.

Execution completeness applies to the selected dynamic invocation, not to
coverage of every static instruction or branch. A 15-instruction execution of
a 21-instruction function can be complete.

## Two properties

These are independent. The first does not imply the second.

| Property | Complete when |
| --- | --- |
| Execution-trace completeness | For the selected invocation: entry and exit registers, executed instruction addresses and bytes, the control flow actually taken, and every in-scope memory access with an accounted-for type, size, and value. Drop counts are zero. The exit is supported. |
| Replay-state completeness | [`fixture.qualify()`](../../reconstruction_trace/fixture.py) accepts an `execution-observation/v1` trace. Unchanged. |

An unknown allocation boundary alone does not make a trace
execution-incomplete. A recorded access into an unknown region can still be
execution-complete. It is replay-incomplete until entry state, allocation
bounds, and pointer edges are sufficient for `fixture.qualify()`.

A write does not supply the bytes it replaced. An address-shaped integer is
not a relocation edge. A missing fact stays missing. The builder does not
invent a region, a value, or a pointer.

A capture may be execution-complete and still fail replay qualification. That
failure is the existing `execution-observation/v1` result. This contract
creates no evidence tier. A `trace-target/v1` descriptor is not E1 evidence,
not `FUNCTION-CAPTURE v1`, and not a fixture.

## Supported execution

One preselected function per authorized session. The supported shape is one
contiguous verified window and one leaf invocation. GEN1 declares the
supported x86-64 register and flag domain and refuses anything outside it.
Refusal is the result. It is not a claim that continuation was transparent.

## Out of scope

Provenance data-flow, bounded prestate snapshots, a new raw-trace schema,
Frida Stalker, optional trace backends, function discovery, process-wide
instrumentation, more than one function per session, and code replacement.
