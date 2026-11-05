# Textures

## API

`MTLTextureDescriptor`, format selection, BC3 upload, and mip levels. The OpenGL source for ordinal 5 is `GL_COMPRESSED_RGBA_S3TC_DXT5_EXT`.

## Sources

- `Metal.framework/Headers/MTLTexture.h`
- `Metal.framework/Headers/MTLPixelFormat.h`
- `Metal.framework/Headers/MTLDevice.h`
- [MTLTextureDescriptor](https://developer.apple.com/documentation/metal/mtltexturedescriptor)
- [`eu4_gfx_texture.h`](../../benchmark/eu4_gfx_texture.h)
- [Ordinal 5](../metal-init/compressed-texture-5.md)

## Provenance

Reviewed 2026-11-05 against macOS SDK 27.0. The three headers above were read. The `MTLTextureDescriptor` page was opened the same day. The BC3 row stride is not in those headers. It stays a project reconstruction.

## Metal contract

[Apple docs] A descriptor configures a new texture. Metal copies the descriptor's properties into the texture, and a later edit of the descriptor does not change that texture.

[SDK] `supportsBCTextureCompression` reports whether the device supports BC formats. [SDK] `MTLPixelFormatBC3_RGBA` is a pixel format in this SDK. [SDK] `replaceRegion:mipmapLevel:withBytes:bytesPerRow:` copies the caller's bytes into one mip. The header does not state a block size or a BC3 row stride.

[Project-derived] Ordinal 5 is linear DXT5 and is uploaded as `MTLPixelFormatBC3_RGBA`. The other compressed ordinals are rejected. Uncompressed selection in [`eu4_gfx_texture.mm`](../../benchmark/eu4_gfx_texture.mm) is `MTLPixelFormatRGBA8Unorm` for format 9, `MTLPixelFormatBGRA8Unorm_sRGB` for formats `0xc` and `0xe`, and `MTLPixelFormatBGRA8Unorm` otherwise.

[Project-derived] For ordinal 5, `bytesPerRow` is `ceil(width / 4) * 16`, including a side shorter than 4. That formula is the reconstruction in [ordinal 5](../metal-init/compressed-texture-5.md), not an Apple requirement copied from `MTLTexture.h`.

## OpenGL differences

[Project-derived] The GL path writes the compressed internal format and uploads the caller's image. Metal requires a pixel format the device supports, a mip level count, and an explicit row length. A missing BC3 capability is a failed texture, not a silent uncompressed substitute.

## Backend implications

[Project-derived] The descriptor uses shared storage and `MTLTextureUsageShaderRead`. A mip count of 0 on a BC3 texture becomes 1. [Project-derived] `eu4_gfx_texture_make` with an image count of 0 allocates storage and does not invent pixels. [Project-derived] `eu4_gfx_test_no_bc` and a device that fails `supportsBCTextureCompression` both return `EU4_GFX_TEX_METAL` for ordinal 5.

[Unresolved] Whether every Mac GPU this process can see supports BC3 is a device query at runtime. The header does not promise support.

## Validation

[Project-derived] [`tests/gfx_texture_host.mm`](../../tests/gfx_texture_host.mm) checks `MTLPixelFormatBC3_RGBA` and the mip count. [`tests/test_gfx_texture.py`](../../tests/test_gfx_texture.py) is the Python host for the same uploader.
