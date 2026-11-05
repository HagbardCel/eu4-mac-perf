#ifndef EU4_GFX_INIT_H
#define EU4_GFX_INIT_H

#include <stdint.h>

#ifdef __OBJC__
#import <Foundation/Foundation.h>

/* One owner of the Metal objects and the four engine headers. Master +0x180
   holds the single __bridge_retained pointer. */
@interface EU4GfxBackend : NSObject
@property (nonatomic, strong) id layer;
@property (nonatomic, strong) id device;
@property (nonatomic, strong) id queue;
@property (nonatomic, strong) id depthState;
@property (nonatomic, strong) id fallback;
@property (nonatomic) void *blendHeader;
@property (nonatomic) void *depthHeader;
@property (nonatomic) void *rasterHeader;
@property (nonatomic) void *textureHeader;
/* Textures created by _GfxInitializeTexture. Released with the backend. */
@property (nonatomic, strong) NSMutableDictionary *textures;
/* Pending GL scissor box, fixed at the call. Signed. Not a Metal rectangle. */
@property (nonatomic) BOOL scissorValid;
@property (nonatomic) int32_t scissorGlX;
@property (nonatomic) int32_t scissorGlY;
@property (nonatomic) int32_t scissorWidth;
@property (nonatomic) int32_t scissorHeight;
@end
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Memory image written by GetDefaultDepthStencil (0x15c6784). 40 bytes.
   Bytes 1..3 and 0xd..0xf are not stored by that function. */
#define EU4_GFX_DEPTH_DESC_SIZE 0x28
#define EU4_GFX_MASTER_SIZE 0x198
#define EU4_GFX_MASTER_CONTEXT_RVA 0x2238570

void eu4_gfx_write_default_depth_stencil(uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE]);
void eu4_gfx_write_default_blend(void *dest);
void eu4_gfx_write_default_raster(void *dest);

typedef struct {
    int depth_compare;
    int depth_write;
    int stencil_compare;
    int stencil_fail;
    int stencil_depth_fail;
    int stencil_pass;
    uint32_t stencil_read_mask;
    uint32_t stencil_write_mask;
} eu4_gfx_depth_view;

int eu4_gfx_default_depth_stencil_view(const uint8_t *desc, eu4_gfx_depth_view *out);

/* Borrows device (id<MTLDevice>). Returns +1 MTLDepthStencilState, or NULL.
   The caller releases the state. The device is not retained or released.
   NULL device, NULL desc, or a non-default description returns NULL. */
void *eu4_gfx_default_depth_stencil(void *device, const uint8_t *desc);

/* Weak in the host. The boot dylib's copy returns the child-view layer. */
void *eu4_metal_child_layer(void);

void eu4_gfx_set_layer(void *layer);
void eu4_gfx_set_image(uint8_t *image, void **master_slot);
void eu4_gfx_set_sdl(void *(*window_from_id)(uint32_t window_id),
                     void (*window_size)(void *window, int *width, int *height));
void eu4_gfx_set_default_writers(void (*blend)(void *dest), void (*depth)(void *dest),
                                 void (*raster)(void *dest));
void eu4_gfx_set_terminate(void (*fn)(const char *reason));
void eu4_gfx_test_throw_on_alloc(int n);
void eu4_gfx_test_no_device(int on);

/* Records the caller, writes stop.json, then terminates. A callback that
   returns still ends in _exit. */
__attribute__((noreturn)) void eu4_gfx_stop(const char *symbol, const char *reason,
                                            const void *caller);
const void *eu4_gfx_test_stop_caller(void);
uint8_t *eu4_gfx_image(void);
/* Borrowed device and the non-owning backend token at master+0x180. */
void *eu4_gfx_metal_device(void);
void *eu4_gfx_backend_token(void);

int eu4_gfx_phase(void);
int eu4_gfx_backend_releases(void);

void *eu4_gfx_init(void *handle, uint8_t *success);
void eu4_gfx_shutdown(void *master);
void eu4_gfx_set_blend(void *handle, void *state);
void eu4_gfx_set_depth(void *handle, void *state);
void eu4_gfx_set_raster(void *handle, void *state);
/* rdi is the master. Returns 1 and does not write the master. */
int eu4_gfx_reset(void *master);
/* rdi is the master. Force-applies +0x80, +0x88, and +0x90. */
void eu4_gfx_on_reset_master(void *master);
/* rdi is the master. rsi is 24 bytes at CGraphics+0x2c57c, disjoint from the master. */
void eu4_gfx_set_viewport(void *master, const void *rect);
/* _GfxSetScissorRectangle at 0x15ed0aa. rdi is the immediate-context handle and
   (%rdi) is the master. rsi is four int16_t: x1, y1, x2, y2. The original clamps
   negative spans and calls glScissor(x1, master_height - y2, x2-x1, y2-y1). This
   replacement stores that signed GL box. It does not call GL or Metal, and it
   does not enable scissor testing. */
void eu4_gfx_set_scissor_rectangle(void *handle, const void *rect);
/* Returns 1 and writes gl_x, gl_y, width, height when a box is pending. */
int eu4_gfx_scissor_rectangle(void *master, int32_t out[4]);

typedef enum {
    EU4_GFX_SCISSOR_UNSET = 0,
    EU4_GFX_SCISSOR_EMPTY = 1,
    EU4_GFX_SCISSOR_VALID = 2,
} eu4_gfx_scissor_status;

typedef struct {
    uint32_t x;
    uint32_t y;
    uint32_t width;
    uint32_t height;
} eu4_gfx_metal_scissor;

/* Intersect the stored GL box with one attachment. set is 0 when no box was
   recorded. A valid result is nonempty and inside the attachment. Empty and
   unset leave *out unchanged. This does not apply the rectangle.
   A later encoder must recover GL_SCISSOR_TEST separately:
   disabled: do not restrict drawing with the pending box;
   enabled and valid: apply the returned rectangle;
   enabled and empty: no pixels may be modified by scissor-affected draws or
   clears, rather than drawing or clearing the whole attachment;
   enabled and unset: the box was never overridden, so recover the real GL
   initial box before using a full-target default. */
eu4_gfx_scissor_status eu4_gfx_scissor_for_attachment(
    int set, int32_t gl_x, int32_t gl_y, int32_t width, int32_t height,
    uint32_t attachment_width, uint32_t attachment_height, eu4_gfx_metal_scissor *out);
/* _GfxClear at 0x15ea552. rdi is the handle and (%rdi) is the state object.
   esi is the flag dword: bit 0 is color (0x4000), bit 1 is depth (0x100), and
   bit 2 is stencil (0x400). rdx is an optional color; null is (0, 0, 0, 1).
   The glScissor at 0x15ea5e7 uses the viewport image: x at +0xbc, y at +0xc0,
   width at +0xb0, height at +0xac. D1a-only no-op. The original function
   performs GL state changes and a conditional clear but does not modify the
   master object. No Metal render target or render pass is available. This
   does not implement GPU clearing. */
void eu4_gfx_clear(void *handle, unsigned flags, const float *color);
int eu4_gfx_settings(int display, int windowed, int count, void *settings);

#ifdef __cplusplus
}
#endif

#endif
