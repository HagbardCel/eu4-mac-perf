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
| M6 independent reviewer | pipeline done; live model run pending |
| M7 genuine qualification (A0 qualified; frozen pilot preflighted) | done |
| M8 launch-safety verification | done (live capture stays disabled) |
| M9 docs and PR | done (draft PR #181, not merged) |

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
  a pinned cwd, so ambient module resolution cannot pick up a candidate's copy
  (no `-P`: it exists only from Python 3.11).
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

## M6 -- independent reviewer, host-mediated channel

- The reviewer is a separate process with no goal and no completion authority:
  `dsh --profile headless --json "<task>"` in a detached worktree created at the
  candidate commit frozen by the bridge. It writes nothing protected.
- `review-preflight` freezes the validated candidate (once, reused while the
  identity holds), creates the worktree at that exact commit, and writes the
  invocation record with the existing handoff primitive. `review-run` spawns the
  reviewer, and the **host** parses the event stream, recovers the
  `eu4-review/v1` object, takes the session identity from the stream (never from
  the reviewer's own claim), validates the report, and writes both the report
  and the reviewer receipt itself.
- Four conditions are reported separately and never conflated: the process
  exited 0, a schema-valid report was recovered, the outcome is
  `qualified|rejected|unresolved`, and a receipt was created. A completed review
  task is not a qualified candidate.
- `finalize` now honours `goal.require_independent_review`: with it set, the
  frozen candidate must have a `qualified` review whose receipt matches the
  candidate and oracle digests, verified with `require_live=True`. A scripted
  reviewer (the test double) is marked `qualification_mode: scripted` and can
  never authorize completion; `review-verify --allow-scripted` exists only to
  inspect the pipeline.
- The oracle identity is the verification-manifest digest when the campaign has
  one, otherwise the sealed evidence-input digest, because `eu4-review/v1`
  requires a 64-hex oracle and inventing a constant would be worse.
- Verified end to end with a scripted reviewer process: a seeded defect is
  rejected with a finding, `finalize` refuses (`review_required`), the candidate
  is corrected, a second review qualifies it, and `finalize` **still** refuses
  because a scripted receipt is not a live qualification. Also verified: review
  is refused without a passing validation, a reviewer without a session identity
  is refused and gets no receipt, and unparseable output is refused.
- Two defects found and fixed while wiring this:
  - the anti-shadowing root check rejected any candidate worktree of this
    repository for *containing* `automation/dsh_bridge.py`; it now checks that
    the running `automation` package is not resolved inside the candidate, which
    is the property that actually matters;
  - the reviewer worktree was never removed, leaking a registered git worktree
    per review. `review-clean` now removes it explicitly (the report and receipt
    live outside it and survive), and the tests deregister theirs.
- Still pending: an actual live reviewer run against a real model, which needs
  provider credentials and spends tokens, so it remains an operator step.

## M6 interface notes (established from the installed CLI)

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

## M7 -- genuine qualification

### A0 trusted validation: qualified end to end

Run against `campaigns/eu4-a01-trusted-validation` through the bridge, with the
real checker and the installed EU4 bundle:

| step | result |
|---|---|
| `checker-compat` | `ok`; baseline `fail` with the expected counterexample; every `automation.*` helper it imports resolves inside the trusted checkout |
| `prepare` | seed mode; verification-manifest digest `da30a64f5a8fa20c...` bound into the identity |
| `validate` #1 | `fail` -- `candidate.table != frozen instruction_static table` |
| repair | `table` and `engine_enum_4_gl_mode` taken from `primitive-table.evidence.json` (`output.table`, `output.engine_enum_4_gl_mode`), not guessed; `out_of_range_default` deliberately left alone |
| `validate` #2 | `pass`, receipt `d3d29099-r2.json`, `goal_policy: qualified` |
| `finalize` | fresh confirmation run returned `pass`; frozen seed digest `d3d29099...`; `finalization.json` written; 1 of 2 confirmations used |
| journal | 5 entries (`checker_compat`, `receipt`, `receipt`, `freeze`, `finalization`), chain intact |
| `status` | `current: true`, no stale fields |

### Frozen historical pilot: preflighted, honestly blocked

`checker-compat` is now a screening tool, and screening the frozen campaigns
found real problems before any goal round was spent:

| campaign | baseline | verdict |
|---|---|---|
| `eu4-re2-pilot` | **pass** ("reference validated and candidate matched") | `unsupported` -- a completed campaign cannot distinguish a candidate |
| `eu4-index-buffer` | fail ("trusted file edited: tests/gfx_buffers_host.mm") | unusable: the pin itself is inconsistent |
| `eu4-set-scissor-rectangle` | **unavailable** ("shared checker does not match the pinned commit") | `unsupported` after tightening the rule |
| `eu4-vertex-buffer-multiple` | fail ("research note is missing") | **selected**: a concrete, remediable defect |
| `eu4-p511-phase1` | fail ("research note missing") | viable alternative |

The chosen pilot then ran to an honest terminal state: `checker-compat ok`,
`prepare` on a detached worktree at `97ffb65` with an empty delta, `validate` ->
`fail` (`research note is missing`, `goal_policy: remediate`), and `finalize`
refused `not_qualified`. No completion was granted, and no reconstruction work
was attempted -- this milestone demonstrates the machinery, not the renderer.

Two bugs were found by these runs and fixed:

- `checker-compat` accepted a baseline verdict of `unavailable` as compatible.
  A baseline that cannot produce a remediable verdict means the checker or its
  evidence does not match the pin, so no candidate could ever be judged against
  it; only `fail`/`inconclusive` now count as usable.
- the compat baseline created a temporary git worktree and relied on the
  temporary directory being deleted, which would have left a registered
  worktree behind. It is now removed explicitly in a `finally`.

The frozen pilot's candidate worktree is intentionally materialized at
`~/.local/share/eu4-dsh/state/dsh-frozen-offline/workspace` (detached at the
pin), which is why the worktree count is 35 rather than 34.

## M8 -- launch safety, stated precisely

- `tests/test_dsh_launch_safety.py` pins the real semantics rather than a
  convenient reading: the budget is enforced (a fourth claim under a limit of
  three is refused `exhausted`), and **accounting is not mutual exclusion** --
  two claims coexist as `reserved` while budget remains, because `claim()`
  checks only the total. A started reservation stays charged through
  reconciliation.
- The DSH bridge is asserted to import no launch path at all: a source scan
  rejects `live_gfx`, `launch_ledger` and `launch_counter_path`, so the DSH path
  cannot start an original-EU4 launch even by accident.
- A sidecar that tries to enable live capture is rejected with `unavailable`-grade
  failure before anything runs.
- Where a process lease belongs, if it is ever added: a host-wide lease keyed by
  the executable SHA and the capture output, acquired before the launch and held
  for the lifetime of the game process, adopted by *every* launcher. It must stay
  separate from the ledger, whose job is accounting; making the ledger's lock
  long-lived would turn an accounting record into a lock file and break the
  reconcile/no-refund semantics the existing tests rely on.
- Residual compatibility gap, unchanged: worktrees pinned before the ledger
  existed cannot participate in any shared mechanism, so "at most one EU4"
  cannot be guaranteed cluster-wide regardless of what DSH does. That is why
  this PR keeps DSH capture disabled instead of shipping a coordination story it
  cannot enforce.

## M9 -- documentation and PR

- `docs/dsh-native-goal.md` covers the architecture, the two-axis verdict model, the
  checker-aware retry policy, completion and the guard, candidate snapshots and the
  journal, the execution roots and their assertions, the reviewer channel, the Ghidra
  replica, the launch-ledger limits, the threat model, and the operator commands.
- `dsh/README.md` covers the profile and scripts; `automation/README.md` gained a pointer.
- The branch was rebased onto `main` (`606223b`) after the base landed as PR #179, so the
  PR is not stacked. Everything was re-verified after the rebase: 75 DSH tests, 86 OpenCode
  tests, 11 node tests, compileall, preflight.
- Draft PR: https://github.com/HagbardCel/eu4-mac-perf/pull/181 -- opened, deliberately not
  merged.

## Round 5 additions

- **Live mount proof.** Composition could not distinguish "row present" from
  "plugin installed". The web app does not surface plugin INFO logs, so an
  opt-in `EU4_DSH_BOOT_PROOF` path is written at the end of `apply()` with the
  registration counts, and `smoke-boot.sh` boots a real session to read it.
  Measured: 3 tools, 1 guard, correct sidecar/infra/python.
- **Frozen campaign scope corrected.** The `eu4-vertex-buffer-multiple` sidecar
  allowed 2 of the checker's 8 `IMPLEMENTATION_PATHS`, so a DSH agent would have
  been refused the moment it edited replacement code. The sidecar now mirrors the
  checker exactly, forbids the campaign directory and the frozen documents, and
  `tests/test_dsh_campaign_scope.py` compares the two declarations directly so
  they cannot drift again.
- A malformed edit of my own (duplicated helper blocks while moving the mount
  proof) was caught by `node` before it reached a session; the tools section was
  rewritten rather than patched around.

## Round 6 additions

- **Reviewer event contract verified from source, not inferred.** The installed
  `dsh-headless` json projection writes `{"type":"session","sessionId":...}`
  first, one `{"type":"text","text":...}` per committed assistant text block,
  and `{"type":"final","text":...}` when the run finishes (also `thinking`,
  `status`, `tool_call`, `tool_result`). The extractor now prefers the `final`
  event, so a review object merely *quoted* in a thinking block or a tool result
  cannot be mistaken for the answer; it falls back to the whole stream only when
  there is no final event. Both properties are tested with the real shapes.
- **CI portability fixed.** The concurrency test spawned the repo-local `.venv`
  interpreter, which does not exist on the ubuntu runners, so it would have
  errored instead of testing the run-root lock; it now uses `sys.executable`. The
  scope-drift test now skips (rather than fails) when a campaign checker cannot
  be imported in a data-less environment.
- The workflow's own invocation (`discover -s tests`) passes locally: 2313 tests,
  OK, 16 skipped.

## Round 7 additions -- self-review before merge

The plan asks that the infrastructure PR be reviewed for accidental OpenCode
dependency, duplicated validation logic and unnecessary privilege. The mechanical
part of that review is now encoded rather than asserted.

- **A planned deliverable was missing.** `tests/test_dsh_open_regression.py` had
  been listed in the layout and never written. It now exists and asserts: no DSH
  module imports the OpenCode adapter or CLI; the *only* thing the bridge takes
  from `automation.runner` is `run_accept`, never the campaign loop; no DSH module
  spawns a shell (`shell=True`, `os.system`, `os.popen` absent); no DSH module
  writes OpenCode state (`.local/automation` appears only in the refusal message
  that forbids it); the plugin never mentions OpenCode; and
  `automation.state.load_campaign` still rejects a non-`opencode` runtime while
  the DSH reader accepts the same campaign directory.
- **Duplication removed where it was real.** `canonical_json`, `digest_text` and
  the file digest existed in three copies (this module pair plus the frozen
  `verification_manifest`). The DSH modules now delegate to
  `automation.verification_manifest`, verified to produce byte-identical output,
  so receipt and journal digests are unchanged. `verification_manifest` itself is
  untouched: it is pinned by frozen manifests.
- What remains deliberately duplicated is `path_allowed`, because importing the
  L3.2b checker pulls the benchmark packet modules; an equivalence test compares
  it against the trusted implementation so it cannot drift.

Audit results, for the record: no OpenCode imports, no `shell=True`, no OpenCode
state writes, and `run_accept` as the single runner dependency.

## Round 8 additions

- **The skill catalog is verified, not inferred.** `skill-catalog.sh` boots the
  profile with a `--patch`-injected probe and reads `ctx.skills.list({cwd})` --
  the same merged catalog the agent's scope selects. It reports the skill,
  `modelInvocable`, and its path, and exits non-zero if it is absent. That closes
  the last gap I had twice deferred to "needs a real turn": what a real turn still
  can't prove is that the model *chooses* to load the skill, which is a model
  behaviour, not an infrastructure property.
- **Cross-contamination found and removed.** The first probe showed **23** skills
  in the catalog: our reconstruction skill plus 22 unrelated personal ones from
  `~/.agents/skills`. A dedicated EU4 migration profile has no business offering
  the agent Superset and browser-automation skills. `skill-filesystem` now sets
  `includeDefaultRoots: false`, so the catalog is exactly the project root's
  skills -- measured after the change: 1 skill.

- **`smoke-boot.sh` emitted invalid JSON.** It hand-escaped the nested proof
  object, so every quote became `\"` inside an object literal and the output
  could not be parsed -- I had read it as text in round 5 and not noticed. The
  script now composes its output with the interpreter; verified by parsing it:
  `ok: true`, 3 tools, 1 guard.

## Round 9 additions -- the verdict table was under-covered

Auditing my own suite found that the verdicts which steer agent behaviour most
had **no coverage at all**: every fixture emitted only `pass` or `fail`, so
`inconclusive`, `blocked` and `unavailable` were never exercised end to end, and
neither budget stop had ever been reached.

- `tests/test_dsh_verdicts.py` now covers `classify()` as a unit: every verdict
  passed through; `pass` with a non-zero exit demoted to an infrastructure error;
  the named kinds `timeout`/`truncated`/`unreadable` preserved; a non-zero exit
  without a named reason reported as `exit_code`; a clean exit with no verdict as
  `unreadable`.
- A new fixture (`verdict-campaign`) emits whichever verdict a file beside the
  workspace names, so one campaign reaches every row. Each verdict is checked to
  reach its goal policy -- `qualified`, `remediate`, `investigate`, `block`,
  `pause` -- and to be recorded unchanged in the receipt.
- Both budgets are now exercised: the validation budget stops as
  `policy_stop/validation_budget` after its last permitted run, and the
  finalization budget stops as `policy_stop/finalize_budget` after the second
  confirmation rather than confirming a third time.
- Recovery is now also tested for an interrupted **finalization** (a different
  intent prefix): it is quarantined and the passing receipt survives untouched.

## Remaining work (operator-gated, not autonomous)

Three acceptance items need a human or spend, and are scripted and documented but not
executed here:

1. **Live autonomous `/goal` smoke run.** `create_goal` requires direct-human authority, so
   the loop cannot be started from a script. `dsh/scripts/smoke-goal.sh` prints the exact
   `/goal` objective; the expected sequence is round 1 validate-fail, native continuation,
   the model writes `ready`, round 2 validate-pass, and completion only through
   `eu4_finalize_candidate`.
2. **Live reviewer run.** `review-run` without `--reviewer-cmd` invokes
   `dsh --profile headless --json`, which needs provider credentials and spends tokens. The
   host-mediated channel, schema validation and receipt writing are all tested with a
   scripted reviewer; only the model call is missing.
3. **A reconstruction campaign reaching `qualified`.** A0 is a genuine evidence-backed
   qualification (repaired against the frozen instruction-static oracle), but it is a
   trusted-validation campaign, not a reconstruction. The frozen reconstruction pilot
   (`eu4-vertex-buffer-multiple`) is honestly blocked at "research note is missing" because
   this milestone deliberately does not implement the renderer. Closing this needs a
   campaign run by the DSH agent, which depends on item 1.

## Pending / caveats

- **Live mount is now proven** (round 5). `dsh/scripts/smoke-boot.sh` boots a real
  `eu4-web` session with `EU4_DSH_BOOT_PROOF` set and reads the record the plugin
  writes at the end of `apply()`: `registered_tools: 3`, `registered_guards: 1`,
  with the sidecar, infra root and interpreter it resolved. No model request is
  sent, so it costs no tokens. This replaces the previous composition-only claim.
- **Skill visibility is now proven too** (round 8). `preflight.sh` can only show
  that the `skill-filesystem` row is configured; whether the *registry view* the
  agent's scope selects contains the skill is a different question. A committed
  one-shot probe (`dsh/scripts/skill-catalog.sh` with
  `dsh/scripts/probe/skill-catalog/`) injects `skills`, reads the same merged
  catalog the model sees at boot, and asserts the skill is present. Measured:
  exactly one skill, `opengl-metal-reconstruction`, `modelInvocable: true`, from
  this repository's `.opencode/skills`. No model request is sent.
- The plugin is still plain ESM JavaScript (no build step, no npm dependency);
  the real tool surface and completion guard arrive with M3.
- Evidence-input binding is declared but not yet enforced as a gate; M4 adds it
  together with the generic candidate-delta snapshot and the append-only
  journal.
- `~/.dsh` is untouched by the scripts, but the interactive DSH session that runs
  them exports `DSH_HOME=~/.dsh`; `run.sh`/`preflight.sh`/`provision.sh`
  deliberately override it.
