# The pre-P5 offline integration gate: content capture, materialization and declared translation

**Status:** in progress. **P4.9** (bounded resource-content capture in the native
recorder core, and the versioned observation export) is implemented and merged. The
**function** half of **P4.10** is split into **A1** (export → sealed records and
immutable whole-version blobs, native/semantic completeness reconciliation,
collision-safe classification and a verified capture binding) and **A2** (the
`_GfxSetConstantBuffer` observable-domain descriptor, descriptor-driven raw-memory
closure, the versioned `FUNCTION-CAPTURE v1` transcript and a synthetic paired-state
producer). **P4.11** (declared translation, `gfx-v2` stateful replay and negative
admission) and the frame half of P4.10 remain. **Nothing here launches EU IV, opens a
game process, or attaches to a binary**: every input is a file this repository's own
drivers wrote.

This document is authoritative for the pre-P5 offline integration gate. The gate now
splits by acquisition scope (2026-10-18): the **function** half (**P4.9** +
function-oriented **P4.10** + domain-specific negative admission) authorizes **P5a**, and
the **frame** half (frame-oriented **P4.10** + **P4.11**, or a qualified alternative)
authorizes **P5b**; the phase contract is in [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md). The
recorder it builds on is in [P4_RECORDER.md](P4_RECORDER.md); the synthetic replay it must
generalize is in [P4B_REPLAY.md](P4B_REPLAY.md).

## Why this gate exists

P4b's synthetic replay is **not** this gate. P4b's subject emits its own operation
transcript through its own hook layer and supplies resource contents itself, so a
passing P4b shows that a complete transcript *can* be replayed — not that the
**generic P4 recorder** can produce an independently replayable frame. The P4 recorder
captured resource *references* and lifetimes, not resource contents; and its
transcript carried counters and per-observation identity, but never a hook's ABI
**arguments**, so no translator could recover an operation's parameters.

Both gaps are closed by P4.9, and both are prerequisites for the rest of the gate.

## The gate

> Establish, entirely offline, that the recording and materialization pipeline works end to
> end on a sufficiently representative synthetic workload — **at the scope of the acquisition
> it authorizes**.

The gate splits by acquisition scope; [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) is authoritative
for the phase names and the launch authorization. The **function gate** authorizes **P5a**;
the **frame gate** authorizes **P5b**. Frame replay, `gfx-v2` translation and frame-scoped
resource/state closure belong to the **frame gate only** — the function gate never requires
them.

| Requirement | Necessary demonstration | Slice | Gate |
| --- | --- | --- | --- |
| Resource-content capture | The native recorder records initial contents and relevant mutations, not just IDs | P4.9 | function + frame |
| Identity and versioning | Reused handles resolve to the correct generation and resource version | P4.9 / P4.10 | function + frame |
| Complete command stream | Native event accounting reconciles with the captured operations | P4.10 | function + frame |
| Materialization and blob closure | Observations materialize to schema-valid records and immutable blobs; every declared reference resolves over the whole store | P4.10 | function + frame |
| Collision-safe classification | Canonical equality is decided within hash buckets; collisions are counted and observable | P4.10 | function + frame |
| Function-scoped closure and negative admission | Missing required inputs, snapshots or state prevent qualification of the declared **function** observable domain; removing content, a mutation or a required observation prevents it too | P4.10 | **function** |
| Resource/state closure | No unresolved dependencies for the declared synthetic **frame** boundary | P4.11 | frame |
| Independent replay | A fresh process reconstructs the frame from the native recorder's sealed output | P4.11 | frame |
| Frame negative admission | Removing content, a mutation or a required event prevents **frame** qualification | P4.11 | frame |

## Two gates, and why P5 is not circular

The hook-pack requirement splits, and conflating the halves would create a circular gate:

* **Offline, before the matching acquisition:** the scope's half of the pipeline above, plus
  the *static* half of the hook-pack checks — executable-byte identity against the pinned EU
  IV identity in `reverse_engineering/target.json`, and site validation. These read files and
  need no game process. The **function gate** needs only function-scoped materialization,
  closure, accounting and negative admission; frame replay and `gfx-v2` are the **frame
  gate**'s business.
* **In P5a/P5b — a runtime admission experiment:** whether the instrumentation actually
  behaves safely inside EU IV, and whether the intended real evidence can be acquired. This
  is a carefully bounded, operator-authorized experiment with fail-closed abort conditions. It
  cannot be a precondition for the first authorized launch, because it can only be answered
  *by* one.

So `Hard rule` bars a launch until the **applicable** offline half-gate and the static hook
checks pass; it does not demand successful live behavioural qualification before the launch
that would produce it. P5a and P5b begin without assuming that the live hooks, the function
fixture or frame closure are already qualified.

## Frozen interfaces (P4.9)

These are the contracts P4.10 and P4.11 build on. P4.9 freezes the *interfaces*; it
deliberately does not implement the materializer, the translator or the `gfx-v2`
interpreter.

### 1. What captured content *is*

Two rules make captured content trustworthy to anything downstream, and both are
frozen here:

1. **An observation's declared spans, concatenated in declaration order, are the
   complete contents of exactly one resource state.** Never a fragment of one: a
   partial mutation prefix is not content. A digest of an exported blob is therefore a
   digest of a whole resource version, which is what P4.10 publishes as that version's
   `contents_sha256`.
2. **Every captured byte is attributable.** An observation that declares content also
   declares an **origin**: opaque words naming the resource state those bytes were
   copied from. An observation that declares no content need not invent one.

Consequently a hook that only ever holds a *fragment* declares no content at all.
`hook_read` is the case in point: the single byte it observed is not a resource
version, and its value stays recoverable from the version its context names and the
offset in its arguments. Recording that byte as content would publish a one-byte
fragment under a whole-version contract. Under the synthetic subject's hooks:

| Hook | Declared content | Origin |
| --- | --- | --- |
| create | the whole initial contents | defaults to the post-create context |
| update | the whole **post-update** contents | explicit: the version it produced |
| pair | two ordered halves covering the whole version | defaults to the entry context |
| read | **none** | none |
| destroy | **none** | none |

"Declares no explicit origin" is not "declares no content". Only `read` and `destroy`
declare zero resource-content spans; `create` and `pair` declare a complete version and
let their context supply the provenance.

### 2. The native content-span API

```
void recorder_exit_content(token, result, complete, context, ncontext,
                           const recorder_span *spans, uint32_t nspans,
                           uint32_t point,
                           const uint64_t *origin, uint32_t norigin)
void recorder_exit(...)   /* unchanged; equivalent to no content and no origin */
```

* `point` ∈ {`RECORDER_POINT_ENTRY`, `RECORDER_POINT_EXIT`} declares whether the bytes
  are the state the call **saw on entry** or the state it **left behind**. The hook
  layer owns this declaration; it is the *trusted hook's* contract, and it does not
  make copying arbitrary process pointers safe.
* `origin`/`norigin` uses the same word layout as `context` (the subject's
  `SYNTH_CONTEXT_WORDS`, 6 words here). The core stores it verbatim and never
  interprets it; it validates only the array's structural bounds. An explicit origin
  wins; when `norigin == 0` the observation's own context is stored as the effective
  origin. The core is function-agnostic, so the *meaning* of those six words — and
  whether they name a live resource — is the offline verifier's check, not the core's.
* A span of `length == 0` declares nothing. A span that does not fit is lost **whole**,
  never partially captured.
* Spans are copied into the recorder's **own** arena, bounded by
  `recorder_config.content_capacity` (≤ `RECORDER_MAX_CONTENT_BYTES`, 1024) and
  **off by default**. A capacity of zero means *undeclared*, not *lost*: a hook may
  name spans and the recorder counts and copies none of them. That is what keeps every
  existing golden exact.
* At most `RECORDER_MAX_SPANS` (4) spans per observation.

### 3. Malformed declarations fail closed

A hook can misdeclare itself. The core resolves one malformed declaration to exactly
one instrumentation fault — **counted once, however many symptoms it shows**, named by
the first cause in a fixed precedence — and such an observation **declares no content
at all and is never complete**. Voiding the declaration is deliberate: nothing is
counted and nothing is copied, so no consumer can mistake a prefix of a malformed
declaration for the whole, and `content_bytes_lost` keeps meaning *budget* loss only.

| `fault=` | Meaning |
| --- | --- |
| `none` | well formed |
| `args` | an argument count declared with no array, or more arguments than the policy allows |
| `context` | a context count declared with no array, or more context words than the recorder can record |
| `point` | a capture point that is neither entry nor exit |
| `spans` | a malformed span list: too many, or absent while spans are declared |
| `bytes` | a span declares bytes that are not addressable |
| `origin` | content declared with no usable origin |

A declared count with **no array** is malformed, not an empty one: a missing ABI
carrier must never be read as an observed zero, and an unavailable context must never
be read as absent-but-fine. The complement is deliberate — an *absent* array with a
zero declared count is the legitimate empty case and does not fault.

A malformed declaration is judged **before** any of its carriers are read: its declared
count is not trustworthy, so an over-long count never reads past the array the hook
supplied, its diagnostic argument slots are zero, and it remains a fault, not evidence.

A *valid* count is different, and the distinction is the trusted hook's contract. The
core copies exactly the declared `nargs` elements, so those elements must be readable —
the core cannot know an allocation's length from a pointer, exactly as it cannot for a
`context` or a content span. Supplying memory valid for a declared, permitted count is
the hook layer's responsibility, and no runtime pointer check exists that could make it
the core's.

Precedence is the order above. The fault is resolved **before** the policy's selection
is consulted, so a malformed declaration is counted whether or not the policy chose to
observe it — instrumentation misuse is not a policy outcome.

A *well-formed* declaration whose origin is structurally valid but semantically wrong
(a stale generation naming a reused slot) is **not** a core fault: the core cannot know
which resource a subject's origin words mean. It is exported `complete=1` and rejected
by the offline closure verifier, which is the fail-closed direction for it.

### 4. Admission

`ADMISSION ok|REJECTED` is exported per scenario and is the explicit signal P4.10's
admission gate consumes. It is `REJECTED` exactly when unresolved instrumentation
faults were counted (`rejected > 0`).

Bounded loss is **not** a fault. It leaves its observation incomplete, with its exact
byte account — which is what *frame qualification* refuses on, a different and weaker
test than admission. Policy exclusion is likewise not a fault; a frame that requires an
excluded observation simply cannot qualify as complete.

### 5. Two independent identities

```
N_selected = N_captured + N_incomplete + N_lost          /* events  */
B_declared = B_captured + B_lost                         /* content */
```

The two budgets (event slots, content bytes) and the two loss accounts never
conflate. A single incomplete event may account for several lost content spans, so
`content_spans_lost` is not `incomplete` and `content_bytes_lost` is not `lost`.

A **declared span that was not captured changes the observation's completeness**: it
is `incomplete`, whatever the call returned. Completeness is decided in the core, not
by a reader of the export.

Policy interaction is a third, distinct case. Content declared on a hook the policy
did **not** select is neither counted nor copied, so a **policy choice is never
misrepresented as a missing required span**.

### 6. The versioned observation export

```
RECORDER-EXPORT v1
SCENARIO <name>
COUNTERS … content_bytes_declared=… content_bytes_captured=… content_bytes_lost=… content_spans_lost=…
ACCOUNTING ok|VIOLATED
ADMISSION ok|REJECTED
ARENA capacity=… used=… intervals=… overlaps=… out_of_range=…
OBSERVATIONS count=N listed=0|1
OBS thread=… seq=… phase=… hook=… point=… result=… complete=… fault=… nargs=… args=… ncontext=… context=… nspans=… content=… norigin=… origin=…
CONFIG …
END effects=…
```

`content=` is a comma-separated inventory: `c:<length>:<hex>` for a captured span and
`l:<declared-length>` for a lost one. `origin=` uses the same comma-separated form as
`context=`.

The reader enforces the shape rather than merely parsing it. Every declared count must
match the items that follow (`len(args) == nargs`, `len(context) == ncontext`,
`len(content) == nspans` *including* lost spans, `len(origin) == norigin`); a repeated
or empty key is refused; integers must be canonical non-negative decimals; every
scenario must declare its `COUNTERS`, `ACCOUNTING`, `ADMISSION`, `ARENA` and
`OBSERVATIONS` records **at most once** — a second would silently overwrite the first;
`OBSERVATIONS count` must equal the observations printed when `listed=1`, and none may
appear when `listed=0`; the native counter identities are recomputed and must hold,
and the declared `ACCOUNTING` must be `ok`
(`intercepted == selected + unselected`, `selected == captured + incomplete + lost`,
`content_bytes_declared == content_bytes_captured + content_bytes_lost`); the count must
reconcile with the event counters (`count == captured + incomplete`) for a withheld
scenario too; for a `listed=1` scenario the printed split must agree with them
(`complete == captured`, `incomplete == incomplete`), the faulted observations may not
exceed `rejected`, and no faulted observation may coexist with `ADMISSION ok`;
`ADMISSION` must agree with `rejected`; the version line must be first; and there must
be exactly one terminal `END`, last, with no record after it.

`listed=0` exists because under a *bounded* concurrent arena race, which observation
won which region is not a property of the capture. The aggregate `ARENA` and `COUNTERS`
reports are the exact, deterministic statement; the count is still declared, so no
consumer has to guess whether detail was withheld.

This is a **driver-level** contract. The released `RECORDER v1` accounting transcript
is untouched *by construction*, because a different driver authors it — adding a field
to the recorder core cannot change bytes this driver does not print. No compatibility
mode is needed, and none exists. `harness.parse_export_transcript` is the reader.

`ARENA` reports the arena *structurally* — bytes used, interval count, and pairwise
overlaps — rather than by printing placement, because under concurrency which thread
wins which region is arbitrary and must not leak into a golden transcript.

### 7. Content loss, and content publication, in a materialized record (P4.10)

`completeness.losses` keeps its existing meaning: **event-level** sub-observation
loss, and it remains zero for any materialized record. Content loss is carried
**separately** — a byte count plus the affected lifetimes in
`completeness.unavailable_dependencies`, which `qualifies_for_semantic` already
refuses on. The two equations above must not be conflated in the record model either.

| Situation | Native classification | Offline treatment |
| --- | --- | --- |
| Complete event, all declared content captured | `captured` | Eligible for semantic comparison |
| Event present, a declared span missing | `incomplete` | Preserved with its loss explained; never deduplicated |
| Event slot unavailable | `lost` | Counted only; no fabricated event |
| Event excluded by policy | `unselected` | Counted only |
| Content not selected by the declared policy | as the policy defines | Never reported as required missing content |
| Malformed declaration | one `fault`, `rejected += 1` | Admission is `REJECTED`; no record is materialized from it |
| Content whose origin does not resolve | exported `complete=1` | Rejected by closure verification, before the record model |

P4.10 additionally verifies, per content-bearing observation, that the **concatenated
snapshot's length equals the independently established resource size for that lifetime
and version**. The native origin names the resource version but does not by itself
prove that all of its bytes were supplied, so the length check is the offline half of
the attributability rule and is part of P4.10's acceptance, not P4.9's.

### 7a. The function half, split for review (2026-10-23)

**A1 — resource materialization.** Reads one P4.9 export plus a reviewed *capture
declaration* (module identity, hook/ABI map, effective policy per scenario, fidelity,
and the producer's effect-coverage statement) and publishes:

* one schema-valid observation record per exported observation, with the export's
  `point` deciding which of `state.pre`/`state.post` the observation's snapshot (if
  any) occupies;
* **one blob per complete resource version**, assembled from its spans in declaration
  order, length-checked against an *independent* declared size (the creating call's
  argument, else the reviewed producer configuration) and hashed into
  `resources[].contents_sha256`; per-span digests remain diagnostic;
* a sealed capture binding that references the declaration digest, the export digest
  and every record digest, and whose verifier (not `EvidenceStore.verify`) checks the
  blob→record association, record provenance and accounting.

**Resource identity is owned by a world.** A P4.9 scenario initializes independent
worlds, at most one per declared thread, so a resource version is identified by
`(capture_id, scenario, thread, slot, generation)` — the declared
`resource_scope: "scenario-thread"` — and its ID is
`world:<capture>/<scenario>/<thread>:slot:<slot>`. Size lookup, lifetime checks and
the version→content uniqueness invariant use the same scope. Two scenarios may both
contain thread 0, slot 0, generation 0, version 1 with different bytes without
contradicting each other; they are different resources. Cross-scenario semantic
deduplication is not restored by pretending otherwise — A1 accepts the reduced
deduplication, and if canonical resource roles are needed later they must be declared
explicitly.

**Context references bind the version they address.** A `read`, `share` or `destroy`
publishes no new version; it addresses an existing resource state. When the capture
materialized that version, the observation binds the scoped resource identity,
generation, version and content digest in `resources[]`, so the state enters its
semantic projection. If the version was not materialized — for example, because the
updating observation lost its spans — the observation is still stored and accounted
for, but is named `unavailable-dependency:slot:<slot>@<generation>:v<version>` and
cannot deduplicate. A complete observation is not rejected merely because it
references an unavailable version. A content-bearing hook (`create`, `update`, `pair`)
that exports no content is not a reference: its produced state is unknown and is named
`unavailable-dependency:content-unavailable:<hook>`. Version-map construction rejects
a second complete observation that binds the same scoped version to different bytes
rather than silently overwriting the first.

**Only source-bound verification is admission.** With both the export and the
declaration, `verify_binding` re-derives the whole binding from them, requires exact
equality, then proves every derived record and blob is present and byte-identical in
the store. That is `mode: "source-bound"`, `admitted: true`, and the only result
`materialize_export` accepts. Without both sources it performs `mode: "structural"`
checks (manifest, provenance, resource association, counters, ledger) and reports
`admitted: false`: integrity inspection, never proof of correct materialization.
Supplying only one source is refused.

**Arity and unexpected arguments.** Every declared hook has an `arity`, and its
carriers must cover every argument `0..arity-1`. A missing argument is unknown, not
zero. An observed argument beyond the declared arity is not discarded: it stays in
`raw.args` and is named `args:unexpected:<hook>:<index>` in
`state.unknown_dependencies`, which disqualifies the observation while the record
remains stored and accounted for.

**Native completeness is not semantic qualification.** A fully captured observation
whose declared semantic domain is incomplete (for the P4.9 producer: external effects
are exported only as an aggregate counter) is stored and accounted for, but receives
no semantic digest and never becomes `verified_repeat`:

    N_native_captured = N_qualified + N_domain_incomplete

with native-incomplete and lost observations accounted for separately. Native counters
are never rewritten to make finalization pass.

**Context references are not complete input/output semantics.** A bound reference
names the state an observation addressed; it does not bind the observation's complete
inputs or its result. `raw.result` is not in the semantic projection while
`abi.return_carrier` is `None`, and an update binds the version it produced but not its
full pre-state. Deduplication under the test-only `effects: "none"` override is
therefore a mechanism check (scoped identity, collision-safe references), not
behavioral certification. The shipped producer declares `effects: "aggregate-only"`,
so its captured observations remain semantically unqualified. Complete function-level
input/output transitions belong to A2.

A scenario is **withheld** when the producer deliberately printed no observations, and
**rejected** when admission is `REJECTED`, closure fails (for example a stale origin),
a declared policy excludes a printed hook, or a complete observation's content cannot
be sized or attributed. Rejected and withheld scenarios contribute no records; their
counters and the reason remain in the binding.

**Publication is transactional per scenario.** Derivation is pure; blobs, then records,
then the sealed binding are published only for accepted scenarios, so a rejected
scenario cannot leave an orphaned record or blob. A crash between publication steps
can leave unreferenced artifacts; a retry is deterministic and re-publishes them.

**Module identity is producer-bound in A1.** The declaration admits only
`module_identity: "producer-source-bundle"` and the binding records
`attested: false`: it names the reviewed producer sources and cannot attest the module
that actually ran. `loaded-module` is rejected, not silently downgraded; verified
runtime identity is a later slice's obligation.

**A2 — function fixture contract.** Defines the versioned `FUNCTION-CAPTURE v1`
transcript (entry and exit owned snapshots, invocation identity
`capture:scenario:thread:sequence`, explicit per-invocation effects, loss accounting),
a synthetic paired-state producer, the static `_GfxSetConstantBuffer` domain descriptor
(`RDI` vs `*RDI`, ignored carriers, branch-conditional buffer read, three writes, guard
extents) and descriptor-driven **raw-memory** closure. A1's synthetic resource registry
is not reused as a pointer-graph verifier. The fixture is a digest manifest
`(record_digest, domain_digest, verdict, coverage)` with a deterministic projection —
not a second record schema.

Guard pages detect out-of-domain accesses that cross a protected boundary; they do not
fault an undeclared write inside an otherwise writable object. Tier 1 therefore
compares the complete declared observation region (including unchanged bytes and
canaries) and treats a transient write-and-restore inside a declared write region as
outside its guarantee, unless access tracing is added for a function that makes it
material.

### 8. The `gfx-v2` vocabulary (declared here, implemented in P4.11)

`gfx-v1` is unchanged and accepts nothing new. `gfx-v2` is a versioned subject
protocol that declares:

* its accepted operation vocabulary;
* its **state-transition** operations, and which of them emit effects;
* the required resource and **state** dependencies of each operation;
* its terminal accounting rule — state transitions are counted separately from draw
  operations;
* what successful frame completion means.

The new state must affect **actual bytes** — for example a recorded blend mode
selecting additive rather than XOR `mix` — not merely the presence of a state object.
The interpreter, the subject and the replay land in P4.11.

## Slice responsibilities

| Slice | Responsibility |
| --- | --- |
| **P4.9** | Native span-capture API, content provenance, safe bounded arena, whole-version content semantics, fail-closed malformed declarations, correct completeness accounting, versioned export contract and its strict reader, synthetic tests |
| **P4.10** | **Function gate (A1 + A2):** materialization, whole-version content blobs, capture binding and closure, length verification against an independent resource size, native/semantic completeness reconciliation, collision-safe deduplication, function-domain descriptor, raw-memory closure and negative admission. **Frame gate:** complete command-stream accounting |
| **P4.11** | **Frame gate only:** versioned `gfx-v2` translation, state-consuming replay, end-to-end closure, and negative frame admission |

Each is a separately reviewable, separately revertible PR. P4.9's interfaces were
designed together with P4.10 and P4.11; their implementations and acceptances are
independent.

## The content arena

`content_reserve` hands out arena bytes with a compare-and-swap on a monotone
high-water mark. A span that does not fit reserves **nothing**, so a failed span
wastes no capacity. The arithmetic is provably safe rather than merely guarded: the
mark only advances towards the configured capacity, so `next + length` is at most
`2^10 + (2^32 - 1)` and cannot overflow a 64-bit counter.

The arena is separate from the event buffer, so its budget and its loss account cannot
be confused with the capture buffer's.

## P4.9 acceptance

* The export reproduces its committed golden byte-for-byte, deterministically; the
  released `synthetic` and `recorder` goldens are **unchanged**.
* The export carries each observation's hook, arguments, resource context, declared
  capture point, content and content origin — the information a translation rule needs.
* Captured content is the **complete** contents of one resource version: the
  post-update state for an update (not the mutated prefix), the whole version for a
  split pair, and **nothing** for a read.
* Every captured blob is attributable: content is never exported without an origin, and
  the origin resolves to the lifetime the bytes came from. An update's origin names the
  version it produced, not the version it addressed.
* A declared span that does not fit is lost whole; the observation becomes
  `incomplete`; its partial evidence stays inspectable; and its byte accounting
  reconciles independently of the event accounting.
* Every named instrumentation fault occurs at least once; each malformed declaration
  is one rejection however many symptoms it shows; a faulted observation declares no
  content and is never complete; a declared count with no array faults rather than
  fabricating zeros; a malformed declaration is judged before its array is read, so an
  over-long count never dereferences it, even under AddressSanitizer, while a valid
  count's array must be readable for its declared length (the hook's contract); an
  absent array with a zero count stays valid; and scenario `ADMISSION` is `REJECTED`
  exactly when `rejected > 0`.
* A well-formed declaration whose origin names a stale generation is exported complete
  by the core and **rejected by the offline closure verifier**.
* Two calls sharing a resource context are distinguished by their arguments, and the
  values they observed stay recoverable from the published contents of that version.
* `content_capacity == 0` declares no bytes and changes no completeness.
* An over-large content capacity is **rejected**, never truncated.
* The export parser refuses a deleted content item, a truncated argument list, content
  with no origin, a faulted observation that claims completeness, a repeated or missing
  field, a non-canonical integer, an observation count that disagrees with the
  observations or with the event counters, a duplicated scenario control record, an
  observation split that disagrees with the counters, more listed faults than
  rejections, counters that fail a native identity however the accounting verdict is
  labelled (and a `VIOLATED` verdict whose identities hold), an admission verdict that
  contradicts the rejections or that is `ok` beside a listed fault, a missing or
  duplicated terminal `END`, and any record after it.
* No two captured spans share an arena byte, under a four-thread race; with an
  eight-byte arena the race ends exactly full.
* The targets are deterministic across 25 runs, identical at `-O0/-O2/-O3`, clean
  under AddressSanitizer and UBSan, and show zero data races under ThreadSanitizer.

See `tests/test_oracle_recorder_content.py`, `oracle/recorder/native/content_driver.c`
and `oracle/recorder/native/content_golden.txt`.

P4.9 does **not** admit a frame, translate an operation, or replay anything. Those are
P4.10 and P4.11.
