#!/bin/sh
# Boot the eu4-web profile, or print the exact /goal invocation for a campaign.
#
#   ./dsh/scripts/run.sh                 # boot the web profile
#   ./dsh/scripts/run.sh --goal <sidecar>  # print the objective to paste after /goal
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
: "${EU4_DSH_PYTHON:=$INFRA/.venv/bin/python3}"
export EU4_DSH_PYTHON
if [ -z "${EU4_DSH_SIDECAR:-}" ] && [ "${1:-}" = "--goal" ] && [ -n "${2:-}" ]; then
  EU4_DSH_SIDECAR=$(CDPATH= cd -- "$(dirname -- "$2")" && pwd)/$(basename -- "$2")
  export EU4_DSH_SIDECAR
fi

if [ "${1:-}" = "--goal" ]; then
  [ -n "${2:-}" ] || { echo "usage: run.sh --goal <sidecar.json>" >&2; exit 2; }
  exec "$EU4_DSH_PYTHON" -m automation.dsh_bridge goal-prompt --sidecar "$EU4_DSH_SIDECAR"
fi

exec dsh --profile eu4-web "$@"
