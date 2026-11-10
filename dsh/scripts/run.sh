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

# The session workspace is the candidate itself, not the checkout. DSH confines
# writes to the session workspace, so a session rooted at the checkout would let
# the agent edit the trusted checker, the shared acceptance code and the sidecars.
# Rooting it at the candidate keeps every trusted path outside the sandbox.
SESSION_DIR=$WORKSPACE

# A DSH Web session takes its working directory from the workspace registry, not
# from the process cwd. Registering the candidate and making it the default means
# a new session lands on it without the operator picking anything; it is what the
# UI's "open folder" does.
"$EU4_DSH_PYTHON" - "$DSH_HOME/storages/workspace.json" "$SESSION_DIR" <<'PYREG'
import datetime, json, os, sys, uuid

reg_path, directory = sys.argv[1], sys.argv[2]
if not os.path.isdir(directory):
    raise SystemExit(0)
try:
    reg = json.load(open(reg_path))
except (OSError, ValueError):
    raise SystemExit(0)  # DSH creates its own registry; do not invent one

workspaces = reg.setdefault("tables", {}).setdefault("workspaces", {})
wide = reg.setdefault("global", {})
ids = wide.setdefault("workspaceIds", [])

found = next((wid for wid, ws in workspaces.items() if ws.get("path") == directory), None)
if found is None:
    found = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    workspaces[found] = {
        "path": directory,
        "title": os.path.basename(directory.rstrip("/")) or directory,
        "sessionIds": [],
        "createdAt": now,
        "updatedAt": now,
    }
    ids.append(found)
wide["defaultWorkspaceId"] = found

temporary = reg_path + ".tmp"
with open(temporary, "w") as handle:
    json.dump(reg, handle, indent=2)
    handle.write("\n")
os.replace(temporary, reg_path)
print(f"run: default DSH workspace is {directory}", file=sys.stderr)
PYREG


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
