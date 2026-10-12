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
| Automated stack attribution | Failed |

Structured mirror: [`analysis/evidence/r0-integrated-session-20261012T210806Z.json`](../../analysis/evidence/r0-integrated-session-20261012T210806Z.json).

## Drawable readback

`readback_failure_code: 1` (`UNSUPPORTED_DRAWABLE`) means the production path could not obtain plausible drawable dimensions via `kCGLCPSurfaceBackingSize` (no viewport fallback). This documents a **conservative instrumentation limitation**, not proof that macOS/Rosetta/Apple GL forbids readback.

## Stack analyzer (offline reproduction)

```text
PYTHONPATH=benchmark python3 analysis/tools/r0_stack_analyze.py \
  results/20261012T210806Z-r0-measurement/epoch_b.sample.txt \
  --output /tmp/stack_report.json --main-thread-id 6650541
```

Fails with:

```text
ValueError: unsupported sample call-graph line: '    +   791 main  (in eu4) + 10903  [0x100757441]'
```

The weighted-row pattern expects the sample count immediately after a branch marker and single space; extra whitespace in the Rosetta-produced line triggers fail-closed behavior. Details: [`analysis/evidence/r0-stack-analyzer-failure-20261012T210806Z.json`](../../analysis/evidence/r0-stack-analyzer-failure-20261012T210806Z.json).

## Process CPU reproducibility

Committed: [`epoch_a_process_cpu_samples.json`](epoch_a_process_cpu_samples.json) — powermetrics-derived samples for `game_pid` within the epoch-A libproc window, with clock anchor and selection metadata.

Local-only (not committed): `powermetrics.pliststream` (~70 MB). Fingerprint: `powermetrics_pliststream_sha256` in `epoch_a_process_cpu_samples.json` (`9654bf7eb198938dc285db76f99b964bd3be77e47b31fe4946bd0f2f730df680`). Recomputed mean from committed samples is ~1065.46 CPU-ms/s (29 points); manifest process mean 1065.28 ms/s because live session used ephemeral monotonic bounds not fully persisted (~0.18 ms/s difference).

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
| `epoch_a_process_cpu_samples.json` | `306ad5850335564c424f240906c2571ff6cd4de488123ee18f21412917a09f67` |
| `epoch_b.sample.txt` | `5c7cd25e584843a61c1db9dd09440d3ce0d4900b9e61576c0bd9c8e0d18d8518` |
| `r0_probe.log` | `c855a958fc9d1c544428f3ecf77961b193464250818ea309103832a351eeff1f` |
| `r0_control.bin` | `ca592c4483feb74784b7a5b5d9baa53ba74e90650b150f48f74b040e9ab6129e` |
| `game.stdout` / `game.stderr` | (empty) `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

Evidence JSON hashes: see [`analysis/evidence/`](../../analysis/evidence/) files listed in [`results/INDEX.md`](../INDEX.md).
