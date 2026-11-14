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
static const eu4_gfx_draw_raw *g_refusal_raw;
static uint64_t g_last_caller_rva;
static int g_have_last;

void eu4_gfx_draw_route_logical_extent(
    int offscreen_target, uint32_t target_width, uint32_t target_height,
    uint32_t window_width, uint32_t window_height,
    uint32_t *logical_width, uint32_t *logical_height) {
    if (logical_width == NULL || logical_height == NULL) {
        return;
    }
    if (offscreen_target) {
        /* Render targets are allocated at the engine's logical size; the
           viewport is already expressed in their pixels. Applying the window's
           HiDPI ratio here would render only a corner of the target. */
        *logical_width = target_width != 0 ? target_width : window_width;
        *logical_height = target_height != 0 ? target_height : window_height;
        return;
    }
    *logical_width = window_width;
    *logical_height = window_height;
}

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

static void record_refusal(eu4_gfx_draw_route_result route, const char *text) {
    if (g_refusal_raw == NULL) {
        return;
    }
    uint64_t rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe((const void *)(uintptr_t)g_refusal_raw->retaddr, NULL,
                              0, &rva, &inside, &thread, &pid);
    char name[96];
    snprintf(name, sizeof name, "draw-refused-%llx.json",
             (unsigned long long)rva);
    char body[768];
    int n = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-draw-refused/v1\",\"route\":%d,"
        "\"variant\":%u,\"caller_rva\":\"0x%llx\",\"rsi\":%lld,"
        "\"rdx\":%lld,\"rcx\":%lld,\"reason\":\"%s\",\"pid\":%d}\n",
        (int)route, g_refusal_raw->which, (unsigned long long)rva,
        (long long)g_refusal_raw->rsi, (long long)g_refusal_raw->rdx,
        (long long)g_refusal_raw->rcx, text != NULL ? text : "", pid);
    if (n > 0 && (size_t)n < sizeof body) {
        (void)eu4_metal_events_write(name, body);
    }
}

static eu4_gfx_draw_route_result refuse(eu4_gfx_draw_route_result result, char *diagnostic,
                                        size_t capacity, const char *text) {
    if (diagnostic != NULL && capacity > 0) {
        snprintf(diagnostic, capacity, "%s", text);
    }
    record_refusal(result, text);
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
    g_refusal_raw = raw;
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
    uint32_t logical_width = 0;
    uint32_t logical_height = 0;
    eu4_gfx_draw_route_logical_extent(
        backend.currentTargetColor != nil, inputs.target_width,
        inputs.target_height, backend.logicalWidth, backend.logicalHeight,
        &logical_width, &logical_height);
    inputs.logical_width = logical_width;
    inputs.logical_height = logical_height;
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
    void *current_shader = load_ptr(master, 0x70);
    if (current_shader == NULL) {
        return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                      "draw has no admitted current shader");
    }
    const char *identity = eu4_gfx_shader_identity(current_shader);
    int legacy_preferred = identity != NULL &&
        (strstr(identity, "simple") != NULL || strstr(identity, "sprite") != NULL);
    int generic_ready = eu4_gfx_shader_is_generic(current_shader);
    char text[256];
    text[0] = 0;
    if (generic_ready && !legacy_preferred) {
        eu4_gfx_generic_call call = {0};
        call.instance_count = 1;
        call.primitive = load_u32(master, 0x08);
        if (raw->which == 1) {
            call.indexed = 0;
            int32_t count32 = (int32_t)raw->rsi;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x64);
            }
            call.count = count32;
            call.first = (int32_t)raw->rdx;
        } else if (raw->which == 2) {
            call.indexed = 1;
            int32_t count32 = (int32_t)raw->rsi;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x68);
            }
            call.count = count32;
            call.base_vertex = (int32_t)raw->rcx;
        } else {
            call.indexed = 1;
            int32_t count32 = (int32_t)raw->rsi;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, 0x68);
            }
            call.count = count32;
            call.instance_count = (int32_t)raw->rdx > 0 ? (int32_t)raw->rdx : 1;
        }
        char generic_reason[256] = "";
        if (!eu4_gfx_generic_draw_encode_into((const void *)handle, &call, &inputs,
                                              (__bridge void *)command,
                                              generic_reason,
                                              sizeof generic_reason, state)) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          generic_reason[0] != 0
                              ? generic_reason
                              : "generic encoder refused the draw");
        }
    } else {
        if (current_shader == NULL) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "draw has no admitted current shader");
        }
        if (!eu4_gfx_shader_is_linked(current_shader) && !generic_ready) {
            return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                          "current shader is not linked");
        }
        int encoded_ok = 0;
        if (raw->which == 1) {
            eu4_gfx_draw_encode_result encoded = eu4_gfx_draw_encode_into(
                (const void *)handle, (int32_t)raw->rsi, (int32_t)raw->rdx, &inputs,
                (__bridge void *)command, text, sizeof text, state);
            if (encoded == EU4_GFX_DRAW_ENCODE_OK) {
                encoded_ok = 1;
            }
        }
        if (!encoded_ok && generic_ready) {
            eu4_gfx_generic_call call = {0};
            call.instance_count = 1;
            call.primitive = load_u32(master, 0x08);
            call.indexed = raw->which != 1;
            int32_t count32 = (int32_t)raw->rsi;
            if (count32 <= 0) {
                count32 = (int32_t)load_u32(master, raw->which == 1 ? 0x64 : 0x68);
            }
            call.count = count32;
            call.first = (int32_t)raw->rdx;
            call.base_vertex = (int32_t)raw->rcx;
            if (raw->which == 3) {
                call.instance_count = (int32_t)raw->rdx > 0 ? (int32_t)raw->rdx : 1;
            }
            char generic_reason[256] = "";
            if (!eu4_gfx_generic_draw_encode_into((const void *)handle, &call,
                                                  &inputs, (__bridge void *)command,
                                                  generic_reason,
                                                  sizeof generic_reason, state)) {
                return refuse(EU4_GFX_DRAW_ROUTE_UNSUPPORTED, diagnostic, capacity,
                              generic_reason[0] != 0
                                  ? generic_reason
                                  : "generic encoder refused the draw");
            }
        } else if (!encoded_ok) {
            return refuse(raw->which != 1 ? EU4_GFX_DRAW_ROUTE_UNSUPPORTED
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
    /* The diagnostic post-draw readback runs only after the normal post-submit
       state transition above, so its drain cannot leave the backend without a
       completion record or presentation readiness. */
    (void)eu4_gfx_generic_after_dump();
    eu4_gfx_live_draw_sequence++;
    eu4_gfx_order_trace("draw", eu4_gfx_live_draw_sequence);
    backend.lastDrawTargetColor = (__bridge id)inputs.target;
    backend.lastDrawTargetWidth = inputs.target_width;
    backend.lastDrawTargetHeight = inputs.target_height;
    /* A draw whose viewport covers the whole target produces a full-surface
       image (the map/terrain passes). Track the most recent one as the source
       for the engine's 1:1 texture copy into the sampled background. */
    if (backend.currentTargetHeader != NULL &&
        viewport_width * 4 >= inputs.target_width * 3 &&
        viewport_height * 4 >= inputs.target_height * 3) {
        backend.lastFullTargetColor = (__bridge id)inputs.target;
        backend.lastFullTargetWidth = inputs.target_width;
        backend.lastFullTargetHeight = inputs.target_height;
    }

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
    {
        char name[96];
        snprintf(name, sizeof name, "draw-ok-%llx.json", (unsigned long long)caller_rva);
        char body[512];
        int n = snprintf(
            body, sizeof body,
            "{\"schema\":\"gfx-draw-ok/v1\",\"caller_rva\":\"0x%llx\","
            "\"variant\":%u,\"count\":%d,\"call_count\":%d,"
            "\"master64\":%u,\"master68\":%u,\"rsi\":%lld,"
            "\"target\":\"%p\",\"identity\":\"%s\",\"pid\":%d}\n",
            (unsigned long long)caller_rva, raw->which,
            state != NULL ? state->count : (int32_t)raw->rsi,
            state != NULL ? state->count : (int32_t)raw->rsi,
            (unsigned)load_u32(master, 0x64), (unsigned)load_u32(master, 0x68),
            (long long)(int64_t)raw->rsi, inputs.target,
            eu4_gfx_shader_identity(current_shader) ?: "",
            (int)getpid());
        if (n > 0 && (size_t)n < sizeof body) {
            (void)eu4_metal_events_write(name, body);
        }
    }
    (void)eu4_gfx_logical_capture_from_env(
        (__bridge void *)backend.device, (__bridge void *)backend.queue,
        (const void *)handle, state != NULL ? state->count : (int32_t)raw->rsi,
        (int32_t)raw->rdx, &inputs, state, caller_rva);
    eu4_gfx_checkpoint_after_draw((void *)master, raw, state);
    return EU4_GFX_DRAW_ROUTE_HANDLED;
}
