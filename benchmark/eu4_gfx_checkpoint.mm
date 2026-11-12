#import "eu4_gfx_checkpoint.h"

#import "eu4_gfx_init.h"
#import "eu4_metal_events.h"

#import <CommonCrypto/CommonDigest.h>
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
};

static int g_checkpoint;
static uint64_t g_caller_rva;
static int g_occurrence = 1;
static int g_stop = 1;
static char g_dir[1024];
static int g_counts[4];

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
    } else {
        return;
    }
    snprintf(g_dir, sizeof g_dir, "%s", output_dir);
    if (g_checkpoint != EU4_GFX_CHECKPOINT_NONE) {
        eu4_gfx_set_checkpoint_hooks(eu4_gfx_checkpoint_after_clear,
                                     eu4_gfx_checkpoint_before_present);
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
    eu4_gfx_checkpoint_configure(operation, caller_rva, (int)occurrence_value, dir,
                                 stop_after);
    if (g_checkpoint != EU4_GFX_CHECKPOINT_NONE) {
        eu4_gfx_set_checkpoint_hooks(eu4_gfx_checkpoint_after_clear,
                                     eu4_gfx_checkpoint_before_present);
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
