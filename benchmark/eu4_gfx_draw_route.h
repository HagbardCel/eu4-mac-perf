#ifndef EU4_GFX_DRAW_ROUTE_H
#define EU4_GFX_DRAW_ROUTE_H

#include <stddef.h>
#include <stdint.h>

#include "eu4_gfx_draw_capture.h"
#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* One semantic decision for a captured _GfxDraw-family invocation. The caller
   RVA is deliberately not an input: it selects diagnostics elsewhere, never
   the renderer. */
typedef enum {
    EU4_GFX_DRAW_ROUTE_HANDLED = 0,
    EU4_GFX_DRAW_ROUTE_UNSUPPORTED = 1,
    EU4_GFX_DRAW_ROUTE_FAILED = 2,
} eu4_gfx_draw_route_result;

typedef eu4_gfx_draw_route_result (*eu4_gfx_draw_route_fn)(
    const eu4_gfx_draw_raw *raw, eu4_gfx_simple_draw_state *state,
    char *diagnostic, size_t capacity);

/* Install the live route in a Metal boot dylib. The capture host leaves it
   unset and keeps the stop-only diagnostic behavior. */
void eu4_gfx_draw_route_install(eu4_gfx_draw_route_fn route);
eu4_gfx_draw_route_fn eu4_gfx_draw_route_current(void);

/* Write one bounded diagnostic route record. Exposed so the live route can
   record the handled invocation before a checkpoint terminates the process. */
void eu4_gfx_draw_route_record(const char *name, const char *route, const char *reason,
                               const eu4_gfx_simple_draw_state *state,
                               const eu4_gfx_draw_raw *raw, uint64_t caller_rva);

/* The supported dispatcher. Explicit cases only: _GfxDraw + the admitted
   Simple effect. Indexed and instanced variants stop with a precise reason. */
eu4_gfx_draw_route_result eu4_gfx_draw_route_live(
    const eu4_gfx_draw_raw *raw, eu4_gfx_simple_draw_state *state,
    char *diagnostic, size_t capacity);

/* Last successfully handled draw, for later diagnostic stages (presentation).
   Returns 0 when no draw has been handled in this process. */
int eu4_gfx_draw_route_last(eu4_gfx_simple_draw_state *state,
                            uint64_t *caller_rva);

/* Coordinate space of the engine viewport for one draw target. Only the game
   backbuffer is physically scaled relative to the window's logical extent
   (HiDPI); an offscreen render target is allocated at the engine's logical
   size, so its viewport is already in target pixels. Exposed for the host
   regression that pins the offscreen case. */
void eu4_gfx_draw_route_logical_extent(
    int offscreen_target, uint32_t target_width, uint32_t target_height,
    uint32_t window_width, uint32_t window_height,
    uint32_t *logical_width, uint32_t *logical_height);

#ifdef __cplusplus
}
#endif

#endif
