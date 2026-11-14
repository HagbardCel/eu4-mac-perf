#include "eu4_gfx_frame_observe.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define OBS_MAX_RING 32
#define OBS_MAX_PATH 1400
#define OBS_MAX_FRAMES 120000
#define OBS_PREVIEW_MAX 64
#define OBS_MAX_DUMPS 64

typedef struct {
    uint8_t *pixels;
    uint32_t width;
    uint32_t height;
    uint64_t index;
} obs_ring_slot;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static int g_initialized;
static int g_enabled;
static char g_dir[OBS_MAX_PATH];
static char g_backend[24];
static char g_session[128];
static char g_jsonl[OBS_MAX_PATH];
static char g_previews[OBS_MAX_PATH];
static char g_captured[OBS_MAX_PATH];
static char g_request[OBS_MAX_PATH];
static char g_ready[OBS_MAX_PATH];
static char g_release[OBS_MAX_PATH];
static int g_lockstep;
static uint32_t g_lockstep_timeout_ms = 8000;
static uint64_t g_lockstep_timeouts;
static char g_origin[16] = "top_left";
static char g_lockstep_timeout_path[OBS_MAX_PATH];
static uint32_t g_last_draws;
static uint64_t g_last_signature;
static uint32_t g_ring_size = 8;
static uint64_t g_last_index;
static uint64_t g_skipped;
static uint64_t g_dropped;
static uint64_t g_emitted;
static uint64_t g_limit_reported;
static obs_ring_slot g_ring[OBS_MAX_RING];
static int g_ring_next;

static uint32_t g_frame_draws;
static uint64_t g_frame_signature = 1469598103934665603ULL;
static uint32_t g_frame_distinct;
#define OBS_MAX_CALLERS 32
static uint64_t g_frame_callers[OBS_MAX_CALLERS];

/* --- SHA-256 ------------------------------------------------------------ */

typedef struct {
    uint32_t state[8];
    uint64_t bytes;
    uint8_t block[64];
    size_t used;
} obs_sha256;

static const uint32_t kK[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

static uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

static void sha256_block(obs_sha256 *ctx, const uint8_t *block) {
    uint32_t w[64];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)block[i * 4] << 24) | ((uint32_t)block[i * 4 + 1] << 16) |
               ((uint32_t)block[i * 4 + 2] << 8) | (uint32_t)block[i * 4 + 3];
    }
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = ctx->state[0], b = ctx->state[1], c = ctx->state[2], d = ctx->state[3];
    uint32_t e = ctx->state[4], f = ctx->state[5], g = ctx->state[6], h = ctx->state[7];
    for (int i = 0; i < 64; i++) {
        uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t t1 = h + s1 + ch + kK[i] + w[i];
        uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        uint32_t t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    ctx->state[0] += a; ctx->state[1] += b; ctx->state[2] += c; ctx->state[3] += d;
    ctx->state[4] += e; ctx->state[5] += f; ctx->state[6] += g; ctx->state[7] += h;
}

static void sha256_init(obs_sha256 *ctx) {
    static const uint32_t init[8] = {
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
        0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    };
    memcpy(ctx->state, init, sizeof init);
    ctx->bytes = 0;
    ctx->used = 0;
}

static void sha256_update(obs_sha256 *ctx, const uint8_t *data, size_t size) {
    ctx->bytes += size;
    while (size > 0) {
        size_t room = sizeof ctx->block - ctx->used;
        size_t take = size < room ? size : room;
        memcpy(ctx->block + ctx->used, data, take);
        ctx->used += take;
        data += take;
        size -= take;
        if (ctx->used == sizeof ctx->block) {
            sha256_block(ctx, ctx->block);
            ctx->used = 0;
        }
    }
}

static void sha256_final(obs_sha256 *ctx, uint8_t out[32]) {
    uint64_t bits = ctx->bytes * 8;
    uint8_t pad = 0x80;
    sha256_update(ctx, &pad, 1);
    uint8_t zero = 0;
    while (ctx->used != 56) {
        sha256_update(ctx, &zero, 1);
    }
    uint8_t length[8];
    for (int i = 0; i < 8; i++) {
        length[7 - i] = (uint8_t)(bits >> (i * 8));
    }
    sha256_update(ctx, length, 8);
    for (int i = 0; i < 8; i++) {
        out[i * 4] = (uint8_t)(ctx->state[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(ctx->state[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(ctx->state[i] >> 8);
        out[i * 4 + 3] = (uint8_t)ctx->state[i];
    }
}

static void digest_hex(const uint8_t digest[32], char out[65]) {
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 15];
    }
    out[64] = 0;
}

static void sha256_hex(const uint8_t *data, size_t size, char out[65]) {
    obs_sha256 ctx;
    uint8_t digest[32];
    sha256_init(&ctx);
    sha256_update(&ctx, data, size);
    sha256_final(&ctx, digest);
    digest_hex(digest, out);
}

/* --- filesystem helpers ------------------------------------------------- */

static int mkdir_p(const char *path) {
    char buffer[OBS_MAX_PATH];
    size_t length = strlen(path);
    if (length == 0 || length >= sizeof buffer) {
        return -1;
    }
    memcpy(buffer, path, length + 1);
    for (size_t i = 1; i < length; i++) {
        if (buffer[i] == '/') {
            buffer[i] = 0;
            if (mkdir(buffer, 0755) != 0 && errno != EEXIST) {
                return -1;
            }
            buffer[i] = '/';
        }
    }
    return (mkdir(buffer, 0755) == 0 || errno == EEXIST) ? 0 : -1;
}

static int write_atomic(const char *path, const void *data, size_t size) {
    char temporary[OBS_MAX_PATH];
    if (snprintf(temporary, sizeof temporary, "%s.tmp", path) >= (int)sizeof temporary) {
        return -1;
    }
    int fd = open(temporary, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        return -1;
    }
    const uint8_t *cursor = data;
    size_t remaining = size;
    while (remaining > 0) {
        ssize_t written = write(fd, cursor, remaining);
        if (written <= 0) {
            close(fd);
            unlink(temporary);
            return -1;
        }
        cursor += written;
        remaining -= (size_t)written;
    }
    if (fsync(fd) != 0) {
        close(fd);
        unlink(temporary);
        return -1;
    }
    close(fd);
    if (rename(temporary, path) != 0) {
        unlink(temporary);
        return -1;
    }
    return 0;
}

static int append_line(const char *path, const char *line) {
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) {
        return -1;
    }
    size_t length = strlen(line);
    ssize_t written = write(fd, line, length);
    close(fd);
    return written == (ssize_t)length ? 0 : -1;
}

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

/* --- initialization ----------------------------------------------------- */

int eu4_gfx_frame_observe_init(const char *backend, const char *dir,
                               const char *session) {
    if (backend == NULL || dir == NULL || dir[0] != '/') {
        return -1;
    }
    pthread_mutex_lock(&g_lock);
    if (g_initialized) {
        int same = strcmp(g_dir, dir) == 0;
        pthread_mutex_unlock(&g_lock);
        return same ? 0 : -1;
    }
    if (strlen(dir) >= sizeof g_dir || strlen(backend) >= sizeof g_backend) {
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    snprintf(g_backend, sizeof g_backend, "%s", backend);
    snprintf(g_session, sizeof g_session, "%s", session != NULL ? session : "");
    snprintf(g_jsonl, sizeof g_jsonl, "%s/frame-observe.jsonl", dir);
    snprintf(g_previews, sizeof g_previews, "%s/previews", dir);
    snprintf(g_captured, sizeof g_captured, "%s/captured", dir);
    snprintf(g_request, sizeof g_request, "%s/capture-request.json", dir);
    snprintf(g_ready, sizeof g_ready, "%s/ready", dir);
    snprintf(g_release, sizeof g_release, "%s/release", dir);
    snprintf(g_lockstep_timeout_path, sizeof g_lockstep_timeout_path,
             "%s/lockstep-timeout.jsonl", dir);
    const char *lockstep = getenv("EU4_GFX_FRAME_LOCKSTEP");
    if (lockstep != NULL && strcmp(lockstep, "1") == 0) {
        g_lockstep = 1;
    }
    const char *lockstep_timeout = getenv("EU4_GFX_FRAME_LOCKSTEP_TIMEOUT_MS");
    if (lockstep_timeout != NULL) {
        long value = strtol(lockstep_timeout, NULL, 10);
        if (value >= 100 && value <= 120000) {
            g_lockstep_timeout_ms = (uint32_t)value;
        }
    }
    const char *ring = getenv("EU4_GFX_FRAME_RING");
    if (ring != NULL) {
        long value = strtol(ring, NULL, 10);
        if (value >= 0 && value <= OBS_MAX_RING) {
            g_ring_size = (uint32_t)value;
        }
    }
    if (mkdir_p(dir) != 0 || mkdir_p(g_previews) != 0 || mkdir_p(g_captured) != 0 ||
        (g_lockstep && (mkdir_p(g_ready) != 0 || mkdir_p(g_release) != 0))) {
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    g_initialized = 1;
    g_enabled = 1;
    pthread_mutex_unlock(&g_lock);
    return 0;
}

int eu4_gfx_frame_observe_enabled(void) {
    return g_enabled;
}

uint32_t eu4_gfx_frame_observe_dropped(void) {
    return (uint32_t)g_dropped;
}

/* --- draw composition --------------------------------------------------- */

void eu4_gfx_frame_observe_note_draw(uint64_t caller_rva) {
    if (!g_enabled) {
        return;
    }
    g_frame_draws += 1;
    /* The original-GL observer records the unique caller set in encounter
       order.  Using the same signature over the same set makes the two
       backends' composition signatures comparable for alignment. */
    for (uint32_t i = 0; i < g_frame_distinct; i++) {
        if (g_frame_callers[i] == caller_rva) {
            return;
        }
    }
    if (g_frame_distinct >= OBS_MAX_CALLERS) {
        return;
    }
    g_frame_callers[g_frame_distinct++] = caller_rva;
    g_frame_signature ^= caller_rva;
    g_frame_signature *= 1099511628211ULL;
}

void eu4_gfx_frame_observe_take_stats(uint32_t *draws, uint64_t *signature,
                                      uint32_t *distinct_callers) {
    if (draws != NULL) {
        *draws = g_frame_draws;
    }
    if (signature != NULL) {
        *signature = g_frame_signature;
    }
    if (distinct_callers != NULL) {
        *distinct_callers = g_frame_distinct;
    }
    g_frame_draws = 0;
    g_frame_signature = 1469598103934665603ULL;
    g_frame_distinct = 0;
    memset(g_frame_callers, 0, sizeof g_frame_callers);
}

/* --- ring and dumps ----------------------------------------------------- */

static void ring_store(const uint8_t *bgra, uint32_t width, uint32_t height,
                       uint64_t index) {
    if (g_ring_size == 0) {
        return;
    }
    if (width > 8192 || height > 8192) {
        return;
    }
    size_t bytes = (size_t)width * height * 4;
    obs_ring_slot *slot = &g_ring[g_ring_next];
    if (slot->pixels != NULL && (slot->width != width || slot->height != height)) {
        free(slot->pixels);
        slot->pixels = NULL;
    }
    if (slot->pixels == NULL) {
        slot->pixels = malloc(bytes);
        if (slot->pixels == NULL) {
            return;
        }
    }
    memcpy(slot->pixels, bgra, bytes);
    slot->width = width;
    slot->height = height;
    slot->index = index;
    g_ring_next = (g_ring_next + 1) % (int)g_ring_size;
}

static obs_ring_slot *ring_find(uint64_t index) {
    for (uint32_t i = 0; i < g_ring_size; i++) {
        if (g_ring[i].pixels != NULL && g_ring[i].index == index) {
            return &g_ring[i];
        }
    }
    return NULL;
}

/* Minimal request parser: {"frames":[12,13], ...}.  A malformed request is
 * ignored; the coordinator must never crash the game with a bad file. */
static int parse_frames(const char *text, uint64_t *frames, int capacity) {
    const char *key = strstr(text, "\"frames\"");
    if (key == NULL) {
        return 0;
    }
    const char *cursor = strchr(key, '[');
    if (cursor == NULL) {
        return 0;
    }
    cursor++;
    int count = 0;
    while (*cursor != 0 && *cursor != ']' && count < capacity) {
        while (*cursor == ' ' || *cursor == '\t' || *cursor == '\n' || *cursor == ',') {
            cursor++;
        }
        if (*cursor == ']' || *cursor == 0) {
            break;
        }
        char *end = NULL;
        unsigned long long value = strtoull(cursor, &end, 10);
        if (end == cursor) {
            break;
        }
        frames[count++] = (uint64_t)value;
        cursor = end;
    }
    return count;
}

static void dump_ring(obs_ring_slot *slot, uint32_t frame_draws,
                      uint64_t signature, int gpu_completed, int capture_valid) {
    char raw_path[OBS_MAX_PATH];
    char json_path[OBS_MAX_PATH];
    snprintf(raw_path, sizeof raw_path, "%s/frame-%llu.bgra", g_captured,
             (unsigned long long)slot->index);
    if (write_atomic(raw_path, slot->pixels,
                     (size_t)slot->width * slot->height * 4) != 0) {
        g_dropped += 1;
        return;
    }
    char digest[65];
    sha256_hex(slot->pixels, (size_t)slot->width * slot->height * 4, digest);
    snprintf(json_path, sizeof json_path, "%s/frame-%llu.json", g_captured,
             (unsigned long long)slot->index);
    char body[1024];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-frame-dump/v1\",\"backend\":\"%s\",\"session\":\"%s\","
        "\"pid\":%d,\"frame_index\":%llu,\"width\":%u,\"height\":%u,"
        "\"origin\":\"%s\",\"pixel_format\":\"BGRA8\",\"row_stride\":%u,"
        "\"raw_file\":\"%s\","
        "\"raw_bytes\":%zu,\"sha256\":\"%s\",\"draws\":%u,"
        "\"caller_signature\":\"0x%016llx\",\"gpu_completed\":%s,"
        "\"capture_valid\":%s,\"source\":\"ring\"}\n",
        g_backend, g_session, (int)getpid(), (unsigned long long)slot->index,
        slot->width, slot->height, g_origin, slot->width * 4, raw_path,
        (size_t)slot->width * slot->height * 4, digest, frame_draws,
        (unsigned long long)signature, gpu_completed ? "true" : "false",
        capture_valid ? "true" : "false");
    if (length <= 0 || (size_t)length >= sizeof body ||
        write_atomic(json_path, body, (size_t)length) != 0) {
        g_dropped += 1;
    }
}

static void handle_request(uint32_t frame_draws, uint64_t signature,
                           int gpu_completed, int capture_valid) {
    struct stat status;
    static struct stat last_status;
    static int have_status;
    static uint64_t pending[OBS_MAX_DUMPS];
    static int pending_count;
    if (stat(g_request, &status) == 0) {
        int changed = !have_status || status.st_mtime != last_status.st_mtime ||
                      status.st_size != last_status.st_size;
        if (changed) {
            last_status = status;
            have_status = 1;
            FILE *file = fopen(g_request, "r");
            if (file != NULL) {
                char text[4096];
                size_t used = fread(text, 1, sizeof text - 1, file);
                fclose(file);
                text[used] = 0;
                pending_count = parse_frames(text, pending, OBS_MAX_DUMPS);
            }
        }
    }
    /* A requested frame may not be in the ring yet.  Keep the request pending
       until it is captured, then drop it. */
    for (int i = 0; i < pending_count; i++) {
        obs_ring_slot *slot = ring_find(pending[i]);
        if (slot != NULL) {
            dump_ring(slot, frame_draws, signature, gpu_completed, capture_valid);
            pending[i] = pending[pending_count - 1];
            pending_count -= 1;
            i -= 1;
        }
    }
}

/* --- preview ------------------------------------------------------------ */

static void write_preview(const uint8_t *bgra, uint32_t width, uint32_t height,
                          char *digest_out, uint32_t *out_width,
                          uint32_t *out_height) {
    uint32_t target_w = OBS_PREVIEW_MAX;
    uint32_t target_h = OBS_PREVIEW_MAX;
    if (width >= height) {
        target_h = (uint32_t)((uint64_t)height * OBS_PREVIEW_MAX / width);
    } else {
        target_w = (uint32_t)((uint64_t)width * OBS_PREVIEW_MAX / height);
    }
    if (target_w == 0) {
        target_w = 1;
    }
    if (target_h == 0) {
        target_h = 1;
    }
    size_t bytes = (size_t)target_w * target_h * 4;
    uint8_t *preview = malloc(bytes);
    if (preview == NULL) {
        digest_out[0] = 0;
        *out_width = 0;
        *out_height = 0;
        return;
    }
    for (uint32_t y = 0; y < target_h; y++) {
        uint32_t y0 = (uint32_t)((uint64_t)y * height / target_h);
        uint32_t y1 = (uint32_t)(((uint64_t)(y + 1) * height + target_h - 1) / target_h);
        if (y1 <= y0) {
            y1 = y0 + 1;
        }
        for (uint32_t x = 0; x < target_w; x++) {
            uint32_t x0 = (uint32_t)((uint64_t)x * width / target_w);
            uint32_t x1 = (uint32_t)(((uint64_t)(x + 1) * width + target_w - 1) / target_w);
            if (x1 <= x0) {
                x1 = x0 + 1;
            }
            uint64_t sums[4] = {0, 0, 0, 0};
            uint64_t samples = 0;
            for (uint32_t sy = y0; sy < y1 && sy < height; sy++) {
                for (uint32_t sx = x0; sx < x1 && sx < width; sx++) {
                    const uint8_t *pixel = bgra + ((size_t)sy * width + sx) * 4;
                    sums[0] += pixel[0];
                    sums[1] += pixel[1];
                    sums[2] += pixel[2];
                    sums[3] += pixel[3];
                    samples += 1;
                }
            }
            uint8_t *out = preview + ((size_t)y * target_w + x) * 4;
            for (int channel = 0; channel < 4; channel++) {
                out[channel] = samples ? (uint8_t)(sums[channel] / samples) : 0;
            }
        }
    }
    sha256_hex(preview, bytes, digest_out);
    char path[OBS_MAX_PATH];
    snprintf(path, sizeof path, "%s/%s.bgra", g_previews, digest_out);
    struct stat status;
    if (stat(path, &status) != 0) {
        (void)write_atomic(path, preview, bytes);
    }
    free(preview);
    *out_width = target_w;
    *out_height = target_h;
}

/* --- emit --------------------------------------------------------------- */

int eu4_gfx_frame_observe_emit(const uint8_t *bgra, uint32_t width,
                               uint32_t height, const char *origin,
                               uint32_t frame_index, uint32_t draws,
                               uint64_t signature, uint32_t distinct_callers,
                               int gpu_completed, int capture_valid,
                               const char *error) {
    if (!g_enabled || bgra == NULL || width == 0 || height == 0) {
        return -1;
    }
    if (frame_index == 0 || frame_index > OBS_MAX_FRAMES) {
        return -1;
    }
    if (origin != NULL && origin[0] != 0) {
        snprintf(g_origin, sizeof g_origin, "%s", origin);
    }
    g_last_draws = draws;
    g_last_signature = signature;
    char full_digest[65];
    char preview_digest[65];
    uint32_t preview_w = 0, preview_h = 0;
    sha256_hex(bgra, (size_t)width * height * 4, full_digest);

    pthread_mutex_lock(&g_lock);
    if (g_emitted >= OBS_MAX_FRAMES) {
        if (!g_limit_reported) {
            g_limit_reported = 1;
            (void)append_line(g_jsonl,
                              "{\"kind\":\"frame_limit_reached\"}\n");
        }
        g_dropped += 1;
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    write_preview(bgra, width, height, preview_digest, &preview_w, &preview_h);
    uint64_t gap = 0;
    if (g_last_index != 0 && frame_index > g_last_index + 1) {
        gap = frame_index - g_last_index - 1;
        g_skipped += gap;
    }
    ring_store(bgra, width, height, frame_index);
    handle_request(draws, signature, gpu_completed, capture_valid);

    char line[1400];
    int length = snprintf(
        line, sizeof line,
        "{\"schema\":\"%s\",\"backend\":\"%s\",\"session\":\"%s\",\"pid\":%d,"
        "\"frame_index\":%u,\"width\":%u,\"height\":%u,\"origin\":\"%s\","
        "\"pixel_format\":\"BGRA8\",\"row_stride\":%u,\"sha256\":\"%s\","
        "\"preview_file\":\"previews/%s.bgra\",\"preview_width\":%u,"
        "\"preview_height\":%u,\"preview_sha256\":\"%s\",\"draws\":%u,"
        "\"caller_signature\":\"0x%016llx\",\"distinct_callers\":%u,"
        "\"skipped_before\":%llu,\"gpu_completed\":%s,\"capture_valid\":%s,"
        "\"error\":\"%s\",\"ts_ms\":%llu}\n",
        EU4_GFX_FRAME_OBSERVE_SCHEMA, g_backend, g_session, (int)getpid(),
        frame_index, width, height, origin != NULL ? origin : "top_left",
        width * 4, full_digest, preview_digest, preview_w, preview_h,
        preview_digest, draws, (unsigned long long)signature, distinct_callers,
        (unsigned long long)gap, gpu_completed ? "true" : "false",
        capture_valid ? "true" : "false", error != NULL ? error : "",
        (unsigned long long)now_ms());
    if (length <= 0 || (size_t)length >= sizeof line || append_line(g_jsonl, line) != 0) {
        g_dropped += 1;
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    g_emitted += 1;
    g_last_index = frame_index;
    pthread_mutex_unlock(&g_lock);
    return 0;
}



int eu4_gfx_frame_observe_wants(uint32_t frame_index) {
    if (!g_enabled || g_request[0] == 0) {
        return 0;
    }
    struct stat status;
    static struct stat last_status;
    static int have_status;
    static uint64_t frames[OBS_MAX_DUMPS];
    static int count;
    if (stat(g_request, &status) == 0) {
        if (!have_status || status.st_mtime != last_status.st_mtime ||
            status.st_size != last_status.st_size) {
            last_status = status;
            have_status = 1;
            FILE *file = fopen(g_request, "r");
            if (file != NULL) {
                char text[4096];
                size_t used = fread(text, 1, sizeof text - 1, file);
                fclose(file);
                text[used] = 0;
                count = parse_frames(text, frames, OBS_MAX_DUMPS);
            }
        }
    }
    for (int i = 0; i < count; i++) {
        if (frames[i] == (uint64_t)frame_index) {
            return 1;
        }
    }
    return 0;
}

int eu4_gfx_frame_observe_emit_metadata(uint32_t width, uint32_t height,
                                        const char *origin,
                                        uint32_t frame_index, uint32_t draws,
                                        uint64_t signature,
                                        uint32_t distinct_callers,
                                        int gpu_completed, const char *error) {
    if (!g_enabled || width == 0 || height == 0) {
        return -1;
    }
    if (frame_index == 0 || frame_index > OBS_MAX_FRAMES) {
        return -1;
    }
    g_last_draws = draws;
    g_last_signature = signature;
    pthread_mutex_lock(&g_lock);
    if (g_emitted >= OBS_MAX_FRAMES) {
        g_dropped += 1;
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    uint64_t gap = 0;
    if (g_last_index != 0 && frame_index > g_last_index + 1) {
        gap = frame_index - g_last_index - 1;
        g_skipped += gap;
    }
    char line[900];
    int length = snprintf(
        line, sizeof line,
        "{\"schema\":\"%s\",\"backend\":\"%s\",\"session\":\"%s\","
        "\"pid\":%d,\"frame_index\":%u,\"width\":%u,\"height\":%u,"
        "\"origin\":\"%s\",\"pixel_format\":\"BGRA8\",\"row_stride\":%u,"
        "\"sha256\":\"\",\"preview_file\":\"\",\"preview_width\":0,"
        "\"preview_height\":0,\"content\":\"metadata\",\"draws\":%u,"
        "\"caller_signature\":\"0x%016llx\",\"distinct_callers\":%u,"
        "\"skipped_before\":%llu,\"gpu_completed\":%s,"
        "\"capture_valid\":true,\"error\":\"%s\",\"ts_ms\":%llu}\n",
        EU4_GFX_FRAME_OBSERVE_SCHEMA, g_backend, g_session, (int)getpid(),
        frame_index, width, height, origin != NULL ? origin : "top_left",
        width * 4, draws, (unsigned long long)signature, distinct_callers,
        (unsigned long long)gap, gpu_completed ? "true" : "false",
        error != NULL ? error : "", (unsigned long long)now_ms());
    if (length <= 0 || (size_t)length >= sizeof line ||
        append_line(g_jsonl, line) != 0) {
        g_dropped += 1;
        pthread_mutex_unlock(&g_lock);
        return -1;
    }
    g_emitted += 1;
    g_last_index = frame_index;
    pthread_mutex_unlock(&g_lock);
    return 0;
}

int eu4_gfx_frame_observe_lockstep_enabled(void) {
    return g_lockstep;
}

/* Publish this frame's readiness, service pending dump requests while paused,
   then wait for the coordinator's release.  The wait is bounded so a crashed
   coordinator can never suspend the game indefinitely. */
int eu4_gfx_frame_observe_lockstep_wait(uint32_t frame_index,
                                        uint32_t timeout_ms) {
    if (!g_lockstep || !g_enabled) {
        return 0;
    }
    char ready_path[OBS_MAX_PATH];
    char release_path[OBS_MAX_PATH];
    snprintf(ready_path, sizeof ready_path, "%s/frame-%u.json", g_ready, frame_index);
    snprintf(release_path, sizeof release_path, "%s/frame-%u", g_release, frame_index);
    char body[512];
    int length = snprintf(
        body, sizeof body,
        "{\"schema\":\"gfx-frame-ready/v1\",\"backend\":\"%s\","
        "\"session\":\"%s\",\"pid\":%d,\"frame_index\":%u}\n",
        g_backend, g_session, (int)getpid(), frame_index);
    if (length <= 0 || (size_t)length >= sizeof body ||
        write_atomic(ready_path, body, (size_t)length) != 0) {
        return -1;
    }
    uint32_t timeout = timeout_ms != 0 ? timeout_ms : g_lockstep_timeout_ms;
    uint64_t deadline = now_ms() + timeout;
    while (now_ms() < deadline) {
        struct stat status;
        if (stat(release_path, &status) == 0) {
            return 0;
        }
        /* The coordinator needs a dump while the process is paused here. */
        pthread_mutex_lock(&g_lock);
        handle_request(g_last_draws, g_last_signature, 1, 1);
        pthread_mutex_unlock(&g_lock);
        usleep(1000);
    }
    g_lockstep_timeouts += 1;
    {
        char line[256];
        int length = snprintf(
            line, sizeof line,
            "{\"schema\":\"gfx-lockstep-timeout/v1\",\"backend\":\"%s\","
            "\"pid\":%d,\"frame_index\":%u}\n",
            g_backend, (int)getpid(), frame_index);
        if (length > 0 && (size_t)length < sizeof line) {
            (void)append_line(g_lockstep_timeout_path, line);
        }
    }
    return 1;
}
