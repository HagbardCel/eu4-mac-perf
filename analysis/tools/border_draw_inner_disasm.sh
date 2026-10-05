#!/usr/bin/env bash
# Extract full DrawBorders disassembly (GOG EU IV 1.37.5) for criterion-6 CFG audit.
set -euo pipefail
PINNED_SHA256="b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
DRAWBORDERS_START="0x1010cbb00"
DRAWBORDERS_END="0x1010cc456"
EU4="${EU4_PATH:-/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4}"
OUT="${1:-analysis/evidence/drawborders-drawborders-fn-disasm.txt}"
if [[ ! -f "$EU4" ]]; then
  echo "EU4 binary not found: $EU4" >&2
  exit 1
fi
SHA256="$(shasum -a 256 "$EU4" | awk '{print $1}')"
if [[ "$SHA256" != "$PINNED_SHA256" ]]; then
  echo "SHA256 mismatch (expected $PINNED_SHA256, got $SHA256)" >&2
  exit 1
fi
export EU4 OUT DRAWBORDERS_START DRAWBORDERS_END PINNED_SHA256
python3 <<'PY'
import os
import re
import subprocess

eu4 = os.environ["EU4"]
out_path = os.environ["OUT"]
start = int(os.environ["DRAWBORDERS_START"], 16)
end = int(os.environ["DRAWBORDERS_END"], 16)
sha = os.environ["PINNED_SHA256"]
proc = subprocess.run(["otool", "-tv", eu4], capture_output=True, text=True, check=True)
lines: list[str] = []
for line in proc.stdout.splitlines():
    m = re.match(r"^([0-9a-f]+)\t", line)
    if not m:
        continue
    addr = int(m.group(1), 16)
    if start <= addr < end:
        lines.append(line)
if not lines:
    raise SystemExit("no instructions extracted for DrawBorders range")
header = [
    f"# DrawBorders {os.environ['DRAWBORDERS_START']}..{os.environ['DRAWBORDERS_END']} (exclusive end)",
    f"# binary_sha256={sha}",
]
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(header) + "\n")
    f.write("\n".join(lines) + "\n")
print(f"Wrote {len(lines)} instructions to {out_path}")
PY
