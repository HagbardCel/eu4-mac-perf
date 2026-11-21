# M7 event-20 readiness and exact-prefix implementation

Date: 21 November 2026 (Europe/Berlin). Baseline: `main` at
`3e725d3c693797853b1265f870dc16598d8b598c`.
Verdict: **infrastructure and independent checker implemented and qualified;
the live pilot found its first authoritative divergence at event 1, so event-20
parity is not claimed.** The live drawable-format prerequisite for issue #278 is
now recorded from the original EU4 process.

This report distinguishes *observed pixel equality* from engine-semantic
equivalence. Only the original EU4 OpenGL process is the oracle. The
reconstructed OpenGL control was not run and is not the reference.

## Implemented functionality

| Capability | Where | State |
| --- | --- | --- |
| Opt-in exact-prefix post-event observation (events 0..N) | `benchmark/eu4_gfx_frame_trace.c` | implemented; disabled unless `EU4_GFX_FRAME_TRACE_PREFIX_END` is set |
| Default and offscreen colour targets, clears, draws and copies | native trace + `benchmark/eu4_gfx_gl_interpose.c` | implemented; copies read the destination texture |
| Initial pre-event-0 state, pre/post distinction, event ordering | native observation file | implemented; pre-event and post-event records are distinct |
| Depth/stencil evidence where the target/state uses it | native trace | captured; a target without the attachment is recorded `not_present` |
| Aggregate readback byte/time budgets and fail-closed gaps | native trace | implemented; `prefix_budget`, `prefix_time`, `prefix_gap`, `prefix_incomplete` |
| Manifest binding and blob verification | native manifest + `automation/m6_frame.py` | prefix file digest, embedded observations and every blob are re-verified |
| Metal post-event checkpoints | `benchmark/eu4_gfx_frame_replay.mm` | implemented; `timing=post_event` |
| Graph coverage and unsupported-aux policy | `automation/gfx_frame_graph.py` | implemented; isolated unconsumed unsupported clears are explicit exclusions |
| Independent exact checker | `automation/gfx_prefix_check.py`, `campaigns/eu4-m7-event20/accept.py` | implemented; recomputes every comparison from original bytes |
| Issue #278 synthetic observer | `tests/gfx_drawable_observe_host.m` | implemented and tested |
| Issue #278 live drawable record | native manifest `gl.cgl` + `gl.default_drawable` | implemented; live values recorded in the pilot |
| Campaign and DSH sidecar | `campaigns/eu4-m7-event20/`, `dsh/campaigns/m7-event20*.json` | implemented; isolated worktree and trusted checker outside the candidate |

The exact-prefix mode writes one full post-event observation per executed
operation through the selected end event. Each observation carries its event,
target/resource identity, dimensions, origin, row stride, pixel format, colour
blob digest, optional depth/stencil blob, target internal-format/component
identity and readback time. The sealed manifest binds the observation file and
all blobs. A missing operation, an unobserved consumed resource, a wrong event
or resource identity, a stale receipt or a mismatched pixel format fails closed.

RGB8 targets are compared on R/G/B exactly; their BGRA8 materialization's alpha
byte is not an applicable channel of the original alpha-less attachment. This
normalization is declared per checkpoint and is not a tolerance. Default and
RGBA8 targets compare all four channels, including alpha.

## Issue #278 drawable identity

The live observer recorded the effective default drawable in the sealed pilot
(session `47590918-d63a-4798-9737-df00285ad51b`):

| Field | Value |
| --- | --- |
| CGL colour / alpha / depth / stencil sizes | 32 / 8 / 32 / 8 |
| CGL sample buffers / samples | 0 / 0 |
| CGL accelerated / double buffer / stereo | 1 / 1 / 0 |
| OpenGL profile | legacy (`2.1 Metal - 91.7`) |
| Default framebuffer colour | `unsigned_normalized`, 8/8/8/8 |
| Default framebuffer depth | 32 bits, `unsigned_normalized` component type |
| Default framebuffer stencil | 8 bits |
| Render-target path | `default-window` (draw framebuffer 0) |
| Window / back-buffer identity | draw/read buffer `0x405` (GL_BACK); the window id is not exposed by CGL and is recorded as unavailable |

A reconstructed control that draws into an RGBA8 FBO with a 32-bit depth and
8-bit stencil attachment is therefore format-equivalent at the attachment
identity level for this drawable. The control still needs an independently
reviewed target assertion and a re-pinned authority before it can support a
causal Metal claim; that review was not performed. Issue #278's missing
observable is resolved; the control qualification remains optional.

## Live verification result

The third and final authorized launch produced a complete sealed frame:

| Field | Value |
| --- | --- |
| Session / pid | `47590918-d63a-4798-9737-df00285ad51b` / 95131 |
| Seal frame / present / scene | 1 / 72 / 72 |
| Operations | 283 (273 draws, 9 clears, 1 copy) |
| Extent / format / origin | 1728 × 1117 / BGRA8 / lower-left |
| Reference SHA-256 | `b38d3804bce3d3575f848fbe800859e7675337e039d2606a984327f997bab362` |
| Manifest SHA-256 | `e4696cd34eca08a3b6ce364e2c7a8bf2135efe11d8c5dab8bf1cc4a83abe51da` |
| Event-stream SHA-256 | `a867808bf357469f211b1c6c1790358b7d297d88229df5bcb36b1ff007c2ec9f` |
| Exact-prefix end / observations | 20 / 21 post-event, plus the initial pre-event-0 state |
| Prefix payload / readback time | 304,373,440 bytes / 0.894 s |
| Shadow execution | status `ok`, stage `complete`, 283/283 encoded |
| Request digest / checkpoints | `99c8f7cf33b90b2e32bee80e86f5879f227aecafc4e9009eddd05dc051dd82f2`, 26 |

Independent checker result (`analysis/m7/m7-event20-comparison.json`):

| Event | Resource | Status | Mismatched pixels | Max channel error |
| ---: | --- | --- | ---: | ---: |
| 0 | `tex:433` (RGB8) | match (R/G/B) | 0 | 0 |
| 1 | `tex:433` | **divergent** | 23 | 1 |
| 2 | `tex:433` | divergent | 164 | 1 |
| 3 | `tex:433` | divergent | 301 | 1 |
| 4 | `tex:433` | divergent | 1,630 | 9 |
| 5 | `tex:433` | divergent | 1,933 | 9 |
| 6 | `tex:433` | divergent | 2,549 | 94 |
| 7 | `tex:433` | divergent | 2,583 | 94 |
| 8 | `tex:433` | divergent | 3,673 | 118 |
| 9 | `tex:433` | divergent | 5,199 | 118 |
| 10 | `fb:0` | match (RGBA) | 0 | 0 |
| 11 | `tex:430` (unsupported aux) | excluded, isolated | — | — |
| 12 | `tex:431` | match (RGBA) | 0 | 0 |
| 13 | `tex:432` | match (RGBA) | 0 | 0 |
| 14 | `tex:432` | match (RGBA) | 0 | 0 |
| 15 | `tex:431` | match (RGBA) | 0 | 0 |
| 16 | `tex:431` | match (RGBA) | 0 | 0 |
| 17 | `fb:0` | match (RGBA) | 0 | 0 |
| 18 | `fb:0` | divergent | 359 | 12 |
| 19 | `fb:0` | divergent | 1,186 | 12 |
| 20 | `fb:0` | divergent | 4,781 | 22 |

First authoritative divergence: **event 1**, `tex:433`, first differing pixel
(129, 4), bounds x4..169, y4..559, 23 pixels, max channel error 1. Events 10
and 12–17 match exactly, including alpha. The later event-18 divergence (359
px, max 12) is a second, larger difference that is not reachable while the
campaign stops at event 1.

Verdict: **`FIRST_DIVERGENCE` at event 1. Exact verified prefix: events 0
through 0 only** (event 0 is exact; event 1 is the first divergent operation).
Events 10 and 12–17 are exact observations but do not form a contiguous prefix
until event 1 is resolved. No event-20 qualification is claimed.

## Outstanding limitations

- The event-1 difference is not root-caused. It is a 1-LSB, sparse RGB
  difference on a DXT1_ALPHA-textured offscreen target. Forcing the decoded
  RGBA8 texture path made the same checkpoint worse (17,446 px), so the
  compressed BC representation is already the better path; the remaining
  single-LSB difference needs original-driver decode/filter rounding evidence
  or a captured state discriminator.
- Event 11 writes `tex:430` with an internal format the Metal replay does not
  materialize (the event stream records `other`; the sealed run treats it as an
  explicitly excluded, unconsumed auxiliary clear). It is accounted for but not
  compared. If it were consumed inside the prefix, coverage would be invalid.
- The event-18+ divergence on `fb:0` is real and later than the first
  divergence; it was preserved but not corrected.
- The portable replay case is incomplete under the established 512 MiB artifact
  budget: the single-frame case requires 860,327,938 bytes. The full sealed
  frame, shadow outputs, request, receipt and logs are preserved under
  `.local/dsh/m7-event20-stage3/workspace/.local/m7/event20/live`; the case
  manifest records `reproduction.status=incomplete` and
  `reason=artifact_budget`.
- The reconstructed-GL control was not re-qualified for the new seal. Direct
  EU4-versus-Metal evidence does not require it; no causal Metal claim relies on
  it.
- `treats gl_dither and multisample as unsupported Metal state` remains a
  limitation of the shared state coverage. The event-1 pattern is not periodic
  and therefore is not attributed to dithering by this pilot.

## Tests and gates

Focused suites executed during development:

- `tests.test_m7_event20_prefix` (15 tests): exact prefix, one-channel/one-pixel
  failure, event-18 difference that disappears by 20, missing post-event-20,
  foreign seal, forged/stale receipt, pixel-format normalization, tampered
  prefix file, offscreen coverage, excluded auxiliary clear, campaign stop.
- `tests.test_gfx_frame_trace_writer` and `tests.test_m7_drawable_observe`.
- `tests.test_gfx_frame_graph`, `tests.test_gfx_independent_replay`,
  `tests.test_m7_checkpoint_provenance`, `tests.test_gfx_replay_checkpoints`,
  `tests.test_m6_frame`, `tests.test_correction_accept`,
  `tests.test_campaign_guard`, `tests.test_dsh_campaign`,
  `tests.test_dsh_bridge`, `tests.test_dsh_pilot`, `tests.test_m7_diagnosis`.
- One final full offline gate was run on the final integration state; its record
  is in the pilot report.
