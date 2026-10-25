# ACQ diversity-aware live acquisition — `acq-diversity/v1`

**Status:** frozen contract for P5a `_GfxSetConstantBuffer` live capture under
`EU4-GFX-LIVE-SUMMARY v2`. Historical PR D sessions, `FUNCTION-CAPTURE v1`
transcripts, and E1 v1 admission remain immutable.

**Scope:** function `gfx:set-constant-buffer` only. This contract does not authorize
a EU IV launch. Implementation is **ACQ-1** (native) and **ACQ-2** (independent
model, reporter, E1-v2 adapter); see [work-orders/ACQ-1.md](work-orders/ACQ-1.md) and
[work-orders/ACQ-2.md](work-orders/ACQ-2.md).

## Non-negotiable principles

1. A **screening signature** is a sampling decision, not behavioral equivalence. A
   **producer byte match** is not an A2 qualification.
2. The **64 retained-record quota** is one atomic feasibility invariant
   (`R + P_cov + P_div + L(remaining) ≤ 64`). Priority does not bypass it. A failed
   admission either rolls its reservation back completely, or sets
   `admission_invariant_lost`, stops further selection, and makes the session
   structurally inadmissible.
3. A signature becomes **`ANCHORED`** only after a durable write of a record that
   passes the native closure predicate. A later non-retained sample does not remove
   that anchor. A write alone does not establish a coverage cell.

## Motivation (sealed session `gfx-live-20261025T155406Z`)

PR D v1 [`live_admit()`](../../benchmark/eu4_gfx_live_capture.c) reserves one of 64
tickets before the buffer dword is read. That session stopped with `intercepted=864`,
`admitted=64`, `not_admitted_quota=800`, in an armed window of 21.526 ms on one thread
(~4.0×10⁴ calls/s). The v1 reporter requires ≥50 qualified transcripts and
`EU4-GFX-LIVE-SUMMARY v1` accounting.

Offline analysis of the 64 qualified transcripts:

| Measure | Value |
| --- | --- |
| Captured observations | 64 |
| E1 behavior classes | 10 |
| Distinct pre/post span-byte tuples | 39 |
| Exact span-repeat extras | 25 |
| Byte-distinct, same E1 class | 29 |

Exact byte dedup would retain 39 records and still only 10 behavior keys. A
**per-behavior-class retention cap** and a **coverage feasibility invariant** are both
required. E1 coverage policy
[`gfx-set-constant-buffer.json`](policies/gfx-set-constant-buffer.json) and promotion
rules in [P4_INTEGRATION.md](P4_INTEGRATION.md) are unchanged.

## Screening snapshot

After existing register refusals (busy, slot ≥ 6, null/overflow pointer or address,
thread limit, disarmed/closed):

- `slot = uint32(RCX)`, `selector = uint32(R9)`
- If `selector == 0xffffffff`: one `read_bytes()` of 4 bytes at `RSI`. Those bytes
  are `input_value` and the **pre-buffer snapshot** copied into the capture record.
  No second pre-read of that dword on entry.
- Otherwise: `input_value = selector`; the buffer is not read.

Failed read → `read_failed`. Screening key:
`(function_id, slot, selector, input_value)` — no pointers, no context pre-state.
Hashing is an index only; equality is the full key.

Post-buffer read at exit is a separate observation. Mutation after the snapshot
(including inside the original function) is outside the claim. Any second pre-read
that disagrees with the snapshot → `candidate_incomplete`; apply the transition table.

**Projected cells** match [`policy.behavior`](../../oracle/certify/policy.py): slot,
selector path, input class. Fixed-length → `input:absent` (incompatible with
`input:zero` / `input:nonzero`). **Priority** = projected cells that are required and
neither established nor reserved. A reserved slot does not block priority when the
call still projects an unreserved selector or input cell.

The 4-byte Mach `read_bytes()` on the buffer path is potentially significant at the
measured call rate; see [Overhead gate](#overhead-gate).

## Signature table (128 entries)

States: `EMPTY`, `PENDING`, `ANCHORED`, `UNANCHORED`.

| State | Meaning |
| --- | --- |
| `ANCHORED` | ≥1 durable retained transcript. Counter `e` starts at 1 on first commit. Producer match only against this anchor. |
| `UNANCHORED` | Complete observations declined (`distinct_not_retained` or native ineligibility); no retained transcript. Counter `u` from first publish. No `producer_matched_repeat`, no coverage establishment. `reprobe_held` = reprobe in flight. |
| `PENDING` | First capture in progress for a new key. |

### Transition table (after reservation and in-flight flag released)

| Prior state | Non-retained complete | Incomplete / failed | Successfully retained |
| --- | --- | --- | --- |
| `PENDING` | `UNANCHORED` | `EMPTY` | `ANCHORED` |
| `UNANCHORED` reprobe | `UNANCHORED` | `UNANCHORED` | `ANCHORED` |
| `ANCHORED` verification | `ANCHORED` | `ANCHORED` | `ANCHORED` |

Prior retained files remain in the manifest. `storage_failed` still fails structural
admission.

`ANCHORED` and `PENDING` are never evicted. `UNANCHORED` with `reprobe_held` is not a
reclamation victim.

### Linearizable key publication

The 96-bit screening key (`slot`, `selector`, `input_value`) and the entry state/flags
are published with C11 `_Atomic` loads and stores. A 64-bit state CAS does not
contain the key. Ordinary non-atomic fields must not be read or written concurrently.

Writer: CAS generation even→odd, `atomic_store` the key and state, then
release-store the next even generation. Sequentially consistent atomics are an
acceptable conservative default; weaker ordering needs a separate justification and
tests. The odd generation is a write mark, not a fifth signature state.

Phase A reader (≤8 attempts): accept a snapshot only when the generation is even and
unchanged across the copy. A stable snapshot proceeds to screening and sampling.
Eight failed attempts are `signature_read_contended`: no sampling-counter advance, no
Phase B, and no capacity or signature mutation. That outcome is not `screened_repeat`,
`pending_contended`, or `verification_contended`.

Post-bind completion rules are in [Hook path](#hook-path-two-phases). A mismatch
there is not a discarded reader snapshot.

### Unique key

At most one live entry (`PENDING`, `ANCHORED`, or `UNANCHORED`) holds a full
screening key. New-key publication, including reclamation, takes one `_Atomic`
insertion gate. Anchored and unanchored sampling of an existing entry do not take
the gate. There is no concurrent map and no wait in the hook.

The gate CAS goes from idle to held, at most 8 attempts. A caller that never gets
the gate releases the buffer and the admission reservation and is `cas_exhausted`,
not `selected`.

The holder may publish only after a stable snapshot of all 128 entries. An entry
that is not stable within 8 reads is not absent: clear the gate, roll back the
reservation and buffer, publish nothing, and count `cas_exhausted`. If any stable
snapshot already shows the key live, do not publish; the same release applies and
the invocation is `key_insert_contended`. Otherwise publish exactly one `PENDING`
entry, or claim one victim, then clear the gate.

`key_insert_contended` is part of `invalid_refused`. It does not advance `e` or
`u`, does not set an in-flight flag, and does not leave a second entry.

The gate does not replace victim ownership. The claim CAS must still see
`UNANCHORED`, `reprobe_held == 0`, and the expected generation. If it does not,
try the next idle victim inside the same bound, or abort with `cas_exhausted` or
`table_capacity_refused`. Do not overwrite the entry. A diversity candidate that
has already published keeps the entry. Missing-cell priority applies only to a
victim that is not yet claimed. There is no second arbitration word.

### Reclamation

When the table is full, a new key that passes the admission CAS and the insertion
gate may reuse the lowest-index idle `UNANCHORED` entry. The [unique-key](#unique-key)
scan and the victim-claim CAS both apply. No idle victim →
`table_capacity_refused`. Counter `unanchored_reclaimed` is diagnostic (outside
`intercepted`).

### Unanchored reprobe sampling

Same schedule as anchored verification on `u`: select when `u == 2`, or `u > 2` and
`(u - 1) % 16 == 0`, or projected cells meet the priority rule. Skip only when all
three decline. The winner proceeds to Phase B and sets `reprobe_held` only after the
admission CAS succeeds.

## Coverage feasibility

Cell bit order in the admission word: `slot:0`…`slot:5`,
`selector:fixed-length`, `selector:length-from-buffer`, `input:absent`, `input:zero`,
`input:nonzero`.

Lower bound `L(remaining)` for cells neither established nor held by an in-flight
designated reservation:

- `need_fixed = 1` if `selector:fixed-length` or `input:absent` remains
- `need_zero = 1` if `input:zero` remains
- `need_nonzero = 1` if `input:nonzero` remains
- `need_buffer = 1` if `selector:length-from-buffer` remains and both zero/nonzero needs are 0
- `need_slots` = count of remaining `slot:0`…`slot:5`
- `L = max(need_slots, need_fixed + need_zero + need_nonzero + need_buffer)`

Initial remaining obligation: `L = 6`. Contract check: remaining
`{slot:3, selector:fixed-length, input:zero}` → `L = 2`.

**Invariant:** `R + P_cov + P_div + L ≤ 64`.

Every selected capture reserves one retained-record slot. If it projects at least one
required cell that is neither established nor reserved, the admission CAS sets those
bits and counts the attempt as `P_cov`; otherwise it counts `P_div` only. The
coordinator sets `coverage_designated` when that CAS reserved new coverage bits. A
coverage-designated capture is not declined under the retention cap. At most one
in-flight reservation holds a given missing slot. A second signature for an
already-reserved slot does not reduce `L`. A provisional reservation is not an
established cell.

## Hook path (two phases)

Phase A applies to every armed-path attempt. Phase B applies only after selection.

**Phase A:** register refusals, screening snapshot, atomic signature lookup, then the
sampling decision. Outcomes are `screened_repeat`, `pending_contended`,
`verification_contended`, `signature_read_contended`, or proceed to Phase B. Phase A
does not claim a temporary buffer, does not CAS the admission word, and does not
return `capacity_refused_selection`.

**Phase B:**

1. Claim a temporary buffer (pool of 64) or return `capture_buffer_refused`.
2. Admission CAS, at most 8 attempts (`P_cov` or `P_div` as above). Exhaustion is
   `cas_exhausted` and releases the buffer. The word is unchanged.
3. For a new key, take the insertion gate and bind or reclaim as in
   [Unique key](#unique-key). A pre-bind mismatch is an expected race: retry inside
   those attempts, or roll the admission word back and return `cas_exhausted`. Do
   not retain. An existing-key sample CASes that entry and does not take the gate.
4. Rollback of this attempt’s own bits, at most 64 attempts. If they do not clear,
   the outcome is `cas_exhausted`, set `admission_invariant_lost`, and stop further
   selection. That call is not in `selected`.
5. Deep capture, then coordinator classification.

Before classify or commit, the coordinator revalidates the bound entry:

| When | Mismatch | Outcome |
| --- | --- | --- |
| Pre-bind (step 3 has not succeeded) | Expected race | Retry or roll back as in step 3. No retain. |
| Post-bind (the capture owns the reservation and `verify_held` or `reprobe_held`) | Ownership failure | `selected_invariant_failure`, set `admission_invariant_lost`, do not retain, stop further selection. Apply the transition table so an existing `ANCHORED` transcript stays. |

A post-bind mismatch is not `screened_repeat` and not ordinary contention.
Classification converts the reserved slot into `R` or releases it. There is no
post-selection `capacity_refused`. A selected capture that no longer holds its
reservation is also `selected_invariant_failure`.

`admission_invariant_lost` is a sticky session flag, not an invocation outcome.
Threads remain 64. Offline TLS (`live == NULL`) is unchanged.

## Anchored sampling

On `ANCHORED`, the CAS that advances `e` selects when `e == 2`, or `e > 2` and
`(e - 1) % 16 == 0`, or priority cells are still unreserved, and `verify_held` is
clear. That decision is Phase A: a skip is `screened_repeat` and does not claim a
buffer. The winner proceeds to Phase B and sets `verify_held` only after the
admission CAS succeeds. If `verify_held` is already set, the outcome is
`verification_contended` and `e` is unchanged. `PENDING` encounters are
`pending_contended` and do not advance `e` or `u`.

`e` and `u` count eligible encounters, not finished captures. The Phase A CAS that
advances them stays advanced when Phase B returns `capture_buffer_refused`,
`capacity_refused_selection`, or `cas_exhausted` on an existing signature. That
failure increments its refusal counter once, does not increment `selected`, and
does not leave `verify_held` or `reprobe_held` set. `signature_read_contended`,
`pending_contended`, `key_insert_contended`, and a uniqueness scan that cannot
read every entry do not advance `e` or `u`. The unread-scan case is
`cas_exhausted`.

## Commit, native eligibility, retention

Coordinator alone classifies and writes. Ticket states: `READY`, `WRITTEN`,
`WRITE_FAILED`, `UNDRAINED`.

**Native predicate:** [`fixture.assess`](../../oracle/function/fixture.py) with empty
`missing` and zero losses. **Behavior key:** [`policy.behavior`](../../oracle/certify/policy.py).
ACQ-1 implements in C; ACQ-2 differential-tests against Python on shared fixtures.

Native establishment ⊆ adapter qualified cells; over-claim fails structural admission.
Adapter A2 result is certification authority.

**Retention cap (after predicate):** keep if coverage-designated; else first per
behavior key; second only if new `input_value` for that key. Cap = 2. Further variants
→ `distinct_not_retained`. Coverage-designated captures are not declined under the cap.

**Producer match** (anchored only): equal snapshot `input_value`, span bytes, alias
class, pointer role, `RAX` class, canary preservation. Uncertainty → not a match.

**Outcomes:** `candidate_incomplete`; `ineligible_not_established` (+ ≤11
`ineligible_persisted` files); `producer_matched_repeat` (+ ≤8 audit transcripts;
the association lives in [audit-associations.json](#audit-association-artifact),
not in the transcript; native code does not compute A2 `record_digest`);
`novel_retained` on successful rename; `storage_failed`; `undrained`;
`distinct_not_retained`; `selected_invariant_failure`.

Only `FUNCTION-CAPTURE v1` files that are durably retained count toward `R` and
coverage.

## Evidence and E1-v2

| Result | Meaning |
| --- | --- |
| Termination | `sealed` / `partial`, quiescence |
| Structural admission | Counter identities, manifest binding, `written_retained = novel_retained`, `storage_failed = undrained = admission_invariant_lost = 0`, established ⊆ adapter |
| Coverage qualification | E1-v2 adapter only; missing cells do not unseal |

**v2 artifacts:** `EU4-GFX-LIVE-SUMMARY v2`, `acquisition_policy=acq-diversity/v1`,
manifest schema `eu4-gfx-live-manifest/v2` listing retained, audit, and ineligible
files plus `audit-associations.json` by path and file sha256. No qualified-count
floor.

### Audit association artifact

Audit transcripts stay strict `FUNCTION-CAPTURE v1` (for example
`captures/<session>.<n>.audit.capture`). [`parse_capture`](../../oracle/function/fixture.py)
rejects extra fields, so the reference is not stored in the transcript.

The coordinator writes session-root `audit-associations.json`
(`eu4-gfx-live-audit-associations/v1`) and finalizes it before seal, including
`entries: []` when there are no audits. Each entry has `audit_path`, `audit_sha256`,
`reference_path`, and `reference_sha256` (sha256 of file bytes), plus an optional
`screening_key` of `{slot, selector, input_value}`. The v2 manifest hashes that
finalized file. Native code appends a row when an audit transcript is persisted and
does not compute A2 `record_digest`.

ACQ-2 rejects duplicate audit paths, references outside the sealed session,
references that are not retained captures, rows that do not match the audit files,
and hash mismatches. It recomputes the producer-match relationship from the two
transcripts. Matching hashes alone are not a match.

**E1-v2 adapter:** `benchmark/acq_diversity_v1.py`. It re-reads sealed artifacts,
ignores the reporter verdict, and does not call
[`certify_session`](../../oracle/certify/certify.py) unchanged, because that entry
point admits only v1 summaries. It reuses the existing candidate-binding, replay,
coverage, and promotion helpers on **retained** transcripts and emits
`eu4-oracle-tier1-certificate/v1`. v2 evidence does not upgrade
`sealed-session-unverified` into independently attested live-grounded provenance.
It does not modify `oracle/certify/`. E1 v1 still rejects a v2 summary. **Adapter
tests pass before the next live session.** Structural admission is not
`certified`, `scoped`, or `failed`.

## Accounting (v2)

`intercepted` = armed-path attempts only. `uint64` counters; documented domain ≤24 h
armed, ≤10⁸ calls/s.

```
intercepted = invalid_refused + pending_contended + verification_contended
            + screened_repeat + selected

invalid_refused = busy + slot + pointer + address + thread_limit + closed
                + read_failed + signature_read_contended + key_insert_contended
                + table_capacity_refused + capture_buffer_refused
                + capacity_refused_selection + cas_exhausted

selected = first_selected + verification_selected

selected = novel_retained + producer_matched_repeat + distinct_not_retained
         + ineligible_not_established + candidate_incomplete + storage_failed
         + undrained + selected_invariant_failure

producer_matched_repeat = producer_matched_discarded + audit_persisted  (audit ≤ 8)

novel_retained = R = written_retained ≤ 64
ineligible_persisted ≤ 11  (files, not R)

admission_invariant_lost  — sticky 0/1, outside both partitions
```

Do not map these names onto P4 `unique` / `verified_repeat` / `incomplete` /
`counted_only`. `not_admitted_quota` is not a v2 counter.

## Overhead gate (ACQ-1)

Native harness: ≥10⁴ calls each of unhooked leaf, v1 quota-refuse, v2 buffer-path
screen (one 4-byte read + signature CAS, no deep capture), v1 full capture.

Pass: screen p50 ≤ 1.0 µs, p99 ≤ 2.5 µs; full-capture p50 ≤ 1.25× v1 full-capture
p50. Publish ratios vs unhooked and v1 refuse. If the Mach read alone exceeds the
screen cap, **stop for contract amendment** — mandatory; do not use unsafe reads or
relax limits inside ACQ-1.

## Acceptance matrix (contractual tests)

| Scenario | Required outcome |
| --- | --- |
| 100 identical calls | First retained; repeats screened/sampled |
| Same slot, different input DWORD | Different screening key |
| Same key, different pre-state | Not producer match; cap → `distinct_not_retained`; anchor stays |
| Same key, different post-state | Distinct on full verification |
| Zero input after many nonzero | Eligible; coverage capacity not exhausted by repeats |
| Two threads, same unseen key | One `PENDING` owner; loser is `key_insert_contended` or `cas_exhausted`; reservation released |
| Uniqueness scan cannot read one entry | No publish; `cas_exhausted`; `e` and `u` unchanged |
| Reprobe in flight during reclamation | Victim claim fails; reprobe entry unchanged |
| Scheduled sample, Phase B refused | `e` or `u` stays advanced; one refusal; not `selected` |
| Failed first pending capture | Retry eligible (`EMPTY`) |
| Signature table full | `table_capacity_refused` or reclamation |
| 128 unanchored + free R slots, new diversity key | Admitted via reclamation |
| Coverage vs diversity victim contention | Rules in [Reclamation](#reclamation) |
| `reprobe_held` / generation mismatch | No wrongful eviction or cross-key mutation |
| Reader | Never new state + old key |
| Anchored + `distinct_not_retained` / ineligible / `storage_failed` | Stays `ANCHORED`; prior transcript in manifest |
| Unanchored + missing cell | Priority override; reprobe is `P_cov`; cell established only after qualified retain |
| Ineligible persist then same cell | Second capture still gets reservation |
| Capacity reserved for missing cells; known anchored repeat | `screened_repeat`; no buffer and no admission CAS |
| Eight failed atomic key reads | `signature_read_contended` only |
| Post-bind generation mismatch at commit | `selected_invariant_failure`; flag set; prior anchor kept |
| Empty or populated `audit-associations.json` | Finalized before seal; manifest hashes it; adapter recomputes the match |
| False native coverage | Written ineligible does not establish cell |
| 64 retained | Bounded closure, no overwrite |
| Deadline, missing coverage | Honest partial; no false promotion |
| v1 evidence | Unchanged admission |
| Broken summary / counters | Fail closed |
| Audit hash / A2 mismatch | Structural admission fails |
| Rollback fail after bind fail | `cas_exhausted`, flag set, not in `selected` |
| Post-selection missing reservation | `selected_invariant_failure` |

Integrated gate: reconcile every native invocation outcome with ACQ-2’s Python model.

## Boundaries (out of scope)

Multi-function acquisition, dynamic quota above 64 retained records, changes to sealed
historical evidence, frame replay, F2 shadow, general memory-graph discovery, new hook
sites. ACQ-1/ACQ-2 stop and propose amendment if this contract cannot be preserved.

## Versioning

| Artifact | Rule |
| --- | --- |
| Historical PR D sessions | Immutable |
| `FUNCTION-CAPTURE v1` | Unchanged for retained transcripts |
| `EU4-GFX-LIVE-SUMMARY v1` | E1 v1 + v1 reporter only |
| `EU4-GFX-LIVE-SUMMARY v2` | This contract |
| `audit-associations.json` | Session-root association; not part of `FUNCTION-CAPTURE v1` |
| `eu4-gfx-live-manifest/v2` | Hashes the finalized association file and capture paths |
| E1 v1 certifier | Unchanged |
| E1-v2 adapter | New; required before next live session |
