# The pre-P5 offline integration gate: content capture, materialization and declared translation

**Status:** in progress. **P4.9** (bounded resource-content capture in the native
recorder core, and the versioned observation export) is implemented and on the P4.9
branch, revised by one corrective commit that made every exported blob a *complete*
resource version and every captured byte attributable. **P4.10** (offline
materialization, content-blob publication and identity) and **P4.11** (declared
translation, `gfx-v2` stateful replay and negative admission) remain. **Nothing here
launches EU IV, opens a game process, or attaches to a binary**: every input is a file
this repository's own drivers wrote.

This document is authoritative for the pre-P5 offline integration gate. The phase
contract is in [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md); the recorder it builds on is in
[P4_RECORDER.md](P4_RECORDER.md); the synthetic replay it must generalize is in
[P4B_REPLAY.md](P4B_REPLAY.md).

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

> Establish that the recording and replay pipeline works end to end on a sufficiently
> representative synthetic workload, entirely offline.

| Requirement | Necessary demonstration | Slice |
| --- | --- | --- |
| Resource-content capture | The native recorder records initial contents and relevant mutations, not just IDs | P4.9 |
| Identity and versioning | Reused handles resolve to the correct generation and resource version | P4.9 / P4.10 |
| Complete command stream | Native event accounting reconciles with the captured operations | P4.10 |
| Resource/state closure | No unresolved dependencies for the declared synthetic frame boundary | P4.11 |
| Independent replay | A fresh process reconstructs the frame from the native recorder's sealed output | P4.11 |
| Negative admission | Removing content, a mutation or a required event prevents qualification | P4.11 |

## Two gates, and why P5 cannot be circular

The hook-pack requirement splits, and conflating the halves would create a circular
gate:

* **Before P5 — offline, provable now:** the synthetic recorder-to-replay pipeline
  above, plus the *static* half of the hook-pack checks — executable-byte identity
  against the pinned EU IV identity in `reverse_engineering/target.json`, and site
  validation. These read files and need no game process.
* **In P5 — a runtime admission experiment:** whether the instrumentation actually
  behaves safely inside EU IV, and whether the intended real evidence can be
  acquired. This is a carefully bounded, operator-authorized experiment with
  fail-closed abort conditions. It cannot be a precondition for the first authorized
  launch, because it can only be answered *by* one.

So `Hard rule` below bars a launch until the offline pipeline and the static hook
checks pass; it does not demand successful live behavioural qualification before the
launch that would produce it. P5 begins without assuming that the live hooks or frame
closure are already qualified.

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
appear when `listed=0`; the native counter identities must hold
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
| **P4.10** | Materialization, content-blob closure over the whole store, whole-version length verification against the resource size, incomplete-evidence representation, collision-safe deduplication, independent reconciliation of records against native counters |
| **P4.11** | Versioned `gfx-v2` translation, state-consuming replay, end-to-end closure, and negative admission |

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
  fabricating zeros; an absent array with a zero count stays valid; and scenario
  `ADMISSION` is `REJECTED` exactly when `rejected > 0`.
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
  rejections, an admission verdict that contradicts the rejections or that is `ok`
  beside a listed fault, a missing or duplicated terminal `END`, and any record after
  it.
* No two captured spans share an arena byte, under a four-thread race; with an
  eight-byte arena the race ends exactly full.
* The targets are deterministic across 25 runs, identical at `-O0/-O2/-O3`, clean
  under AddressSanitizer and UBSan, and show zero data races under ThreadSanitizer.

See `tests/test_oracle_recorder_content.py`, `oracle/recorder/native/content_driver.c`
and `oracle/recorder/native/content_golden.txt`.

P4.9 does **not** admit a frame, translate an operation, or replay anything. Those are
P4.10 and P4.11.
