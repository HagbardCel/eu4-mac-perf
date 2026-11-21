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
CAMPAIGN_STATE=${EU4_CAMPAIGN_STATE:-}
SIDECAR=${1:?usage: run-campaign.sh <sidecar.json>}
shift 2>/dev/null || true

# A campaign carries an operator deadline and a safety mode. Without a state
# file neither exists, so the launch is refused instead of running ungoverned.
if [ -z "$CAMPAIGN_STATE" ]; then
  echo "run-campaign: a sidecar campaign requires EU4_CAMPAIGN_STATE=<state>; the deadline and safety mode live there" >&2
  exit 2
fi

: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
export EU4_DSH_SIDECAR=$(CDPATH= cd -- "$(dirname -- "$SIDECAR")" && pwd)/$(basename -- "$SIDECAR")

# Resolve the same absolute, NumPy-capable interpreter run.sh/preflight use. An
# explicitly configured EU4_DSH_PYTHON is authoritative; a missing dependency
# fails before the campaign starts.
PYTHON_REQUEST=${EU4_DSH_PYTHON:-}
if [ -z "$PYTHON_REQUEST" ]; then
  if [ -x "$INFRA/.venv/bin/python3" ]; then
    PYTHON_REQUEST=$INFRA/.venv/bin/python3
  elif command -v python3.14 >/dev/null 2>&1; then
    PYTHON_REQUEST=$(command -v python3.14)
  else
    PYTHON_REQUEST=$(command -v python3 || true)
  fi
fi
[ -n "$PYTHON_REQUEST" ] || {
  echo "run-campaign: no Python 3 interpreter is available on PATH" >&2
  exit 1
}
EU4_DSH_PYTHON=$(
  cd "$INFRA" && "$PYTHON_REQUEST" -m automation.python_runtime \
    resolve --requested "$PYTHON_REQUEST" --print-path
) || {
  echo "run-campaign: no Python 3 interpreter with NumPy is available" >&2
  echo "run-campaign: install NumPy, or set EU4_DSH_PYTHON to an absolute interpreter that has it" >&2
  (
    cd "$INFRA" && "$PYTHON_REQUEST" -m automation.python_runtime \
      resolve --requested "$PYTHON_REQUEST" --json >&2
  ) || true
  exit 1
}
export EU4_DSH_PYTHON

# Optional campaign safety policy enforced by the action sites, not the model.
# ``--mode`` is fixed when the state is created; there is no replay quota.
: "${CAMPAIGN_MODE:=autonomous_reconstruction}"
if [ -n "${CAMPAIGN_STATE:-}" ]; then
  EU4_CAMPAIGN_STATE=$(CDPATH= cd -- "$(dirname -- "$CAMPAIGN_STATE")" 2>/dev/null && pwd)/$(basename -- "$CAMPAIGN_STATE") || EU4_CAMPAIGN_STATE=$CAMPAIGN_STATE
  export EU4_CAMPAIGN_STATE
  CAMPAIGN_DEADLINE=$("$EU4_DSH_PYTHON" -c 'import json,sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
limits = data.get("limits") or {}
value = limits.get("deadline_seconds", limits.get("wall_clock_seconds"))
print("" if value is None else value)' "$EU4_DSH_SIDECAR" 2>/dev/null || true)
  if [ -n "$CAMPAIGN_DEADLINE" ]; then
    ( cd "$INFRA" && "$EU4_DSH_PYTHON" -m automation.campaign_guard ensure \
        --state "$EU4_CAMPAIGN_STATE" --mode "$CAMPAIGN_MODE" \
        --deadline-seconds "$CAMPAIGN_DEADLINE" ) >&2 || {
      echo "$(basename "$0"): campaign state refused (mode/deadline/marker)" >&2
      exit 1
    }
  else
    ( cd "$INFRA" && "$EU4_DSH_PYTHON" -m automation.campaign_guard ensure \
        --state "$EU4_CAMPAIGN_STATE" --mode "$CAMPAIGN_MODE" ) >&2 || {
      echo "$(basename "$0"): campaign state refused (mode/marker)" >&2
      exit 1
    }
  fi
  echo "$(basename "$0"): campaign state=$EU4_CAMPAIGN_STATE mode=$CAMPAIGN_MODE deadline=${CAMPAIGN_DEADLINE:-none}" >&2
  if [ -n "${EU4_CAMPAIGN_ADMIN_TOKEN:-}" ]; then
    echo "$(basename "$0"): operator token present; issue each live launch with authorize-launch" >&2
  else
    echo "$(basename "$0"): no EU4_CAMPAIGN_ADMIN_TOKEN; live launches are impossible" >&2
  fi
  unset EU4_CAMPAIGN_ADMIN_TOKEN
fi
echo "run-campaign: python=$EU4_DSH_PYTHON" >&2
PLUGIN=$INFRA/dsh/eu4-goal-plugin/lib/index.js
TOOLS_PLUGIN=$INFRA/dsh/eu4-tools-plugin/lib/index.js

[ -f "$PLUGIN" ] || { echo "run-campaign: plugin missing at $PLUGIN" >&2; exit 1; }
[ -f "$TOOLS_PLUGIN" ] || { echo "run-campaign: tools plugin missing at $TOOLS_PLUGIN" >&2; exit 1; }

# dsh 'headless' does not mount the credentials service on 0.2.2-rc.1, so the
# provider can only authenticate from the environment. Fail before spending a turn
# rather than after a MISSING_CREDENTIAL error.
if [ -z "${OPENCODE_GO_API_KEY:-}" ] && [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "run-campaign: no provider key in the environment." >&2
  echo "  The one-shot profile cannot read the stored credentials, so export" >&2
  echo "  OPENCODE_GO_API_KEY (or use the Web profile, which can):" >&2
  echo "    OPENCODE_GO_API_KEY=... ./dsh/scripts/run-campaign.sh <sidecar>" >&2
  exit 1
fi

# Prepare first, so the workspace exists and the trusted anchor is recorded
# before any model turn sees it.
PREPARE=$("$EU4_DSH_PYTHON" -m automation.dsh_bridge prepare --create-anchor --sidecar "$EU4_DSH_SIDECAR" 2>&1) || {
  echo "run-campaign: prepare failed: $PREPARE" >&2
  exit 1
}
WORKSPACE=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["workspace"])')
[ -d "$WORKSPACE" ] || { echo "run-campaign: workspace missing: $WORKSPACE" >&2; exit 1; }

REQUIRE_REVIEW=$(printf '%s' "$PREPARE" |
  "$EU4_DSH_PYTHON" -c 'import json,sys; print(str(bool(json.load(sys.stdin)["require_independent_review"])).lower())')

OBJECTIVE=$(printf '%s' "$PREPARE" |
  "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["objective"])')

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
        requireReview: $REQUIRE_REVIEW
    - id: eu4-tools
      name: '$TOOLS_PLUGIN'
      config:
        python: '$EU4_DSH_PYTHON'
        infra: '$INFRA'
PATCH

# dsh --profile headless answers ONE turn and exits, measured: a goal created in
# that turn keeps no round driver alive afterwards. So the task says so, and the
# model is expected to finish within the turn.
TASK="Create a goal for the objective below, then pursue it using the eu4_* tools.
This run is a single turn: the one-shot runner exits when it ends, so finish the
campaign now rather than leaving work for a round that will not run. Call
create_goal with exactly this objective and no goal-round cap: continue while a
justified experiment remains, and stop only on verified success, a concrete
blocker or the operator deadline.

$OBJECTIVE"

echo "run-campaign: workspace=$WORKSPACE" >&2
echo "run-campaign: sidecar=$EU4_DSH_SIDECAR" >&2
cd "$WORKSPACE"
exec dsh --profile headless --patch "$OVERLAY" "$@" "$TASK"
