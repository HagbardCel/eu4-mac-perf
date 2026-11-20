# Harness remediation integration report

Date: 20 November 2026. This report records technical readiness, review state
and merge state. It is not a claim that every scientific blocker is closed.
The exact-head audit is in [audit-20261120.md](audit-20261120.md).

## Merged stack

| PR | Final head | Merge commit | Audit verdict |
|---|---|---|---|
| #241 exact verdicts | `3be433f7` | `1e03b54a` | Accepted in principle; residual envelope/checkpoint defects fixed by #258 |
| #242 attribution | `7bcaaa1c` | `ced6cf25` | Accepted |
| #243 native builds | `f62c7f18` | `befd49cc` | Accepted |
| #244 compressed textures | `cd4245cf` | `8822c03c` | Accepted; authentic same-seal comparison partial |
| #245 sampler/raster | `ada20dbc` | `fb766200` | Accepted |
| #246 publication/discovery | `8c17f779` | `d45a9079` | Accepted |
| #247 reconnect context | `ab28f2da` | `8c98db67` | Accepted |
| #248 identity/recipes | `1bffc7cb` | `755c20c1` | Accepted |
| #249 terminal capture | `555447fb` | `c9576f4f` | Accepted |
| #250 reservations/no-progress | `401d6863` | `3dabc6fe` | Accepted |
| #251 physical work | `050f9272` | `845d7df6` | Accepted |
| #252 knowledge/matrix | `275df332` | `4cc0ed80` | Partial; #257 and the docs follow-up correct stale states |
| #253 904 three-way | `2596b50e` | `f1271302` | Accepted as analysis; control unqualified |
| #254 B2/B3 qualification | `63a3148e` | `d5452a79` | Accepted as a gate; scientific blocker open |
| #255 issue #234 hardening | `090f2b52` | `c289adf4` | Accepted |
| #256 per-session isolation | `c403f06e` | `f0bfa16c` | Accepted; formal external approval absent |
| #257 final docs | `b1bacce8` | `a0e3a4e8` | Partial; this follow-up corrects the remaining stale states |
| #258 envelope/checkpoint hardening | `8a5d4bc2` | `d29f45f0` | Accepted after independent exact-head review; non-blocking residuals named in the audit |

Portable CI passed for every merged head. That is not independent approval.
GitHub has no `APPROVE` review for #241–#258; the earlier report's claim that an
independent review was recorded for all of them was not supported by the review
records. #258's independent exact-head review is recorded on the PR as a review
comment, not an `APPROVE` state, because the only available GitHub identity is
the author. The audit in [audit-20261120.md](audit-20261120.md) is technical
review evidence, not a formal approval.

## Follow-ups

| PR | Head | State |
|---|---|---|
| This documentation follow-up | this PR | Open; state, scientific-language and P0-finding corrections; strict links and docs tests required |
| Ledger integrity P0 | not opened | `unresolved`; fail-closed campaign claim reads and cross-process/interrupted-write regressions |
| Control authority P0 | not opened | `unresolved`; authenticated original-API control identity/provenance separated from pixel equality and Metal blame |
| Three paired efficiency trials | not run | `outstanding`; no wall-time improvement claimed |

## Verification

- Audit aggregate at `a0e3a4e8`: 29 modules, **852 ran / 849 executed / 3
  skipped / 0 failures** (`20261120T185637Z-1a7c318d`).
- #258 hardening at `8a5d4bc2`: **132 ran / 131 executed / 1 skipped / 0
  failures / 0 errors**; neighbours **162 executed / 0 failures**. The same
  new tests fail on base `a0e3a4e8` (8 failures + 2 errors), so they are genuine
  regressions. Portable CI passed at the reviewed SHA.
- Native checks executed on arm64 macOS: compressed texture host 18/18,
  render-state host 10/10, native MSL cache regression 1/1, frame-trace writer,
  texture versions, shadow receipt, target lifetime and blob verifier. Fixture
  skips are declared, not compiler failures.
- The five-pixel 904 control residual was reproduced, not assumed: replaying
  `experiments/gl-c-comp-event14/result.bgra` against the sealed checkpoint 15
  gives 5 pixels / max 1; `gl-c-comp-event15` against checkpoint 16 gives 4,645
  / max 86, with zero alpha differences in both pairs.
- No protected comparison policy, frozen checker, fixture, authoritative
  control or sealed 904 reference was weakened.

## Remaining risks and blockers

1. **Scientific: authentic live-GL control equivalence (B2/B3).** `blocked`.
   The stored 904 control is unqualified at checkpoint 15 (5/1) and 16
   (4,645/86); `metal_blame_allowed=false`. Draw 14 has depth test/write enabled
   and the capture is marked multisample. Draw 1 targets framebuffer 6, not
   `fb:0`; the first `fb:0` draw is 14, and the event-7 `fb:0` depth clear is 1,
   which the one-draw control reproduces. The drawable state (`GL_SAMPLES`,
   `GL_SAMPLE_BUFFERS`, bit depths) and prior-frame contents were never observed
   in 904. **The 904 measurements are historical and are preserved unchanged;
   do not create a new capture with the 904 seal.** New observations require a
   newly sealed capture, paired with a new original-API control from that
   capture's own stream, resources and checkpoint. Only zero differing BGRA
   pixels permit a Metal residual to be considered; otherwise name the next
   missing observation. Do not treat the five-pixel residual as parity or
   substitute a window/10-bit FBO.
2. **Campaign claim ledger integrity.** `unresolved`. Corrupt, truncated or
   unreadable `campaign/claims.jsonl` is currently read as fewer or zero
   attempts, so the validation budget can be reset. The fix must fail closed,
   preserve `flock` atomicity and idempotent claims, and add cross-process and
   interrupted-write regressions.
3. **Original-API control provenance/authority.** `unresolved`. Pixel equality
   plus optional drawable/prestate does not bind the control to the sealed
   frame, stream, resource, checkpoint or a completed original-API execution.
   An exact image from a different seal can qualify. The fix must separate
   exact comparison, authenticated qualification and attribution permission,
   and keep the independent correction checker unchanged.
4. **Authentic compressed same-seal comparison.** BC1 alpha and orientation are
   verified against the decoded path; an authentic 904 decoded-vs-original-block
   comparison remains.
5. **Per-session isolation.** #256 retains the campaign-wide claim ledger and
   host-wide lease; its final head is audit-reviewed but has no formal GitHub
   approval. Direct CLI `--session-id` can create an empty session root before
   `prepare` (nonblocking).
6. **Native cache cost.** #248 keys native functions on MSL bytes; correctness
   is tested, but per-lookup hashing was not profiled on an authentic 326-event
   frame.
7. **Efficiency.** D1 records honest A/B numbers (3 confirmed launches in A, 3
   focused runner calls in B, GPU unknown); three paired trials of the new
   workflow were not run. They remain `outstanding`; no wall-time improvement
   is claimed.
8. **Docs.** #252 was merged before its review's state corrections; #257 fixed
   part of the stale page. This follow-up fixes the post-merge `main`, the
   #258 head/merge, the remaining open-PR text and the unsupported approval
   claims.

## Merge recommendation

The merged stack needs no re-merge. #258 was merged at `d29f45f0` only after
its independent exact-head review at `8a5d4bc2`; that review is recorded on the
PR as a review comment, not an `APPROVE` state. This documentation follow-up
requires its own independent exact-head review before merge. The ledger and
control-authority fixes are separate P0 PRs and each requires independent
review of its own final SHA. The scientific blockers are not merge blockers for
the infrastructure; they are blockers for any claim of exact GL/Metal parity or
Metal blame.
