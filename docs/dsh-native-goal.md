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

## Execution roots

Three roots are kept distinct and are asserted before a checker runs.

| Root | Supplies |
|---|---|
| `INFRA_ROOT` | the bridge, the plugin, trusted verification (this checkout) |
| `CAMPAIGN_ROOT` | the authoritative objective, checker, manifest and policy |
| `CANDIDATE_ROOT` | the reconstruction source under evaluation |

The bridge is launched with an explicit interpreter and `PYTHONPATH` from
`INFRA_ROOT` (with `-P`, so the current directory cannot shadow `automation`),
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
./dsh/scripts/provision.sh --init   # one time: create and populate the profile
./dsh/scripts/preflight.sh          # fail-closed checks on the effective profile
./dsh/scripts/run.sh                # boot the eu4-web profile
```
