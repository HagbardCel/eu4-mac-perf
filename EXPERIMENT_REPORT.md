# Experiment report — authentic Simple loading-screen draw on Metal

DSH session: `session-721a5c50-eb9f-4558-8a52-4803f5b00efd`
Branch: `experiment/dsh-full-reconstruction`
Base commit: `1790798` (Merge PR #182, `dsh/qualification-hardening`)
Date: 2026-11-11

## Objective

Reconstruct the interconnected rendering functionality needed for the authentic
`Simple` loading-screen draw (`_GfxDraw` at caller RVA `0xedfcd1` in
`CEU3Graphics::ShowBasicLoadText`) and implement a faithful Metal equivalent,
validated by real Metal execution.

## Starting state

The repository already had: a qualified L3.2b-I draw packet with the authentic
vertex buffer and matrix, a `Simple` MSL translation and pipeline builder, Metal
vertex-buffer and texture modules, and the Metal boot backend with an offscreen
target and readback. What was missing was the actual draw: `_GfxDraw` is
replaced by a capture shim that skips the GL call, the pipeline used a
40-byte vertex stride, and nothing joined shader, vertices, matrix, texture,
blend, sampler and viewport into one encoded draw.

## Discoveries

1. **The authentic vertex layout is stride 24, not 40.** The packet's 96 bytes
   decode as `float3 position @0`, `float2 uv @12`, `uchar4 colour @20`
   (`0xffffffff`). The previous pipeline read a `float4` position at 0, a
   `float2` uv at 16 and a `float4` colour at 24 with stride 40, which would
   have consumed the UV as position `w` and read the colour from beyond each
   vertex.
2. **The matrix is `diag(1/512, 2/661, 1, 1)`.** The 661 is
   `floor((window_height << 10) / window_width)` computed in
   `ShowBasicLoadText`; the quad fills the width and crops 16 % of the height.
3. **The constant buffer object needs no new Metal module.** `_GfxCreate/Update/
   SetConstantBuffer` are pure CPU operations; at draw time the 64-byte matrix
   is at `master+0xf0 → object+8`, with the object's word count at `+0`.
4. **The draw is a 4-vertex triangle strip** (`master+8 == 5`, `rsi < 1` so the
   count comes from `master+0x64 == 4`), not triangles. The P5.2a note's mode
   remark is confirmed: the callsite executes `_GfxSetPrimitiveType(4)`, which
   maps to GL mode 5.
5. **The loading images are 2048×1536 BGR24 DDS**, and the engine applies a
   power-of-two UV correction only on its no-NPOT branch; the capture's UVs are
   0/1, so that branch was not taken.

## Implementation changes

| File | Change |
| --- | --- |
| `benchmark/eu4_gfx_shader.mm` | Decode the MSL position as `float3` and build the implicit `w = 1`; vertex descriptor now `Float3@0`, `Float2@12`, `UChar4Normalized@20`, stride 24 (was `Float4@0`, `Float2@16`, `Float4@24`, stride 40). |
| `benchmark/eu4_gfx_buffers.h/.mm` | `eu4_gfx_vertex_buffer_for_header()` — borrowed `MTLBuffer` for an engine vertex header. |
| `benchmark/eu4_gfx_texture.h/.mm` | `eu4_gfx_texture_for_header()` — borrowed `MTLTexture` retained by `_GfxInitializeTexture`; `_GfxSetTextures` now records the unit-0 engine header in the backend (falling back to the master's `+0x98` default), and `eu4_gfx_bound_texture()` resolves it for the encoder. |
| `benchmark/eu4_gfx_simple_draw.h/.mm` | New encoder: reads the engine master (`mode`, `count`, `shader`, slot-0 vertex header/offset/stride, constant slot 2), refuses unsupported inputs, creates the `Simple` pipeline through the shader registry, copies the matrix, binds vertex/uniform buffers, fragment texture and sampler, sets the viewport, and submits a BGRA8Unorm pass. |
| `benchmark/eu4_gfx_init.h` | Backend property for the current unit-0 texture header. |
| `benchmark/eu4_metal_boot.py` | The new module is compiled into `libeu4_metal_boot.dylib`. |
| `tests/gfx_simple_draw_host.mm` | Real-Metal host: authentic packet + optional original DDS, CPU reference sampling, flip discriminator, alpha-blend boundary, fail-closed cases. |
| `tests/test_gfx_simple_draw.py` | Binds the packet hashes, builds/runs the host, covers original-DDS and hermetic-synthetic paths. |
| `analysis/reconstruction/simple-loading-draw/note.md` | Evidence-labeled reconstruction note. |

The resource chain is now exercised end to end: `_GfxCreateShader` →
`_GfxAdd*Shader` → `_GfxLinkShader` → `_GfxSetShader`, `_GfxCreateVertexBuffer`
→ master slot 0, the original constant-buffer objects, `_GfxSetTextures` →
backend binding, and the encoder. Blend state is
`SRC_ALPHA`/`ONE_MINUS_SOURCE_ALPHA`, sampler is linear/linear/linear with
repeat addressing (both from the installed `simple.shader`).
The render target is `BGRA8Unorm` (project-derived P5.1 default), the viewport
is `0,0,1728,1117`, and no depth attachment is used.

## Tests

### Failed first, then repaired

1. `python3 -m unittest tests.test_gfx_simple_draw` could not import under
   Python 3.9 (`Path | None`); replaced with `Optional[Path]`.
2. The host failed at `_GfxInit`: `write_success` needs a Metal events
   directory; added `eu4_metal_events_set_dir(mkdtemp(...))`.
3. `eu4_gfx_configure_offscreen` refused a synthesised master; switched the
   test to the real master returned by `_GfxInit` and set the shader through
   `_GfxSetShader`.
4. `_GfxClear` refused because the viewport image did not cover the attachment
   and the colour mask was zero; set the authentic `1728×1117` viewport image
   through `eu4_gfx_set_viewport` and the default `0x01010101` mask.
5. The flip discriminator failed on the original image with a 5×5 grid (the
   image is locally smooth); replaced it with a 40×40 grid and a
   `flip_differences` threshold.

### Passing

```
$ python3 -m unittest tests.test_gfx_simple_draw -v
test_authentic_packet_bytes ... ok
test_hermetic_synthetic_texture_draw ... ok
test_original_loading_screen_draw ... ok
Ran 3 tests ... OK
```

Direct run on the original asset:

```
texture=original-dds 2048x1536
ok checked=1600 worst=0.0028 flip_differences=368 blend_bad=0
done
```

1600 pixels of the 1728×1117 Metal readback match CPU bilinear sampling of the
same DDS through the authentic matrix within 0.0028 (≈0.7/255); the vertically
flipped reference differs at 368 samples, so orientation is actually tested.
The 64×64 alpha-blend pass reads `(127,0,128)` within 3/255 on all 4096 pixels.

Regression suite:

```
python3 -m unittest tests.test_gfx_contracts tests.test_gfx_entry_registry \
  tests.test_gfx_draw_packet tests.test_gfx_shader tests.test_gfx_buffers \
  tests.test_gfx_texture tests.test_gfx_init
Ran 56 tests ... OK
```

`benchmark/eu4_metal_boot.py:build_library()` builds
`benchmark/.build/libeu4_metal_boot.dylib` with the new module.

## Remaining uncertainties / blockers

- The exact loading-screen file used by the captured invocation is not
  recoverable (randomised index; identity, not contents, was captured). The
  original-DDS test uses a real installed asset (`load_0.dds`, SHA-256
  `621f3caa…`), not the exact frame.
- Sampler object, depth/stencil state, scissor and sRGB are not captured. The
  asset's sampler/blend contract and the project-derived BGRA8Unorm target are
  used; depth is irrelevant for this full-screen constant-z quad but remains
  unproven; scissor defaults to the full attachment.
- Vertex colour byte order (RGBA vs BGRA) is unobservable with `0xffffffff`.
- The encoder is compiled into the Metal boot dylib but `_GfxDraw` is not yet
  routed to it. Enabling it live needs an authorized original launch and
  validation of game-backbuffer/present ordering (the `_GfxClear` command buffer
  is submitted separately). Until then the engine path still skips the draw.
- `ShowBasicLoadText`'s text (font) draws are a separate path and are not part
  of this experiment; this covers the `Simple` background draw at `0xedfcd1`.

## Reproduction

```
cd /Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-dsh-full-reconstruction
python3 -m unittest tests.test_gfx_simple_draw -v
benchmark/.build/gfx_simple_draw_host \
  --vb analysis/gfx-draw/v0/l32b/effective-state/packet/payloads/vb_3.bin \
  --matrix analysis/gfx-draw/v0/l32b/effective-state/packet/payloads/matrix.bin \
  --dds /Applications/EuropaUniversalisIV/gfx/loadingscreens/load_0.dds
cd benchmark && python3 -c "import eu4_metal_boot as m; m.build_library()"
```

## Commit / PR

Draft PR: https://github.com/HagbardCel/eu4-mac-perf/pull/183
Branch: `experiment/dsh-full-reconstruction`
Commits: `4d43139` (draw, layout, validation) plus the follow-up commit that
records the unit-0 texture binding and this report. Not merged.
