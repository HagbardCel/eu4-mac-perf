# EU IV graphics-reconstruction foundation — frozen candidate list

This directory contains a source-evidence-driven **explicit list** of functions to extract in a new renderer-focused Ghidra snapshot. Read `IMPLEMENTATION_PLAN.md` for the exact Stage 1 and Stage 2 plan. Start with `renderer-functions.tsv` for review and `renderer-entry-rvas.txt` to feed the exporter.

The list is generated once, offline, from the function inventory and static direct call graph in `eu4-callsite-evidence.zip`. The selection is deliberately conservative about inference: it includes graphics-named families and a subset of direct dependencies, but cannot prove completeness across unknown indirect call targets. Labels are *candidate scope categories*, not recovered architecture.

No expensive regeneration of the original function inventory, callsite audit or game execution is required to use this list. Exports may be incremental/restartable; do not automatically change the selection during execution.

`manifest-summary.json` records input SHA, total counts, subcategories and reproducibility hashes.
