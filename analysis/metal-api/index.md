# Metal API index

Search this directory with `rg`. Contracts are the linked files. A label inside a contract says whether a sentence is from the SDK, an opened Apple page, this project, or still unresolved.

| Metal | OpenGL | Contract | Documentation | Risk |
| --- | --- | --- | --- | --- |
| `setScissorRect:`, `MTLScissorRect` | `glScissor` | [scissor.md](scissor.md) | [setScissorRect:](https://developer.apple.com/documentation/metal/mtlrendercommandencoder/setscissorrect(_:)) | Signed GL coordinates, an attachment-bounded rectangle, and enablement stored apart from the box |
| `loadAction`, `MTLRenderPassDescriptor` | `glClear` | [render-pass.md](render-pass.md) | [loadAction](https://developer.apple.com/documentation/metal/mtlrenderpassattachmentdescriptor/loadaction) | A pass-start clear is not a mid-frame `glClear`. Color defaults to dont-care; depth and stencil default to clear. A color dont-care store drops pixels a later pass still needs |
| `MTLBuffer` | `glBufferData`, `glBufferSubData` | [buffers.md](buffers.md) | [MTLBuffer](https://developer.apple.com/documentation/metal/mtlbuffer) | The GL name is not the Metal object. Shared storage is unsynchronized, and an update keeps the same buffer |
| `MTLTextureDescriptor`, `MTLPixelFormatBC3_RGBA` | DXT5, `GL_COMPRESSED_RGBA_S3TC_DXT5_EXT` | [textures.md](textures.md) | [MTLTextureDescriptor](https://developer.apple.com/documentation/metal/mtltexturedescriptor) | BC3 support is a device query. The BC3 row stride is project-derived |

```sh
rg -n -i 'setScissorRect|glScissor' analysis/metal-api
rg -n -i 'MTLBuffer|glBufferSubData' analysis/metal-api
rg -n -i 'loadAction|glClear' analysis/metal-api
```
