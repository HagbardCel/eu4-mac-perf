# Exact-dedup live acquisition — `exact-dedup/v1`

**Status:** frozen contract for future `_GfxSetConstantBuffer` sessions. It
supersedes [`acq-diversity/v1`](ACQ_DIVERSITY_V1.md) as the implementation path.
That document stays frozen and is not amended. Historical PR D sessions,
`EU4-GFX-LIVE-SUMMARY v1`, `FUNCTION-CAPTURE v1` transcripts, and E1 v1 admission
stay immutable. This contract does not authorize a EU IV launch.

**Scope:** one function, `gfx:set-constant-buffer`. No coverage reservation, no
pre-call signature table, and no sampling policy.

## What is retained

The hook copies one complete observation into a temporary slot. A single
coordinator decides whether to keep it. "First N" means the first N distinct
complete observations **processed by the coordinator**, scanning slots from index
0 upward. It is not entry order.

Two complete observations are duplicates only when their canonical bytes are
equal. Pointers are not normalized. E1 behavior classes are not an acquisition
key. Equality is equality of the declared capture regions, not proof that the
function touched no other memory.

An incomplete capture is counted and is not fingerprinted. A call that finds no
free temporary slot is `buffer_refused`. It was not observed.

## Limits

| Resource | Rule |
| --- | --- |
| `max_unique` | Runtime value, default 64, inclusive range 1..4096. Python and the dylib both reject anything outside that range. 4096 is the memory and disk budget for one canonical blob plus one transcript per retained observation. |
| Temporary pool | 64 reusable slots. This is not a lifetime retention cap. Retaining 128 uniques with this pool is required. |
| Deadline | Existing deadline. |
| After N | Stop new reservations. Drain in-flight slots. Do not retain observation N+1. |

The retained set is allocated before arming. Allocation failure refuses startup
and does not arm.

## Slot ownership

A slot is `FREE`, `RESERVED`, `READY`, `PROCESSING`, or `QUARANTINE`.

- The hook claims `FREE → RESERVED` and owns the slot until it publishes `READY`
  or records an incomplete outcome.
- Only the coordinator takes `READY → PROCESSING`. It returns the slot to `FREE`
  only after the outcome counter has been updated.
- Counters accumulate for the session. They are not rebuilt by inspecting final
  slot states.
- Shutdown stores `accepting = false` and moves the session to closing **before**
  waiting for outstanding captures. `live_begin` does not cover the whole call,
  so a `RESERVED` slot is what shows that a capture is still in the original call.
- A slot is never returned to `FREE` while its producer can still write it.
- If a `RESERVED` or `READY` slot is still outstanding when the existing quiesce
  or drain timeout expires, it becomes `QUARANTINE`, increments
  `undrained_observations`, and is not recycled. The session state is `partial`.
  A nonzero undrained count is not clean evidence.

A full lock-free queue is out of scope. The coordinator scans the 64 slots.

## Outcomes

These complete outcomes are mutually exclusive:

| Outcome | Meaning |
| --- | --- |
| `unique_retained` | Complete, distinct, and durably renamed into `captures/<session>.<index>.capture` |
| `duplicates_discarded` | Complete, and its canonical bytes match a retained observation |
| `unique_limit_discarded` | Complete and distinct, processed after `unique_retained` already equals `max_unique` |
| `storage_failed` | Complete and distinct, but the durable write failed |

```
complete_observations =
    unique_retained + duplicates_discarded
    + unique_limit_discarded + storage_failed
```

`incomplete_observations`, `buffer_refused`, and `undrained_observations` are
outside that sum. Existing pre-capture refusals stay: busy, slot, pointer,
address, thread limit, and closed. There is no lifetime `quota` and no
`not_admitted_quota`.

```
intercepted =
    not_admitted + buffer_refused + incomplete_observations
    + complete_observations + undrained_observations
```

`not_admitted` is the sum of the pre-capture refusal counters.

The fingerprint is committed only after `fflush`, `fsync`, and `rename` of the
transcript succeed. A failure unlinks the partial file, increments
`storage_failed`, and does not insert the fingerprint. A later identical
observation can still be retained. A `*.capture.partial` file is not a retained
transcript.

When `unique_retained` reaches `max_unique`, new admits stop. In-flight slots
still finish. Duplicates are classified as duplicates. Further distinct
observations are `unique_limit_discarded`. `closed_reason` is `unique_limit`
once those slots have drained. Deadline and operator stop still close the
session.

## Canonical bytes — `eu4-exact-dedup-canon/v1`

Do not hash a C struct. Numeric fields are little-endian. There is no padding.

| Order | Field | Width |
| --- | --- | --- |
| 1 | magic | 8 bytes, ASCII `EXDEDUP1` |
| 2 | function id length | `u16`, then that many UTF-8 bytes. For this function the bytes are `gfx:set-constant-buffer` |
| 3 | `RDI`, `RSI`, `RCX`, `R9` | four `u64` values, the captured carriers |
| 4 | buffer present | `u8`, 1 when the buffer region was captured, else 0 |
| 5 | regions | the fixed order below |
| 6 | `RAX` | `u64`, the captured return value |

Each region is either one `u8` of 0 (absent) or:

| Field | Width |
| --- | --- |
| present | `u8` = 1 |
| address | `u64`, the address written in the transcript |
| length | `u16`, the pre-state length |
| pre-state | `length` bytes |
| post-state | `length` bytes |

Region order, always: `handle`, `ctx`, `canary-before`, `canary-after`,
`buffer`. The buffer region is absent on the fixed-length path and present on
the length-from-buffer path. The other four are present on every complete
observation this hook publishes.

Excluded from the bytes: thread id, sequence, ticket, timestamp, filename,
scenario, epoch, callsite, and any other recording metadata.

SHA-256 of these bytes is only a lookup. Equal hashes still compare the bytes.
A hash collision must not discard a distinct observation.

The reporter rebuilds this encoding from retained `FUNCTION-CAPTURE v1`
transcripts. It can prove the retained set is mutually distinct. It cannot prove
that a discarded call was a duplicate, because those bytes are not kept.
Collision behavior is a native harness test.

## Summary

Header line: `EU4-GFX-LIVE-SUMMARY exact-dedup/v1`.

Required fields: `session`, `pid`, `state` (`sealed` or `partial`),
`closed_reason`, `quiescence`, `started_ns`, `armed_ns`, `closed_ns`,
`max_unique`, `threads`, `intercepted`, `not_admitted`, the six
`not_admitted_*` refusal counters, `buffer_refused`, `complete_observations`,
`unique_retained`, `duplicates_discarded`, `unique_limit_discarded`,
`storage_failed`, `incomplete_observations`, `undrained_observations`, then
`END`.

`startup.status` records the same `max_unique` when the dylib installs. The
reporter reads that configured value. It does not assume 64.

`EU4-GFX-LIVE-SUMMARY v1` admission is unchanged, including its 64-ticket checks.

## What this contract does not do

It does not change E1 admission, replay, certificates, or coverage policy.
Coverage and diversity, if reported, are computed afterwards from retained
transcripts. The producer's duplicate count is not evidence of equivalence.
Certificate integration is not a gate for this recorder.
