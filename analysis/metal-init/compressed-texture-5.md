# Compressed texture ordinal 5

Ordinal 5 is linear DXT5. `GetOpenGLTextureFormat` at `0x15ede5d` decrements the ordinal and jumps through the table at `0x15edf84`. Ordinal 5 lands at `0x15edecb` and writes only `0x83F3` (`GL_COMPRESSED_RGBA_S3TC_DXT5_EXT`). It does not write the format or type outputs. `_GfxInitializeTexture` does not call `GetsRGBFormat`, so this is not ordinal 6 (`0x8C4F`).

`IsBlockCompressed` at `0x15c5de9` is true for ordinals 1 through 7. Only ordinal 5 is uploaded. The others stay unsupported.

`GetBitsPerPixel` at `0x15c5e15` reads the seven dwords at RVA `0x1a793e4`. Ordinal 5 is 8 bits. The header dword at `+0x34` is that value shifted down by 3, so it is `1`. One block is 4×4 pixels and 16 bytes. A mip stores `ceil(width / 4) * ceil(height / 4) * 16` bytes, and a side shorter than 4 still uses one block. The Metal format is `MTLPixelFormatBC3_RGBA`. `bytesPerRow` is `ceil(width / 4) * 16`.

The compressed upload at `0x15ec5d2` passes `tex_image.bytes` (`+0x10`) as the image size and the pointer at `+0x18` as the data. The supplied size has to equal the block span.

The header keeps the description's mip dword at `+0x40`, including 0. Metal's level count is `max(that dword, 1)`, and that count also has to fit the chain implied by the dimensions. Bytes `+0x20` and `+0x24` stay as they were. The written fields are the GL name `0`, cubemap byte `0`, `+0x19` and `+0x1a` clear, internal format `0x83F3`, the dimensions, format `5`, and byte count `1`.
