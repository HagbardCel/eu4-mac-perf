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

Portable CI passed for every merged head. That is not independent approval.
GitHub has no formal `APPROVE` review for #241–#257; the earlier report's claim
that an independent review was recorded for all of them was not supported by
the review records. The audit in [audit-20261120.md](audit-20261120.md) is the
explicit independent verdict at the heads above.

## Follow-ups

| PR | Head | State |
|---|---|---|
| #258 envelope/checkpoint hardening | `fb717ead` | Open; 127 focused tests (126 executed / 1 skip) and 288 neighbors pass; independent review of the exact final SHA required |
| This documentation follow-up | this PR | Open; state and approval corrections only; strict links and docs tests required |

## Verification

- Audit aggregate at `a0e3a4e8`: 29 modules, **852 ran / 849 executed / 3
  skipped / 0 failures** (`20261120T185637Z-1a7c318d`).
- #258 hardening: **127 ran / 126 executed / 1 skipped / 0 failures**.
  Neighbors: **288 executed / 0 failures**.
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

1. **Scientific: live-GL control equality (B2/B3).** `blocked`. The stored
   904 control is unqualified at checkpoint 15 (5/1) and 16 (4,645/86);
   `metal_blame_allowed=false`. Draw 14 has depth test/write enabled and the
   capture is marked multisample, but 904 never recorded `GL_SAMPLES`,
   `GL_SAMPLE_BUFFERS` or drawable bit depths, and the one-draw control clears
   depth rather than reproducing the earlier depth-writing draw 1. Next test:
   one targeted same-seal capture of those drawable/depth observations, then
   the same selected-draw control; require exact four-channel equality before
   Metal blame. Do not treat the five-pixel residual as parity or substitute a
   window/10-bit FBO.
2. **Authentic compressed same-seal comparison.** BC1 alpha and orientation are
   verified against the decoded path; an authentic 904 decoded-vs-original-block
   comparison remains.
3. **Per-session isolation.** #256 retains the campaign-wide claim ledger and
   host-wide lease; its final head is audit-reviewed but has no formal external
   approval. Direct CLI `--session-id` can create an empty session root before
   `prepare` (nonblocking).
4. **Native cache cost.** #248 keys native functions on MSL bytes; correctness
   is tested, but per-lookup hashing was not profiled on an authentic 326-event
   frame.
5. **Efficiency.** D1 records honest A/B numbers (3 confirmed launches in A, 3
   focused runner calls in B, GPU unknown); three paired trials of the new
   workflow were not run.
6. **Docs.** #252 was merged before its review's state corrections; #257 fixed
   part of the stale page. This follow-up fixes the post-merge `main`, the
   #255 head, the remaining open-PR text and the unsupported approval claims.

## Merge recommendation

The merged stack needs no re-merge. #258 is technically ready after its focused
checks but must receive independent review of `fb717ead` before merge. This
documentation follow-up is ready after its own exact-head review. The
scientific blockers are not merge blockers for the infrastructure; they are
blockers for any claim of exact GL/Metal parity or Metal blame.
