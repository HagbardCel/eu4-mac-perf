# Function package — `_GfxForceWireframe`

This directory is the function package for `_GfxForceWireframe` only. It is the
G1 hand-off from the RE0 contract to a later reconstruction and replay adapter.
It follows the first function's split (a contract plus an instruction inventory)
and adds the hand-off sections G1 requires. It is not a schema for a third
function.

The behavioural contract, the gate verdict, and the rejected candidates are in
[`contract.md`](contract.md). This file does not restate them.

**RE0→RE1: PASS.** The four written criteria and the evidence for each are in
the contract section *Written gate, criterion by criterion*.
**G2 has not started.** There is no RE1 source, no P-code pilot, no harness,
and no replay adapter in this directory.

## What this package is

| File | Role |
| --- | --- |
| [`contract.md`](contract.md) | Pinned identity, RE0 contract, layout, dependency map, callers, gate |
| [`instructions.json`](instructions.json) | Instruction inventory in the existing `eu4-re0-instruction-inventory/v1` shape |
| `package.md` | Fixture plan, evidence tier, harness plan, A2 decision, G2 work |

`reconstruction/`, `pilot/`, and `differential/` exist for
`_GfxSetConstantBuffer` because that function continued past RE0. They are
absent here on purpose.

## Evidence tier

Claims in the contract are static sealed-snapshot disassembly, byte-checked
against the pinned binary. That is the evidence this package actually holds.

Three named tiers are unacquired for this function:

- Ghidra P-code execution (the tier of the first function's offline pilot);
- native original-code execution;
- live game observation.

The local decompiler line cited in the contract is a hypothesis. No fixture
file is committed. The plan below describes synthetic constructed state. It is
a plan, not a capture, and it must stay labelled synthetic until a P-code or
stronger reference exists.

## A2 closure — add a small branch

**Decision: add a small per-function closure branch. Do not reuse the
`_GfxSetConstantBuffer` closer. Do not implement the branch in G1.**

`oracle/function/fixture.py` loads one descriptor,
`descriptors/gfx_set_constant_buffer.json`, and closes one domain: a handle at
`RDI`, an observation window on `*RDI`, the `R9D == 0xffffffff` buffer read,
three stores, and `RAX == *RDI`. `P4_INTEGRATION.md` records that A2 is not a
descriptor framework and that a second function may add its own small closure
branch.

This function's domain is different. `RDI` is the object. `SIL` is the stored
byte. The only store is `[RDI + 0x194]`. There is no slot, no buffer read, and
no dirty flag. Pointing the existing closer at this symbol would apply the
constant-buffer domain to a different store.

The new branch, when it exists, has to leave the constant-buffer rules
unchanged. `oracle/function/**` is frozen for G1 and is not edited here.

## Harness plan

A per-function harness, not
`analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/harness.cpp`.

Planned behaviour, for G2 to implement:

- Build the constructed object from the fixture plan.
- Call the RE1 body with `RDI` = object base and `SIL` = the case byte
  (System V).
- Report the byte at `+0x194`, the canary bytes, and the entry/exit `RAX`.
- Leave the constant-buffer report fields (`word_count`, `dirty`, `buffer`,
  `size=0x130`) unused. Those fields describe a different domain.

If the host can map guard pages, the harness may guard the bytes outside the
canary band so a walk off `+0x194` faults. The original body has no such walk.
The guard would be a harness check.

## Fixture plan

Synthetic constructed state. Not acquired. No live capture.

- **Subject byte:** `[object + 0x194, object + 0x195)`.
- **Canaries:** one byte at `+0x193` and one byte at `+0x195`. G2 may widen
  that band. Widening it does not widen the function's own store.
- **Cases to include:**
  - `SIL = 0x00` and `SIL = 0x01`, the values evidenced at the seven caller
    sites in the contract;
  - `SIL = 0xff`, because `MOV r/m8, SIL` stores the low byte and those sites
    do not prove a mask;
  - a pre-state byte different from `SIL`, so the store is visible.
- **`RAX`:** record entry and exit. The contract says this body does not write
  it. That record is an observed outcome.
- **Reference, when acquired:** Ghidra P-code execution of the pinned 13 bytes
  under that constructed state, held to `body_sha256` in `instructions.json`.
  Same tier as the first function's pilot. The state is synthetic. A live EU IV
  session is not part of this plan.
- **Qualification:** a row counts only after that reference exists. Agreement
  between a reconstruction and this prose contract is not a qualified
  reference.

## Replay-adapter work G2 must complete

`oracle.function.replay` supports `function_id` `gfx:set-constant-buffer` and
symbol `_GfxSetConstantBuffer`. Any other descriptor is refused with
`reference:unsupported-function`. This package does not claim that replay
supports `_GfxForceWireframe`.

G2 is not started. The work it has to finish:

1. **RE1.** A faithful x86-64 C++ body that stores `SIL` at offset `0x194`,
   plus an ABI adapter and a layout check for that one offset. Written from
   [`contract.md`](contract.md). Decompiler output stays out of the tree.
2. **Harness.** The per-function harness above.
3. **Reference.** An offline P-code (or stronger) reference under the fixture
   plan, pinned to the body digest. Until it exists, there is no behavioural
   oracle for this function.
4. **A2 branch.** The small closure branch above, so a transcript can close on
   this domain. It is a prerequisite of a qualified fixture. It edits
   `oracle/function/**`, which G1 does not touch, and it must not change the
   constant-buffer descriptor.
5. **Replay adapter.** A per-function adapter that consumes a capture closed by
   that branch and compares the subject byte and the canaries. It does not
   change the constant-buffer comparison window `[ctx+0xb8, ctx+0x130)`.
6. **RE2 code-window pin, later.** `code_windows` is not an RE0→RE1 criterion.
   `target.json` at the first function's RE0 commit (`5fdc728`) had no such
   key; `f4fce9c` added it so RE2 can authenticate emulated instruction bytes.
   This package pins the image with the existing SHA-256 and pins the body in
   `instructions.json`. It does not edit `target.json`. A later stage that
   needs `code_window("_GfxForceWireframe")` has to propose that amendment on
   its own.

## Limits

Authoritative list: the contract's "Not established" section.

- Static evidence only. P-code, native, and live tiers are unacquired.
- Replay does not support this symbol.
- Object type, object lifetime, and caller thread are unknown.
- `target.json` was not modified.
- Rejected candidates are recorded in the contract. Full contracts for them
  were not written.

## Checks

Recorded 2026-10-25 on `feature/oracle-g1-second-function-re0`.

Ran:

```sh
python3 -m unittest tests.test_reconstruction_contract -v
```

Result: 6 tests, OK. Those tests read
`analysis/reconstruction/_GfxSetConstantBuffer/` only. They meet none of the
four RE0→RE1 criteria for `_GfxForceWireframe`. The local snapshot and the
pinned binary were present, so that checker's own byte tests ran.

The byte evidence for this function's callable-boundary criterion is a
separate local check: `assert_pinned_executable` and `verify_static_row_bytes`
on the `gfx-batch-00044.zip` row at entry `0x15ed175`, result `(5, 13)`, body
SHA-256 `45957994d8a114d2be65e430e66d98c04f4efa818180cbc1fe274fb75b3b3dae`.
That check is not a committed test. `tests/` is outside this package.

Skipped, because they belong to the first function's later stages and do not
read this symbol:

- `tests/test_reconstruction_pilot.py`
- `tests/test_reconstruction_differential.py`
- `tests/test_reconstruction_faithful.py`
- `tests/test_oracle_function_fixture.py`
- `tests/test_function_replay.py`

No P-code emulator was started. EU IV was not launched.
