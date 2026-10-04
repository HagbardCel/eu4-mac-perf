# EU IV macOS performance — active plan

## Status

- **PR #30 merged** (Gfx PR1 static RE) and **PR #31 merged** (protocol v2 mesh observer harness) on `main` (`2211e08`).
- **Observer smoke:** first Venice attempt `20261004T111812Z` incomplete (10 s phases vs `summarize()` sample floor); see [validation.md](results/20261004T111812Z-submission-experiment/validation.md). Re-run `--engagement-smoke` after harness fix.

## PR1 (complete)

Offline — see [analysis/gfx-subrecord-pr1-gonogo.md](analysis/gfx-subrecord-pr1-gonogo.md):

- Classified `SFlushData` (0x50) and mesh draw subrecord (0xe8) layout JSON with CI enforcement
- Draw dependency map + engine elision inventory
- `submission_state_equivalent` / `setup_elision_eligible` / `draw_batch_eligible` in C
- Hook-site contract JSON (insertion before `0x14c81f3`, draw @ `0x14c8404` landmark)

## PR2 (runtime observer landed; ROI gate pending)

Mesh hook + capability 1 dylib on `main`. Pending: successful `--engagement-smoke` with per-phase `eligible_pair_hits > 0` on B before mutating candidate work; full ABABA remains capability 2 only.

## Canonical Phase-C (frozen)

```text
python3 benchmark/eu4_frame_model.py verify-evidence \
  results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

Historical `manifest.json` blob remains immutable for that run.

## Out of scope

New Phase-C capture, profiler/evidence expansion, generic GL setter caches. Long roadmap: [analysis/historical-roadmap.md](analysis/historical-roadmap.md).
