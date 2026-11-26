#ifndef EU4_GFX_DRAW_ENCODE_H
#define EU4_GFX_DRAW_ENCODE_H

#include <stddef.h>
#include <stdint.h>

#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Semantic dispatcher for the reconstructed _GfxDraw operations.
 *
 * Dispatch is by the admitted engine shader identity, never by caller RVA.
 * Every admitted effect uses its own encoder; an unadmitted identity returns
 * UNSUPPORTED with a diagnostic. This is the one entry point used by both the
 * live physical target and the diagnostic logical target. */
typedef enum {
    EU4_GFX_DRAW_ENCODE_OK = 0,
    EU4_GFX_DRAW_ENCODE_UNSUPPORTED = 1,
    EU4_GFX_DRAW_ENCODE_FAILED = 2,
} eu4_gfx_draw_encode_result;

eu4_gfx_draw_encode_result eu4_gfx_draw_encode_into(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, void *command_buffer,
    char *diagnostic, size_t capacity, eu4_gfx_simple_draw_state *state);

#ifdef __cplusplus
}
#endif

#endif
