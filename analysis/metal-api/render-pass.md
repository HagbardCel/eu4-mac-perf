# Render pass

## API

`MTLRenderPassDescriptor`, attachment `loadAction` and `storeAction`, and `MTLRenderCommandEncoder` lifetime. The OpenGL operation is `glClear` inside `_GfxClear`.

## Sources

- `Metal.framework/Headers/MTLRenderPass.h`
- `Metal.framework/Headers/MTLCommandEncoder.h`
- [loadAction](https://developer.apple.com/documentation/metal/mtlrenderpassattachmentdescriptor/loadaction)
- [storeAction](https://developer.apple.com/documentation/metal/mtlrenderpassattachmentdescriptor/storeaction)
- [`eu4_gfx_clear`](../../benchmark/eu4_gfx_init.h)
- [`_GfxClear`](../reconstruction/gfx-semantic/v1/state/_GfxClear.md)

## Provenance

Reviewed 2026-11-05 against macOS SDK 27.0. Both headers above were read. The `loadAction` and `storeAction` pages were opened the same day. Their color, depth, and stencil defaults are the Apple docs sentences below. The base header comment is a different, coarser default.

## Metal contract

[SDK] `loadAction` is the action for an attachment at the beginning of a render pass. [SDK] The base property comment says the default is `MTLLoadActionDontCare` unless a creation or init method says otherwise. [Apple docs] For a color render target the default is `MTLLoadActionDontCare`. [Apple docs] For a depth or stencil render target the default is `MTLLoadActionClear`.

[SDK] `clearColor`, `clearDepth`, and `clearStencil` are used when that attachment's `loadAction` is `MTLLoadActionClear`. [SDK] `storeAction` is the action at the end of the pass. The base comment says its default is `MTLStoreActionDontCare` unless a creation or init method says otherwise. [Apple docs] For a color render target the default is `MTLStoreActionStore`. [Apple docs] For a depth or stencil render target the default is `MTLStoreActionDontCare`. [Project-derived] Those are framework defaults. A later pass still chooses the action that keeps the pixels it needs.

[SDK] `endEncoding` declares that command generation from the encoder is complete and detaches it from the command buffer.

## OpenGL differences

[Project-derived] `_GfxClear` builds a `glClear` mask from independent color, depth, and stencil flags, after its own scissor and masks. [Project-derived] That clear is a GL command at the call. It is not a property of a render-pass descriptor, and it can run when no Metal pass exists.

[SDK] `MTLLoadActionClear` clears because the pass is beginning. A scissor rectangle set on a later encoder does not change a load action that already ran.

## Backend implications

[Project-derived] [`eu4_gfx_clear`](../../benchmark/eu4_gfx_init.h) does not create a render target or a render pass, and it does not implement GPU clearing. The GL clear is deferred, not applied. A later pass that must keep a previous attachment sets `loadAction` itself. A value this backend writes on a descriptor is not the framework default.

## Validation

[Project-derived] [`tests/gfx_init_host.mm`](../../tests/gfx_init_host.mm) builds a color attachment and sets `loadAction` to `MTLLoadActionClear` before encoding. That test does not show a depth or stencil default, and it does not show that `eu4_gfx_clear` cleared the GPU.
