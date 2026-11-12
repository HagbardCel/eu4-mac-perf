#include "eu4_gfx_draw_capture.h"

#include "eu4_gfx_buffers.h"
#include "eu4_gfx_draw_route.h"
#include "eu4_gfx_init.h"
#include "eu4_metal_events.h"

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* _GfxDrawIndexed reads the context through (%rdi). The other two entries
   have no verified context layout, so their receipts stay raw. */
enum {
    EU4_GFX_DRAW = 1,
    EU4_GFX_DRAW_INDEXED = 2,
    EU4_GFX_DRAW_CONTEXT_WANT = 0x171,
    EU4_GFX_DRAW_CALLER = 0xedfcd1,
    EU4_GFX_DRAW_SKIP_MAX = 256,
    EU4_GFX_SHADER_OBJECT = 0x78
};

static int g_draw_count;
static int g_skip_count;
static int g_route_handled;
static int g_route_skipped;
static eu4_gfx_draw_route_fn g_route;

void eu4_gfx_draw_route_install(eu4_gfx_draw_route_fn route) {
    g_route = route;
}

eu4_gfx_draw_route_fn eu4_gfx_draw_route_current(void) {
    return g_route;
}

void eu4_gfx_draw_selection_reset(void) {
    g_draw_count = 0;
    g_skip_count = 0;
}

_Static_assert(offsetof(eu4_gfx_draw_raw, rdi) == 8, "rdi slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, xmm) == 56, "xmm slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, retaddr) == 184, "return slot");
_Static_assert(offsetof(eu4_gfx_draw_raw, stack) == 192, "stack slot");
_Static_assert(sizeof(eu4_gfx_draw_raw) == 256, "raw size");

static int hex_put(const void *src, int n, char *out, int cap) {
    static const char *digits = "0123456789abcdef";
    if (n < 0 || cap < n * 2 + 1) {
        return -1;
    }
    const uint8_t *bytes = src;
    for (int i = 0; i < n; i++) {
        out[i * 2] = digits[bytes[i] >> 4];
        out[i * 2 + 1] = digits[bytes[i] & 0xf];
    }
    out[n * 2] = 0;
    return 0;
}

static const char *symbol_for(uint32_t which) {
    if (which == 1) {
        return "_GfxDraw";
    }
    if (which == EU4_GFX_DRAW_INDEXED) {
        return "_GfxDrawIndexed";
    }
    if (which == 3) {
        return "_GfxDrawIndexedInstanced";
    }
    return NULL;
}

static int strip_ready(int inside, uint64_t rva, int complete, int shader_known, uint64_t shader,
                       int mode_known, uint32_t mode, int count_known, int32_t count, int32_t least) {
    return inside && rva == EU4_GFX_DRAW_CALLER && complete && shader_known && shader != 0 &&
           mode_known && mode == 5 && count_known && count >= least;
}

int eu4_gfx_draw_receipt(const eu4_gfx_draw_raw *raw, char *out, int cap) {
    if (raw == NULL || out == NULL || cap < 64) {
        return -1;
    }
    const char *symbol = symbol_for(raw->which);
    if (symbol == NULL) {
        return -1;
    }
    char image[512];
    uint64_t rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe((const void *)(uintptr_t)raw->retaddr, image, sizeof image, &rva, &inside,
                              &thread, &pid);
    /* _GfxDraw's last read is the byte at +0x170. Indexed uses the same bound. */
    int context_want = raw->which == 1 || raw->which == EU4_GFX_DRAW_INDEXED ? EU4_GFX_DRAW_CONTEXT_WANT : 0;
    if (context_want > EU4_GFX_DRAW_CONTEXT_WANT) {
        return -1;
    }
    int context_got = 0;
    uint8_t context[EU4_GFX_DRAW_CONTEXT_WANT];
    memset(context, 0, sizeof context);
    if (context_want > 0) {
        uint8_t slot[8];
        int slot_got = 0;
        if (eu4_metal_events_read((const void *)(uintptr_t)raw->rdi, slot, 8, &slot_got) == 0 &&
            slot_got == 8) {
            uint64_t context_ptr = 0;
            memcpy(&context_ptr, slot, sizeof context_ptr);
            if (context_ptr != 0) {
                (void)eu4_metal_events_read((const void *)(uintptr_t)context_ptr, context, context_want,
                                            &context_got);
            }
        }
    }
    int complete = context_got == context_want;
    char stack_hex[129];
    char xmm_hex[8][33];
    char context_hex[EU4_GFX_DRAW_CONTEXT_WANT * 2 + 1];
    context_hex[0] = 0;
    if (hex_put(raw->stack, 64, stack_hex, (int)sizeof stack_hex) != 0) {
        return -1;
    }
    for (int i = 0; i < 8; i++) {
        if (hex_put(raw->xmm[i], 16, xmm_hex[i], (int)sizeof xmm_hex[i]) != 0) {
            return -1;
        }
    }
    if (context_got > 0 && hex_put(context, context_got, context_hex, (int)sizeof context_hex) != 0) {
        return -1;
    }
    char shader_json[32] = "null";
    char count_json[16] = "null";
    char dirty_json[8] = "null";
    char uniform_json[8] = "null";
    char object_hex[EU4_GFX_SHADER_OBJECT * 2 + 1];
    char object_json[EU4_GFX_SHADER_OBJECT * 2 + 3];
    char extra[512];
    extra[0] = 0;
    int invocation = 1;
    if (raw->which == EU4_GFX_DRAW) {
        int count_known = 0;
        int32_t count = 0;
        int32_t esi = (int32_t)raw->rsi;
        if (esi > 0) {
            count = esi;
            count_known = 1;
            snprintf(count_json, sizeof count_json, "%d", esi);
        } else if (context_got >= 0x68) {
            memcpy(&count, context + 0x64, sizeof count);
            count_known = 1;
            snprintf(count_json, sizeof count_json, "%d", count);
        }
        int shader_known = 0;
        uint64_t shader = 0;
        if (context_got >= 0x78) {
            memcpy(&shader, context + 0x70, sizeof shader);
            shader_known = 1;
            snprintf(shader_json, sizeof shader_json, "\"0x%llx\"", (unsigned long long)shader);
        }
        int mode_known = 0;
        uint32_t mode = 0;
        if (context_got >= 12) {
            memcpy(&mode, context + 8, sizeof mode);
            mode_known = 1;
        }
        if (context_got >= 0x129) {
            snprintf(uniform_json, sizeof uniform_json, "%u", context[0x128]);
        }
        if (context_got >= 0x171) {
            snprintf(dirty_json, sizeof dirty_json, "%u", context[0x170]);
        }
        int empty = strip_ready(inside, rva, complete, shader_known, shader, mode_known, mode, count_known,
                                count, 0) &&
                    count <= 2;
        int drawable = strip_ready(inside, rva, complete, shader_known, shader, mode_known, mode, count_known,
                                   count, 3);
        const char *selection = "ineligible_stop";
        int skipped = g_skip_count;
        if (!eu4_gfx_buffers_on_thread()) {
            invocation = g_draw_count;
        } else {
            g_draw_count += 1;
            invocation = g_draw_count;
            if (empty && g_skip_count < EU4_GFX_DRAW_SKIP_MAX) {
                /* The original body would still upload dirty uniforms and attributes. */
                g_skip_count += 1;
                return 1;
            }
            if (empty) {
                selection = "budget_exhausted";
            } else if (drawable) {
                selection = "candidate";
            }
            skipped = g_skip_count;
        }
        int object_got = 0;
        memcpy(object_json, "null", 5);
        if (shader_known && shader != 0) {
            uint8_t object[EU4_GFX_SHADER_OBJECT];
            (void)eu4_metal_events_read((const void *)(uintptr_t)shader, object, EU4_GFX_SHADER_OBJECT,
                                        &object_got);
            if (object_got == EU4_GFX_SHADER_OBJECT &&
                hex_put(object, object_got, object_hex, (int)sizeof object_hex) == 0) {
                int quoted = snprintf(object_json, sizeof object_json, "\"%s\"", object_hex);
                if (quoted < 0 || quoted >= (int)sizeof object_json) {
                    return -1;
                }
            }
        }
        int extra_len = snprintf(extra, sizeof extra,
                                 ",\"shader_ptr\":%s,\"effective_count\":%s,\"attrib_dirty\":%s,"
                                 "\"uniform_dirty\":%s,\"selection\":\"%s\",\"skipped_count\":%d,"
                                 "\"shader_object_want\":%d,\"shader_object_got\":%d,\"shader_object\":%s",
                                 shader_json, count_json, dirty_json, uniform_json, selection, skipped,
                                 EU4_GFX_SHADER_OBJECT, object_got, object_json);
        if (extra_len < 0 || extra_len >= (int)sizeof extra) {
            return -1;
        }
    }
    char chain_buf[80];
    char caller_buf[80];
    const char *chain = "";
    const char *caller = "null";
    if (inside) {
        snprintf(chain_buf, sizeof chain_buf, "\"0x%llx\"", (unsigned long long)rva);
        snprintf(caller_buf, sizeof caller_buf, "\"0x%llx\"", (unsigned long long)rva);
        chain = chain_buf;
        caller = caller_buf;
    }
    int length = snprintf(
        out, (size_t)cap,
        "{\"symbol\":\"%s\",\"invocation_id\":%d,\"provenance\":\"%s\",\"call_chain\":[%s],"
        "\"image\":\"%s\",\"caller_rva\":%s,\"pid\":%d,\"thread\":%llu,"
        "\"rdi\":\"0x%llx\",\"rsi\":\"0x%llx\",\"rdx\":\"0x%llx\",\"rcx\":\"0x%llx\","
        "\"r8\":\"0x%llx\",\"r9\":\"0x%llx\",\"stack\":\"%s\","
        "\"xmm\":[\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\",\"%s\"],"
        "\"context_want\":%d,\"context_got\":%d,\"context_complete\":%s,\"context\":\"%s\"%s}\n",
        symbol, invocation, inside ? "game" : "rejected", chain, image, caller, pid,
        (unsigned long long)thread,
        (unsigned long long)raw->rdi, (unsigned long long)raw->rsi, (unsigned long long)raw->rdx,
        (unsigned long long)raw->rcx, (unsigned long long)raw->r8, (unsigned long long)raw->r9,
        stack_hex, xmm_hex[0], xmm_hex[1], xmm_hex[2], xmm_hex[3], xmm_hex[4], xmm_hex[5],
        xmm_hex[6], xmm_hex[7], context_want, context_got, complete ? "true" : "false",
        context_hex, extra);
    if (length < 0 || length >= cap) {
        return -1;
    }
    return 0;
}

void eu4_gfx_draw_route_record(const char *name, const char *route, const char *reason,
                               const eu4_gfx_simple_draw_state *state,
                               const eu4_gfx_draw_raw *raw, uint64_t caller_rva) {
    char body[2048];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-draw-route/v0\",\"route\":\"%s\",\"reason\":\"%s\","
        "\"variant\":\"%s\",\"caller_rva\":\"0x%llx\",\"count\":%d,\"first\":%d,"
        "\"shader_identity\":\"%s\",\"primitive\":%u,\"vertex_stride\":%u,"
        "\"vertex_offset\":%u,\"vertex_bytes\":%llu,\"constant_hash\":\"0x%llx\","
        "\"constant_hex\":\"%s\",\"vertex_hash\":\"0x%llx\",\"vertex_hex\":\"%s\","
        "\"texture\":\"0x%llx\",\"texture_size\":[%u,%u],"
        "\"target\":\"0x%llx\",\"target_size\":[%u,%u],"
        "\"logical_size\":[%u,%u],\"viewport\":[%u,%u,%u,%u],"
        "\"blend\":%d,\"depth_default\":%d,\"raster_default\":%d,\"scissor_full\":%d}\n",
        route != NULL ? route : "", reason != NULL ? reason : "",
        symbol_for(raw != NULL ? raw->which : 0) != NULL
            ? symbol_for(raw != NULL ? raw->which : 0)
            : "unknown",
        (unsigned long long)caller_rva,
        state != NULL ? state->count : 0, state != NULL ? state->first : 0,
        state != NULL ? state->shader_identity : "",
        state != NULL ? state->primitive : 0,
        state != NULL ? state->vertex_stride : 0,
        state != NULL ? state->vertex_offset : 0,
        state != NULL ? (unsigned long long)state->vertex_bytes : 0,
        state != NULL ? (unsigned long long)state->constant_hash : 0,
        state != NULL ? state->constant_hex : "",
        state != NULL ? (unsigned long long)state->vertex_hash : 0,
        state != NULL ? state->vertex_hex : "",
        state != NULL ? (unsigned long long)state->texture_pointer : 0,
        state != NULL ? state->texture_width : 0,
        state != NULL ? state->texture_height : 0,
        state != NULL ? (unsigned long long)state->target_pointer : 0,
        state != NULL ? state->target_width : 0,
        state != NULL ? state->target_height : 0,
        state != NULL ? state->logical_width : 0,
        state != NULL ? state->logical_height : 0,
        state != NULL ? state->viewport_x : 0,
        state != NULL ? state->viewport_y : 0,
        state != NULL ? state->viewport_width : 0,
        state != NULL ? state->viewport_height : 0,
        state != NULL ? state->blend_supported : 0,
        state != NULL ? state->depth_default : 0,
        state != NULL ? state->raster_default : 0,
        state != NULL ? state->scissor_full : 0);
    if (length < 0 || (size_t)length >= sizeof body) {
        return;
    }
    (void)eu4_metal_events_write(name, body);
}

/* Bounded diagnostic sequence of every _GfxDraw-family invocation, written
   before the semantic route decides. This is how a later blocked operation is
   located without guessing from caller RVAs. Disabled unless explicitly armed;
   never part of the render path. */
static int g_sequence_written;

static void record_draw_sequence(const eu4_gfx_draw_raw *raw) {
    if (raw == NULL || g_sequence_written >= 256) {
        return;
    }
    const char *enabled = getenv("EU4_GFX_DRAW_SEQUENCE");
    if (enabled == NULL || strcmp(enabled, "1") != 0) {
        return;
    }
    const char *dir = getenv("EU4_GFX_CHECKPOINT_DIR");
    if (dir == NULL || dir[0] != '/') {
        dir = getenv("EU4_GFX_STARTUP_DIR");
    }
    if (dir == NULL || dir[0] != '/') {
        return;
    }
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe((const void *)(uintptr_t)raw->retaddr, NULL, 0,
                              &caller_rva, &inside, &thread, &pid);
    const uint8_t *handle = (const uint8_t *)(uintptr_t)raw->rdi;
    const uint8_t *master = NULL;
    if (handle != NULL) {
        memcpy(&master, handle, sizeof master);
    }
    uint32_t mode = 0;
    int32_t count = (int32_t)raw->rsi;
    uint64_t shader = 0;
    if (master != NULL) {
        memcpy(&mode, master + 8, sizeof mode);
        if (count <= 0) {
            memcpy(&count, master + 0x64, sizeof count);
        }
        memcpy(&shader, master + 0x70, sizeof shader);
    }
    char path[1200];
    if (snprintf(path, sizeof path, "%s/draw-sequence.jsonl", dir) >=
        (int)sizeof path) {
        return;
    }
    FILE *file = fopen(path, "a");
    if (file == NULL) {
        return;
    }
    fprintf(file,
            "{\"symbol\":\"%s\",\"caller_rva\":\"0x%llx\","
            "\"inside\":%s,\"mode\":%u,\"count\":%d,\"shader\":\"0x%llx\"}\n",
            symbol_for(raw->which), (unsigned long long)caller_rva,
            inside ? "true" : "false", mode, count,
            (unsigned long long)shader);
    fflush(file);
    fclose(file);
    g_sequence_written += 1;
}

int eu4_gfx_draw_capture(const eu4_gfx_draw_raw *raw) {
    record_draw_sequence(raw);
    char body[8192];
    const char *reason = "error";
    const char *symbol = "_GfxDraw";
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    if (raw != NULL) {
        const char *named = symbol_for(raw->which);
        if (named != NULL) {
            symbol = named;
        }
        int disposition = eu4_gfx_draw_receipt(raw, body, (int)sizeof body);
        if (disposition == 1) {
            eu4_gfx_draw_route_record("draw-noop.json", "noop", "proven empty draw", NULL, raw, 0);
            return 1;
        }
        if (disposition == 0 && eu4_metal_events_write("draw_invocation.json", body) == 0 &&
            strstr(body, "\"provenance\":\"game\"") != NULL) {
            reason = "draw";
        }
        eu4_metal_events_describe((const void *)(uintptr_t)raw->retaddr, NULL, 0,
                                  &caller_rva, &inside, &thread, &pid);
    }
    if (raw != NULL && g_route != NULL) {
        eu4_gfx_simple_draw_state state;
        char diagnostic[256];
        eu4_gfx_draw_route_result route =
            g_route(raw, &state, diagnostic, sizeof diagnostic);
        if (route == EU4_GFX_DRAW_ROUTE_HANDLED) {
            g_route_handled = 1;
            eu4_gfx_draw_route_record("draw-route.json", "live", "", &state, raw, caller_rva);
            return 1;
        }
        if (route == EU4_GFX_DRAW_ROUTE_UNSUPPORTED && g_route_handled &&
            getenv("EU4_GFX_DRAW_SKIP_UNSUPPORTED") != NULL &&
            strcmp(getenv("EU4_GFX_DRAW_SKIP_UNSUPPORTED"), "1") == 0) {
            /* Bounded presentation diagnostic: later draws are outside the
               admitted route. Skipping them is recorded explicitly; the target
               draw has already been submitted and its contents are prepared
               for the engine's own present. */
            g_route_skipped += 1;
            char skip[512];
            int skip_length = snprintf(
                skip, sizeof skip,
                "{\"schema\":\"gfx-draw-skip/v1\",\"symbol\":\"%s\","
                "\"caller_rva\":%llu,\"skipped\":%d,\"reason\":\"%s\"}\n",
                symbol, (unsigned long long)caller_rva, g_route_skipped,
                diagnostic);
            if (skip_length > 0 && (size_t)skip_length < sizeof skip) {
                (void)eu4_metal_events_write("draw-skipped.json", skip);
            }
            return 1;
        }
        eu4_gfx_draw_route_record(route == EU4_GFX_DRAW_ROUTE_UNSUPPORTED
                               ? "draw-unsupported.json"
                               : "draw-failed.json",
                           route == EU4_GFX_DRAW_ROUTE_UNSUPPORTED ? "unsupported" : "failed",
                           diagnostic, &state, raw, caller_rva);
        eu4_gfx_stop(symbol,
                     route == EU4_GFX_DRAW_ROUTE_UNSUPPORTED ? "unsupported" : "failed",
                     (const void *)(uintptr_t)raw->retaddr);
    }
    const void *caller = raw == NULL ? NULL : (const void *)(uintptr_t)raw->retaddr;
    eu4_gfx_stop(symbol, reason, caller);
}
