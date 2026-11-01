# Partial layouts

A field is listed only when a tracked contract or a validated static body supports that displacement on that object. An instruction displacement outranks a name suggested by the decompiler. Nearby offsets are not one struct. Unknown bytes stay unnamed. Possible aliases are marked as aliases, not as facts.

Binary and batch identity are in [README.md](README.md).

## Master allocation from `_GfxInit`

`_GfxInit` allocates `0x198` bytes, stores the pointer in `gMasterContext`, and stores the same pointer at offset 0 of the block. The following are writes or later reads on the object reached as that allocation, or by loading `*handle` in a function that uses the same handle shape. This is not a claim that every `_Gfx*` argument is this object.

| Offset | What the body does | Confidence | Source |
| --- | --- | --- | --- |
| `0` | Stores the allocation pointer | inferred from the decompilation of the store; size `0x198` is the allocation argument | `_GfxInit`, `gfx-batch-00043.zip` |
| `+0x8` | Primitive token written by `_GfxSetPrimitiveType` and read by the draw calls | inferred | [set primitive](buffers/_GfxSetPrimitiveType.md), [draw indexed](draw/_GfxDrawIndexed.md) |
| `+0xc`, `+0x30`, `+0x54` | Embedded blend, depth-stencil, and rasterizer snapshots filled by `GetOpenGLContext*` | inferred | [reset](init/_GfxReset.md) |
| `+0x64`, `+0x6c` | Vertex count and a stride word, written for slot 0 | inferred | [set vertex buffers](buffers/_GfxSetVertexBuffers.md) |
| `+0x68` | Index count | inferred | [set index buffer](buffers/_GfxSetIndexBuffer.md) |
| `+0x70` | Current shader pointer | inferred | [set shader](shaders/_GfxSetShader.md) |
| `+0x78` | Current render-target pointer | inferred | [set render target](textures/_GfxSetRenderTarget.md) |
| `+0x80`, `+0x88`, `+0x90` | Default blend, depth-stencil, and rasterizer objects | inferred | [init](init/_GfxInit.md). Depth-stencil description itself is the B1 record in [gfx-init-handoff](../../../../analysis/metal-census/v1/gfx-init-handoff.md) |
| `+0x98` | Fallback texture | inferred | [init](init/_GfxInit.md), used by [set textures](textures/_GfxSetTextures.md) |
| `+0xa0`, `+0xa4` | Window width and height from `SDL_GetWindowSize` | inferred | [init](init/_GfxInit.md) |
| `+0xa8` | A count copied from the init argument and later compared with 1 | inferred | [init](init/_GfxInit.md), [set textures](textures/_GfxSetTextures.md) |
| `+0xac` through `+0xc0` | Viewport rectangle | inferred | [set viewport](state/_GfxSetViewPort.md) |
| `+0xc8` slot stride `0x10` | Constant-buffer word count and pointer. The reviewed layout is the existing contract, not a second guess | static-verified in that contract | [_GfxSetConstantBuffer contract](../../_GfxSetConstantBuffer/contract.md) |
| `+0x128` | Dirty byte set when constants or the current shader change, cleared by a draw after upload | inferred | [set constant buffer](shaders/_GfxSetConstantBuffer.md), [draw](draw/_GfxDraw.md) |
| `+0x130`, `+0x150`, `+0x160` | Per-slot vertex-buffer pointer, stride, and offset | inferred | [set vertex buffers](buffers/_GfxSetVertexBuffers.md) |
| `+0x170` | Dirty byte for vertex attribs | inferred | [set vertex buffers](buffers/_GfxSetVertexBuffers.md) |
| `+0x178` | SDL window | inferred | [init](init/_GfxInit.md), [present](init/_GfxPresent.md) |
| `+0x180` | GL context | inferred | [init](init/_GfxInit.md), [shutdown](init/_GfxShutdown.md) |
| `+0x188`, `+0x18f` | Capability bytes copied out by `_GfxGetCaps` | inferred | [get caps](init/_GfxGetCaps.md) |
| `+0x194` | Cleared by `_GfxInit`. Read by `_GfxSetRasterizerState` on the dereferenced handle | inferred | [init](init/_GfxInit.md), [set rasterizer](state/_GfxSetRasterizerState.md) |

`_GfxForceWireframe` writes `+0x194` on the pointer in its first argument. That ABI does not load through a handle. The existing contract does not identify the object. Do not treat that store as a field of the master allocation until a caller proves the pointer is the master.

## Other objects

These are separate allocations. They are not views of the master.

| Object | Size | Contents that are supported | Source |
| --- | --- | --- | --- |
| Texture or render-target header | `0x44` | Zeroed on create. Name at `+4`. Description fields from `+0x28`. Framebuffer and renderbuffer names at `+8`, `+0xc`, `+0x10`, `+0x14` | [create texture](textures/_GfxCreateTexture.md), [clear texture](textures/_GfxClearTexture.md) |
| Shader object | `0x78` | Program name in the first dword. Further fields are not mapped | [create shader](shaders/_GfxCreateShader.md) |
| Vertex-buffer header | `0xc` | Size, stride, and a GL buffer name at `+8` | [create vertex buffer](buffers/_GfxCreateVertexBuffer.md) |
| Index-buffer header | `8` | Count, and a GL buffer name at `+4` | [create index buffer](buffers/_GfxCreateIndexBuffer.md) |
| Constant-buffer header | `0x10` | Dword count, and a CPU pointer at `+8`. No GL name | [create constant buffer](buffers/_GfxCreateConstantBuffer.md) |
| Blend state | `0x48` | Description copied from `+0x24` | [create blend](state/_GfxCreateBlendState.md) |
| Depth-stencil state | `0x4c` | Description copied from `+0x24` | [create depth](state/_GfxCreateDepthStencilState.md) |
| Rasterizer state | `0x2c` | Description copied from `+0x10` | [create rasterizer](state/_GfxCreateRasterizerState.md) |
| Sampler | `0x2c` | Translated wrap and filter fields. No GL sampler name | [create sampler](textures/_GfxCreateSampler.md) |
| Vertex-layout element | `0x28` | Allocated as an array. Fields inside the record are not mapped | [add vertex layout](buffers/_GfxAddVertexLayout.md) |

Buffer caches (`g_IndexBufferCache`, `g_ConstantBufferCache`) and the counters `nGLVBCount`, `nGLIBCount`, and `nGLCBCount` are process globals. Ownership is a free-list of headers, not a reference count.

## Settings object written by `_GfxGetGraphicsSettings`

This is the caller's fourth argument, not the master. Supported writes: dword `+0x14` (anisotropy, or 0), byte `+0x10` set to 1, qword `+0x18` set to `0x30000000300`, dword `+0x20` set to `0x400`. A pointer at `+8` is walked as an array of `0xc`-byte sample records. What those constants mean to the caller is `unknown`.
