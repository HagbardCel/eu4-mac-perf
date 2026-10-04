# EU IV macOS performance — active plan (PR #29)

## Current focus

Close measurement and evidence infrastructure on PR #29 without a new live intrusive capture. Historical Phase-C `results/20261003T210202Z-intrusive-diagnostic/manifest.json` stays byte-for-byte unchanged during canonical reseal.

## Done in this remediation

- **Power lifecycle:** `PowerTail.finish()` drains pliststream after powermetrics exits; intrusive post-capture seals after derived analysis.
- **Submission ABABA:** mmap `submission_control.bin` + `libeu4_submission_experiment.dylib`; per-phase counter deltas; screenshot after settle, before measurement window; bracket-normalized validation and hermetic scene reference (`benchmark/submission_scene_reference.json`).
- **Evidence:** `capsule_profile` on seal; `verify-evidence` CLI; `powermetrics_stderr` in `raw_evidence.json`; salvage reports Spearman/Kendall and count-complete S/U ranks (reporting only).
- **Docs:** Long-form roadmap moved to [`analysis/historical-roadmap.md`](analysis/historical-roadmap.md).

## Canonical Phase-C closure

```text
python3 benchmark/eu4_frame_model.py intrusive-salvage results/20261003T210202Z-intrusive-diagnostic
python3 benchmark/eu4_frame_model.py seal-evidence results/20261003T210202Z-intrusive-diagnostic \
  --profile intrusive_complete_v1 --no-manifest-write
python3 benchmark/eu4_frame_model.py verify-evidence results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1
```

CI runs the same hermetic verify on every push/PR (see `.github/workflows/profiler.yml`).

## Next PR (out of scope)

Static `_FlushData` / `_TransparentFlushData` / `0xe8` RE → one conservative candidate → offline validation → one profiler-off ABABA.
