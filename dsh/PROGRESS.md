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

## Round 10 -- the live reviewer path, and what it exposed

I executed the reviewer's *default* command (`dsh --profile headless --json`) for
the first time, believing no credentials were present and so no cost. That was
wrong: the first attempt completed and wrote a **live** review receipt
(`qualification_mode: live`), so a real model call happened. I had already deleted
the temporary run root before inspecting it, so its outcome and receipt were lost;
**that was an unintended spend and an unrecorded result.**

The second attempt timed out at 300s, and exposed two real defects:

- `ReviewError` was not caught, so a reviewer timeout surfaced as
  `integrity_refusal`/`internal_error` -- the wrong axis. A reviewer that cannot
  answer has proven nothing about the candidate, so it is now
  `infrastructure_error`/`review_failed` with goal policy `unavailable`, and a
  journal entry records it.
- The CLI's catch-all reported unexpected exceptions as `integrity_refusal` too;
  an unhandled exception is an infrastructure condition, not evidence of
  tampering, so it is now `infrastructure_error`/`internal_error`.

Both are covered: a reviewer script that sleeps past a one-second budget is
asserted to produce `infrastructure_error`/`review_failed`/`unavailable` with no
report written, and a synthetic exception is asserted to emit JSON rather than a
traceback on stdout.

Also found while exercising this path: **review of a seed candidate crashed**
(`git worktree add` with a `None` commit) instead of refusing. Seed candidates
have no commit to check out, so `review-preflight` and `review-run` now refuse
`review_needs_commit` -- previously the smoke campaign, which is seed mode, could
not be reviewed at all and failed with a traceback.

### Deliberate live capture (recorded)

Approved by the operator after the unintended run above. A headless reviewer
completed in 334s against a worktree candidate:

| field | value |
|---|---|
| outcome | **`unresolved`** (not qualified) |
| qualification_mode | `live` |
| reviewer session | `session-ab907543-6ca8-4901-bac0-3bbc1dd0e9aa` |
| findings | 1 (P1, `evidence_gap`) |
| receipt | `eu4-reviewer-receipt/v1`, report sha256 `2c0cb46b…` |
| strict `review-verify` | not qualified -- "review outcome was unresolved" |
| `finalize` | refused `review_required` |

The reviewer did not accept a thin candidate, which is the behaviour the design
wants. Its finding was also substantively correct and found a real flaw:

> "The candidate delta against aa16c30 is only an added candidate.txt … it makes
> no migration change. The oracle identity 37517e5f… is exactly sha256 of the
> canonical empty evidence list."

That is true. `_oracle_digest` fell back to `evidence_inputs_digest`, which for a
campaign with no manifest and no declared evidence is the digest of an empty
list -- a meaningless identity presented as an oracle. `eu4-review/v1` requires a
64-hex oracle, and I had chosen the empty-list digest over "inventing a
constant"; the reviewer showed that is the same mistake wearing a hash. Review
now refuses `review_identity_missing` unless the campaign declares a verification
manifest or evidence paths, so a reviewer is never handed a fake oracle.

## Round 11 -- documentation drift, caught and guarded

Two documentation edits I made in earlier rounds had silently done nothing: a
`str.replace` whose anchor no longer matched, so `docs/dsh-native-goal.md` still
listed three operator scripts while eight existed, and `review-run` /
`review-verify` were described in prose but never named. Worse, `dsh/README.md`
claimed `DSH_HOME` could be overridden in the environment when the scripts
deliberately ignore it -- the exact trap that produced the round-1 misprovision.

Both documents are now complete and accurate: all 12 bridge operations and all 8
scripts are listed, the home-resolution story is corrected, and
`tests/test_dsh_docs.py` asserts the mapping in both directions so it cannot
regress. That test also pins the documented exit codes to the bridge's own
constants rather than to prose.

Writing it caught one more thing of mine: the exit-code assertion was brittle
about line wrapping and failed on correct prose, so it now compares collapsed
whitespace.

## Round 12 -- the smoke run never started

The operator reported conducting the smoke test. It had not run: the dedicated
home contained no `dsh-offline-smoke` state at all, and the only startup log
(the boot at 12:15) recorded

```
StartupError: dsh: startup failed: 2 required plugins did not activate
  webserver (required)  Package: @deepseek-ai/dsh-host-webserver
  Error: listen EADDRINUSE: address already in use 127.0.0.1:3080
```

`run.sh` passed no `--port`, so the profile inherited the composed web default
3080 -- the port the operator's ordinary DSH GUI was already using. The most
likely moment to boot this profile is while a DSH is running, so this was a
predictable defect in my script, not operator error.

`run.sh` now binds an OS-chosen port unless `--port` or `EU4_DSH_PORT` is given,
and the docs say so. Verified: the same command now prints
`http://127.0.0.1:51608` and no `EADDRINUSE`.

A second consequence worth noting: a failed boot leaves no campaign state, so
"nothing happened" looks identical to "never attempted" unless the startup log is
read. `smoke-boot.sh` already catches this class of failure for the profile; the
operator-facing path is now resistant to it too.

### A second stumble was waiting behind the first

`validate` refuses an unprepared campaign (`not_prepared`), and `prepare` had no
model-facing tool -- so even after a successful boot, the first validate would
have stalled and the agent could only have reported a blocker it could not act
on. `eu4_validate_candidate` and `eu4_finalize_candidate` now run `prepare`
first: it is idempotent and anchors only trusted material (never the candidate),
so a fresh goal no longer needs a manual preparation step.

Making that testable needed one change of shape: `apply()` accepts an optional
`bridge` in its config, defaulting to the real `runBridge`, so a test can script
the operation sequence. That immediately caught a wiring mistake of mine -- the
`status` call still used the real bridge, so the injected one never saw it -- and
then a substring rename that clobbered the exported `runBridge` declaration. Both
were caught by the tests before any live run.

Measured after the change: 15 node tests pass (up from 11), and a live boot still
reports 3 tools and 1 guard.

## Round 13 -- the profile was unconfigured, so the UI asked for a key

The operator booted the profile (the port fix worked) and the web UI asked for an
API key. Cause, established from the composed tree rather than guessed: the
shipped `web` bundle mounts `llm-pi-ai` with **no providers**, and
`agent-default-model` falls back to the base default
`deepseek-official`/`deepseek-flash`. A dedicated home has no credentials for
that provider, so there is nothing to select and the UI prompts. The host has all
three pieces -- provider wiring, a default model, and a credential store -- and
none of them had been carried into the dedicated profile.

Fixed in three parts:

- the tracked overlay now declares the `opencode-go` provider
  (`apiKeyEnv: OPENCODE_GO_API_KEY`) and an `agent-default-model`, matching the
  host, both overridable through `EU4_DSH_PROVIDER` / `EU4_DSH_MODEL`;
- `provision.sh` copies `~/.dsh/.credentials.yaml` into the dedicated home at mode
  `0600` when it lacks provider keys, and `--refresh-credentials` re-copies after
  a key rotation. Copy rather than symlink, so a UI-saved key cannot write back
  into the host store;
- the README and docs describe the three pieces and the env-var alternative.

Verified: the composed tree now shows the provider config and the chosen model;
the dedicated credential store is 298 bytes at mode 600 holding both
`DEEPSEEK_API_KEY` and `OPENCODE_GO_API_KEY`; preflight still passes; and a boot
prints a URL with no `StartupError`. The host path this mirrors is known-working:
the round-10 live reviewer ran through it with no API key in the environment,
which is why the shared credential store is the right mechanism.

## Round 14 -- the candidate lived outside the agent's write sandbox

With the provider working, the next blocker was structural: the smoke campaign's
run root defaulted to `~/.local/share/eu4-dsh/state/dsh-offline-smoke`, while a
DSH session started from the checkout can only write inside its own workspace.
The agent could never have written `marker.txt`; the run would have failed with
sandbox denials that look like a tool bug rather than a configuration mistake.

- the smoke sidecar now sets `run_root: ".local/dsh/dsh-offline-smoke"`, so the
  candidate is `<checkout>/.local/dsh/dsh-offline-smoke/workspace` -- inside the
  session workspace, and gitignored;
- the roots assertion allows a candidate **inside the checkout only under
  `.local/`**, and still refuses the checkout itself, a candidate that contains
  the checkout, or a candidate anywhere else in the trusted tree;
- `smoke-goal.sh` runs `prepare` itself, so the workspace exists before the goal
  is created rather than appearing on the first `validate`.

Trade-off, stated plainly: this pilot's receipts now sit in an agent-writable
directory. Authority is unaffected -- completion still requires a fresh checker
run by trusted tooling, and the journal remains tamper evidence rather than
access control. Production campaigns should keep receipts in the trusted state
root, which needs the run-root/receipt-root split recorded as a follow-up.

Verified on a throwaway run root against the real `automation-smoke` checker:
`fail` -> write `ready` -> `pass` -> `finalize` `pass`/`qualified`. The shipped
campaign is prepared and clean: workspace seeded, 0 validations.

## Round 15 -- Phase 0 gate checked, and it has not been met

The refactor directive makes a genuine autonomous run the gate for everything
else, so I checked for one instead of assuming.

Evidence, from the dedicated home:

- `~/.local/share/eu4-dsh/sessions/--Users-fabian-Documents-deepseek-harness-default-workspace--/session-5ffdc432.../`
  exists, but decompressing it shows only `session`, `permission/preset`
  (`workspace-write`), `sandbox/mode` (`workspace-write`), `approval/policy`
  (`ask`) and `session/end-seed`. No user message, no goal, no tool call.
- The smoke campaign run root reports `validations: 0`, `current: false`, no
  receipts.

So no `/goal` was created; the UI was opened and a session seeded, nothing more.

The session also reveals the structural reason a run would have failed: its
workspace is `~/Documents/deepseek-harness/default-workspace` -- DSH's first-use
workspace under Documents -- not this checkout, and workspaces are chosen in the
browser, not from the process cwd. The campaign workspace is
`<checkout>/.local/dsh/dsh-offline-smoke/workspace`, outside that session's
sandbox, so edits would have been denied.

### What I changed instead of refactoring

The gate forbids starting structural work, and the blocker was a silent
mismatch, so I made it explicit: `workspaceRefusal()` in the plugin compares the
harness's validated session `cwd` (`agent.session.header.cwd`) with the campaign
workspace the bridge reports, and refuses with `workspace_mismatch` before any
checker runs. It is a pure function with unit tests (inside, outside,
shared-prefix sibling, missing values) and it is wired into both
`eu4_validate_candidate` and `eu4_finalize_candidate`.

This deliberately reads the trusted session header rather than an environment
variable, which the directive requires and which the earlier `EU4_DSH_WORKSPACE`
check could not satisfy.

Verified: 21 node tests pass (up from 15), 108 Python DSH tests, 86 OpenCode
tests, and a live boot still reports 3 tools and 1 guard.

### What unblocks the gate

The operator must open a session whose workspace contains the campaign
workspace -- i.e. add `/Users/fabian/Projects/MiniProjects/eu4-dsh-infra` as a
workspace in the UI and create the session there -- then paste the printed
objective. Until that session exists, every later phase stays blocked by the
directive's own gate.

## Phase 0 -- gate PASSED (genuine autonomous run)

Run with `dsh/scripts/run-campaign.sh dsh/campaigns/offline-smoke.json --json`,
which binds the session workspace *before* DSH starts: it prepares the campaign,
resolves the workspace, generates a `--patch` overlay mounting this checkout's
plugin and skill root into the shipped `headless` profile, `cd`s to the workspace
and launches. No Python process drives the loop; the shipped goal-round driver
owns it.

Evidence from that run:

| item | value |
|---|---|
| session | `session-f7427783-9fe5-499a-80a8-de11071ae8f9` |
| session cwd | `<checkout>/.local/dsh/dsh-offline-smoke/workspace` (the campaign workspace) |
| goal | `goal-05f2a045-9df4-4ff6-8847-9b9d1fc706be`, phase `complete` |
| tool order | `create_goal`, status, bash/read, write, edit, bash, **validate (fail)**, **validate (pass)**, **finalize (pass)**, `get_goal` |
| receipts | `b0685081-r1.json` (fail), `b0685081-r2.json` (pass) |
| finalization | `fresh_verdict: pass`, frozen seed digest `b0685081…`, 2 entries |
| journal | 4 entries `receipt, receipt, freeze, finalization`, chain intact |
| candidate | `marker.txt` = `ready` with no trailing newline; `progress.md` keeps one `## Next` heading |

The model created its own goal from the task text (direct-human authority), fixed
the candidate, took the checker from `fail` to `pass`, and completed through
`eu4_finalize_candidate` -- the guard never let it self-certify.

Two honest observations:

1. The whole campaign completed in **one** headless turn. The goal-round driver
   was not needed for multi-turn continuation here, so `fail → pass` was driven by
   the model calling the checker twice in one turn, not by a scheduled round. A
   campaign that needs several rounds still has to be observed.
2. The run exposed a real defect: the finalization record says
   `session_id: "undefined"` and the plugin binding has no `session_id` at all.
   `agent.sessionId` does not exist on the headless runtime agent -- the id lives
   on `agent.session.id` -- so the missing value was stringified into argv. Fixed
   with an ordered identity chain and covered by tests; the record from this run
   is left as-is because it is the evidence that found the bug.

## Phase 2A / Phase 4 -- shared acceptance, and a smaller plugin

**Checker execution moved to `automation/acceptance.py`.** `runner.py` and the DSH
bridge now import the same `run_accept`; `automation.runner.run_accept is
automation.acceptance.run_accept` is asserted by a test, so the verdict
vocabulary, the output cap and the timeout handling have exactly one home. 42
lines of checker execution left the runner, which keeps only the campaign loop.
The OpenCode regression suite is unchanged at 86 passing.

**The plugin is down to two tools.** `eu4_campaign_status` is gone, as approved:
`get_goal` covers goal state and the bridge covers campaign state, so the tool
only duplicated both. `prepare` already reports the run root and the workspace,
so removing the status tool also removed a bridge call from `finalize`.

Two real defects surfaced while doing this, both caught by tests before any live
run:

- `finalize` **rebound** the session binding to whatever goal was live before
  comparing it, so a human editing the objective would have inherited the earlier
  qualification. It no longer rebinds: the binding established by validation is
  compared, and an edited objective now refuses with `goal_binding`.
- `validate` never bound anything, because the call was guarded by a `run_root`
  that the *validate* result does not carry. The binding is now written from the
  `prepare` result, and kept in memory when it cannot be persisted.

## Phase 2B -- one campaign contract, two readers

`automation/campaign_contract.py` now resolves the fields both backends need:
objective and its digest, the accept command and the trusted checker it names,
the optional verification manifest, the candidate workspace mode with its seed or
pinned baseline, and the checker timeout. The OpenCode loader and the DSH sidecar
reader both consume it, so there is no second campaign schema.

What stayed backend-specific, deliberately:

- the OpenCode loader keeps `runtime`, `model`, `opencode_version`, round and
  time limits, and its live-observation parsing;
- the DSH sidecar keeps its run root, orchestration limits, retry policy and
  candidate policy.

Containment turned out to be a **policy**, not a shared rule: existing OpenCode
fixtures legitimately reuse a checker from a sibling directory, so the shared
reader only resolves what the campaign states and the DSH reader keeps its own
"checker and seed must live inside the campaign" checks. Enforcing containment
in the contract broke 24 OpenCode tests, which is exactly the kind of assertion
that belongs to one backend.

Deleted from `dsh_campaign.py`: the duplicated objective, accept, manifest and
seed loaders (roughly 60 lines), replaced by thin adapters over the contract.

Verified: 86 OpenCode tests, 109 DSH tests, 23 node tests, compileall clean, and
the prepared campaign still reports `current: true` with the pass receipt.

## Phase 3/6 -- the bridge is an interface again

The directive limits the bridge to resolving, validating, finalizing and
reviewing. Four operations fell outside that and are gone: `status`, `snapshot`,
`recover` and `goal-prompt`. `prepare` now reports the objective, so the launcher
builds the task text itself instead of asking the bridge for it.

Deleted with them, because they existed to serve a Python-driven loop rather
than DSH:

- **counters** (`validations`, `finalizations`, `policy_stops`, elapsed, last
  verdict) and every budget stop: DSH's own round cap is the outer limit, and a
  caller that wants another check simply asks again;
- **the retry policy** (`stateful`/`deterministic`, confirmation rounds,
  no-progress detection): the smoke checker's designed first-round failure now
  works by asking twice, which is what it always meant;
- **the pass-reuse path**: each validate runs the checker;
- **intent records and quarantine**: an interrupted run leaves no record, which
  is already safe, and DSH resumes its own sessions;
- **the journal**: it was tamper *evidence* around a record whose authority is
  the fresh run, so its absence changes no guarantee. The forged-receipt test now
  asserts the thing that matters directly -- a re-hashed `pass` is refused with
  `fresh_validation_failed`.

The record itself collapsed from a receipts directory plus a pointer, counters
and a journal to one `qualification.json` per campaign.

Sizes: `dsh_bridge.py` 1573 -> 1184 lines, `dsh_receipts.py` 274 -> 114, DSH tests
109 -> 93 (obsolete machinery removed, essential behaviours rewritten rather than
dropped: the confirmation round, concurrent validation leaving one record, and the
forged-receipt refusal all still have tests).

Verified: 93 DSH tests, 86 OpenCode tests, 23 node tests, compileall and doc links
clean, and a throwaway end-to-end run still reaches `fail -> pass -> qualified`
with a six-file run root.

Note for the next live run: the existing smoke campaign's run root holds the old
receipt layout from the Phase 0 evidence, so `finalize` needs a fresh `validate`
before it will qualify again. The old records are left in place deliberately --
they are the Phase 0 evidence.

## Phase 4/5 -- review is one operation and one tool

`review-preflight` and `review-clean` are gone. The reviewer worktree belonged to
no operation in particular, which is why it leaked once; it now lives and dies
inside `review-run`, which replaces a stale checkout, runs the reviewer, and
removes it while keeping the report, receipt and invocation outside it.

The plugin exposes `eu4_review_candidate` as its third tool, and only when the
contract sets `require_independent_review`: the launcher reads that flag from
`prepare` before DSH starts and writes it into the overlay, so a campaign without
review never sees the tool.

The bridge is now six operations -- `prepare`, `validate`, `finalize`,
`checker-compat`, `review-run`, `review-verify` -- and 1079 lines from the
original 1573.

The review tests no longer need a preflight step to learn the frozen commit: the
scripted reviewer reads the commit and oracle out of its own task text, which is
also what a real reviewer does. 12 review tests, including that a review leaves no
worktree behind while its report and receipt survive.

Verified: 91 DSH tests, 24 node tests, 86 OpenCode tests, doc links and compileall
clean.

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
