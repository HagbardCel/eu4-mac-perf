# P4 recorder: observation records, evidence store and offline gates

**Status:** in progress. **P4.1** (the record schema, canonicalization and the
immutable evidence store) and **P4.2** (the portable C11 synthetic target with a
deterministic golden transcript) are implemented under `oracle/recorder/` with
offline tests; the recorder core follows (P4.3+). **Nothing here launches EU IV,
opens a game process, or attaches to a binary**: the recorder consumes
already-captured native events, and P4 is proven entirely offline.

This document is authoritative for P4. The phase contract is in
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md); the trust boundary is a refinement of the
P3 slice's in [P3_SLICE.md](P3_SLICE.md).

## Purpose

The P4 gate is a **function-agnostic, policy-pluggable recorder plus a synthetic
target**: two functions sharing mutable state; resource create/update/destroy and
handle reuse; aliasing across serialize/replay; equal-args-different-pre-state not
deduplicated; a missing resource yielding `INCOMPLETE_ORACLE`; counters exact under
disabled capture and overflow; reentrancy and concurrency safety; loss reported;
and R0-M protocol compatibility validated offline. **P4b** then reconstructs a
frame from recorded synthetic resources, including resource closure and handle
reuse, using the declared closure method.

## Trust boundary

`oracle/recorder/` runs only **trusted, repository-controlled** programs. The
synthetic target and recorder are built from this repository and executed offline;
application-level isolation is acceptable for that scope **and for that scope
only**. It is not a guarantee for arbitrary agent-generated native code or a real
EU IV instrumentation session, which require their own isolation and are deferred
(see the preserved blueprint's PR-F). No code executed during P4 needs the EU IV
binary or a game process.

## PR structure

| PR | Commits | Scope | Exit criterion |
| --- | --- | --- | --- |
| **P4r-A** | P4.1, P4.2 | versioned schema + evidence store; portable C11 synthetic target | records round-trip; load-time integrity; deterministic native golden output |
| **P4r-B** | P4.3-P4.5, P4.7 | native capture accounting; policy plugin; identity/aliasing/dedup; reentrancy/concurrency + bounded capture | accounting exact; aliases and semantic dedup correct; overflow/reentrancy/concurrency regressions pass |
| **P4r-C** | P4.6, P4.8 | resource registry + closure verifier; R0-M protocol compatibility offline | generations + closure verified; synthetic multi-epoch protocol accepted offline |
| **P4b** | - | independent synthetic graphics replay | a sealed frame replays in a fresh process; missing dependencies fail closed |

Each commit is append-only and separately revertible. P4.1 passes its own gates
before P4.2 begins, so a genuine schema defect is corrected before the native
target depends on the interface.

## The record model (P4.1)

A record is a JSON object with these top-level areas. Raw addresses are retained
only privately, as diagnostics.

| Area | Representation |
| --- | --- |
| Record type | `record_type == "observation"` |
| Provenance | binary/module sha256, function id, callsite, capture-policy digest, scenario, epoch, thread |
| Invocation identity | hook id, call id, parent span, per-thread sequence |
| ABI | calling convention, typed register/stack carriers, return carrier, field confidence |
| State | ordered pre- and post-state byte spans, each with a value, a valid mask and a confidence; declared unknown dependencies |
| Resources | logical identity (`resource_id`@`generation`), content `version`, content digest, alias references |
| Effects | ordered external effects with an effect-completeness flag |
| Completeness | capture state, transport losses, unavailable dependencies |
| Fidelity | declared comparison mode and policy version (an opaque tag; see below) |
| Raw | digest of the content-addressed raw bytes, when present |
| Private | diagnostics (e.g. raw addresses) that never enter semantic equality |

### Two digests

* **Record digest** - integrity over the record's canonical bytes. Defined for
  *any* well-formed record. It answers "are these the same bytes?" and never
  claims observable equivalence.
* **Semantic digest** - equality over the canonical *semantic projection* of a
  **fully qualified** observation. Defined only when the observation is complete
  and canonical; it is the only digest eligible for semantic deduplication.

The semantic projection includes the module and capture-policy identity (behavior
is only comparable within a fixed observation scope), the function identity, the
ABI carriers, the pre/post state, the declared unknown inputs, the resources and
the ordered effects, and the fidelity policy. It **excludes** occurrence metadata -
the callsite, scenario, epoch, thread, invocation identity and per-thread
sequence, the raw blob digest, and the private diagnostics - so the *same*
behavior observed twice, at a different time or on a different thread, deduplicates.

### Three rules

1. **`semantic_digest` only for fully qualified observations.** An incomplete,
   lossy or dependency-unavailable record may be integrity-checked, but it can
   never prove or disprove observable equality, so it is never semantically
   deduplicated.
2. **An unknown byte is masked, never zero-filled.** A span carries a value and a
   valid mask of the same length; a known zero and an unknown byte are different
   observations, and an unknown byte's contents never reach semantics.
3. **Lifetime is distinct from content.** `resource_id`@`generation` identifies a
   lifetime; the `version` identifies the resource's state in that lifetime. Both
   are preserved.

## Canonicalization

Canonical form is key-sorted JSON, no insignificant whitespace, no NaN/Infinity,
and lowercase hex for bytes, so the native encoder and this reader agree. The
canonicalization version is pinned (`eu4-oracle-canon/v1`) and carried in the
sealed envelope. There is no earlier record version yet, so no migration path is
defined; an unsupported `schema_version` is rejected outright, and the migration
mechanism is reserved for the first genuine transition rather than exercised with
a dummy legacy schema.

## The evidence store (P4.1)

`oracle/recorder/store.py` is a small, **independent** content-addressed store.
It does not reuse the P3 run-receipt directory as an event CAS. It adapts the
create-only atomic-publication discipline already proven in
`reverse_engineering/renderer_extract.py` - write a private staging file, then
`os.link` it into place, which cannot overwrite an existing name - and pairs it
with the record schema's canonical hashing.

A manifest references every blob by digest. Loading a store validates the
manifest, and `verify()` is the load-time integrity gate: every referenced blob
must exist, match its digest and size, and (for records) re-validate against the
schema. A tampered, truncated or falsely-referenced blob is caught at load time
rather than trusted.

## The synthetic target (P4.2)

`oracle/recorder/synthetic/` is the *subject*: a small, portable C11 library
(`synth.h`, `synthetic.c`) with a deterministic scenario driver (`driver.c`). It
models the observation problems the recorder must handle:

* **two operations sharing mutable state** - `synth_update` and `synth_read` both
  traverse the same resource table, and a read observes a write made through
  another handle;
* **create / update / destroy with handle reuse** - a destroyed slot is reused by
  the next `synth_create` with an advanced generation, so old handles stay invalid;
* **aliasing** - `synth_share` copies a live handle, so two handles address one
  resource;
* **pre-state dependence** - each byte advances relative to what is already there,
  so equal `(seed, span)` arguments over different pre-state give a different
  result (equal arguments are not equal behavior);
* **a bounded recursive traversal** - `synth_fold`; and
* **an ordered external-effect stream** - the library calls the trusted sink
  `synth_effect`, which the driver now - and the recorder in P4.3 - implements.

`driver.c` runs a fixed script and prints one canonical line per step:
`SYNTH v1`, interleaved `EFF` effects, `CALL` results, `DATA` byte spans, and a
final `END` line of counters. The transcript is the **native golden output**: it
depends only on C semantics - fixed-width unsigned arithmetic and explicit byte
encoding, no pointers, no clock, no locale - so gcc on Linux and clang on macOS
produce it byte-for-byte. `oracle/recorder/harness.py` copies the trusted sources
into a private directory, compiles them (`-std=c11`), runs the target and returns
the transcript; `tests/test_oracle_recorder_synthetic.py` asserts it reproduces
the committed `golden.txt` exactly and that the transcript actually exhibits each
property above.

## Fidelity modes (owned by PR #44)

The three modes - **faithful**, **GL optimization**, **GPU/backend redesign** -
change what counts as an observable difference, so P4 records *which* policy a
fixture is qualified under without implementing any comparator. The modes are
owned by the reconstruction contract `docs/renderer-reconstruction-and-differential-validation.md`
(see [open PR #44](https://github.com/HagbardCel/eu4-mac-perf/pull/44), not yet on
`main`); the recorder declares them as an opaque, versioned tag and does not fork a
second taxonomy.

## P4r-A acceptance (P4.1 - P4.2)

**P4.1 - the record model and store**, offline, adversarial, independent of any
native code:

* a well-formed record seals and verifies; canonical bytes are key-order
  independent; non-finite values are rejected;
* an unsupported schema version, an unknown enum, a bad hex field, a boolean where
  an integer is expected, and a value/mask length mismatch are all rejected;
* a record that is `incomplete`, `counted-only`, lossy, dependency-unavailable or
  with an incomplete effect log is refused a `semantic_digest` while keeping its
  `record_digest`;
* a known zero and an unknown byte are distinct, and an unknown byte's contents do
  not change semantics; and
* a resource's lifetime and its content version are distinguished.

See `tests/test_oracle_recorder_schema.py` and
`tests/test_oracle_recorder_store.py`.

**P4.2 - the synthetic target**, deterministic native golden output:

* the committed transcript reproduces byte-for-byte across runs and compilers;
* the transcript exhibits handle reuse with an advanced generation, a rejected
  stale handle, aliasing across two handles, equal arguments over different
  pre-state yielding different bytes, bounded recursion, an ordered and complete
  effect stream, and exact counters.

See `tests/test_oracle_recorder_synthetic.py`.
