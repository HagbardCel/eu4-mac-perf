# DSH native-goal infrastructure

A second, independent agentic migration path for EU4 OpenGL to Metal, built on
DeepSeek Harness's own `/goal` mechanism. It reuses the existing EU4
reconstruction skills, checkers, manifests and evidence standards; it does not
replace or drive the OpenCode runner.

Status: phases 0-7 implemented. Native multi-round continuation and
completion through a fresh checker pass are proven in a supervised Web session;
a live reviewer rejection/remediation loop is still outstanding. See
[PROGRESS.md](PROGRESS.md) for the per-phase record and
[docs/dsh-native-goal.md](../docs/dsh-native-goal.md) for the architecture.

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
./dsh/scripts/run.sh --workspace <dir>  # root new sessions in a Git worktree
./dsh/scripts/smoke-goal.sh [sidecar]  # print the exact /goal objective
./dsh/scripts/run-campaign.sh <sidecar>  # start an autonomous campaign (headless, workspace-bound)
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

## Offline reconstruction experiments

Campaign mode and ordinary reconstruction experiments are different modes. For
an offline experiment launched from one worktree:

```sh
cd /absolute/path/to/eu4-dsh-full-reconstruction
./dsh/scripts/run.sh --workspace "$PWD"
```

`--workspace` also applies
[offline-reconstruction.patch.yml](profile/offline-reconstruction.patch.yml);
no extra `--patch` is needed for the standard workflow.

`--workspace` resolves the path to its canonical worktree root, registers it as
the default workspace for newly created Web sessions, and fails instead of
falling back to another workspace. It also derives `EU4_DSH_SKILL_ROOT` from that
worktree when the variable is unset and refuses a skill root from another
checkout. The overlay disables only the campaign `eu4-goal` plugin; native
`/goal`, `goal-round-driver`, the terminal/filesystem/search tools, the configured
DeepSeek model and `opengl-metal-reconstruction` remain mounted. `--campaign` is
unchanged and still installs the campaign binding.

After the URL is printed, create a **new** session (do not continue an older
smoke-test session) and confirm the session's working directory is the worktree
printed by the launcher. The workspace registry's default is already that path,
so the new-session flow selects it without an operator pick.

## Provider and credentials

The shipped `web` bundle mounts `llm-pi-ai` with no providers and leaves the base
default model (`deepseek-official`/`deepseek-flash`) in place. A dedicated home
has no credentials for that, so the UI asks for an API key. Three things fix it,
and `provision.sh` handles all three:

1. the tracked overlay declares the `opencode-go` provider
   (`apiKeyEnv: OPENCODE_GO_API_KEY`) and an `agent-default-model`;
2. `provision.sh` copies `~/.dsh/.credentials.yaml` into the dedicated home
   (mode `0600`), so the key is shared with the host installation;
3. re-provisioning refreshes it — `--refresh-credentials` forces a re-copy after
   the host rotates a key.

Copying is deliberate: a key saved through the UI then cannot write back into the
host's credential store. If you would rather supply the key at run time, export
`OPENCODE_GO_API_KEY` in the shell that launches `run.sh` and skip the file.

Override the model without editing tracked config:

```sh
EU4_DSH_PROVIDER=opencode-go EU4_DSH_MODEL=deepseek-v4.1-flash ./dsh/scripts/run.sh
```

## Pinned runtime

[runtime.json](runtime.json) records the verified DSH release, Node and pnpm
versions, the package digests of every required plugin, and the shared skill
path. No absolute machine paths are tracked; `preflight.sh` resolves them and
prints what it used.
