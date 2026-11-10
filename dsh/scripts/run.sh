#!/bin/sh
# Boot the eu4-web profile, optionally with a campaign already prepared.
#
#   ./dsh/scripts/run.sh                       # plain profile, free port
#   ./dsh/scripts/run.sh --campaign <sidecar>  # prepare the campaign, then boot
#
# With --campaign the sidecar is resolved and prepared *before* DSH starts, and
# the campaign is handed to the plugin through the environment the tracked profile
# already reads (EU4_DSH_SIDECAR / EU4_DSH_REQUIRE_REVIEW), so the running process
# knows which campaign it serves without a second plugin row. The session workspace is still
# chosen in the UI -- DSH does not take it from the process cwd -- so the script
# prints the directory to select. Selecting a directory that does not contain the
# campaign workspace is refused by the plugin rather than failing silently later.
#
# For a one-turn autonomous campaign use run-campaign.sh instead.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)

# The dedicated home is deliberate. An inherited DSH_HOME (the interactive
# session's ~/.dsh) must never be reused: this profile owns its own sessions,
# plugins and settings, and must not touch the user's normal installation.
: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
export EU4_DSH_PYTHON=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}

CAMPAIGN=''
if [ "${1:-}" = "--campaign" ]; then
  CAMPAIGN=${2:?--campaign needs a sidecar path}
  shift 2
fi

# Never collide with a DSH that is already running: the composed web default is
# 127.0.0.1:3080, where the operator's ordinary GUI usually listens. The flag is
# appended rather than prepended, because a launcher option such as --dump-config
# is only recognised before the first app argument.
seen_port=0
launcher_only=0
for argument in "$@"; do
  case "$argument" in
    --port|--port=*) seen_port=1 ;;
    --dump-config|--dump-config-schema|--dump-default-config) launcher_only=1 ;;
  esac
done
PORT_ARGS=''
if [ "$seen_port" -eq 0 ] && [ "$launcher_only" -eq 0 ]; then
  PORT_ARGS="--port ${EU4_DSH_PORT:-0}"
fi

if [ -z "$CAMPAIGN" ]; then
  # shellcheck disable=SC2086  # PORT_ARGS is a deliberate two-word flag pair
  exec dsh --profile eu4-web "$@" $PORT_ARGS
fi

export EU4_DSH_SIDECAR=$(CDPATH= cd -- "$(dirname -- "$CAMPAIGN")" && pwd)/$(basename -- "$CAMPAIGN")
PLUGIN=$INFRA/dsh/eu4-goal-plugin/lib/index.js
[ -f "$PLUGIN" ] || { echo "run: plugin missing at $PLUGIN" >&2; exit 1; }

PREPARE=$("$EU4_DSH_PYTHON" -m automation.dsh_bridge prepare --sidecar "$EU4_DSH_SIDECAR" 2>&1) || {
  echo "run: prepare failed: $PREPARE" >&2
  exit 1
}
WORKSPACE=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["workspace"])')
OBJECTIVE=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["objective"])')
RUN_ROOT=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["run_root"])')
REQUIRE_REVIEW=$(printf '%s' "$PREPARE" | "$EU4_DSH_PYTHON" -c 'import json,sys; print(str(bool(json.load(sys.stdin)["require_independent_review"])).lower())')
export EU4_DSH_REQUIRE_REVIEW=$REQUIRE_REVIEW

case "$WORKSPACE" in
  "$INFRA"/*) SESSION_DIR=$INFRA ;;
  *) SESSION_DIR=$(dirname -- "$WORKSPACE") ;;
esac

cat >&2 <<INFO

Campaign prepared: $EU4_DSH_SIDECAR
  candidate workspace : $WORKSPACE
  qualification state : $RUN_ROOT
Create the session with this workspace:
  $SESSION_DIR
Then send this as one message (the /goal command):
--------------------------------------------------------------------
$OBJECTIVE
--------------------------------------------------------------------

INFO

# shellcheck disable=SC2086  # PORT_ARGS is a deliberate two-word flag pair
# shellcheck disable=SC2086
dsh --profile eu4-web "$@" $PORT_ARGS
