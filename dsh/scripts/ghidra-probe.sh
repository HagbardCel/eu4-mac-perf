#!/bin/sh
# Reproducible concurrency probe for the shared Ghidra project.
#
# analyzeHeadless takes an exclusive project lock even with -readOnly, so two
# concurrent queries abort. This probe records that outcome instead of assuming
# it, and is the evidence behind the read-only-replica decision.
#
#   GHIDRA_INSTALL=... JAVA_HOME=... ./dsh/scripts/ghidra-probe.sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
PY=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}
META=${EU4_GHIDRA_META:-$INFRA/.local/eu4/$(./"$PY" -c 'import sys;sys.path.insert(0,"'"$INFRA"'");from reverse_engineering.paths import binary_sha256;print(binary_sha256())')/ghidra_project.json}

[ -f "$META" ] || { echo "probe: no ghidra_project.json at $META (set EU4_GHIDRA_META)" >&2; exit 1; }
[ -n "${GHIDRA_INSTALL:-}" ] || { echo "probe: GHIDRA_INSTALL is not set" >&2; exit 1; }
GH=$GHIDRA_INSTALL/support/analyzeHeadless
[ -x "$GH" ] || { echo "probe: analyzeHeadless not executable at $GH" >&2; exit 1; }

PD=$("$PY" -c "import json;print(json.load(open('$META'))['project_dir'])")
PN=$("$PY" -c "import json;print(json.load(open('$META'))['project_name'])")
PR=$("$PY" -c "import json;print(json.load(open('$META'))['program_name'])")
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

"$GH" "$PD" "$PN" -process "$PR" -noanalysis -readOnly >"$TMP/1.out" 2>&1 &
P1=$!
"$GH" "$PD" "$PN" -process "$PR" -noanalysis -readOnly >"$TMP/2.out" 2>&1 &
P2=$!
if wait $P1; then E1=0; else E1=$?; fi
if wait $P2; then E2=0; else E2=$?; fi

if [ "$E1" -eq 0 ] && [ "$E2" -eq 0 ]; then
  echo '{"concurrent_readonly_safe":true}'
  exit 0
fi

LOCK=$(grep -ho 'LockException: [^"]*' "$TMP"/1.out "$TMP"/2.out | head -1 || true)
printf '{"concurrent_readonly_safe":false,"exit":["%s","%s"],"detail":"%s"}\n' \
  "$E1" "$E2" "${LOCK:-unknown}"
