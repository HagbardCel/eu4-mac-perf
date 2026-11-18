# M7-P2 — review request and final-head checklist

Status: **awaiting independent review.** CI success is not approval and this
file is not a merge authorization.

## Branch and base

- PR: [#212](https://github.com/HagbardCel/eu4-mac-perf/pull/212).
- Base: `246d7e08` (merge of M7-P1 / PR #211).
- Branch: `m7/p2-infra-hardening`.
- Final head: recorded in the PR description after the last push; a commit
  cannot contain its own SHA.
- PRs: this branch delivers P2 A+B+C together because the changes share the
  same test-runner and agent-tool contracts. A separate PR is not justified
  when the reliability gate must exercise the compact interface.

## What must be reviewed

1. **Launch isolation.** `automation/test_profiles` plus the guards in
   `gfx_launch.spawn`, `m6_frame.capture`, `live_gfx.capture`/
   `live_gfx.probe`. Verify that production (unset profile) is unchanged and
   that an explicit offline profile cannot launch EU4.
2. **Fail-closed gate.** `automation.test_runner --profile offline
   --min-tests`; the mandatory module manifest; the CI change. Verify that a
   zero/short run is `incomplete` and that the live profile requires an explicit
   module.
3. **Failure taxonomy.** `agent_tools._classify_failure`. Verify that a
   differing-pixel comparison stays `comparison`, while timeout, credential,
   graphics, invalid evidence and missing evidence are distinct and never
   `ok`.
4. **Compact evidence.** `_compact_case_view`, `_bounded_envelope`, the
   `gfx-agent-case/v1` shape and `--full`. Verify that identity, qualification,
   first divergence, next checkpoint and artifacts survive; that truncation is
   explicit; and that no unsupported evidence is promoted.
5. **Reuse identity.** `automation/case_baseline`. Verify that changed
   payloads, shaders, renderer sources, policy, backend or seal invalidate the
   baseline and that the module is not a general cache.
6. **Causal narrowing.** The compact diagnosis retains the existing
   `unresolved_interval` and `next_checkpoint`; a lower bound is `null` and
   `search_start_event` is not evidence. Verify that no cross-capture or
   unrelated-resource observation can bound the interval.
7. **Efficiency evidence.** `analysis/m7/p2-efficiency.md` and
   `analysis/m7/p2-evidence.json`. Verify the P1 comparison is not presented as
   a new model session and that unmeasured metrics are null, not zero.
8. **Governance.** `analysis/m7/p2-governance.md`. The repository settings are
   enforced; the independent approval is still outstanding.

## Verification to reproduce

```sh
python3.14 -m automation.test_profiles verify --profile offline
python3.14 -m automation.test_runner run --profile offline --label p2-focused \
  --module tests.test_test_profiles \
  --module tests.test_m7_evidence_identity \
  --module tests.test_case_baseline \
  --module tests.test_failure_taxonomy \
  --module tests.test_p2_workflow \
  --module tests.test_agent_tools \
  --module tests.test_m7_diagnosis \
  --module tests.test_dsh_efficiency \
  --module tests.test_gfx_frame_trace_writer
python3.14 -m automation.test_runner run --profile offline --label p2-full
```

The authentic-case commands are in
[P2 efficiency](p2-efficiency.md#authentic-demonstration-c1). No new EU4 launch
or replay is required.

## Known limits to carry into review

- No new model-driven session was run (no provider credential in this
  environment); the operational comparison is deterministic plus the preserved
  P1 export.
- `gl_dither`/`multisample` remain unsupported and are not blamed.
- The pre-event-142 interval is unresolved; event 71 needs a new capture and
  cannot bound the existing frame.
- A multi-checkpoint same-frame capture is not implemented in P2.
