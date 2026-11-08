# DSH native-goal infrastructure -- implementation progress

Plan: rev. 3 (approved). Branch `dsh/native-goal`, base `6fa1888`
(`feature/l3-2b-bootstrap`), worktree `~/Projects/MiniProjects/eu4-dsh-infra`.

## Status

| Milestone | State |
|---|---|
| M1 profile, pinning, skill parity, Ghidra probe | done at composition level; one live-session check pending |
| M2 minimal bridge, retry policy, classification | done |
| M3 seed-only native goal loop | bridge + plugin done; live run needs an operator-created goal |
| M4 delta snapshot, evidence binding, journal, replica | done |
| M5 recovery, limits, isolation | done |
| M6 independent reviewer | not started |
| M7 genuine qualification (A0 + frozen offline) | not started |
| M8 launch-safety verification | not started |
| M9 docs and PR | not started |

## M1 -- established

- Worktree created at `6fa1888`. The primary checkout was on `4f3b217`
  (`feature/l3-2b-bootstrap`, three commits ahead of the pin); those commits touch
  only L3.2b gfx/contract code and none of the modules this work reuses
  (`state.py`, `handoff.py`, `launch_ledger.py`, `live_gfx.py`, `runner.py`).
  The primary checkout, its branch, and its 32 pre-existing worktrees were left
  unchanged.
- `eu4-web` profile lives in a dedicated `DSH_HOME` (`~/.local/share/eu4-dsh`).
  `provision.sh` initially honoured an inherited `DSH_HOME=~/.dsh` and created
  `~/.dsh/profiles/eu4-web`; that profile was removed after verification and the
  scripts now ignore an inherited `DSH_HOME` in favour of `EU4_DSH_HOME`.
  `~/.dsh/profiles/web`, `sessions/`, credentials and `profiles/node_modules`
  were not modified.
- Patch semantics corrected: id-targeted entries cannot insert a row
  (`patch: entry "eu4-goal" not found`); insertion uses a top-level `- insert:`.
  `skill-filesystem` is re-enabled on the host plane and the composed tree's own
  provenance comment shows it patched by the profile overlay.
- `preflight.sh` passes: pinned dsh `0.2.0-rc.2`, node `v26.13.0`, pnpm `12.18.0`;
  `goal`, `goal-round-driver`, `tool-goal`, `command-goal` and `eu4-goal` present
  and enabled somewhere in the tree; host-plane `skill-filesystem` enabled with
  `customSkillDirs` -> `EU4_DSH_SKILL_ROOT`; plugin resolves from the profile root
  as a cordis plugin (`apply`, `name`, `inject`); skill sha256
  `2c2a14e60cc26a238dc9cfe99919dd8658cb94622ecf3234dcfa6b5d5f0d64df`.
- Offline Metal parity: `tests.test_gfx_shadow_worker` -> 12 tests OK in 1.9s,
  run inside the worktree with its own `.venv` (python 3.12.13, Pillow only).
- Ghidra query parity: `_GfxDraw` -> `ok`, entry `0x15ea789`, 9.4s.
- **Ghidra concurrency is unsafe.** Two simultaneous `analyzeHeadless -readOnly`
  processes: one exits 0, one aborts with
  `ghidra.framework.store.LockException: Unable to lock project!`.
  `-readOnly` suppresses saving, not the project lock. Amendment G therefore
  selects a read-only replica (project is 701 MB), to be set up in M2.
  `dsh/scripts/ghidra-probe.sh` reproduces the result.
- Ghidra query needs `GHIDRA_INSTALL` and a JDK (`JAVA_HOME`); the query module
  truncates its failure detail at ~1000 characters, so the pristine message was
  captured by running `analyzeHeadless` directly.

## M2 -- established

- `automation/dsh_campaign.py` reads a campaign that has no `runtime` or
  `opencode_version` field at all, proving the reader is backend-neutral:
  `automation.state.load_campaign` is untouched and still rejects anything but
  `opencode`. Objective, accept/checker, optional verification manifest and the
  DSH execution config (mode, policy, retry, limits) are resolved and validated;
  a policy may be inline or reference a generic pattern document such as
  `campaigns/eu4-l32b-bootstrap/infra.json`, so no campaign or symbol is
  hardcoded in DSH code.
- `automation/dsh_receipts.py` stores receipts, a `current.json` pointer and
  counters, and digests seed trees (symlinks recorded by target, not followed).
- `automation/dsh_bridge.py` exposes `prepare`, `status`, `validate` and
  `goal-prompt`. It reuses `automation.runner.run_accept` for checkout-free
  execution and verdict parsing, so the verdict vocabulary has one source of
  truth, and never invents a verdict.
- Classification is two-axis. `checker_verdict` is only ever what the checker
  emitted. `bridge_classification` is `verdict`, `policy_stop`,
  `infrastructure_error` or `integrity_refusal`. An unreadable or timed-out
  checker becomes `infrastructure_error` with `goal_policy: unavailable`; a
  repeated identical failure becomes `policy_stop`/`no_progress` and is never
  reported as a checker `blocked`.
- The retry policy is checker-aware. `stateful` checkers declare
  `confirmation_rounds`; `deterministic` ones may declare
  `max_identical_retries`. Admission uses only prior state (previous candidate
  digest, previous verdict, stored counters), never a diagnostics digest that
  cannot exist before the run.
- Verified, in order:
  - fixture acceptance test: an **unchanged, already-correct** candidate
    produces `fail` then `pass` under `stateful`/1, with an identical candidate
    digest across both runs and 2 validations recorded;
  - `deterministic`/0 refuses the identical retry as `policy_stop`;
  - a checker emitting non-JSON yields `infrastructure_error`/`unreadable` with
    `goal_policy: unavailable` (recorded for audit, but carrying no verdict);
  - a checker modified after `prepare` fails closed on `checker_sha256` drift
    (exit 3); a candidate path outside the allowlist fails closed on `policy`;
  - an identical verified `pass` is reused, not re-run (the checker's own
    invocation counter stays at 1);
  - end-to-end on the real `campaigns/automation-smoke` campaign and checker:
    `fail` then `pass`, receipt `abfa9369-r2.json`, `status` reports
    `current: true`.
- Review finding 2 is applied where it matters: the receipt is not treated as
  authority. `prepare` anchors the trusted material (checker, sidecar, source
  campaign, manifest, policy, objective, baseline) and every `validate` compares
  against that anchor, so editing the checker is drift, not a new identity.
- Correction to an earlier draft of this bridge: it recorded
  `sandbox_mode: "workspace-write"` and `ok: true` for the workspace assertion.
  The bridge cannot observe DSH's sandbox, so it now records what the host
  declared (`EU4_DSH_WORKSPACE`, `EU4_DSH_SANDBOX_MODE`) and reports
  `verified: false` when nothing was declared. M4 turns a verified assertion
  into a hard gate.

## M3 (in progress) -- finalize authority

- `dsh_bridge finalize` implements Amendment B: a stored receipt is never
  authority. Under the run-root lock it re-checks the prepared anchor, requires
  a current receipt whose identity digest matches the freshly derived one,
  refuses if that receipt is not a pass, and then **re-runs the checker**. Only
  that fresh run can authorize completion; the confirmation is bounded by
  `FINALIZE_VALIDATION_LIMIT = 2` and counted separately from the model's
  validation budget.
- A failing fresh run writes an auditable receipt for the attempt and refuses
  with `fresh_validation_failed`; nothing is frozen and no `finalization.json`
  is produced.
- The required adversarial test exists and passes: a `fail` receipt is edited to
  `pass` **and re-hashed**, so it is self-consistent and passes digest
  verification, and `finalize` still refuses because the fresh run returns
  `fail`. A receipt edited without re-hashing is rejected as `receipt_corrupt`.
- `finalize` also refuses `stale` when the candidate moved after the passing
  validation, and `not_qualified` when the latest validation never passed.

## M3 (in progress) -- plugin and completion gate

- `dsh/eu4-goal-plugin` exposes `eu4_campaign_status`, `eu4_validate_candidate`
  and `eu4_finalize_candidate`, and registers exactly one monotonic guard.
- The guard denies **every** model-initiated `update_goal(action="complete")`
  with no dependence on binding, receipt or session state, which closes the
  first-round gap the review found: binding cannot be consulted before any
  campaign tool has run. Verified from `dsh-tools/lib/index.js` that
  `guardReason` consults `layers.global` before the agent scope chain, so a
  plain-context guard does cover the preset-registered `tool-goal`; the plugin
  registers on the plain context for that reason and performs no I/O.
- Human control is intact: `dsh-command-goal` and the GoalBar reach the goal
  domain through `ctx.goals.*` from a command invocation, not through the tool
  pipeline, so the guard does not restrict an operator pausing, clearing or
  completing a goal.
- Completion is two-phase: the finalize tool re-verifies through the bridge and
  only then calls `ctx.goals.complete(agent, ref)` itself. The goal that gets
  completed is the one bound at bind time: `goal_id`, `revision` and an
  objective digest are persisted to `<run-root>/plugin-binding.json`, so editing
  or replacing the goal invalidates prior qualification.
- The plugin registers tools with a plain JSON Schema rather than importing
  `defineTool`, because a strict pnpm layout does not make
  `@deepseek-ai/dsh-tools` resolvable from an out-of-tree plugin. That keeps the
  plugin dependency-free and installable without network access.
- Bridge invocations use an explicit interpreter, `cwd` and `PYTHONPATH` with
  `-P`, so ambient module resolution cannot pick up a candidate's copy.
- Verified: 11 node tests (`pnpm -C dsh/eu4-goal-plugin test`) covering the
  guard matrix, tool registration against a fake context, and the goal-binding
  refusal, including that a live goal with no prior binding is not completable;
  preflight still green; 37 Python DSH tests; OpenCode suite 86/86.
- `dsh/scripts/smoke-goal.sh` prints the operator steps, including the `/goal`
  objective. **The autonomous end-to-end run is human-gated**: creating a goal
  requires direct-human authority, so the loop cannot be started by a script.

## M4 -- generic delta, journal, assertions, replica

- `automation/dsh_snapshot.py` enumerates a candidate delta from porcelain v2,
  because v1 discards the status letter: a deletion, a mode change and a rename
  were previously indistinguishable from a modification, and
  `handoff.collect_candidate_rels` can only see one filename under
  `analysis/qualification`. Adds, deletes, renames, mode changes, typechanges,
  untracked files and symlinks are all represented.
- Every entry must be permitted. An out-of-allowlist path, a frozen path, or a
  symlink the policy did not allow is a refusal; nothing is silently dropped.
- `build_candidate` reproduces exactly the delta on top of the pinned baseline
  in a throw-away index, and `verify_round_trip` re-derives the diff from the
  built commit and requires it to match the intended delta in paths, kinds and
  modes, in both directions (no missing change, no unintended change). A rename
  removes the old path as well as adding the new one -- the first version did
  not, and the round-trip check caught it.
- `fingerprint`/`recheck` detect the worktree changing while the snapshot is
  built.
- The bridge now identifies a worktree candidate by that delta, exposes a
  `snapshot` op, and **freezes the candidate at finalization**: the commit
  recorded in `finalization.json` is the one the fresh passing run saw.
- The journal is append-only and hash-chained, and every receipt is written
  through it. `finalize` refuses `journal_mismatch` when the current receipt is
  not the journalled one. This is tamper evidence, not authority: the required
  Amendment-B test now covers both layers -- forging the receipt alone is caught
  by the journal, and forging the receipt *and* rebuilding the journal chain is
  still refused by the fresh run (`fresh_validation_failed`).
- Execution roots are asserted before any run: the candidate may not be the
  infrastructure root or nested inside it, the campaign and checker must live in
  the trusted checkout, a candidate may not ship `automation/dsh_bridge.py`, a
  declared `EU4_DSH_WORKSPACE` must equal the campaign workspace, and
  `EU4_DSH_SANDBOX_MODE=danger-full-access` refuses qualification outright.
- Ghidra contention is solved by a private replica rather than a lock, because a
  DSH-only lock cannot coordinate with pinned OpenCode worktrees or a GUI.
  Measured: `--create` clones the 701 MB project to
  `~/GhidraEU4/<sha>-dsh`, a query with `EU4_RE_EXPORT` pointed at the replica
  returns the same evidence (`_GfxDraw` at `0x15ea789`), and a shared-project
  query and a replica query run **concurrently** with both exiting 0 -- which
  two shared-project queries cannot do.
- Verified: 55 Python DSH tests, 11 node tests, preflight green, OpenCode suite
  86/86.

## M5 -- recovery, limits and isolation

- A validation writes `<run-root>/validation/<id>.intent.json` before the checker
  starts and removes it only after the receipt is durable. An interrupted run
  therefore leaves a visible marker rather than a candidate that merely looks
  unvalidated.
- `recover` takes the run-root lock, moves every surviving intent to
  `validation/quarantine/`, journals the quarantine, and reports whether the
  current receipt still describes the current candidate. It never writes a
  receipt, so it cannot manufacture a pass.
- Limits are enforced where they cannot be laundered by a restart, because the
  counters live in the run root rather than in the session: `max_validations`,
  `wall_clock_seconds`, the checker-aware retry allowance, and a separate
  bounded finalization budget (two confirmation runs).
- Isolation: two concurrent `validate` processes against one run root produce at
  most one `busy` refusal and exactly one valid `current.json` pointing at a
  parseable receipt with an intact journal. This is a real two-process test, not
  two threads: `redirect_stdout` is process-global, so the first version of that
  test could not have detected a race.
- Session isolation: `goals` stay authoritative for goal lifecycle and the run
  root for evidence. DSH disarms a goal on restart by design, so nothing here
  re-arms it, and live capture is refused outright, so a restored session cannot
  resume capture.

## M6 -- reviewer interface established (not implemented yet)

Checked against the installed CLI rather than assumed:

- `headless` is a **shipped profile**, not an app argument. `dsh --profile eu4-web
  headless ...` boots the web app and silently ignores the word, so the reviewer
  must be a separate `dsh --profile headless` process.
- `--from-default-profile headless` is rejected: shipped profiles cannot be
  customisation targets, so there is no `eu4-review` profile to create from it.
- `dsh --profile headless [--json] [--session-id <id>] "<task>"` is the one-shot
  interface. `--json` emits newline-delimited run events instead of the final
  message, which is what lets the host capture the reviewer's structured output
  and its session identity; `--session-id` adopts a persisted session and errors
  on an unknown id.
- The headless bundle layers directly over `dsh-base`, which keeps the
  host-plane `skill-filesystem` row that `web-app` disables. Running the
  reviewer with `cwd` set to the candidate worktree therefore discovers the
  repository's own `.opencode/skills` without any profile work.

Consequence for the design: the reviewer is a separate headless process in a
detached worktree at the frozen candidate commit, the host parses its NDJSON
events, validates the final message as `eu4-review/v1`, and writes both the
report and the reviewer receipt itself, using the host-observed session id. The
reviewer supplies no identity and writes nothing protected.

## Pending / caveats

- The profile and plugin are verified at composition level. A live interactive
  session has not yet been booted, so "session loads
  `opengl-metal-reconstruction`" still needs one human-run check.
- The plugin is still plain ESM JavaScript (no build step, no npm dependency);
  the real tool surface and completion guard arrive with M3.
- Evidence-input binding is declared but not yet enforced as a gate; M4 adds it
  together with the generic candidate-delta snapshot and the append-only
  journal.
- `~/.dsh` is untouched by the scripts, but the interactive DSH session that runs
  them exports `DSH_HOME=~/.dsh`; `run.sh`/`preflight.sh`/`provision.sh`
  deliberately override it.
