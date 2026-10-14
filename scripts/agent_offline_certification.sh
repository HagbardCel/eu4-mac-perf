#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ALLOW_MODEL_REQUESTS=false
export EU4_AGENT_FIXTURE_MODE=1
export EU4_AGENT_AUTHORIZE_PAID_RUN=0

REPORT="${1:-$ROOT/analysis/graphics-reconstruction/evidence/offline-certification.json}"
mkdir -p "$(dirname "$REPORT")"

python3 -m unittest discover -s tests -p 'test_graphics_agent*.py' -q
python3 -m unittest discover -s tests -p 'test_ghidra*.py' -q
python3 -m unittest discover -s tests_agent -q

uv run python -m reverse_engineering.graphics_agent.pilot_run --fixture-smoke --investigation-id offline-cert

READY=true
python3 - <<PY
import json, time
from pathlib import Path
report = {
  "generated_at": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime()),
  "fixture_smoke": True,
  "allow_model_requests": False,
  "portable_tests": "tests/",
  "agent_tests": "tests_agent/",
  "readiness": "ready" if $([ "$READY" = true ] && echo True || echo False) else "blocked",
}
Path("$REPORT").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
PY

echo "Wrote $REPORT"
