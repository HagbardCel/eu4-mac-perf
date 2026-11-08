# DSH native-goal infrastructure -- implementation progress

Plan: rev. 3 (approved). Branch `dsh/native-goal`, base `6fa1888`
(`feature/l3-2b-bootstrap`), worktree `~/Projects/MiniProjects/eu4-dsh-infra`.

## Status

| Milestone | State |
|---|---|
| M1 profile, pinning, skill parity, Ghidra probe | done at composition level; one live-session check pending |
| M2 minimal bridge, retry policy, classification | done |
| M3 seed-only native goal loop | not started |
| M4 delta snapshot, evidence binding, journal | not started |
| M5 recovery, limits, isolation | not started |
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
