# DSH native-goal infrastructure

A second, independent agentic migration path for EU4 OpenGL to Metal, built on
DeepSeek Harness's own `/goal` mechanism. It reuses the existing EU4
reconstruction skills, checkers, manifests and evidence standards; it does not
replace or drive the OpenCode runner.

Status: M1 (profile, pinned runtime, skill parity). See
[docs/dsh-native-goal.md](../docs/dsh-native-goal.md) for the architecture and
the milestone record.

## Profile

`eu4-web` is derived from the shipped `web` template and lives in its own
`DSH_HOME`, so it never mutates `~/.dsh`, another profile, or an existing
session. The tracked overlay is [cordis.patch.yml](profile/eu4-web/cordis.patch.yml);
`provision.sh` synchronizes it into the dedicated home.

Two deliberate deviations from the shipped web profile:

- `skill-filesystem` is re-enabled on the host plane with `customSkillDirs`
  pointing at this repository's `.opencode/skills`, so the reconstruction
  methodology is shared rather than duplicated.
- `eu4-goal` mounts the out-of-tree plugin in
  [eu4-goal-plugin/](eu4-goal-plugin/).

## Scripts

```sh
./dsh/scripts/provision.sh --init   # one time: create and populate the profile
./dsh/scripts/preflight.sh          # fail-closed checks on the composed profile
./dsh/scripts/smoke-boot.sh         # boot a session and prove the plugin mounted
./dsh/scripts/skill-catalog.sh      # prove the agent's catalog has the skill
./dsh/scripts/run.sh                # boot the profile on a free port
./dsh/scripts/smoke-goal.sh [sidecar]  # print the exact /goal objective
./dsh/scripts/ghidra-probe.sh       # reproduce the shared-project lock failure
./dsh/scripts/ghidra-replica.sh --check|--create  # private Ghidra replica
```

The scripts set `DSH_HOME` themselves from `EU4_DSH_HOME` (default
`~/.local/share/eu4-dsh`) and **deliberately ignore an inherited `DSH_HOME`**, so
this profile can never reuse or mutate the interactive `~/.dsh` install. Seven
tokens are opt-in: `EU4_DSH_SANDBOX_MODE` and `EU4_DSH_WORKSPACE` are recorded by
the bridge when set, and `EU4_DSH_BOOT_PROOF` makes the plugin write its mount
receipt.

`provision.sh` is idempotent. Only `--init` (or a missing profile directory)
passes `--from-default-profile`, because doing so later would replace the
profile and its local configuration.

## Pinned runtime

[runtime.json](runtime.json) records the verified DSH release, Node and pnpm
versions, the package digests of every required plugin, and the shared skill
path. No absolute machine paths are tracked; `preflight.sh` resolves them and
prints what it used.
