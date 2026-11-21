# M7 event-20 readiness and exact-prefix implementation

Date: 21 November 2026 (Europe/Berlin). Baseline: `main` at
`3e725d3c693797853b1265f870dc16598d8b598c`.
Verdict: **checker and trusted-evidence remediation is implemented; the live
pilot's first authoritative divergence remains event 1, so event-20 parity is
not claimed. `M7_AUTONOMOUS_READY` is also not claimed because the corrected
checker has not been exercised in a fresh native multi-round DSH run.** The live
drawable-format prerequisite for issue #278 is recorded from the original EU4
process.

Remediation status for PR #280: requests are independently derived from the
sealed graph, the checker builds and executes the **candidate** renderer from a
verified source snapshot, original evidence is an immutable copied snapshot
outside the candidate workspace, alpha/format scope is derived from attachment
identity, and prefix classification walks events sequentially. The scientific
result was re-derived from the same immutable artifacts without another EU4
launch. A controlled candidate defect changed the independently executed
result; reverting it restored the baseline result.

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
| Independent exact checker | `automation/gfx_prefix_check.py`, `campaigns/eu4-m7-event20/accept.py` | implemented; derives the request from the graph, executes the trusted Metal replay, recomputes every comparison from original bytes |
| Trusted candidate execution | `gfx_prefix_check.verify_candidate`, `replay_host_dependencies.snapshot_sources/build_snapshot_host` | implemented; candidate worktree/revision/policy verified, dependency snapshot copied, host built and executed from candidate source, receipt binds source/build/host/outputs |
| Trusted execution and evidence | `gfx_prefix_check._run_trusted_replay`, `prepare_trusted_evidence` | implemented; candidate `replay.json`/receipt/pixels are not authority; immutable copied reference snapshot verified before use |
| Replay accounting | `campaign_guard.attempt("replays")` in the checker | implemented; one actual replay consumed, failures charged, nested calls join, exhaustion refused before build |
| Portable bounded reproduction | `automation/gfx_prefix_case.py` | implemented; 75,456,851-byte derived event-0..1 case, 56 files, independently reproduces event 1 |
| Canonical campaign revision | `dsh/campaigns/m7-event20.json` | one active pin; stage sidecars archived as non-executable |
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

Alpha applicability is derived from the sealed attachment identity, not from a
mutable declaration. The default drawable and RGBA8 targets compare all four
channels, including alpha. RGB8 targets have no stored alpha and are reported
as **RGB-scope** exact comparisons; their BGRA8 materialization's alpha byte is
excluded explicitly, not silently. R32F targets are compared as the exact
four-byte float payload (**R32F-scope**). A declared `alpha_applicable` that
contradicts the attachment identity invalidates the frame.

## Candidate execution proof (A/B)

A fresh canonical candidate worktree was used; no EU4 launch was made.

| Run | Candidate source digest | Replay-host SHA-256 | Result |
| --- | --- | --- | --- |
| Baseline | `255a5b7e…` | `190fde92…` | first divergence event 1, 23 px / max 1 |
| Controlled defect (red clear +0.01) | `492e6654…` | `533f79c6…` | first divergence event 0, 1,930,176 px / max 3 |
| Defect reverted | `255a5b7e…` | `190fde92…` | first divergence event 1, 23 px / max 1 |
| Same candidate on immutable copy | `255a5b7e…` | `190fde92…` | first divergence event 1, 23 px / max 1 |

The checker builds from a snapshot of the candidate dependency closure, binds
the source digest, compiler/toolchain identity, source file hashes and compiled
host SHA-256, executes against the verified copied reference, rehashes the host,
source and every output after execution, and recomputes all comparisons from
those outputs. Candidate-authored `replay.json`, receipts and pixels are never
authority. A source change after the build, a replaced output, receipt, replay
result or executable is detected. Full record:
[`m7-event20-candidate-execution.json`](m7-event20-candidate-execution.json).

Replay accounting: the trusted execution reserves one `replays` allowance
before translation/build/GPU execution. A failed or interrupted run remains
charged; nested checker/replay calls join one reservation; exhaustion refuses
before the build starts. Tests cover repeated calls, failure and nesting.

## R32F comparison semantics

R32F is one 32-bit IEEE-754 payload per pixel. It is compared bitwise
(`exact_bitwise`, `mismatched_pixels`, `max_ulp_error`), never as four RGBA
channel intensities. `max_channel_error` is reported as zero for an R32F
comparison and the result declares `format_scope=R32F`.

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
| 11 | `tex:430` (R32F) | unobserved in this seal; excluded | — | — |
| 12 | `tex:431` | match (RGBA) | 0 | 0 |
| 13 | `tex:432` | match (RGBA) | 0 | 0 |
| 14 | `tex:432` | match (RGBA) | 0 | 0 |
| 15 | `tex:431` | match (RGBA) | 0 | 0 |
| 16 | `tex:431` | match (RGBA) | 0 | 0 |
| 17 | `fb:0` | match (RGBA) | 0 | 0 |
| 18 | `fb:0` | divergent | 359 | 12 |
| 19 | `fb:0` | divergent | 1,186 | 12 |
| 20 | `fb:0` | divergent | 4,781 | 22 |

These results were re-derived by the corrected checker using a trusted replay
executed from the infrastructure checkout (request
`f3f22ce7eef778670820d88478634293bd1f1ff27f743ab71ee2f29dd030fffb`,
host `benchmark/.build/gfx_frame_replay`), not from candidate metadata.
First authoritative divergence: **event 1**, `tex:433`, first differing pixel
(129, 4), bounds x4..169, y4..559, 23 pixels, max channel error 1, **RGB
scope**. Events 10 and 12–17 match exactly in **RGBA scope**, including alpha.
The later event-18 divergence (359 px, max 12, RGBA scope) is a second, larger
difference that is not reachable while the campaign stops at event 1.

Verdict: **`FIRST_DIVERGENCE` at event 1. Exact verified prefix: events 0
through 0 only** (event 0 is exact; event 1 is the first divergent operation).
Events 10 and 12–17 are exact observations but do not form a contiguous prefix
until event 1 is resolved. No event-20 qualification is claimed.

## Final decisions

- **M7_AUTONOMOUS_READY: NO.** Trusted candidate execution, evidence isolation,
  accounting, focused tests and the full gate pass, but a genuine native DSH
  multi-round correction run with the corrected checker has not been
  demonstrated.
- **EVENT20_EXACT: NO.** Event 1 diverges (23 RGB pixels, max byte-channel
  difference 1); event 11 has no original R32F observation in this seal. The
  exact verified prefix is `[0, 0]`.
- **Merge: NO** until independent review of the exact final head and the
  native multi-round gate are complete.

## Outstanding limitations

- The event-1 difference is not root-caused. It is a 1-LSB, sparse RGB
  difference on a DXT1_ALPHA-textured offscreen target. Forcing the decoded
  RGBA8 texture path made the same checkpoint worse (17,446 px), so the
  compressed BC representation is already the better path; the remaining
  single-LSB difference needs original-driver decode/filter rounding evidence
  or a captured state discriminator.
- Event 11 writes `tex:430`; the live observation records internal format
  **0x822E (GL_R32F)**, component type float, red bits 32. Native and Metal
  R32F support, plans, readback and tests are implemented, but the existing
  seal captured the event before that support existed, so it has no original
  R32F reference pixels. It remains an explicitly unverified operation:
  `EVENT20_EXACT` is impossible for this seal, and exclusions are never
  accepted as exact. A future capture will record and compare R32F.
- The event-18+ divergence on `fb:0` is real and later than the first
  divergence; it was preserved but not corrected.
- The original full-frame case attempt required 860,327,938 bytes and remains
  recorded as incomplete under the 512 MiB budget. A bounded **derived prefix**
  case is now verified: events 0..1, 75,456,851 bytes, 56 files, explicit
  `derived_from` provenance, trusted-checker verdict `FIRST_DIVERGENCE` at
  event 1. It is not the original full seal.
- The reconstructed-GL control was not re-qualified for the new seal. Direct
  EU4-versus-Metal evidence does not require it; no causal Metal claim relies on
  it.
- `gl_dither` and `multisample` remain unsupported Metal state. The event-1
  pattern is not periodic and is not attributed to dithering by this pilot.
- The corrected checker has not been exercised in a new native multi-round DSH
  run. Previous controlled multi-round evidence predates these changes, so
  `M7_AUTONOMOUS_READY` is **not** claimed.
- A fresh live capture has **not** been launched. Task 8 requires Tasks 1–7 to
  pass first; native multi-round integration remains open.

## Tests and gates

Focused suites executed during development:

- `tests.test_m7_event20_prefix` (35 tests): exact prefix; one-channel and
  alpha-only failures; event-18 difference that disappears by 20; missing
  post-event-20; foreign seal; forged/stale receipt; request substitution;
  reference-copied-into-Metal rejection; pixel-format normalization; tampered
  prefix file; offscreen coverage; R32F plan; forged alpha; inconsistent
  attachment identity; sequential-prefix classification; trusted-evidence
  mutation; derived prefix case.
- `tests.test_gfx_frame_trace_writer` and `tests.test_m7_drawable_observe`.
- `tests.test_gfx_frame_graph`, `tests.test_gfx_independent_replay`,
  `tests.test_m7_checkpoint_provenance`, `tests.test_gfx_replay_checkpoints`,
  `tests.test_m6_frame`, `tests.test_correction_accept`,
  `tests.test_campaign_guard`, `tests.test_dsh_campaign`,
  `tests.test_dsh_bridge`, `tests.test_dsh_pilot`, `tests.test_m7_diagnosis`.
- Required focused remediation command passed:
  `tests.test_m7_event20_prefix`, `test_m7_drawable_observe`,
  `test_gfx_frame_graph`, `test_gfx_independent_replay`,
  `test_gfx_replay_checkpoints`, `test_m7_checkpoint_provenance`,
  `test_campaign_guard`, `test_dsh_campaign`, `test_dsh_bridge`.
- Final autonomy gate (`20261121T172542Z-17dd1d11`) at `4b34bbd5`:
  4,445 ran / 4,409 executed / 36 skipped / 0 failures / 0 errors, 1,079.9 s.
  `test_profiles verify --profile offline` passed before the gate.
- Canonical `accept.py` on the final pinned candidate (`6619f13a`) and immutable
  copied reference returned `fail`: `FIRST_DIVERGENCE` at event 1, 23 px /
  max 1 RGB, candidate source digest `255a5b7e…`, host `190fde92…`, trusted
  reference root `b3e3d7b5…`.
