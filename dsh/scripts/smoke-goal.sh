#!/bin/sh
# Print the exact operator steps for the M3 autonomous smoke run.
#
# The goal itself must be created by a human: create_goal requires direct-human
# authority, so no script can perform this step.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
SIDECAR=${1:-$INFRA/dsh/campaigns/offline-smoke.json}

: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
export EU4_DSH_HOME
export EU4_DSH_SKILL_ROOT=$INFRA/.opencode/skills
export EU4_DSH_SIDECAR=$SIDECAR
export EU4_DSH_PYTHON=$INFRA/.venv/bin/python3

echo "1. the autonomous path is one command:"
echo "   $INFRA/dsh/scripts/run-campaign.sh $SIDECAR"
echo "   (it prepares the campaign, binds the workspace and starts headless DSH)"
echo
echo "2. for the interactive web UI instead:  $INFRA/dsh/scripts/run.sh"
echo "   create the session in this checkout, then paste this after /goal:"
echo '---8<---'
"$EU4_DSH_PYTHON" -m automation.dsh_bridge prepare --sidecar "$SIDECAR" |
  "$EU4_DSH_PYTHON" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("objective","") if d.get("ok") else d)'
echo '--->8---'
echo
echo "4. expected: round 1 validates and fails, the driver continues, the model"
echo "   writes ready into marker.txt, round 2 validates and passes, and only"
echo "   eu4_finalize_candidate completes the goal."
