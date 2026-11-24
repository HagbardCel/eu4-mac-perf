# M7 event-1 investigation

The independent checker first diverges at **event 1**, `_GfxDrawIndexed` at
`0x10d4e50`, program **310**, framebuffer **6**, texture **433** level 0.
The retained precise-math candidate has **20 / 1,930,176** differing RGB pixels
with a maximum channel error of **1**. Event 0 matches and the exact prefix is
`[0, 0]`. The event-1 cause remains unresolved.

## Evidence identities

Links into `.local` resolve in the operator checkout.

- **Original OpenGL.** Trusted seal `47590918-d63a-4798-9737-df00285ad51b`,
  frame 1, present 72. The trusted root digest is
  `b3e3d7b5d1e4cefe814f460c99a19c32011242d100a8b0c5bdd97aa9e485756c`.
  The sealed event stream supplies the indexed draw, shader and resource
  inputs, and state. Its `tex:433` prefix observations include post-clear
  event-0 color and depth immediately before the draw, and event-1 post-draw
  color and depth. Color is RGB8 transported as lower-left BGRA8. These
  snapshots establish attachment values and constrain coverage. Contributing
  fragment identity, UV2, texture sample values and pre-conversion fragment
  color remain unobserved. [Exact-prefix report](m7-exact-prefix-report.md) ·
  [sealed observations][gl] · [trusted manifest][trusted].
- **Candidate Metal.** Pinned commit `9d92a0c03415f4fedc233016ab54428039b309e0`
  plus the retained precise-default change. The five-file renderer source
  closure hashes to `026925b7d25a1fdc7f7138907a50135a336927947430cfcdb5963aee3c0f8d4a`.
  The only modified renderer input is `benchmark/eu4_gfx_frame_replay.mm`
  (file SHA-256 `a756652c8d9ba4773400348aaa99f0b14523a465ab80d99b40b5ff6732027687`,
  Git blob prefix `9a1c53e4`). The latest qualification receipt binds the
  whole worktree at validation
  (`62ac011a59c0587e0b3e20b231410ee66ffffa86d3777ac6a9ab982da4e711c0`)
  and records `fail` at event 1. Worktree digests also cover research edits.
  [Qualification receipt][qualification] · [source history](m7-operator-runbook.md#historical-runs) ·
  [receipt-bound Metal images][canonical].
- **Diagnostic controls.** Historical Metal variants and reconstructed GL
  controls explain the behavior of those interventions on their measured
  inputs. Original GL pixel and depth authority stays with the seal. The
  original-API control from seal `90825686` is unqualified and addresses a
  different draw. [GL control qualification](gl-control-qualification-90825686.md).

## Checkpoint accounting

The table supersedes the rate in [diagnosis §28a][diagnosis]. Using the trusted
manifest and producer receipt, recheck the GL blobs and Metal BGRA8 readbacks
and join post-event checkpoints by seal, resource, timing and dimensions.
Compare the three color bytes in their lower-left layout. Let `D[e]` be the
current RGB mismatch mask. New divergences are `D[e] − D[e−1]` and persistent
divergences are `D[e] ∩ D[e−1]`. The GL RGB-change mask is derived by comparing
consecutive authentic GL checkpoints and coincides here with the GL depth
change mask. It measures observable attachment changes. Writes that preserve
attachment values can escape it, and a pixel can remain divergent across
checkpoints even if a later fragment writes it.

| Event | Cumulative mismatch | New divergence | Persistent divergence | GL RGB changed | New divergence on GL RGB changes | Corrected rate on GL RGB changes | Historical rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0 | — | — | — | — | — | — |
| 1 | 20 | 20 | 0 | 101,568 | 20 | 0.0001969 | 0.000197 |
| 2 | 120 | 100 | 20 | 53,923 | 100 | 0.001854 | 0.002225 |
| 3 | 236 | 116 | 120 | 46,807 | 116 | 0.002478 | 0.005042 |
| 4 | 1,385 | 1,149 | 236 | 309,021 | 1,149 | 0.003718 | 0.004482 |
| 5 | 1,642 | 257 | 1,385 | 163,324 | 257 | 0.001574 | 0.010054 |
| 6 | 2,182 | 540 | 1,642 | 157,248 | 540 | 0.003434 | 0.013876 |
| 7 | 2,223 | 41 | 2,182 | 110,760 | 41 | 0.0003702 | 0.020070 |
| 8 | 3,194 | 971 | 2,223 | 412,844 | 970 | 0.002350 | 0.007737 |
| 9 | 4,491 | 1,297 | 3,194 | 354,190 | 1,297 | 0.003662 | 0.012680 |

The historical rate divided cumulative mismatches by the current event's
change-mask size. The corrected numerator includes only newly divergent
pixels inside that mask. All prior mismatches persist through the next
checkpoint, and one new event-8 mismatch lies outside the GL change mask.
The observed rates do not measure mismatches per painted fragment.
[Historical JSON][structure] · [producer receipt][canonical].

## Experiments

| Hypothesis | Experiment | Prediction | Observation | Conclusion | Evidence | Next discriminating test |
| --- | --- | --- | --- | --- | --- | --- |
| Compile math and vertex calculations affect candidate output | Precise vs fast math, reciprocal substitution and position variants | A change in lowering can change checkpoint pixels | Cumulative events 1/2/3 are 20/120/236 precise, 23/164/301 fast and 18/99/193 reciprocal. Precise math fixes 9 event-1 pixels and introduces 6. Reciprocal fixes 3 and introduces 1 | Supported | [Diagnosis §§5d, 21, 26–27][diagnosis] · [audit](run-60m03-evidence-audit.md) · [reciprocal receipt][rcp] | Compare original and Metal fragment inputs, including UV2, with verified correspondence |
| One-pixel axial image displacement explains the event-1 residual | Compare all-RGB neighbor equality to a gradient-range-matched null | A consistent direction shows excess equality | Each of the four tested directions has 5/20 or 9/20 hits, below its null rate of 0.529–0.555 | Falsified within tested scope | [Shift null model][shift] | If fragment coverage differs, identify the affected primitives and coverage/depth decisions |
| A tested constant UV2 offset or vertex lowering restores exactness | Signed offsets, including ±1 ULP, and division/position variants | The correct intervention restores the prefix | The tested variants still differ. The +1 ULP x offset improves counts at all three cumulative checkpoints and changes the residual set | Falsified within tested scope | [Diagnosis §§21, 26–27, 29][diagnosis] | Measure the signed distribution of corresponding original and Metal UV2 values |
| Bilinear filtering, LOD or texture decode accounts for the residual | Metal LOD-0 clamp, recorded GL-decoded upload, rounded software decode with a live control | A correction changes or removes the event-1 residual | Clamp and rounded decode leave the tested event-1 image unchanged. Recorded-decoded upload worsens the earlier fast-math baseline. Later events respond to the controls | Unresolved | [Diagnosis §§4–5a, 11][diagnosis] | Compare per-sample values at verified equal UV2 under captured sampler state |
| Coverage, depth or discard selects a different fragment | Remove the Metal height discard and compare depth readbacks | The tested discard removal changes residual pixels if it explains their Metal output | Removal leaves event 1 byte-identical. Depth differs by up to two measured quanta and depth steps also occur at 98.5% of drawn pixels. The winning GL and Metal fragments are unverified | Unresolved | [Depth-edge report][depth] · [diagnosis §28][diagnosis] · [depth audit](run-60m03-evidence-audit.md#depth-diagnostic-and-its-correction) | Establish coverage and the color-contributing fragment with depth, discard and writes accounted for |
| Fragment arithmetic or RGB8 conversion accounts for the residual | Fixed 4×4 ordered dither sign check and scalar ULP sensitivity estimates | A fixed signed additive dither predicts one error sign per lattice cell/channel | Three cells have opposite signs for the same channel. The dither model fails. Arithmetic and conversion values were unobserved | Unresolved | [Diagnosis §§16–17, 26d][diagnosis] | If samples agree, compare pre-conversion outputs, then the RGB8 conversion |
| The historical GL control can attribute the offscreen event | Attempt the default-checkpoint qualification path | It binds and reproduces the authentic offscreen attachment | The current qualifier cannot bind this `tex:433` observation. Existing authentic color and depth are still available | Falsified within tested scope | [Diagnosis §§6a, 19][diagnosis] · [control qualifier](../../automation/gfx_gl_control_qualification.py) | Inspect existing event-1 UV2 probe and the sealed pre-/post-draw evidence for attribution |

The gradient enrichment in §28f is descriptive. The gradient comes from the
quantized output image. The ratios in §29a use newly divergent counts and
three successive checkpoints of one target with differing scene content. The gradient-normalized ratios do
not establish a numerical law, statistical significance or a sampling-stage
cause. The signed perturbations establish sensitivity. A systematic GL UV2
bias, a split of the residual into interpolation-related fractions, and the
exclusions of rasterization, texture sampling, arithmetic or conversion remain
unsupported. Neighbor matches and small count changes also leave spatial
dependence and the rate of accidental matches unresolved. [Historical
diagnosis][diagnosis] · [residual coordinates][residual].

**Next discriminator.** Establish which fragments contributed color at the
event-1 residual pixels and a small matched control sample. Compare their
original GL and Metal UV2 bits if correspondence can be verified. If it cannot,
report the missing coverage, depth, discard or write evidence first. Reuse the
[existing UV2 probe](../../automation/gfx_uv2_probe.py). Avoid another exact
validation for documentation-only changes or exact rollbacks. Avoid repeating
the clamp, decode, discard, offset and lowering sweeps, or the unqualified
control, until an input or stage observable changes.
[Validation reuse audit](run-60m03-evidence-audit.md#trusted-validation-audit).

## Numerical observation coverage

This assessment applies to seal `47590918`. The offscreen color and depth
observations are in the sealed prefix file. The default-framebuffer GL
control qualifier serves a different interface. [Sealed observations][gl] ·
[prefix loader](../../automation/m6_frame.py) · [GL control qualifier](../../automation/gfx_gl_control_qualification.py).

| Observation | In the authenticated capture? | Can be obtained offline? | Needs a new controlled capture? |
| --- | --- | --- | --- |
| Event-1 final RGB8 output | Yes, post-event `tex:433` | Yes, digest-bound GL color and receipt-bound Metal checkpoint | No. [Observation][gl] · [candidate receipt][canonical] |
| Original pre-event color | Yes, event-0 clear on the same attachment | Yes, last observed contents before the event-1 draw | No. [Observation][gl] · [sealed observation loader](../../automation/m6_frame.py) |
| Original pre-event depth | Yes, event-0 post-clear depth for renderbuffer 3 | Yes, sealed float32 depth plane | No. [Observation][gl] · [sealed observation loader](../../automation/m6_frame.py) |
| Original fragment coverage | No contributing-fragment record | Color and depth changes give a derived lower bound on writes | Yes for contributing coverage. [Checkpoint accounting](#checkpoint-accounting) · [GL observer](../../benchmark/eu4_gfx_frame_trace.c) |
| Original fragment UV2 | No | Isolated GL sidecars give conditional values if available. The sidecar bytes sit outside the seal and the pass bypasses depth and original discard | Yes for attributable UV2. [GL observer](../../benchmark/eu4_gfx_frame_trace.c) · [probe](../../automation/gfx_uv2_probe.py) |
| Metal fragment UV2 | Candidate-side only. No event-1 probe payload is preserved with the canonical replay | Existing opt-in replay probe can produce conditional values in an isolated pass | No GL capture for the Metal probe. Matching contributing fragments requires additional evidence. [Metal replay](../../benchmark/eu4_gfx_frame_replay.mm) · [harness](../../docs/harness.md#event-1-uv2-diagnostic-probe) |
| Individual sampled texture values | No. Captured textures and sampler state are inputs | A reconstruction can produce derived values | Yes for values from original GL, if UV2 and correspondence agree. [Event evidence](../../automation/gfx_event_evidence.py) · [GL replay inputs](../../automation/gfx_gl_replay_input.py) |
| Pre-quantization fragment color | No | The seal contains converted RGB8 | Yes if comparison reaches this stage. [Observation format scope](../../automation/gfx_observation.py) · [sealed observations][gl] |

The probe's `localize` command reads the preserved evidence directly. Invoke
from the infrastructure checkout.

```sh
python3.14 -m automation.gfx_uv2_probe localize \
  --frame .local/m7/event20/trusted-copy/47590918/frame \
  --replay .local/dsh/m7-event3/workspace/.local/m7/event3/exp-canonical-check/ind-canonical \
  --renderer-sha256 787670d93816de787b513289388107b5f4e3615844e58b13343c029362dd15c7
```

The command verifies the original seal and the producer receipt's checkpoint
binding. `--renderer-sha256` compares an independently supplied expected
executable digest with the producer receipt. The command uses the recorded
build identity and does not rebuild or independently verify the renderer source
closure. Current v3 receipts must agree with the native compile options in
`replay.json` and pass recipe validation. The cited historical replay remains
usable for RGB8 comparison with `compile_mode_provenance=unverified`.

The report gives the 20 existing residuals and up to eight nearby
`rgb_match_control` pixels, selected in lower-left row order within the
observed GL RGB-change region. The region comes from the bound event-0 and
event-1 color snapshots. It supplies no primitive or sample correspondence.
Missing or unbound predecessor planes are listed explicitly. Without bound
pre-draw color the report omits the controls. Signed differences are Metal
minus OpenGL. RGB8 is the first observed difference. The contributing-fragment
stage remains unresolved, and the existing `compare` command reports isolated
coverage and UV2 conditionally. [Producer receipt][canonical] ·
[offline harness](../../docs/harness.md#event-1-uv2-diagnostic-probe).

### Operator request for the next observation

- Capture one draw, event **1**, program **310**, framebuffer **6**, RGB8
  texture **433** level **0**. Use one controlled authorization and bound
  prefix observations to events **0–1**. Preserve the unmodified original GL
  post-draw color plus the pre-draw color and depth, draw inputs, attachment
  sample count and write state. [Controlled capture workflow](../../docs/harness.md#event-1-uv2-diagnostic-probe).
- Before spending the authorization, extend and review the existing probe to
  observe which primitive and sample contributes color, with coverage,
  pre-draw depth and test decision, original discard, write mask, blend and
  logic state accounted for. Retain enough fragment order to disambiguate
  overlap. Report UV2 float32 bits for the attributable fragment at the
  residual coordinates and up to eight RGB-match controls. Extend the opt-in
  branch in the existing GL observer and keep the original draw and readback
  as the reference. The existing `--uv2-probe` produces a control and
  isolated UV2 sidecar. Its depth bypass, replacement fragment shader and
  unsealed sidecars leave this request
  unsupported today. [GL probe implementation](../../benchmark/eu4_gfx_frame_trace.c).
- Expect a fresh `frame-seal.json`, the prefix observation manifest and blobs,
  and digest-bound sparse observation records containing seal, event and target
  identities, sample/primitive labels and raw UV2 bits. Independently recheck
  the new seal, observation payload digests, original-shader behavior and
  readback layout. Pair observations only within the new seal and pin the Metal
  renderer identity before recomputing the residual and control coordinates.
  Diagnostic re-execution stays conditional if fragment correspondence or
  attachment behavior remains unproven. The independent exact-prefix checker
  continues to use the sealed original color.

Once fragment correspondence is established, compare UV2 first. A difference
directs investigation to vertex outputs, interpolation and perspective
precision. Agreement advances to sampled values, then pre-conversion color,
then RGB8 conversion. A coverage or contributing-primitive difference directs
investigation to rasterization, depth and discard. Missing evidence leaves the
next comparison unresolved.

## Compile-option identity and the retained candidate

For translated shaders, `EU4_FRAME_REPLAY_FAST_MATH` accepts exactly `0`
(precise) or `1` (fast). Unset uses the native renderer's default. The retired
`EU4_FRAME_REPLAY_PRECISE_MATH` is rejected, including when both names are
present. Main retains fast math by default. The standalone producer's v3
recipe binds the effective mode and renderer default reported in
`replay.json`. A case baseline also checks the requested effective mode
before reuse. Historical receipts still support pixel and checkpoint
inspection. Exact correction acceptance requires verified native compile-mode
provenance. The independent
exact-prefix checker scrubs ambient `EU4_*` switches and binds the candidate
source closure, which includes its native default.

After infrastructure review, port the native changes to
`benchmark/eu4_gfx_frame_replay.mm` in the pinned candidate. That file is
within the sidecar's allowed `benchmark/**` scope.

1. Set `kDefaultShaderFastMath = NO` to preserve the candidate's canonical
   precise-math baseline. Port `resolve_shader_fast_math` and resolve the mode
   once per frame into `EU4MetalReplayState.shader_fast_math`.
2. Use that mode for both the function cache key and
   `MTLCompileOptions.fastMathEnabled`. Remove the candidate's separate
   `getenv("EU4_FRAME_REPLAY_FAST_MATH")` block in `function_for` and the old
   options-key helper. Built-in debug functions keep their fixed compile options.
3. Port the native `shader_compile_options` report for success and failure
   results. Use reviewed infrastructure for v3 receipt generation. Record the
   new renderer-source closure before comparing outputs with historical
   results, which retain their original identities and receipts.

The existing campaign objective includes this index's infrastructure path in
the prepared goal text. The pinned workspace can read that path in the
reviewed infrastructure checkout. [Campaign
objective](../../campaigns/eu4-m7-event20/objective.md#evidence-convention).

[gl]: ../../.local/m7/event20/trusted-copy/47590918/frame/prefix-observations.jsonl
[trusted]: ../../.local/m7/event20/trusted-copy/47590918/trusted-evidence.json
[qualification]: ../../.local/m7/event3/launches/m7-event3.h47xoaox/run/sessions/session-78977c9f-8392-479a-ae6c-ec494e53561b/qualification.json
[canonical]: ../../.local/dsh/m7-event3/workspace/.local/m7/event3/exp-canonical-check/ind-canonical/producer-receipt.json
[rcp]: ../../.local/dsh/m7-event3/workspace/.local/m7/event3/exp-div-rcp/ind-rcp/producer-receipt.json
[diagnosis]: ../../.local/dsh/m7-event3/workspace/analysis/m7/reports/event3-prefix-diagnosis.md
[shift]: ../../.local/dsh/m7-event3/workspace/analysis/m7/reports/event1-shift-null-model.json
[structure]: ../../.local/dsh/m7-event3/workspace/analysis/m7/reports/event3-residual-structure.json
[depth]: ../../.local/dsh/m7-event3/workspace/analysis/m7/reports/event1-depth-edge-evidence.json
[residual]: ../../.local/dsh/m7-event3/workspace/analysis/m7/reports/event1-residual-pixels.json
