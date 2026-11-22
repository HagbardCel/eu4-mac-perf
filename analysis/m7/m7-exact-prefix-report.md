# Exact-prefix graphics qualification (M7 report)

Date: 22 November 2026 (Europe/Berlin). Base: `main` at `3e725d3c`.
This is the consolidated technical report for PR #280. Operator commands are in
[m7-operator-runbook.md](m7-operator-runbook.md); the implementation contract is
in [AGENTS.md](../../AGENTS.md) and [status](../../docs/status.md).

Verdict: the observation and qualification mechanism is implemented and
reusable. For the authentic M7 seal (session `47590918…`) the independently
verified exact prefix is `[0, 0]`; the first divergence is event 1
(`tex:433`, RGB scope, 23 pixels, max channel error 1). Event-20 parity and a
`M7_AUTONOMOUS_READY` claim are **not** established.

## One mechanism

| Concern | Single owner |
| --- | --- |
| Observation record and channel identity | `automation/gfx_observation.py` |
| Native guarded readback and target identity | `benchmark/eu4_gfx_frame_trace.c` (`read_observation_pixels`, `prefix_target_*`) |
| Resource dependency/coverage and observation plan | `automation/gfx_frame_graph.py` |
| Capture seal, event stream, observation-file verification | `automation/m6_frame.py` |
| Candidate execution, checkpoint binding and pixel comparison | `automation/gfx_independent_replay.py` |
| Generic verdict and trusted-reference/candidate enforcement | `automation/gfx_prefix_check.py` |
| Campaign policy (M7 names only here) | `campaigns/eu4-m7-*/` |

The native writer emits one record per line of `prefix-observations.jsonl`. The
manifest references the file by digest, byte count and record count; it no
longer duplicates the records. The reader accepts a legacy manifest that embeds
the same records only when they agree exactly with the file.

The shared readback saves and restores the draw/read framebuffer bindings, the
read buffer, the active unit's 2D texture binding, pack alignment/row/skip,
byte-swap/bit-order, and a bound pixel-pack buffer. It never calls `glGetError`
or `glFinish`, so a pending renderer error is neither consumed nor invented.
The same primitive serves the final reference, default checkpoints and prefix
observations.

Channel scope is derived from the effective attachment identity:

| Internal format | Read layout | Compared channels |
| --- | --- | --- |
| default drawable (8/8/8/8) | BGRA8 | RGBA |
| `GL_RGBA8` | RGBA8 | RGBA |
| `GL_RGB8` | BGRA8 | RGB (alpha byte excluded) |
| `GL_SRGB8_ALPHA8` | BGRA8 | RGBA |
| `GL_R32F` | R32F | exact 32-bit float payload |

Any other or ambiguous format is `unsupported`, never normalized. The default
drawable's alpha applicability comes from its queried channel sizes, and
`GL_SRGB8_ALPHA8` is no longer grouped with `GL_RGB8`.

## Authentic evidence

| Field | Value |
| --- | --- |
| Session / pid | `47590918-d63a-4798-9737-df00285ad51b` / 95131 |
| Seal frame / present / scene | 1 / 72 / 72 |
| Operations | 283 (273 draws, 9 clears, 1 copy) |
| Extent / origin | 1728 × 1117 / lower-left |
| Reference SHA-256 | `b38d3804bce3d3575f848fbe800859e7675337e039d2606a984327f997bab362` |
| Manifest SHA-256 | `e4696cd34eca08a3b6ce364e2c7a8bf2135efe11d8c5dab8bf1cc4a83abe51da` |
| Event-stream SHA-256 | `a867808bf357469f211b1c6c1790358b7d297d88229df5bcb36b1ff007c2ec9f` |
| Prefix observations | 21 post-event + initial pre-event-0 |
| Observation payload / readback | 304,373,440 bytes / 0.894 s |
| Model request digest | `99c8f7cf33b90b2e32bee80e86f5879f227aecafc4e9009eddd05dc051dd82f2`, 26 checkpoints |

Independently re-derived result for this seal:

| Event | Resource | Status | Mismatched px | Max channel error |
| ---: | --- | --- | ---: | ---: |
| 0 | `tex:433` (RGB8) | match (RGB) | 0 | 0 |
| 1 | `tex:433` | **divergent** | 23 | 1 |
| 2–9 | `tex:433` | divergent | 164 … 5,199 | 1 … 118 |
| 10 | `fb:0` | match (RGBA) | 0 | 0 |
| 11 | `tex:430` (R32F) | unobserved in this seal | — | — |
| 12–17 | `tex:431/432`, `fb:0` | match (RGBA) | 0 | 0 |
| 18 | `fb:0` | divergent | 359 | 12 |
| 19 | `fb:0` | divergent | 1,186 | 12 |
| 20 | `fb:0` | divergent | 4,781 | 22 |

Exact verified prefix: `[0, 0]`. The event-1 cause (DXT1 alpha path) is not
root-caused. Events 18–20 are genuine later divergences and cannot be hidden by
the earlier one.

The same result was re-derived offline from the preserved legacy-format seal
(`.local/m7/event20/trusted-copy/47590918`, existing Metal outputs) after the
consolidation: `FIRST_DIVERGENCE` at event 1, event 0 RGB, events 10/12–17 RGBA,
event 11 unsupported, events 18–20 divergent. The legacy observation schema and
manifest-embedded records remain readable.

## Trusted candidate execution and boundary

Retained: candidate worktree and allowed-path verification, dependency snapshot,
build from the actual candidate renderer sources, source/compiler/build/host
digests, immutable copied reference outside the candidate workspace,
post-execution output rehashing, and one charged replay reservation.
Candidate-authored `replay.json`, receipts and pixels are never authority.

The checker materializes a **reference-free replay input** for the candidate.
The sanitized event representation is built first (removing
`default_checkpoint`), and the blob allowlist is derived from that
representation. Final reference images, default-checkpoint images and
observation payloads are omitted even when their content-addressed name
collides with an input; the shader translation runs in trusted infrastructure
and supplies only translated shader artifacts. Overlap is reported as
`shared_input_outputs` with the affected observation/checkpoint, and
`independent_equality=false` when a compared output is also a renderer input.
The M7 acceptance adapter requires `independent_equality=true` before a pass.

The candidate replay is wrapped in the platform's existing Seatbelt mechanism
(`sandbox-exec`). The profile denies reads and writes of the trusted frame,
trusted-evidence root and original capture directory. If Seatbelt is
unavailable, candidate execution is refused rather than run without isolation.
The execution record names the mechanism and protected paths. Compiler
dependency closure is taken from `clang -MM`, with all non-system includes
required to reside in the attested snapshot; the build environment is a fixed
allowlist that drops `CPATH`, `DYLD_*`, `LD_*` and related injection variables.

The candidate still controls the native renderer and its GPU readback loop.
This is an input-separation boundary, **not a sandbox**: arbitrary native
candidate code can read the host filesystem and could fabricate output bytes.
What it cannot obtain from its inputs is the authenticated expected payload.
An output payload that is also a legitimate renderer input (for example a
texture later sampled by the frame) is indistinguishable at this boundary and
is recorded as `shared_input_outputs` in the execution record.

Quoted source includes must resolve inside the candidate checkout; an absolute
or escaping include is rejected as unattested rather than compiled from the
host. System/SDK headers remain part of the trusted toolchain identity.

Evidence of the boundary: `CandidateTrustBoundaryTests` builds a deterministic
replayable fixture in test preparation (no pre-existing `.local` evidence
required) and compiles real modified candidates. Three attacks are exercised:
copying `blobs/<reference_file>` from the sanitized replay input, reading the
absolute trusted-reference directory, and replacing checkpoint output
generation. Each test asserts the patch is present in the built source digest;
the first two assert no marker is written and `independent_equality=true`; the
interception test asserts the output marker is present and no exact verdict is
returned. A second test performs two validations with different candidate
revisions under one campaign root: new execution directories, new host digest,
new pixels and new provenance.

## Repeated correction rounds

`_run_trusted_replay` treats its configured directory as a campaign output
root. Every validation creates a unique `exec-*` child containing its replay
input, checkpoint request, shaders, candidate snapshot, host, Metal outputs,
receipt and `execution.json`. Nothing from a previous round is reused as a
result path. Failed attempts are retained and their execution directory is
recorded in `failed_execution.execution_dir`.

Translation is reused through the existing content-keyed translation cache;
candidate hosts are cached by their complete source/compiler identity. A
changed candidate source therefore rebuilds, and an unchanged one reuses the
cached host only after verifying its digest. Replay budget accounting remains
one reservation per validation, with nested calls joining it.

| Check | State |
| --- | --- |
| Candidate baseline/policy/revision verified | yes |
| Host built from a source snapshot, not the worktree | yes |
| Source changed between snapshot and build | detected |
| Output/receipt/replay changed after execution | detected |
| Reference copied into candidate output | does not qualify |
| Replay budget charged once, nested joins, exhaustion refused | tested |

## Reuse beyond M7

The same `gfx_prefix_check.evaluate` and reconciliation path is exercised by:

* **Scenario A (authentic, M7 scope):** the seal above through event 20;
  result `FIRST_DIVERGENCE` at event 1 with a verified prefix `[0, 0]`.
* **Scenario B (offline, different scope/format):** a synthetic sealed fixture
  with its own event sequence and an authenticated `R32F` event, evaluated with
  `require_end` equal to that fixture's own end. The generic verdict is
  `EXACT_PREFIX` (or the correct divergence), with `authority=synthetic`; it
  qualifies the mechanism and never original-frame equivalence.
* **Selected late events:** a 130-event synthetic sealed frame selects events
  118, 120 and 129 only. The observation file contains three colour payloads
  and no initial state; the checker returns `EXACT_SELECTION`, never
  `EXACT_PREFIX`, and a mutation classifies as `FIRST_DIVERGENCE` at the
  selected event. The same native readback, record and reconciler are used.
* **Derived reproduction:** `gfx_prefix_case.build_prefix_frame` cuts a
  contiguous prefix and re-validates dependency coverage on the derived frame;
  the result is labelled `derived_from … not the original full seal` and
  `authority=derived`.

Unsupported events or absent observations cannot produce exactness: a missing,
duplicated, substituted, foreign or unobservable operation yields
`INCOMPLETE_EVIDENCE` or `UNSUPPORTED`, never `EXACT_PREFIX`.

## Exactness contract

The asserted verdict is **colour-payload exactness** for the observed
attachment. The checker compares the applicable colour channels derived from
the sealed attachment identity; it does not compare captured depth/stencil
payloads with Metal depth/stencil attachments. Depth/stencil snapshots are off
by default and enabled only by
`EU4_GFX_FRAME_TRACE_OBSERVE_DEPTH_STENCIL=1` as diagnostics. On the preserved
seal, the old observations contained 162,134,784 colour bytes and 142,218,496
depth/stencil bytes; the new default saves 46.7% of the captured observation
bytes (and the corresponding readbacks for 17 of 22 observations). Colour
equality is not a claim of full backend-state parity.

## Verdicts

`gfx_prefix_check.evaluate` returns one of:

* `EXACT_PREFIX` – every event from 0 through the required end matched, with
  complete authenticated evidence;
* `EXACT_RANGE` – the complete observed range matched but is shorter than the
  requested end;
* `FIRST_DIVERGENCE` – the earliest verified divergence, with the prefix before
  it;
* `INCOMPLETE_EVIDENCE` – missing/invalid/unverified evidence (including a
  mismatch hidden behind an unverified earlier event);
* `UNSUPPORTED` – coverage is complete but an explicitly excluded auxiliary
  operation prevents an exactness claim;
* `EXACT_SELECTION` – every independently selected post-event observation
  matched, with no initial-state anchor and no prefix claim.

`EVENT20_EXACT` exists only in `campaigns/eu4-m7-event20/accept.py` and is
derived from an authentic `EXACT_PREFIX` through event 20.

## Retained artifact identity

Generated comparison documents were removed from Git. Their content is
reproducible from the immutable seal and the checker; the digests of the
removed review artifacts are recorded here:

| Removed artifact | SHA-256 |
| --- | --- |
| `m7-event20-candidate-execution.json` | `f1394c4ceb433656bbbfbb5f7288124084d84e6c9db98184e320a9d41e109079` |
| `m7-event20-case.json` | `c33efb12455515ff29473b5b70fef80fa22e6a037398efb2c77ba695bb872049` |
| `m7-event20-comparison-trusted.json` | `5b6d5fe79443337ff2f8949608861c00adf5f96be27236c264e0bbb5d2ce50fa` |
| `m7-event20-comparison-untrusted-20261121.json` | `9046f54138042ed8482c7968d9398e2b977c4b8652e1e8635b5ef4fcf9305867` |
| `m7-event20-comparison.json` | `14cf70a70a86ea0892058c6db2f171ba97bf678349f52514ffc128ab700081f9` |
| `pilot-round1-20261119/evidence.json` | `91def19fbe9cc829d6e18957acb9626cd6e6cd67235dcb2fb5d7bace28ead3a9` |
| `pilot-round1-20261119/report.md` | `d6231749cb54c8e31523714347906e4582f9051f35a1c29955526414c6052ec6` |

The derived reproduction case (56 files, 75,456,851 bytes, root
`d87b9241295333b7233afa11386c6ae176f18ee4d967ebb0b8d415d52d4da80d`) lives
under `.local/m7/event20/case-prefix` and is regenerable with
`automation.gfx_prefix_case`.

## Validation

* Focused: `tests.test_m6_frame`, `tests.test_gfx_frame_graph`,
  `tests.test_gfx_independent_replay`, `tests.test_m7_event20_prefix`,
  `tests.test_replay_host_dependencies`, `tests.test_m7_shadow_receipt` and
  `tests.test_m7_continuous_replay`: 0 failures, 101.3 s.
* Compiled candidate boundary: `CandidateTrustBoundaryTests` performs real
  candidate-source builds and Metal replays.  All three bypass attempts are
  prevented or caught; patches are verified against the built source digest;
  execution directories, host digests and pixels differ between revisions;
  translation is reused from the content cache.
* Native: `TraceStateHostTests` asserts state restoration, sentinel-error
  preservation and fail-closed unsupported draw-buffer, incomplete-framebuffer,
  cube-map and renderbuffer configurations (`observation=1 negative=1`).
* Selected late events: `prefix-late` selects 118, 120 and 129 of 130 events,
  captures three payloads (192 bytes, no depth/stencil) and returns
  `EXACT_SELECTION`, never `EXACT_PREFIX`.
* Legacy seal re-evaluation (offline, existing Metal outputs): unchanged
  `FIRST_DIVERGENCE` at event 1, prefix `[0, 0]`, authentic authority.
* Final offline gate `20261122T113751Z-6691f830`: 4,469 ran / 4,433 executed /
  36 skipped / 0 failures / 0 errors, 804.3 s (skips are pre-existing
  environment-gated suites, not the compiled-candidate tests).

### Candidate bypass results

| Attempt | Result |
| --- | --- |
| Copy `blobs/<reference_file>` from the sanitized replay input | reference payload absent; no marker; patch digest bound in the build record |
| Read the absolute trusted-reference directory | Seatbelt denies the read; no marker; execution record names `seatbelt` and the protected roots |
| Replace checkpoint output generation | marker present, wrong/short payload, no `EXACT_PREFIX` |
| Two revisions, one campaign root | distinct `exec-*` dirs, distinct host digests and pixels; translation cache hit |

The candidate tests do not skip: the fixture is generated during test
preparation from a real triangle draw and a trusted replay, and the source
patch is checked against `execution.candidate.source_digests`.

Historical M6 seals remain usable: `tests.test_m6_frame`,
`tests.test_m7_shadow_receipt`, `tests.test_m7_continuous_replay` and the
legacy-seal re-evaluation pass.  The new receipt/evidence schemas accept the
legacy M7 names.

## Limitations

* The event-1 divergence (`tex:433`, 23 px, max 1) is not root-caused.
* Event 11 is R32F and unobserved in this seal; R32F comparison is implemented
  and covered by synthetic/native tests.
* A driver/context without `GL_ARB_framebuffer_object` cannot report attachment
  component identity; such a target is `unsupported`, not normalized.
* Unsupported native configurations fail closed: non-`GL_BACK` default draw
  buffer, multisampled targets, non-complete framebuffers, draw buffers other
  than `GL_COLOR_ATTACHMENT0`, cube/array/3D texture attachments and
  renderbuffer colour attachments. A renderbuffer is identified as `fb:<n>`,
  never as a texture that shares its numeric GL name.
* Candidate replay is Seatbelt-isolated from the trusted frame/evidence roots,
  but candidate code is not a general sandbox and still runs native code with
  access to other host files.  Expected-only payloads are absent from its
  inputs; a payload that is also a legitimate renderer input is reported and
  cannot support an independent-equality acceptance.
* Default-framebuffer observations rely on the drawable extent reported by the
  host hook; a context that exposes neither extent nor viewport fails closed.
* Depth/stencil payloads are not part of the equality claim and are not
  compared with Metal depth/stencil; they remain opt-in diagnostics.  A future
  migration that needs depth parity must add a format-aware comparison.
* Selected observation sets qualify only the selected checkpoints; they never
  establish a contiguous prefix or initial-state equivalence.
* Independent review of the final head is still required before merge.
