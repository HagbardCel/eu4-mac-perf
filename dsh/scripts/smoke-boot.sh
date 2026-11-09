#!/bin/sh
# Prove the eu4-web profile actually boots with the plugin mounted.
#
# Composition-level checks (preflight.sh) can only show that the profile tree
# contains the rows. This boots a real session, waits for the plugin's mount
# line, and exits without sending a model request -- so it costs no tokens.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
export EU4_DSH_SIDECAR=${EU4_DSH_SIDECAR:-$INFRA/dsh/campaigns/offline-smoke.json}
export EU4_DSH_PYTHON=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}

LOG=$(mktemp -t eu4-dsh-boot.XXXXXX)
PROOF=$(mktemp -t eu4-dsh-proof.XXXXXX)
rm -f "$PROOF"
export EU4_DSH_BOOT_PROOF=$PROOF
[ -d "$DSH_HOME/profiles/eu4-web" ] || {
  echo '{"ok":false,"stage":"provision","detail":"run dsh/scripts/provision.sh --init"}'
  exit 1
}

dsh --profile eu4-web --no-open --port 0 >"$LOG" 2>&1 &
PID=$!
cleanup() {
  kill "$PID" 2>/dev/null || true
  wait "$PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

mounted=false
deadline=$(( $(date +%s) + 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if [ -f "$PROOF" ] || grep -q '\[eu4-goal\] mounted' "$LOG" 2>/dev/null; then
    mounted=true
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

fatal=$(grep -iE "fatal|unhandled|Error:" "$LOG" | head -3 || true)
mounted_line=$(grep -m1 '\[eu4-goal\] mounted' "$LOG" | sed 's/"/\\"/g' || true)
proof=$(tr -d '\n' < "$PROOF" 2>/dev/null | sed 's/"/\\"/g' || true)
url=$(grep -oE "http://[^ ]+" "$LOG" | head -1 || true)

if [ "$mounted" = true ]; then
  printf '{"ok":true,"mounted":true,"url":"%s","plugin":"%s","fatal":"%s","proof":%s}\n' \
    "${url:-}" "${mounted_line:-}" "${fatal:-}" "${proof:-null}"
  exit 0
fi

printf '{"ok":false,"mounted":false,"url":"%s","fatal":"%s","log_tail":"%s"}\n' \
  "${url:-}" "${fatal:-}" "$(tail -5 "$LOG" | tr '\n' ' ' | sed 's/"/\\"/g')"
exit 1
