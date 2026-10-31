# `_GfxInit` handoff

Static call edges only, from [frame-model-static.json](../../frame-model-static.json) and [boundary.json](boundary.json). This is not a reconstruction. A row is engine-visible, OpenGL-only, or undetermined. Logging and C++ stream calls are omitted.

`_GfxInit` is `0x15e8ea0`. The opt-in B0 checkpoint returns from the primary window, bypasses `SDL_GL_SetSwapInterval` at `0x13cc1e4`, returns a sentinel from `SDL_GL_CreateContext` at `0x15e90d5`, and exits at `_glewInit` `0x15e91fd`. That sentinel is not a context. `_GfxGetGraphicsSettings` still calls `_glewInit` at `0x15ed576` and is not part of this checkpoint.

| Dependency | Static evidence | Unknown | Label |
| --- | --- | --- | --- |
| OpenGL conversion tables | `InitOpenGLConversionTables` is an early direct call | Whether any table is reused outside GL | undetermined |
| SDL window and context setup | `_GfxInit` calls `SDL_GetWindowFromID`, then `SDL_GL_CreateContext` at `0x15e90d5`, then `SDL_GetWindowSize` | Context ownership and what the engine stores | undetermined |
| GLEW | `_glewInit` at `0x15e91fd` is after context creation | Which capability decisions later code requires | undetermined |
| Rasterizer, blend, and depth-stencil defaults | `GetDefaultRasterizer`, `SOpenGLRasterizerState::Init`, then the three `GetOpenGLContext*` readers | Rasterizer and blend remain undetermined. The default depth-stencil description is in the B1 section | undetermined |
| `GfxCreateTexture` | Called at `0x15e99fb`, before those readers | Arguments, purpose, and lifetime | undetermined |
| `GetOpenGLContext*` readers | They read the original GL rasterizer, depth-stencil, and blend state | The Metal defaults that would replace those reads | OpenGL-only |
| `_GfxInit` return and stored state | `rax` returns the `0x198`-byte master context stored in `gMasterContext` | The window, GL context, GLEW flags, blend, rasterizer, and texture that return also requires | undetermined |

The readers are the only row the graph can mark OpenGL-only.

## B1 contract

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Instruction bytes below were read from that file. Pseudocode was checked against those instructions. Route B: `_GfxInit` is not replaced, and no startup mode was added.

`_GfxInit` (`0x15e8ea0`) takes the window-handle struct in `rdi` and a success byte in `rsi`. It returns the `0x198`-byte master context in `rax` (`movq %r12, %rax` at `0x15e9c6c`) after storing that pointer in `gMasterContext` (`0x15e8fe7`). Before that return it also stores the SDL window, an OpenGL context, GLEW flags, blend and rasterizer objects, a 4×4 texture, and this depth-stencil object. A return without those objects would be placeholder state. That blocks a `_GfxInit` replacement.

`CGraphics::InitGfx` (`0x149a824`) has two direct calls, neither patched:

| Callsite | Bytes | Target |
| --- | --- | --- |
| `0x149ada9` | `e8f2e01400` | `0x15e8ea0` |
| `0x149ae0a` | `e891e01400` | `0x15e8ea0` |

The fourteen `call [RCX+0x38]` sites inside `_GfxInit` are unresolved. They are not inputs to the description below. The logging calls around GLEW are not part of this slice.

`GetDefaultDepthStencil` (`0x15c6784`) writes a 40-byte description through `rdi`. `_GfxInit` calls it at `0x15e9921`, passes it to `SOpenGLDepthStencilState::Init` at `0x15e9960`, and stores the object at `master+0x88` (`0x15e9965`). `_GfxSetDepthStencilState` reads that pointer at `0x15e9a43`. Bytes 1–3 and `0xd`–`0xf` are not written. The constant pool at RVA `0x1a79440` is `ffffffffffffffff0000000000000000`.

| Offset | Bytes written | Evidence | Metal | Claim |
| --- | --- | --- | --- | --- |
| `0` | `01` | `movb $1, (%rdi)` | depth test on | verified |
| `4` | `01 00 00 00` | low half of `movabs $0x300000001` | depth write on | verified |
| `8` | `03 00 00 00` | high half of that immediate | compare 3 → `GL_LEQUAL` (`3+0x200`) → `MTLCompareFunctionLessEqual` | verified |
| `0xc` | `00` | `movb $0, 0xc(%rdi)` | EU4 disables `GL_STENCIL_TEST`. The Metal faces stay Always/Keep, which cannot reject a fragment or modify stencil values. This SDK's face properties are `null_resettable`, so the getter never returns nil | verified at the compare/operation level |
| `0x10`, `0x14` | `ffffffff` | `movaps` from `0x1a79440` | both faces use read and write mask `0xffffffff`. The two dwords match, so read versus write is not separated by this default | verified |
| `0x18`, `0x1c`, `0x20` | `0` | `movaps` zeros, then the low half of `movabs $0x700000000` | `GetOpenGLStencilOperatorFunction(0)` is `GL_KEEP` (`0x1e00`). Both faces use `MTLStencilOperationKeep` | verified |
| `0x24` | `07 00 00 00` | high half of that immediate | compare 7 → `GL_ALWAYS`. Both faces use `MTLCompareFunctionAlways` | verified |
| `1..3`, `0xd..0xf` | not written | no store | none | unverified |

`SOpenGLDepthStencilState::Init` copies byte 0, sets its write byte from the dword at `+4`, and maps the dword at `+8` through `GetOpenGLCompareFunction` (`0x15edd14`: values below 8 become `value+0x200`). The same mapping turns dword `+0x24` into `GL_ALWAYS`. `GetOpenGLStencilOperatorFunction(0)` returns `0x1e00` (`GL_KEEP`). The slice implements that default only. Any other description returns NULL. `MTLDepthStencilState` does not expose these fields. The offline check reads a descriptor built by the same factory that creates the Metal state. The caller owns the returned reference and must release it. The slice does not release the device. A later buffer allocation shows that the borrowed device is still usable. It does not prove a reference-count balance.

At the stencil compare/operation level, Always/Keep cannot reject fragments or modify stencil values, matching the inactive behavior of EU4's disabled stencil test. This does not establish equivalent attachment requirements, hardware execution, or performance. A later `_GfxSetDepthStencilState` replacement must keep byte `0xc` in logical state. It must not be read back from the Metal face pointers.

Offline completion is this description and that Metal state. It is not completion of `_GfxInit`, and it is not a live integration. The consumer at `0x15e9a43` passes `master+0x88` to `_GfxSetDepthStencilState`, which still expects an `SOpenGLDepthStencilState*`. A receipt, a process exit, or another GL call would not show that this state was used. No live run is authorized until a consumer can observe depth compare less-equal, depth write on, and stencil faces at Always/Keep with all-ones masks, without reading a GL object.
