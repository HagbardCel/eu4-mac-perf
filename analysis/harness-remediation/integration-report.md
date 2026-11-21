# Harness remediation integration report

Date: 21 November 2026. This report records technical readiness, independent
review and merge state for the harness remediation. It is not a claim that every
scientific blocker is closed; the definitive disposition is
[closure.md](closure.md).

## Merged (independent review of the exact final head)

| PR | Final head | Merge commit | Focused verification |
|---|---|---|---|
| #241 exact verdicts | `3be433f7` | `1e03b54a` | 241 ran / 240 executed / 1 skip |
| #242 attribution | `7bcaaa1c` | `ced6cf25` | 92 pass; generator byte-identical |
| #243 native builds | `f62c7f18` | `befd49cc` | 34 executed / 12 fixture skips |
| #244 compressed textures | `cd4245cf` | `8822c03c` | 18/18 native |
| #245 sampler/raster | `ada20dbc` | `fb766200` | 68 executed / 12 fixture skips |
| #246 publication/discovery | `8c17f779` | `d45a9079` | 155 ran / 154 executed / 1 skip |
| #247 reconnect context | `ab28f2da` | `8c98db67` | 115/115; plugin 44/44 |
| #248 identity/recipes | `1bffc7cb` | `755c20c1` | 118 executed; native stale-shader |
| #249 terminal capture | `555447fb` | `c9576f4f` | 182 ran / 181 executed / 1 skip |
| #250 reservations/no-progress | `401d6863` | `3dabc6fe` | 197 ran / 196 executed / 1 skip |
| #251 physical work | `050f9272` | `845d7df6` | 153 ran / 152 executed / 1 skip |
| #252 knowledge/matrix | `275df332` | `4cc0ed80` | 45 ran / 44 executed / 1 skip |
| #253 904 three-way | `2596b50e` | `f1271302` | offline recomputation |
| #254 B2/B3 qualification | `63a3148e` | `d5452a79` | 6/6 |
| #255 issue #234 hardening | `090f2b52` | `c289adf4` | 174/174 |
| #256 per-session isolation | `c403f06e` | `f0bfa16c` | 246/246 |
| #257 final docs | `b1bacce8` | `a0e3a4e8` | docs selection 70 ran / 69 executed |
| #258 envelope/checkpoint | `8a5d4bc2` | `d29f45f0` | 132 ran / 131 executed / 1 skip |
| #259 docs follow-up | `2142f071` | `d5f6b73a` | 70 ran / 69 executed / 1 skip |
| #260 claim ledger | `63d00d31` | `1d7e37df` | 103 ran / 103 executed; neighbours 214 |
| #261 control authority | `60e70b62` | `4bf03285` | 32/32 qualification; neighbours 353 |
| #262 final P0 state | `7f553813` | `25ee46db` | docs selection |
| #263 attribution recomputation | `5ae0ccff` | `3fe7c602` | 39/39; neighbour 116 ran / 115 executed |
| #264 ledger head witness | `3cb4d39d` | `7cd4a072` | 27/27; module+bridge 116/116 |
| #265 flood cleanup retry | `fbe23d51` | `118c070c` | 7/7; order selection 123 ran / 122 executed |
| #266 anchor/session-root | `dc219a6d` | `52cc1e89` | 93/93; neighbours 241/241 |
| #267 plugin unload | `f5301f69` | `d3a0ce6b` | node 50/50 |
| #268 clean build dirs | `c3fbeb77` | `6ba77c05` | 11/11 clean tree |
| #269 closure integration | `d3a0ce6b` | `fecff675` | merge-only; reviewed exact tree |

Portable CI passed on every merged head. #263–#268 have an independent review
comment at their exact heads; #251–#257 have no independent review comment on
GitHub (audit verdict/author verification/CI only); #241–#250 were reviewed by
Codex/audit at earlier commits. #269 is the merge-only integration of the five
remaining reviewed heads. GitHub records the comments as `COMMENTED`, not
`APPROVE`, because the only available identity is the author.

## Verification

- Full offline profile, baseline `25ee46db`: 4,221 ran / 4,156 executed / 65
  skipped / 0 failures / 6 errors (clean-build defect, fixed in #268),
  1,244.7 s (`20261120T224312Z-911ef529`).
- Full offline profile, candidate `ba8b7ba4`: 4,241 ran / 4,176 executed / 65
  skipped / 0 failures / 6 errors (same defect), 621.4 s
  (`20261120T230503Z-40f002f7`).
- Final integrated profile after #268: recorded in
  [verification-20261121.md](verification-20261121.md).
- Targeted native checks on arm64 macOS remain as reported at `4bf03285`
  (255 ran / 243 executed / 12 skipped).
- Sealed-904 compressed-vs-decoded comparison and cache profile completed
  offline; three paired efficiency trials completed. No parity claim.
- No protected comparison policy, frozen checker, fixture, authoritative
  control or sealed 904 reference was weakened.

## Remaining risks and blockers

1. **Scientific: live-GL control equality (B2/B3).** `blocked`, transferred to
   M7. The stored 904 control is unqualified at 15 (5/1) and 16 (4,645/86);
   `metal_blame_allowed=false`. The seal lacks drawable samples/bit depths and
   prior-frame contents. Next test: a newly sealed capture with those
   observations and a new original-API renderer from that capture's own
   stream/resources/checkpoint, bound to a registered authority; require zero
   differing BGRA pixels before Metal blame. The 904 measurements are preserved
   unchanged.
2. **Authentic compressed comparison.** Completed for 904: the block path is
   byte-identical to the stored shadow and closer to live GL than the decoded
   path (4 vs 858 pixels at checkpoint 15); the remaining 4 pixels are not
   explained by texture representation. No parity claim.
3. **Native cache cost.** Profiled: translation 3.211 s cold / 0.374 s warm,
   identity 26.8 ms, native replay 0.603 s, GPU unknown; native
   function/pipeline cache in-process only. No material regression.
4. **Efficiency.** Three paired trials completed; medians 7.525 s vs 7.575 s, no
   wall-time improvement claimed; payload 6,195,746 → 7,768 bytes, 0 confirmed
   EU4 launches.
5. **Harness boundaries.** The claim-ledger head is a local witness (deleting
   both files resets; a stale head leaves one uncommitted tail). A coordinated
   control+receipt rewrite matters only once `AUTHORITIES` is populated, which
   requires receipt-to-renderer binding. Worktree-scoped build serialization is
   an owned follow-up; the current gate policy is one suite per worktree.
6. **Docs.** The coverage matrix, `docs/status.md` and this report now name the
   final merged heads and no longer describe #256/#257 as open or `c289adf4` as
   current main.
