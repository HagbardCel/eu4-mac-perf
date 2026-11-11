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

`python3 -m automation.dsh_bridge {prepare,validate,finalize} --sidecar <path>`
does the deterministic project work and nothing else. It runs the checker through
`automation.acceptance`, the same function the OpenCode runner uses, so the checker
vocabulary has one source of truth, and it never invents a verdict.

Two axes are reported separately:

| Axis | Values | Meaning |
|---|---|---|
| `checker_verdict` | `pass` `fail` `inconclusive` `blocked` `unavailable` | emitted by the checker, passed through unchanged |
| `bridge_classification` | `verdict` `policy_stop` `infrastructure_error` `integrity_refusal` | what the bridge itself concluded |

A timeout, truncated output, unreadable JSON, an invalid verdict enum or `pass`
with a non-zero exit is an `infrastructure_error` mapped to goal policy
`unavailable` — never a pass and never a remediable `fail`.

The retry policy is checker-aware, because some checkers legitimately depend on
trusted external state across runs:

```json
"checker_retry": { "mode": "stateful", "confirmation_rounds": 1 }
```

`campaigns/automation-smoke/accept.py` is exactly that case: it deliberately
fails once even when the candidate is already correct. There is no retry scheduler
and no allowance to configure: every `validate` runs the checker once, so an agent
that wants another check simply asks again, which is what the smoke campaign's
confirmation round is.

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

## Candidate snapshots and the qualification record

A candidate is identified by its complete delta against the pinned baseline, not
by a list of filenames: additions, deletions, renames, mode changes, typechanges
and symlinks are enumerated from porcelain v2 and every one of them must be
permitted by the campaign policy. The snapshot is rebuilt from the baseline and
re-diffed, and the rebuild must reproduce the intended delta exactly before it is
accepted. Finalization freezes that commit, so review evidence describes the
candidate the passing run actually saw.

Validation writes **one** qualification record per campaign, replacing the
previous one. It is a record, not authority: finalization re-runs the checker and
only that result completes a goal. There is no journal, no per-attempt history and
no retry scheduler -- DSH owns the session history and its round cap bounds the
campaign.

Before any run the bridge asserts its execution roots. The boundary between the
candidate and the trusted checkout is the **session workspace**: the plugin
requires the session's working directory to *be* the campaign workspace, so the
DSH sandbox keeps `automation/`, `campaigns/` and the sidecars outside it. The
candidate may not be the infrastructure root, and it may not ship
`automation/dsh_bridge.py`. The trusted modules this path executes are digested
into the anchored identity, so an uncommitted edit to the checker's supporting
code is refused rather than run. The bridge cannot observe DSH's sandbox mode and
does not claim to.
## Qualification hardening

Four boundaries were tightened after the first end-to-end campaign.

**Goal binding is established once.** `validate` binds the session to the live
goal on its first call and afterwards compares instead of replacing, so editing
the objective and validating again refuses with `goal_binding` rather than
inheriting the earlier qualification. `finalize` compares too, and never rebinds.

**A checker never sees provider credentials.** `automation.acceptance` runs every
checker with a scrubbed environment: anything shaped like a provider credential
(`*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, and `OPENCODE_`/`DEEPSEEK_`/
`OPENAI_`/`ANTHROPIC_`-prefixed names) is removed, while toolchain and build
settings pass through. A checker runs candidate-authored tests, so anything it can
read is effectively available to the candidate; the reviewer, which legitimately
needs a key, is spawned separately with an explicit environment.

**The reviewer runs isolated and read-only.** The plugin hands the bridge an
explicit environment allowlist (`PATH`, `HOME`, `TMPDIR`, `LANG`, `DSH_HOME`,
the provider keys) instead of `process.env`. The reviewer is spawned with
`DSH_PERMISSION_MODE=read-only` and a cwd inside its own worktree, so it cannot
modify the candidate even if it tries; without `DSH_HOME` the run refuses
`review_environment` rather than silently using the operator's installation, and it
refuses the same way when no provider key is in the environment, because the
one-shot profile cannot read the stored credential. After
it exits, the worktree is fingerprinted again and any change refuses
`review_worktree_modified`, dropping the checkout and keeping the changed paths in
the refusal.

**The trusted identity covers what the checker executes.** `trusted_modules_digest`
combines the modules this path runs with the **transitive** `automation.*` closure
the checker reaches -- the same analysis `checker-compat` reports, so the two
cannot drift. The walk parses imports rather than matching text, keeps full dotted
paths (`automation.gfx_contracts.api` and `automation.gfx_contracts` are different
files), follows what each discovered module imports in turn, and understands both
`import automation.x` and `from automation import x`. A package is digested module
by module, because `gfx_contracts` selects its validator version at runtime with
`importlib.import_module`: which version cannot be seen statically, so every
version in the package is covered. An unresolvable name refuses
`checker_dependency_unresolved` instead of anchoring a `null`.

**The candidate identity is complete, and finalization replays.** Symlink rows
carry a digest of their target, so a retarget is a change. Gitignored inputs --
including the contents of ignored *directories*, which git reports as a single
record -- are inventoried under a file and byte bound, digested at validation and
compared again at finalization; a change refuses `inputs_changed`. Files are
hashed in chunks and skipped above a per-file cap, and a truncated inventory
refuses `inputs_incomplete` unless the campaign sets `allow_incomplete_inputs` --
an inventory that cannot cover everything must not be presented as evidence. Finalization
freezes the candidate and then replays it as a **working-tree delta over the pinned
baseline**: a worktree at the baseline commit with the frozen delta applied on top.
Reconstruction checkers require `HEAD` to stay at the pin, so replaying from the
frozen commit itself would fail a valid candidate for a reason that has nothing to
do with the candidate. State the checker reads from *beside* the workspace -- a reconstruction
checkpoint, for instance -- is declared with `replay_inputs`, digested when a
validation passes, compared before replay (`replay_inputs_changed`), and
reproduced next to the replay; a declared input that is missing refuses
`replay_inputs_missing`. A frozen tree that does not reproduce the accepted result
refuses `frozen_mismatch`.

**Terminal verdicts move the goal.** The values are `GOAL_POLICY`'s alone:
`unavailable` -- including a checker that timed out or printed something
unreadable -- pauses the goal, and a `blocked` verdict blocks it, both through the
native goal service; `fail` and `inconclusive` leave it
active so the round driver keeps going. The model still has no tool that changes
the goal phase, and a transition the goal service rejects is reported as
`goal_transition_failed` rather than returning the bare verdict as if the policy
had been applied.

## Restart and resumption

DSH persists the goal in its session log and deliberately disarms an active goal
when a session starts, so continuation after a restart is an explicit operator
action: resuming the goal in the UI. The project side keeps no recovery state; an
interrupted validation simply leaves no record, which is already safe, and the next
`validate` replaces whatever a previous run left behind.
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

The reviewer worktree belongs to the review operation: `review-run` replaces a
stale checkout, runs the reviewer in it, and removes it afterwards. The report,
receipt and invocation live outside it and survive; there is no separate
preparation or cleanup step.

The plugin exposes the review as `eu4_review_candidate`, and only when the
campaign contract sets `require_independent_review`.

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

`run-campaign.sh` drives the shipped `headless` profile, which answers **one
turn** and exits: measured, a goal created in that turn gets no further rounds.
Use it for a campaign that finishes in one turn.

For a campaign that needs several rounds use the Web profile:

```sh
./dsh/scripts/run.sh --campaign dsh/campaigns/<sidecar>.json
```

The launcher resolves the sidecar, runs `prepare`, registers this checkout in the
DSH workspace registry as the default and boots the profile. A Web session takes
its working directory from that registry, not from the process cwd, so the default
is what makes the campaign workspace reachable; a session created elsewhere gets a
clear `workspace_mismatch` refusal instead of failing silently.

Then one human action is required, because DSH authorizes goal creation from a
human turn: open the printed URL, start a session, and send `/goal <objective>`.
The objective is printed by the launcher and stored in the campaign's
`objective.md`. From there the goal-round driver continues on its own.

Measured end to end: a supervised campaign ran rounds 1 and 2 in one session, the
agent's write outside the candidate workspace was refused by the sandbox, the
checker failed once and then passed, and `eu4_finalize_candidate` confirmed it with
a fresh run and completed the goal.

Open the URL with its `token` intact. A browser that already holds a cookie for
`127.0.0.1` from another DSH instance will be rejected with "dsh web authentication
required" -- cookies ignore ports -- so use a private window for the campaign
instance.

`run.sh` binds an OS-chosen port unless you pass `--port` or set `EU4_DSH_PORT`.
The composed web default is `127.0.0.1:3080`, which is where an ordinary DSH GUI
usually listens, so an unqualified boot collides with a running DSH and fails
with `EADDRINUSE` — verified the hard way; the script now avoids it and prints
the URL it chose.

## Provider and credentials

The `eu4-web` profile is derived from the shipped `web` template, which mounts
`llm-pi-ai` with no providers and leaves the base default model
(`deepseek-official`/`deepseek-flash`) in place. A dedicated `DSH_HOME` has no
credentials for that model, so the web UI asks for an API key — the profile is
correct but unconfigured.

The tracked overlay therefore mirrors the host profile's own user layer: it
declares the `opencode-go` provider (`apiKeyEnv: OPENCODE_GO_API_KEY`) and an
`agent-default-model`, both env-overridable via `EU4_DSH_PROVIDER` and
`EU4_DSH_MODEL`. `provision.sh` copies the host's `.credentials.yaml` into the
dedicated home at mode `0600`, and `--refresh-credentials` re-copies after the
host rotates a key. Copying rather than symlinking means a key saved through the
UI cannot write back into the host store.

## Bridge operations

`python3 -m automation.dsh_bridge <operation> --sidecar <path>` prints exactly one
JSON object. The interface is deliberately small: resolve, validate, finalize,
screen a checker, and drive the reviewer. Exit `0` means a classification or status was produced, `2` is a
usage error, and `3` is a refusal before the checker ran.

| Operation | Purpose |
|---|---|
| `prepare` | validate the sidecar, seed or verify the workspace, record the identity anchor |
| `validate` | run the trusted checker under the retry policy and write a receipt |
| `finalize` | re-run the checker and complete the goal only on a fresh pass |
| `review-run` | freeze the candidate, run the reviewer in its own worktree, validate the report and write the receipt |
| `review-verify` | re-check an existing report and receipt against the frozen candidate |
| `checker-compat` | screen a checker before relying on it |
