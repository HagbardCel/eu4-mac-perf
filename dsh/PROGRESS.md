# DSH native-goal infrastructure -- implementation progress

Plan: rev. 3 (approved). Branch `dsh/native-goal`, base `6fa1888`
(`feature/l3-2b-bootstrap`), worktree `~/Projects/MiniProjects/eu4-dsh-infra`.

## Status

| Milestone | State |
|---|---|
| M1 profile, pinning, skill parity, Ghidra probe | done at composition level; one live-session check pending |
| M2 minimal bridge | not started |
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

## Pending / caveats

- The profile and plugin are verified at composition level. A live interactive
  session has not yet been booted, so "session loads
  `opengl-metal-reconstruction`" still needs one human-run check.
- The plugin is plain ESM JavaScript in M1 (no build step, no npm dependency);
  the TypeScript toolchain and real tool surface arrive with M2/M3.
- `~/.dsh` is untouched by the scripts, but the interactive DSH session that runs
  them exports `DSH_HOME=~/.dsh`; `run.sh`/`preflight.sh`/`provision.sh`
  deliberately override it.
