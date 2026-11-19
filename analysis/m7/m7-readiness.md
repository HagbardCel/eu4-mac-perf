# M7 final readiness report

Date: 2026-11-19 (Europe/Berlin)
Verdict: **NOT_READY** — the technical instrumentation works on authentic
evidence; the governance and autonomous-campaign gates are not met.

## Evidence categories (kept distinct)

| # | Category | Present evidence | Status |
| --- | --- | --- | --- |
| 1 | Authentic live multi-checkpoint capture and offline replay | Prior sealed 325-operation frame (session `5a4eb79e…`), four `fb:0` checkpoints, offline replay; interval [14, 20] | **Established** |
| 2 | Controlled two-round DSH correction of a seeded defect | `wt_m7_campaign`, task 2: seeded `RGBA8→BGRA8` format regression, genuine source fix `eb6a9b24`, checker-accepted (1,925,408/255 → 1,161,075/75) | **Established** (not a renderer-equivalence result) |
| 3 | Synthetic exact-match regression tests | Unit fixtures in `tests.test_correction_accept` (generated frames/replays) | **Established as unit coverage only** |
| 4 | Genuine, independently preserved exact-match regression cases | None available in the controlled campaign's worktree; the regression inventory was empty and recorded as a limitation | **Not established** |
| 5 | Uncorrected authentic event-20 rendering difference | Event 20 `fb:0` remains divergent (876,044 px, max 134); final frame 1,161,075/75 | **Open** |

These categories are not interchangeable: the controlled correction proves the
autonomous code→build→replay→checker loop; it does not correct the authentic
event-20 difference, and it is not backed by a genuine exact-match regression
case.

## Repositories and revisions

| Item | Value |
| --- | --- |
| Repository | `HagbardCel/eu4-mac-perf` |
| Verified `main` baseline | `2c00ea13061b0c950146e4e6e2eb93746d34d2fd` (merge of #213) |
| Worktree used | this checkout on the stacked branches below |
| PR 1 | #214 `m7/pr1-multi-checkpoint` @ `ea554cae` |
| PR 2 | #215 `m7/pr2-independent-acceptance` @ `01b3b60b` |
| PR 3 | #216 `m7/pr3-autonomous-campaign` @ `de2786bd` |
| PR 4 (pilot fix) | #217 `m7/pr4-blob-verifier` @ `7e4d3a96` |
| Report | #218 `m7/final-readiness` @ this head |
| EU4 executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| EU4 SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Replay host build fingerprint (after PR4) | `96d29ff8b09b23f6…` |
| Replay host producer snapshot SHA-256 | `eacecf8869a2ec12…` (bound by the replay receipt; the shared build may be rebuilt later) |

The PR heads are unmerged. No independent `APPROVED` review exists for any of
them, and no operator waiver has been recorded. Nothing was merged, no
auto-merge was enabled, and branch protection was not changed.

## Authentic capture

One owned EU4 process was launched; no retry or second launch was made. The
selection reused the previously successful event-142 experiment
(`0x103b3c8`, occurrence 300, scene boundary) and added the bounded
multi-checkpoint selection verified from the prior trace (all four candidate
events are draw operations in a 325-event frame).

```sh
EU4_CAMPAIGN_STATE=.local/m7/live-pilot/campaign-state.json \
python3.14 -m automation.m6_frame capture \
  --output .local/m7/live-pilot/capture \
  --callers 0x103b3c8 --min-draws 1 --occurrence 300 --boundary scene \
  --continuous --shadow --seal --max-frames 1 --max-pending 1 \
  --max-draws 512 --max-bytes 2147483648 --case-max-bytes 2147483648 \
  --copy-source --default-checkpoint-events 14,20,71,142 \
  --background --duration 240 --timeout 600 \
  --shadow-timeout-ms 120000 --shadow-wait-ms 180000
```

| Field | Value |
| --- | --- |
| Session / pid | `5a4eb79e-1de2-401a-ba0b-d3524e45c68d` / 67343 |
| capture_frame_id | 1 |
| present_index / scene_index | 612 / 612 |
| Boundary | scene (continuous, one frame) |
| Operations | 325 (315 draws, 9 clears, 1 copy) |
| Dimensions | 1728 × 1117, BGRA8, lower-left |
| Manifest SHA-256 | `c6bcc714886bd6c23613d5f1ce28269e95df06ee3a4a61d2a5f2433e68c6787c` |
| Event stream SHA-256 | `2c754402b7f001c95f7135facabe05a4dee48c61560756ab0511fdf6ee7720af` |
| Reference SHA-256 | `99e476d724f30e6970116c88358c3da82397f8eb873cb840bdc4ad8a83e80cbe` |
| Declared checkpoints | 14, 20, 71, 142 (all captured) |
| Seal | sealed, complete, stream validated, readback ok |

Process and desktop ownership:

- Display mode `background` on BetterDisplay `EU4 Test` (id 12), 1920×1200,
  `main=false`; window 1728×1117. The physical `Built-in Display` (id 1) was
  not selected.
- EU4 terminated with `terminated=true`, `killed=false`.
- The GOG `settings.txt` (`~/Documents/Paradox Interactive/Europa Universalis IV
  GOG/settings.txt`) is restored to its pre-capture SHA-256
  `62b4902a3a999ad3…dd496`. (The separate Steam `Europa Universalis IV`
  directory is untouched, mtime 2026-10-25.)

## Failure and recovery

The in-process Metal shadow execution failed closed **before GPU work**:

```text
frame 1 Metal replay failed: sealed_input_verification:
conflicting repeated blob reference seed-137637b6….bin
```

Localization (no additional capture): the event-14 `fb:0` checkpoint is
byte-identical to the frame seed. Content-addressed storage therefore returned
the existing `seed-1376….bin` name for the checkpoint. The seed record omits
`bytes`; the checkpoint record declares it. The sealed-input verifier stored
the omitted size as zero and rejected the declared size as a conflict.

Correction (PR #217, isolated commit, no renderer change): an omitted `bytes`
is unknown, not zero; declared sizes that disagree, conflicting digests and
on-disk digest mismatches still fail. The production verifier is exercised by
`tests.test_replay_blob_verifier` and accepts the authentic frame
(`blob_references=0`).

## Same-frame checkpoint comparisons (authentic replay)

Offline replay of the sealed frame through the rebuilt production host
(`.local/m7/live-pilot/replay-1`): 325/325 events encoded, 0 failed,
`command_status=4`, readback present, `stage=complete`. 11 checkpoints
requested and returned, 0 invalid.

| Event | Resource | Status | Mismatched pixels | Max channel error |
| --- | --- | --- | --- | --- |
| 11 | `tex:431` | match | 0 | 0 |
| 13 | `tex:432` | match | 0 | 0 |
| **14** | **`fb:0`** | **match** | **0** | **0** |
| **20** | **`fb:0`** | **divergent** | **876,044** (45.39 %) | **134** |
| 71 | `fb:0` | divergent | 874,649 | 134 |
| 142 | `fb:0` | divergent | 872,186 | 134 |
| 270 | `tex:433` | divergent (diagnostic) | 798,169 | 255 |
| 284 | `fb:0` | divergent | 1,236,121 | 99 |
| 286 | `tex:442` | divergent | 1,236,121 | 99 |
| 288 | `tex:447` | divergent | 303,917 | 94 |
| 290 | `tex:443` | divergent | 316,029 | 27 |

Final framebuffer: 1,161,075 / 1,930,176 mismatched (60.15 %), max channel
error 75, alpha identical, differing bounds full frame.

## First confirmed divergence and causal interval

- First confirmed divergent checkpoint: **event 20**, `fb:0`, a `draw`
  (`_GfxDrawIndexed`, caller RVA `0x10d4e50`, program 301).
- Candidate operation: **event 19** (the producer draw; event 20 observes its
  result).
- Causal interval: **start 14 → end 20**, `lower_bound=same_resource_match`,
  all observations from the same sealed capture (`session_id`,
  `capture_frame_id=1`, event-stream digest).
- Next discriminating checkpoint proposed by the existing machinery:
  **event 17**, `fb:0`, as a target inside *this* frame's interval. A future
  capture produces a new frame identity; its checkpoints cannot be combined
  with this frame to manufacture a tighter interval.
- Classification: `incomplete_diagnostics` because the offline standalone
  replay has no same-process identity report (`frame_shader_identity =
  unverified_session`). Pixels and execution are valid; this is not an
  equivalence claim. `rendering_state_coverage = partial`.

## Investigation, hypotheses and tests

The investigation was performed in this DSH session; no additional EU4 launch
was made.

- **H1 (supported):** the difference is introduced between events 14 and 20.
  Evidence: event 14 `fb:0` matches exactly; event 20 `fb:0` differs. No earlier
  observation is needed to bound this interval; a pre-event-14 difference is
  refuted as the cause of the event-20 state.
- **H2 (insufficient):** the event-19 indexed draw (program 301) produces the
  difference. Evidence: event 19 is the only producer between 14 and 20.
  Discriminating test: checkpoint `fb:0` at 17 and 19; **not run** — it needs a
  second capture, and the pilot launch budget is 1.
- **H3 (refuted):** the divergence predates the first checkpoint. Evidence:
  event 14 matches at every pixel.

No renderer correction was attempted: the evidence supports an additional
checkpoint (event 17), not a specific renderer change. That is the correct
`requires_additional_evidence` outcome.

The independent correction checker was exercised on a controlled one-pixel
fixture instead (PR #215): detected, corrected, accepted, pixel-perfect, with
adversarial rejection of fabricated statistics, substituted artifacts, stale
receipts, changed baselines, failed executions, foreign captures and hidden
regression failures.

## Resource accounting (externally enforced)

| Resource | Budget | Used | Enforced at |
| --- | --- | --- | --- |
| EU4 launches | 1 | 1 | before `startup.run_ops` |
| Sealed frames | 1 | 1 | recorded from the seal |
| Metal shadow executions | 1 | 1 (failed at input verification) | recorded from the capture result |
| Offline candidate replays | 2 | 1 | before the host is built |
| Renderer corrections | 1 | 0 | pilot budget |
| Wall clock | 1800 s | ~12 min to the result | checked before each action |
| Automatic merges | 0 | 0 | operator policy |

The guard refused the first offline replay because a per-resource exhaustion
was wrongly recorded as a campaign stop; the pilot control was corrected in
PR #217 and the stop was cleared with a recorded justification. All counts come
from the action sites, not the model's report.

Model/tool-call counts are not independently exported for this interactive Web
session; the headless pilot runner's efficiency export is the supported source
for those numbers. This is a reporting gap, not an enforcement gap.

## Offline qualification

- Focused suites for PR 1–4 passed (frame trace writer, frame graph, M7
  diagnosis, m6_frame, DSH pilot/bridge, correction acceptance, blob verifier).
- The full offline gate passed on the PR3 integration state (1140 s,
  `20261119T133445Z-ca3de126`).
- **Final-state gate after the final code change (correction allowance):**
  passed (721 s, `20261119T182911Z-a2cfafcc`, label `final-gate-20261119b`,
  0 failures).
- Earlier integrated gate after the Phase A corrections: passed (699 s,
  `20261119T170815Z-6afa4ac1`, label `final-integrated-20261119`).
- Earlier final-state gate: passed (877 s, 3848 tests,
  `20261119T145206Z-17c1021e`, label `full-pr4-final`, 42 skipped, 0 failures).
  Two earlier attempts failed on different, unrelated load-sensitive tests:
  `test_oracle_shadow_worker.ShadowWorkerTests.test_timeout_kills_the_process_group`
  and `test_r0_sample_qualification.R0AuthenticSampleQualification.test_authentic_sample_native_capture`.
  Each passes in isolation and its whole module passes; neither touches the
  changed code. They are recorded as flaky, not as regressions.
- The authentic capture and replay were bounded and fail-closed; no invalid
  cross-frame interval was constructed.

## Final remediation (Phase A, 2026-11-19)

- **#214** forwards `--checkpoint-max-bytes` for the singular and plural
  selection forms and omits absent capture identity from the checkpoint
  request. Two recorded legacy requests rebuild **byte-identically**
  (`cc7d69b9…`, `73af04fc…`), and their structures match.
- **#215** mocks producer identity in the correction fixtures (portable, no
  native host), enforces a mandatory regression inventory derived from the
  configured contract and the conclusion's own claims, validates full-frame
  execution/provenance before pixels, binds the comparator's readback to the
  producer receipt, and binds the claimed revision to the build's recorded
  source revision. Regression identity is now compared with **each
  regression's own seal**, with tests for a different sealed capture, a replay
  whose reference disagrees with its seal, and a receipt naming another
  capture.
- **#216** reserves atomically, validates the explicit reservation at the
  launch choke point (the redundant post-reservation budget check and the
  `env_extra` token were removed), passes the identity explicitly through
  `run_ops`/`gfx_launch.spawn`, keeps all-or-nothing launch/frame/shadow
  reservation, reaps the coordinator on failure, blocks repeated failures
  without a model reset, requires an operator token for recovery, and denies
  merges from command tools. New tests cover one launch under a budget of one,
  a second launch refused before any process, zero frame/shadow allowance
  before coordinator startup, continuous upper-bound reservation, no orphaned
  coordinator/reservation, and direct unreserved launches.
- **#217** retains only the repeated-blob verifier fix. Its native host accepts
  the preserved authentic capture (`blob_references=0`), and the five native
  verifier tests pass on macOS.
- **#218** is this report.

Portable CI on the final heads: **#214–#218 all pass**. The full offline gate
passed on the integrated final state (699 s, 0 failures).

## Controlled autonomous campaign (Phase C offline, 2026-11-19)

- Dedicated worktree `wt_m7_campaign`, controlled renderer-relevant fault
  committed as `71dbd4bb`, sealed differential case `m7-controlled-fault` with a
  producer-verified baseline.
- **Two genuine native DSH goal rounds** in this same Web session: round 1
  localized the earliest same-frame divergence (event 20, `fb:0`, program 301,
  producer 19, interval [14, 20]); round 2 identified the format-translation
  regression, applied a minimal source correction (`eb6a9b24`), rebuilt the
  replay host, replayed the same sealed case, and submitted to
  `automation.correction_accept`.
- Independent checker: **accepted**, improved from 1,925,408/255 to
  1,161,075/75, 0 problems, `pixel_perfect=false` (offline identity
  `unverified_session`, partial state coverage). No exact-match regression
  cases were available as sealed cases; recorded as a limitation.
- Budget: 1 replay of 4, 1 correction of 2, 0 EU4 launches, under 30 minutes.
  No merges. The `corrections` allowance is now enforced at the
  `correction_accept.evaluate` boundary (atomic reservation before evidence),
  with CLI/API bypass tests.
- **Workflow caveat:** continuation was demonstrated through the running DSH
  Web session's native goal driver (profile `web`), not through a fresh
  `eu4-web` session booted by `run.sh --campaign … --campaign-state …`. That
  launcher requires an operator-created Web/TUI session; it was not exercised
  here.

### Documented `eu4-web` launcher validation (2026-11-19)

The documented path was exercised with a fresh offline sidecar and a fresh
external campaign state:

```sh
./dsh/scripts/run.sh --campaign dsh/campaigns/m7-launcher-smoke.json \
  --campaign-state .local/m7/launcher/state.json --help
```

Result: the launcher resolved the Python/native runtime, exported the campaign
state (0 launches, 0 frames, 0 shadow executions, 1 replay), created and
anchored the isolated seed workspace, printed the campaign objective and the
`eu4-web` session instructions, then exited through the Web app's `--help`
without starting a server or creating a session. It does **not** create the
goal: the operator must open the Web/TUI session and send the printed objective
there. That operator dependency is the reason a fresh two-round run through
this exact launcher is not recorded; the two-round evidence below comes from
the running Web session's native goal driver.

## Previous review findings and resolution (not independently confirmed)

The findings that drove the remediation rounds were addressed as follows; no
independent reviewer is available to confirm them, so they remain
self-assessed.

| Finding | Resolution | Evidence |
| --- | --- | --- |
| #214 byte budget only forwarded for the plural form; identity keys changed legacy requests | Forwarded for both forms; absent identity omitted; legacy request bytes/hashes preserved | `ea554cae`, graph tests, byte-identical recorded requests |
| #215 portable suite needed a native host; claims not independently recomputed; regressions optional; unbound readback; revision string unproven | Producer mocking; mandatory inventory; execution/provenance before pixels; canonical readback; build-revision binding; per-regression seal identity | `01b3b60b`, 26 acceptance tests incl. different-capture regression |
| #216 check-then-record, coordinator leaks, strategy reset, no merge boundary, launch reservation rejected the just-reserved allowance | Atomic reservations; pre-child multi-resource capture; coordinator reaping; operator-token recovery; merge denial; explicit reservation at the choke point; corrections allowance at acceptance | `82c09bfb`, `de2786bd`, boundary + budget tests |
| #217 scope creep into campaign budgets | Verifier fix only; budget changes moved to #216 | `7e4d3a96`, native verifier tests |
| #218 evidence conflated the controlled correction with authentic evidence | Five evidence categories separated; launcher validation and review blocker recorded | This report |

Previous governance failures remain documented: PR #212 and #213 merged
without an `APPROVED` review or an explicit waiver; GitHub reports
`reviewDecision: REVIEW_REQUIRED` for the earlier stack. `main` branch
protection currently requires **0** approving reviews, and the repository has
one collaborator.

## Final gate

| Condition | State |
| --- | --- |
| Multi-checkpoint capture and same-frame causal interval on authentic evidence | **Met** ([14, 20]) |
| Live capture preserves GL behavior, desktop state, process ownership | **Met** (settings restored, background display, terminated) |
| Metal replay/provenance rejects invalid or incomplete execution | **Met** (failed closed twice; fixed verifier still rejects conflicts) |
| Real DSH multi-round campaign demonstrated, not a one-turn pilot | **Met offline** (2 native goal rounds, checker-confirmed correction); the specific `eu4-web` + `run.sh --campaign` workflow was not exercised |
| Independent checker rejects fabricated improvements and accepts verified ones | **Met** (controlled fixture) |
| Controlled end-to-end correction cycle including regressions | **Met for the seeded defect** (genuine source fix, checker-accepted); no genuine exact-match regression case existed, and the authentic event-20 defect is untouched |
| Authentic live pilot captures, diagnoses, preserves evidence | **Met** (bounded same-frame interval) |
| Limits and stop conditions enforced independent of the agent | **Met** (guard + bridge limits; launch reservation validated at the choke point; corrections reserved at acceptance; refusal observed and recovered) |
| Focused, full offline, native, applicable live gates pass | Focused **met**; full offline **met** on the final state (699 s, 0 failures); native blob verifier **met**; portable CI **met**; the in-process shadow of the authentic pilot did not complete in-process |
| Independent final-head review or operator waiver; no self-merge | **Not met** — the repository has only the owner collaborator, no invitations, and branch protection requires 0 approvals; no eligible independent reviewer exists in this environment. No PR was merged. |
| Final reviewed report with exact revision and limits | **This report; not yet reviewed** |

## Missing evidence and minimum remediation

1. **Independent review of the exact final heads.** The repository must gain
   an eligible independent reviewer (or a recorded operator waiver); the
   current single-collaborator configuration and 0-approval branch protection
   are not an approval boundary. No PR has been merged.
2. **The documented `eu4-web` + `run.sh --campaign <sidecar>
   --campaign-state <state>` multi-round run.** The native goal driver was
   demonstrated in the running Web session, but a fresh `eu4-web` campaign
   session created through that workflow was not run.
3. **One explicitly authorized live capture** (budget `eu4_launches=1`) with
   `fb:0` checkpoints at `14,17,19,20` in a new frame, after the review gate
   passes. It must establish its own sealed identity and its own causal
   interval; its observations must never be combined with the previous frame's
   `[14, 20]` interval. Event 17 is only a target inside the new frame. A
   minimal renderer correction would then be verified by
   `correction_accept`. The authentic producing operation is not established.

## Unresolved limits

- The verdict is **NOT_READY** because the governance gate (independent review)
  and the documented `eu4-web` campaign workflow are unmet, and no new
  authorized live capture was performed.
- The controlled campaign's correction was a **texture-format translation
  regression** seeded for the exercise, not a remediation of the authentic
  event-20 defect.
- The Metal renderer is **not** equivalent to OpenGL: 60.15 % of pixels differ
  on this frame.
- The first confirmed difference is localized to `[14, 20]`; its producing
  operation (event 19 or an earlier draw 15–18) is not established.
- The offline standalone replay identity is `unverified_session` by design; no
  qualified equivalence claim is possible from it.
- `rendering_state_coverage` is partial; `gl_dither`/multisample remain
  unsupported.
- No live multi-round campaign has been run.

Raw artifacts: `.local/m7/live-pilot/` (sealed capture, replay, campaign state).
