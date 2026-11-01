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

int eu4_gfx_phase(void);
int eu4_gfx_backend_releases(void);

void *eu4_gfx_init(void *handle, uint8_t *success);
void eu4_gfx_shutdown(void *master);
void eu4_gfx_set_blend(void *handle, void *state);
void eu4_gfx_set_depth(void *handle, void *state);
void eu4_gfx_set_raster(void *handle, void *state);
int eu4_gfx_settings(int display, int windowed, int count, void *settings);

#ifdef __cplusplus
}
#endif

#endif
