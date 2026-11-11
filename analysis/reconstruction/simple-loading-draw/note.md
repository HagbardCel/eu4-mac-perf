# Simple loading-screen draw — reconstruction note

Target: the `_GfxDraw` call at caller RVA `0xedfcd1` (call at `0xedfccc`) inside
`CEU3Graphics::ShowBasicLoadText` (`0xedfaf8`), and the Metal equivalent of the
draw it submits.

Pinned executable SHA-256: `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.
No EU4 launch was performed for this note. All original facts come from the
tracked L3.2b-I packet, the tracked decompilation corpus, and the installed game
assets named below.

Labels: **Project-derived** = read from the pinned binary / tracked evidence,
**Asset** = read from the installed game data, **SDK** = read from the installed
macOS SDK headers, **Unresolved** = no evidence.

## Evidence read

| Evidence | Location | Use |
| --- | --- | --- |
| L3.2b-I draw packet | `analysis/gfx-draw/v0/l32b/effective-state/packet/` | authentic draw invocation, 96-byte vertex buffer, 64-byte matrix |
| L3.2b-I review | `analysis/gfx-draw/v0/l32b/effective-state/review.json` | `qualified`, packet digest `7f227ad4…` |
| L3.2a probe note/packet | `analysis/gfx-draw/v0/probe/` | viewport `1728×1117`, mode 5, effective count 4 |
| P5.2a shader contract | `analysis/automation/eu4-p52a-shader-contract/note.md`, `analysis/gfx-shader/v0/` | effect identity, sampler and blend contract |
| Decompiled bodies | `.local/eu4/<sha>/exports/auto-v1-20261013/decompiled/batch-00093.jsonl`, `batch-00131.jsonl` | `_GfxDraw` `0x15ea789`, `_GfxSetVertexBuffers` `0x15ebf41`, `_GfxSetBlendState` `0x15ea0bf`, `_GfxCreate/Set/UpdateConstantBuffer` `0x15ec2bc/0x15ec36a/0x15ec3b5`, `ShowBasicLoadText`, `LoadLoadingScreens`, `AddSimpleEffect` |
| Simple shader asset | `/Applications/EuropaUniversalisIV/gfx/FX/simple.shader` | VS/PS bodies, sampler, blend |
| Loading screens | `/Applications/EuropaUniversalisIV/gfx/loadingscreens/load_*.dds` | 38 files, 2048×1536, uncompressed BGR24 |
| Metal SDK | `MTLVertexDescriptor.h`, `MTLRenderPipeline.h`, `MTLRenderCommandEncoder.h` (macOS 27.2 SDK) | descriptor, pipeline and encoder declarations |

The installed SDK declares `MTLRenderPipelineDescriptor.vertexDescriptor` at
`MTLRenderPipeline.h:187`, `MTLVertexDescriptor.layouts/attributes` at
`MTLVertexDescriptor.h:147-148`, and
`setVertexBuffer:offset:atIndex:`, `setViewport:`,
`setFragmentTexture:atIndex:`, `setFragmentSamplerState:atIndex:` and
`drawPrimitives:vertexStart:vertexCount:` in `MTLRenderCommandEncoder.h`.
(SDK)

## What the original call does (Project-derived)

`ShowBasicLoadText` on the loading branch:

1. `CGraphics::BeginScene` / `ClearScene`.
2. `GetGfxEffect(*(this+0x2c660))` — the `Simple` technique created by
   `AddSimpleEffect` from `gfx/FX/simple.lua` (`simple.shader` on disk).
3. `_GfxUpdateConstantBuffer(ctx, *(this+0x2c670), matrix, -1)` and
   `_GfxSetConstantBuffer(ctx, cb, 2, …)`.
4. `_GfxSetVertexBuffers(ctx, 1, this+0x2c668, 0, 0, 0)`.
5. `_GfxSetPrimitiveType(ctx, 4)` → GL mode 5 (`GL_TRIANGLE_STRIP`).
6. `_GfxSetShader`, `_GfxSetTextures(ctx, 1, this+0x2c650, 1, 0, effect+0x20)`
   when `*(this+0x2c650)` is non-null, then `_GfxDraw(ctx, 0, 0)`. The
   replacement stores the unit-0 engine header (or the master's default texture
   at `+0x98` when the entry is null) in the backend; the encoder resolves that
   header through the texture registry.
7. `Render2dObjectsToScreen` / `EndScene` / `PresentScene`.

`_GfxDraw` (`0x15ea789`) reads:

| Field | Meaning | Authentic value |
| --- | --- | --- |
| `master+0x08` | `glDrawArrays` mode | 5 |
| `rsi` / `master+0x64` | effective count | 4 (`rsi = 0`) |
| `master+0x70` | current shader object | non-null `Simple` |
| `master+0x130 + 8·slot` | vertex header | slot 0 |
| `master+0x150 + 4·slot` | byte offset | 0 |
| `master+0x160 + 4·slot` | stride word (copied from header+0) | 24 |
| `master+0xd0 + 16·2` | constant object, slot 2 | `Matrix` |
| `master+0xc8 + 16·2` | uploaded float4 count | 4 |

`_GfxCreateConstantBuffer` allocates a 0x10-byte object (`u32 word count` at
`+0`, data pointer at `+8`, data size `bytes & ~3`); `_GfxUpdateConstantBuffer`
`memcpy`s `count<<2` bytes into it. `_GfxDraw` then calls
`glUniform4fv(location, count, data)` for the dirty slot. There is no GL call in
Create/Update/Set, so the CPU object remains valid in a Metal-only process and
the encoder reads it directly.

### Vertex buffer (Project-derived)

`LoadLoadingScreens` builds one static 24-byte-stride quad after the loading
image is loaded. The tracked payload decodes as:

| Vertex | position | texcoord | colour |
| --- | --- | --- | --- |
| 0 | (-512, 384, 0.5) | (0, 0) | `0xffffffff` |
| 1 | (512, 384, 0.5) | (1, 0) | `0xffffffff` |
| 2 | (-512, -384, 0.5) | (0, 1) | `0xffffffff` |
| 3 | (512, -384, 0.5) | (1, 1) | `0xffffffff` |

The decompiled buffer construction agrees byte for byte: `-512`, `384`, `0.5`,
two zero floats, `0xffffffff`, then `512`, `384`, `0.5`, the u scale, `0`,
`0xffffffff`, and so on. UVs stay 0/1 because the engine only applies the
`width / Pow2Ceiling(width)` correction on the no-NPOT branch.

SHA-256 `f13dea22…` (96 bytes).

### Matrix (Project-derived)

The tracked 64-byte constant is

```
[1/512, 0, 0, 0]
[0, 2/661, 0, 0]
[0, 0, 1, 0]
[0, 0, 0, 1]
```

`ShowBasicLoadText` computes the vertical extent as
`floor((height << 10) / width)` = `floor(1117·1024/1728)` = 661, then uses
`2/661`. The effect multiplies with `mul(Matrix, vPosition)`, so the quad maps
to `x ∈ [-1,1]` and `y ∈ [-1.162,1.162]`: full width, 16 % cropped vertically
(a fit-width cover of the 4:3 loading image on the 1728×1117 window).

SHA-256 `e00f5824…` (64 bytes).

### Effect, state and texture (Asset)

`Simple` binds `VertexShaderSimple` (`Out.vPosition = mul(Matrix, v.vPosition)`,
pass-through UV/colour) and `PixelShaderSimple`
(`tex2D(SimpleTexture, uv) * vColor`). Sampler `SimpleTexture` index 0 is
`Wrap`/`Linear`/`Linear`. Blend is `SRC_ALPHA` / `INV_SRC_ALPHA`.

`LoadLoadingScreens` enumerates `gfx/loadingscreens/*`, picks an index with
`CCrudeRandom` seeded through `NSeedController`, loads it with
`Gfx_Util::LoadTextureFromMemory`, creates the engine texture into
`CEU3Graphics+0x2c650`, and recreates the quad when the texture dimensions
change. The 38 installed `load_*.dds` files are all 2048×1536 BGR24.

## Metal mapping (implemented)

`benchmark/eu4_gfx_simple_draw.{h,mm}` encodes the draw from the engine master;
`benchmark/eu4_gfx_shader.mm` now builds the pipeline with the authentic layout.

| Original | Metal |
| --- | --- |
| `master+0x08` mode 5 | `MTLPrimitiveTypeTriangleStrip` (other modes refused) |
| `master+0x64` count, `edx` first | `vertexStart:first`, `vertexCount:count` |
| `master+0x70` + `_GfxSetShader` | linked `Simple` record → `MTLRenderPipelineState` |
| `master+0x130/0x150/0x160` | `setVertexBuffer:offset:atIndex:0` |
| `glUniform4fv(Matrix)` | `setVertexBuffer:uniforms atIndex:1` (`constant Uniforms &` in MSL) |
| `glEnable(GL_BLEND)` + `glBlendFunc` | pipeline `blendingEnabled`, `SourceAlpha` / `OneMinusSourceAlpha` (RGB and alpha) |
| `_GfxSetTextures` unit 0 | replacement records the engine header in the backend; the encoder resolves it to the retained `MTLTexture` and binds `setFragmentTexture:atIndex:0` with a linear/repeat sampler |
| viewport `1728×1117` | `setViewport:` (the engine's viewport image is the encoder's input) |
| clear + draw | `loadAction Load` after `_GfxClear`; `storeAction Store`; the draw shares the backend `MTLCommandQueue` so it is ordered after the clear |
| `glDrawArrays` | `drawPrimitives:vertexStart:vertexCount:` |

Vertex descriptor: `Float3` at 0, `Float2` at 12, `UChar4Normalized` at 20,
stride 24. MSL takes `float3 position` and supplies the implicit `w = 1` that GL
provides for a three-component `float4` attribute.

## Semantic mismatches handled or left open

- **Implicit attribute w.** GL supplies `w = 1` for a 3-component POSITION read
  as `float4`; Metal has no implicit component. The MSL constructs
  `float4(position, 1.0)`. (Project-derived + SDK)
- **Normalized colour.** GL converts `GL_UNSIGNED_BYTE` vertex colour to float
  automatically; Metal needs `MTLVertexFormatUChar4Normalized` and a `float4`
  stage input. The authentic colour is white, so byte order (RGBA vs BGRA)
  cannot be distinguished from this packet. **Unresolved**; RGBA is used.
- **Texture V orientation.** GL row 0 is tied to `v = 0`; Metal's `v = 0` is the
  first uploaded row. Because both the coordinate and the render-target origin
  conventions shift together, this quad is visually identical in both APIs.
  This is verified empirically, not assumed: 1600 sampled pixels match the
  CPU transform within 0.0028 absolute, while the vertically flipped image
  differs at 368 of those samples.
- **sRGB.** The texture module uploads format 9 as `RGBA8Unorm` and the target
  is `BGRA8Unorm` (P5.1 project-derived default). If the original backbuffer
  were sRGB the blend/readback values would differ. **Unresolved**.
- **Depth / stencil.** No depth attachment is used. The draw covers the full
  screen at constant z, so depth ordering cannot change these pixels; the
  original depth test state is not captured. **Unresolved**.
- **Scissor.** The Metal pass leaves the implicit full-attachment scissor; the
  captured draw reports scissor as missing. **Unresolved**.
- **Sampler object.** The asset's wrap/linear contract is used; the engine
  `SSamplerStateOpenGL` object reached through `r9` is not decoded.
  **Unresolved**.
- **Submission order.** `_GfxClear` and `_GfxPresent` commit on
  `backend.queue`; a draw on a private queue could overtake them. The encoder
  takes an optional queue and the host passes the backend queue. A boundary test
  commits a green clear without waiting, draws 50 % red on the same queue, and
  requires `(127,127,0)`; a private queue would leave the target green.
- **Loading-screen index.** The runtime index is randomised; the capture kept
  only texture identity, not contents. The test can use the installed
  `load_0.dds` (a real asset) or a deterministic synthetic image; neither is the
  exact image of the captured invocation.
- **Live entry wiring.** The module compiles into `libeu4_metal_boot.dylib`
  (`benchmark/eu4_metal_boot.py`) but `_GfxDraw` is still the capture shim that
  skips the GL draw. Enabling the encoder live needs an authorized launch and
  the game-backbuffer/present ordering (`_GfxClear` in a separate command
  buffer) to be validated first.

## Boundary tests derived from the mismatches

Implemented in `tests/gfx_simple_draw_host.mm`; each must fail closed:

- mode 4 instead of 5 → `EU4_GFX_SIMPLE_DRAW_PRIMITIVE`;
- stride 40 instead of the admitted 24 → `EU4_GFX_SIMPLE_DRAW_VERTEX`;
- `count = 2` → `EU4_GFX_SIMPLE_DRAW_COUNT`;
- no texture → `EU4_GFX_SIMPLE_DRAW_TEXTURE`;
- 1600-pixel grid comparison against CPU bilinear sampling of the same image,
  plus the flip discriminator;
- a 50 %-alpha source over a cleared blue target → `SRC_ALPHA/INV_SRC_ALPHA`
  readback `(127,0,128)` within 3/255 on all 4096 pixels of a 64×64 target.

## Validation (real Metal)

`tests/test_gfx_simple_draw.py` builds and runs the host on macOS with
`MTL_DEBUG_LAYER=1`. The host binds the texture through the replacement
`_GfxSetTextures` (not a direct Metal pointer) and checks that the backend
resolves the same `MTLTexture`. Result on 2026-11-11:

```
texture=original-dds 2048x1536
ok checked=1600 worst=0.0028 flip_differences=368 blend_bad=0 order_bad=0
done
```

and the hermetic path (synthetic 2048×1536 BGR image) passes the same checks.
`libeu4_metal_boot.dylib` builds with the new module, and the existing
`test_gfx_contracts`, `test_gfx_entry_registry`, `test_gfx_draw_packet`,
`test_gfx_shader`, `test_gfx_buffers`, `test_gfx_texture` and `test_gfx_init`
suites (56 tests) still pass.

Reproduction:

```
python3 -m unittest tests.test_gfx_simple_draw -v
benchmark/.build/gfx_simple_draw_host \
  --vb analysis/gfx-draw/v0/l32b/effective-state/packet/payloads/vb_3.bin \
  --matrix analysis/gfx-draw/v0/l32b/effective-state/packet/payloads/matrix.bin \
  --dds /Applications/EuropaUniversalisIV/gfx/loadingscreens/load_0.dds
```
