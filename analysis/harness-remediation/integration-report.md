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
| #259 final state/docs | `2142f071` | `d5f6b73a` | Accepted; corrected heads, scientific language, P0 findings |
| #260 campaign ledger integrity | `63d00d31` | `1d7e37df` | Accepted; fail-closed claim reads, cross-process and interrupted-write tests |
| #261 GL control authority | `60e70b62` | `4bf03285` | Accepted; fail-closed authenticated control gate, empty renderer registry |

Portable CI passed for every merged head. That is not independent approval.
GitHub has no `APPROVE` review for #241–#261; the earlier report's claim that an
independent review was recorded for all of them was not supported by the review
records. Each follow-up's independent exact-head review is recorded on its PR as
a review comment, not an `APPROVE` state, because the only available GitHub
identity is the author. The audit in [audit-20261120.md](audit-20261120.md) is
technical review evidence, not a formal approval.

## Follow-ups

| PR | Head | State |
|---|---|---|
| This final documentation follow-up | this PR | Open; records the merged P0 fixes, verification and scientific stop; strict links and docs tests required |
| Three paired efficiency trials | not run | `outstanding`; no wall-time improvement claimed |
| Renderer registry population | not opened | Blocked by design: a hand-written receipt can impersonate a registered renderer until the receipt is bound to the renderer (signed/registered key). Do not populate `AUTHORITIES` before that binding. |

## Verification

- Audit aggregate at `a0e3a4e8`: 29 modules, **852 ran / 849 executed / 3
  skipped / 0 failures** (`20261120T185637Z-1a7c318d`).
- #258 hardening at `8a5d4bc2`: **132 ran / 131 executed / 1 skipped / 0
  failures / 0 errors**; neighbours **162 executed / 0 failures**. The same
  new tests fail on base `a0e3a4e8` (8 failures + 2 errors), so they are genuine
  regressions. Portable CI passed at the reviewed SHA.
- #260 ledger at `63d00d31`: **103 ran / 103 executed / 0 failures**;
  neighbours **214/214**; base `d5f6b73a` with the new tests: 14 ran / 14 error
  records (missing `ClaimLedgerError`) and the bridge test fails open.
- #261 control at `60e70b62`: **32/32 qualification**, **353 ran / 352
  executed / 1 skipped / 0 failures** with neighbours, plugin **9/9**. The
  registry is empty, so the gate authorizes no blame; real 616 draw 14 /
  checkpoint 15 verifies 1,123 blobs but remains unqualified.
- Targeted native suites on merged `main`: **255 ran / 243 executed / 12
  skipped / 0 failures** (compressed textures, render state, translation/cache,
  checkpoint reconciliation, control qualification, campaign guard, claim
  ledger, continuous supervisor, Metal replay; runner
  `20261120T213558Z-2eac8514`). The 12 skips are the declared
  canonical-M6-frame and copy-bearing-scene-75 fixture absences.
- Full offline profile on the P0 integration tree: **4,209 ran / 4,144 executed
  / 65 skipped / 1 failure / 6 errors**. The failures are the border-loop native
  harnesses (`clang -Werror` build) and the oracle flood-process test; the same
  seven failures reproduce on base `1d7e37df`, so they are pre-existing native
  toolchain failures, not skips and not regressions from these changes. Report
  them as failures; do not convert them to skips.
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
   The first non-exact checkpoint in 904 is 15 (4 px / max 1, first differing
   pixel (209,50), zero alpha-differing pixels). The stored control is unqualified
   at 15 (5/1) and 16 (4,645/86); `metal_blame_allowed=false`. The drawable
   state (`GL_SAMPLES`, `GL_SAMPLE_BUFFERS`, bit depths) and prior-frame
   contents were never observed in 904. **The 904 measurements are historical
   and are preserved unchanged; do not create a new capture with the 904 seal.**
   New observations require a newly sealed capture, paired with a new
   original-API control from that capture's own stream, resources and
   checkpoint, and a renderer registered only after its receipt binding is
   reviewed. Only zero differing BGRA pixels permit a Metal residual to be
   considered; otherwise name the next missing observation. See
   [scientific-continuation-20261120.md](scientific-continuation-20261120.md).
2. **Campaign claim ledger integrity.** `fixed` in #260. Corrupt, truncated or
   unreadable state refuses with an explicit integrity refusal before the
   checker; cross-process and interrupted-write regressions pass.
3. **Original-API control provenance/authority.** `fixed` in #261, fail-closed.
   The gate derives the checkpoint from the seal-verified event stream, verifies
   all referenced blobs, executes a challenge-bound renderer, compares captured
   live bytes and refuses blame unless the renderer is in the checked-in
   registry. The registry is empty; populating it requires a separate reviewed
   change that binds the receipt to the renderer.
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

The merged stack needs no re-merge. #258, #259, #260 and #261 were merged only
after independent exact-head review; each review is recorded on its PR as a
review comment, not an `APPROVE` state. This final documentation follow-up
requires its own independent exact-head review before merge. The fail-closed
control gate authorizes no blame while `AUTHORITIES` is empty; populating it is
a separate reviewed change that requires a renderer-bound receipt. The
scientific blockers are not merge blockers for the infrastructure; they are
blockers for any claim of exact GL/Metal parity or Metal blame.
