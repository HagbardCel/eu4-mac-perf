# Present-904 GL control qualification

Dated offline recomputation from the stored seal. No EU4 launch and no new
capture. This report records what the original-API GL control does and does not
qualify; it does **not** establish renderer parity or Metal blame.

## Identity

| Field | Value |
|---|---|
| Session | `afd444f2-6478-459f-8db6-e565c49dbd63` |
| Sealed frame | `.local/m7/goal-round1/capture-c/frame` |
| Stream | `8801f037a06ed618e973f081fa5a20dca5a705124f92fca50c9d12554e3dd547` |
| Present | 904 (327 operations) |
| Draw | event 14, `_GfxDrawIndexed`, program 301, `caller_rva=0x10d4e50` |
| Extent | 1728 × 1117, BGRA8, lower-left |

## Authentic pre-draw state recovered from the seal

The frame's own ordered events establish the pre-draw color, depth and stencil
state at draw 14:

- Event 7 clears **framebuffer 0** with `flags=7` (color, depth, stencil),
  `depth=1`, `stencil=0`, `color=(0,0,0,1)`.
- No later fb:0 producer occurs before draw 14 (graph writers: `[7]`).
- Checkpoint 14 is the live pre-draw image and is byte-bound in the checkpoint
  request as `seed-137637b6…`; checkpoint 15 is `default-checkpoint-3f7126…`.
  The control manifest names seed `137637b6…` and reference `3f7126…`.
- Draw 14 tests and writes depth (`GL_LEQUAL`, depth test/write true) and
  enables multisampling (`GL_MULTISAMPLE` true).

The stored GL control already seeds the captured checkpoint-14 image and
clears depth to 1.0, so color and depth history are authentic for this draw.

## Missing observation: drawable sampling/precision

The 904 trace records only `GL_VENDOR`, `GL_RENDERER` and `GL_VERSION`. It does
not record the default drawable's `GL_SAMPLES`, `GL_SAMPLE_BUFFERS`, depth or
stencil bits, or color component bits. `GL_MULTISAMPLE` being enabled is not a
sample count. The recorder now captures those fields in the `gl.drawable`
projection of `frame-trace.json`, but this seal predates that field, so the
drawable remains **unobserved** for present 904.

## Qualification result (all four BGRA channels)

| Checkpoint | Live vs stored GL control | Alpha differences | Status |
|---|---:|---:|---|
| 15 | **5 / 1** | 0 | **unqualified** |
| 16 | **4,645 / 86** | 0 | **unqualified** |

The five-pixel residual at checkpoint 15 is not parity, and the larger
checkpoint-16 residual is not explainable by the qualifying inputs alone.
Per `automation/gfx_gl_control_qualification.py`, `metal_blame_allowed=false`.

Stored data: [qualification JSON](gl-control-904-qualification.json).

## Consequence

Do not use this control to attribute the Metal residual to shader translation,
raster state or the Metal backend. The present-904 measurements above are
historical and are preserved unchanged: they are not re-measured, and no new
capture may reuse the 904 seal.

The missing observations are the default drawable's sample count, sample
buffers, depth/stencil bits and color bits (the new recorder fields), plus the
prior-frame contents where the selected draw needs them. Those observations do
not exist in the 904 seal. Acquire them only from a newly sealed capture, then
reconstruct a new original-API control from that capture's own stream,
resources and checkpoint, and require zero differing BGRA pixels against that
capture's live GL before assigning any residual to Metal. An exact image from
the 904 seal or any other seal is not a control for the new capture.

Do not substitute a window, 10-bit or sRGB drawable and call the result a
control: the earlier window experiment produced 1,446,688 differing pixels
including 1,445,806 alpha differences.
