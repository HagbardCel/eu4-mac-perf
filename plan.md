# EU IV macOS performance — active plan

## Status

- **PR #29 merged** to `main` (`0034def`): Phase-C evidence capsule, hermetic `verify-evidence` in CI, profiler-off ABABA scaffold.
- **Current focus:** Gfx subrecord static RE + predicates (PR1 branch `gfx-flushdata-re-predicate`), then conditional runtime candidate (PR2).

## PR1 (in progress)

Offline only — see [analysis/gfx-subrecord-pr1-gonogo.md](analysis/gfx-subrecord-pr1-gonogo.md):

- Classified `SFlushData` (0x50) and mesh draw subrecord (0xe8) layout JSON with CI enforcement
- Draw dependency map + engine elision inventory
- `submission_state_equivalent` / `setup_elision_eligible` / `draw_batch_eligible` in C
- Hook-site contract JSON (insertion before `0x14c81f3`, draw @ `0x14c8404` landmark)

## PR2 (conditional)

After PR1 **GO A** with observer ROI gate — protocol v2, `candidate_site_entries`, mutating candidate only if removed-work inventory justifies it; full ABABA for capability 2 mutation only.

## Canonical Phase-C (frozen)

```text
python3 benchmark/eu4_frame_model.py verify-evidence \
  results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

Historical `manifest.json` blob remains immutable for that run.

## Out of scope

New Phase-C capture, profiler/evidence expansion, generic GL setter caches. Long roadmap: [analysis/historical-roadmap.md](analysis/historical-roadmap.md).
