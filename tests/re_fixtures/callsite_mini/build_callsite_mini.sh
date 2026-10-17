#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
clang -arch x86_64 -nostdlib -static -Wl,-e,_start -o callsite_mini callsite_mini.s
echo "Built $ROOT/callsite_mini"
