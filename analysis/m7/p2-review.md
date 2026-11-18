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
   `gfx_launch.spawn`, `eu4_gfx_startup_trace.run_ops` and `live_gfx.probe`.
   Verify that production (unset profile) is unchanged and that an explicit
   offline profile cannot launch EU4.
2. **Fail-closed gate.** `automation.test_runner --profile offline
   --min-tests`; the mandatory module/test manifest; the CI change. Verify that
   the floor counts executed tests, a classified-skip-only suite is
   `incomplete`, skipped/missing mandatory tests fail, and deduplication
   distinguishes profile and floor.
3. **Failure taxonomy.** `agent_tools._classify_failure` and
   `_nested_evidence_state`. Verify that a differing-pixel comparison stays
   `comparison`, shader/replay correctness failures are `tool_failure`, named
   GPU-capability failures are `graphics_unavailable`, dependency failures are
   `dependency_unavailable`, invalid evidence wins, and nested invalid evidence
   cannot look qualified.
4. **Compact evidence.** `_compact_case_view`, `_bounded_envelope`,
   `_emit_text`, the `gfx-agent-case/v1` shape and `--full`. Verify that the
   limit is exact emitted UTF-8 bytes, identity/verification/first
   divergence/unresolved interval/next checkpoint/artifacts survive, and
   truncation is explicit.
5. **Reuse identity.** `automation/case_baseline`. Verify that record/check
   require `gfx_replay_case.verify()`, re-hash every declared payload, require a
   completed replay/readback, and include the renderer include closure
   (especially `eu4_gfx_replay_semantics.h`), shader inputs, binary/source,
   policy and platform. Verify that an unverified current case is never
   reusable merely because a fingerprint matches, and that this remains a
   single receipt, not a cache.
6. **Replay producer identity.** `gfx_independent_replay.write_producer_receipt`
   and `gfx_replay_case.verify_producer_receipt`. Verify the receipt binds the
   executable SHA-256, build fingerprint/stamp, capture identity and
   `replay.json`/Metal-readback digests; portable cases package it; baseline
   reuse rejects a changed producer and legacy cases without a receipt.
7. **Build publication.** `m6_frame.build_replay_host`. Verify private temp
   compilation, atomic publication, exclusive lock, binary digest in the stamp,
   tamper detection, timeout/failure cleanup and overlapping builds.
8. **Replay-host dependencies.** `automation.replay_host_dependencies` is the
   single closure for `m6_frame.build_replay_host` and
   `case_baseline`. Verify that a semantics-header edit rebuilds, an unchanged
   set does not, flags/compiler changes invalidate, and a failed build removes
   the binary and stamp.
9. **Payload pass.** `case_baseline` calls `gfx_replay_case.verify()` once as
   the authoritative payload hashing pass; it must not re-walk declared files.
   A reusable baseline requires `stage=complete`, `status=ok`, `readback=true`.
10. **Causal narrowing.** The compact diagnosis retains the existing
   `unresolved_interval` and `next_checkpoint`; a lower bound is `null` and
   `search_start_event` is not evidence. Verify that no cross-capture or
   unrelated-resource observation can bound the interval.
11. **Efficiency evidence.** `analysis/m7/p2-efficiency.md` and
   `analysis/m7/p2-evidence.json`. Verify that the P1 autonomous session, the
   two-call P2 interface demonstration and the absent P2 autonomous session are
   distinguished, and that end-to-end efficiency is marked not measured.
12. **Governance.** `analysis/m7/p2-governance.md`. The repository settings are
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
  --module tests.test_replay_host_dependencies \
  --module tests.test_trace_tr3
python3.14 -m automation.test_runner run --profile offline --label p2-full \
  --min-tests 3000
```

The authentic-case commands are in
[P2 efficiency](p2-efficiency.md#authentic-demonstration-c1). No new EU4 launch
or replay is required.

## Known limits to carry into review

- No new model-driven session was run (no provider credential in this
  environment); only interface-level efficiency is measured. End-to-end P2
  efficiency is explicitly not measured.
- `gl_dither`/`multisample` remain unsupported and are not blamed.
- The pre-event-142 interval is unresolved; event 71 needs a new capture and
  cannot bound the existing frame.
- A multi-checkpoint same-frame capture is not implemented in P2.
