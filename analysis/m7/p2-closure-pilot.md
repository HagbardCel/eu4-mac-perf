# M7-P2 closure — bounded model-driven pilot and acceptance

Date: 2026-11-19. Starting revision: `6e3b7913ec4f7dacf69e6520674bb43716aba6f9`
(PR #212 merge; PRs #210, #211 and #212 are all ancestors of `origin/main`).

Status: **one authentic model-driven pilot ran and was independently accepted
offline.** M7 operational acceptance is **not** complete: #212 merged without a
recorded approving review, no live event-71 capture was run, and the renderer
correction/root cause is still unknown. No PR is merged; independent review of
the final head is the gate.

## 1. Merged state and gates

| Item | Value |
|---|---|
| Starting SHA / `origin/main` | `6e3b7913ec4f7dacf69e6520674bb43716aba6f9` |
| #210 merge | `b4a1f310` (ancestor) |
| #211 merge | `246d7e08` (ancestor) |
| #212 merge | `6e3b7913`; head `668b40ed` |
| Working tree at start | clean; tree identical to `origin/main` |
| Required `portable` check on `6e3b7913` | **success**, completed 2026-11-19T09:03:09Z |
| Offline manifest | `automation.test_profiles verify --profile offline` → `ok=true`, 0 problems |
| Focused P2 selection (pre-fix) | run `20261119T091239Z-c287d4c4`: 382 ran, 369 executed, 13 classified skips, 0 failures |
| Focused P2 selection (post-fix) | run `20261119T092932Z-e557e673`: passed, 0 failures |
| Full offline gate | `20261119T092938Z-23b4e94b`: 3,763 ran, 3,721 executed, 42 classified skips, 0 failures, 675.7 s |

### Governance gap (recorded, not repaired)

#212 was merged by the owner at 08:58:54Z with **no `APPROVED` review** in its
history; GitHub still reports `reviewDecision: REVIEW_REQUIRED`. The same
pattern applies to #210 and #211. Branch protection is enabled (`portable`
strict, 1 approval, dismiss stale, require last-push approval, enforce admins,
conversation resolution), and was not disabled or weakened. The API cannot say
whether the merge used an administrative bypass or a transient protection
change; either way the documented gate was not effective. The closure PR must
receive a fresh independent approval of its exact final head. A green CI run is
not approval. Full record: [P2 governance](p2-governance.md).

## 2. Case-selection correction

The old P2 objective selected the historical P0 case
`.local/m7/p0-checkpoint-142/shadow/case`. That case verifies but has **no
producer receipt**, so baseline reuse correctly fails with
`producer_identity_missing`. The verified producer-bound candidate is
`.local/m7/p2/producer-case-v5` (`m7-p2-producer-bound-v5`), which exists and
verifies with `producer_identity=verified`.

The objective now selects the producer-bound case. The historical case remains
read-only context: it supplies the in-process diagnostic report but is not
relabelled, copied over the producer case, or used to manufacture a receipt.
The two evidence classes are kept distinct:

| Evidence | Historical P0 case | Producer case v5 |
|---|---|---|
| Case verification | ok | ok, 1,246 files, graph complete |
| Producer receipt | absent | verified |
| Diagnostic report | in-process `gfx-frame-diagnosis/v1`, identity verified | standalone `gfx-independent-replay/v1`; `frame_shader_identity=unverified_session` by design |
| Classification | `difference_with_partial_state_coverage` | `incomplete_diagnostics` (standalone) |
| Baseline reuse | rejected (`producer_identity_missing`) | reusable, fingerprint `dcf83b9ec3e23e54f43da360e989bb88c21b970513062f0b6bbe6242d25bca48` |

The producer case's identity:

- case manifest SHA-256 `9af1c29deed44d81ed95abb8d6258776615ba269df2b5b65ab4d706d307c2a66`
- frame seal SHA-256 `794c482d49767b0b0aa356f5fc28919b9c2a57a33c88f7112152efd8c07d0ca6`
- capture `session a3111e0d-9b76-4b23-910d-38bd48c9aa93`,
  `capture_frame_id=1`, event stream
  `62cfdae407ecf38cbed0c98a956c5115203de12a85d46fd9b92e3d1c2dd9778c`,
  present/scene 617
- producer executable `3ef85b04863e9abbb29746a3f540a50926f70b4e3e2f80976000ccdd7bf03ca6`,
  build fingerprint `63cf3649220bea66deb3ebfae2f6243c6f3588f069e90a5ef58a064f965ad3ca`,
  build stamp `f6de97f30c89f117c7822eba468119de18e2621f841a077a4c3f185ba9909afa`

## 3. One bounded model-driven session

`dsh/scripts/run-pilot.sh` was used unchanged as the launcher; no new agent
orchestrator was created. A run-specific objective was written at
`.local/m7/p2/closure/objective.md`; the tracked P2 objective was corrected to
the same case. The session was wrapped by an external 20-minute deadline
(`.local/m7/p2/closure/run_with_deadline.py`), with an offline-only environment
(`EU4_TEST_PROFILE=offline`, `EU4_OFFLINE_ONLY=1`). Exactly one session ran.

| Item | Value |
|---|---|
| Provider / model | `opencode-go` / `deepseek-v4.1-flash` |
| DSH / Node / Python / NumPy | `0.2.2-rc.1` / `v26.13.1` / `3.14.10` / `2.5.5` |
| Session id | `session-fbd20740-656f-462c-9f28-7a33991a4051` |
| Turns / model calls | 1 / 13 |
| Wall time | 314.55 s (5.24 min) |
| External deadline | 1,200 s; **not hit** |
| Tool calls | **20** total: 2 native, 9 shell, 6 read, 2 glob, 1 write |
| Native tools executed | `eu4_case_inspect` (1), `eu4_re_query` (1) |
| Returned output | 85,622 chars (efficiency export); 68,616 UTF-8 bytes of raw tool-result payload |
| Oversized / truncated | 1 / 0 |
| Failed tool calls / shell nonzero | 0 / 2 (both exploratory NumPy scripts, recovered) |
| EU4 launches | **0** |
| Replays / captures | **0** / **0** |
| Test invocations | 0 |
| Repeated exact calls | 0; one file read twice with different limits (`automation/dsh_pilot.py`) |
| Tool wait | 4.057 s (1.29% of elapsed) |
| Tokens | 524,175 total: 65,349 input, 432,256 cache read (82.46%), 26,570 output |

The model called `eu4_case_inspect` first, then the recorded baseline check
(`reusable=true`, no replay), then used bounded reads, NumPy pixel arithmetic,
one targeted `eu4_re_query resolve 0x10ce96e` (no match), and wrote only the
conclusion. It did not run `eu4_diff_run`, `eu4_capture probe`, a live capture
or a game executable.

The producer-bound acceptance fix was applied to `automation/dsh_pilot.py`
while the turn was running; the model read the validator for the conclusion
schema. Its self-validation is therefore **not** treated as independent
evidence. The `validate` and `summarize` results in §4 were rerun after the
turn against the final code and the exact producer case.

## 4. Conclusion and independent verification

Outcome: **`requires_additional_evidence`**. No correction; no root cause; no
causal interval claimed. Starting classification `incomplete_diagnostics`
(standalone replay), first observed divergence event 142 `fb:0`, producer
event 141 / program 331, root cause `not_established`.

| Hypothesis | Status | Result |
|---|---|---|
| H1: the event-142 difference is dominated by a systematic 1-LSB downward quantization-scale offset | supported (signature only) | 94.2823% of 871,620 differing pixels are exactly 1 LSB; 162 are ≥8 LSB; 98.35% B / 96.87% G / 97.07% R of ±1 errors are −1 (Metal lower); 224/256 tiles and all 16 row bands; ≥8-LSB tail is edge-localized (5.14× reference gradient). Final frame: 77.29% exactly 1 LSB. |
| H2: the event-141 draw is the producer | insufficient | `prior_producers` events 134–141 are all program 331; only `fb:0` checkpoint at or before 142 is the divergent one; P1 binding audit 2,739/2,739 correct; `eu4_re_query` resolve `0x10ce96e` returned no match. Draw 141 cannot be isolated from 134–140. |
| H3: unsupported `gl_dither`/`multisample` explains it | insufficient | Captured `dither=true`, `multisample=true`; no A/B toggle and no per-state evidence. The pixel signature is compatible with more than one mechanism; neither state is named as the cause. |

Independent reproduction (this acceptance run, not the model's own script):

- event-142 checkpoint `capture/blobs/default-checkpoint-d77fa555….bin` vs
  `replay/intermediate-event142-tex0.bgra`: **871,620** differing pixels, max
  **103**, **94.2823%** exactly 1 LSB, **162** ≥8 LSB — exact match;
- final `capture/frame-reference.bgra` vs `replay/metal-frame.bgra`: **1,160,291**
  differing pixels, max **70**, **77.29%** exactly 1 LSB — exact match;
- first divergence event 142 `fb:0` agrees with the historical in-process
  authoritative report (`gfx-frame-diagnosis/v1`, identity verified) and with
  the producer case's standalone replay report;
- the unresolved interval remains `[0,142]` with `start_event=null`,
  `lower_bound=unbounded`; event 71 is a **new-capture** request and cannot
  bound the existing frame.

### Acceptance commands

```sh
python3.14 -m automation.dsh_pilot validate \
  --conclusion .local/m7/p2/closure/pilot-conclusion.json \
  --case .local/m7/p2/producer-case-v5          # ok=true, 0 problems

python3.14 -m automation.dsh_pilot summarize \
  --run .local/m7/p2/closure/run \
  --conclusion .local/m7/p2/closure/pilot-conclusion.json \
  --case .local/m7/p2/producer-case-v5          # ok=true, 0 problems
```

The native gate records `native_executed=[eu4_case_inspect, eu4_re_query]`,
`ok=true`, no failed or unfinished native calls. The summary binds the session
to the producer case and reports `eu4_launches=0`, `native_tool_count=2`,
`tool_count=20`.

### Acceptance-tool correction (minimal)

Before this closure, `dsh_pilot` required an in-process
`diagnostic-report.json` for **every** acceptance. The producer-bound case has
no such report, so both commands failed with `case_diagnostic_undeclared` even
though the case verifies and its baseline is reusable.

The fix adds a producer-bound evidence path: when a case declares no in-process
report, a `gfx-independent-replay/v1` report is accepted only if the case's
producer receipt verifies and the report's embedded capture identity matches
the frame seal. That evidence can supply the **upper** observation of an
**unbounded** interval. A **bounded** interval still requires the in-process
verified report, so the strongest causal guarantee is not weakened. The launch
count and case verification gates are unchanged. Regressions:
`tests.test_dsh_pilot` (61 tests, including three new producer-bound cases).

## 5. Efficiency comparison with P1

Both sessions are real model turns; the P2 deterministic two-call interface
demonstration is **not** used as the efficiency measurement.

| Metric | P1 (2026-11-18) | P2 (2026-11-19) | Change |
|---|---|---|---|
| Tool calls | 80 (2 native, 66 shell) | **20** (2 native, 9 shell) | −75.0% |
| Model calls | 72 | **13** | −81.9% |
| Wall time | 1,220.13 s (20.3 min) | **314.55 s** (5.24 min) | −74.2% |
| Tokens (total) | 10,084,703 | **524,175** | −94.8% |
| Cache read share | 97.09% | 82.46% | — |
| Returned chars | 400,666 | **85,622** | −78.6% |
| Oversized / truncated results | 6 / 2 | **1 / 0** | — |
| Tool wait | 34.131 s (2.8%) | **4.057 s** (1.29%) | −88.1% |
| Replays during pilot | 0 | **0** | — |
| EU4 launches | 0 | **0** | — |
| Tests | 0 | 0 | — |

Correctness comparison: both sessions ended with
`requires_additional_evidence`, no correction and no root-cause claim. P1
refuted positional/ordered dither and coverage-edge MSAA signatures and found
the program-331 producer insufficient. P2 independently reproduced P1's
94.28% / 162-pixel figures, added the signed −1 LSB bias and spatial
uniformity, and made the producer uncertainty sharper (draws 134–141 all share
program 331; the targeted caller lookup found no match). Neither proves the
mechanism; P2 does not overclaim.

## 6. Live-capture qualification (not run)

Phase D was **not executed**. The offline pilot succeeded and did not expose a
defect in the native capture path; that path already has separate live
qualification (M7 PR 1/PR 2). The requested event-71 `fb:0` checkpoint is a
diagnostic need for a **new** frame, not a validation of the capture path, and
it can never bound the existing event-142 observation. A live launch would add
process/display risk without changing the pilot's accepted conclusion. The
request is preserved for a separately authorized bounded capture:

- event 71, `fb:0`, new sealed frame, its own capture identity;
- never combine its observations with the historical event-142 frame to
  manufacture a causal interval;
- at most one EU4 process, one sealed scene frame and one shadow Metal
  execution, with BetterDisplay/process-ownership checks.

## 7. Offline gate and repository state

The closure full offline gate (`20261119T092938Z-23b4e94b`,
`--profile offline --min-tests 3000`, label `p2-closure-full`) passed:
**3,763 ran, 3,721 executed, 42 classified skips, 0 failures, 0 errors,
675.7 s**. The three added tests are the producer-bound acceptance
regressions. The earlier post-merge full gate (`20261119T072637Z-551b7e55`)
was 3,760 ran / 3,718 executed / 42 skipped / 0 failures in 1,318.3 s.

Large evidence (case payloads, raw event stream, session export, efficiency
report, native gate, validation/summary JSON) stays under
`.local/m7/p2/closure/` and is not committed. The closure commit contains only
the corrected objective, compact reports/docs, the acceptance fix and its
regressions.

## 8. Remaining limitations and readiness

Limitations carried forward:

- M7-P2 implementation is merged; its **independent final-head approval is
  missing**, and M7 operational acceptance is not complete.
- The producer case is `incomplete_diagnostics` by design; it is not a
  qualified match. The historical in-process diagnosis supplies the
  authoritative divergence but not producer identity.
- `gl_dither` and `multisample` remain unsupported; no A/B experiment exists.
- The pre-event-142 causal interval is unresolved; event 71 needs a new capture.
- No multi-checkpoint same-frame capture exists yet.
- The systematic −1 LSB downward offset is characterized, not explained; the
  producing stage (shader math, blending, resolve/quantization, or unsupported
  state) is unknown.
- No live capture was run in this closure.

Readiness: **READY WITH LIMITATIONS for another bounded, offline,
model-driven diagnostic task** against an existing verified producer-bound
case. **Not ready** for unattended renderer migration, a live diagnosis
campaign, or a claim of M7 operational acceptance. The next task should be the
single new-capture event-71 experiment, not a speculative Metal fix.
