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

Variable key is 96 bits (`slot`, `selector`, `input_value`). Writer: CAS even
generation → odd, write key and state, release-store next even generation. Reader:
copy only when generation is even and unchanged across the copy. Odd generation is a
write mark, not a fifth state. Completion CAS must match the even generation; mismatch
releases that capture’s reservation and buffer without modifying the rebound entry.

### Reclamation

When the table is full, a new key that passes the admission CAS may reuse the
lowest-index idle `UNANCHORED` entry (any key that passes admission, not only
missing-cell keys). At victim CAS: coverage candidate may set `coverage_reclaim` and
bind; diversity candidate that has not bound skips that victim and tries the next idle
entry within eight attempts; diversity candidate that already won keeps the entry.
Binding after admission CAS; failed bind rolls back admission. No idle victim →
`table_capacity_refused`. Counter `unanchored_reclaimed` is diagnostic (outside
`intercepted`).

### Unanchored reprobe sampling

Same schedule as anchored verification on `u`: select when `u == 2`, or `u > 2` and
`(u - 1) % 16 == 0`, or projected cells meet the priority rule — plus free buffer and
successful admission CAS. Skip only when all three decline. Winner sets `reprobe_held`.

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

Admission CAS reserves unreserved projected cells and increments `P_cov` or `P_div`.
At most one in-flight reservation per missing slot. Second novel signature for an
already-reserved slot does not reduce `L`. Provisional reservation ≠ established cell;
establishment only on durable commit with native predicate pass.

## Admission linearization

One atomic 64-bit word: `established`, `reserved`, `R`, `P_cov`, `P_div` (only
capacity linearization point).

Every selected verification and unanchored reprobe reserves one worst-case `P_div` in
that CAS. Classification converts to `R` or releases. No post-selection
`capacity_refused`; pre-CAS pressure is `capacity_refused_selection`.

`admission_invariant_lost` is a **sticky session flag**, not an invocation outcome.
Missing reservation after successful selection → `selected_invariant_failure` + flag +
stop selection.

**Order:**

1. Claim temporary buffer (64 pool) or `capture_buffer_refused`.
2. Admission CAS, ≤8 attempts → else `cas_exhausted`, release buffer.
3. Signature CAS, ≤8 attempts; on failure roll back admission → `cas_exhausted`.
4. Rollback own bits, ≤64 attempts; on failure → `cas_exhausted`, set
   `admission_invariant_lost`, stop selection (not counted in `selected`).

Threads: 64. Offline TLS (`live == NULL`) unchanged.

## Anchored sampling

On `ANCHORED`, CAS advancing `e` selects when `e == 2`, or `e > 2` and `(e-1) % 16 == 0`,
or priority cells unreserved; needs `verify_held == 0` and admission CAS. Winner sets
`verify_held`. Contended → `verification_contended`. Skip → `screened_repeat`.
`PENDING` encounters → `pending_contended` (no `e`/`u` advance).

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
`ineligible_persisted` files); `producer_matched_repeat` (+ ≤8 audit files with
`reference_capture_sha256` of retained bytes — native does not compute A2
`record_digest`); `novel_retained` on successful rename; `storage_failed`;
`undrained`; `distinct_not_retained`; `selected_invariant_failure`.

Only `FUNCTION-CAPTURE v1` files that are durably retained count toward `R` and
coverage.

## Evidence and E1-v2

| Result | Meaning |
| --- | --- |
| Termination | `sealed` / `partial`, quiescence |
| Structural admission | Counter identities, manifest binding, `written_retained = novel_retained`, `storage_failed = undrained = admission_invariant_lost = 0`, established ⊆ adapter |
| Coverage qualification | E1-v2 adapter only; missing cells do not unseal |

**v2 artifacts:** `EU4-GFX-LIVE-SUMMARY v2`, `acquisition_policy=acq-diversity/v1`,
manifest lists `retained`, `audit`, `ineligible` by path and file sha256. No
qualified-count floor.

**E1-v2 adapter:** `benchmark/acq_diversity_v1.py` — re-reads sealed artifacts, does
not trust reporter verdict, A2 + existing comparator + unchanged policy. Does not
modify `oracle/certify/`. E1 v1 rejects v2 summary. **Adapter tests pass before the
next live session.**

## Accounting (v2)

`intercepted` = armed-path attempts only. `uint64` counters; documented domain ≤24 h
armed, ≤10⁸ calls/s.

```
intercepted = invalid_refused + pending_contended + verification_contended
            + screened_repeat + selected

invalid_refused = busy + slot + pointer + address + thread_limit + closed
                + read_failed + table_capacity_refused + capture_buffer_refused
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
| Two threads, unseen key | Bounded pending; no false verified repeat |
| Failed first pending capture | Retry eligible (`EMPTY`) |
| Signature table full | `table_capacity_refused` or reclamation |
| 128 unanchored + free R slots, new diversity key | Admitted via reclamation |
| Coverage vs diversity victim contention | Rules in [Reclamation](#reclamation) |
| `reprobe_held` / generation mismatch | No wrongful eviction or cross-key mutation |
| Reader | Never new state + old key |
| Anchored + `distinct_not_retained` / ineligible / `storage_failed` | Stays `ANCHORED`; prior transcript in manifest |
| Unanchored + missing cell | Priority override on next encounter |
| Ineligible persist then same cell | Second capture still gets reservation |
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
| E1 v1 certifier | Unchanged |
| E1-v2 adapter | New; required before next live session |
