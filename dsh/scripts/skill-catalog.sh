#!/bin/sh
# Print the skill catalog the eu4-web agent actually receives, and fail if the
# reconstruction skill is missing.
#
# Composition-level checks cannot answer this: preflight.sh proves the
# skill-filesystem row is configured, not that the registry view contains the
# skill. This boots a real session with a one-shot probe and sends no model
# request, so it costs no tokens.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
EXPECTED=${EU4_DSH_EXPECT_SKILL:-opengl-metal-reconstruction}

: "${EU4_DSH_HOME:=$HOME/.local/share/eu4-dsh}"
DSH_HOME=$EU4_DSH_HOME
export DSH_HOME
export EU4_DSH_SKILL_ROOT=${EU4_DSH_SKILL_ROOT:-$INFRA/.opencode/skills}
export EU4_DSH_SIDECAR=${EU4_DSH_SIDECAR:-$INFRA/dsh/campaigns/offline-smoke.json}
export EU4_DSH_PYTHON=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}

[ -d "$DSH_HOME/profiles/eu4-web" ] || {
  echo '{"ok":false,"stage":"provision","detail":"run dsh/scripts/provision.sh --init"}'
  exit 1
}

WORK=$(mktemp -d -t eu4-skill-catalog.XXXXXX)
trap 'rm -rf "$WORK"' EXIT INT TERM
OUT=$WORK/catalog.json
export EU4_SKILL_PROBE_OUT=$OUT
cat > "$WORK/patch.yml" <<PATCH
- insert:
    - id: skill-catalog-probe
      name: '$HERE/probe/skill-catalog/lib/index.js'
PATCH

dsh --profile eu4-web --patch "$WORK/patch.yml" --no-open --port 0 >"$WORK/boot.log" 2>&1 &
PID=$!
for _ in $(seq 1 80); do
  [ -f "$OUT" ] && break
  kill -0 "$PID" 2>/dev/null || break
  sleep 0.5
done
kill "$PID" 2>/dev/null || true
wait "$PID" 2>/dev/null || true

[ -f "$OUT" ] || {
  printf '{"ok":false,"stage":"boot","detail":"probe produced no catalog","log":"%s"}\n' \
    "$(tail -3 "$WORK/boot.log" | tr '\n' ' ' | sed 's/"/\\"/g')"
  exit 1
}

"$EU4_DSH_PYTHON" - "$OUT" "$EXPECTED" <<'PY'
import json, sys
path, expected = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
rows = data.get("skills") or []
names = [row.get("name") for row in rows if isinstance(row, dict)]
found = [row for row in rows if isinstance(row, dict) and row.get("name") == expected]
payload = {
    "ok": bool(found),
    "expected": expected,
    "count": len(names),
    "names": names,
}
if found:
    payload["invocation"] = found[0].get("invocation")
    payload["path"] = found[0].get("path")
print(json.dumps(payload, indent=2))
sys.exit(0 if found else 1)
PY
