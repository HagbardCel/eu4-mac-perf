#!/bin/sh
# Boot the eu4-web profile, optionally with a campaign or an offline experiment
# workspace already selected.
#
#   ./dsh/scripts/run.sh                       # plain profile, free port
#   ./dsh/scripts/run.sh --campaign <sidecar>  # prepare the campaign, then boot
#   ./dsh/scripts/run.sh --workspace <dir>     # root new sessions in <dir>
#
# With --campaign the sidecar is resolved and prepared *before* DSH starts, and
# the campaign is handed to the plugin through the environment the tracked profile
# already reads (EU4_DSH_SIDECAR / EU4_DSH_REQUIRE_REVIEW), so the running process
# knows which campaign it serves without a second plugin row. The session workspace is still
# chosen in the UI -- DSH does not take it from the process cwd -- so the script
# registers the candidate and makes it the default before booting.
#
# With --workspace an ordinary (non-campaign) session is rooted in an existing
# Git worktree. The directory is canonicalized, registered in DSH's own
# workspace registry and made the default for newly created sessions. When
# EU4_DSH_SKILL_ROOT is not already set it is derived from that worktree, so the
# agent sees this checkout's reconstruction skill rather than another checkout's.
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

CAMPAIGN=''
WORKSPACE=''
while [ $# -gt 0 ]; do
  case "$1" in
    --campaign)
      CAMPAIGN=${2:?--campaign needs a sidecar path}
      shift 2
      ;;
    --campaign=*)
      CAMPAIGN=${1#--campaign=}
      shift
      ;;
    --workspace)
      WORKSPACE=${2:?--workspace needs a directory}
      shift 2
      ;;
    --workspace=*)
      WORKSPACE=${1#--workspace=}
      shift
      ;;
    *)
      break
      ;;
  esac
done

if [ -n "$CAMPAIGN" ] && [ -n "$WORKSPACE" ]; then
  echo "run: use --campaign or --workspace, not both" >&2
  exit 2
fi

if [ -n "$WORKSPACE" ]; then
  [ -d "$WORKSPACE" ] || {
    echo "run: --workspace is not a directory: $WORKSPACE" >&2
    exit 1
  }
  WORKSPACE=$(CDPATH= cd -- "$WORKSPACE" && pwd -P)
  TOP=$(git -C "$WORKSPACE" rev-parse --show-toplevel 2>/dev/null) || {
    echo "run: --workspace is not a Git checkout/worktree: $WORKSPACE" >&2
    exit 1
  }
  TOP=$(CDPATH= cd -- "$TOP" && pwd -P)
  [ "$TOP" = "$WORKSPACE" ] || {
    echo "run: --workspace must be the top level of the Git worktree: $WORKSPACE" >&2
    echo "run: the worktree root is $TOP" >&2
    exit 1
  }
  [ -d "$DSH_HOME/profiles/eu4-web" ] || {
    echo "run: eu4-web profile missing: run dsh/scripts/provision.sh --init" >&2
    exit 1
  }
fi

# A worktree of this repository usually has no .venv (it is ignored), so the
# launcher falls back to the PATH interpreter instead of exporting a path that
# does not exist. Campaign preparation still runs from the checkout that owns
# the trusted automation modules.
if [ -z "${EU4_DSH_PYTHON:-}" ]; then
  if [ -x "$INFRA/.venv/bin/python3" ]; then
    EU4_DSH_PYTHON=$INFRA/.venv/bin/python3
  else
    EU4_DSH_PYTHON=python3
  fi
fi
export EU4_DSH_PYTHON

if [ -n "$WORKSPACE" ]; then
  : "${EU4_DSH_SKILL_ROOT:=$WORKSPACE/.opencode/skills}"
fi
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
if [ -n "$WORKSPACE" ]; then
  EXPECTED_SKILL_ROOT=$(CDPATH= cd -- "$WORKSPACE/.opencode/skills" 2>/dev/null && pwd -P) || {
    echo "run: reconstruction skill directory missing under $WORKSPACE/.opencode/skills" >&2
    exit 1
  }
  ACTUAL_SKILL_ROOT=$(CDPATH= cd -- "$EU4_DSH_SKILL_ROOT" 2>/dev/null && pwd -P) || {
    echo "run: reconstruction skill root does not exist: $EU4_DSH_SKILL_ROOT" >&2
    exit 1
  }
  [ "$ACTUAL_SKILL_ROOT" = "$EXPECTED_SKILL_ROOT" ] || {
    echo "run: EU4_DSH_SKILL_ROOT does not belong to the requested worktree" >&2
    echo "run: expected $EXPECTED_SKILL_ROOT" >&2
    echo "run: found    $ACTUAL_SKILL_ROOT" >&2
    exit 1
  }
  [ -f "$EU4_DSH_SKILL_ROOT/opengl-metal-reconstruction/SKILL.md" ] || {
    echo "run: reconstruction skill missing under $EU4_DSH_SKILL_ROOT" >&2
    exit 1
  }
fi

# Register one directory in DSH's durable workspace registry and make it the
# default for new sessions. This is the same record format the Web UI writes.
# strict=1 (--workspace) creates a missing registry and fails if the result
# cannot be read back; strict=0 preserves the campaign path's historical
# best-effort behavior.
register_workspace() {
  python3 - "$DSH_HOME/storages/workspace.json" "$1" "$2" <<'PYREG'
import datetime, json, os, sys, uuid

reg_path, directory, strict = sys.argv[1], os.path.realpath(sys.argv[2]), sys.argv[3] == "1"


def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(1)


try:
    with open(reg_path, encoding="utf-8") as handle:
        reg = json.load(handle)
except FileNotFoundError:
    if not strict:
        raise SystemExit(0)  # DSH creates its own registry; do not invent one
    os.makedirs(os.path.dirname(reg_path), mode=0o700, exist_ok=True)
    reg = {
        "unit": {"name": "workspace", "version": 2},
        "global": {"initialized": True, "workspaceIds": []},
        "tables": {"workspaces": {}},
    }
except (OSError, ValueError) as error:
    if strict:
        fail(f"run: cannot read workspace registry {reg_path}: {error}")
    raise SystemExit(0)

try:
    workspaces = reg.setdefault("tables", {}).setdefault("workspaces", {})
    wide = reg.setdefault("global", {})
    ids = wide.setdefault("workspaceIds", [])
    found = next(
        (
            wid
            for wid, ws in workspaces.items()
            if os.path.realpath(str(ws.get("path", ""))) == directory
        ),
        None,
    )
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
    # Always point the default at the requested directory, even when it was
    # registered before: an older default would otherwise keep winning.
    wide["initialized"] = True
    wide["defaultWorkspaceId"] = found

    temporary = reg_path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(reg, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, reg_path)

    with open(reg_path, encoding="utf-8") as handle:
        check = json.load(handle)
    default_id = check["global"]["defaultWorkspaceId"]
    entry = check["tables"]["workspaces"][default_id]
    if os.path.realpath(entry["path"]) != directory or default_id not in check["global"]["workspaceIds"]:
        fail(f"run: workspace registration could not be verified for {directory}")
except (OSError, KeyError, ValueError) as error:
    if strict:
        fail(f"run: workspace registration failed for {directory}: {error}")
    raise

print(f"run: default DSH workspace is {directory}", file=sys.stderr)
PYREG
}

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

if [ -n "$WORKSPACE" ]; then
  register_workspace "$WORKSPACE" 1
  cat >&2 <<INFO

Offline reconstruction workspace: $WORKSPACE
  skill root : $EU4_DSH_SKILL_ROOT
Create a NEW session (do not continue the previous one); its cwd will be:
  $WORKSPACE
The workspace registry's default is already set to that directory.
INFO
  # shellcheck disable=SC2086  # PORT_ARGS is a deliberate two-word flag pair
  exec dsh --profile eu4-web "$@" $PORT_ARGS
fi

if [ -z "$CAMPAIGN" ]; then
  # shellcheck disable=SC2086  # PORT_ARGS is a deliberate two-word flag pair
  exec dsh --profile eu4-web "$@" $PORT_ARGS
fi

export EU4_DSH_SIDECAR=$(CDPATH= cd -- "$(dirname -- "$CAMPAIGN")" && pwd)/$(basename -- "$CAMPAIGN")
PLUGIN=$INFRA/dsh/eu4-goal-plugin/lib/index.js
[ -f "$PLUGIN" ] || { echo "run: plugin missing at $PLUGIN" >&2; exit 1; }

PREPARE=$("$EU4_DSH_PYTHON" -m automation.dsh_bridge prepare --create-anchor --sidecar "$EU4_DSH_SIDECAR" 2>&1) || {
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
register_workspace "$SESSION_DIR" 0

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
