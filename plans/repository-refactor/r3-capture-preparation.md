# R3 authentic capture preparation

The selected workload is the color-only offscreen draw reached from
`_GfxDraw` caller `0xed7ba9` in the retained M7 event 273 seal. The
selection document pins the caller, both shader digests and the attachment
format and extent. Event and object numbers are local to each seal.
[Protected workload identity](r3-offscreen-selection.json)

## Retained evidence

| Workload | Assessment |
| --- | --- |
| Window draw 18 from seal `90825686…` | The sealed evidence lacks default drawable equivalence and lossless D32 depth. |
| Offscreen draw 273 from `47590918…` | Event 275 has a lossless later texture inventory. The seal lacks a verified texture lifetime through that read and an immediate attachment observation. |
| Offscreen draw 14 from `47590918…` | The later RGBA texture is byte-identical to the verified full white initialization. Every channel byte is 255. |
| Same caller at draw 286 from `2da0a8fc…` | Lifecycle history verifies. The retained target has incomplete direct observation and stencil applicability. |
| `afd444f2…` and `1efc8880…` | The inventory lacks a direct observation of the selected offscreen workload. |

The authorized alternative is event 273 because the window reference is
inapplicable. Qualification requires one new seal with the complete
observation and retains each earlier finding under its own identity.
[Window control report](../../analysis/m7/gl-control-qualification-90825686.md)

## Required observation

Capture a unique call with the pinned shader and resource identity. Require
the same complete single-sample texture attachment at entry and exit,
COLOR_ATTACHMENT0 draw and read bindings, direct lossless pre/post color,
and checked depth and stencil absence. The selected original observation
refuses a GL error. The capture pins the process architecture and GL
implementation, driver and context. Candidate execution uses that
architecture and verifies the observed GL identity.

The engine uploads dirty uniforms and prepares vertex attributes before its
final `glDrawArrays` call. The existing state recorder queries effective
linked resources at the draw boundary. The evaluator compares the actual
candidate's uniform values, vertex range, required sampled texture level,
sampler parameters, framebuffer, applicable raster state and attachment
effects. The pre/post observer keeps reference bytes on the verifier side
and restores temporary bindings and pixel-pack state. The verifier requires
exact equality and a separate correctly initialized omission with a
different attachment. [Original draw dossier](../../analysis/reconstruction/gfx-semantic/v1/draw/_GfxDraw.md)

Operator authorization is separate from this contract. Before requesting one
launch, verify the matching offline fixture and the governed execution path.
A capture-health failure or incomplete seal ends the capture attempt.
