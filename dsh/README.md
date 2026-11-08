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
./dsh/scripts/preflight.sh          # fail-closed checks on the effective profile
./dsh/scripts/run.sh                # boot the profile
```

`DSH_HOME` defaults to `~/.local/share/eu4-dsh`; override it in the environment.

`provision.sh` is idempotent. Only `--init` (or a missing profile directory)
passes `--from-default-profile`, because doing so later would replace the
profile and its local configuration.

## Pinned runtime

[runtime.json](runtime.json) records the verified DSH release, Node and pnpm
versions, the package digests of every required plugin, and the shared skill
path. No absolute machine paths are tracked; `preflight.sh` resolves them and
prints what it used.
