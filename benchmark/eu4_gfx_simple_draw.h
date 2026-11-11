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
     cbuf   = master+0xf0                      (constant slot 2: 4x4 Matrix)
   and tails into glDrawArrays(mode, first, count). The Metal encoder replaces
   the GL uniform upload, vertex-attrib enable, texture bind and glDrawArrays
   with their Metal equivalents, and fails closed on any input it cannot encode
   faithfully. It does not invent engine state. */

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
} eu4_gfx_simple_draw_status;

#define EU4_GFX_SIMPLE_VERTEX_STRIDE 24

typedef struct {
    void *device;    /* id<MTLDevice>, borrowed */
    void *target;    /* id<MTLTexture>, BGRA8Unorm render target, borrowed */
    void *texture;   /* id<MTLTexture> bound as SimpleTexture, borrowed */
    /* Optional id<MTLCommandQueue>. Supplying the backend queue preserves
       submission order with _GfxClear/_GfxPresent; NULL creates a private
       queue, which has no ordering against backend work (offline hosts that
       clear and wait first). Not retained. */
    void *queue;
    uint32_t target_width;
    uint32_t target_height;
    /* GL lower-left-origin viewport. The encoder converts y to Metal's
       top-left origin; the authentic full-attachment viewport is 0,0 and is
       unchanged by that conversion. */
    uint32_t viewport_x;
    uint32_t viewport_y;
    uint32_t viewport_width;
    uint32_t viewport_height;
    /* Optional RGBA clear. NULL keeps the target contents (the engine already
       cleared in CGraphics::ClearScene). */
    const float *clear_color;
} eu4_gfx_simple_draw_inputs;

/* Encodes, submits and waits for the draw. On non-OK, *reason (when non-null)
   names the refused input. The draw retains no borrowed resource after return;
   only the process-local sampler cache keeps its device and sampler alive.

   Submission boundary: the wait is an offline diagnostic contract, not the
   production engine contract. It lets this host read the target and report the
   exact failure, and it keeps borrowed buffers/textures alive until the GPU has
   finished with them. Routing the encoder into live _GfxDraw requires a
   non-blocking command-buffer lifetime owned by the backend scheduler; that
   wiring is deliberately not part of this offline milestone. */
eu4_gfx_simple_draw_status eu4_gfx_simple_draw(const void *immediate_context, int32_t count,
                                               int32_t first,
                                               const eu4_gfx_simple_draw_inputs *inputs,
                                               const char **reason);

const char *eu4_gfx_simple_draw_status_name(eu4_gfx_simple_draw_status status);

/* Engine master fields consumed by the encoder. Values are offsets, not
   pointers, so a caller can resolve them without a private layout copy. */
enum {
    EU4_GFX_MASTER_MODE = 0x08,
    EU4_GFX_MASTER_COUNT = 0x64,
    EU4_GFX_MASTER_SHADER = 0x70,
    EU4_GFX_MASTER_CONSTANT_SLOT2 = 0xf0,
    EU4_GFX_MASTER_VERTEX_HEADER = 0x130,
    EU4_GFX_MASTER_VERTEX_OFFSET = 0x150,
    EU4_GFX_MASTER_VERTEX_STRIDE = 0x160,
};

#ifdef __cplusplus
}
#endif

#endif
