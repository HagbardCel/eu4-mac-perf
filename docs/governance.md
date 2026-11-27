# Campaign governance

Status: current policy for EU4 reconstruction campaigns. The science is
unchanged: the original OpenGL capture is the reference, and only the
independent checker establishes exactness.

## Three mechanisms

| Mechanism | Owner | Boundary |
|---|---|---|
| Execution safety | `launch_lease`, `campaign_guard`, `test_profiles` | May this process exist? Is this campaign allowed to act? |
| Experiment rationale | `experiment_rationale` | Why is this expensive replay necessary? |
| Progress | DSH native goal rounds, operator deadline | Should the agent continue? |

There are no replay, correction, validation, frame, launch or goal-round
counters. A campaign may run as many justified experiments as it can explain.

## Safety

- **Host-wide lease** (`/tmp/eu4-mac-perf-launch-lease-<uid>`, no environment
  override): one EU4 process per installation. The marker for the active
  campaign lives in the same fixed directory and is fail-closed: a missing,
  corrupt or conflicting state refuses launches instead of disabling the guard.
- **Execution modes**: `autonomous_reconstruction` (default) refuses live
  capture; `controlled_capture` permits the capture workflow. `ensure` refuses
  a mode change.
- **One-use launch authorization**: each live launch needs an authorization
  issued by the operator with the campaign token. `gfx_frame.capture` never mints
  one and refuses before preflight when the mode or authorization is wrong. The
  claim is atomic immediately before `Popen` and is never refunded.
- **One operator deadline**: declared in the sidecar
  (`limits.deadline_seconds`), seeded into the guard at launch, never rewound by
  a resume. Expiry refuses every expensive action, including checker and replay
  work.
- **Serialized GPU execution**: trusted replay hosts take one host-wide lock,
  so two experiments do not contend for the device.
- **Stop**: `campaign_guard stop` refuses expensive work until the operator runs
  `resume` (which does not rewind the deadline). `deactivate` removes the
  host-wide marker.
- Preserved: per-process timeouts, owned-process cleanup, trusted reference
  immutability, checker/identity anchors, candidate worktree restrictions, no
  automatic merge, offline/live profiles.

Launchers require a campaign state, run `ensure` (validating an existing mode)
and seed the deadline:
`run.sh --campaign <sidecar> --campaign-state <state>` and
`EU4_CAMPAIGN_STATE=<state> run-campaign.sh <sidecar>`.

## Evidence-first replays

Agent-facing expensive replay entry points (`eu4_replay` independent/frame/case/
sequence, the replay CLIs, direct `gfx_frame replay`) require `--rationale` with
`question`, `evidence`, `gap`, `hypothesis`, `action`, `expected_information`,
`cheaper_alternatives_checked`. The harness checks structure and that evidence
paths resolve, then writes `experiment-rationale.json` beside the output marked
`authoritative: false`. There is no cache, no identity and no automatic reuse;
scientific merit and the decision to repeat are the agent's.

Read-only verification, diagnosis of existing output, focused tests and trusted
checker validation need no rationale.

The trusted checker is invoked with the campaign's selected sidecar bound
explicitly; ambient `EU4_M7_CANDIDATE_SIDECAR` / `EU4_M7_EVENT20_TRUSTED_ROOT`
values are scrubbed at that boundary, so a live campaign cannot fall back to the
historical sidecar's reference.

## Progress and stopping

A repeated failure is evidence to reassess, not a terminal stop. The campaign
stops on verified success, a genuine evidence/environment blocker, an unsafe
state, operator cancellation, or the operator deadline. DSH owns continuation;
no round cap is declared.

## Historical migration

Campaign states and sidecars using the removed fixed-budget schema are refused
with an instruction to start a fresh campaign state. Historical evidence,
receipts and archive sidecars are untouched.
