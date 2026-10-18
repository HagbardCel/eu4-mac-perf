# P4 recorder: observation records, evidence store and offline gates

**Status:** complete, merged on `main` as **P4r-A** (PR #61), **P4r-B** (PR #63) and
**P4r-C** (PR #65); the phase's replay release is tagged
`oracle-v2-p4b-20261018`. **P4.1** (the record schema, canonicalization and the
immutable evidence store), **P4.2** (the portable C11 synthetic target with a
deterministic golden transcript), **P4.3** (the native recorder core's exact
capture accounting), **P4.4** (the pluggable capture policy), **P4.5** (offline
finalization: resource identity, aliasing and semantic dedup), **P4.6** (the
resource registry and closure verifier), **P4.7** (concurrency and bounded capture)
and **P4.8** (R0-M epoch compatibility, validated offline) are implemented under
`oracle/recorder/` with offline tests - **P4r-B and P4r-C are complete**. **P4b**
(the independent synthetic graphics replay) is complete too: it reconstructs a frame
from a sealed capture of a synthetic graphics subject, in a fresh process, under
[P4B_REPLAY.md](P4B_REPLAY.md). **P4.9** - bounded resource-content capture and the
versioned observation export - is the recorder's contribution to the pre-P5 offline
integration gate, and is described below and in
[P4_INTEGRATION.md](P4_INTEGRATION.md). **Nothing here launches EU IV, opens a game
process, or attaches to a binary**: the recorder consumes already-captured native
events, and P4 is proven entirely offline.

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
| **P4r-A** (done) | P4.1, P4.2 | versioned schema + evidence store; portable C11 synthetic target | records round-trip; load-time integrity; deterministic native golden output |
| **P4r-B** (done) | P4.3-P4.5, P4.7 | native capture accounting; policy plugin; identity/aliasing/dedup; reentrancy/concurrency + bounded capture | accounting exact; aliases and semantic dedup correct; overflow/reentrancy/concurrency regressions pass |
| **P4r-C** (done) | P4.6, P4.8 | resource registry + closure verifier; R0-M protocol compatibility offline | generations + closure verified; synthetic multi-epoch protocol accepted offline |
| **P4b** (done) | - | independent synthetic graphics replay | a sealed frame replays in a fresh process; missing dependencies fail closed |

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

## The recorder core (P4.3)

`oracle/recorder/native/` is the portable core the harness links the subject
against. It is function-agnostic: it knows only about hooks, and it counts exactly
what it intercepted, selected, captured, left incomplete, and lost.

| Counter | Meaning |
| --- | --- |
| `intercepted` | hook entries observed |
| `selected` | entries the policy chose for deep capture |
| `unselected` | entries counted only (`intercepted - selected`) |
| `captured` | selected entries with a complete observation |
| `incomplete` | selected entries whose declared scope was not observable |
| `lost` | selected entries the bounded capture buffer could not hold |
| `reentrant` | entries observed while already inside an observation |
| `rejected` | instrumentation errors: over-deep, an exit with no matching enter, and malformed declarations (one per observation) |
| `content_bytes_declared` | content bytes a well-formed captured observation declared (P4.9; zero when content capture is off) |
| `content_bytes_captured` | of those, the bytes copied into the arena |
| `content_bytes_lost` | of those, the bytes lost whole for want of arena |
| `content_spans_lost` | the number of spans that did not fit |

Three identities hold by construction and are asserted after every scenario:

* `intercepted = selected + unselected`
* `selected = captured + incomplete + lost`
* `content_bytes_declared = content_bytes_captured + content_bytes_lost`

An **incomplete** entry is a selected observation whose declared scope was not
observable - the call operated on no live resource, so there was no state to
capture. A **lost** entry is a selected observation the bounded capture buffer
could not hold. Loss is reported, never silent: there is no unbounded buffer and no
dropped counter, so the accounting stays exact even when the buffer overflows.
**Reentrant** observation (a hook's capture entering another hook) is detected, and
an exit with no matching enter is **rejected** rather than accepted.

The **hook layer** in `driver.c` stands in for real interposition: it calls
`recorder_enter`, runs the subject, then `recorder_exit`, passing whether the
observation's scope was fully observable. The synthetic target proves recorder
*semantics* - accounting, boundedness, reentrancy - and deliberately does **not**
prove Darwin x86-64 trampoline or calling-convention fidelity, which stays behind
the separate hook-pack gate before P5.

The driver's transcript is a deterministic golden output, like the subject's:
fixed-width arithmetic, no pointers, no clock. `tests/test_oracle_recorder_native.py`
asserts it reproduces byte-for-byte and that the identities hold independently of
the flag the core reports.

## The capture policy (P4.4)

Which hooks are captured in full, and which are counted only, is a **declared,
pluggable descriptor** (`oracle/recorder/native/policy.h`), not a hard-coded list
inside the recorder:

```
recorder_policy { version, default_rule, rule_count, rules[ {hook_id, rule} ] }
```

The recorder is function-agnostic, so the policy names hooks rather than functions
and can be swapped without touching the core. Because two policies qualify the same
behavior differently, a record must name the policy it was captured under, so the
descriptor has a **canonical byte encoding**:

* big-endian fields, and rules **sorted by hook id** - so the bytes depend on the
  policy's meaning, not on the order it was written down;
* the native core emits those bytes; it does **not** hash.

The policy digest (sha256 over the canonical bytes) is computed **offline** - in
`oracle/recorder/policy_reference.py`, a differential *reference* model, not a
second source of truth. The native declaration is authoritative; the reference
re-derives the bytes so a test fails if the two drift. That digest is exactly the
`capture_policy_digest` a P4.1 record carries, which is how two records captured
under different policies are kept distinct even when the observed behavior is
identical.

## Offline finalization (P4.5)

The recorder counts; it does not decide equality. `oracle/recorder/finalize.py`
closes the capture/classification split, offline, over the sealed records a run
produced and the recorder's native counters:

* **resource identity follows the lifetime** (`resource_id@generation`), never the
  handle an observation happened to use - so two handles onto one resource collapse
  to one identity, while a reused slot in a new generation is a different resource;
  the content `version` is kept apart from the lifetime;
* **fully qualified records are classified** into `unique` and `verified_repeat` by
  their `semantic_digest` - canonical semantic equality, never a hot-path byte
  compare. Occurrence metadata (call id, thread, epoch, scenario) is excluded, so
  the *same* behavior observed twice deduplicates; equal arguments over a
  *different* pre-state do not, because the pre-state is part of the projection;
* the **accounting identity** is asserted, not assumed:

  ```
  intercepted = unique + verified_repeat + incomplete + counted_only + lost
  ```

  so a run cannot report "nothing new" while quietly dropping observations.

`classify` refuses a `counted-only` record (such observations exist only as a
counter), refuses a `captured` record that does not qualify for semantic
comparison, and refuses a materialized record that carries sub-observation loss.
The native capture that *populates* these records - reading the state of the
resource an observation addressed - lands in P4.6; P4.5 defines the classification
the capture must satisfy.

## Concurrency and bounded capture (P4.7)

The core is safe under concurrent observation, with no lock on the hot path:

* every counter is a C11 atomic, and the bounded buffer hands out slots with one
  atomic fetch-add, so **loss is exact under load** rather than approximate;
* observation depth, thread identity and sequence are **thread-local**, so one
  thread's nesting and ordering never corrupt another's;
* each captured event records the observing **thread** and that thread's own,
  contiguous **sequence**, plus the declared capture **phase**.

The harness never invents a total order over concurrent events. It declares its
sync edges explicitly (here, one rendezvous between two phases of four threads:
`SYNC barrier=1 participants=0,1,2,3 phase=0->1`), and `oracle/recorder/finalize.py`
reconstructs the **partial** happens-before order those edges and the per-thread
sequences induce: within a thread, consecutive sequences are ordered; across
threads, the barrier orders every phase-0 event before every phase-1 event; two
same-phase events on different threads stay genuinely unordered. The transcript
stays deterministic because it prints accounting and per-thread
`(thread, sequence, phase)` records, never the interleaving, and events are printed
sorted by `(thread, sequence)`.

The thread-safety claim is not asserted from one lucky run: the concurrency
scenarios are deterministic across repeated runs and the build is clean under
**ThreadSanitizer** (zero data races) as well as AddressSanitizer and UBSan.

## Resource closure (P4.6)

The recorder core is function-agnostic, so it cannot know what a resource is. Each
exit therefore carries an **opaque observation context** - declared by the hook layer,
stored verbatim by the core and never interpreted there. For this subject the context
names the resource the call addressed and the slot state that resolves it:

```
[0] ref_valid  [1] ref_slot  [2] ref_gen  [3] live  [4] slot_gen  [5] version
```

`(ref_slot, ref_gen)` is the referenced resource's **lifetime**; `(live, slot_gen,
version)` is that slot as the observation saw it - **at entry** for the
handle-addressing calls, so a destroy's resource is still live and a stale handle's
is not, and after creation for create. `oracle/recorder/registry.py` reads that
context and decides closure:

* a resource is named by its lifetime, `slot:<slot>@<generation>`, never by a bare
  handle;
* a handle whose generation no longer lives in its slot names a resource that is
  **gone**: the observation yields **`INCOMPLETE_ORACLE`** instead of being attributed
  to whatever now occupies the slot. A reused slot never leaks into the observation
  that used the old handle;
* an observation is **closed** when its reference resolves. The lifetimes it could
  not resolve become its `unavailable_dependencies` - exactly the field P4.1 already
  uses to disqualify a record from semantic comparison.

One direction is enforced, not assumed: a **complete** observation must address a
live resource. The converse does not hold - a bad argument on a live resource is
incomplete yet perfectly resolvable.

## R0-M epoch compatibility (P4.8)

Every observation is attributed to the R0-M **epoch** it was captured under.
`oracle/recorder/epoch.py` decodes the R0-M control word (version, mode, scenario id,
generation) **itself**, derives the `provenance.epoch` and `provenance.scenario`
tokens a record must carry, and verifies a multi-epoch capture offline:

* every control word is decodable and generations strictly increase - a word the
  protocol's own reader rejects is never attributed;
* no record is attributed to a **passive** epoch, because passive observes nothing;
* a record's scenario must be the one its epoch declares.

`benchmark/eu4_r0_control.py` is the protocol's implementation, and a test
cross-checks this decoder against it **field by field** over a corpus spanning every
mode and the boundary values, so compatibility is a checked claim rather than a
copied constant. This is the REUSE_MAP's "ADAPT (co-design)" entry made concrete: the
Oracle validates the protocol offline and does not touch R0-M's measurement path.

## Fidelity modes (owned by the reconstruction contract)

The three modes - **faithful**, **GL optimization**, **GPU/backend redesign** -
change what counts as an observable difference, so P4 records *which* policy a
fixture is qualified under without implementing any comparator. The modes are
owned by the reconstruction contract
[`../renderer-reconstruction-and-differential-validation.md`](../renderer-reconstruction-and-differential-validation.md)
(merged in PR #44); the recorder declares them as an opaque, versioned tag and does
not fork a second taxonomy.

## Resource-content capture and the observation export (P4.9)

The recorder as shipped through P4.8 captured resource *references* and lifetimes but
never their **contents**, and its transcript never carried a hook's ABI **arguments**.
Neither is recoverable offline, so no translator could reconstruct an operation's
parameters. P4.9 closes both gaps - see
[P4_INTEGRATION.md](P4_INTEGRATION.md) for the gate they serve and the contracts they
freeze.

`recorder_exit_content` lets a hook declare up to `RECORDER_MAX_SPANS` byte spans,
copied into the recorder's own arena at the moment of exit, and declares *when* those
bytes were valid (`RECORDER_POINT_ENTRY` as the call was entered, or
`RECORDER_POINT_EXIT` after it returned). `recorder_exit` is unchanged and equivalent
to declaring no content and no origin.

* **Whole version, not a fragment.** An observation's spans, concatenated in
  declaration order, are the *complete* contents of one resource state. A partial
  mutation prefix is not content, and a hook that only ever holds a fragment - a read's
  single byte - declares nothing.
* **Every captured byte is attributable.** An observation declaring content also
  declares an **origin**: opaque words naming the state those bytes came from. An
  explicit origin wins; with `norigin == 0` the observation's own context is stored as
  the effective origin. A hook that captured the state it already named need not repeat
  it. Content declared with no usable origin declares no content at all.
* **Malformed declarations fail closed.** One malformed declaration is one
  instrumentation fault, counted once however many symptoms it shows and named by
  `RECORDER_FAULT_*` in a fixed precedence; it declares no content and is never
  complete. A declared argument or context count with **no array** is malformed - a
  missing ABI carrier is never read as an observed zero - while an absent array with a
  zero count stays valid. A *malformed* declaration is judged before its carriers are
  read, so an over-long count never dereferences the array; a valid count's array must be
  readable for its declared length, which is the trusted hook layer's contract, not
  something a pointer can be checked against. The fault is resolved before the policy is
  consulted, so misuse is counted even for an unselected hook.
* **Off by default.** `recorder_config.content_capacity` bounds the arena and defaults
  to zero, which means content is *undeclared*, not *lost*: a hook may name spans and
  the recorder counts and copies none of them. Every golden predating P4.9 is
  therefore byte-identical, and a test asserts it.
* **Whole-span or nothing.** A span that does not fit is lost in full and the
  observation is downgraded from complete to **incomplete** - completeness is decided
  in the core, not by a reader of the export. Partial capture never happens.
* **A failed span wastes no capacity.** `content_reserve` compares-and-swaps a
  monotone high-water mark, and a span that does not fit reserves nothing. The
  arithmetic is provably safe: the mark only advances towards the configured capacity,
  so the sum cannot overflow.
* **A policy choice is not a loss.** Content declared on a hook the policy did not
  select is neither counted nor copied, so it is never misrepresented as a missing
  required span.

The importable artefact is a **versioned observation export** (`RECORDER-EXPORT v1`),
written by `oracle/recorder/native/content_driver.c`: per observation, the hook, its
arguments, its opaque resource context, its declared capture point, its content
inventory, that content's origin and its instrumentation fault, plus a per-scenario
`ADMISSION ok|REJECTED` verdict. Because the export is a *driver-level* contract, the
released accounting transcript is untouched by construction rather than by a
compatibility mode. `harness.parse_export_transcript` reads it, and enforces every
declared cardinality rather than trusting the rendering.

One latent defect was corrected in passing: the accounting driver's `recorder_config`
was written field by field from an uninitialised local, which the new field would have
exposed. It is now fully initialised.

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

## Acceptance (P4r-B and P4r-C: P4.3 - P4.8)

**P4.3 - the recorder core**, exact accounting under disabled capture, a bounded
buffer, and reentrant observation:

* every scenario reports the accounting identity as holding, and the identity is
  re-derived independently from the counters;
* disabled capture counts every interception and selects none;
* enabled capture selects every interception, loses none, and marks as incomplete
  exactly the calls that operated on no live resource;
* a capacity below the number of selected events loses exactly the surplus and
  still balances;
* reentrant observation is detected, and an unmatched exit is rejected.

See `tests/test_oracle_recorder_native.py`.

**P4.4 - the capture policy**, a declared, pluggable descriptor:

* the native canonical descriptor bytes match the reference encoding, and the
  digest is independent of rule declaration order;
* a policy deep-captures exactly the hooks it selects and counts the rest;
* two policies qualify the same behavior differently, and therefore carry
  different digests.

See `tests/test_oracle_recorder_native.py`.

**P4.5 - offline finalization**, identity, aliasing and semantic dedup:

* the same behavior observed twice (different call id, thread, epoch, scenario) is
  one `unique` and one `verified_repeat`;
* equal arguments over a different pre-state are *not* deduplicated, and a
  different function is not the same behavior;
* two handles onto one resource collapse to one identity, a reused slot in a new
  generation does not, and the content version is preserved apart from the lifetime;
* the accounting identity is asserted, a `counted-only` record and a
  sub-observation-lossy record are refused, and a `captured` record that does not
  qualify for semantic comparison is rejected.

See `tests/test_oracle_recorder_finalize.py`.

**P4.6 - resource closure**, the resource registry and closure verifier:

* a live reference resolves to its lifetime, and two aliases of one resource share
  one;
* a reference to a destroyed lifetime, and to a slot reused in a new generation, both
  yield `INCOMPLETE_ORACLE`; an invalid reference names nothing at all;
* a complete observation must address a live resource, while an incomplete one may
  still resolve;
* end-to-end over the recorder's own capture: a fully captured scenario is closed,
  and a scenario with a gone resource plus a reused slot marks `slot:0@0` unavailable
  and `slot:0@1` as a distinct, closing lifetime.

See `tests/test_oracle_recorder_registry.py`.

**P4.7 - concurrency and bounded capture**:

* four threads observing through one recorder account exactly, and a buffer smaller
  than the capture loses exactly the surplus while still balancing;
* every captured event carries a distinct `(thread, sequence)`, and every thread's
  sequence is contiguous;
* the reconstructed happens-before order is **partial** - the declared barrier
  orders phase 0 before phase 1, yet two same-phase events on different threads are
  unordered - and it is acyclic;
* the transcript is deterministic across repeated runs and the build is clean under
  ThreadSanitizer.

See `tests/test_oracle_recorder_native.py`.

**P4.8 - R0-M epoch compatibility**, validated offline:

* the decoder agrees with the R0-M mirror on every word tested, including the modes
  and the words the mirror rejects;
* generations must strictly increase, and an invalid word is rejected, not
  attributed;
* a valid multi-epoch capture is attributed per epoch; a record in a passive epoch,
  naming an unknown epoch, or with the wrong scenario is rejected;
* the epoch tokens are valid record provenance, so the attribution is enforceable
  against the P4.1 schema.

See `tests/test_oracle_recorder_epoch.py`.

**P4.9 - content capture and the observation export**, whole-version, attributable,
fail-closed and default-off:

* the export reproduces its committed golden byte-for-byte and deterministically, and
  the released `synthetic` and `recorder` goldens are unchanged;
* every observation carries its hook, arguments, context, capture point, content and
  content origin, and every scenario an `ADMISSION` verdict;
* captured content is the complete contents of one resource version - the post-update
  state, not the mutated prefix - and nothing at all for a read, whose value stays
  recoverable from the version its context names and its argument offset;
* content is never declared without an origin, and an update's origin names the version
  it produced while its context names the version it addressed;
* every instrumentation fault is exercised; each malformed declaration is exactly one
  rejection, declares no content and is never complete; a declared count with no array
  faults rather than fabricating zeros, a malformed declaration is judged before its
  array is read (an over-long count never dereferences it, even under AddressSanitizer),
  while a valid count's array must be readable for its declared length and an absent
  array with a zero count stays valid; a well-formed declaration with a stale origin is
  exported complete and rejected by the closure verifier;
* the export parser refuses every contradicted cardinality, a repeated or missing
  field, a non-canonical integer, a wrong observation count, a split that disagrees
  with the counters, a duplicated scenario control record, more listed faults than
  rejections, counters that fail a native identity however the accounting verdict is
  labelled, an admission verdict that contradicts the rejections or sits `ok` beside a
  listed fault, and a missing, duplicated or non-terminal `END`;
* a span that does not fit is lost whole, partial evidence survives, the observation
  becomes incomplete, and the byte identity reconciles independently of the event
  identity - including one event that accounts for several lost spans;
* two calls sharing a resource context are distinguished by their arguments;
* a zero-length span declares nothing, `content_capacity == 0` declares nothing, and
  an over-large capacity is rejected rather than truncated;
* no two captured spans share an arena byte under a four-thread race, which fills an
  eight-byte arena exactly.

See `tests/test_oracle_recorder_content.py`.
