# Harness remediation integration report

Date: 20 November 2026. This report records technical readiness, independent
approval and merge state. It is not a claim that every scientific blocker is
closed.

## Merged (independent approval on the exact final head)

| PR | Final head | Merge commit | Focused verification |
|---|---|---|---|
| #241 exact verdicts | `3be433f7` | `1e03b54a` | 241 ran / 240 executed / 1 skip; portable CI |
| #242 attribution | `7bcaaa1c` | `ced6cf25` | 92 pass; generator byte-identical |
| #243 native builds | `f62c7f18` | `befd49cc` | 34 executed / 12 fixture skips; fixture-backed 54 |
| #244 compressed textures | `cd4245cf` | `8822c03c` | 18/18 native; transparent-index alpha + orientation |
| #245 sampler/raster | `ada20dbc` | `fb766200` | 68 executed / 12 fixture skips |
| #246 publication/discovery | `8c17f779` | `d45a9079` | 155 ran / 154 executed / 1 skip |
| #247 reconnect context | `ab28f2da` | `8c98db67` | 115/115; plugin 44/44 |
| #248 identity/recipes | `1bffc7cb` | `755c20c1` | 118 executed; native stale-shader test |
| #249 terminal capture | `555447fb` | `c9576f4f` | 182 ran / 181 executed / 1 skip |
| #250 reservations/no-progress | `401d6863` | `3dabc6fe` | 197 ran / 196 executed / 1 skip |
| #251 physical work | `050f9272` | `845d7df6` | 153 ran / 152 executed / 1 skip |
| #252 knowledge/matrix | `275df332` | `4cc0ed80` | 45 ran / 44 executed / 1 skip; strict links |
| #253 904 three-way | `2596b50e` | `f1271302` | offline recomputation only |
| #254 B2/B3 qualification | `63a3148e` | `d5452a79` | 6/6; 904 control unqualified |

Portable CI passed for every merged head. Independent review of the exact final
SHA was recorded for #241, #242, #243, #244, #245, #246, #247, #248, #249,
#250, #251 and #254. #252's independent review requested doc-state corrections;
#257 supersedes those corrections.

## Follow-ups

| PR | Head | State |
|---|---|---|
| #255 issue #234 hardening | `6b4c4bff` | whitespace replay-input regression added; awaiting final confirmation |
| #256 per-session isolation | `e6615e17` | result-row history fix restores `max_identical_retries`; awaiting final confirmation |
| #257 final docs reconciliation | `b35c1519`+ | merged-state/head corrections; awaiting review |

## Verification

- Focused/neighbor suites for every changed module passed on the reviewed head
  (commands and counts in the coverage matrix).
- #250's earlier full offline gate: 3,993 ran / 3,928 executed / 65 skipped /
  0 failures. The post-amendment integrated gate is recorded with the final
  merge candidate; the amendment touched accounting/doc paths only.
- Native checks executed on arm64 macOS: compressed texture host 18/18,
  render-state host 10/10, native MSL cache regression 1/1, frame-trace writer
  55/55, texture versions 6/6, shadow receipt 8/8, target lifetime 3/3, blob
  verifier 6/6. Fixture-dependent skips are named (canonical M6 frame absent,
  copy-bearing scene-75 absent).
- No protected comparison policy, frozen checker, fixture, authoritative
  control or sealed 904 reference was weakened.

## Remaining risks and blockers

1. **Scientific: live-GL control equality (B2/B3).** `blocked`. The stored
   904 control is unqualified at checkpoint 15 (5/1) and 16 (4,645/86);
   `metal_blame_allowed=false`. Present 904's drawable samples/bit depths were
   never captured. The recorder now observes them only when
   `GL_DRAW_FRAMEBUFFER_BINDING == 0`. Next test: one targeted capture with the
   same seal, then the same selected-draw control; require exact four-channel
   equality before Metal blame. Do not treat the five-pixel residual as parity.
2. **Authentic compressed same-seal comparison.** BC1 alpha and orientation are
   verified against the decoded path; an authentic 904 decoded-vs-original-block
   comparison remains.
3. **Per-session isolation.** #256 retains the campaign-wide claim ledger and
   host-wide lease; its final review is pending. Direct CLI `--session-id` can
   create an empty session root before `prepare` (nonblocking).
4. **Native cache cost.** #248 keys native functions on MSL bytes; correctness
   is tested, but per-lookup hashing was not profiled on an authentic 326-event
   frame.
5. **Efficiency.** D1 records honest A/B numbers (3 confirmed launches in A, 3
   focused runner calls in B, GPU unknown); three paired trials of the new
   workflow were not run.
6. **Docs.** #252 was merged before its independent review's state corrections;
   #257 fixes the stale status page, heads, 615 wording and issue-open notes.
