#import "eu4_gfx_draw_route.h"

#import "eu4_gfx_draw_encode.h"
#import "eu4_gfx_generic_draw.h"
#import "eu4_gfx_shader.h"

#import "eu4_gfx_checkpoint.h"
#import "eu4_gfx_init.h"
#import "eu4_gfx_logical_capture.h"
#import "eu4_metal_events.h"

#import <Metal/Metal.h>
#import <stdio.h>
#import <string.h>

static eu4_gfx_simple_draw_state g_last_state;
static uint64_t g_last_caller_rva;
static int g_have_last;

int eu4_gfx_draw_route_last(eu4_gfx_simple_draw_state *state,
                            uint64_t *caller_rva) {
    if (!g_have_last) {
        return 0;
    }
    if (state != NULL) {
        *state = g_last_state;
    }
    if (caller_rva != NULL) {
        *caller_rva = g_last_caller_rva;
    }
    return 1;
}

static void *load_ptr(const uint8_t *base, size_t offset) {
    void *value = NULL;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static uint32_t load_u32(const uint8_t *base, size_t offset) {
    uint32_t value = 0;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static eu4_gfx_draw_route_result refuse(eu4_gfx_draw_route_result result, char *diagnostic,
                                        size_t capacity, const char *text) {
    if (diagnostic != NULL && capacity > 0) {
        snprintf(diagnostic, capacity, "%s", text);
    }
    return result;
}

eu4_gfx_draw_route_result eu4_gfx_draw_route_live(
    const eu4_gfx_draw_raw *raw, eu4_gfx_simple_draw_state *state,
    char *diagnostic, size_t capacity) {
    if (diagnostic != NULL && capacity > 0) {
        diagnostic[0] = 0;
    }
    if (state != NULL) {
        memset(state, 0, sizeof *state);
    }
    if (raw == NULL) {
        return refuse(EU4_GFX_DRAW_ROUTE_FAILED, diagnostic, capacity, "null draw frame");
    }
    if (raw->which != 1 && raw->which != 2 && raw->which != 3) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "unknown draw variant");
    }

    const uint8_t *handle = (const uint8_t *)(uintptr_t)raw->rdi;
    const uint8_t *master = handle != NULL ? (const uint8_t *)load_ptr(handle, 0) : NULL;
    if (master == NULL) {
        return refuse(EU4_GFX_DRAW_ROUTE_FAILED, diagnostic, capacity,
                      "draw has no live immediate context");
    }
    __unsafe_unretained EU4GfxBackend *backend =
        (__bridge EU4GfxBackend *)load_ptr(master, 0x180);
    if (backend == nil || backend.poisoned || !backend.gameWindow) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "no usable Metal game backend");
    }
    uint64_t generation = backend.geometryGeneration;
    if (backend.gameClearGeneration == 0 || backend.gameClearGeneration != generation) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "draw arrived before an engine clear for the current geometry generation");
    }
    if (backend.queue == nil) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "draw has no Metal queue");
    }
    id current_target = backend.currentTargetColor;
    if (current_target == nil) {
        if (backend.gameColorTarget == nil ||
            backend.gameWidth != backend.physicalWidth ||
            backend.gameHeight != backend.physicalHeight ||
            backend.gameWidth == 0 || backend.gameHeight == 0) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "game backbuffer is absent or does not match the committed geometry");
        }
        current_target = backend.gameColorTarget;
    }
    eu4_gfx_physical_geometry_state published;
    if (eu4_gfx_physical_geometry_snapshot(&published) && published.valid) {
        if (published.transition_in_progress) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "draw arrived during a physical geometry transition");
        }
        if (published.width != backend.gameWidth || published.height != backend.gameHeight) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "game backbuffer belongs to an older physical generation");
        }
    }
    if (backend.pendingGameCommand != nil &&
        backend.pendingGameGeneration != generation) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "an older-generation GPU command is still pending");
    }

    uint32_t viewport_height = load_u32(master, 0xac);
    uint32_t viewport_width = load_u32(master, 0xb0);
    uint32_t viewport_x = load_u32(master, 0xbc);
    uint32_t viewport_y = load_u32(master, 0xc0);
    if (viewport_width == 0 || viewport_height == 0) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "engine viewport is unset");
    }

    eu4_gfx_simple_draw_inputs inputs = {};
    inputs.device = (__bridge void *)backend.device;
    inputs.target = (__bridge void *)current_target;
    inputs.queue = NULL; /* the backend queue is used by the command buffer below */
    inputs.target_width = backend.currentTargetColor != nil
        ? backend.currentTargetWidth : backend.gameWidth;
    inputs.target_height = backend.currentTargetColor != nil
        ? backend.currentTargetHeight : backend.gameHeight;
    inputs.logical_width = backend.logicalWidth;
    inputs.logical_height = backend.logicalHeight;
    inputs.viewport_x = viewport_x;
    inputs.viewport_y = viewport_y;
    inputs.viewport_width = viewport_width;
    inputs.viewport_height = viewport_height;
    inputs.clear_color = NULL; /* _GfxClear already established the contents */

    id<MTLCommandBuffer> command = [backend.queue commandBuffer];
    if (command == nil) {
        return refuse(EU4_GFX_DRAW_ROUTE_FAILED, diagnostic, capacity,
                      "backend queue refused a command buffer");
    }
    char text[256];
    void *current_shader = load_ptr(master, 0x70);
    if (current_shader == NULL) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "draw has no admitted current shader");
    }
    if (eu4_gfx_shader_is_generic(current_shader)) {
        int32_t count32 = (int32_t)raw->rsi;
        eu4_gfx_generic_call call = {0};
        call.instance_count = 1;
        if (raw->which == 1) {
            call.indexed = 0;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x64);
            }
            call.count = count32;
            call.first = (int32_t)raw->rdx;
        } else if (raw->which == 2) {
            call.indexed = 1;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x68);
            }
            call.count = count32;
            call.base_vertex = (int32_t)raw->rcx;
        } else {
            call.indexed = 1;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x68);
            }
            call.count = count32;
            call.instance_count = (int32_t)raw->rdx > 0 ? (int32_t)raw->rdx : 1;
        }
        const char *generic_reason = NULL;
        if (!eu4_gfx_generic_draw_encode_into((const void *)handle, &call, &inputs,
                                              (__bridge void *)command,
                                              &generic_reason, state)) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          generic_reason != NULL && generic_reason[0] != 0
                              ? generic_reason
                              : "generic encoder refused the draw");
        }
    } else {
        if (!eu4_gfx_shader_is_linked(current_shader)) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "current shader is not linked");
        }
        if (raw->which != 1) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          raw->which == 2
                              ? "_GfxDrawIndexed has no admitted encoder for this shader"
                              : "_GfxDrawIndexedInstanced has no admitted encoder for this shader");
        }
        eu4_gfx_draw_encode_result encoded = eu4_gfx_draw_encode_into(
            (const void *)handle, (int32_t)raw->rsi, (int32_t)raw->rdx, &inputs,
            (__bridge void *)command, text, sizeof text, state);
        if (encoded != EU4_GFX_DRAW_ENCODE_OK) {
            return refuse(encoded == EU4_GFX_DRAW_ENCODE_UNSUPPORTED
                              ? EU4_GFX_DRAW_ROUTE_UNSUPPORTED
                              : EU4_GFX_DRAW_ROUTE_FAILED,
                          diagnostic, capacity,
                          text[0] != 0 ? text : "draw encoder refused the draw");
        }
    }
    [command commit];
    /* pendingGameCommand now owns the last game submission. It is still one
       in-order queue: clear, every draw and the present blit are serialized by
       Metal. Present drains this last command; completion of the last command
       implies completion of every earlier one on the queue. */
    backend.pendingGameCommand = command;
    backend.pendingGameGeneration = generation;
    backend.gameContentsKnown = NO;
    backend.readyForPresent = NO;

    /* Record the handled route before the checkpoint may terminate the
       process. The capture layer would otherwise never observe the return. */
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe((const void *)(uintptr_t)raw->retaddr, NULL, 0,
                              &caller_rva, &inside, &thread, &pid);
    if (state != NULL) {
        g_last_state = *state;
        g_last_caller_rva = caller_rva;
        g_have_last = 1;
    }
    eu4_gfx_draw_route_record("draw-route.json", "live", "", state, raw, caller_rva);
    (void)eu4_gfx_logical_capture_from_env(
        (__bridge void *)backend.device, (__bridge void *)backend.queue,
        (const void *)handle, state != NULL ? state->count : (int32_t)raw->rsi,
        (int32_t)raw->rdx, &inputs, state, caller_rva);
    eu4_gfx_checkpoint_after_draw((void *)master, raw, state);
    return EU4_GFX_DRAW_ROUTE_HANDLED;
}
