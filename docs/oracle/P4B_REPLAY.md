# P4b: independent synthetic graphics replay

**Status:** complete. **P4b** replays a *sealed capture* of a synthetic graphics
subject — a small portable C11 library under `oracle/replay/synthetic/` — in a fresh
process, and reconstructs the frame the subject presented. Nothing here launches
EU IV, opens a game process, or attaches to a binary; the replay's only input is a
file.

This document is authoritative for P4b. The phase contract is in
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md); the capture/closure machinery it consumes is
in [P4_RECORDER.md](P4_RECORDER.md).

## The gate

> Reconstruct a frame from recorded synthetic resources including resource closure
> and handle reuse, using the declared closure method. A sealed frame replays in a
> fresh process; missing dependencies fail closed.

Each clause is a checked claim:

| Clause | How it is met |
| --- | --- |
| *from recorded synthetic resources* | The replay reads only a sealed capture of the resource timeline. It never links the recorder, never sees the original process and never re-runs the subject. |
| *resource closure and handle reuse* | A resource is named by its **lifetime**, `slot:<slot>@<generation>`. The subject destroys a vertex buffer and reuses its slot in a new generation; both lifetimes are captured and distinct. |
| *the declared closure method* | References resolve by the rule P4.6 declares. `oracle/replay/capture.py` calls `registry.lifetime_name`, so there is one naming rule, not a lookalike. |
| *a fresh process* | `python -m oracle.replay <capture>` reconstructs the frame as its own process from a file alone. A test runs exactly that and asserts the frame. |
| *missing dependencies fail closed* | A reference the capture does not hold raises `IncompleteOracle` and the process exits non-zero — it never yields a plausible *different* frame. |

## The subject

`oracle/replay/synthetic/gfx.{h,c}` models the *shape* of a graphics subsystem, not
any real one:

* typed resources — textures and vertex buffers — addressed by handles
  `(slot, generation)`, with create, destroy and slot reuse across generations;
* four byte-level draw operations — `fill`, `blit`, `mix` — applied to a target
  texture, and `present`, which digests the target.

Every operation is integer-only and every byte it writes is written explicitly, so
the frame is a pure function of the captured contents and the operation timeline.
That is what makes an independent reconstruction possible at all: nothing depends on
a pointer, a clock, a locale or the host word size. `gfx_effect` is the trusted
effect sink, implemented by the driver.

The driver's frame script exercises the gate's two hard cases in one run: it destroys
the vertex buffer and re-creates it in the **same slot** with a **new generation**,
then blits from the new lifetime. An old handle to the destroyed lifetime must never
resolve again.

## The capture

`oracle/replay/synthetic/golden.txt` is the committed golden capture transcript — the
driver's canonical record of the whole frame:

```
CREATE slot=0 gen=0 kind=1 size=16 result=0 contents=000102…0f
FILL   slot=0 gen=0 offset=0 span=16 pattern=3 result=0
PRESENT slot=0 gen=0 result=0 digest=6045a395 bytes=0304…
…
DESTROY slot=1 gen=0 result=0
CREATE slot=1 gen=1 kind=2 size=8 result=0 contents=d0d1…d7
BLIT   slot=0 gen=0 offset=0 source_slot=1 source_gen=1 result=0
PRESENT slot=0 gen=0 result=0 digest=26dc49d4 bytes=020304054446404e…
END creates=4 destroys=1 ops=6 effects=13
```

It contains no pointer, no address, no clock and no process identity, so a frame can
be reconstructed elsewhere. `parse_frame_transcript` turns it into the capture
document; a capture that recorded a **failed** operation is rejected rather than
replayed.

## Sealing

A capture is sealed before it is trusted:

* `seal` wraps it with a digest over its **canonical** bytes, reusing the recorder's
  canonicalization — one canonicalization, not two;
* `write_sealed` publishes it with the create-only discipline the evidence store
  adapted, so a sealed capture is never silently overwritten with different content;
* `read_sealed` verifies the structure, the digest **and** that the file is
  byte-for-byte the canonical form, so tampering is detected at load.

## The replay

`replay` walks the timeline once, maintaining the registry itself: it resolves each
reference against the lifetimes it has reconstructed, applies the operation, and at
`present` compares the reconstructed frame to the captured one **byte-for-byte**. A
frame that digests differently is a failure, not a pass — the replay refuses to
return a plausible wrong frame.

Because the replay reimplements the declared semantics of each operation instead of
calling the subject, the match is evidence about the *capture*, not about the code
that produced it: the frame survives leaving its process.

## Acceptance (P4b)

* the graphics subject reproduces its golden capture byte-for-byte; the transcript is
  deterministic across 25 runs, identical at `-O0`/`-O2`/`-O3`, warning-free under
  `-Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion`, and clean under
  ASan + UBSan;
* the replay reconstructs the recorded frame — digest `26dc49d4`, bytes
  `020304054446404e9c0e0f100f101112` — and is deterministic;
* a destroyed generation and a reused slot are **distinct** lifetimes, and the reused
  one closes;
* every removal of a dependency the frame needs yields `INCOMPLETE_ORACLE`: a missing
  `create`, a stale reference to the destroyed generation, and a reference to a
  lifetime that was never created. None of them yields a different frame;
* a sealed capture round-trips, re-publication refuses different content, and a
  tampered payload, a tampered digest and a non-canonical file are each detected at
  load;
* the replay reproduces the frame in a **fresh process** from the sealed file alone,
  and exits non-zero with an `INCOMPLETE_ORACLE` diagnostic when a dependency is
  missing.

See `tests/test_oracle_replay_frame.py`.

## What P4b is not

The subject is a model, so P4b proves the **capture and replay discipline** — closure,
lifetime identity, sealing, fail-closed reconstruction — not the behaviour of EU IV's
engine or of any GPU API. Darwin x86-64 interposition fidelity remains behind the
separate hook-pack gate before P5.
