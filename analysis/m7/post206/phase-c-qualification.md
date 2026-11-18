# Phase C — authentic EU4 qualification

Status: authentic same-process capture and differential replay completed. This
document records the evidence, not a merge authorization.

## What was run

One EU4 process was launched on the BetterDisplay `EU4 Test` display and
captured through the existing continuous tracer. No second EU4 process was
launched for the replay.

```sh
# checked interpreter: /opt/homebrew/bin/python3
python3 -m automation.m6_frame capture \
  --output .local/m7/phase-c-authentic-v2 \
  --callers 0x103b3c8 --min-draws 1 --occurrence 300 --boundary scene \
  --continuous --shadow --seal --max-frames 3 --max-pending 2 \
  --max-bytes 2147483648 --case-max-bytes 2147483648 --copy-source \
  --continue-on-difference --max-draws 512 --background --duration 240 --timeout 600 \
  --shadow-timeout-ms 120000 --shadow-wait-ms 180000
```

`--max-draws 512` is required: the first attempt (default 256-event cap)
ended with `draw_cap` and published no sealed frame. That failed launch is
preserved as a known attempt and is not counted as evidence. The successful
capture used one process (pid 84477, session
`7ee7b098-c097-4d03-946a-b7eb05a05236`), completed three sealed frames in
about 247 s wall time, restored the shared `settings.txt` to its original
SHA-256 `62b4902a…dd496`, and left no EU4 process running.

## Capture identity and gap-free monitoring

| Sequence | capture id | present index | scene index | operations | draws | clears | copies | seal (ms) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1 | 615 | 615 | 325 | 315 | 9 | 1 | 40.9 |
| 2 | 2 | 616 | 616 | 325 | 315 | 9 | 1 | 39.0 |
| 3 | 3 | 617 | 617 | 325 | 315 | 9 | 1 | 39.0 |

The engine present indices are consecutive: **615, 616, 617**. The native
sequence and campaign records now carry the same indices as the seals, and
`validate_sequence` rejects a skipped or partially identified frame. The
portable case verification reports `engine_identity.status=gap_free`.

Per-frame readback was 21.7–23.4 ms and finalize 17.2–17.5 ms; the seal cost is
compatible with the earlier 38–41 ms range.

Frame digests (from `frames/sequence.json`):

| Sequence | manifest SHA-256 | event stream SHA-256 | reference SHA-256 |
| --- | --- | --- | --- |
| 1 | `fd7ad829…2c26e1e7` | `c75fc472…b993a77c` | `c69e1a3a…03619b50` |
| 2 | `8f6ff05d…78db63c4` | `3ab4a5a7…a001fc3` | `2396c9e1…e2dc2d3b` |
| 3 | `6e5cce90…32df610d` | `f97b253a…31e1df80` | `2f588b78…90846acb` |

## GL/GL control: zero differing pixels

The copy source was captured with `--copy-source`. The engine's `_GfxResolve`
is a plain `glCopyTexSubImage2D` from the default framebuffer into the
destination texture; Ghidra confirms this:

```text
_GfxResolve:
  glBindTexture(GL_TEXTURE_2D, dst);
  glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, width, height);
  glBindTexture(GL_TEXTURE_2D, 0);
```

The same-backend control now verifies that the first sample of the destination
is still the version produced by the selected copy. It compares the captured
GL default-framebuffer source (BGRA8) with that captured GL destination
texture (RGBA8) under the declared `bgra_to_rgba` channel swap and zero row
flip. The historical capture has lifecycle coverage for destination 442 only
in frame 1; frames 2 and 3 do not, so the control fails closed:

```text
frame 1: match         checked=1930176  mismatched=0
frame 2: unavailable   reason=unverified_lifecycle_coverage
frame 3: unavailable   reason=unverified_lifecycle_coverage
```

The run-level tool result is:

```sh
python3 -m automation.agent_tools eu4_capture gl-control \
  --run .local/m7/phase-c-authentic-v2
```

```json
{"status": "incomplete", "outcome": {"verdict": "unverified",
 "evidence": "unverified", "qualified": false}}
```

The report records the selected copy event (284), the first consumer (286),
and `provenance.first_consumer_event`; a later copy, write, clear, lifecycle
mutation or missing lifecycle record is `unavailable`, not `different`.

**Scope limitation:** this is a same-backend control of the captured copy and
readback path. It does not re-execute the full 325-operation frame through
OpenGL. Frame 1 proves the transfer and capture decoder are exact; frames 2/3
are incomplete evidence, not differences, until copy-only lifecycle coverage
is captured. The authentic differential evidence for those frames remains the
GL/Metal sequence and final-pixel statistics.

## GL/Metal differential

Three frames were replayed through one persistent Metal session without
relaunching EU4:

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

All three frames complete with `operation_coverage=complete` (325/325 encoded,
0 failed), `gpu_execution=ok`, `resource_provenance=verified`, history
`status=ok`, sequence 3, and `state_coverage=partial` for the declared
unsupported states `gl_dither` and `multisample`. Classification is
`difference_with_partial_state_coverage`; renderer equivalence is **not**
claimed.

| Sequence | first authoritative divergence | final mismatched | final max error |
| --- | --- | --- | --- |
| 1 | event 284, target 0 (`fb:0`), `glCopyTexSubImage2D`, producer draw 283 / program 346 | 1,160,152 / 1,930,176 | 78 |
| 2 | event 284, target 0, same copy | 1,163,953 / 1,930,176 | 67 |
| 3 | event 284, target 0, same copy | 1,162,904 / 1,930,176 | 73 |

The first divergence moved from event 286 (the copy destination) in the older
capture to **event 284 (the copy source)** once the source image was captured.
That is the truthful result: the default framebuffer already differs before
the copy executes. The GL/GL control independently shows the copy is exact.

## Portable reproduction

A three-frame v2 case was packaged with the per-frame translations and preserved
checkpoint requests:

```text
case_id: m7-phase-c-authentic-3frame
files: 3,811
logical bytes: 1,348,032,569
case.json SHA-256: b226fbf4c48732c5574cb971b6c63ce821fd9c83f8caa67efd00700b8f5e1f34
sequence.json SHA-256: b3c549ab5fd7b5736e77ad81160346f9e973e01e53e82b063c00e5fe7e962a34
reproduction.status: complete
independently_reproducible: true
```

The 512 MiB default budget on this case is an explicit
`reproduction.status=incomplete` / `reason=artifact_budget` summary with no
replay command; the qualified configuration is the explicit 2 GiB budget above.
The case is outside routine CI (`.local`, gitignored).

Verification and replay, with no EU4 launch:

```text
verify: ok=true, problems=[], 1.93 s
engine_identity: gap_free (615, 616, 617)
replay: campaign complete, target sequence 3, 21.56 s
history: ok, sequence 3, missing=0, stale=0
portable target: 1,162,904 / 1,930,176 mismatched, max 73
```

A second replay of the relocated case produced the same final statistics and
the same history result, demonstrating deterministic reproduction.

## Controlled later-frame divergence

The controlled fault is a sampler-unit shift applied only to frame 2 event 1:

```sh
python3 -m automation.agent_tools eu4_replay sequence \
  ... --max-frames 3 --continue-on-difference \
  --fault-frame 2 --fault-event 1 --sampler-shift 1 --sampler-shift-min 0
```

Result:

- frame 1: unchanged, `fault_injection=null`, first divergence still event 284;
- frame 2: `fault_injection={"frame":2,"event":1,...}`, first divergence
  `kind=semantic`, `event=1`, `operation=draw`;
- the campaign reports `fault_injection.frame=2`; the deliberate contract
  violation is classified `invalid_provenance` and is **not** qualified.

The native frame-scope regression also proves the image effect is confined to
the selected later frame: a fault at frame 2 event 291 changes only frame 2's
Metal image; frame 1 is byte-identical to the unfaulted run. This covers the
pixel-level frame index; the authentic run covers the exact event location.

## Fail-closed checks exercised

- A frame that is not sealed (`draw_cap`) stops the campaign and publishes no
  qualified result.
- A missing/incomplete checkpoint request is `incomplete_diagnostics`, not a
  difference.
- A non-consecutive engine index is `engine_frame_gap`.
- A partial engine identity is `engine_identity_partial`.
- A missing copy source/destination is a `ControlError`; a one-pixel change
  makes the GL/GL control `different`, never `match`.
- Unsupported `gl_dither`/`multisample` keeps `state_coverage=partial` and
  prevents renderer equivalence.

## Evidence bundle

Machine-readable values and hashes are in
[`evidence.json`](evidence.json). The raw artifacts remain under
`.local/m7/phase-c-authentic-v2` (gitignored) and are **not** substituted by
this report.
