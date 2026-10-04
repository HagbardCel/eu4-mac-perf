# EU IV macOS performance — active plan

## Status

- **PR #30 merged** (Gfx PR1 static RE) and **PR #31 merged** (protocol v2 mesh observer harness) on `main` (`2211e08`).
- **Observer smoke:** `20261004T112503Z` harness **complete**, engagement **failed** (0 `eligible_pair_hits` on Venice); see [validation.md](results/20261004T112503Z-submission-experiment/validation.md). Prior incomplete run: [20261004T111812Z](results/20261004T111812Z-submission-experiment/validation.md).

## PR1 (complete)

Offline — see [analysis/gfx-subrecord-pr1-gonogo.md](analysis/gfx-subrecord-pr1-gonogo.md):

- Classified `SFlushData` (0x50) and mesh draw subrecord (0xe8) layout JSON with CI enforcement
- Draw dependency map + engine elision inventory
- `submission_state_equivalent` / `setup_elision_eligible` / `draw_batch_eligible` in C
- Hook-site contract JSON (insertion before `0x14c81f3`, draw @ `0x14c8404` landmark)

## PR2 (observer validated; buffer-bind ROI negative on Venice)

Mesh hook + capability 1 dylib exercised on `main`. Paused Venice smoke: hook traffic confirmed, **no** buffer-bind predicate hits — mutating candidate not justified for this scene. Full ABABA remains capability 2 only if a future predicate/workload shows ROI.

## Canonical Phase-C (frozen)

```text
python3 benchmark/eu4_frame_model.py verify-evidence \
  results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

Historical `manifest.json` blob remains immutable for that run.

## Out of scope

New Phase-C capture, profiler/evidence expansion, generic GL setter caches. Long roadmap: [analysis/historical-roadmap.md](analysis/historical-roadmap.md).
