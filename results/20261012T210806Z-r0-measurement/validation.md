# R0-M integrated session validation — `20261012T210806Z`

## Scientific conclusion

The first live R0-M Venice session established a reconciled main-thread and process-CPU baseline, verified essential timing and ownership controls, and captured raw stack evidence. Production drawable census could not proceed under the existing readback policy, and automated stack attribution failed on an unsupported `sample` formatting variant. Neither frame staticity nor lossless-skip candidates were evaluated.

Acquisition status remains **`incomplete_gates`** (immutable; see [Future requalification](#future-requalification)).

## Provenance

| Field | Value |
|---|---|
| Acquisition commit | `a9048e19e80eb391feea91f3890940a9307dfd9d` |
| EU IV executable SHA | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Save SHA | `a04bc2e86eeda8025d7a914efb11b18b82dea423631fdcf48c50471578369607` |
| Render settings SHA | `cffdd7b53b8abdf8c3f698b32be208c659bd227fa0965efae78fe15f1474bd84` |
| Fixture | VEN, 1444.11.11, paused (speed 0) |
| Platform | macOS 27.0.1; EU IV **x86_64 (translated)** |
| Resolution | 3456×2234 |
| `thread_cpu_backend` | `libproc` |
| Session ended (UTC) | `2026-10-12T21:11:31.559830+00:00` |

### Runtime source provenance

| Component | Confidence |
|---|---|
| Git HEAD baseline `a9048e1` | Known (recorded above) |
| GOG executable and fixture hashes | Recorded in manifest preflight |
| Runtime Python source identical to HEAD | **Unverified** |
| Stashed local changes saved at PR B branch time | Known (git stash object `b4f0bc437124b9f548e9a80b757478dcd319b3a1`; not a preserved patch) |
| Exact dirty-tree contents at acquisition | **Not established** |

The acquisition was performed with Git HEAD at `a9048e1`, but the runtime source-tree contents were not independently hashed. The stash object indicates possible local modifications to `benchmark/r0_measurement_session.py`, `benchmark/autonomous_runner.py`, `benchmark/r0_probe.py`, and tests (e.g. `probe_env` path resolution, `normalize_output_dir` for relative `results/...` invocations, ready-timeout diagnostics). A successful run shows probe injection worked; that does not prove every modified path had zero effect. **No identified change is currently known to invalidate the CPU arithmetic**; exact source-state reproducibility cannot be established.

## Measurements (epoch A / readiness)

| Metric | Value |
|---|---|
| Readiness swap rate | 57.25 swaps/s |
| Readiness CPU-ms/s | 1065.9 |
| Process CPU-ms/s (epoch A) | 1065.28 |
| Main-thread CPU-ms/s (epoch A) | 773.58 |
| CPU reconciliation residual | 0.14 ms/s (`reconcile_ok`) |
| Scene alignment `mean_rgb_delta` | 9.59 (0 px shift) |

Instrumented paused-Venice baseline—not an uninstrumented EU IV overhead estimate.

`epoch_a_thread_cpu.json` reports `threads_appeared_mid_window: 59` with zero `threads_disappeared_mid_window`. In `cpu_delta.accumulate_thread_deltas()`, the first poll snapshot counts all observed threads as “appeared”; with 59 threads stable across 61 polls and no `ESRCH` skips, this reflects **initial population**, not 59 thread creations during epoch A.

## Evidence interpretation vs raw `acceptance_gates`

`manifest.json` retains the raw `acceptance_gates` object from acquisition. When `drawable_capability.failure_class` is `unsupported_drawable`, several gate booleans are **policy bypasses** for partial-completion logic in `acceptance_gates()`, not evidence that the corresponding experiments passed.

| Evidence | Actual result |
|---|---|
| Epoch A CPU reconciliation | Passed |
| Main-thread mapping | Passed |
| Clock alignment | Passed |
| Flush ownership | Passed |
| Production drawable dimension discovery | Failed |
| Qualified census windows | Not evaluated |
| Positive-control pixel verification | Not evaluated |
| ROI pixel verification | Not evaluated |
| Frame-evolution classification | Not evaluated |
| Automated stack attribution (acquisition) | Failed |
| Automated stack attribution (offline requalification, PR #43) | Passed (`partial_complete` on working copy) |

Structured mirror: [`analysis/evidence/r0-integrated-session-20261012T210806Z.json`](../../analysis/evidence/r0-integrated-session-20261012T210806Z.json).

## Drawable readback

`readback_failure_code: 1` (`UNSUPPORTED_DRAWABLE`) means the production path could not obtain plausible drawable dimensions via `kCGLCPSurfaceBackingSize` (no viewport fallback). This documents a **conservative instrumentation limitation**, not proof that macOS/Rosetta/Apple GL forbids readback.

## Stack analyzer (offline reproduction)

```text
PYTHONPATH=benchmark python3 analysis/tools/r0_stack_analyze.py \
  results/20261012T210806Z-r0-measurement/epoch_b.sample.txt \
  --output /tmp/stack_report.json --main-thread-id 6650541
```

Acquisition tooling (`a9048e1`) failed closed on Rosetta whitespace (see [`r0-stack-analyzer-failure-20261012T210806Z.json`](../../analysis/evidence/r0-stack-analyzer-failure-20261012T210806Z.json)). After PR #43, the same `epoch_b.sample.txt` parses offline on a **working copy** requalification run: 1744 weighted rows, root inclusive 791, `stack_analyzer` gate passes. Derived reports: [`r0-stack-report-20261012T210806Z.json`](../../analysis/evidence/r0-stack-report-20261012T210806Z.json), [`r0-requalification-20261012T210806Z.json`](../../analysis/evidence/r0-requalification-20261012T210806Z.json). Canonical acquisition bytes are unchanged.

## Process CPU reproducibility

Committed: [`epoch_a_process_cpu_samples.json`](epoch_a_process_cpu_samples.json) — 29 powermetrics-derived samples for `game_pid` within the epoch-A libproc monotonic window (`window_start_ns` / `window_end_ns` in `epoch_a_thread_cpu.json`), with clock anchor and selection metadata.

Live acquisition (`a9048e1`) calls `tail.poll()` once, then `_process_cpu_ms_per_s(tail.samples, …)`; retrospective extraction drains the full local stream via `PowerTail.finish()` and can include additional plists inside the same window.

| Subset | Sample count | Mean CPU-ms/s | Residual vs thread sum (1065.135) |
|---|---:|---:|---:|
| Acquisition (inferred first 27) | 27 | 1065.278148 | 0.143325 (matches manifest) |
| Retrospective (all committed) | 29 | 1065.455517 | 0.320694 |

The 27-sample acquisition subset is reconstructed by **exact numerical match** to `manifest.process_cpu_ms_per_s`; the live `PowerTail.samples` snapshot was not preserved separately. Samples 28–29 are marked `in_acquisition_subset: false`; late plist arrival after live `poll()` is the best-supported explanation, not a conclusively established root cause. Both residuals are within the ±50 ms/s reconciliation tolerance. **`manifest.json` is unchanged.**

Local-only (not committed): `powermetrics.pliststream` (~70 MB). Fingerprint: `powermetrics_pliststream_sha256` in `epoch_a_process_cpu_samples.json`.

## Future requalification

This directory is an **immutable acquisition record**. Do **not** run `r0_measurement_session.py --requalify` in-place on the committed capsule (it would rewrite `manifest.json`, status, and gates).

After a stack-parser fix:

1. Copy the run directory to a working path.
2. Ensure `manifest.probe_log_path` resolves to the **copy’s** `r0_probe.log`.
3. Run `--requalify` only on the copy.
4. Commit derived evidence (e.g. `stack_report.json`, updated interpretation) in a **separate** PR linked to this acquisition.

## Artifact SHA-256 (after sanitization / final edit)

| File | SHA-256 |
|---|---|
| `manifest.json` | `a1310398a36b7919cae6850328179ca9266290a4dd02df4742e91b80f5311ad7` |
| `events.jsonl` | `a6470d89d3f32d0fd17d11b5d01e2550275164b3e8d6348c1ab37ba75fd3f81d` |
| `epoch_a_thread_cpu.json` | `efb0d03b0d26cfbd0f6f46a29869f69b36d3755b7cd3ba214225986a6a34ed29` |
| `epoch_a_process_cpu_samples.json` | `286dd065d2b6edbfe6fd171eebe9145816f8eef51abbfd99dcbaf5640573a5d3` |
| `epoch_b.sample.txt` | `5c7cd25e584843a61c1db9dd09440d3ce0d4900b9e61576c0bd9c8e0d18d8518` |
| `r0_probe.log` | `c855a958fc9d1c544428f3ecf77961b193464250818ea309103832a351eeff1f` |
| `r0_control.bin` | `ca592c4483feb74784b7a5b5d9baa53ba74e90650b150f48f74b040e9ab6129e` |
| `game.stdout` / `game.stderr` | (empty) `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

| `analysis/evidence/r0-integrated-session-20261012T210806Z.json` | `17ea2613faa0d03109c30729614c89c4cfb77c6d07b0408864debbb8060a01dc` |

| `analysis/evidence/r0-stack-report-20261012T210806Z.json` | `094b24f29336a1c2c4e7ac42754fd35a5406ab2417bda1bdca7c04e28bcaac44` |
| `analysis/evidence/r0-main-thread-cost-model-20261012T210806Z.json` | `b2f5da6ea09406888fd73d2916294b32e605829b6e418b8bf9fd399e798aaacb` |
| `analysis/evidence/r0-cpu-reconciliation-20261012T210806Z.json` | `1e8bc51e2e683746c372abc4515884e15170da0519201ee7f7e6f675fcb72b3a` |
| `analysis/evidence/r0-requalification-20261012T210806Z.json` | `9bd9fd1f88a9cf470c376228f067a237fbd06ba0cd45255cc6002503bb5451c7` |

Other evidence JSON: [`r0-frame-evolution-20261012T210806Z.json`](../../analysis/evidence/r0-frame-evolution-20261012T210806Z.json), [`r0-stack-analyzer-failure-20261012T210806Z.json`](../../analysis/evidence/r0-stack-analyzer-failure-20261012T210806Z.json) (historical acquisition failure record).
