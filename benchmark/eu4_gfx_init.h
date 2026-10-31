#ifndef EU4_GFX_INIT_H
#define EU4_GFX_INIT_H

#include <stdint.h>

/* Memory image written by GetDefaultDepthStencil (0x15c6784). 40 bytes.
   Bytes 1..3 and 0xd..0xf are not stored by that function. */
#define EU4_GFX_DEPTH_DESC_SIZE 0x28

void eu4_gfx_write_default_depth_stencil(uint8_t desc[EU4_GFX_DEPTH_DESC_SIZE]);

/* Properties read back from the Metal descriptor this slice submits.
   Stencil fields are the same on both faces. */
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

/* 0 when desc is the verified default. -1 for any other byte pattern. */
int eu4_gfx_default_depth_stencil_view(const uint8_t *desc, eu4_gfx_depth_view *out);

/* Borrows device (id<MTLDevice>). Returns +1 MTLDepthStencilState, or NULL.
   The caller releases the state. The device is not retained or released.
   NULL device, NULL desc, or a non-default description returns NULL. */
void *eu4_gfx_default_depth_stencil(void *device, const uint8_t *desc);

#endif
