#ifndef EU4_GFX_TEXTURE_H
#define EU4_GFX_TEXTURE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* rdi is unused. rsi is the caller-owned 0x44 header. rdx is the description.
   rcx is the image array. r8d is the image count. Returns void.
   Disassembly at 0x15ec43b: kind 1 is a cubemap, formats 1..7 are compressed.
   Ordinal 5 is linear DXT5 and is uploaded as BC3. The other compressed ordinals stay rejected.
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

/* _GfxInitializeRenderTargetTexture at 0x15ec8e2. Creates the engine's
   render-target colour texture (and a Metal depth companion) for a header
   allocated by _GfxCreateRenderTargetTexture. rsi is the 0x44 TextureGFX
   header; rdx/rcx are width/height; r8d is EGfxTextureFormat; r9d is the
   "has depth" flag; the stack argument is the multisample count. */
void eu4_gfx_initialize_render_target_texture(void *handle, void *header,
                                              uint32_t width, uint32_t height,
                                              uint32_t format, uint32_t flags,
                                              uint32_t samples);

/* _GfxUpdateTexture at 0x15ec66f. r9 is the TextureGFX header; rsi is the CPU
   image for mip level 0; edx/ecx are width/height. The original binds the GL
   texture and re-uploads the level with glTexImage2D. The Metal path uploads
   into the texture created by _GfxInitializeTexture for the same header. */
void eu4_gfx_update_texture(void *ignored, const void *pixels, uint32_t width,
                            uint32_t height, uint32_t flags, void *header);

/* _GfxCreateRenderTargetTexture at 0x15ecb7f. Allocates the 0x44 header and
   initialises it. Returns the header. */
void *eu4_gfx_create_render_target_texture(void *handle, uint32_t width,
                                           uint32_t height, uint32_t format,
                                           uint32_t flags, uint32_t samples);

/* Borrowed depth texture paired with a render-target header (NULL when the
   header is not a render target). */
void *eu4_gfx_depth_texture_for_header(void *header);

/* _GfxClearTexture at 0x15ec836. Deletes the GL renderbuffer/framebuffer/
   texture names and clears the corresponding header fields. The Metal path has
   no GL names; release the backend texture for this header and preserve the
   engine-visible field mutations. */
void eu4_gfx_clear_texture(void *header);

/* Borrowed MTLTexture retained by the backend for this engine header after a
   successful _GfxInitializeTexture. NULL when the header is unknown. */
void *eu4_gfx_texture_for_header(void *header);

/* Borrowed MTLTexture for the engine header bound to unit 0 by the replacement
   _GfxSetTextures. A header substituted for a null entry (master+0x98) resolves
   to backend.fallback; unknown headers return NULL. */
void *eu4_gfx_bound_texture(void);

/* Forces the BC3 capability check to fail. The host sets it back to 0. */
void eu4_gfx_test_no_bc(int on);

/* Decoded SOpenGL sampler state (GL enums). `_GfxCreateSampler` stores the GL
   wrap at +0x18/+0x1c, mag at +0x20, min at +0x24 and the mip filter at +0x28. */
typedef struct {
    uint32_t min_filter;
    uint32_t mag_filter;
    uint32_t mip_filter;
    uint32_t wrap_s;
    uint32_t wrap_t;
    uint32_t wrap_r;
} eu4_gfx_sampler_state;

/* Returns 0 and fills `out`, or -1 when the sampler object is missing or uses a
   state the reconstructed backend cannot represent. */
int eu4_gfx_decode_sampler(const void *sampler, eu4_gfx_sampler_state *out);

/* The engine stores the base min filter and the mip filter separately. The
   effective GL min filter follows the bound texture: a texture with mip levels
   uses the engine's mip filter; a single-level texture uses the base min.
   Returns a GL min-filter enum (GL_LINEAR_MIPMAP_NEAREST when mipmapped). */
uint32_t eu4_gfx_effective_min_filter(const eu4_gfx_sampler_state *state,
                                      int has_mip_levels);

/* _GfxSetTextures at 0x15ecbf2. rdi is the handle and (%rdi) is the state object.
   esi is the count. rdx is the texture-pointer array. ecx bit 0 binds from r8d,
   and bit 1 binds from r8d + 0xf. r9 is an optional sampler-pointer array. A null
   texture is replaced by the pointer at state +0x98. The dword at state +0xa8 is
   compared with 1. The only calls are InternalSetTextureAndSamplerState at
   0x15ecc6d and 0x15eccc6. That helper's first control transfer is callq *(%rax)
   at 0x15ecd06 through ___glewActiveTextureARB. The 0xc crash did not capture
   that slot. D1a-only control-flow replacement. The CPU-side object is left
   unchanged. Texture and sampler binding is skipped. No Metal bind is
   implemented. A later draw_reached shows the game entered a draw, not that the
   original binding workload was reconstructed. */
void eu4_gfx_set_textures(void *handle, unsigned count, void *textures, unsigned flags,
                          unsigned base_unit, void *samplers);

#ifdef __cplusplus
}
#endif

#endif
