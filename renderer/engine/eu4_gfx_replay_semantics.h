#ifndef EU4_GFX_REPLAY_SEMANTICS_H
#define EU4_GFX_REPLAY_SEMANTICS_H

#include <stdint.h>

/* Engine _GfxClear flags: bit 0 color, bit 1 depth, bit 2 stencil.  They are
   not GLbitfield values; using a GL-style mask here clears depth for a
   color-only operation. */
static inline int eu4_gfx_clear_color(uint32_t flags) { return (flags & 1u) != 0u; }
static inline int eu4_gfx_clear_depth(uint32_t flags) { return (flags & 2u) != 0u; }
static inline int eu4_gfx_clear_stencil(uint32_t flags) { return (flags & 4u) != 0u; }

/* A depth-only or stencil-only clear does not change the color texture, so it
   must not advance the color generation. */
static inline int eu4_gfx_clear_advances_color(uint32_t flags) {
    return eu4_gfx_clear_color(flags);
}

#endif
