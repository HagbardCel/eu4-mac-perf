---
package: GEN3
upstream: GEN2 execution-observation/v1
contracts:
  - trace-target/v1
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/tr3/run.py
  - tests/test_trace_tr3.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - docs/oracle/GENERIC_TRACE_V1.md
  - reconstruction_trace/schema.py
  - reconstruction_trace/fixture.py
  - reconstruction_trace/tr3/project.py
  - reconstruction_trace/tr3/native.py
  - reconstruction_trace/tr3/gfx.py
  - reconstruction_trace/tr3/adapter.py
  - reconstruction_trace/tr3/insert.c
  - reconstruction_trace/tr3/insert_stub.S
  - reconstruction_trace/tr3/startup.py
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# GEN3 — first generic EU4 capture

## Deliverable

One readiness record for `_GfxSetConstantBuffer`, then a launcher repair that
can run only after this work order merges. Merging GEN3-A authorizes that
repair. It does not authorize an EU4 launch. GEN3-C is one later operator
authorization after both merge and this review still holds.

The repair touches only the owned paths. No new module, C, assembly, decoder,
budget parameter, or call to `project` from the launcher.

## Readiness

The pin in `reverse_engineering/target.json` and `run.py` is file
`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`, entry
`0x15ec36a`, exclusive end `0x15ec3b5`, window
`8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943`, 75 bytes.
Preferred image base is `0x100000000`.

`analysis/reconstruction/_GfxSetConstantBuffer/instructions.json` has 21
instructions, every `call` false. Mnemonics are `PUSH MOV CMP JZ LEA ADD TEST
CMOVNS SAR JMP SHR SHL POP RET`. The graph is acyclic. The inventory prints
branch targets as preferred-image addresses (`JZ 0x1015ec38d`, `JMP
0x1015ec398`): image base `0x100000000` plus RVAs `0x15ec38d` and `0x15ec398`.
Both go forward. The taken branch is 15 instructions. The fall-through is 18.
Neither path revisits an instruction or approaches the 64-instruction ceiling.
No AVX in this function is not proof that surrounding machine state is
preserved, and it is not a Rosetta proof. The controlled-host continuation
test does not prove transparent continuation inside EU4.

Budgets are `startup.CEILING`: 64 instructions, 256 accesses, 65536 bytes.
`startup.envelope(75)` is 1387 bytes. Overflow stays incomplete.
`TR3_LEGACY_REGIONS` stays unset. GEN2 copies only regions and pointer edges
present in the raw record.

## Live path

Written here. Not changed in C.

`authenticate()` checks the file hash, window hash, and mapped bytes before
the 5-byte `rel32`. The displacement is range-checked. A failed install
restores and checks the bytes, or `fail()`s. It does not continue on a
silently changed entry.

Continuation is GPR, `eflags`, and `fxrstor64` in `insert_stub.S`. YMM state
above that image is not restored and is not a GEN3 claim.

`tr3_insert_dispatch` sets an internal `natural_return` only when `rip` is
the saved return slot and `rsp` is entry `rsp+8`, with no boundary reason.
That boolean is not a raw JSON field. `capture_complete()` folds it into
`run.execution_complete`. A rejected boundary publishes and exits. It does
not resume.

The 120-second limit is the wait for a sealed capture. Child termination adds
up to two 30-second waits. Compilation is outside that timer. There is no
hard total wall-clock limit.

The dylib writes `insert.json.tmp` and renames it. A missing, unsealed, or
wrong session or pid log is not this launch.

The 5-byte patch is not atomic with respect to other EU4 threads. The
first-invocation hook limits repeated interception. It does not remove a
concurrent-entry race. That race is an accepted experiment risk, not a
demonstrated safety property. A failed restore still exits rather than
continuing on a torn entry.

Stop, and do not authorize GEN3-C, on an unsupported instruction, an
unbounded capture wait, or a patch failure that would continue execution.
The accepted thread race is not that stop.

## Launcher repair

After this work order merges, `run.py` may:

1. Build one `trace-target/v1` descriptor from the pin, `hex(image_base)`,
   the one range, `first-invocation`, and a copy of `startup.CEILING`.
   `startup.check_descriptor` raises; catch `RuntimeError`, `KeyError`, and
   `TypeError` and exit nonzero before `startup.build` or `Popen`. Refuse
   when `max_bytes` is below `startup.envelope` of the 75-byte window. Do not
   compare the copied ceilings with themselves. The dylib still enforces its
   compiled ceilings.
2. Keep `image.check` in `main()`. Inside `launch_live`, reuse `decide()`
   for file hash, window hash, entry, and end. A pin match is the existing
   unauthorized return. Also require the pinned image base and that the
   passed window equals the window on the target. That guard does not hash
   the executable.
3. Copy `os.environ`, drop inherited `TR3_LEGACY_REGIONS`,
   `TR3_CHECK_SYMBOL`, `TR3_SYMBOL`, and `TR3_MAX_*`, then set the pinned
   identity, entry, window length, log, session, dylib, and the three
   ceilings. Do not call `startup.target_env`. It forces a symbol check.
4. Leave session matching, the sealed log, the 120-second capture wait,
   owned-child stop, and `gfx.interpret` as they are. Do not import `project`.
5. `captured_complete` only when `run.execution_complete` is true and the
   interpreted observation is complete. A missing flag stays incomplete.
6. Store that descriptor on the launcher output. Do not alter the sealed raw
   record.

`tests/test_trace_tr3.py` stays one module. No EU4 process in tests.

## One session

Not this work order. One launch, one function, the first invocation only. No
retry. Offline, call `project(raw, descriptor, synthetic=False)`. On
`ProjectError`, record the refusal and do not call `fixture.qualify`. On
success, qualify that observation. Keep the `gfx.interpret` result beside
the projection. If they disagree, record both.

A true `run.execution_complete` entails the internal `natural_return` check.
GEN2 does not certify it. Keep entry and exit RIP and RSP. They are not a
second certificate.

Success requires a sealed session and pid match, `proc_translated == 1`, the
existing `hook_gap()` attestation, native `execution_complete`, a GEN2
observation, and a stopped child. A crash or forced kill after a sealed
capture is not continuation success.

The receipt, written only after the session, is
`docs/oracle/receipts/gen3-gfx-set-constant-buffer.md`. Record target
identity, process identity, hook status, native execution, trace size,
projection, replay only when projection returned an observation, and the
missing addresses, ranges, initial bytes, and pointer relationships. Do not
invent them.

Replay qualification is not required. A valid replay-incomplete observation
is success. Qualification as well is beyond the requirement. No log, a
failed injection, an unreachable `rel32`, a hook gap, QBDI never entering,
or an incomplete native execution is not success.

## Non-goals

No edit to the frozen paths. No memory inventory, pointer-edge inference, or
second raw schema. No launch path in `benchmark/eu4_gfx_live_capture.py`.

## Stopping rules

- **Contract conflict:** a change to `execution-observation/v1`, a second raw
  schema, or a weaker `fixture.qualify()` stops for an amendment.
- **Scope expansion:** a file not named above, a live EU4 run inside the
  repair, or a generic inventory stops.
- **Evidence limitation:** an inferred region or pointer is not observed.
  Native execution completeness is not fixture qualification.

## Merge gate

This file and the authorization paragraph are the GEN3-A gate. The repair's
gate is the owned tests, with zero unexpected skips and no EU4 process.
Explain more than 160 nonblank handwritten lines in the repair. More than
200 needs a named correctness requirement or a failed acceptance test.
