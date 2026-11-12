#ifndef EU4_GFX_SPRITE_DRAW_H
#define EU4_GFX_SPRITE_DRAW_H

#include <stddef.h>
#include <stdint.h>

#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Authentic Metal encoding of the loading-screen sprite/font draw reached at
   caller RVA 0x14f6c1b (CSpriteType::RenderInternal +2271).

   The original GLSL (captured from the live program) is:
     VS: UNIFORM0[6] = { matrix columns, Color, (vXOffset,..) }
         position = mul(Matrix, float4(position,1)); texcoord.x += vXOffset
     PS: OutColor = tex2D(MapTexture, texcoord) * Color
   Vertex layout: stride 20, float3 position at +0, float2 texcoord at +12.
   State: alpha blend SRC_ALPHA / ONE_MINUS_SRC_ALPHA, depth off.

   The encoder fails closed on every input it cannot reproduce; it never
   substitutes state. One implementation serves the physical game target and
   the diagnostic logical target. */

typedef enum {
    EU4_GFX_SPRITE_DRAW_OK = 0,
    EU4_GFX_SPRITE_DRAW_CONTEXT = 1,
    EU4_GFX_SPRITE_DRAW_SHADER = 2,
    EU4_GFX_SPRITE_DRAW_PRIMITIVE = 3,
    EU4_GFX_SPRITE_DRAW_COUNT = 4,
    EU4_GFX_SPRITE_DRAW_VERTEX = 5,
    EU4_GFX_SPRITE_DRAW_CONSTANT = 6,
    EU4_GFX_SPRITE_DRAW_TEXTURE = 7,
    EU4_GFX_SPRITE_DRAW_TARGET = 8,
    EU4_GFX_SPRITE_DRAW_PIPELINE = 9,
    EU4_GFX_SPRITE_DRAW_ENCODE = 10,
    EU4_GFX_SPRITE_DRAW_STATE = 11,
} eu4_gfx_sprite_draw_status;

#define EU4_GFX_SPRITE_VERTEX_STRIDE 20
#define EU4_GFX_SPRITE_CONSTANT_BYTES 96

eu4_gfx_sprite_draw_status eu4_gfx_sprite_draw_encode_into(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, void *command_buffer,
    const char **reason, eu4_gfx_simple_draw_state *state);

const char *eu4_gfx_sprite_draw_status_name(eu4_gfx_sprite_draw_status status);

#ifdef __cplusplus
}
#endif

#endif
