# Three paired efficiency trials — sealed present 904

Date: 21 November 2026. Offline, single host, fixed workload. This report
supersedes the "outstanding" state of finding D1b/3e in the harness-review
matrix. It measures one workflow step honestly and claims no renderer change.

## Fixed workload

| Field | Value |
|---|---|
| Frame | `.local/m7/goal-round1/capture-c/frame` |
| Session / present | `afd444f2-6478-459f-8db6-e565c49dbd63` / 904 |
| Events / extent | 327 / 1728 × 1117 BGRA8 |
| Event stream | `8801f037a06ed618e973f081fa5a20dca5a705124f92fca50c9d12554e3dd547` |
| Metal replay | independent replay host, 327/327 encoded, 0 failed, readback ok |
| Shaders | the sealed shadow's translated shader set (warm cache), `shaders.json` `843076d3…` |
| Ground truth | first non-exact checkpoint **15** (event 15), **4** differing pixels, max channel error **1**, first pixel (209, 50) |

Both arms re-execute the same replay on the same input and differ only in the
projection the caller consumes:

- **A — legacy full report.** `python3.14 -m automation.gfx_independent_replay`
  writes the producer's `replay-report.json` (6,195,746 bytes here); the caller
  extracts `first_divergence` and checkpoint statistics from it.
- **B — canonical compact.** `python3.14 -m automation.agent_tools eu4_replay
  independent` re-executes the same replay and returns the bounded
  `gfx-agent-diagnosis/v1` envelope (7,768 bytes, `truncated=true`).

## Results

Order alternated as A/B, B/A, A/B. All six runs were correct by the
pre-declared criterion: first divergence event 15, checkpoint-15 pixels 4, max
channel error 1, and not a tool failure.

| Trial | Arm | Wall to correct classification (s) | Payload bytes | First event | Pixels / max | Classification | EU4 launches |
|---:|---|---:|---:|---:|---:|---|---:|
| 1 | A | 7.855 | 6,195,746 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |
| 1 | B | 7.530 | 7,768 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |
| 2 | B | 7.575 | 7,768 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |
| 2 | A | 7.496 | 6,195,746 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |
| 3 | A | 7.525 | 6,195,746 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |
| 3 | B | 7.667 | 7,768 | 15 | 4 / 1 | `incomplete_diagnostics` | 0 |

| Arm | Median wall (s) | Range (s) | Median payload (bytes) |
|---|---:|---:|---:|
| A legacy full report | 7.525 | 7.496–7.855 | 6,195,746 |
| B canonical compact | 7.575 | 7.530–7.667 | 7,768 |

**No wall-time improvement is claimed.** The medians differ by 0.05 s (about
0.7 %), inside the observed spread; the replay itself dominates both arms. The
measured interface difference is payload: **798× less JSON** (6,195,746 →
7,768 bytes) for the same correct classification, with the compact envelope
bounded and explicitly truncated.

## Accounting

| Metric | A | B |
|---|---:|---:|
| Replay invocations | 1 | 1 |
| Checker invocations | 0 | 0 |
| Confirmed EU4 launches | 0 | 0 |
| Confirmed EU4 processes during any trial | 0 (`pgrep -fl eu4.app/Contents/MacOS/eu4` empty) | 0 |
| Shader-translation cache | warm hit (sealed shadow set supplied) | warm hit |
| Native Metal function/pipeline cache | in-process only; recompiled per run (miss) | same |
| GPU service time | `unknown` (host records no GPU timing) | `unknown` |

The launch count is a process observation, not a command-string estimate; no
launch guard/lease was created by either arm.

## Cache context

The paired cache profile (`cache-profile.md`) measured on the same frame:
cold translation 3.211 s vs warm lookup 0.374 s (8.6× on translation), cache
identity hashing 26.8 ms, native replay 0.603 s, GPU time unknown. Those numbers
explain why the anti-clock in this A/B is diagnosis and process startup, not
shader translation; they are not used to claim an improvement here.

## Limitations

- Machine-side wall time only: it excludes model reasoning, human time and tool
  selection, which the review's assessment shows dominated the historical
  sessions. This is not a session-level productivity measurement.
- One workload, one host, three pairs per arm. Order was alternated but the
  arms are not randomized; the machine was otherwise idle for these runs.
- Both arms execute the same renderer; the experiment varies only the
  projection. It says nothing about renderer correctness or speed.
- `incomplete_diagnostics` is the correct outcome for this offline replay
  (state coverage partial, shader identity `unverified_session`); it is not a
  tool failure and is identical in both arms.
- The raw JSON comparison was produced with `automation.m6_compare`, exact
  four-channel, no row flip; it is a diagnosis statistic, not a parity claim.

## Reproduction

```sh
python3.14 analysis/harness-remediation/evidence/efficiency/trials.py \
  --repo .local/worktrees/closure-candidate \
  --frame .local/m7/goal-round1/capture-c/frame \
  --shaders .local/m7/goal-round1/capture-c/shadow/shaders \
  --trials 3 --order AB,BA,AB
```

Raw records: [`evidence/efficiency/trials.json`](evidence/efficiency/trials.json).
