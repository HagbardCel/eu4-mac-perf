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
| `benchmark/eu4_gfx_texture.h/.mm` | `eu4_gfx_texture_for_header()` — borrowed `MTLTexture` retained by `_GfxInitializeTexture`; `_GfxSetTextures` records the unit-0 engine header in the backend (falling back to the master's `+0x98` default). `eu4_gfx_bound_texture()` resolves registry textures normally, and resolves that default header to `backend.fallback` (the white 4×4 texture created by `_GfxInit`). |
| `benchmark/eu4_gfx_simple_draw.h/.mm` | New encoder: reads the engine master (`mode`, `count`, `shader`, slot-0 vertex header/offset/stride, constant slot 2), refuses unsupported inputs, creates the `Simple` pipeline through the shader registry, copies the matrix, binds vertex/uniform buffers, fragment texture and sampler, sets the viewport, and submits a BGRA8Unorm pass. It accepts the backend command queue so the draw is ordered after `_GfxClear`. Vertex-range validation uses the highest vertex the draw actually reads (`offset + (first + count) * stride`), rejects negative `first`, and does the arithmetic in unsigned 64-bit. Shader admission now requires the registry's exact `simple-lua-Simple/asset-maincode-v0` identity, not merely a non-null linked shader. |
| `benchmark/eu4_gfx_init.h` | Backend property for the current unit-0 texture header. |
| `benchmark/eu4_metal_boot.py` | The new module is compiled into `libeu4_metal_boot.dylib`. |
| `tests/gfx_simple_draw_host.mm` | Real-Metal host: authentic packet + optional original DDS, CPU reference sampling, flip discriminator, alpha-blend boundary, fail-closed cases, `first`/`count` range boundaries, a non-Simple identity rejection, a null-entry default-texture bind resolved to `backend.fallback` (with the previous binding restored), and a synthetic repeated-UV boundary. Vertex state is established through the replacement `_GfxSetVertexBuffers`; the test asserts that `_GfxSetShader` itself published the shader instead of rewriting `master+0x70` by hand. |
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

### Submission boundary (decision)

The encoder commits its command buffer and calls `waitUntilCompleted`. That is
retained deliberately for this offline diagnostic entry point: it makes the
readback and the refusal reason deterministic, and it keeps the borrowed vertex
buffer, texture, target and uniform bytes alive until the GPU is finished. It is
**not** the production submission contract. A live `_GfxDraw` must not wait after
every draw; it needs the backend scheduler to own the command-buffer and resource
lifetimes. That wiring is intentionally out of scope for this PR.

### Focused correctness review

- Borrowed `MTLBuffer`, vertex headers, textures and drawable targets are only
  used while the synchronous command is in flight; no pointer is retained after
  return.
- Unit-0 texture resolution still goes through `_GfxSetTextures` →
  `eu4_gfx_bound_texture()` → the texture registry; only the engine's default
  `+0x98` header resolves to `backend.fallback`, and any other missing header
  fails closed.
- The target must be a single-sample 2D BGRA8Unorm render target whose actual
  dimensions, device and declared viewport describe the same image. The texture
  must be a single-sample, sampleable 2D texture on the same device and must not
  alias the target. The GL lower-left viewport origin is converted to Metal's
  top-left origin (a no-op for the authentic full-attachment viewport).
- A supplied command queue must belong to the same `MTLDevice`.
- The 64-byte matrix is copied into a fresh shared buffer before encoding, so
  the engine's constant-object storage may be rewritten immediately after the
  call.
- The sampler cache now holds its `MTLDevice` strongly. A recycled raw device
  pointer can no longer return a sampler created by a destroyed device.

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
6. The first repeated-UV boundary attempt called `eu4_gfx_configure_offscreen`
   while the ordering case still owned a pending clear. The test now drains that
   clear through `eu4_gfx_complete_offscreen` before reconfiguring, which also
   exercises the backend's pending-clear ownership.

### Passing

```
$ python3 -m unittest tests.test_gfx_simple_draw -v
test_authentic_packet_bytes ... ok
test_hermetic_synthetic_texture_draw ... ok
test_original_loading_screen_draw ... ok
Ran 3 tests ... OK
```

Direct run on the original asset (the test was executed, not skipped):

```
texture=original-dds 2048x1536
ok checked=1600 worst=0.0028 flip_differences=368 blend_bad=0 order_bad=0
repeat_bad=0 repeat_differences=3694 worst_repeat=0.0031 first_bad=0
done
```

1600 pixels of the 1728×1117 Metal readback match CPU bilinear sampling of the
same DDS through the authentic matrix within 0.0028 (≈0.7/255); the vertically
flipped reference differs at 368 samples, so orientation is actually tested.
The 64×64 alpha-blend pass reads `(127,0,128)` within 3/255 on all 4096 pixels.
A clear/draw ordering case commits a green clear without waiting and then draws
50 % red on the backend queue; the readback `(127,127,0)` proves the draw is
ordered after the clear (`order_bad=0`).

Synthetic boundary (not an original-game observation): the same quad and matrix
with texcoords spanning `[-1, 2]` over a 4×4 texture. The Repeat sampler contract
matches the wrapping CPU reference on all 4096 pixels (`repeat_bad=0`), and the
clamped reference differs at 3694 of them, so the test is not vacuous.

Range boundaries on the authentic four-vertex buffer: `first=0,count=4` and
`first=1,count=3` are accepted; `first=1,count=4`, `first=-1`,
`count=first=INT32_MAX` and `first=INT32_MAX,count=3` are refused as
`EU4_GFX_SIMPLE_DRAW_VERTEX` without overflowing. A first=1,count=3 white-source
draw covers only the lower-right triangle, so `first` reaches
`drawPrimitives:vertexStart:` rather than being silently dropped. A second
linked record carrying the Simple stages but relabelled with a non-Simple
identity is refused with `EU4_GFX_SIMPLE_DRAW_SHADER`; this proves admission
compares the registry identity, not that a second effect has been reconstructed.
A mismatched declared target size and a target/texture alias also fail closed. A
null unit-0 entry binds the master's default header, resolves to
`backend.fallback`, and is accepted by the encoder; the previous binding is
restored after the check.

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
- The synchronous `commit` + `waitUntilCompleted` is an offline-host contract
  only (see “Submission boundary”); it is not evidence for a production
  submission strategy.
- The repeated-UV case is synthetic. It proves the encoder's sampler contract
  (Repeat/Linear), not that the captured original frame used UVs outside
  `[0,1]`; the captured packet's UVs are 0/1.
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
Offline GPU-backed milestone commits on the branch: `4d43139` (draw, layout,
validation), `fffac3b` (unit-0 texture binding through `_GfxSetTextures`),
`5c5df4a` (report), `7d11de2` (backend-queue ordering), `96a6c9f` (report
commit list), then this correction pass: `b20b64a` (range/identity/admission
boundaries and the offline submission contract), `d2c7724` (viewport origin,
nonzero-first readback and review-driven refusals), `8b69907` (sampleable alias
coverage), and `ee0fb1d` (default texture header → `backend.fallback`). Not
merged.
