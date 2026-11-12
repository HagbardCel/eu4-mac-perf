#ifndef EU4_GFX_SIMPLE_DRAW_H
#define EU4_GFX_SIMPLE_DRAW_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Faithful Metal encoding of the Simple-technique draw reached at caller RVA
   0xedfcd1 inside CEU3Graphics::ShowBasicLoadText.

   The original body of _GfxDraw (RVA 0x15ea789) reads:
     mode   = master+0x08                      (GL_TRIANGLE_STRIP after enum 4)
     count  = rsi < 1 ? master+0x64 : rsi      (authentic: 4)
     shader = master+0x70                      (Simple effect program)
     slots  = master+0x130/0x150/0x160         (header/offset/stride, slot 0)
     cbuf   = master+0xd0                      (constant slot 2: 4x4 Matrix)
   and tails into glDrawArrays(mode, first, count). The Metal encoder replaces
   the GL uniform upload, vertex-attrib enable, texture bind and glDrawArrays
   with their Metal equivalents, and fails closed on any input it cannot encode
   faithfully. It does not invent engine state.

   One implementation serves both the offline diagnostic host and the live
   game backend. `eu4_gfx_simple_draw_encode_into` validates the original
   engine state and encodes into a caller-owned command buffer; the callers
   differ only in who creates/commits/waits that command buffer. */

typedef enum {
    EU4_GFX_SIMPLE_DRAW_OK = 0,
    EU4_GFX_SIMPLE_DRAW_CONTEXT = 1,   /* null immediate context or master */
    EU4_GFX_SIMPLE_DRAW_SHADER = 2,    /* +0x70 is not the admitted Simple effect */
    EU4_GFX_SIMPLE_DRAW_PRIMITIVE = 3, /* +0x08 is not GL_TRIANGLE_STRIP (5) */
    EU4_GFX_SIMPLE_DRAW_COUNT = 4,     /* effective count below 3 */
    EU4_GFX_SIMPLE_DRAW_VERTEX = 5,    /* slot 0 absent, wrong stride, bad range */
    EU4_GFX_SIMPLE_DRAW_CONSTANT = 6,  /* matrix constant absent or < 64 bytes */
    EU4_GFX_SIMPLE_DRAW_TEXTURE = 7,   /* no SimpleTexture image */
    EU4_GFX_SIMPLE_DRAW_TARGET = 8,    /* target or viewport invalid */
    EU4_GFX_SIMPLE_DRAW_PIPELINE = 9,  /* Metal pipeline construction failed */
    EU4_GFX_SIMPLE_DRAW_ENCODE = 10,   /* encoding or submission failed */
    EU4_GFX_SIMPLE_DRAW_STATE = 11,    /* blend/depth/raster/scissor not admitted */
} eu4_gfx_simple_draw_status;

#define EU4_GFX_SIMPLE_VERTEX_STRIDE 24

/* Master offsets read by the original _GfxDraw body. */
enum {
    EU4_GFX_MASTER_MODE = 0x08,
    EU4_GFX_MASTER_COUNT = 0x64,
    EU4_GFX_MASTER_SHADER = 0x70,
    EU4_GFX_MASTER_CONSTANT_SLOT2 = 0xd0,
    EU4_GFX_MASTER_VERTEX_HEADER = 0x130,
    EU4_GFX_MASTER_VERTEX_OFFSET = 0x150,
    EU4_GFX_MASTER_VERTEX_STRIDE = 0x160,
};

typedef struct {
    void *device;    /* id<MTLDevice>, borrowed */
    void *target;    /* id<MTLTexture>, BGRA8Unorm render target, borrowed */
    void *texture;   /* id<MTLTexture> bound as SimpleTexture, borrowed */
    /* Optional id<MTLCommandQueue>. Supplying the backend queue preserves
       submission order with _GfxClear/_GfxPresent; NULL is only for the
       offline wrapper. Not retained. */
    void *queue;
    uint32_t target_width;
    uint32_t target_height;
    /* Engine logical dimensions (master +0xa0/+0xa4). Zero means "same as the
       target", which keeps the offline host's existing 1:1 contract. When the
       physical drawable is scaled (HiDPI), the GL lower-left viewport is
       scaled by target/logical before conversion to Metal's top-left origin. */
    uint32_t logical_width;
    uint32_t logical_height;
    /* GL lower-left-origin viewport. */
    uint32_t viewport_x;
    uint32_t viewport_y;
    uint32_t viewport_width;
    uint32_t viewport_height;
    /* Optional RGBA clear. NULL keeps the target contents (the engine already
       cleared in CGraphics::ClearScene). */
    const float *clear_color;
} eu4_gfx_simple_draw_inputs;

/* Metadata observed while encoding. Every field is read from original engine
   objects or from the engine-owned Metal target; none is host-synthesized. */
typedef struct {
    char shader_identity[64];
    uint32_t primitive;
    int32_t count;
    int32_t first;
    uint32_t vertex_stride;
    uint32_t vertex_offset;
    uint64_t vertex_bytes;
    uint64_t vertex_hash;
    char vertex_hex[193];
    uint64_t constant_hash;
    char constant_hex[193];
    uint64_t texture_pointer;
    uint32_t texture_width;
    uint32_t texture_height;
    uint64_t target_pointer;
    uint32_t target_width;
    uint32_t target_height;
    uint32_t logical_width;
    uint32_t logical_height;
    uint32_t viewport_x;
    uint32_t viewport_y;
    uint32_t viewport_width;
    uint32_t viewport_height;
    int blend_supported;
    int depth_default;
    int raster_default;
    int scissor_full;
} eu4_gfx_simple_draw_state;

/* Validate original state and encode one draw into `command_buffer`.
   The caller owns the render pass, commit and completion. `command_buffer`
   is an id<MTLCommandBuffer> (void* to keep this header C-compatible). */
eu4_gfx_simple_draw_status eu4_gfx_simple_draw_encode_into(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, void *command_buffer,
    const char **reason, eu4_gfx_simple_draw_state *state);

/* Offline diagnostic wrapper: creates its own command buffer, commits, waits
   and reports the exact failure. Not the production submission contract. */
eu4_gfx_simple_draw_status eu4_gfx_simple_draw(const void *immediate_context, int32_t count,
                                               int32_t first,
                                               const eu4_gfx_simple_draw_inputs *inputs,
                                               const char **reason);

/* Same wrapper, additionally returning the observed state. */
eu4_gfx_simple_draw_status eu4_gfx_simple_draw_with_state(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, const char **reason,
    eu4_gfx_simple_draw_state *state);

const char *eu4_gfx_simple_draw_status_name(eu4_gfx_simple_draw_status status);

#ifdef __cplusplus
}
#endif

#endif
