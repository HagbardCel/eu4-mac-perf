#!/bin/sh
# Provide a DSH-private read-only replica of the shared Ghidra project.
#
# analyzeHeadless takes an exclusive project lock even with -readOnly, so a DSH
# query and an OpenCode query (or an open Ghidra GUI) cannot share one project.
# A replica removes the contention without requiring every participant to adopt
# a lock protocol, which the pinned OpenCode worktrees cannot be made to do.
#
#   ./dsh/scripts/ghidra-replica.sh --check     # report state (default)
#   ./dsh/scripts/ghidra-replica.sh --create    # copy the project once
#   ./dsh/scripts/ghidra-replica.sh --force     # refresh an existing replica
#
# Then run DSH queries with:
#   EU4_RE_EXPORT=<replica> the-query
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA=$(CDPATH= cd -- "$HERE/../.." && pwd)
PY=${EU4_DSH_PYTHON:-$INFRA/.venv/bin/python3}

MODE=check
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --create) MODE=create ;;
    --force) MODE=force ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "ghidra-replica: unknown option: $arg" >&2; exit 2 ;;
  esac
done

SHA=$("$PY" -c 'import sys;sys.path.insert(0,"'"$INFRA"'");from reverse_engineering.paths import binary_sha256;print(binary_sha256())')
META=${EU4_GHIDRA_META:-$INFRA/.local/eu4/$SHA/ghidra_project.json}
[ -f "$META" ] || { echo "ghidra-replica: no ghidra_project.json at $META" >&2; exit 1; }

# Keep the documented layout rule: Ghidra rejects path components starting
# with '.', so the replica must not live under ~/.local.
REPLICA=${EU4_GHIDRA_REPLICA:-$HOME/GhidraEU4/$SHA-dsh}
PROJECT=$REPLICA/ghidra-project

report() {
  printf '{"replica":"%s","project":"%s","exists":%s,"usable":%s}\n' \
    "$REPLICA" "$PROJECT" "$1" "$2"
}

if [ "$MODE" = check ]; then
  if [ -d "$PROJECT" ]; then
    if [ -f "$REPLICA/ghidra_project.json" ]; then
      report true true
    else
      report true false
    fi
  else
    report false false
  fi
  exit 0
fi

if [ -d "$PROJECT" ] && [ "$MODE" = create ]; then
  echo "ghidra-replica: replica already exists (use --force to refresh)" >&2
  report true true
  exit 0
fi
if [ -d "$PROJECT" ]; then
  echo "ghidra-replica: removing existing replica at $PROJECT" >&2
  rm -rf "$PROJECT"
fi

SOURCE=$("$PY" -c "import json;print(json.load(open('$META'))['project_dir'])")
[ -d "$SOURCE" ] || { echo "ghidra-replica: source project missing: $SOURCE" >&2; exit 1; }

mkdir -p "$REPLICA"
echo "ghidra-replica: copying $SOURCE -> $PROJECT (this is a large copy)" >&2
cp -a "$SOURCE" "$PROJECT"

# The replica's metadata must point at the replica, or queries would silently
# reopen the shared project and reintroduce the lock contention.
"$PY" - "$META" "$REPLICA/ghidra_project.json" "$PROJECT" <<'PY'
import json, sys
source, target, project = sys.argv[1:4]
meta = json.load(open(source, encoding="utf-8"))
meta["project_dir"] = project
meta["replica_of"] = json.load(open(source, encoding="utf-8")).get("project_dir")
json.dump(meta, open(target, "w", encoding="utf-8"), indent=2, sort_keys=True)
print("wrote", target, file=sys.stderr)
PY

printf '{"replica":"%s","project":"%s","exists":true,"usable":true}\n' "$REPLICA" "$PROJECT"
echo "ghidra-replica: use EU4_RE_EXPORT=$REPLICA for DSH queries" >&2
