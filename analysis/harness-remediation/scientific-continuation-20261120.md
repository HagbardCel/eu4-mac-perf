# Scientific continuation — present 904 first divergence

Date: 20 November 2026. Offline recomputation from the stored sealed 904
case. No EU4 launch, no new capture and no Metal correction were performed.
This report stops at the exact missing observation; it does **not** assign the
residual to Metal.

## Case identity (retained, unchanged)

| Field | Value |
|---|---|
| Session | `afd444f2-6478-459f-8db6-e565c49dbd63` |
| Sealed frame | `.local/m7/goal-round1/capture-c/frame` |
| Stream | `8801f037a06ed618e973f081fa5a20dca5a705124f92fca50c9d12554e3dd547` |
| Present / operations | 904 / 327 |
| Extent / format | 1728 × 1117, BGRA8, lower-left |
| Checkpoint request | `.local/m7/goal-round1/capture-c/shadow/checkpoint-request.json` |
| Metal replay | `.local/m7/goal-round1/capture-c/shadow` |

The 904 measurements are historical and are preserved unchanged. They are not
combined with another seal as a before/after series.

## First meaningful divergence

All four BGRA channels, recomputed from the stored live blobs and Metal
intermediates:

| Checkpoint | Resource | Pixels / max | Alpha pixels | First differing pixel |
|---|---|---:|---:|---|
| 11 | tex:431 | 0 / 0 | 0 | — |
| 13 | tex:432 | 0 / 0 | 0 | — |
| 14 | fb:0 | 0 / 0 | 0 | — |
| **15** | **fb:0** | **4 / 1** | **0** | **(209, 50)** |
| 16 | fb:0 | 3,602 / 119 | 0 | (432, 0) |
| 17 | fb:0 | 7,484 / 130 | 0 | (432, 0) |

The first non-exact checkpoint is **15**, i.e. the state produced by the
`fb:0` draw at event 14 (program 301, depth test/write enabled). The first
differing byte is the green channel of pixel (209, 50), changed by one LSB.

## Stored control

| Pair | Pixels / max | Alpha pixels | First differing pixel |
|---|---:|---:|---|
| `gl-c-comp-event14` vs live checkpoint 15 | 5 / 1 | 0 | (224, 50) |
| `gl-c-comp-event15` vs live checkpoint 16 | 4,645 / 86 | 0 | (450, 0) |

The eight unique pixels that differ between live, Metal and the stored control
at checkpoint 15 are:

| Pixel | Live BGRA | Metal BGRA | Control BGRA |
|---|---|---|---|
| (10, 191) | (127,107,95,255) | (127,107,95,255) | (128,108,95,255) |
| (30, 166) | (178,142,124,255) | (179,142,124,255) | (179,142,124,255) |
| (208, 56) | (107,86,74,255) | (107,87,74,255) | (107,86,74,255) |
| (209, 50) | (100,92,82,255) | (100,92,83,255) | (100,92,82,255) |
| (209, 60) | (101,81,70,255) | (101,81,70,255) | (100,81,70,255) |
| (222, 57) | (136,110,97,255) | (136,110,97,255) | (136,109,97,255) |
| (224, 50) | (57,92,75,255) | (57,92,75,255) | (56,92,75,255) |
| (226, 58) | (148,117,104,255) | (148,117,103,255) | (148,117,104,255) |

Every difference is one LSB in one non-alpha channel. Metal and control share
only the (30,166) difference; the other differences are disjoint. This is
consistent with a sub-LSB rounding/drawable-precision residual, but it is not
proof: the stored control is unqualified, so the residual cannot be assigned
to rounding, shader translation or Metal.

## Qualification state

Under the authenticated qualification gate, the stored 904 control is
**unqualified**:

- it was produced without an executed renderer receipt and a pinned renderer
  authority;
- the 904 trace records only `GL_VENDOR`, `GL_RENDERER` and `GL_VERSION`, not
  the default drawable's `GL_SAMPLES`, `GL_SAMPLE_BUFFERS` or bit depths;
- prior-frame contents are not in the trace.

`metal_blame_allowed=false`. The five-pixel checkpoint-15 residual and the
larger checkpoint-16 residual are not parity.

## Exact missing observation

To move beyond this stop, obtain from a **newly sealed capture**:

1. the default drawable's `GL_SAMPLES`, `GL_SAMPLE_BUFFERS`, depth/stencil bits
   and color component bits at the selected draw; and
2. the prior-frame contents where the selected draw needs them.

Those observations do not exist in the 904 seal and must not be manufactured by
capturing under the historical 904 seal. The new capture must be paired with a
new original-API control rendered from that capture's own stream, resources and
checkpoint, with an executed renderer receipt and a pinned renderer authority.
Only zero differing BGRA pixels between that control and that capture's live GL
permit a Metal residual to be considered; otherwise the next missing
observation is named and the experiment stops again.

## Decision

- No Metal correction was attempted.
- No independent acceptance test was run.
- No speculative shader sweep was performed.
- Attribution remains **inconclusive**; the next experiment is the bounded
  new-seal capture plus its own control described above.

## Measured work

| Quantity | Value |
|---|---|
| New EU4 launches | 0 |
| GPU service time | not measured (offline recomputation) |
| Cache hits | not applicable (no shader cache used) |
| Diagnostic attempts | 1 offline recomputation |
| Shader sweeps | 0 |
| Wall time (offline recompute) | a few seconds |
