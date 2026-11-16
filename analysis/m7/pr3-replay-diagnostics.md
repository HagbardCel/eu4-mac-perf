# M7 follow-up — native replay failure diagnostics

Status: implementation complete; focused native/Python suites green. One
follow-up PR targets `main` after PR #199 (`67bf7a1`) and PR #200 (`dac2ea9`).
No EU4 launch is involved: the change does not alter in-process callback
behaviour, only what a failure records.

## Problem

The replay core had two diagnostic gaps:

1. `run_replay()` returned early for input verification, manifest/event-stream
   loading, shader loading, frame structure, seed upload, isolated preparation,
   event-boundary waits, attachment initialization and pre-image readback. Most
   of those exits wrote only to stderr and left `eu4_metal_replay_result.error`
   empty, so the C ABI caller reported `replay failed (code N)`. The shadow
   report then had to classify every replay failure as `stage=metal_replay`.
2. Nested JSON was trusted after parsing. Valid JSON with an object where an
   array or dictionary was expected could reach an Objective-C subscript and
   raise. Several missing-error paths in sealed-input verification returned
   `NO` with no `NSError`; those paths passed a possibly null
   `localizedDescription.UTF8String` to `%s`.

## Failure model

The C ABI stays at `EU4_METAL_REPLAY_ABI_VERSION == 2`. No struct field changed.
The existing `error[512]` channel now always starts with a stable stage token:

```
<stage> (operation <N>): <underlying error>
<stage>: <underlying error>              (no operation)
```

`replay.json` carries the same information structurally:

```json
{
  "status": "failed",
  "stage": "event_boundary",
  "error": "event_boundary (operation 1): event boundary exceeded the replay deadline",
  "failure": {
    "stage": "event_boundary",
    "operation": 1,
    "event": 1,
    "kind": "draw",
    "caller": "0x...",
    "error": "event boundary exceeded the replay deadline",
    "timed_out": true,
    "command_status": 2
  },
  "failures": [ ... ],
  "events": 326,
  "encoded": 1,
  "failed": 1,
  "timed_out": true,
  "command_status": 2,
  "duration_ns": 55685000,
  "readback": false
}
```

Stages used by the core:

| Stage | Failure |
| --- | --- |
| `sealed_input_verification` | event-stream/manifest/reference/shader digest, size or blob reference mismatch |
| `manifest_load` | missing/unreadable/non-object `frame-trace.json` |
| `event_stream_load` | missing/unreadable/non-UTF-8/malformed `frame-events.jsonl` |
| `input_validation` | valid JSON with an unusable event/manifest shape |
| `shader_load` | missing/invalid `shaders.json` |
| `checkpoint_load` | missing/invalid checkpoint document |
| `attachment_initialization` | default/isolated/attachment target allocation or initialization |
| `frame_structure` | frame has no draw events |
| `seed_upload` | captured frame seed missing or invalid |
| `isolated_preparation` | `--only-event` outside the frame or isolated target allocation |
| `event_boundary` | per-event command-buffer wait |
| `pre_image_upload` | isolated pre-image blob missing or wrong size |
| `pre_image_readback` | isolated pre-image capture failed |
| `event_encode` | clear/draw/resolve encode failure (unsupported state, shader stage, missing blob, command failure) |
| `post_draw_completion` | post-draw command-buffer wait |
| `post_image_readback` | isolated post-image capture failed |
| `checkpoint_readback` | requested checkpoint target missing or readback/write failed |
| `frame_completion` | final command-buffer wait or injected command failure |
| `frame_readback` | final readback wait, allocation, or `metal-frame.bgra` write |
| `artifact_write` | `replay-provenance.jsonl` or `replay.json` write |
| `internal_exception` | defensive catch for an unexpected Objective-C exception |

`timed_out` is still set only by the shared monotonic-deadline wait, so a
deadline expiry stays distinct from an ordinary command-buffer failure. Event
failures record the operation index and caller; frame-level failures use the
event count as the operation, matching the existing failure list convention.

## Malformed input

`run_replay()` now validates the manifest shape, every event object and its
nested `target`, `state`, `program`, `draw`, `indices`, texture, attribute,
resolve-rect and destination-offset fields, the shader report and the checkpoint
document before execution. A type mismatch becomes `input_validation` with the
event index and field name. A final `@try/@catch` converts any remaining
unexpected exception into a structured `internal_exception` result instead of a
process crash. Every validation/verification failure path now populates an
`NSError`, and all `%s` diagnostics use a null-safe UTF-8 helper.

## Propagation

`benchmark/eu4_gfx_shadow_bridge.mm` reads the structured `failure` object from
`replay.json` (falling back to the ABI error prefix) and writes the native stage
to `shadow-report.json` at both top level and under `metal`. It no longer
collapses every replay failure to `stage=metal_replay`. Normal replay,
shader-receipt/session-creation failures and Objective-C exceptions all build
from one identity base (`shadow_report_base`), so every report carries the same
`capture_frame_id`, `pid`, `session_id`, `capture_dir`, reference/manifest
digests and operation count. A successful report has `status=ok`,
`stage=complete`, an empty `error` and an empty `failure` object; no fallback
failure text is synthesized on success. A caught exception keeps its original
description under `stage=internal_exception`.

`automation/m6_frame.py` uses one `shadow_failure_detail()` helper for
`coordinator.json` and `capture-report.json`, so the native stage and underlying
error survive unchanged. Failed coordinator runs now write `status=failed`
instead of leaving the default `incomplete`. The final capture summary keeps
the full shadow report, `shadow_stage`, `shadow_failure` and a specific
`shadow_error`.

Success is unchanged: the canonical title frame still replays 326/326 events,
0 failed, command status 4, readback ok, Metal frame SHA-256
`ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15`; successful
`replay.json` has no `failure` or `error` field.

## Verification

Focused, native-only (macOS arm64):

```sh
python3 -m automation.test_runner run \
  --module tests.test_m7_metal_replay --module tests.test_resolve_replay
```

The extended `tests/test_m7_metal_replay.py` covers:

- missing manifest, missing events, malformed JSON, non-object events, invalid
  UTF-8, no-draw frames and missing shaders (`manifest_load`,
  `event_stream_load`, `input_validation`, `frame_structure`, `shader_load`);
- type-confused event fields (target/program/state/attributes/textures/resolve
  rectangle) as structured failures, never crashes;
- canonical-fixture failures at `event_encode`, `seed_upload`,
  `pre_image_upload` and `checkpoint_readback`, including operation indices;
- the existing event-boundary timeout, final-command timeout, readback timeout
  and ordinary command failure, now also asserting the distinct stages
  `event_boundary`, `frame_completion` and `frame_readback`;
- a native shadow-report host exercising the actual bridge report builders for
  success, structured replay failure, shader-receipt failure,
  session-creation failure and a controlled exception; it asserts identical
  sealed identity fields on every path and an empty success diagnostic;
- mocked coordinator runs proving failure/provenance separation
  (`provenance_ok`, `execution_failed`) and that successful reports keep
  `status=ok`, `stage=complete` and no synthesized error;
- first-failure coverage: an earlier `event_encode` failure survives a later
  boundary timeout and a later artifact-write failure; timeouts retain their
  operation index and classification;
- stale-artifact coverage: a failed replay in a reused output directory does
  not advertise the earlier replay's `metal-frame.bgra`,
  `replay-provenance.jsonl` or result path;
- canonical pixel parity, reused sessions, scene-75 known divergence,
  sealed blob verification and CLI/C ABI equivalence.

The native ABI host gained `--only-event`, `--stop-after`, `--checkpoints`,
`--seed-marked`, `--capture-pre`, `--capture-post`, `--depth-from-prefix` and
structured `stage`/`failure` output; it still exercises the v2 C ABI only.

Focused final evidence: run `20261116T092007Z-fc0178fd`, 124 tests across the
required replay, resolve, seal, m6-frame, checkpoint and output-safety modules
— passed. `tests/test_m7_metal_replay.py` alone is 20 tests
(`20261116T091951Z-c35a9c0b`), including the native shadow-report host, the
success/failure/exception semantics, first-failure and stale-artifact cases. A
base-worktree comparison at `dac2ea92` (`20261116T080832Z-6dd0c6ce`) shows the
one `test_agent_tools` failure found during a broader run is pre-existing, not
introduced here.

Portable CI was executed on the tracked base plus this change in a Linux
Python 3.14 container as a non-root CI user with clang, NumPy 2.5.4 and Pillow
12.3.0: `Ran 3098 tests in 124.400s`, `OK (skipped=292)`, exit 0. Skipped tests
are platform-specific native/macOS or absent-evidence cases, matching the
portable GitHub Actions selection. Structured evidence is recorded in
[pr3-replay-diagnostics-evidence.json](pr3-replay-diagnostics-evidence.json).

## Non-goals

No shader, render-state, dithering, multisampling, continuous rendering or
cross-process comparison behaviour changed. This PR does not reopen #199 or
#200 and does not require a new authentic EU4 launch.
