# Current project status

Authoritative summary of what is established, qualified, and in flight. Operational commands live in [profiler and benchmark operations](reference/profiler-and-benchmark-operations.md).

## M6 — authentic title scene through Metal (2026-11-13)

Qualified offline and accepted live. An authentic 1728×1117 title/main-menu
frame (pinned GOG build `b3d38876…`, captured at `CGraphics::PresentScene`) is
replayed byte-identically through Metal, and three clean live launches render
and present the complete title scene — animated world map behind the EU4 logo,
Paradox logo, Continue Game, Single Player/Multiplayer/Tutorial/Exit, version
and icon row. [Qualification report](../analysis/m6/qualification-report.md),
[acceptance](../analysis/m6/live-acceptance.json),
[campaign record](../analysis/m6/campaign-record.md).

- **Frame (corrected reference).** The earlier reference was captured with an
  observer defect that corrupted texture unit 0 whenever the active unit was
  nonzero; the readback is now non-invasive. The authoritative capture is the
  menu-caller occurrence-300 frame (326 events, reference SHA-256 `ff2f4e2b…`,
  undirected corrected capture `92ea2167…`).
- **Offline replay.** The corrected capture replays exactly: 326/326 events
  encoded, zero failed, GPU completed, 0 differing pixels of 1,930,176,
  identical reference and Metal SHA-256 (`92ea2167…`). The old `c3dab19c…`
  evidence against the corrupted reference is superseded.
- **Live acceptance.** Three clean starts, all `accepted`: executed,
  GPU-completed, presented, UI structural correlation 0.77 / 0.94 / 0.55 against
  the authentic reference, and a map region with real rendered structure.
  The live checker fails closed on a missing/invalid reference.
- **Corrections that reached the scene.** Sampler binding by name; game depth
  cleared on depth clears; integer attribute conversion accepting MSL type
  names; rasterizer state decoded at the engine's real offsets (cull-enable at
  +0xd); `_GfxResolve` draining pending draws and copying the live backbuffer.
- **Sampler state (fixed).** Per-unit sampler objects are decoded and bound
  as cached Metal sampler states; partial/offset texture binds are applied.
  The live map palette is unchanged by this, so the remaining divergence is in
  the map passes' shader inputs.
- **Sprite validation (fixed).** Synthetic `sprite_vs` draws now receive the
  engine's constant-slot-2 uniform block at buffer index 1; a settled
  validation run reports zero draw errors.
- **Uniforms (verified).** The terrain pass's captured GL uniform block matches
  the live packed block exactly; the remaining suspect is the compressed-texture
  upload path.
- **Blocking defect.** The live map's palette is materially wrong: the
  authentic map is static across captures, while the live map is pink and the
  composite's brightness/season input (the 864x558 render target) is
  near-black. Live acceptance reports `visual_match=false`; M6 is not
  accepted and the PR is not merge-ready until this layer renders.
- **Limitation (offline).** None: the replay of the captured frame is
  byte-identical to the GL reference.

## M4 — original-GL reference and Metal presentation (2026-11-12)

Qualified by the M4 checker. The authentic Simple loading draw was captured
from the original OpenGL renderer, the corresponding reconstructed Metal
output is pixel-identical under a fixed exact policy, and the reconstructed
frame is independently observable in the real EU4 window.
[Qualification report](../analysis/m4/qualification-report.md),
[checker](../campaigns/eu4-m4/accept.py), [policy](../analysis/m4/comparison-policy.json).

- **Original-GL reference.** `_GfxDraw` at caller `0xedfcd1`, mode 5, 4 vertices,
  1728×1117 BGRA8 from `GL_BACK`, `glFinish`+`glReadPixels` clean, raw SHA-256
  `69d41a88…`; an independent launch produced the same hash. The live slot-0
  vertex payload is captured and bound to the draw.
- **Reconstructed Metal output.** The live route renders the same draw at the
  engine's logical extent with the same encoder and the same live vertex
  payload (`f13dea22…`), GPU-completed (raw SHA-256 `c668b054…`).
- **Comparison.** `automation.gfx_compare` reports `pass` with 0 differing
  pixels out of 1,930,176; the only normalization is the declared lower-left to
  top-left row decode.
- **Presentation.** `presentDrawable` completed, the destination drawable
  contains the image (`6921c242…`), and the main-runloop evidence hold lets
  Core Animation commit. An independent window-server capture of the EU4 window
  (`window-21765.png`) shows the loading frame with mean |Δ| 1.311/255 against
  the drawable.
- **Checker.** The M4 checker fails closed on missing reference, GPU,
  presentation, provenance or regression evidence and pins the evidence manifest
  digest. It recomputes screenshot/drawable similarity and validates the
  after-present checkpoint; its verdict on the recorded evidence is `pass`.
- **Readback safety.** The original-GL observer saves/restores pixel-pack state
  and rejects unsupported framebuffer configurations; the final live capture
  records `restored=true`.

## M5 — live sprite draw and composition (2026-11-12)

The live Metal-backed EU4 process now reaches the next authentic original draw
after the M4 Simple text: the loading-screen sprite at `_GfxDraw` caller
`0x14f6c1b` (`CSpriteType::RenderInternal + 2271`). The probe records the
authentic engine invocation, admits `sprite-map/asset-maincode-v0` through the
real shader path, encodes it through the shared dispatcher/sprite encoder, and
completes on the GPU against the engine-owned 3456×2234 backbuffer
(`command_status=4`). The live inputs match the original: 80-byte stride-20
vertex buffer, 96-byte uniform prefix, 1235×175 texture, linear/clamp sampler
and alpha blend. The live prefix composes the frame's Simple draw and the
sprite and produces the same 157,254 changed pixels as the GL pre/post pair.

Three original entries were reconstructed to reach the draw: `_GfxBeginScene`
(preserve the current-shader clear, default blend application and return 1,
skip the GLEW attrib-disable branch), `_GfxClearTexture` (release the backend
texture and preserve header mutations), and the sprite blend descriptor
(admit the measured alpha blend). The exact seeded offline replay remains the
pixel qualification. The live prefix pre-draw still differs from the original-GL
pre-draw because the loading-screen background producer is unreconstructed, so
live composed pixel equivalence is reported separately and is not claimed. See
[the M5 qualification report](../analysis/m5/qualification-report.md),
[target](../analysis/m5/target.md) and [checker](../campaigns/eu4-m5/accept.py).

## Latest milestone — authentic Simple draw on Metal (2026-11-12)

Merged in [PR #185](https://github.com/HagbardCel/eu4-mac-perf/pull/185). Evidence: [live Simple draw](../analysis/reconstruction/simple-loading-draw/live-evidence.md); reusable workflow: [live graphics experiments](live-gfx-experiments.md).

- **Authentic engine invocation.** The pinned GOG EU4 1.37.5 x86-64 executable reached `_GfxDraw` at caller RVA `0xedfcd1` (`CEU3Graphics::ShowBasicLoadText`). The recorded status is `draw_reached=true`.
- **Metal draw encoding and GPU execution.** The shared Simple encoder accepted the admitted shader, vertex, constant, texture and viewport state; the command buffer reached `MTLCommandBufferStatusCompleted`. The recorded status is `draw_encoded=true` and `gpu_completed=true`.
- **Pixels captured from the engine-owned backbuffer.** The checkpoint read back the 3456×2234 `BGRA8Unorm` target (30,882,816 bytes). The same raw SHA-256 was produced by independent launches; `pixels_captured=true`.
- **Presentation not demonstrated by this checkpoint run.** The probe stops at the draw, before later font/UI drawing and before any present. `presented=false`.
- **No original OpenGL reference equivalence yet.** No authentic original-OpenGL image exists for this checkpoint, so `reference_matched=null`. An independent CPU bilinear reference from `load_0.dds` matches the captured pixels on 23,808 samples, but that is not an OpenGL capture.

This milestone does **not** claim a fully rendered or presented game frame, and it does not establish production submission or resource-lifetime behavior.

The sections below record earlier evidence and remain as history; they do not override this milestone.

## Closed

- **Phase C intrusive profiling and forensic investigation** — complete. Do not repeat live intrusive capture (`run --diagnostic-only`) merely to collect more diagnostic data.
- Sealed record: [Phase C capsule report](../results/20261003T210202Z-intrusive-diagnostic/report.md). Offline verification: [`analysis/frame-model-verification.md`](../analysis/frame-model-verification.md).

## Still usable (within qualification limits)

- **R-mode** segments and structural evidence from the Phase C capsule support baseline and contract interpretation.
- **C-mode** absolute timings are **not** quantitatively qualified for production claims (same tier as post-WP11 / Tier-1 admission failure).
- **Tier-1 / WP11** (`33339073`, failed v4 admission): profiler absolute timings are not production-qualified. See [live diagnostic measurement contract](live-diagnostic-measurement-contract.md) and [post-WP11 diagnostic roadmap](eu4-live-diagnostic-roadmap.md).

Future **targeted** measurements (including further R0-M work) remain possible when separately justified; this does not reopen the Phase C investigation.

## R0-M integrated session (`20261012T210806Z`) — partial only

[Results index](../results/INDEX.md), [validation](../results/20261012T210806Z-r0-measurement/validation.md), [R0-M protocol](r0-measurement.md):

- CPU reconciliation obtained in epoch A.
- Drawable census **not evaluated** (`unsupported_drawable` readback policy).
- Automated stack attribution failed on an unsupported `sample` formatting variant.
- Acquisition status **`incomplete_gates`**; frame staticity and lossless-skip candidates were **not** evaluated.

## Current technical direction

- **L3.2b-II fail-closed completion infrastructure (2026-11-09).** The completed L3.2b-I checker is frozen by its manifest; the new immutable revision is `automation/gfx_draw_accept_v2.py`. It classifies every required draw input as `validated`, `provisional`, `missing` or `invalid`, so an established field without a trusted validator can never pass. Structurally valid evidence for a provisional field produces a durable checkpoint bundle under the workflow root; a partial checkpoint is a qualification work order, not L3.2b acceptance. Contract versions are proposed in a separate campaign, executed in a separate process, and reviewed by an independent reviewer with field-specific adversarial cases. The coordinator `automation/gfx_l32b_complete.py` chains checkpoint validation, snapshot, qualification, review, integration and continuation; the completion campaign uses two identities (`candidate_base_commit` and `trusted_contract_commit`) and one global launch ledger. The bootstrap campaign is [campaigns/eu4-l32b-bootstrap](../campaigns/eu4-l32b-bootstrap/) with the frozen acceptance suite `tests/test_gfx_infra_frozen.py`; the completion template is [campaigns/eu4-l32b-complete](../campaigns/eu4-l32b-complete/). No EU4 launch, Metal workload or pixel claim is made by the infrastructure. See [decision 0004](decisions/0004-l32b-autonomous-completion.md).
- **Live operation capture and Metal replay (2026-11-08).** `python3 -m automation.live_gfx capture` produced one authentic original-EU4 `_GfxSetViewPort` capture (session `f975f721-ec09-4a57-87b3-4e7a53fb3243`, pid 43379, seven invocations, no overlap, complete integrity). The first invocation is the `CGraphics::InitGfx` call at `0x149b21a` (return RVA `0x149b21f`); the other six are the `CGraphics::Setup2dGraphics` callsite at `0x149d046`; all run on the init thread. The unchanged `gfx-startup/v0` context is paired with the new operation sidecar at [analysis/gfx-ops/v0/capture](../analysis/gfx-ops/v0/capture) ([note](../analysis/gfx-ops/v0/note.md), [contract](../analysis/gfx-ops/v0/comparison-contract.md)). `python3 -m automation.live_gfx replay` executes the captured initialization and all seven operations through the existing Metal shadow worker; all six viewport words match for every call. A deliberate swap in `eu4_gfx_set_viewport` produced a precise `viewport[0]`/`viewport[1]` mismatch (original 1117/1728, Metal 1728/1117), and the restored implementation passed again. A pass establishes only `function_cpu_state` for the captured invocations, not GPU state, pixels, or complete engine-path equivalence. Worker failure, missing evidence, unsupported ordering, and declined qualification are distinct verdicts and never become a false pass.
- **Authentic Simple draw probe (2026-11-08).** One operator-authorized passive original launch used `python3 -m automation.live_gfx capture --target _GfxDraw --target _GfxSetPrimitiveType --until-target-draw --caller-rva 0xedfcd1 --timeout 120 --authorize-live` (session `d5af7baf-cc1e-4def-a5f4-96e1d5325641`, pid 34926). The first retained `_GfxDraw` is `call_id` 4 at caller `0xedfcd1`, with mode 5, incoming `esi` 0, first 0, effective count 4 from `master+0x64`, a non-null shader, and a preceding `_GfxSetPrimitiveType(4)` at `call_id` 3. This confirms Decision 0003's predicate on the passive original path; the earlier observed zero is consistent with the Metal draw shim, not the original loading screen. The capture is [analysis/gfx-draw/v0/probe/capture](../analysis/gfx-draw/v0/probe/capture), the packet is [analysis/gfx-draw/v0/probe/draw-packet.json](../analysis/gfx-draw/v0/probe/draw-packet.json), and the note is [analysis/gfx-draw/v0/probe/note.md](../analysis/gfx-draw/v0/probe/note.md). The packet is `incomplete`/`packet_ready=false`: it establishes the draw invocation, primitive semantics, viewport, default render target and structural vertex bindings, and explicitly reports shader identity/sources, vertex contents/layout, texture/sampler, constants, render state and scissor as missing. No Metal workload ran and no pixels are claimed.
- **Agent-driven L3.2b-I effective state (2026-11-08).** One OpenCode campaign (`eu4-l32b-effective-state`, model `opencode/big-pickle`) ran from the L3.2a merge `1b5a82a`, passed the new trusted draw checker on its first round, and used one authorized capture of three. The fresh capture (session `9a8a50c4-c42c-40da-b3db-9472820ce477`, pid 44927, observer `0f53b00e…`) has the selected `_GfxDraw` at caller `0xedfcd1`, `call_id` 11, mode 5, effective count 4. It establishes `vertex_buffer_bindings`, `vertex_buffer_contents` (`vb_3`, 96 bytes, stride 24) and `matrix_constant` (`cb_1`, slot 0, 64 bytes) from same-session evidence; shader/texture/sampler/attribute-layout/render-state/scissor inputs remain explicitly missing and `packet_ready=false`. The candidate is [analysis/gfx-draw/v0/l32b/effective-state](../analysis/gfx-draw/v0/l32b/effective-state/note.md); the campaign/checker are [campaigns/eu4-l32b-effective-state](../campaigns/eu4-l32b-effective-state/) and [automation/gfx_draw_accept.py](../automation/gfx_draw_accept.py); the manual review sequence is documented there. The initial review (`r1`) was superseded after the checker was corrected to bind matrix/vertex/evidence bytes to the original capture records; the existing capture was re-validated in the original pinned workspace without relaunch (`requalification.json`, final manifest `f3c87a4a…`, packet unchanged), and a fresh independent review (`r2`, `opencode/iceman-free`) returned `qualified`. No Metal workload ran.
- **First live Metal draw (2026-11-02).** [Decision 0002](decisions/0002-first-live-metal-draw.md) keeps 0001's Metal-only execution order and changes the next runtime target from a presented frame to the first eligible game-owned `_GfxDraw*`. [Decision 0003](decisions/0003-d1-draw-selection.md) selects the first `_GfxDraw` at caller `0xedfcd1` with a complete master, a non-null shader, mode 5, and an effective count of at least 3. `results/20261103T062113Z-metal-init` is pid 93416: init and `_GfxCreateVertexBuffer` succeeded, and the format-5 `_GfxInitializeTexture` stop at `0x15ec663` did not recur. The crash file `eu4_20261103_072119` records `SIGSEGV` at address `0xbc6a0ce4bf4c2646` with the EU4 frame at the return from `call _GfxCreateIndexBuffer` (RVA `0x14bfa60` in `CreateIndexBuffer`), reached from `CPdxMeshType::InitForDevice`. The first indirect call in `_GfxCreateIndexBuffer` is `callq *(%rax)` at RVA `0x15ec1a9` through `___glewGenBuffersARB`. The launcher deadline then killed the process. There is no draw receipt. `d1` is `not_started`. It is not a Decision 0003 candidate, and it is not `packet_ready`. `results/20261102T223946Z-metal-init` is pid 72943: init and `_GfxCreateVertexBuffer` succeeded, `_GfxSetDepthStencilState` returned at `0x149d061`, and the rasterizer tail jump at `0x149d07d` did not stop the process. The same thread then stopped on `_GfxInitializeTexture` at return RVA `0x15ec663` in `_GfxCreateTexture`. `blocked_at.json` records branch `compressed`, format 5, kind 0. Exit 2, launcher did not signal. There is no draw receipt and no `depth_state.json`. `d1` is `blocked_at`. It is not a Decision 0003 candidate, and it is not `packet_ready`. `results/20261102T221329Z-metal-init` is pid 69436: init and `_GfxCreateVertexBuffer` succeeded, the draw shim returned, and the same thread stopped on `_GfxSetDepthStencilState` at return RVA `0x149d061` in `CGraphics::Setup2dGraphics`. Exit 2, launcher did not signal. There is no draw receipt. `d1` is `not_started`. It is not a candidate. `results/20261102T213704Z-metal-init` is pid 62930: the same caller `0xedfcd1` reached `_GfxDraw` with a captured shader pointer `0x7f77e3c3a5d0` and `effective_count` 0. The master read is complete. `rsi` is 0 and the dword at `+0x64` is 0, so the tail would call `_glDrawArrays` with mode 5, first 0, and count 0. `d1` remains `draw_reached`. Invocation 1 is `draw_nonvisible` because the count is 0. This session does not show a later positive count at that callsite. D1b is not `packet_ready`. `results/20261102T210315Z-metal-init` is pid 59605: init and `_GfxCreateVertexBuffer` succeeded, the texture stub returned, and the same thread stopped on `_GfxDraw` at caller RVA `0xedfcd1` in `CEU3Graphics::ShowBasicLoadText`. Exit 2, launcher did not signal. That is `draw_reached`. The receipt records the original call. The shader failure returns, the clear stub, and the texture stub did not issue a GPU draw. `results/20261102T202215Z-metal-init` is pid 56537: init and `_GfxCreateVertexBuffer` succeeded, the `_GfxClear` stub returned, and the crash file records `SIGSEGV` at address `0xc` with the EU4 frame at the return from `call InternalSetTextureAndSamplerState` (RVA `0x15ecc72` in `_GfxSetTextures`). The launcher deadline then killed the process. That is not a draw. `results/20261102T200328Z-metal-init` is pid 53881: init and `_GfxCreateVertexBuffer` succeeded, the pixel-shader failure return continued, and the same thread stopped on `glScissor` at RVA `0x15ea5ec` in original `_GfxClear`. Exit 2, launcher did not signal. That is `blocked_at`, not a draw. `results/20261102T194105Z-metal-init` is pid 51420: init and `_GfxCreateVertexBuffer` succeeded, the vertex-shader failure return continued, and the crash file records `SIGSEGV` at the return from `call _GfxAddPixelShader` (RVA `0x15eb844`). The launcher deadline then killed the process. That is not a draw. `results/20261102T171241Z-metal-init` is pid 47765: init and `_GfxCreateVertexBuffer` succeeded, `BuildGLSL` returned, and the crash file records callee address 0 at the return from `call _GfxAddVertexShader` (RVA `0x15eb3e5`). The launcher deadline then killed the process. That is not a draw. `results/20261102T163915Z-metal-init` is pid 44393: init and `_GfxCreateVertexBuffer` succeeded, `_GfxCreateShader` returned an object with program name 0, and the same thread stopped on `glGetString` (`0x1F00`, `GL_VENDOR`) at RVA `0x15eff24` in `BuildGLSL`. Exit 2, launcher did not signal. That is `blocked_at`, not a draw. `results/20261102T161213Z-metal-init` is pid 41936: init and the vertex-buffer receipt succeeded, then a 3-second `sample` found the main thread blocked in `AbortHandler` waiting on the crash reporter. The sample shows callee address 0 and the crash-reporter wait, with `CreateEffectFromFileNew` at RVA `0x1511e8d` above the signal. The binary identifies that as the return from `_GfxCreateShader`; `callq *(%rax)` at RVA `0x15eaeb9` loads the qword at `___glewCreateProgramObjectARB`. The launcher deadline then killed the process. That is not a draw. A draw receipt counts only when the caller is the pinned executable and the launcher stop matches it.
- **Semantic reading, then Metal (2026-11-01).** The reading of the frozen 92 `_Gfx*` functions is recorded ([decision 0001](decisions/0001-semantic-gfx-then-metal.md)). The Metal `_GfxInit` backend for the six entries is implemented and the offline host tests pass ([note](../analysis/metal-init/pr3/note.md)). Live `_GfxInit` integration is recorded in `results/20261101T220023Z-metal-init`: init succeeded, then `glGetIntegerv` at RVA `0x15e9e20` on the init thread, and the process exited 2 without a launcher signal. PR4 replaces `_GfxReset` and `_GfxOnResetMasterContext` ([note](../analysis/metal-init/pr4/note.md)). The offline host tests pass. `results/20261101T224903Z-metal-init` `stop.json` is not the game. `results/20261101T233006Z-metal-init` is pid 76944 under LLDB: init succeeded, no `stop.json`, and the fault stop is `EXC_BAD_ACCESS` at pc 0 with return address RVA `0x15ebd02` in `GfxCreateVertexBuffer`, called from `CPdxParticleObject::OnResetDevice`. `AbortHandler` was resolved and not hit. `metadata.json` there is reconstructed evidence (`status` `evidence_only`), not a launcher result. That is verified continuation and a later fault, not a GL diagnostic and not a successful completion. The run is one operator-authorized `metal_init` session, not a CI job. PR5 replaces `_GfxCreateVertexBuffer`, `_GfxUpdateVertexBuffer`, and `_GfxDestroyVertexBuffer` ([note](../analysis/metal-init/pr5/note.md)). The offline host tests pass. `results/20261102T072350Z-metal-init` is pid 5979: init succeeded, then `glViewport` at RVA `0x149b21f` on the init thread, and the process exited 2 without a launcher signal. That callsite is `_GfxSetViewPort` from `CGraphics::InitGfx` after `OnResetDevice` returned. The create return is indirect, and the pc-0 fault at `0x15ebd02` did not recur. `results/20261102T100321Z-metal-init` is pid 14511: `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4` on the init thread, and the same process then stopped on `glViewport` at RVA `0x149b21f` and exited 2 without a launcher signal. That pair shows the particle creation path was reached and execution continued. PR6 replaces `_GfxSetViewPort` ([note](../analysis/metal-init/pr6/note.md)). The offline host tests pass. `results/20261102T120220Z-metal-init` is pid 20282: init succeeded, `create.json` again records `_GfxCreateVertexBuffer` at RVA `0x14d44e4` on the init thread, and the same thread then stopped on `glGenTextures` at RVA `0x15ec4ed` in `_GfxInitializeTexture` and exited 2 without a launcher signal. The previous stop at `0x149b21f` did not recur. From the create return at `0x14d44e4` through `InitGfx`'s return at `0x149b25c`, the image call tree is settings, the logger, strings, the stream, and the mutex, and the logger virtual at `0x149b17d` is `ctype<char>::do_widen`. That path does not reach `_GfxInitializeTexture`, so the later `glGenTextures` stop is after the viewport replacement and after `InitGfx` returned. B2 remains unfixtured and is not a prerequisite. B0 stays the last artificial startup checkpoint.
- **Metal-only backend (2026-10-31).** OpenGL is a separate reference run. Hybrid GL/Metal frames, including M1/R2b and the shared map seam, are superseded in the [rendering strategy](eu4_recommended_strategy.md). The primary graphics window is the `SDL_CreateWindow` call at RVA `0x13cc1d7` in `CApplication::CreateApplicationWindow`. The `PdxDetectWindow` call at `0x15ed55a` is a temporary settings probe and is not patched. A1 verified: the primary SDL/Cocoa window hosted a Metal layer; command-buffer completion and 4×4 clear readback succeeded in `results/20261031T200822Z-metal-boot/` (exit 0). A2 verified: offline GL–Metal pixel equivalence for synthetic Color fixtures (shader pair, vertex layout, CW and CCW culling, and the clip-z remap), which is not the historical live EU4 draw and not a live frame. Startup was observed through the `_GfxInit` GLEW checkpoint. No CI job launches EU4 for this probe. The opt-in B0 checkpoint reached `_glewInit` inside `_GfxInit` in `results/20261031T223735Z-metal-boot-bootstrap/` (exit 0). It is not a Metal backend. B1 is the default depth-stencil description from `GetDefaultDepthStencil`, checked offline as a Metal depth-stencil state. B2's selected candidate is the observed Color draw; B2 is not complete ([contract](../analysis/metal-draw/b2/color-contract.md)). The 2026-11-01 session inspected 22 indexed programs and recorded no qualifying Color identity ([note](../analysis/metal-draw/b2/color-note.md)); it does not accept B2. An offline audit of the retained traces found no draw with complete same-draw replay inputs ([audit](../analysis/metal-draw/offline-candidate.md)). That search is closed. An offline `_GfxDrawIndexed` adapter copies caller-supplied vertex records on the CPU into the existing `drawPrimitives` reference; it is not `drawIndexedPrimitives`, not an engine replacement, and not EU4 pixel equivalence. One Venice observation of the original `_GfxDrawIndexed` at `0x15eaa77` retained 64 post-readiness samples, all mode 4 with `esi` 0 and a pending uniform byte ([note](../analysis/metal-draw/gfx-draw-indexed-live.md)). That session is not a Metal draw. The replacement boundary stays `_Gfx*` ([handoff](../analysis/metal-census/v1/gfx-init-handoff.md)).
- Renderer **reconstruction**, **differential validation**, and **elimination of unnecessary rendering work** per the canonical [rendering strategy](eu4_recommended_strategy.md) ([navigation](roadmap.md)).
- **Function-first reconstruction (2026-10-18; amended 2026-10-23/24).** The acquisition gate is split: **P5a** (first real-function acquisition) and **P5b** (frame-level). The first target is `_GfxSetConstantBuffer` (21 instructions, 0 selected direct callees); RE0 (contract) and a bounded offline original-code execution pilot precede the function-oriented P4.10, and frame-level P4.11 no longer blocks P5a. The workstream is now **two-tier** — Tier 1 offline replay against qualified fixtures, Tier 2 live shadow replay in an isolated worker with the original authoritative — and **RE3 is deferred** until both tiers succeed. **PR A1**–**PR D** are implemented (PR D merged at `09223f8`). **D-live (2026-10-25):** one bounded P5a session completed — `gfx-live-20261025T155406Z`, reporter `accepted`, 64/64 qualified; manifest SHA-256 `e8914fb1…ea437`; publication [summary](../results/20261025T155406Z-gfx-live-publication/publication-summary.md). Raw `.capture` transcripts remain operator-local. First E1 replay on that corpus: **64/64 match**, promotion **`scoped`** (missing slots 3–5, `input:zero`, diversity 10/12). See [Oracle plan of record](oracle/PLAN_OF_RECORD.md) and [PR D contract](oracle/P4_INTEGRATION.md).
- **Exact-dedup recorder (2026-10-26).** Merged in PR #94 (`7b17c83`). Runtime `--max-unique` is 1..4096. Offline native and reporter tests passed. Live diversity, overhead, and any further EU IV session remain separately gated. See [exact-dedup contract](oracle/EXACT_DEDUP_V1.md).
- **Parallel E–G implementation (2026-10-24).** **E1** (Tier 1 certifier), **F1** (offline shadow worker), **G1** (second-function RE0) and **G3a** (agent adapter seam on `synth:apply`) may proceed offline from the amendment merge; **E2** prepares adversarial fixtures once the E contracts freeze. **F2**, **G2**, **G3b**, **G4** and **H1** remain gated. G3b's first real-function pilot is an offline reconstruction pilot, not an E live-grounded certificate. No package authorizes a further EU IV launch without separate operator authorization.
- **Trace and fixture stage (2026-10-26).** Offline **TR0**, **TR1**, and **INV** may start from the contract amendment; **TR2** composition follows TR1, and its QBDI-to-fixture integration follows a supported TR0 result. The schema is [`execution-observation/v1`](oracle/TRACE_V1.md). **Historical TR3 preflight gate:** live EU IV instrumentation was not authorized by that gate. Its preflight passed on 2026-10-29 at `0a24c90` (zero-skip macOS run of the two TR2 modules). The preflight-only work order is [work-orders/TR3.md](oracle/work-orders/TR3.md). The work order records prerequisites and a proposed later experiment. That pass authorizes no implementation, EU4 launch, injection, live trace, or replacement. The implementation scope is [work-orders/TR3-IMPL.md](oracle/work-orders/TR3-IMPL.md). Writing its named paths waits for that work order to merge. That gate left an EU4 run unauthorized. See [Oracle plan of record](oracle/PLAN_OF_RECORD.md).
- **Function-agnostic tracing (GEN0, 2026-10-30).** The next trace milestone is one preselected function, described by [`trace-target/v1`](oracle/GENERIC_TRACE_V1.md). The [plan of record](oracle/PLAN_OF_RECORD.md) records an operator report of the first EU4 interception: 15 instructions, eight memory operations, natural return boundary, no sealed digest, no qualified fixture. **GEN1** is the native tracer ([work-orders/GEN1.md](oracle/work-orders/GEN1.md)). **GEN2** may start offline from [work-orders/GEN2.md](oracle/work-orders/GEN2.md). **GEN3-A** may be written from [work-orders/GEN3.md](oracle/work-orders/GEN3.md); after it merges, **GEN3-B** may repair the launcher. One live EU4 launch stays separately authorized.
- **Next phase (primary capability):** ground-truth acquisition + agent-driven C++ reconstruction of the graphics stack, per the [Oracle plan of record](oracle/PLAN_OF_RECORD.md) ([program index](oracle/README.md)). The Oracle **P3** slice is complete and released (`oracle-v2-p3b-20261017`), and so is **P4/P4b** — the offline recorder plus synthetic target, then the synthetic graphics replay — released as annotated tag `oracle-v2-p4b-20261018`. **P4r-A**, **P4r-B** and **P4r-C** (the record schema and immutable evidence store; exact capture accounting, a declared capture policy, offline finalization, concurrency and bounded capture; the resource registry/closure verifier and offline R0-M protocol compatibility) and **P4b** (the independent synthetic graphics replay) are all merged. No EU IV launch is authorized until P4/P4b pass **and the applicable offline half-gate passes**; passing P4/P4b does not satisfy that gate. That gate is sliced **P4.9-P4.11** ([P4_INTEGRATION.md](oracle/P4_INTEGRATION.md)): **P4.9** — bounded resource-content capture, whole-version content semantics, content provenance, fail-closed malformed declarations and the versioned observation export — is implemented. The remaining gate is split by acquisition scope (see above): the function-oriented **P4.10** authorizes **P5a**, and **P4.11** authorizes **P5b**. **PR D** P5a acceptance evidence exists for the session above; no CI job launches EU IV.
- Consolidated rendering knowledge: [macOS rendering findings](eu4_macos_rendering_findings.md), [alternatives ranking](eu4_rendering_alternatives_ranked.md).

## Reverse engineering (PR D tooling)

- **Ghidra Phase 1 automation** is in [`reverse_engineering/`](../reverse_engineering/README.md): headless import/inventory/decompile orchestration, SQLite search CLI, and census reports.
- **Local full export** requires `GHIDRA_INSTALL` and a maintainer run of D1–D5; committed aggregate reports are integration-fixture samples until `measurement_source` is `local_ghidra_export`.

## Pinned environment

GOG EU IV v1.37.5.0 x86-64 at `/Applications/EuropaUniversalisIV`, Venice paused fixture, documented in the [operations manual](reference/profiler-and-benchmark-operations.md).
