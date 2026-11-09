# Native DSH goal infrastructure for EU4 OpenGL -> Metal

A second, independent agentic migration path built on DeepSeek Harness's own
`/goal` mechanism. It reuses the existing EU4 reconstruction skill, checkers,
manifests and evidence standards and never drives the OpenCode runner.

Plan of record: rev. 3. Progress and per-milestone evidence:
[dsh/PROGRESS.md](../dsh/PROGRESS.md).

## Why a separate path

The OpenCode runner (`automation/runner.py` + `automation/opencode.py`) owns its
own continuation loop and its own state under `.local/automation/<campaign>/`.
DSH already provides durable goals and same-session autonomous continuation, so
this path owns orchestration natively and adds only what DSH does not have: a
trusted qualification gate.

Nothing in the OpenCode path is modified. `automation/state.load_campaign` still
rejects any `runtime` other than `opencode`, so the historical campaign files
keep their meaning; a DSH campaign is described by a sidecar that references the
existing campaign instead of changing it.

## Division of authority

| Question | Authority |
|---|---|
| Should the agent keep working? | DSH goal service + goal-round driver |
| Did the candidate pass? | the campaign's existing checker, run by trusted tooling |
| May the goal complete? | the EU4 plugin, after a fresh validation run |

`update_goal(action="complete")` is denied unconditionally for model-initiated
calls in this profile; only the plugin's finalize tool completes a goal, by
calling the goal service. Human `/goal` commands reach the goal service directly
and are unaffected, so pausing or clearing a goal remains an operator control.

## Trusted bridge

`python3 -m automation.dsh_bridge {prepare,status,validate,goal-prompt} --sidecar <path>`
does the deterministic project work and nothing else. It reuses
`automation.runner.run_accept` for execution and verdict parsing, so the checker
vocabulary has one source of truth, and it never invents a verdict.

Two axes are reported separately:

| Axis | Values | Meaning |
|---|---|---|
| `checker_verdict` | `pass` `fail` `inconclusive` `blocked` `unavailable` | emitted by the checker, passed through unchanged |
| `bridge_classification` | `verdict` `policy_stop` `infrastructure_error` `integrity_refusal` | what the bridge itself concluded |

A timeout, truncated output, unreadable JSON, an invalid verdict enum or `pass`
with a non-zero exit is an `infrastructure_error` mapped to goal policy
`unavailable` — never a pass and never a remediable `fail`. Exhausting the retry
allowance is `policy_stop`/`no_progress`, which is not a checker `blocked`.

The retry policy is checker-aware, because some checkers legitimately depend on
trusted external state across runs:

```json
"checker_retry": { "mode": "stateful", "confirmation_rounds": 1 }
```

`campaigns/automation-smoke/accept.py` is exactly that case: it deliberately
fails once even when the candidate is already correct, so its DSH sidecar
declares `stateful` with one confirmation round. A deterministic checker declares
`mode: "deterministic"` and is refused on an identical repeat. An identical
verified `pass` is reused rather than re-run — idempotency applies to success,
not to failure.

`prepare` records an anchor of the trusted material (checker, sidecar, source
campaign, verification manifest, policy, objective, candidate baseline). Every
`validate` re-derives the current identity and refuses on any drift, so editing a
checker after preparation is a hard failure rather than a silently new identity.

## Completion

Completion is two-phase, because a synchronous guard cannot run a checker.

1. `eu4_finalize_candidate` re-verifies through the bridge, which re-runs the
   checker, and only on a fresh pass calls `ctx.goals.complete(agent, ref)`.
2. A monotonic guard denies **every** model-initiated
   `update_goal(action="complete")`, with no dependence on binding state.

The guard is deliberately unconditional. Consulting a binding would leave the
first autonomous round unguarded, before any campaign tool has run, and would
fail open if binding state were lost across a restart. It is registered on the
plain context, which `ToolRuntime` evaluates before any agent-scoped guard
layer, so it covers the preset-registered `tool-goal`; and it performs no I/O,
so it cannot stall the loop.

Operator control is unaffected: `/goal` and the goal bar reach the goal service
directly rather than through the tool pipeline.

Qualification is bound to one goal. `goal_id`, `revision` and an objective
digest are recorded per session in `<run-root>/plugin-binding.json`; editing or
replacing the goal invalidates prior qualification instead of authorizing a
different objective.

## Candidate snapshots and the journal

A candidate is identified by its complete delta against the pinned baseline, not
by a list of filenames: additions, deletions, renames, mode changes, typechanges
and symlinks are enumerated from porcelain v2 and every one of them must be
permitted by the campaign policy. The snapshot is then rebuilt from the baseline
and re-diffed, and the rebuild must reproduce the intended delta exactly — no
missing change and no unintended one — before it is accepted. Finalization
freezes that commit, so review evidence always describes the candidate the
passing run actually saw.

Receipts are written through an append-only hash-chained journal, and
finalization refuses when the current receipt is not the journalled one. That is
tamper evidence, not authority: authority remains the fresh checker run, which
is why a forged receipt *and* a rebuilt journal chain still cannot authorize
completion.

Before any run the bridge asserts its execution roots: the candidate may not be
the infrastructure root or nested inside it, the campaign and checker must live
in the trusted checkout, a candidate may not ship `automation/dsh_bridge.py`, a
declared `EU4_DSH_WORKSPACE` must equal the campaign workspace, and
`danger-full-access` refuses qualification outright because it provides no
boundary for qualification state.

## Interruption and recovery

A validation records an intent before the checker starts and clears it only once
the receipt is durable. `recover` quarantines any surviving intent and reports
whether the current receipt still describes the current candidate; it never
writes a receipt, so recovery cannot manufacture a pass. Limits (validation
count, wall clock, retry allowance, finalization confirmations) are counted in
the run root rather than in the session, so restarting or forking a session
cannot reset them.

DSH disarms an active goal on session restart, and nothing in this
infrastructure re-arms it: continuation after a restart is an explicit operator
decision.

## Independent review

The reviewer is a separate `dsh --profile headless` process in a detached
worktree at the candidate commit the bridge froze — not a subagent and not a
role switch. It has no goal, no completion authority, and no write access to
protected state.

The channel is host-mediated. The bridge spawns the reviewer, parses its
newline-delimited run events, recovers the single `eu4-review/v1` object, takes
the reviewer's session identity from the stream rather than from anything the
reviewer says, validates the report against the candidate and oracle digests,
and writes the report and the reviewer receipt itself.

Four conditions stay separate: the process exited 0, the report is schema-valid,
the outcome is `qualified` / `rejected` / `unresolved`, and a receipt exists. A
completed review task is not a qualified candidate.

With `require_independent_review`, finalization additionally requires a
`qualified` review whose receipt matches the frozen candidate and is verified as
live. A scripted test double is marked `qualification_mode: scripted` and can
never authorize completion.

`review-clean` removes the reviewer worktree once its evidence is no longer
needed; the report, receipt and invocation live outside it and survive.

## Campaign compatibility

A historical campaign predates this bridge, and its checker runs from the
trusted checkout, so it imports modern `automation` helpers rather than the ones
from its own commit. `checker-compat` records that explicitly: the checker's
digest, every `automation.*` module it resolves (with digests and whether each
resolves inside the trusted checkout), and a baseline run against a pristine
copy of the seed — or, for a worktree-pinned campaign, against a temporary
worktree at the pin.

The baseline must produce a **remediable** verdict. A checker that already
passes on its own baseline cannot distinguish a candidate, and one that returns
`unavailable` or `blocked` there means the checker or its evidence does not match
the pin; both are `unsupported`. That screening is worth running first: it found
a completed campaign and a pin whose checker no longer matches its own commit
before any goal round was spent.

## Execution roots

Three roots are kept distinct and are asserted before a checker runs.

| Root | Supplies |
|---|---|
| `INFRA_ROOT` | the bridge, the plugin, trusted verification (this checkout) |
| `CAMPAIGN_ROOT` | the authoritative objective, checker, manifest and policy |
| `CANDIDATE_ROOT` | the reconstruction source under evaluation |

The bridge is launched with an explicit interpreter and `PYTHONPATH` from
`INFRA_ROOT` (with cwd pinned to the infrastructure root, so the implicit script directory cannot shadow `automation` (no `-P`: it exists only from Python 3.11)),
never by ambient module resolution from wherever DSH happens to sit. Candidate
builds and tests run with `CANDIDATE_ROOT` on the path instead.

A historical campaign checkout predates this infrastructure, so its checker runs
from `CAMPAIGN_ROOT` inside `INFRA_ROOT` and imports the modern `automation`
helpers. That is a semantic change for a historical campaign, which is why a
checker compatibility preflight records which modules resolve and requires a
stable non-pass baseline before the campaign is used.

## Shared Ghidra project

The Ghidra project is outside every worktree, so worktree isolation does not
protect it. Measured on this machine: two concurrent
`analyzeHeadless -readOnly` queries cannot both proceed — the second aborts with
`ghidra.framework.store.LockException: Unable to lock project!`. `-readOnly`
prevents saving, not locking.

Consequences:

- a DSH-only advisory lock would coordinate DSH alone, because the pinned
  OpenCode worktrees and any interactive Ghidra session do not take it;
- the chosen approach is a read-only project replica for DSH queries, which
  needs no cross-process protocol;
- a shared lock remains a multi-party follow-up, not part of this work.

`ghidra-replica.sh --create` clones the project to `~/GhidraEU4/<sha>-dsh` and
rewrites the replica's metadata so queries cannot silently reopen the shared
project. Use `EU4_RE_EXPORT=<replica>` for DSH queries. Measured on this
machine: a shared-project query and a replica query run at the same time both
exit 0 and return the same evidence, which was impossible before the replica.

[`dsh/scripts/ghidra-probe.sh`](../dsh/scripts/ghidra-probe.sh) reproduces the
measurement.

## Original-EU4 capture

`automation/launch_ledger.py` is authoritative **accounting** for how many
launches a workflow may make. It is not mutual exclusion: `claim()` checks
`used >= limit`, so two callers under a limit of three both succeed, and the
legacy `busy` outcome covers only a transient campaign-local counter. DSH capture
therefore stays disabled (`live_capture.enabled: true` is refused) until a
host-wide process lease exists and every launch path adopts it.

## What the ledger does and does not guarantee

`automation.launch_ledger` is authoritative **accounting**: it refuses a claim
when the budget is spent, and a started reservation stays charged through
reconciliation. It is not mutual exclusion — `claim()` checks only the total, so
two callers under a limit of three both receive valid reservations. The tests in
`tests/test_dsh_launch_safety.py` pin that down so neither path can describe the
ledger as a concurrency control.

If host-wide exclusion is ever added, it belongs in a separate lease keyed by the
executable SHA and the capture output, acquired before the launch and held for
the lifetime of the game process, adopted by every launcher. Making the ledger's
lock long-lived would convert an accounting record into a lock file and break its
reconcile-without-refund semantics.

Until then DSH capture stays disabled, and the DSH bridge is asserted to import
no launch path at all.

## Threat model

The qualification receipt is a record, not authority. Authority comes from a
fresh checker run by trusted tooling at completion time, so a forged `pass`
receipt cannot authorize completion.

| Threat | Defended | How |
|---|---|---|
| Model declares success | yes | completion denied; only finalize completes |
| Receipt edited to `pass` | yes | finalize re-runs the checker |
| Stale candidate/checker/evidence | yes | identities re-verified before and after every run |
| Accidental state drift | yes | state root outside the workspace, atomic writes, journal |
| Agent with the operator's full privileges | **no** | no local integrity mechanism is sound; qualification runs are refused under `danger-full-access` |

## Operator commands

```sh
./dsh/scripts/provision.sh --init      # one time: create and populate the profile
./dsh/scripts/preflight.sh             # fail-closed checks on the composed profile
./dsh/scripts/smoke-boot.sh            # prove the plugin mounted and registered
./dsh/scripts/skill-catalog.sh         # prove the agent's catalog has the skill
./dsh/scripts/run.sh                   # boot the eu4-web profile
./dsh/scripts/smoke-goal.sh [sidecar]  # print the exact /goal objective
```

`preflight.sh`, `smoke-boot.sh` and `skill-catalog.sh` send no model request, so
they cost nothing and are safe to run at any time. Creating a goal is a human
action; `smoke-goal.sh` only prints what to paste.

## Bridge operations

`python3 -m automation.dsh_bridge <operation> --sidecar <path>` prints exactly one
JSON object. Exit `0` means a classification or status was produced, `2` is a
usage error, and `3` is a refusal before the checker ran.

| Operation | Purpose |
|---|---|
| `prepare` | validate the sidecar, seed or verify the workspace, record the identity anchor |
| `status` | identities, counters, current receipt, and whether it is stale |
| `validate` | run the trusted checker under the retry policy and write a receipt |
| `snapshot` | materialise the candidate delta as a commit and verify the round trip |
| `finalize` | re-run the checker and complete the goal only on a fresh pass |
| `recover` | quarantine interrupted runs; never writes a receipt |
| `review-preflight` | freeze the candidate and create the reviewer worktree |
| `review-run` | spawn the reviewer, validate its report, write the receipt |
| `review-verify` | re-check an existing report and receipt against the frozen candidate |
| `review-clean` | remove the reviewer worktree, keeping its evidence |
| `checker-compat` | screen a checker before relying on it |
| `goal-prompt` | print the objective text to paste after `/goal` |
