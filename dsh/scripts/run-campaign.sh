#!/bin/sh
# Start one autonomous campaign in a headless DSH session, workspace-bound.
#
# The campaign workspace is selected *before* DSH starts: the session's cwd is the
# campaign workspace, so the sandbox and the candidate agree by construction and
# no environment variable is trusted for that.
#
#   ./dsh/scripts/run-campaign.sh <sidecar.json> [extra dsh args]
#
# The goal is created inside the session by the model under the direct-human
# authority of the task text -- the same path a human typing /goal takes. Nothing
# here schedules or drives goal rounds; the shipped goal-round driver owns that.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
SIDECAR=${1:?usage: run-campaign.sh <sidecar.json>}
shift 2>/dev/null || true

: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
export EU4_DSH_SIDECAR=$(CDPATH= cd -- "$(dirname -- "$SIDECAR")" && pwd)/$(basename -- "$SIDECAR")
export EU4_DSH_PYTHON=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}
PLUGIN=$INFRA/dsh/eu4-goal-plugin/lib/index.js

[ -f "$PLUGIN" ] || { echo "run-campaign: plugin missing at $PLUGIN" >&2; exit 1; }

# Prepare first, so the workspace exists and the trusted anchor is recorded
# before any model turn sees it.
PREPARE=$("$EU4_DSH_PYTHON" -m automation.dsh_bridge prepare --sidecar "$EU4_DSH_SIDECAR" 2>&1) || {
  echo "run-campaign: prepare failed: $PREPARE" >&2
  exit 1
}
WORKSPACE=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["workspace"])')
[ -d "$WORKSPACE" ] || { echo "run-campaign: workspace missing: $WORKSPACE" >&2; exit 1; }

OBJECTIVE=$("$EU4_DSH_PYTHON" -m automation.dsh_bridge goal-prompt --sidecar "$EU4_DSH_SIDECAR" |
  "$EU4_DSH_PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d["objective"])')

# Overlay: mount this checkout's plugin and skill root into the shipped headless
# profile, without creating or modifying a profile of its own.
OVERLAY=$(mktemp -t eu4-headless.XXXXXX.yml)
trap 'rm -f "$OVERLAY"' EXIT INT TERM
cat > "$OVERLAY" <<PATCH
- id: skill-filesystem
  config:
    includeDefaultRoots: false
    customSkillDirs:
      - $EU4_DSH_SKILL_ROOT

- insert:
    - id: eu4-goal
      name: '$PLUGIN'
      config:
        sidecar: $EU4_DSH_SIDECAR
        python: $EU4_DSH_PYTHON
PATCH

TASK="Create a goal for the objective below, then pursue it in this session using the
eu4_* tools. Call create_goal with exactly this objective and max_goal_rounds 6.

$OBJECTIVE"

echo "run-campaign: workspace=$WORKSPACE" >&2
echo "run-campaign: sidecar=$EU4_DSH_SIDECAR" >&2
cd "$WORKSPACE"
exec dsh --profile headless --patch "$OVERLAY" "$@" "$TASK"
