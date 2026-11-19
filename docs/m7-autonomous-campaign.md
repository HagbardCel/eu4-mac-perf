# M7 autonomous campaign contract

Status: implementation, offline qualification and a genuine two-round DSH
correction workflow are complete; the workflow used a controlled seeded fault
and an authentic renderer correction has not been demonstrated (see the
readiness report under `analysis/m7/`). M7 is READY for infrastructure and
controlled autonomous execution, not for a claim of OpenGL/Metal equivalence.

## Orchestration stays native to DSH

Two DSH modes exist, and only one can continue a goal:

| mode | continuation | evidence |
| --- | --- | --- |
| `dsh --profile headless` / `run-pilot.sh` | **one turn, then exit** | the one-shot runner has no round driver alive; a goal created in that turn is not continued |
| `dsh --profile eu4-web` / `run.sh --campaign` / TUI | **native goal rounds** | `@deepseek-ai/dsh-goal-round-driver` is mounted; `max_goal_rounds` bounds the rounds and `update_goal(action="complete")` is denied by `eu4-goal-plugin` unless `eu4_finalize_candidate` passes a fresh checker run |

`run-campaign.sh` and `run-pilot.sh` therefore do **not** schedule or repeat
turns. They say so in the task text, and this repository does not claim that
repeated one-turn calls are autonomous goal orchestration. A multi-round
campaign is started from the Web/TUI profile with the objective printed by
`run.sh --campaign <sidecar>`.

## What DSH does not bound

DSH bounds rounds and completion, but it does not know about EU4 launches,
sealed frames, Metal shadow executions or candidate replays. Those are bounded
by `automation/campaign_guard.py`, which is enforced at the action sites:

| budget | enforced before |
| --- | --- |
| `eu4_launches` | the one-use launch claim immediately before each process spawn (`run`, `run_ops`, `gfx_launch.spawn`) |
| `sealed_frames` | a single-frame capture records whether a seal was produced |
| `shadow_executions` | a single-frame capture with `--shadow` records the in-process bridge run |
| `replays` | `automation.gfx_independent_replay.replay` (single frame) and `gfx_replay_case.replay_case` (sequence) before the host is built |
| `wall_clock_seconds` | every `check`, against the campaign deadline |
| `corrections` | the pilot budget; the correction itself is accepted only by `automation/correction_accept.py` |

Pilot limits (`PILOT_BUDGETS`):

```text
EU4 launches 1 · sealed frames 1 · shadow executions 1 · replays 2
corrections 1 · wall clock 1800 s · automatic merges 0
```

A campaign state is opt-in. `EU4_CAMPAIGN_STATE=<path>` enables it; the
launchers (`run.sh --campaign-state <path>`, `run-pilot.sh --campaign-state`,
`run-campaign.sh` with `EU4_CAMPAIGN_STATE`) initialise the state if it does not
exist and export it into the DSH session. Without the variable, the action sites
behave exactly as before.

Accounting is **reservation-first**. A multi-resource capture charges the EU4
launch, the sealed-frame bound and the shadow-execution bound atomically under
the state lock before the shadow coordinator or EU4 is spawned. If any one is
unavailable the whole reservation is refused and no child is created. A
completed capture reconciles the conservative upper bound with the verified
frame count; a failed or interrupted action keeps its full charge, and an
abandoned reservation is marked conservatively without restoring the allowance.
A replay reserves its budget before the host is built.

A launch reservation is also **one-use**. Passing the read-only preflight is not
enough to start a process: immediately before `subprocess.Popen`, the launch
path atomically records a claim on the reservation. The claim is refused if the
reservation is missing, closed, owned by another campaign, lacks a positive
`eu4_launches` allowance, was already claimed, or if the campaign is stopped or
past its deadline. The claim does not close the reservation, so completion and
outcome accounting still run; a failed spawn after the claim keeps the consumed
allowance. Two concurrent launch attempts against one reservation produce
exactly one success. The claim is per reservation, not per unit of allowance: a
reservation that declares more than one launch still authorizes exactly one
process, so a campaign that needs several launches creates a reservation for
each one.

Every EU4 process-spawn site (`eu4_gfx_startup_trace.run`,
`eu4_gfx_startup_trace.run_ops` and `gfx_launch.spawn`) keeps the read-only
preflight and then consumes the one-use claim, so an alternative tool or a
direct CLI invocation cannot bypass the guard and a reservation cannot authorize
a second process.

State is a JSON document with budgets, counts, open reservations, remaining,
failure classes, blocked actions and a bounded history.

## Failure handling and stop conditions

A failed capture/replay is classified (`execution_timeout`,
`missing_credential`, `gpu_execution_failure`, `evidence_invalid`,
`evidence_missing`, `tool_failure`). The same failure twice blocks that action
for the remainder of the campaign (`requires_admin_recovery`). There is no
model-accessible reset and a different textual strategy is not a recovery:
only an administrative recovery authorized by the operator's token can clear a
stop or a block, and the reason is recorded in the audit history. The launcher
reads `EU4_CAMPAIGN_ADMIN_TOKEN` only to initialise the campaign and unsets it
before starting the agent session.

The campaign stops with a structured result when a budget is exhausted, the
deadline passes, an identical failure repeats, evidence is invalid/incomplete,
a correction fails independent acceptance, or the environment cannot be
restored. `python3 -m automation.campaign_guard stop --reason <reason>` records
the explicit stop; the goal plugin sees a `policy_stop` and pauses the goal
rather than claiming success. Exhausting one resource refuses that action and
blocks every further attempt to consume it, but it does not block an unrelated
permitted resource. `requires_additional_evidence` and `blocked` are valid
outcomes and are preferable to a fabricated root cause.

The bridge enforces the sidecar's `max_validations`, `wall_clock_seconds` and
`max_identical_retries` from its own `validations.jsonl` attempt lifecycle. Each
checker execution has a distinct attempt ID: a start record is persisted before
the checker is invoked and a terminal result after it. An interrupted attempt
(a start with no result) still counts after a restart. The allowance counts
attempts, not log rows, and the identical-failure gate only considers classified
terminal failures. Finalization's fresh checker run and frozen-candidate replay
are charged through the same limits and refuse before starting when the budget is
exhausted; the administrative `checker-compat` baseline probe is explicitly not
charged because it never qualifies a candidate.

## Isolation and merge discipline

- The candidate is a dedicated Git worktree; the session workspace is that
  worktree, so the trusted checker and acceptance code stay outside the sandbox.
- The bridge refuses identity drift during a checker run.
- No automatic merge: `auto_merge` is always false in the campaign state, and
  the goal plugin denies `gh pr merge`, `git merge` and direct pushes to
  `main` from command tools. Branch protection and the operator's credential
  remain the authority; the guard is an additional boundary, not a replacement.
- The BetterDisplay virtual screen and the owned EU4 process are managed by the
  existing `gfx_display`/`gfx_session` code; a campaign must not move the
  physical desktop or leave a process behind. A failed restore is a stop
  condition.

## Efficiency defaults

- Reuse verified cases and baselines before any recapture.
- Prefer compact native diagnostics; full artifacts stay under `.local/`.
- At most three hypotheses (enforced by `dsh_pilot.validate_conclusion`) with at
  most two discriminating tests each.
- Focused tests after changes; one full offline gate per final integration
  state; at most one full offline qualification run at the end of a change set.
- The pilot summary counts actual launches, replays, tool calls, model calls,
  output volume and elapsed time from the run event stream.

## Offline demonstration

`tests.test_campaign_guard` proves atomic all-or-nothing reservations,
concurrent competition for one allowance, one-use launch claims (including
concurrent claims, stopped/expired campaigns and spawn failure after a claim),
completion/failure reconciliation, abandoned reservations, the failure block and
operator-only recovery, per-resource independence, and the launch/replay action
gates. `tests.test_dsh_bridge.CheckerAttemptAccountingTests` proves the attempt
lifecycle for passing, failing, exceptional, identity-drifted and
incomplete-input executions, interrupted attempts, finalization accounting and
refusal before an exhausted checker run. `tests.test_dsh_pilot` proves that a
correction is only accepted through the independent checker. The existing
`dsh/campaigns/multiround` smoke campaign exercises two native rounds and a
checker pass when started from the Web/TUI profile.

## Unresolved limits

- The headless profile cannot continue a goal; only the Web/TUI profile can.
- The genuine two-round correction used a controlled seeded fault; an authentic
  renderer correction is still not demonstrated. The authentic difference is
  localized to `[14, 17]` in the bounded pilot, rendering-state coverage remains
  partial, and the renderer root cause is not established.
- A fresh `eu4-web` launcher-to-goal session has not been demonstrated end to
  end; creating that goal remains an operator step.
- A multi-round campaign needs a provider credential. The headless profile
  cannot read the credential service on `0.2.2-rc.1`; the Web profile can.
