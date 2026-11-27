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
| `dsh --profile eu4-web` / `run.sh --campaign` / TUI | **native goal rounds** | `@deepseek-ai/dsh-goal-round-driver` is mounted; the campaign no longer declares a round cap, and `update_goal(action="complete")` is denied by `eu4-goal-plugin` unless `eu4_finalize_candidate` passes a fresh checker run |

`run-campaign.sh` and `run-pilot.sh` therefore do **not** schedule or repeat
turns. They say so in the task text, and this repository does not claim that
repeated one-turn calls are autonomous goal orchestration. A multi-round
campaign is started from the Web/TUI profile with the objective printed by
`run.sh --campaign <sidecar>`.

## What DSH does not bound

DSH bounds continuation and completion. It does not know about EU4 launches or
GPU replays, so those boundaries are enforced by the trusted infrastructure at
the action sites. The governance model has three parts and no experiment quotas
([governance](governance.md)):

| mechanism | owner | enforced before |
| --- | --- | --- |
| active campaign mode, deadline and stop | `automation/campaign_guard.py` | each EU4 spawn |
| host-wide process lease | `automation/launch_lease.py` | each EU4 spawn |
| configured workflow budget and provenance | `automation/launch_ledger.py` | a live capture with `--campaign-run-root` |
| experiment rationale (structure + evidence exists) | `automation/experiment_rationale.py` | agent-facing `eu4_replay`, replay CLIs, direct `m6_frame replay` |
| operator execution deadline | sidecar declaration, seeded into the guard at launch | every expensive action (guard) and every checker run (bridge) |
| per-process timeout | the checker/replay/launch caller | each child process |
| safety stop | `automation/campaign_guard.py` | every replay, launch and correction evaluation |

There is **no** replay, correction, sealed-frame, shadow-execution, validation
or goal-round quota. An autonomous campaign may run as many justified
experiments as it can explain, and continuation is progress-based.

### Execution modes

A campaign state is created in one of two modes:

- `autonomous_reconstruction` (default): live capture is refused outright.
- `controlled_capture`: live development tools run under the campaign deadline
  and acquire the host-wide lease.

`ensure` validates the mode of an existing state as well as creating a new one,
so a prepared campaign cannot be silently downgraded. Activation is host-wide and fail-closed: the marker in the fixed
`/tmp/eu4-mac-perf-launch-lease-<uid>` directory is authoritative, no
environment variable (`TMPDIR`, `EU4_*`) changes that path, an explicit state
path naming a different campaign is refused as a conflict, and a marker naming a
missing/corrupt state still refuses launches. A campaign ends with
`python3 -m automation.campaign_guard deactivate --state <state>`; `resume`
clears an explicit stop without rewinding the deadline.

Launchers require a campaign state, pass
`--campaign-mode controlled_capture|autonomous_reconstruction`, run `ensure`
unconditionally and seed the sidecar deadline at launch; a campaign without a
state is refused rather than running without its deadline. Development tools
also run directly when no campaign is active. State records mode, deadline,
stop and bounded audit history.

### Evidence-first experiments

Before an expensive replay the agent records why it is necessary: the question,
the existing evidence, the gap, the hypothesis, the expected information and the
cheaper alternatives already tried. The harness checks only that the fields are
present and that referenced evidence paths exist. Agent-facing replay entry
points require `--rationale`; read-only verification, diagnosis of existing
output and trusted checker validation do not.

The record is written beside the result as `experiment-rationale.json`, marked
`authoritative: false`. There is no cache and no automatic reuse: whether to
repeat an experiment, and whether a rationale is scientifically adequate, stays
the agent's judgment. A record never becomes rendering evidence.

### Progress, failure and stop conditions

A failed capture/replay is classified (`execution_timeout`,
`missing_credential`, `gpu_execution_failure`, `evidence_invalid`,
`evidence_missing`, `tool_failure`) and recorded. A repeated identical failure
is evidence for the agent to reassess: it is not a harness stop, and no
identical-failure block or strategy label gate exists. The agent must change the
candidate, the experiment inputs, the evidence it consults or the observation it
requests; otherwise it records a concrete blocker.

The campaign stops only on verified success, a genuine blocker (missing
evidence, unavailable environment, unsafe state), explicit operator
cancellation, or the operator deadline. `resume` is an explicit operator action
after the blocker is resolved and does not extend the deadline:

    python3 -m automation.campaign_guard stop --state <state> --reason blocked

A stop refuses cheap analysis and expensive execution alike. A shorter verified
prefix stays a shorter prefix; `requires_additional_evidence` and `blocked` are
valid outcomes and are preferable to a fabricated root cause.

The operator execution deadline (`limits.deadline_seconds`, historical name
`wall_clock_seconds`) is the single campaign-wide time limit. The launcher
starts it at launch (preparation does not consume it), a resume keeps the
original epoch, and expiry refuses further expensive work rather than only final
validation. Each checker and subprocess keeps its own timeout. Attempts are logged in
`validations.jsonl` for audit and reconnect, not as an allowance; an
interrupted attempt stays visible after a restart.

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

- Reuse verified cases and baselines before any recapture; consult
  `eu4_session discover` and existing experiment records first.
- Prefer compact native diagnostics; full artifacts stay under `.local/`.
- At most three hypotheses in a *conclusion document* (enforced by
  `dsh_pilot.validate_conclusion`) with at most two discriminating tests each.
  This caps a report format, not investigation.
- Focused tests after changes; the full offline gate at integration milestones,
  not after every edit.
- The pilot summary counts actual launches, replays, tool calls, model calls,
  output volume and elapsed time from the run event stream.

## Offline demonstration

`tests.test_campaign_guard` covers mode immutability, stop and expiration,
fail-closed host activation and a stop between launch preflight and spawn.
`tests.test_governance_entrypoints` proves the agent-facing replay paths refuse
without a rationale, both launchers require campaign state and validate its mode
via `ensure`, and a stopped/expired campaign refuses a replay. `tests.test_dsh_bridge` proves the attempt lifecycle remains an
audit trail without an allowance, the operator deadline is the only time
refusal, and the trusted checker/identity anchors still fail closed.
`tests.test_correction_accept` proves corrections are evidence-gated rather than
counted. `tests.test_m7_event20_prefix` proves trusted execution is not budgeted
and a safety stop still refuses it. The `dsh/campaigns/multiround` smoke
campaign exercises native rounds and a checker pass when started from the
Web/TUI profile.

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
