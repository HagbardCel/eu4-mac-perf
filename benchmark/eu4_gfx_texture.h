#ifndef EU4_GFX_TEXTURE_H
#define EU4_GFX_TEXTURE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* rdi is unused. rsi is the caller-owned 0x44 header. rdx is the description.
   rcx is the image array. r8d is the image count. Returns void.
   Disassembly at 0x15ec43b: kind 1 is a cubemap, formats 1..7 are compressed.
   On success the GL name at header+4 is written 0. A rejected record leaves the header
   untouched. glDeleteTextures is a diagnostic stop, not a Metal destroy. */
enum {
    EU4_GFX_TEX_OK = 0,
    EU4_GFX_TEX_HEADER = 1,
    EU4_GFX_TEX_CUBEMAP = 2,
    EU4_GFX_TEX_COMPRESSED = 3,
    EU4_GFX_TEX_FORMAT = 4,
    EU4_GFX_TEX_SIZE = 5,
    EU4_GFX_TEX_PIXELS = 6,
    EU4_GFX_TEX_LEVEL = 7,
    EU4_GFX_TEX_METAL = 8,
    EU4_GFX_TEX_BACKEND = 9
};

/* Fills the header only on OK. Does not allocate a texture. */
int eu4_gfx_texture_prepare(void *header, const void *desc, const void *images, uint32_t count);

/* On OK, *out_texture is +1. device is id<MTLDevice>. A later upload call is
   required when count is 0: this allocates storage and does not invent pixels. */
int eu4_gfx_texture_make(void *device, void *header, const void *desc, const void *images,
                         uint32_t count, void **out_texture);

void eu4_gfx_initialize_texture(void *ignored, void *header, const void *desc, const void *images,
                                uint32_t count);

#ifdef __cplusplus
}
#endif

#endif
