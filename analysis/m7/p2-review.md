# M7-P2 — review request and final-head checklist

Status: **merged implementation without a recorded independent approval.** PR
[#212](https://github.com/HagbardCel/eu4-mac-perf/pull/212) merged as
`6e3b7913` on 2026-11-19T08:58:54Z; its final head was `668b40ed`. The review
history has no `APPROVED` state and GitHub still reports `REVIEW_REQUIRED`.
See [P2 governance](p2-governance.md) for the enforcement gap.

This file is now the checklist for the **closure PR**
[#213](https://github.com/HagbardCel/eu4-mac-perf/pull/213) (the bounded pilot,
corrected objective, acceptance-integrity repair and evidence report). A fresh
independent review of the closure PR's exact final head is the merge gate. CI
success, this file, or the author's own review is not approval.

## Merged base and closure branch

- Merged base: `6e3b7913` (`origin/main`, PR #212 merge). PRs #210, #211 and
  #212 are all ancestors.
- Closure work: one branch off `6e3b7913`; compact reports and documentation
  only, plus genuinely necessary regressions.
- Large captures, session exports and binaries stay under `.local/` and are not
  committed.

## What must be reviewed in the closure PR

1. **Pilot objective correction.** `dsh/pilots/m7-p2/objective.md` selects the
   producer-bound case `.local/m7/p2/producer-case-v5` and the recorded
   baseline, not the historical P0 case. Verify that the historical
   `diagnostic-report.json` was not relabelled as producer evidence.
2. **One bounded model session.** The raw event stream, session export,
   efficiency report, native gate, verification results and conclusion are
   preserved under `.local/`. Verify that exactly one model session ran, that
   the external deadline was enforced, and that no second session or automatic
   retry was started.
3. **Native-tool evidence.** The gate must show an actually executed native EU4
   tool from `events.jsonl`; advertised tools and bash-only runs do not count.
4. **Case and baseline identity.** The conclusion binds to the producer case
   manifest/seal and capture identity; baseline reuse is checked, not
   overwritten. A mismatch is reported, not forced.
5. **Same-capture causal discipline.** No new capture or historical checkpoint
   may bound the event-142 interval. Event 71 is a `requires_capture`
   recommendation, not observed evidence.
6. **Conclusion honesty.** No correction is claimed without measured before/
   after statistics on the identical sealed case; unsupported dithering/MSAA
   attribution is refused; `requires_additional_evidence` is a valid ending.
7. **Acceptance tooling and integrity.** Any change to
   `automation.dsh_pilot` must be the minimum needed to accept a
   producer-bound case's evidence without treating an unverified standalone
   replay as a qualified in-process diagnosis. Verify that the standalone path
   applies `full_frame_completion_problems()` to `replay/replay.json` and
   rejects failed, partial, timed-out, partially encoded and unsuccessful
   Metal execution, invalid report execution (including non-empty execution
   failures), invalid checkpoint validation, fault contamination (including
   `outcome.fault_injection=true`), report/replay execution-field mismatch and
   malformed/non-object reports. A verified producer receipt must not substitute
   for execution success. It must not weaken the bounded-interval lookup or the
   launch count, and a genuinely divergent completed replay must still pass.
8. **First-divergence claim.** The conclusion's
   `starting_diagnosis.first_divergence` must match the authoritative report's
   event and resource in the same sealed capture. A fabricated event or a
   foreign capture must be rejected.
9. **Governance.** The closure report must state the #212 approval gap and the
   outstanding operational acceptance; it must not declare M7 complete. The
   repository currently has one collaborator (the owner), so the one-approval
   gate is unsatisfiable without an external reviewer or an explicit
   pre-declared override.

## Closure result (2026-11-19)

For the reviewer, the closure evidence is already recorded:

- one headless session `session-fbd20740-656f-462c-9f28-7a33991a4051`
  (`opencode-go` / `deepseek-v4.1-flash`), 20 tool calls (2 native, 9 shell),
  13 model calls, 314.6 s, 0 replays, 0 EU4 launches;
- native gate: `eu4_case_inspect` and `eu4_re_query` actually executed;
- conclusion: `requires_additional_evidence`, no correction, no root cause,
  unresolved `[0,142]` interval, event-71 `fb:0` new-capture request;
- `dsh_pilot validate` and `summarize` both pass on the preserved
  `.local/m7/p2/producer-case-v5` after the integrity repair; independent NumPy
  reproduction matches the event-142 and final-frame statistics exactly;
- the acceptance-integrity repair in `automation/dsh_pilot.py` now applies the
  shared full-frame execution contract to the packaged `replay/replay.json`,
  rejects invalid report execution/checkpoint validation/fault contamination,
  and verifies the claimed first divergence against the authoritative report;
- `tests.test_dsh_pilot` has 78 tests; the new negative cases use internally
  consistent receipt hashes and sealed identities so they fail on the intended
  gate, not an unrelated payload error;
- an unbounded upper observation may come from a producer-receipt-backed
  standalone report; a bounded interval still requires the in-process verified
  report;
- no new DSH session and no live capture were run.

This is evidence for independent review, not an approval. The reviewer must
confirm the producer-bound acceptance change does not weaken the full-frame
execution, bounded-interval, first-divergence or launch-count guarantees.

## Verification to reproduce

```sh
python3.14 -m automation.test_profiles verify --profile offline
python3.14 -m automation.test_runner run --profile offline --label p2-closure-focused \
  --module tests.test_test_profiles \
  --module tests.test_m7_evidence_identity \
  --module tests.test_case_baseline \
  --module tests.test_failure_taxonomy \
  --module tests.test_p2_workflow \
  --module tests.test_agent_tools \
  --module tests.test_m7_diagnosis \
  --module tests.test_dsh_efficiency \
  --module tests.test_replay_host_dependencies \
  --module tests.test_trace_tr3 \
  --module tests.test_dsh_pilot
python3.14 -m automation.dsh_pilot validate \
  --conclusion .local/m7/p2/closure/pilot-conclusion.json \
  --case .local/m7/p2/producer-case-v5
python3.14 -m automation.dsh_pilot summarize \
  --run .local/m7/p2/closure/run \
  --conclusion .local/m7/p2/closure/pilot-conclusion.json \
  --case .local/m7/p2/producer-case-v5
python3.14 -m automation.test_runner run --profile offline \
  --label p2-closure-integrity-final-full --min-tests 3000
```

Last recorded: focused 291 ran / 288 executed / 3 skips / 0 failures
(`20261119T104917Z-270996f5`); full 3,780 ran / 3,738 executed / 42 classified
skips / 0 failures in 1,070.0 s (`20261119T104932Z-3de77549`).

## Known limits to carry into review

- The producer case is a standalone replay: `frame_shader_identity=
  unverified_session`, so it is `incomplete_diagnostics`. The historical
  in-process P0 diagnosis remains the authoritative divergence evidence, but it
  cannot supply producer identity.
- `gl_dither`/`multisample` remain unsupported and are not blamed.
- The pre-event-142 interval is unresolved; event 71 needs a new capture and
  cannot bound the existing frame.
- A multi-checkpoint same-frame capture is not implemented in P2.
- The independent final-head approval of #212 is missing and cannot be
  retroactively manufactured.
