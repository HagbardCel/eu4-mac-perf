#ifndef EU4_GFX_GENERIC_DRAW_H
#define EU4_GFX_GENERIC_DRAW_H

#include <stddef.h>
#include <stdint.h>

#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Performs a deferred after-composite diagnostic readback, once the committed
   draw has completed. Returns 0 when nothing was armed or the write succeeded.
   No effect unless a composite dump was armed. */
int eu4_gfx_generic_after_dump(void);

/* Diagnostic texture dump (BGRA8, bottom-up, BC-aware). Returns 0 only when the
   pixel file was fully written. */
int eu4_gfx_dump_texture(void *texture, const char *path);

/* Decodes a selected Metal texture (including BC1/BC2/BC3) into a bottom-up
   BGRA8 image via nearest texel-centre sampling. Returns 0 on success and
   hands ownership of `out` to the caller. Diagnostic use only. */
int eu4_gfx_decode_texture_rgba8(void *texture, unsigned char **out,
                                 size_t *row, size_t *height);

/* Generic live encoder for shaders admitted from the translated runtime cache.
 *
 * The encoder reads the same engine state the original _GfxDraw* bodies read
 * (master mode/count, current shader, vertex-buffer element table, constant
 * slots, bound textures, blend/depth/raster snapshots) and encodes the draw
 * through the MSL functions of the cached shader. It never selects by caller
 * RVA and has no scene-specific branches. */

typedef struct {
    int indexed;          /* 1 for _GfxDrawIndexed / Instanced */
    int32_t count;        /* index count when indexed, vertex count otherwise */
    int32_t first;        /* first vertex for arrays */
    int32_t base_vertex;  /* glDrawElementsBaseVertex base */
    int32_t instance_count;
    /* Primitive mode captured at draw entry (engine state can also be
       mutated by other rendering threads after the entry). */
    uint32_t primitive;
} eu4_gfx_generic_call;

/* Encodes one draw into a caller-owned command buffer. Returns 1 on success.
 * On failure it fills reason[0..capacity) and returns 0. The reason is owned by
 * the caller, so it stays valid after this function returns. */
int eu4_gfx_generic_draw_encode_into(const void *immediate_context,
                                     const eu4_gfx_generic_call *call,
                                     const eu4_gfx_simple_draw_inputs *inputs,
                                     void *command_buffer, char *reason,
                                     size_t reason_capacity,
                                     eu4_gfx_simple_draw_state *state);

/* Decides whether a bound texture may feed a sampler declared as 2D or cube.
   Returns 1 when the binding is valid (optionally with a diagnostic
   substitution) and 0 when the draw must be refused. */
int eu4_gfx_texture_binding_decision(int expects_cube, int bound_type,
                                     int allow_substitution, int *substitute);

/* 1 when this draw has everything the generic encoder needs; otherwise writes
 * the reason. No Metal objects are created. */
int eu4_gfx_generic_draw_supported(const void *immediate_context,
                                   const eu4_gfx_generic_call *call,
                                   char *reason, size_t capacity);

/* Number of draws encoded by this module (diagnostics). */
uint64_t eu4_gfx_generic_draw_count(void);

#ifdef __cplusplus
}
#endif

#endif
