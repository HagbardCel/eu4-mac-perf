# Reproducing the post-PR-#206 qualification

These commands assume the preparation worktree and the checked interpreter
`/opt/homebrew/bin/python3` (Python 3.15.2, NumPy 2.5.5). Authentic capture is
outside routine CI and requires the local EU4 installation.

## 1. Capture (one process, three frames)

```sh
python3 -m automation.m6_frame capture \
  --output .local/m7/phase-c-authentic-v2 \
  --callers 0x103b3c8 --min-draws 1 --occurrence 300 --boundary scene \
  --continuous --shadow --seal --max-frames 3 --max-pending 2 \
  --max-bytes 2147483648 --case-max-bytes 2147483648 --copy-source \
  --continue-on-difference --max-draws 512 --background --duration 240 --timeout 600 \
  --shadow-timeout-ms 120000 --shadow-wait-ms 180000
```

`--max-draws 512` is now the command default; it covers the 325-event title
frame and prevents the former 256-event `draw_cap` failure. `--max-bytes` and
`--case-max-bytes` are the explicit 2 GiB case budget; a 512 MiB budget
publishes an explicit incomplete summary instead of dropping evidence.

## 2. Translate frames 2 and 3

The live coordinator translates frame 1 when it stops. For the full offline
sequence, translate every frame explicitly:

```sh
for i in 1 2 3; do
  python3 -m automation.m6_frame translate \
    --frame .local/m7/phase-c-authentic-v2/frames/frame-$(printf '%06d' "$i") \
    --out .local/m7/phase-c-authentic-v2/shadow/frame-$(printf '%06d' "$i")/shaders \
    --cache-dir .local/m7/phase-c-authentic-v2/shadow/translation-cache
done
```

## 3. Same-backend GL control

```sh
python3 -m automation.agent_tools eu4_capture gl-control \
  --run .local/m7/phase-c-authentic-v2
```

Expected: frame 1 `match` with zero differing pixels; frames 2/3 are
`unavailable` (`unverified_lifecycle_coverage`) because the historical capture
has no lifecycle coverage for copy-only destination 442. The aggregate result is
`incomplete`/`unverified`, not a three-frame match. This is a transfer/readback
control, not a full-frame GL replay.

## 4. GL/Metal sequence

```sh
python3 -m automation.agent_tools eu4_replay sequence \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000001 \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000002 \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000003 \
  --shaders .local/m7/phase-c-authentic-v2/shadow/frame-000001/shaders \
  --out .local/m7/phase-c-authentic-v2/replay-sequence \
  --max-frames 3 --continue-on-difference --require-engine-identity \
  --frame-timeout 300 --timeout 1800
```

Expected: all three frames complete, 325/325 operations, history ok, engine
identity `gap_free`, classification `difference_with_partial_state_coverage`,
first divergence event 284 target 0.

## 5. Portable case

Package the three-frame case from the captured frames, their per-frame
translations and the supervisor's checkpoint requests:

```sh
python3 - <<'PY'
import json
from pathlib import Path
from automation import gfx_replay_case

run = Path(".local/m7/phase-c-authentic-v2").resolve()
frames = [run / "frames" / f"frame-{i:06d}" for i in (1, 2, 3)]
shaders = run / "shadow" / "frame-000001" / "shaders"
frame_shaders = {
    str(frame.resolve()): (run / "shadow" / f"frame-{i:06d}" / "shaders").resolve()
    for i, frame in enumerate(frames, 1)
}
checkpoint_sources = [
    run / "replay-sequence" / "checkpoints" / f"frame-{i:03d}"
    for i in (1, 2, 3)
]
report = json.load(open(
    run / "replay-sequence" / "out-003" / "diagnostic-report.json"))
manifest = gfx_replay_case.package_sequence_diagnosis(
    frames,
    run / "replay-sequence" / "out-003",
    run / "phase-c-authentic-case-v2",
    report,
    case_id="m7-phase-c-authentic-3frame",
    shaders=shaders,
    frame_shaders=frame_shaders,
    checkpoint_sources=checkpoint_sources,
    max_bytes=2 * 1024 ** 3,
)
print(json.dumps(manifest.get("reproduction"), indent=2))
PY
```

The 2 GiB `max_bytes` is the explicit case budget. The default 512 MiB is an
intentional bound and publishes an incomplete summary, not a silent drop.

Then:

```sh
python3 -m automation.agent_tools eu4_replay verify \
  --case .local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2
python3 -m automation.agent_tools eu4_replay case \
  --case .local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2 \
  --replay-out .local/m7/phase-c-case-replay
```

## 6. Controlled later-frame divergence

```sh
python3 -m automation.agent_tools eu4_replay sequence \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000001 \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000002 \
  --frame-dir .local/m7/phase-c-authentic-v2/frames/frame-000003 \
  --shaders .local/m7/phase-c-authentic-v2/shadow/frame-000001/shaders \
  --out .local/m7/phase-c-authentic-v2/replay-fault-frame2 \
  --max-frames 3 --continue-on-difference --require-engine-identity \
  --fault-frame 2 --fault-event 1 --sampler-shift 1 --sampler-shift-min 0
```

Expected: frame 1 unchanged; frame 2 reports `fault_injection.frame=2`,
first divergence `kind=semantic`, `event=1`; the deliberate fault is not
qualified.

## 7. Compact diagnosis

```sh
python3 -m automation.agent_tools eu4_replay diagnose \
  --frame .local/m7/phase-c-authentic-v2/frames/frame-000001 \
  --metal .local/m7/phase-c-authentic-v2/replay-sequence/out-001 \
  --out .local/m7/phase-c-authentic-v2/diagnose-compact \
  --shaders .local/m7/phase-c-authentic-v2/shadow/frame-000001/shaders \
  --require-seal
```

`diagnose-compact/agent-diagnosis.json` is the bounded projection; the full
`diagnostic-report.json` remains authoritative.

## Artifact retrieval

The authentic portable case is **not committed** (1,348,032,569 logical bytes,
3,811 files) and this environment has no external artifact store. The durable
artifacts are:

- the manifest hashes and file inventory in
  [`evidence.json`](evidence.json);
- the local case at
  `.local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2`;
- the reproduction procedure above, which rebuilds the case from the sealed
  frames and translations.

Check the local case before citing it:

```sh
python3 -m automation.agent_tools eu4_replay verify   --case .local/m7/phase-c-authentic-v2/phase-c-authentic-case-v2
```

A missing `.local` case is reported as absent; it is never reconstructed from
this report alone.
