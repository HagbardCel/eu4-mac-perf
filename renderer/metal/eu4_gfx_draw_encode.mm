#import "eu4_gfx_draw_encode.h"

#import "eu4_gfx_shader.h"
#import "eu4_gfx_simple_draw.h"
#import "eu4_gfx_sprite_draw.h"

#include <stdio.h>
#include <string.h>

static void *load_ptr(const uint8_t *base, size_t offset) {
    void *value = NULL;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static void describe(char *diagnostic, size_t capacity, const char *effect,
                     const char *status, const char *detail) {
    if (diagnostic != NULL && capacity > 0) {
        snprintf(diagnostic, capacity, "%s encoder refused the draw: %s (%s)",
                 effect != NULL ? effect : "unknown", status != NULL ? status : "unknown",
                 detail != NULL ? detail : "no detail");
    }
}

eu4_gfx_draw_encode_result eu4_gfx_draw_encode_into(
    const void *immediate_context, int32_t count, int32_t first,
    const eu4_gfx_simple_draw_inputs *inputs, void *command_buffer,
    char *diagnostic, size_t capacity, eu4_gfx_simple_draw_state *state) {
    if (diagnostic != NULL && capacity > 0) {
        diagnostic[0] = 0;
    }
    if (immediate_context == NULL) {
        describe(diagnostic, capacity, "draw", "context", "null immediate context");
        return EU4_GFX_DRAW_ENCODE_FAILED;
    }
    const uint8_t *master = (const uint8_t *)load_ptr((const uint8_t *)immediate_context, 0);
    if (master == NULL) {
        describe(diagnostic, capacity, "draw", "context", "null master");
        return EU4_GFX_DRAW_ENCODE_FAILED;
    }
    void *shader = load_ptr(master, EU4_GFX_MASTER_SHADER);
    const char *identity = shader != NULL ? eu4_gfx_shader_identity(shader) : NULL;
    const char *reason = NULL;
    if (identity != NULL && strcmp(identity, EU4_GFX_SHADER_SIMPLE_IDENTITY) == 0) {
        eu4_gfx_simple_draw_status status = eu4_gfx_simple_draw_encode_into(
            immediate_context, count, first, inputs, command_buffer, &reason, state);
        if (status == EU4_GFX_SIMPLE_DRAW_OK) {
            return EU4_GFX_DRAW_ENCODE_OK;
        }
        describe(diagnostic, capacity, identity,
                 eu4_gfx_simple_draw_status_name(status), reason);
        return status == EU4_GFX_SIMPLE_DRAW_SHADER ? EU4_GFX_DRAW_ENCODE_UNSUPPORTED
                                                    : EU4_GFX_DRAW_ENCODE_FAILED;
    }
    if (identity != NULL && strcmp(identity, EU4_GFX_SHADER_SPRITE_IDENTITY) == 0) {
        eu4_gfx_sprite_draw_status status = eu4_gfx_sprite_draw_encode_into(
            immediate_context, count, first, inputs, command_buffer, &reason, state);
        if (status == EU4_GFX_SPRITE_DRAW_OK) {
            return EU4_GFX_DRAW_ENCODE_OK;
        }
        describe(diagnostic, capacity, identity,
                 eu4_gfx_sprite_draw_status_name(status), reason);
        return status == EU4_GFX_SPRITE_DRAW_SHADER ? EU4_GFX_DRAW_ENCODE_UNSUPPORTED
                                                    : EU4_GFX_DRAW_ENCODE_FAILED;
    }
    describe(diagnostic, capacity, identity != NULL ? identity : "unlinked",
             "unsupported", "shader identity is not admitted by the live dispatcher");
    return EU4_GFX_DRAW_ENCODE_FAILED;
}
