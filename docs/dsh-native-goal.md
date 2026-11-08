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
