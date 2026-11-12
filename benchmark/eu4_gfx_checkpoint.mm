#import "eu4_gfx_checkpoint.h"

#import "eu4_gfx_draw_route.h"
#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <CommonCrypto/CommonDigest.h>
#import <dispatch/dispatch.h>
#import <pthread.h>
#import <Metal/Metal.h>
#import <stdio.h>
#import <stdlib.h>
#import <string.h>
#import <sys/stat.h>
#import <unistd.h>

enum {
    EU4_GFX_CHECKPOINT_NONE = 0,
    EU4_GFX_CHECKPOINT_AFTER_CLEAR = 1,
    EU4_GFX_CHECKPOINT_AFTER_DRAW = 2,
    EU4_GFX_CHECKPOINT_BEFORE_PRESENT = 3,
    EU4_GFX_CHECKPOINT_AFTER_PRESENT = 4,
};

static int g_checkpoint;
static uint64_t g_caller_rva;
static int g_occurrence = 1;
static int g_stop = 1;
static char g_dir[1024];
static int g_counts[5];
static int g_hold_ms;
static uint8_t *g_present_pixels;
static size_t g_present_capacity;
static int g_present_selected;
static uint32_t g_present_width;
static uint32_t g_present_height;
static uint64_t g_present_window_number;
static int g_present_window_visible;
static int g_present_window_occluded;
static double g_present_window_width;
static double g_present_window_height;
static double g_present_window_scale;
static char g_present_window_diagnostics[512];

static void *load_ptr(const uint8_t *base, size_t offset) {
    void *value = NULL;
    memcpy(&value, base + offset, sizeof value);
    return value;
}

static int parse_uint(const char *text, uint64_t *out) {
    if (text == NULL || text[0] == 0 || text[0] == '-') {
        return 0;
    }
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 0);
    if (end == text || *end != 0) {
        return 0;
    }
    *out = (uint64_t)value;
    return 1;
}

void eu4_gfx_checkpoint_configure(const char *operation, uint64_t caller_rva,
                                  int occurrence, const char *output_dir,
                                  int stop_after_capture) {
    g_checkpoint = EU4_GFX_CHECKPOINT_NONE;
    g_caller_rva = caller_rva;
    if (occurrence < 1) {
        g_occurrence = 1;
        g_dir[0] = 0;
        return;
    }
    g_occurrence = occurrence;
    g_stop = stop_after_capture ? 1 : 0;
    memset(g_counts, 0, sizeof g_counts);
    g_present_selected = 0;
    g_dir[0] = 0;
    if (operation == NULL || output_dir == NULL || output_dir[0] != '/') {
        return;
    }
    if (strcmp(operation, "after-clear") == 0) {
        g_checkpoint = EU4_GFX_CHECKPOINT_AFTER_CLEAR;
    } else if (strcmp(operation, "after-draw") == 0) {
        g_checkpoint = EU4_GFX_CHECKPOINT_AFTER_DRAW;
    } else if (strcmp(operation, "before-present") == 0) {
        g_checkpoint = EU4_GFX_CHECKPOINT_BEFORE_PRESENT;
    } else if (strcmp(operation, "after-present") == 0) {
        g_checkpoint = EU4_GFX_CHECKPOINT_AFTER_PRESENT;
    } else {
        return;
    }
    snprintf(g_dir, sizeof g_dir, "%s", output_dir);
    if (g_checkpoint != EU4_GFX_CHECKPOINT_NONE) {
        eu4_gfx_set_checkpoint_hooks(eu4_gfx_checkpoint_after_clear,
                                     eu4_gfx_checkpoint_before_present,
                                     eu4_gfx_checkpoint_after_present);
    }
}

int eu4_gfx_checkpoint_configure_from_env(void) {
    g_checkpoint = EU4_GFX_CHECKPOINT_NONE;
    const char *operation = getenv("EU4_GFX_CHECKPOINT");
    const char *dir = getenv("EU4_GFX_CHECKPOINT_DIR");
    const char *caller = getenv("EU4_GFX_CHECKPOINT_CALLER_RVA");
    const char *occurrence = getenv("EU4_GFX_CHECKPOINT_OCCURRENCE");
    const char *stop = getenv("EU4_GFX_CHECKPOINT_STOP");
    if (operation == NULL || dir == NULL || dir[0] != '/') {
        return 0;
    }
    uint64_t caller_rva = 0;
    uint64_t occurrence_value = 1;
    if (caller != NULL && caller[0] != 0 &&
        (!parse_uint(caller, &caller_rva) || caller_rva > 0xFFFFFFFFULL)) {
        return 0;
    }
    if (occurrence != NULL && occurrence[0] != 0) {
        if (!parse_uint(occurrence, &occurrence_value) || occurrence_value < 1 ||
            occurrence_value > 0x7FFFFFFFULL) {
            return 0;
        }
    }
    int stop_after = 1;
    if (stop != NULL && stop[0] != 0 && strcmp(stop, "0") == 0) {
        stop_after = 0;
    }
    g_hold_ms = 0;
    const char *hold = getenv("EU4_GFX_CHECKPOINT_HOLD_MS");
    if (hold != NULL && hold[0] != 0) {
        uint64_t value = 0;
        if (!parse_uint(hold, &value) || value > 60000) {
            return 0;
        }
        g_hold_ms = (int)value;
    }
    eu4_gfx_checkpoint_configure(operation, caller_rva, (int)occurrence_value, dir,
                                 stop_after);
    if (g_checkpoint != EU4_GFX_CHECKPOINT_NONE) {
        eu4_gfx_set_checkpoint_hooks(eu4_gfx_checkpoint_after_clear,
                                     eu4_gfx_checkpoint_before_present,
                                     eu4_gfx_checkpoint_after_present);
    }
    return g_checkpoint != EU4_GFX_CHECKPOINT_NONE;
}

int eu4_gfx_checkpoint_enabled(void) {
    return g_checkpoint != EU4_GFX_CHECKPOINT_NONE;
}

int eu4_gfx_checkpoint_test_enabled(void) {
    return g_checkpoint != EU4_GFX_CHECKPOINT_NONE;
}

int eu4_gfx_checkpoint_test_occurrence(void) {
    return g_occurrence;
}

static int qualifies(int checkpoint, uint64_t caller_rva) {
    if (g_checkpoint != checkpoint) {
        return 0;
    }
    if (g_caller_rva != 0 && caller_rva != g_caller_rva) {
        return 0;
    }
    g_counts[checkpoint] += 1;
    return g_counts[checkpoint] == g_occurrence;
}

static void json_text(char *out, size_t capacity, const char *text) {
    size_t n = 0;
    if (capacity == 0) {
        return;
    }
    if (text == NULL) {
        text = "";
    }
    for (size_t i = 0; text[i] != 0 && n + 2 < capacity; i++) {
        unsigned char c = (unsigned char)text[i];
        if (c == '"' || c == '\\') {
            out[n++] = '\\';
            out[n++] = (char)c;
        } else if (c < 0x20 || c > 0x7e) {
            out[n++] = '?';
        } else {
            out[n++] = (char)c;
        }
    }
    out[n] = 0;
}

static int write_bytes(const char *path, const void *bytes, size_t length) {
    FILE *out = fopen(path, "wb");
    if (out == NULL) {
        return -1;
    }
    int failed = fwrite(bytes, 1, length, out) != length;
    if (!failed) {
        failed = fflush(out) != 0;
    }
    int fd = fileno(out);
    if (!failed && fd >= 0) {
        failed = fsync(fd) != 0;
    }
    if (fclose(out) != 0) {
        failed = 1;
    }
    if (failed) {
        (void)unlink(path);
        return -1;
    }
    return 0;
}

static void sha256_hex(const void *bytes, size_t length, char out[65]) {
    uint8_t digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(bytes, (CC_LONG)length, digest);
    static const char digits[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = digits[digest[i] >> 4];
        out[i * 2 + 1] = digits[digest[i] & 0xf];
    }
    out[64] = 0;
}

static const char *env_text(const char *name) {
    const char *value = getenv(name);
    return value != NULL ? value : "";
}

static void write_failure(const char *operation, const char *reason, uint64_t caller_rva,
                          int occurrence) {
    char path[1280];
    char escaped[256];
    json_text(escaped, sizeof escaped, reason);
    snprintf(path, sizeof path, "%s/checkpoint-%s-%d.json", g_dir, operation, occurrence);
    char body[1024];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-checkpoint/v1\",\"checkpoint\":\"%s\",\"occurrence\":%d,"
        "\"caller_rva\":\"0x%llx\",\"status\":\"failed\",\"reason\":\"%s\","
        "\"pixels_captured\":0,\"pid\":%d}\n",
        operation, occurrence, (unsigned long long)caller_rva, escaped, (int)getpid());
    if (length > 0 && (size_t)length < sizeof body) {
        (void)write_bytes(path, body, (size_t)length);
    }
}

static void capture(const char *operation, const char *symbol, const char *stop_reason,
                    void *master, const void *caller, uint64_t caller_rva,
                    const eu4_gfx_simple_draw_state *state) {
    __unsafe_unretained EU4GfxBackend *backend =
        master != NULL ? (__bridge EU4GfxBackend *)load_ptr((const uint8_t *)master, 0x180)
                       : nil;
    if (backend == nil || backend.gameColorTarget == nil) {
        write_failure(operation, "no game-owned render target", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    uint32_t width = backend.gameWidth;
    uint32_t height = backend.gameHeight;
    if (width == 0 || height == 0 ||
        (uint64_t)width * (uint64_t)height > SIZE_MAX / 4u) {
        write_failure(operation, "invalid render target dimensions", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    size_t need = (size_t)width * (size_t)height * 4u;
    uint8_t *pixels = (uint8_t *)malloc(need);
    if (pixels == NULL) {
        write_failure(operation, "readback allocation failed", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    size_t got = 0;
    uint32_t actual_width = 0, actual_height = 0, stride = 0;
    uint64_t generation = 0;
    int gpu_status = (int)MTLCommandBufferStatusNotEnqueued;
    eu4_gfx_clear_status read = eu4_gfx_read_game_target(
        master, pixels, need, &got, &actual_width, &actual_height, &stride, &generation,
        &gpu_status);
    if (read != EU4_GFX_CLEAR_OK || got != need) {
        free(pixels);
        write_failure(operation, "game-owned target readback failed", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    char operation_name[32];
    snprintf(operation_name, sizeof operation_name, "%s", operation);
    char raw_name[64];
    snprintf(raw_name, sizeof raw_name, "checkpoint-%s-%d.bgra", operation_name,
             g_counts[g_checkpoint]);
    char raw_path[1280];
    snprintf(raw_path, sizeof raw_path, "%s/%s", g_dir, raw_name);
    if (write_bytes(raw_path, pixels, need) != 0) {
        free(pixels);
        write_failure(operation, "raw checkpoint write failed", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    char digest[65];
    sha256_hex(pixels, need, digest);
    free(pixels);

    char image[512];
    uint64_t described_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, image, sizeof image, &described_rva, &inside,
                              &thread, &pid);
    if (!inside) {
        described_rva = 0;
    }
    char shader_identity[256];
    json_text(shader_identity, sizeof shader_identity,
              state != NULL ? state->shader_identity : "");
    int draw_reached = state != NULL ? 1 : 0;
    int draw_encoded = state != NULL ? 1 : 0;
    int gpu_completed = gpu_status == (int)MTLCommandBufferStatusCompleted ? 1 : 0;
    int presented = 0;

    char body[4096];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-checkpoint/v1\",\"checkpoint\":\"%s\",\"occurrence\":%d,"
        "\"caller\":{\"rva\":\"0x%llx\",\"observed_rva\":\"0x%llx\",\"inside\":%s,\"thread\":%llu,\"pid\":%d},"
        "\"session\":\"%s\",\"executable\":\"%s\",\"executable_sha256\":\"%s\","
        "\"library_sha256\":\"%s\","
        "\"target\":{\"width\":%u,\"height\":%u,\"row_stride\":%u,"
        "\"pixel_format\":\"BGRA8Unorm\",\"generation\":%llu},"
        "\"gpu\":{\"command_status\":%d,\"completed\":%s},"
        "\"capture\":{\"raw_file\":\"%s\",\"raw_bytes\":%zu,\"raw_sha256\":\"%s\"},"
        "\"status\":{\"draw_reached\":%d,\"draw_encoded\":%d,\"gpu_completed\":%d,"
        "\"pixels_captured\":1,\"presented\":%d},"
        "\"draw\":{\"shader_identity\":\"%s\",\"primitive\":%u,\"count\":%d,\"first\":%d,"
        "\"vertex_stride\":%u,\"vertex_offset\":%u,\"vertex_bytes\":%llu,"
        "\"vertex_hash\":\"0x%llx\",\"vertex_hex\":\"%s\","
        "\"constant_hash\":\"0x%llx\",\"constant_hex\":\"%s\",\"texture\":\"0x%llx\","
        "\"texture_size\":[%u,%u],\"target\":\"0x%llx\","
        "\"logical_size\":[%u,%u],\"viewport\":[%u,%u,%u,%u],"
        "\"blend_supported\":%d,\"depth_default\":%d,\"raster_default\":%d,"
        "\"scissor_full\":%d},"
        "\"observed_state\":{\"blend\":\"engine master snapshot\",\"depth\":\"engine snapshot (depth_default reports equality with the backend default); no depth attachment\","
        "\"raster\":\"backend default snapshot; cull disabled in the reconstructed pass\","
        "\"sampler\":\"simple.shader asset contract\",\"texture\":\"engine texture registry\"}}\n",
        operation_name, g_counts[g_checkpoint], (unsigned long long)caller_rva,
        (unsigned long long)described_rva, inside ? "true" : "false",
        (unsigned long long)thread, pid, env_text("EU4_GFX_STARTUP_ID"),
        env_text("EU4_GFX_EXPECTED_EXE"), env_text("EU4_GFX_EXECUTABLE_SHA256"),
        env_text("EU4_GFX_LIBRARY_SHA256"), actual_width, actual_height, stride,
        (unsigned long long)generation, gpu_status, gpu_completed ? "true" : "false",
        raw_name, need, digest, draw_reached, draw_encoded, gpu_completed, presented,
        shader_identity, state != NULL ? state->primitive : 0,
        state != NULL ? state->count : 0, state != NULL ? state->first : 0,
        state != NULL ? state->vertex_stride : 0,
        state != NULL ? state->vertex_offset : 0,
        state != NULL ? (unsigned long long)state->vertex_bytes : 0,
        state != NULL ? (unsigned long long)state->vertex_hash : 0,
        state != NULL ? state->vertex_hex : "",
        state != NULL ? (unsigned long long)state->constant_hash : 0,
        state != NULL ? state->constant_hex : "",
        state != NULL ? (unsigned long long)state->texture_pointer : 0,
        state != NULL ? state->texture_width : 0,
        state != NULL ? state->texture_height : 0,
        state != NULL ? (unsigned long long)state->target_pointer : 0,
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
    char json_name[64];
    snprintf(json_name, sizeof json_name, "checkpoint-%s-%d.json", operation_name,
             g_counts[g_checkpoint]);
    char json_path[1280];
    snprintf(json_path, sizeof json_path, "%s/%s", g_dir, json_name);
    int wrote = length > 0 && (size_t)length < sizeof body
                    ? write_bytes(json_path, body, (size_t)length)
                    : -1;
    if (wrote != 0) {
        write_failure(operation, "checkpoint JSON write failed", caller_rva,
                      g_counts[g_checkpoint]);
        if (g_stop) {
            eu4_gfx_stop(symbol, "failed", caller);
        }
        return;
    }
    if (g_stop) {
        eu4_gfx_stop(symbol, stop_reason, caller);
    }
}

void eu4_gfx_checkpoint_after_draw(void *master, const eu4_gfx_draw_raw *raw,
                                   const eu4_gfx_simple_draw_state *state) {
    if (g_checkpoint != EU4_GFX_CHECKPOINT_AFTER_DRAW || raw == NULL) {
        return;
    }
    const void *caller = (const void *)(uintptr_t)raw->retaddr;
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, NULL, 0, &caller_rva, &inside, &thread, &pid);
    if (!qualifies(EU4_GFX_CHECKPOINT_AFTER_DRAW, caller_rva)) {
        return;
    }
    capture("after-draw", "_GfxDraw", "draw", master, caller, caller_rva, state);
}

void eu4_gfx_checkpoint_after_clear(void *master, const void *caller) {
    if (g_checkpoint != EU4_GFX_CHECKPOINT_AFTER_CLEAR) {
        return;
    }
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, NULL, 0, &caller_rva, &inside, &thread, &pid);
    if (!qualifies(EU4_GFX_CHECKPOINT_AFTER_CLEAR, caller_rva)) {
        return;
    }
    capture("after-clear", "_GfxClear", "clear", master, caller, caller_rva, NULL);
}

void eu4_gfx_checkpoint_before_present(void *master, const void *caller) {
    if (g_checkpoint == EU4_GFX_CHECKPOINT_AFTER_PRESENT) {
        g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT] += 1;
        if (g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT] != g_occurrence) {
            return;
        }
        __unsafe_unretained EU4GfxBackend *backend =
            master != NULL
                ? (__bridge EU4GfxBackend *)load_ptr((const uint8_t *)master, 0x180)
                : nil;
        if (backend == nil || backend.gameWidth == 0 || backend.gameHeight == 0 ||
            backend.gameWidth != backend.physicalWidth ||
            backend.gameHeight != backend.physicalHeight) {
            write_failure("after-present", "no committed physical drawable",
                          0, g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
            return;
        }
        uint64_t need = (uint64_t)backend.gameWidth * backend.gameHeight * 4u;
        if (need == 0 || need > (uint64_t)128 * 1024 * 1024) {
            write_failure("after-present", "drawable extent is unsupported",
                          0, g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
            return;
        }
        if (g_present_pixels == NULL || g_present_capacity < (size_t)need) {
            free(g_present_pixels);
            g_present_pixels = (uint8_t *)malloc((size_t)need);
            g_present_capacity = (size_t)need;
        }
        if (g_present_pixels == NULL) {
            write_failure("after-present", "drawable readback allocation failed",
                          0, g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
            return;
        }
        g_present_width = backend.gameWidth;
        g_present_height = backend.gameHeight;
        eu4_metal_ensure_visible();
        eu4_metal_window_diagnostics(g_present_window_diagnostics,
                                     sizeof g_present_window_diagnostics);
        (void)eu4_metal_window_state(&g_present_window_number,
                                     &g_present_window_visible,
                                     &g_present_window_occluded,
                                     &g_present_window_width,
                                     &g_present_window_height,
                                     &g_present_window_scale);
        eu4_gfx_present_capture_set(g_present_pixels, g_present_capacity);
        g_present_selected = 1;
        return;
    }
    if (g_checkpoint != EU4_GFX_CHECKPOINT_BEFORE_PRESENT) {
        return;
    }
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, NULL, 0, &caller_rva, &inside, &thread, &pid);
    if (!qualifies(EU4_GFX_CHECKPOINT_BEFORE_PRESENT, caller_rva)) {
        return;
    }
    capture("before-present", "_GfxPresent", "present", master, caller, caller_rva, NULL);
}

static void *g_repeat_master;

static void repeat_present(void) {
    if (g_repeat_master == NULL) {
        return;
    }
    eu4_metal_ensure_visible();
    int presented = eu4_gfx_present_diagnostic(g_repeat_master);
    char diagnostics[512];
    eu4_metal_window_diagnostics(diagnostics, sizeof diagnostics);
    char body[1024];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-repeat-present/v1\",\"presented\":%s,"
        "\"diagnostics\":\"%s\"}\n",
        presented ? "true" : "false", diagnostics);
    char path[1200];
    if (length > 0 && (size_t)length < sizeof body &&
        snprintf(path, sizeof path, "%s/repeat-present.json", g_dir) <
            (int)sizeof path) {
        (void)write_bytes(path, body, (size_t)length);
    }
}

static void *hold_thread(void *arg) {
    long milliseconds = (long)(intptr_t)arg;
    if (milliseconds > 0) {
        usleep((useconds_t)milliseconds * 1000u);
    }
    return NULL;
}

void eu4_gfx_checkpoint_after_present(void *master, const void *caller) {
    if (g_checkpoint != EU4_GFX_CHECKPOINT_AFTER_PRESENT || !g_present_selected) {
        return;
    }
    (void)master;
    g_present_selected = 0;
    size_t need = (size_t)g_present_width * g_present_height * 4u;
    if (g_present_pixels == NULL || need == 0) {
        write_failure("after-present", "drawable payload is absent", 0,
                      g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
        return;
    }
    char raw_name[64];
    snprintf(raw_name, sizeof raw_name, "checkpoint-after-present-%d.bgra",
             g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
    char raw_path[1300];
    if (snprintf(raw_path, sizeof raw_path, "%s/%s", g_dir, raw_name) >=
            (int)sizeof raw_path ||
        write_bytes(raw_path, g_present_pixels, need) != 0) {
        write_failure("after-present", "drawable payload write failed", 0,
                      g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
        return;
    }
    char digest[65];
    sha256_hex(g_present_pixels, need, digest);
    uint64_t caller_rva = 0;
    int inside = 0;
    uint64_t thread = 0;
    int pid = 0;
    eu4_metal_events_describe(caller, NULL, 0, &caller_rva, &inside, &thread, &pid);
    char image[512];
    eu4_metal_events_describe(caller, image, sizeof image, &caller_rva, &inside,
                              &thread, &pid);
    eu4_gfx_simple_draw_state draw;
    memset(&draw, 0, sizeof draw);
    uint64_t draw_caller = 0;
    int have_draw = eu4_gfx_draw_route_last(&draw, &draw_caller);
    char shader_identity[256];
    json_text(shader_identity, sizeof shader_identity,
              have_draw ? draw.shader_identity : "");
    char body[4096];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-checkpoint/v1\",\"checkpoint\":\"after-present\","
        "\"occurrence\":%d,\"caller\":{\"rva\":\"0x%llx\",\"inside\":%s,"
        "\"thread\":%llu,\"pid\":%d,\"image\":\"%s\"},"
        "\"session\":\"%s\",\"executable\":\"%s\","
        "\"executable_sha256\":\"%s\",\"library_sha256\":\"%s\","
        "\"target\":{\"width\":%u,\"height\":%u,\"row_stride\":%u,"
        "\"pixel_format\":\"BGRA8\",\"origin\":\"top_left\","
        "\"storage\":\"CAMetalLayer drawable BGRA8Unorm\"},"
        "\"gpu\":{\"command_status\":%d,\"completed\":true,"
        "\"evidence\":\"presentDrawable + waitUntilCompleted + drawable readback\"},"
        "\"capture\":{\"raw_file\":\"%s\",\"raw_bytes\":%zu,"
        "\"raw_sha256\":\"%s\",\"pixel_format\":\"BGRA8\"},"
        "\"presentation\":{\"shown\":true,\"method\":\"drawable_readback\","
        "\"drawable_scheduled\":true,\"drawable_readback\":true,"
        "\"command\":\"presentDrawable\",\"width\":%u,\"height\":%u,"
        "\"window_number\":%llu,\"window_visible\":%s,\"window_occluded\":%s,"
        "\"window_points\":[%.1f,%.1f],\"backing_scale\":%.2f,"
        "\"window_diagnostics\":\"%s\"},"
        "\"status\":{\"draw_reached\":%d,\"draw_encoded\":%d,"
        "\"gpu_completed\":true,\"pixels_captured\":1,\"presented\":1},"
        "\"draw\":{\"symbol\":\"_GfxDraw\",\"caller_rva\":\"0x%llx\","
        "\"shader_identity\":\"%s\",\"primitive\":%u,\"count\":%u,"
        "\"first\":%d,\"logical_size\":[%u,%u],\"viewport\":[%u,%u,%u,%u]}}\n",
        g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT],
        (unsigned long long)caller_rva, inside ? "true" : "false",
        (unsigned long long)thread, pid, image,
        env_text("EU4_GFX_STARTUP_ID"), env_text("EU4_GFX_EXPECTED_EXE"),
        env_text("EU4_GFX_EXECUTABLE_SHA256"), env_text("EU4_GFX_LIBRARY_SHA256"),
        g_present_width, g_present_height, g_present_width * 4u,
        (int)MTLCommandBufferStatusCompleted, raw_name, need, digest,
        g_present_width, g_present_height,
        (unsigned long long)g_present_window_number,
        g_present_window_visible ? "true" : "false",
        g_present_window_occluded ? "true" : "false",
        g_present_window_width, g_present_window_height,
        g_present_window_scale, g_present_window_diagnostics,
        have_draw ? 1 : 0, have_draw ? 1 : 0,
        (unsigned long long)draw_caller, shader_identity, draw.primitive,
        draw.count, draw.first, draw.logical_width, draw.logical_height,
        draw.viewport_x, draw.viewport_y, draw.viewport_width,
        draw.viewport_height);
    char json_path[1300];
    if (length <= 0 || (size_t)length >= sizeof body ||
        snprintf(json_path, sizeof json_path,
                 "%s/checkpoint-after-present-%d.json", g_dir,
                 g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]) >=
            (int)sizeof json_path ||
        write_bytes(json_path, body, (size_t)length) != 0) {
        write_failure("after-present", "drawable descriptor write failed", 0,
                      g_counts[EU4_GFX_CHECKPOINT_AFTER_PRESENT]);
        return;
    }
    char marker[768];
    int marker_length = snprintf(
        marker, sizeof marker,
        "{\"schema\":\"gfx-presentation/v1\",\"shown\":true,"
        "\"checkpoint\":\"%s\",\"raw_file\":\"%s\","
        "\"raw_sha256\":\"%s\",\"width\":%u,\"height\":%u,"
        "\"window_number\":%llu,\"window_visible\":%s,\"window_occluded\":%s,"
        "\"window_diagnostics\":\"%s\","
        "\"draw_caller_rva\":\"0x%llx\",\"present_caller_rva\":\"0x%llx\"}\n",
        "after-present", raw_name, digest, g_present_width, g_present_height,
        (unsigned long long)g_present_window_number,
        g_present_window_visible ? "true" : "false",
        g_present_window_occluded ? "true" : "false",
        g_present_window_diagnostics,
        (unsigned long long)draw_caller, (unsigned long long)caller_rva);
    char marker_path[1300];
    if (marker_length > 0 && (size_t)marker_length < sizeof marker &&
        snprintf(marker_path, sizeof marker_path, "%s/presented.json", g_dir) <
            (int)sizeof marker_path) {
        (void)write_bytes(marker_path, marker, (size_t)marker_length);
    }
    /* Keep the process alive for a bounded interval so an independent window
       screenshot can be taken. The wait must NOT block the presenting thread:
       Core Animation commits the drawable transaction on the main run loop, so
       sleeping here would leave the window blank while the drawable readback
       already contains the image. A detached thread owns the wait instead. */
    int hold = g_hold_ms > 0 ? g_hold_ms : 5000;
    g_repeat_master = master;
    if (pthread_main_np() != 0) {
        /* Keep the main run loop alive for the evidence window. Returning to
           the engine lets it continue into operations outside the admitted
           route (and crash) before Core Animation commits the drawable;
           blocking with sleep() prevents the commit entirely. Running the
           run loop does both: the transaction commits and the engine does not
           advance. */
        NSDate *deadline =
            [NSDate dateWithTimeIntervalSinceNow:(NSTimeInterval)hold / 1000.0];
        while ([deadline timeIntervalSinceNow] > 0.0) {
            @autoreleasepool {
                [[NSRunLoop currentRunLoop]
                    runMode:NSDefaultRunLoopMode
                    beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
            }
            if (g_repeat_master != NULL) {
                static int repeated;
                if (!repeated) {
                    repeated = 1;
                    repeat_present();
                }
            }
        }
    } else {
        pthread_t waiter;
        if (pthread_create(&waiter, NULL, hold_thread, (void *)(intptr_t)hold) == 0) {
            pthread_detach(waiter);
        }
    }
}
