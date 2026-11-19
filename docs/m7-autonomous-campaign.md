# M7 autonomous campaign contract

Status: implementation and offline qualification are complete; an authentic
live multi-round campaign has not been run (see the readiness report under
`analysis/m7/`).

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
| `eu4_launches` | `automation.m6_frame capture` calls `startup.run_ops` |
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

Both EU4 launch choke points (`eu4_gfx_startup_trace.run_ops` and
`gfx_launch.spawn`) require an open launch reservation while a campaign state is
active, so an alternative tool or a direct CLI invocation cannot bypass the
guard.

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
`max_identical_retries` from its own `validations.jsonl` attempt log, so a
campaign cannot keep validating after those limits are exhausted.

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
concurrent competition for one allowance, completion/failure reconciliation,
abandoned reservations, the failure block and operator-only recovery, per-resource
independence, and the launch/replay action gates. `tests.test_dsh_bridge.ValidationBudgetTests` proves
the bridge-side limits, and `tests.test_dsh_pilot` proves that a correction is
only accepted through the independent checker. The existing `dsh/campaigns/
multiround` smoke campaign exercises two native rounds and a checker pass when
started from the Web/TUI profile.

## Unresolved limits

- The headless profile cannot continue a goal; only the Web/TUI profile can.
- An authentic live multi-round campaign and an authentic renderer correction
  have not been demonstrated. The event-142 producing operation is unknown.
- A multi-round campaign needs a provider credential. The headless profile
  cannot read the credential service on `0.2.2-rc.1`; the Web profile can.
