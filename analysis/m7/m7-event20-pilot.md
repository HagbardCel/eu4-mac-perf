# M7 event-20 pilot record

Date: 21 November 2026 (Europe/Berlin). Operator: Fabian.
Purpose: execute a bounded autonomous original-EU4-OpenGL versus Metal exact
prefix through event 20. Scientific integrity, not a nominal milestone, decides
the outcome.

## Revisions

| Item | Revision |
| --- | --- |
| Repository baseline (`main`, verified against `origin/main`) | `3e725d3c693797853b1265f870dc16598d8b598c` |
| Exact-prefix implementation | `29fc2318c1023abcdedd31f71a46ce2559c2b40d` |
| Packed depth/stencil and clear-flag fix | `7d6686ef` |
| Isolated unsupported auxiliary clear policy | `030cf0a5b5fe6bb50c2db08d318a516e3b1b8de1` |
| Prefix blob-budget fix | `c06e3ea6c37336606b0e95d2d4545a2f19fb6d42` |
| Stage-3 candidate worktree HEAD | `c06e3ea6c37336606b0e95d2d4545a2f19fb6d42` |
| Trusted checker | `campaigns/eu4-m7-event20/accept.py` from the infrastructure checkout, importing `automation` from that checkout, never from the candidate |

The checker was invoked against the candidate workspace; the candidate could
edit `benchmark/**`, `analysis/m7/**`, `tests/**`, `progress.md` and `.local/**`.
It could not edit the campaign, checker or trusted `automation` modules.

## Environment

- Apple M5 Max, macOS, Apple OpenGL 2.1 Metal 91.7.
- EU4 GOG executable SHA-256
  `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.
- BetterDisplay virtual screen `EU4 Test` (display id 6, 1920×1200, not main,
  `hidpi=false`); game window 1728×1117 at `(1755,27)`.
- Python 3.14.10 (`/opt/homebrew/opt/python@3.14/bin/python3.14`, NumPy 2.5.5).
- GOG `settings.txt` before and after every launch:
  `62b4902a3a999ad3e623dd92800c3406359b44e60497491393e6b191836dd496`.
- No EU4 process was running at the start; none remains at the end.
- The physical main display was unchanged; the background virtual screen was
  used for every attempt.

## Budgets and operator decisions

Initial pilot budgets: 1 EU4 launch, 1 newly sealed frame, 1 shadow execution,
2 offline replays, 1 correction evaluation, 1800 s wall clock, 0 automatic
merges. The campaign guard enforced each budget at the action site; the
host-wide one-use launch ledger was used for every process.

Two additional launches were explicitly authorized by the operator after
instrumentation-only failures:

1. First additional launch: after the initial attempt failed closed at event 0
   with `prefix_stencil_unavailable` (a packed depth-stencil renderbuffer is
   reported on `GL_DEPTH_ATTACHMENT`, and a colour-only clear's default stencil
   mask was misread). Fix `7d6686ef`.
2. Final launch: after the second attempt captured all 21 observations but
   failed at seal with `artifact_size_limit` (the ordinary 512 MiB frame-wide
   blob cap, not the 700 MB prefix budget). Fix `c06e3ea6`.

No budget was reset, merged or silently restored. Total genuine launches: **3**
(1 initial + 2 explicitly authorized). No correction evaluation was submitted.

## Attempts

### Attempt 1 — instrumentation failure at event 0

Session `4fd7deb8-4836-4107-90af-a954a5b8bd53`, pid 93577. The capture reached
present 72 with 283 operations and recorded the initial pre-event state, then
failed closed at the event-0 post-clear with `prefix_stencil_unavailable`. No
seal was produced; the process was terminated (`terminated=true`,
`killed=false`), the display lease released, and `settings.txt` restored to the
recorded SHA-256.

Evidence: `.local/dsh/m7-event20-20261121/workspace/.local/m7/event20/live`
(partial frame, 283-event stream, reference bytes, live CGL drawable record).

### Attempt 2 — seal failure after a complete prefix

Session `1bf18fa3-ae39-4065-9879-cd96ee5e5e5f`, pid 94688. The prefix was
complete: 21/21 post-event observations, 304,373,440 bytes, 0.894 s, 279
operations. Sealing failed with `artifact_size_limit` because the prefix blobs
(304 MB) plus ordinary frame artifacts exceeded the fixed 512 MiB cap. No seal
was produced; cleanup and display restoration succeeded.

### Attempt 3 — sealed pilot

Session `47590918-d63a-4798-9737-df00285ad51b`, pid 95131, frame 1, present 72,
283 operations (273 draws, 9 clears, 1 copy), 1728×1117 BGRA8 lower-left.

Command (run from the stage-3 candidate worktree, with absolute control paths):

```sh
EU4_CAMPAIGN_STATE=<repo>/.local/m7/event20/state-stage3.json \
EU4_DSH_PYTHON=/opt/homebrew/opt/python@3.14/bin/python3.14 \
python3.14 -m automation.m6_frame capture \
  --output .local/m7/event20/live \
  --campaign-run-root <repo>/.local/dsh/m7-event20-stage3/run \
  --callers 0x10d52fc,0x1486ad9 --min-draws 8 --max-draws 512 \
  --duration 120 --timeout 300 \
  --prefix-end 20 --prefix-max-bytes 700000000 --prefix-max-ns 10000000000 \
  --seal --shadow --background \
  --shadow-timeout-ms 360000 --shadow-wait-ms 360000 --seal-timeout-ms 600000
```

Managed infrastructure only: the one-use launch claim was consumed immediately
before `Popen`; the BetterDisplay session was prepared by the existing harness;
no custom launcher or capture script was used.

Result: seal written, shadow execution `ok`/`complete`, 283/283 events encoded,
26 checkpoint entries (21 post-event prefix entries plus the ordinary graph
checkpoints). Divergence: event 1, `tex:433`, 23 pixels, max channel error 1.

## Offline discriminator

One offline replay was used to test the texture-representation hypothesis. The
event-1 checkpoint was replayed with `EU4_FRAME_REPLAY_FORCE_DECODED=1` (the
captured decoded RGBA8 chain instead of the original BC blocks) and
`--stop-after 2`:

| Candidate | Mismatched pixels | Max channel error |
| --- | ---: | ---: |
| Compressed BC blocks (sealed shadow) | 23 | 1 |
| Forced decoded RGBA8 | 17,446 | 1 |

The decoded path is substantially worse, so the compressed representation is
already the better path and is not the correction. One offline replay remains
unused; no correction was submitted.

## Independent verification

```sh
python3.14 -c 'from automation import gfx_prefix_check; ...'
```

The checker verified the seal, event stream, prefix file and blobs, request
digest and receipt binding, request/response identity, target/resource
identity, dimensions, origin, pixel format and every comparison from the
original bytes. Result: `FIRST_DIVERGENCE` at event 1; exact verified prefix
`[0, 0]`; exact observations at 10 and 12–17; event 11 excluded as an isolated
unsupported auxiliary clear. Full output: `analysis/m7/m7-event20-comparison.json`.

The campaign `accept.py` would print `fail` for this result. The model did not
certify its own output.

## Cleanup

| Item | Result |
| --- | --- |
| EU4 process | terminated (`terminated=true`, `killed=false`), no orphan |
| Launch lease | released |
| Background display | `EU4 Test` (not main); physical display unchanged |
| `settings.txt` | restored byte-for-byte to `62b4902a3a999ad3…dd496` |
| Campaign reservation | completed atomically; no open reservation |
| Automatic merges | 0 |

## Counts

| Item | Value |
| --- | ---: |
| Genuine EU4 launches | 3 (1 initial + 2 operator-authorized) |
| Newly sealed frames | 1 |
| Shadow executions | 1 completed (plus 2 failed attempts that did not produce a seal) |
| Offline replays | 1 used / 2 allowed |
| Correction evaluations submitted | 0 / 1 |
| Successful qualifications | 0 (first divergence at event 1) |
| Live wall-clock (stage 3, launch to cleanup) | under 300 s |
| Total session work for the pilot and implementation | approximately 50 minutes |

The campaign ends with `FIRST_DIVERGENCE` at event 1 and
`requires_additional_evidence` for the root cause. Exact event-20 parity is not
claimed.
