#ifndef EU4_GFX_INIT_H
#define EU4_GFX_INIT_H

#include <stddef.h>
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
/* Optional offscreen clear target. Shared BGRA8Unorm render target. */
@property (nonatomic, strong) id colorTarget;
@property (nonatomic) uint32_t colorWidth;
@property (nonatomic) uint32_t colorHeight;
@property (nonatomic) BOOL contentsKnown;
@property (nonatomic, strong) id pendingClear;
/* Game-owned intermediate backbuffer. Distinct from colorTarget. */
@property (nonatomic) BOOL gameWindow;
@property (nonatomic, strong) id gameColorTarget;
/* Physical drawable dimensions of gameColorTarget. */
@property (nonatomic) uint32_t gameWidth;
@property (nonatomic) uint32_t gameHeight;
/* Logical master +0xa0/+0xa4 and physical drawable tuple of the current
   effective generation. */
@property (nonatomic) uint32_t logicalWidth;
@property (nonatomic) uint32_t logicalHeight;
@property (nonatomic) uint32_t physicalWidth;
@property (nonatomic) uint32_t physicalHeight;
@property (nonatomic) uint64_t geometryGeneration;
@property (nonatomic) uint64_t pendingGameGeneration;
@property (nonatomic) uint64_t clearedGeneration;
@property (nonatomic) BOOL awaitingFreshClear;
@property (nonatomic) BOOL gameContentsKnown;
@property (nonatomic) BOOL readyForPresent;
@property (nonatomic, strong) id pendingGameCommand;
@property (nonatomic) BOOL poisoned;
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
/* When non-zero, the next offscreen texture allocation fails once. */
void eu4_gfx_test_fail_offscreen_alloc(int on);
/* When non-zero, the next game-backbuffer allocation fails once. */
void eu4_gfx_test_fail_game_alloc(int on);
/* When non-zero, present treats nextDrawable as nil once. */
void eu4_gfx_test_fail_next_drawable(int on);
/* When non-zero, present staging texture allocation fails once. */
void eu4_gfx_test_fail_present_staging_alloc(int on);
/* Borrowed MTLTexture used as the present destination instead of nextDrawable. */
void eu4_gfx_test_set_present_drawable(void *texture);
/* When dest is non-null, present blits the destination into staging and copies
   BGRA8 bytes into dest after a successful wait. Cleared after one present. */
void eu4_gfx_test_set_present_readback(void *dest, size_t capacity);

/* Select game-window mode before _GfxInit. metal_init calls this. Hosts that
   omit it stay on the diagnostic offscreen path. */
void eu4_gfx_enable_game_window(void);

/* Two-phase Cocoa geometry publication. Phase 1 records the intended physical
   size and marks a transition in progress; present/clear decline as stale until
   phase 2 commits. Phase 2 is called only after layer.drawableSize has reached
   that size. Neither call reads the engine master. */
void eu4_gfx_publish_geometry_transition(uint32_t width, uint32_t height);
void eu4_gfx_publish_physical_geometry(uint32_t width, uint32_t height);

typedef struct {
    uint32_t width;
    uint32_t height;
    uint64_t epoch;
    int valid;
    int transition_in_progress;
    uint32_t transition_width;
    uint32_t transition_height;
} eu4_gfx_physical_geometry_state;

/* Copy the committed physical snapshot. Returns 1 when a committed size exists.
   transition_in_progress is set between the two publication phases. */
int eu4_gfx_physical_geometry_snapshot(eu4_gfx_physical_geometry_state *out);
/* Effective generation of the current logical+physical tuple. 0 before init.
   Strictly monotonic: a repeated tuple does not reclaim an earlier value. */
uint64_t eu4_gfx_geometry_generation(void *master);

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
typedef enum {
    EU4_GFX_CLEAR_OK = 0,
    EU4_GFX_CLEAR_UNSUPPORTED = 1,
    EU4_GFX_CLEAR_FAILED = 2,
    /* Geometry changed since the last known contents. Distinct from
       unsupported/failed: nothing was waited on, submitted, or presented. */
    EU4_GFX_CLEAR_GEOMETRY_STALE = 3,
} eu4_gfx_clear_status;

/* Create or replace the offscreen BGRA8Unorm color target. Rejects while a
   clear is pending. Allocates before publishing so a failed allocate leaves
   the prior target intact. Resets contentsKnown. */
eu4_gfx_clear_status eu4_gfx_configure_offscreen(void *master, uint32_t width,
                                                 uint32_t height);
/* Returns 1 when contentsKnown is set. */
int eu4_gfx_offscreen_contents_known(void *master);
/* _GfxClear at 0x15ea552. rdi is the handle and (%rdi) is the state object.
   esi is the flag dword: bit 0 is color (0x4000), bit 1 is depth (0x100), and
   bit 2 is stencil (0x400). rdx is an optional color; null is (0, 0, 0, 1).
   The glScissor at 0x15ea5e7 uses the logical viewport image: x at +0xbc, y at
   +0xc0, width at +0xb0, height at +0xac. Production routes to the game
   backbuffer when game mode is on and +0x78 is null; otherwise to the
   diagnostic offscreen when configured. A geometry-transition stale skip
   returns normally; unsupported or failed cases stop. */
void eu4_gfx_clear(void *handle, unsigned flags, const float *color);
/* Diagnostic offscreen only. Validate and commit without waiting. ok means
   submitted. Rejects a second clear while one is pending. Invalidates
   contentsKnown on submit. */
eu4_gfx_clear_status eu4_gfx_clear_checked(void *handle, unsigned flags,
                                           const float *color);
/* Wait for the pending diagnostic clear, check terminal status, update
   contentsKnown. Writes the Metal command-buffer status when non-null. */
eu4_gfx_clear_status eu4_gfx_complete_offscreen(void *master, int *command_status);
/* Read known diagnostic BGRA8 bytes. capacity must be at least width*height*4. */
eu4_gfx_clear_status eu4_gfx_read_offscreen(void *master, void *dest, size_t capacity,
                                            size_t *out_len);
/* Wait for the pending game clear. On OK sets gameContentsKnown and
   readyForPresent only when the pending generation is still current. */
eu4_gfx_clear_status eu4_gfx_complete_game(void *master, int *command_status);
int eu4_gfx_game_contents_known(void *master);
int eu4_gfx_ready_for_present(void *master);
/* _GfxSetRenderTarget at 0x15ed004. null selects the default game backbuffer.
   Non-null is unsupported, does not mutate the master, and does not drain.
   On a verified geometry transition a pending clear may be drained and the
   replacement allocated before the master viewport/+0x78 writes. */
eu4_gfx_clear_status eu4_gfx_set_render_target_checked(void *handle, void *target);
void eu4_gfx_set_render_target(void *handle, void *target);
/* _GfxPresent at 0x15ea6cb. rdi is the master. Returns 1 on success.
   The destination must be exactly the game backbuffer physical size and
   BGRA8Unorm; otherwise, with unchanged geometry, the call fails before submit
   and keeps gameContentsKnown and readyForPresent unchanged. A geometry
   transition returns GEOMETRY_STALE before acquiring a drawable. */
eu4_gfx_clear_status eu4_gfx_present_checked(void *master);
int eu4_gfx_present(void *master);
int eu4_gfx_settings(int display, int windowed, int count, void *settings);

#ifdef __cplusplus
}
#endif

#endif
