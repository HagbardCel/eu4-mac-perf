#!/usr/bin/env bash
# Extract DrawBorders inner-walk disassembly (GOG 1.37.5) for criterion-6 audit.
set -euo pipefail
EU4="${EU4_PATH:-/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4}"
OUT="${1:-analysis/evidence/drawborders-inner-walk-disasm.txt}"
otool -tv "$EU4" | rg '^00000001010cbe|^00000001010cc[0-3]' > "$OUT"
echo "Wrote $(wc -l < "$OUT") lines to $OUT"
