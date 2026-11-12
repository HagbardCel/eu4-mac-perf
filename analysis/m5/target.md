# M5 target — the loading-screen sprite draw after the Simple text draw

Status: selected from the live original renderer and reconstructed. The live
engine path to this draw is blocked by an unreconstructed frame-start GL entry
(see [Engine reach](#engine-reach-and-blocker)); the operation itself is
implemented in the production dispatcher and qualified through a deterministic
live-Metal replay of its authentic inputs.

## Selected operation

| Field | Value |
| --- | --- |
| Entry | `_GfxDraw` (`GL_TRIANGLE_STRIP`, mode 5, first 0, count 4) |
| Caller | `0x14f6c1b` (`CSpriteType::RenderInternal + 2271`) |
| Occurrence | first invocation at this caller in the loading screen |
| Original GL program | 22, two attached stages |
| Original GLSL digest | `51868985…` (`glGetShaderSource` length-prefixed digest) |
| Qualified compiler inputs | VS `e9422b2c…`, PS `10ca615e…` (normalized by replacing the driver's `GL_ARB_shader_texture_lod` extension line with `#define NO_SHADER_TEXTURE_LOD`) |
| Metal identity | `sprite-map/asset-maincode-v0` |
| Vertex | 80-byte live VBO, stride 20, offset 0: `float3 position` + `float2 texcoord` |
| Constants | 7 × `vec4` in slot 2; first 6 used: 4×4 matrix, `Color`, `vXOffset` |
| Texture | GL name 357, 1235 × 175, `GL_RGBA8`; sampled as `MapTexture` |
| Blend | enabled; RGB `SRC_ALPHA` / `ONE_MINUS_SRC_ALPHA`, `FUNC_ADD` |
| Depth | disabled; the reconstructed colour pass has no depth attachment |
| Raster | backend default snapshot; the quad's two strip triangles have the same winding |
| Scissor | unset/full attachment |
| Target | original `GL_BACK`, 1728 × 1117, BGRA8, viewport 0,0,1728,1117 |

The original GLSL body is:

```glsl
// vertex
Out.vPosition = mul(WorldViewProjectionMatrix, float4(v.vPosition.xyz, 1));
Out.vTexCoord = v.vTexCoord;
Out.vTexCoord.x += vXOffset;

// pixel
float4 OutColor = tex2D(MapTexture, v.vTexCoord);
OutColor *= Color;
return OutColor;
```

The qualifier `sprite-map` is the project's semantic identity for this effect,
not a new engine string. The exact engine compiler inputs are pinned in
[`analysis/gfx-shader/v0/qualified-sources.json`](../gfx-shader/v0/qualified-sources.json);
admission remains exact SHA-256, not a marker or substring match.

## How it was selected

1. **Original-GL inventory.** `python3 -m automation.live_gfx capture --target
   _GfxDraw --max-events 1024 --duration 30` recorded the authentic draw
   sequence past the loading text. The first caller after `0xedfcd1` is
   `0x14f6c1b`, mode 5, count 4. The CPU samples identify that caller as
   `CSpriteType::RenderInternal + 2271`; the later callers are
   `CBitmapFont::RenderToScreen` and map/UI draws.
2. **Original-GL state.** Extending the existing draw observer with the live
   VBO range, the constant block, the exact attached shader sources and the
   bound texture image captured the authentic inputs for the sprite draw. The
   observer restores the GL array-buffer binding and pixel-pack state.
3. **Metal probe.** A Metal probe with a bounded draw-sequence log showed only
   one `_GfxDraw` before the first present and then the original
   `CGraphics::BeginScene` crash. The engine does not reach the second loading
   frame in the current Metal path.

## Engine reach and blocker

The sprite draw is the next distinct `_GfxDraw` in the original sequence, but it
occurs after the first presented frame. In the reconstructed path the engine
returns from `presentDrawable`, advances to the next frame, and crashes at:

```
CGraphics::BeginScene + 39
  callq _GfxBeginScene
```

`_GfxBeginScene` is an original GL entry that is not reconstructed. The exact
smallest next action is to reconstruct `_GfxBeginScene` (and the immediately
following `CGraphics::ClearScene` frame-start path) so the Metal path can run a
second loading frame. That is a separate rendering operation from the sprite
draw and is not claimed here.

Because the engine path cannot reach the draw within the authorized launch
budget, the M5 qualification uses a deterministic **live-Metal replay** of the
same operation: the production sprite encoder runs on a Metal GPU with the
captured vertex, constant and texture bytes, over a diagnostic target seeded
with the authentic original-GL pre-draw payload. The replay is not a second
renderer; it calls the same `eu4_gfx_draw_encode_into` dispatcher and the same
`eu4_gfx_sprite_draw_encode_into` used by the physical route.

## Technical implications

- **New shader identity.** The sprite effect is translated to MSL with the
  `UNIFORM0[6]` layout, `vXOffset`, `MapTexture` and `Color`. Admission uses the
  exact captured compiler-input hashes. The Simple path is unchanged.
- **Second vertex layout.** The pipeline constructor now selects a stride-20
  `float3`+`float2` descriptor for the sprite effect. The Simple stride-24
  layout remains the default.
- **Alpha blending is required.** Unlike the M4 Simple quad (opaque, alpha 1),
  the sprite genuinely blends over prior contents. The pipeline uses
  `SRC_ALPHA`/`ONE_MINUS_SRC_ALPHA`; the state admission requires the measured
  sprite blend snapshot and refuses any other blend.
- **Destination contents matter.** An isolated draw into a fresh target cannot
  be compared with the original. The diagnostic logical target is persistent
  for a prefix and can be seeded with the authentic prior framebuffer. The
  selected draw is then encoded in order over that content. The physical target
  and the diagnostic target use the same encoder.
- **Exact comparison is valid.** The texture, constants, vertex data, sampler,
  viewport and blend are identical on both sides, and the replay is seeded with
  the exact original pre-draw bytes. Zero differing pixels over 1,930,176
  pixels is the observed result; the policy does not admit a tolerance.
