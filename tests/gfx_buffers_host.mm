#import "eu4_gfx_buffers.h"
#import "../benchmark/eu4_gfx_init.h"
#import "../benchmark/eu4_metal_events.h"

#import <Metal/Metal.h>
#import <QuartzCore/CAMetalLayer.h>
#import <dlfcn.h>
#import <pthread.h>
#import <spawn.h>
#import <stdio.h>
#import <string.h>
#import <sys/mman.h>
#import <sys/wait.h>
#import <unistd.h>

extern char **environ;

/* Trusted image window. These are not taken from the candidate header. */
enum {
    kIndexCountRva = 0x2238584,
    kIndexCacheTopRva = 0x2238588,
    kIndexCacheRva = 0x2238590,
    kIndexCacheSlots = 128,
};

extern "C" {
void *eu4_gfx_create_index_buffer(void *ignored, const void *data, int32_t count);
void eu4_gfx_update_index_buffer(void *ignored, void *header, const void *data, int32_t count);
void eu4_gfx_destroy_index_buffer(void *header);
void eu4_gfx_set_index_buffer(void *handle, void *header);
int eu4_gfx_index_records(void);
int eu4_gfx_index_gpus(void);
size_t eu4_gfx_index_buffer_length(void *header);
int eu4_gfx_index_buffer_read(void *header, void *dest, size_t n);
int eu4_gfx_index_buffer_shared(void *header);
void *eu4_gfx_current_index_header(void);
int eu4_gfx_current_index_read(void *dest, size_t n);
}

static void fail(const char *message) {
    fprintf(stderr, "%s\n", message);
    _exit(1);
}

static char *g_argv0;
static uint8_t *g_image;
static void *g_slot;
static void *g_master;
static const void *g_expect_fn;
static int g_expect_records;
static int g_expect_gpus;
static int32_t g_expect_counter;
static void *g_kept;
static uint8_t g_kept_byte;
static void *g_kept_index;
static uint8_t g_kept_index_bytes[2];
static int g_expect_index_records;
static int g_expect_index_gpus;
static int32_t g_expect_ib;
static int g_check_master_count;
static int32_t g_expect_master_count;
static int g_check_master_span;
static uint8_t g_master_span[0x58];
static int g_check_master_flag;
static uint8_t g_expect_master_flag;

static int32_t *counter(void) { return (int32_t *)(g_image + EU4_GFX_VB_COUNT_RVA); }

static int32_t *ib_counter(void) { return (int32_t *)(g_image + kIndexCountRva); }

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 800;
    *height = 600;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void boot(int do_init) {
    if (g_image == NULL) {
        size_t bytes = kIndexCacheRva + (size_t)kIndexCacheSlots * sizeof(void *);
        g_image = (uint8_t *)mmap(NULL, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (g_image == MAP_FAILED) {
            fail("image map");
        }
        eu4_gfx_set_image(g_image, &g_slot);
        eu4_gfx_set_sdl(fake_window, fake_size);
        eu4_gfx_set_default_writers(eu4_gfx_write_default_blend, write_depth, eu4_gfx_write_default_raster);
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (device == nil) {
            fail("no Metal device");
        }
        CAMetalLayer *layer = [CAMetalLayer layer];
        layer.device = device;
        eu4_gfx_set_layer((__bridge void *)layer);
    }
    if (!do_init) {
        return;
    }
    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    uint32_t window_id = 7, source = 9;
    memcpy(handle, &window_id, 4);
    memcpy(handle + 0x18, &source, 4);
    uint8_t ok = 0;
    g_master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || g_master == NULL) {
        fail("init");
    }
}

static void expect_header(void *header, int32_t stride, int32_t count) {
    int32_t got_stride = 0, got_count = 0;
    uint32_t name = 0;
    memcpy(&got_stride, header, 4);
    memcpy(&got_count, (uint8_t *)header + 4, 4);
    memcpy(&name, (uint8_t *)header + 8, 4);
    if (got_stride != stride || got_count != count || name != 1) {
        fail("header");
    }
}

static void child_stop(const char *reason) {
    (void)reason;
    Dl_info saved, expected;
    const void *caller = eu4_gfx_test_stop_caller();
    if (dladdr(caller, &saved) == 0 || dladdr(g_expect_fn, &expected) == 0 ||
        saved.dli_saddr == NULL || saved.dli_saddr != expected.dli_saddr ||
        eu4_gfx_vertex_records() != g_expect_records || eu4_gfx_vertex_gpus() != g_expect_gpus ||
        eu4_gfx_index_records() != g_expect_index_records || eu4_gfx_index_gpus() != g_expect_index_gpus ||
        g_image == NULL || *counter() != g_expect_counter || *ib_counter() != g_expect_ib) {
        _exit(3);
    }
    if (g_check_master_count) {
        int32_t got = 0;
        memcpy(&got, (uint8_t *)g_master + 0x68, 4);
        if (got != g_expect_master_count) {
            _exit(3);
        }
    }
    if (g_check_master_span &&
        memcmp(g_master_span, (uint8_t *)g_master + 0x130, sizeof g_master_span) != 0) {
        _exit(3);
    }
    if (g_check_master_flag && ((uint8_t *)g_master)[0x170] != g_expect_master_flag) {
        _exit(3);
    }
    if (g_kept != NULL) {
        size_t length = eu4_gfx_vertex_buffer_length(g_kept);
        uint8_t back[64];
        if (length == 0 || length > sizeof back ||
            eu4_gfx_vertex_buffer_read(g_kept, back, length) != 0 || back[0] != g_kept_byte) {
            _exit(3);
        }
    }
    if (g_kept_index != NULL) {
        uint8_t registry[2];
        uint8_t bound[2];
        if (eu4_gfx_index_buffer_read(g_kept_index, registry, 2) != 0 ||
            memcmp(registry, g_kept_index_bytes, 2) != 0 ||
            eu4_gfx_current_index_read(bound, 2) != 0 || memcmp(bound, g_kept_index_bytes, 2) != 0) {
            _exit(3);
        }
    }
    _exit(2);
}

static void parent_stop(const char *reason) {
    fprintf(stderr, "unexpected stop %s\n", reason == NULL ? "" : reason);
    _exit(1);
}

static void arm(const void *fn, int records, int gpus, int32_t count) {
    g_expect_fn = fn;
    g_expect_records = records;
    g_expect_gpus = gpus;
    g_expect_counter = count;
    g_expect_index_records = 0;
    g_expect_index_gpus = 0;
    g_expect_ib = 0;
    g_check_master_count = 0;
}

__attribute__((noinline)) static void run_fail_metal(void) {
    boot(1);
    arm((const void *)run_fail_metal, 0, 0, 0);
    eu4_gfx_buffer_test_fail_metal(1);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    fail("metal failure returned");
}

__attribute__((noinline)) static void run_fail_metal_throw(void) {
    boot(1);
    uint8_t byte = 0x5a;
    g_kept = eu4_gfx_create_vertex_buffer(NULL, &byte, 1, 1, 0);
    g_kept_byte = byte;
    if (g_kept == NULL || *counter() != 1) {
        fail("metal-throw setup");
    }
    arm((const void *)run_fail_metal_throw, 1, 1, 1);
    eu4_gfx_buffer_test_fail_metal_throw(1);
    eu4_gfx_update_vertex_buffer(&g_master, g_kept, NULL, 4, 0, 0);
    fail("metal throw returned");
}

__attribute__((noinline)) static void run_fail_entry(void) {
    boot(1);
    arm((const void *)run_fail_entry, 0, 0, 0);
    eu4_gfx_buffer_test_fail_entry(1);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    fail("entry failure returned");
}

__attribute__((noinline)) static void run_fail_insert(void) {
    boot(1);
    arm((const void *)run_fail_insert, 0, 0, 0);
    eu4_gfx_buffer_test_fail_insert(1);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    fail("insert failure returned");
}

__attribute__((noinline)) static void run_fail_header(void) {
    boot(1);
    arm((const void *)run_fail_header, 0, 0, 0);
    eu4_gfx_buffer_test_fail_header(1);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    fail("header failure returned");
}

__attribute__((noinline)) static void run_bad_size(void) {
    boot(1);
    arm((const void *)run_bad_size, 0, 0, 0);
    eu4_gfx_create_vertex_buffer(NULL, NULL, -1, 4, 0);
    fail("negative size returned");
}

__attribute__((noinline)) static void run_bounds(void) {
    boot(1);
    uint8_t bytes[16];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 4, 4, 0);
    if (header == NULL || *counter() != 1) {
        fail("bounds setup");
    }
    arm((const void *)run_bounds, 1, 1, 1);
    eu4_gfx_update_vertex_buffer(&g_master, header, bytes, 4, 8, 1);
    fail("bounds returned");
}

__attribute__((noinline)) static void run_null_data(void) {
    boot(1);
    uint8_t bytes[16];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 4, 4, 0);
    if (header == NULL) {
        fail("null-data setup");
    }
    arm((const void *)run_null_data, 1, 1, 1);
    eu4_gfx_update_vertex_buffer(&g_master, header, NULL, 1, 0, 1);
    fail("null subrange returned");
}

__attribute__((noinline)) static void run_after_shutdown(void) {
    boot(1);
    void *header = eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 1, 1);
    if (header == NULL || *counter() != 1) {
        fail("shutdown setup");
    }
    eu4_gfx_shutdown(g_master);
    if (eu4_gfx_vertex_records() != 1 || eu4_gfx_vertex_gpus() != 0 || *counter() != 1) {
        fail("shutdown dropped the header");
    }
    arm((const void *)run_after_shutdown, 1, 0, 1);
    eu4_gfx_update_vertex_buffer(&g_master, header, NULL, 4, 0, 0);
    fail("update after shutdown returned");
}

__attribute__((noinline)) static void run_double_destroy(void) {
    boot(1);
    void *header = eu4_gfx_create_vertex_buffer(NULL, NULL, 2, 2, 0);
    eu4_gfx_destroy_vertex_buffer(header);
    if (*counter() != 0 || eu4_gfx_vertex_records() != 0) {
        fail("first destroy");
    }
    arm((const void *)run_double_destroy, 0, 0, 0);
    eu4_gfx_destroy_vertex_buffer(header);
    fail("second destroy returned");
}

__attribute__((noinline)) static void run_no_backend(void) {
    boot(0);
    arm((const void *)run_no_backend, 0, 0, 0);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 1, 1, 0);
    fail("create without backend returned");
}

__attribute__((noinline)) static void *wrong_create(void *unused) {
    (void)unused;
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    return NULL;
}

__attribute__((noinline)) static void run_wrong_thread(void) {
    boot(1);
    arm((const void *)wrong_create, 0, 0, 0);
    pthread_t thread;
    if (pthread_create(&thread, NULL, wrong_create, NULL) != 0) {
        fail("pthread");
    }
    pthread_join(thread, NULL);
    fail("wrong thread returned");
}

__attribute__((noinline)) static void *wrong_shutdown(void *unused) {
    (void)unused;
    eu4_gfx_shutdown(g_master);
    return NULL;
}

__attribute__((noinline)) static void run_wrong_shutdown(void) {
    boot(1);
    void *header = eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    if (header == NULL || eu4_gfx_vertex_gpus() != 1) {
        fail("shutdown-thread setup");
    }
    arm((const void *)wrong_shutdown, 1, 1, 1);
    pthread_t thread;
    if (pthread_create(&thread, NULL, wrong_shutdown, NULL) != 0) {
        fail("pthread");
    }
    pthread_join(thread, NULL);
    fail("wrong shutdown returned");
}

static void expect_child(const char *mode, const char *symbol, const char *reason, const char *phase) {
    char dir[] = "/tmp/eu4-vb-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("child dir");
    }
    char *argv[] = {g_argv0, (char *)mode, dir, NULL};
    pid_t pid = 0;
    if (posix_spawn(&pid, g_argv0, NULL, NULL, argv, environ) != 0) {
        fail("spawn");
    }
    int status = 0;
    if (waitpid(pid, &status, 0) != pid || !WIFEXITED(status)) {
        fail("child wait");
    }
    if (WEXITSTATUS(status) == 3) {
        fail("child invariants");
    }
    if (WEXITSTATUS(status) != 2) {
        fail(mode);
    }
    char path[80];
    snprintf(path, sizeof path, "%s/stop.json", dir);
    FILE *file = fopen(path, "r");
    char body[1280];
    if (file == NULL || fgets(body, sizeof body, file) == NULL) {
        fail("stop.json");
    }
    fclose(file);
    char symbol_bit[64];
    char reason_bit[64];
    char phase_bit[64];
    snprintf(symbol_bit, sizeof symbol_bit, "\"symbol\":\"%s\"", symbol);
    snprintf(reason_bit, sizeof reason_bit, "\"reason\":\"%s\"", reason);
    snprintf(phase_bit, sizeof phase_bit, "\"phase\":\"%s\"", phase);
    if (strstr(body, symbol_bit) == NULL || strstr(body, reason_bit) == NULL ||
        strstr(body, phase_bit) == NULL) {
        fail("stop fields");
    }
}

static char g_parent_dir[64];

__attribute__((noinline)) static void success(void);

static void read_create(char *body, size_t cap) {
    char path[96];
    snprintf(path, sizeof path, "%s/create.json", g_parent_dir);
    FILE *file = fopen(path, "r");
    if (file == NULL || fgets(body, (int)cap, file) == NULL) {
        fail("create.json");
    }
    fclose(file);
}

static void expect_create_caller(const char *body) {
    char symbol_bit[] = "\"symbol\":\"_GfxCreateVertexBuffer\"";
    char success_bit[] = "\"success\":1";
    char pid_bit[32];
    snprintf(pid_bit, sizeof pid_bit, "\"pid\":%d", (int)getpid());
    const char *address_bit = strstr(body, "\"address\":\"0x");
    unsigned long long address = 0;
    if (strstr(body, symbol_bit) == NULL || strstr(body, success_bit) == NULL ||
        strstr(body, pid_bit) == NULL || address_bit == NULL ||
        sscanf(address_bit, "\"address\":\"0x%llx\"", &address) != 1) {
        fail("create fields");
    }
    Dl_info info;
    memset(&info, 0, sizeof info);
    if (dladdr((void *)(uintptr_t)address, &info) == 0 || info.dli_saddr != (void *)success) {
        fail("create caller");
    }
}

__attribute__((noinline)) static void success(void) {
    boot(1);
    if (*counter() != 0) {
        fail("counter did not start at zero");
    }
    uint8_t initial[16];
    memset(initial, 0x11, sizeof initial);
    void *header = eu4_gfx_create_vertex_buffer((void *)0x1, initial, 4, 4, 0);
    char first_receipt[1280];
    read_create(first_receipt, sizeof first_receipt);
    expect_create_caller(first_receipt);
    expect_header(header, 4, 4);
    uint8_t got[16];
    if (*counter() != 1 || !eu4_gfx_vertex_buffer_shared(header) ||
        eu4_gfx_vertex_buffer_length(header) != 16 || eu4_gfx_vertex_buffer_read(header, got, 16) != 0 ||
        memcmp(got, initial, 16) != 0) {
        fail("create bytes");
    }
    uint8_t shorter[8];
    memset(shorter, 0x22, sizeof shorter);
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_update_vertex_buffer(&g_master, header, shorter, 2, 0, 0);
    expect_header(header, 4, 4);
    if (eu4_gfx_vertex_buffer_length(header) != 8 || eu4_gfx_vertex_buffer_read(header, got, 8) != 0 ||
        memcmp(got, shorter, 8) != 0 || ((uint8_t *)g_master)[0x170] != 1) {
        fail("shorter replace");
    }
    memset(shorter, 0x33, sizeof shorter);
    eu4_gfx_update_vertex_buffer(&g_master, header, shorter, 2, 0, 0);
    if (eu4_gfx_vertex_buffer_read(header, got, 8) != 0 || memcmp(got, shorter, 8) != 0) {
        fail("equal replace");
    }
    uint8_t patch[4] = {1, 2, 3, 4};
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_update_vertex_buffer(&g_master, header, patch, 1, 4, 1);
    if (eu4_gfx_vertex_buffer_read(header, got, 8) != 0 || memcmp(got + 4, patch, 4) != 0 ||
        memcmp(got, shorter, 4) != 0 || ((uint8_t *)g_master)[0x170] != 1) {
        fail("subrange");
    }
    eu4_gfx_update_vertex_buffer(&g_master, header, NULL, 2, 0, 0);
    if (eu4_gfx_vertex_buffer_length(header) != 8 || !eu4_gfx_vertex_buffer_shared(header)) {
        fail("null replace");
    }
    eu4_gfx_destroy_vertex_buffer(NULL);
    if (*counter() != 1) {
        fail("null destroy changed the counter");
    }
    eu4_gfx_destroy_vertex_buffer(header);
    if (*counter() != 0 || eu4_gfx_vertex_records() != 0) {
        fail("destroy");
    }
    uint8_t again[4] = {9, 8, 7, 6};
    header = eu4_gfx_create_vertex_buffer(NULL, again, 1, 4, 0);
    if (eu4_gfx_vertex_buffer_read(header, got, 4) != 0 || memcmp(got, again, 4) != 0) {
        fail("recreate");
    }
    eu4_gfx_destroy_vertex_buffer(header);

    void *guard = mmap(NULL, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (guard == MAP_FAILED) {
        fail("guard");
    }
    header = eu4_gfx_create_vertex_buffer(NULL, guard, 0, 4, 0);
    expect_header(header, 4, 0);
    if (eu4_gfx_vertex_buffer_length(header) != 0 || !eu4_gfx_vertex_buffer_shared(header)) {
        fail("zero length");
    }
    void *particle = eu4_gfx_create_vertex_buffer(NULL, NULL, 6, 1, 1);
    expect_header(particle, 1, 6);
    if (eu4_gfx_vertex_buffer_length(particle) != 6) {
        fail("null create");
    }
    void *empty = eu4_gfx_create_vertex_buffer(NULL, guard, 3, 0, 0);
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_update_vertex_buffer(&g_master, empty, guard, 1, 0, 1);
    if (((uint8_t *)g_master)[0x170] != 1 || eu4_gfx_vertex_buffer_length(empty) != 0) {
        fail("zero subrange");
    }
    if (*counter() != 3) {
        fail("counter after creates");
    }
    eu4_gfx_destroy_vertex_buffer(header);
    eu4_gfx_destroy_vertex_buffer(particle);
    eu4_gfx_destroy_vertex_buffer(empty);
    if (*counter() != 0) {
        fail("counter after cleanup");
    }

    void *grown[8];
    for (int i = 0; i < 8; i++) {
        uint8_t byte = (uint8_t)(0x40 + i);
        grown[i] = eu4_gfx_create_vertex_buffer(NULL, &byte, 1, 1, 0);
        uint8_t back = 0;
        if (eu4_gfx_vertex_buffer_read(grown[i], &back, 1) != 0 || back != byte) {
            fail("growth bytes");
        }
    }
    if (eu4_gfx_vertex_records() != 8 || eu4_gfx_vertex_gpus() != 8 || *counter() != 8) {
        fail("growth count");
    }
    for (int i = 0; i < 8; i++) {
        eu4_gfx_destroy_vertex_buffer(grown[i]);
    }
    if (*counter() != 0 || eu4_gfx_vertex_records() != 0) {
        fail("growth destroy");
    }

    uint8_t mark[4] = {0xab, 0xcd, 0xef, 0x10};
    header = eu4_gfx_create_vertex_buffer(NULL, mark, 4, 1, 0);
    int32_t saved_count = 0;
    memcpy(&saved_count, (uint8_t *)header + 4, 4);
    eu4_gfx_shutdown(g_master);
    int32_t after = 0;
    memcpy(&after, (uint8_t *)header + 4, 4);
    if (after != saved_count || eu4_gfx_vertex_records() != 1 || eu4_gfx_vertex_gpus() != 0 ||
        *counter() != 1) {
        fail("shutdown release");
    }
    g_master = NULL;
    eu4_gfx_destroy_vertex_buffer(header);
    if (*counter() != 0 || eu4_gfx_vertex_records() != 0) {
        fail("destroy after shutdown");
    }
    char later_receipt[1280];
    read_create(later_receipt, sizeof later_receipt);
    if (strcmp(first_receipt, later_receipt) != 0) {
        fail("create overwritten");
    }
}

static void expect_index_header(void *header, int32_t count) {
    int32_t got = -1;
    uint32_t name = 0;
    memcpy(&got, header, 4);
    memcpy(&name, (uint8_t *)header + 4, 4);
    if (got != count || name == 0) {
        fail("index header");
    }
}

static void arm_index(const void *fn, int records, int gpus, int32_t count) {
    arm(fn, 0, 0, 0);
    g_expect_index_records = records;
    g_expect_index_gpus = gpus;
    g_expect_ib = count;
}

__attribute__((noinline)) static void run_ib_size(void) {
    boot(1);
    arm_index((const void *)run_ib_size, 0, 0, 0);
    eu4_gfx_create_index_buffer(NULL, NULL, -1);
    fail("negative index count returned");
}

__attribute__((noinline)) static void run_ib_overflow(void) {
    boot(1);
    arm_index((const void *)run_ib_overflow, 0, 0, 0);
    eu4_gfx_create_index_buffer(NULL, NULL, 0x40000000);
    fail("overflow index count returned");
}

__attribute__((noinline)) static void run_ib_metal(void) {
    boot(1);
    arm_index((const void *)run_ib_metal, 0, 0, 0);
    eu4_gfx_buffer_test_fail_metal(1);
    eu4_gfx_create_index_buffer(NULL, NULL, 4);
    fail("index metal failure returned");
}

__attribute__((noinline)) static void run_ib_double(void) {
    boot(1);
    uint16_t index = 1;
    void *header = eu4_gfx_create_index_buffer(NULL, &index, 1);
    eu4_gfx_destroy_index_buffer(header);
    if (*ib_counter() != 0 || eu4_gfx_index_records() != 0) {
        fail("index first destroy");
    }
    arm_index((const void *)run_ib_double, 0, 0, 0);
    eu4_gfx_destroy_index_buffer(header);
    fail("index second destroy returned");
}

__attribute__((noinline)) static void run_ib_released(void) {
    boot(1);
    uint16_t index = 1;
    void *header = eu4_gfx_create_index_buffer(NULL, &index, 1);
    if (header == NULL || *ib_counter() != 1) {
        fail("index shutdown setup");
    }
    eu4_gfx_shutdown(g_master);
    if (eu4_gfx_index_records() != 1 || eu4_gfx_index_gpus() != 0 || *ib_counter() != 1) {
        fail("index shutdown dropped the header");
    }
    arm_index((const void *)run_ib_released, 1, 0, 1);
    eu4_gfx_update_index_buffer(NULL, header, &index, 1);
    fail("index update after shutdown returned");
}

__attribute__((noinline)) static void *wrong_index_create(void *unused) {
    (void)unused;
    eu4_gfx_create_index_buffer(NULL, NULL, 1);
    return NULL;
}

__attribute__((noinline)) static void run_ib_thread(void) {
    boot(1);
    arm_index((const void *)wrong_index_create, 0, 0, 0);
    pthread_t thread;
    if (pthread_create(&thread, NULL, wrong_index_create, NULL) != 0) {
        fail("pthread");
    }
    pthread_join(thread, NULL);
    fail("index wrong thread returned");
}

__attribute__((noinline)) static void run_ib_bound_metal(void) {
    boot(1);
    uint16_t index = 0x5a;
    void *header = eu4_gfx_create_index_buffer(NULL, &index, 1);
    if (header == NULL) {
        fail("bound metal setup");
    }
    eu4_gfx_set_index_buffer(&g_master, header);
    memcpy(g_kept_index_bytes, &index, 2);
    g_kept_index = header;
    arm_index((const void *)run_ib_bound_metal, 1, 1, 1);
    eu4_gfx_buffer_test_fail_metal(1);
    eu4_gfx_update_index_buffer(NULL, header, &index, 4);
    fail("bound metal update returned");
}

__attribute__((noinline)) static void run_ib_unknown(void) {
    boot(1);
    uint8_t header[8];
    memset(header, 0, sizeof header);
    arm_index((const void *)run_ib_unknown, 0, 0, 0);
    eu4_gfx_update_index_buffer(NULL, header, header, 1);
    fail("unknown zero header returned");
}

__attribute__((noinline)) static void run_ib_released_set(void) {
    boot(1);
    uint16_t index = 1;
    void *header = eu4_gfx_create_index_buffer(NULL, &index, 1);
    if (header == NULL || *ib_counter() != 1) {
        fail("released set setup");
    }
    int32_t sentinel = 7;
    memcpy((uint8_t *)g_master + 0x68, &sentinel, 4);
    eu4_gfx_release_vertex_buffers(eu4_gfx_backend_token(), __builtin_return_address(0));
    if (eu4_gfx_index_records() != 1 || eu4_gfx_index_gpus() != 0) {
        fail("released set did not drop the buffer");
    }
    arm_index((const void *)run_ib_released_set, 1, 0, 1);
    g_check_master_count = 1;
    g_expect_master_count = 7;
    eu4_gfx_set_index_buffer(&g_master, header);
    fail("released set returned");
}

__attribute__((noinline)) static void run_ib_missing(void) {
    boot(1);
    arm_index((const void *)run_ib_missing, 0, 0, 0);
    g_check_master_count = 1;
    g_expect_master_count = 7;
    int32_t sentinel = 7;
    memcpy((uint8_t *)g_master + 0x68, &sentinel, 4);
    eu4_gfx_set_index_buffer(&g_master, (void *)0x8);
    fail("index set missing returned");
}

__attribute__((noinline)) static void vertex_set_success(void) {
    /* Engine writes: 0x15ec103, 0x15ec0b7, 0x15ec0c7, 0x15ec0d3, 0x15ec0dc,
       0x15ec0eb, 0x15ec0f0, 0x15ec098. The disable call at 0x15ebf9d is deferred:
       only its dword argument is kept. */
    boot(0);
    uint8_t shader[0x40];
    memset(shader, 0, sizeof shader);
    void *base = shader;
    int32_t attribs = 1;
    int32_t attrib = 7;
    memcpy(shader + 0x0c, &attrib, 4);
    memcpy(shader + 0x10, &base, sizeof base);
    memcpy(shader + 0x18, &attribs, 4);
    void *shader_ptr = shader;
    memcpy((uint8_t *)g_master + 0x70, &shader_ptr, sizeof shader_ptr);
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_set_vertex_buffers(&g_master, 0, NULL, 0, NULL, NULL);
    int32_t got_attrib = 0;
    if (((uint8_t *)g_master)[0x170] != 1 || eu4_gfx_disabled_vertex_attrib_count() != 1 ||
        eu4_gfx_disabled_vertex_attrib(0, &got_attrib) != 0 || got_attrib != 7) {
        fail("vertex count zero");
    }

    uint8_t initial[16];
    memset(initial, 0x11, sizeof initial);
    void *header = eu4_gfx_create_vertex_buffer(NULL, initial, 4, 4, 0);
    if (header == NULL) {
        fail("vertex create");
    }
    uint32_t token = 0;
    memcpy(&token, (uint8_t *)header + 8, 4);
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_set_vertex_buffers(&g_master, 1, &header, 0, NULL, NULL);
    void *selected = NULL;
    memcpy(&selected, (uint8_t *)g_master + 0x130, sizeof selected);
    if (token != 1 || selected != header || ((uint8_t *)g_master)[0x170] != 1 ||
        eu4_gfx_vertex_slot_header(0) != header) {
        fail("vertex fresh set");
    }

    uint8_t blank[12];
    memset(blank, 0, sizeof blank);
    void *blank_ptr = blank;
    eu4_gfx_set_vertex_buffers(&g_master, 1, &blank_ptr, 0, NULL, NULL);
    void *after_blank = NULL;
    memcpy(&after_blank, (uint8_t *)g_master + 0x130, sizeof after_blank);
    if (after_blank != header || eu4_gfx_vertex_slot_header(0) != header) {
        fail("vertex zero token");
    }

    void *pair[2] = {header, header};
    eu4_gfx_set_vertex_buffers(&g_master, 2, pair, 0, NULL, NULL);
    void *slot0 = NULL;
    void *slot1 = NULL;
    int32_t off0 = -1, word0 = -1, at64 = -1, at6c = -1;
    memcpy(&slot0, (uint8_t *)g_master + 0x130, sizeof slot0);
    memcpy(&slot1, (uint8_t *)g_master + 0x138, sizeof slot1);
    memcpy(&off0, (uint8_t *)g_master + 0x150, 4);
    memcpy(&word0, (uint8_t *)g_master + 0x160, 4);
    memcpy(&at64, (uint8_t *)g_master + 0x64, 4);
    memcpy(&at6c, (uint8_t *)g_master + 0x6c, 4);
    if (slot0 != header || slot1 != header || off0 != 0 || word0 != 4 || at64 != 4 || at6c != 4) {
        fail("vertex engine slots");
    }

    int32_t keep64 = 0x55;
    memcpy((uint8_t *)g_master + 0x64, &keep64, 4);
    int32_t offsets[1] = {9};
    int32_t words[1] = {3};
    eu4_gfx_set_vertex_buffers(&g_master, 1, &header, 1, offsets, words);
    int32_t got64 = 0, off1 = 0, word1 = 0;
    void *slot1b = NULL;
    memcpy(&got64, (uint8_t *)g_master + 0x64, 4);
    memcpy(&off1, (uint8_t *)g_master + 0x154, 4);
    memcpy(&word1, (uint8_t *)g_master + 0x164, 4);
    memcpy(&slot1b, (uint8_t *)g_master + 0x138, sizeof slot1b);
    if (got64 != keep64 || off1 != 9 || word1 != 3 || slot1b != header) {
        fail("vertex slot one");
    }

    void *nulls[1] = {NULL};
    if (g_image[EU4_GFX_VB_ERROR_RVA] != 0) {
        fail("vertex flag preset");
    }
    eu4_gfx_set_vertex_buffers(&g_master, 1, nulls, 2, NULL, NULL);
    void *slot2 = NULL;
    memcpy(&slot2, (uint8_t *)g_master + 0x140, sizeof slot2);
    if (g_image[EU4_GFX_VB_ERROR_RVA] != 1 || slot2 != NULL) {
        fail("vertex null header");
    }

    uint8_t raw[12];
    memset(raw, 0, sizeof raw);
    int32_t stride = 4;
    int32_t count = 4;
    uint32_t named = 1;
    memcpy(raw, &stride, 4);
    memcpy(raw + 4, &count, 4);
    memcpy(raw + 8, &named, 4);
    void *raw_ptr = raw;
    eu4_gfx_set_vertex_buffers(&g_master, 1, &raw_ptr, 3, NULL, NULL);
    void *slot3 = NULL;
    memcpy(&slot3, (uint8_t *)g_master + 0x148, sizeof slot3);
    if (slot3 != raw) {
        fail("vertex unknown master");
    }

    /* Metal selection is the header, resolved on read. Release and destroy clear
       that header and do not write the master. Safety policy in the research note. */
    uint8_t metal[16];
    void *same_buffer = eu4_gfx_vertex_slot_buffer(0);
    if (eu4_gfx_vertex_slot_header(0) != header || eu4_gfx_vertex_slot_header(1) != header ||
        same_buffer == NULL || eu4_gfx_vertex_slot_buffer(1) != same_buffer ||
        eu4_gfx_vertex_slot_read(0, metal, 16) != 0 || eu4_gfx_vertex_slot_read(1, metal, 16) != 0 ||
        memcmp(metal, initial, 16) != 0 || eu4_gfx_vertex_slot_header(3) != raw ||
        eu4_gfx_vertex_slot_buffer(3) != NULL || eu4_gfx_vertex_slot_read(3, metal, 16) == 0) {
        fail("vertex metal select");
    }
    uint8_t inplace[16];
    memset(inplace, 0x22, sizeof inplace);
    eu4_gfx_update_vertex_buffer(&g_master, header, inplace, 4, 0, 0);
    uint8_t metal0[16];
    uint8_t metal1[16];
    void *still = NULL;
    memcpy(&still, (uint8_t *)g_master + 0x130, sizeof still);
    if (still != header || eu4_gfx_vertex_slot_header(0) != header ||
        eu4_gfx_vertex_slot_header(1) != header || eu4_gfx_vertex_slot_buffer(0) != same_buffer ||
        eu4_gfx_vertex_slot_read(0, metal0, 16) != 0 || eu4_gfx_vertex_slot_read(1, metal1, 16) != 0 ||
        memcmp(metal0, inplace, 16) != 0 || memcmp(metal1, inplace, 16) != 0) {
        fail("vertex in place");
    }
    uint8_t replaced[8];
    memset(replaced, 0x33, sizeof replaced);
    eu4_gfx_update_vertex_buffer(&g_master, header, replaced, 2, 0, 0);
    uint8_t metal_new[8];
    void *fresh_buffer = eu4_gfx_vertex_slot_buffer(0);
    if (eu4_gfx_vertex_slot_header(0) != header || eu4_gfx_vertex_slot_header(1) != header ||
        fresh_buffer == NULL || fresh_buffer == same_buffer || eu4_gfx_vertex_slot_buffer(1) != fresh_buffer ||
        eu4_gfx_vertex_slot_read(0, metal_new, 8) != 0 || eu4_gfx_vertex_slot_read(1, metal_new, 8) != 0 ||
        memcmp(metal_new, replaced, 8) != 0 || eu4_gfx_vertex_slot_read(0, metal0, 16) == 0) {
        fail("vertex replaced");
    }

    uint8_t other_bytes[16];
    memset(other_bytes, 0x44, sizeof other_bytes);
    void *other = eu4_gfx_create_vertex_buffer(NULL, other_bytes, 4, 4, 0);
    if (other == NULL) {
        fail("vertex second create");
    }
    eu4_gfx_set_vertex_buffers(&g_master, 1, &other, 2, NULL, NULL);
    void *saved = NULL;
    memcpy(&saved, (uint8_t *)g_master + 0x130, sizeof saved);
    uint8_t master_copy[0x58];
    memcpy(master_copy, (uint8_t *)g_master + 0x130, sizeof master_copy);
    eu4_gfx_destroy_vertex_buffer(header);
    void *after_destroy = NULL;
    uint8_t kept[16];
    memcpy(&after_destroy, (uint8_t *)g_master + 0x130, sizeof after_destroy);
    if (after_destroy != saved || memcmp(master_copy, (uint8_t *)g_master + 0x130, sizeof master_copy) != 0 ||
        eu4_gfx_vertex_slot_header(0) != NULL || eu4_gfx_vertex_slot_header(1) != NULL ||
        eu4_gfx_vertex_slot_header(2) != other || eu4_gfx_vertex_slot_header(3) != raw ||
        eu4_gfx_vertex_slot_read(2, kept, 16) != 0 || memcmp(kept, other_bytes, 16) != 0 ||
        eu4_gfx_vertex_records() != 1) {
        fail("vertex destroy");
    }
    eu4_gfx_release_vertex_buffers(eu4_gfx_backend_token(), (const void *)vertex_set_success);
    uint32_t released_token = 0;
    memcpy(&released_token, (uint8_t *)other + 8, 4);
    if (memcmp(master_copy, (uint8_t *)g_master + 0x130, sizeof master_copy) != 0 ||
        released_token != 1 || eu4_gfx_vertex_slot_header(2) != NULL ||
        eu4_gfx_vertex_slot_header(3) != NULL || eu4_gfx_vertex_slot_buffer(2) != NULL ||
        eu4_gfx_disabled_vertex_attrib_count() != 0 || eu4_gfx_vertex_gpus() != 0 ||
        eu4_gfx_vertex_records() != 1) {
        fail("vertex release");
    }
    eu4_gfx_destroy_vertex_buffer(other);
    if (eu4_gfx_vertex_records() != 0 || eu4_gfx_vertex_slot_header(2) != NULL) {
        fail("vertex second destroy");
    }
    eu4_gfx_shutdown(g_master);
    g_master = NULL;
    boot(1);
    for (int slot = 0; slot < EU4_GFX_VERTEX_SLOTS; slot++) {
        if (eu4_gfx_vertex_slot_header(slot) != NULL || eu4_gfx_vertex_slot_buffer(slot) != NULL) {
            fail("vertex reinit");
        }
    }
    if (eu4_gfx_disabled_vertex_attrib_count() != 0) {
        fail("vertex reinit attrib");
    }
}

/* count 2 reads base+0x34, then base+0x0c. The record is [9, 4]. */
static void install_two_attribs(uint8_t *shader) {
    memset(shader, 0, 0x40);
    void *base = shader;
    int32_t count = 2;
    int32_t low = 4;
    int32_t high = 9;
    memcpy(shader + 0x0c, &low, 4);
    memcpy(shader + 0x34, &high, 4);
    memcpy(shader + 0x10, &base, sizeof base);
    memcpy(shader + 0x18, &count, 4);
    void *shader_ptr = shader;
    memcpy((uint8_t *)g_master + 0x70, &shader_ptr, sizeof shader_ptr);
}

static void clear_shader_pointer(void) {
    void *no_shader = NULL;
    memcpy((uint8_t *)g_master + 0x70, &no_shader, sizeof no_shader);
}

static void empty_disabled_attribs(void) {
    clear_shader_pointer();
    eu4_gfx_set_vertex_buffers(&g_master, 0, NULL, 0, NULL, NULL);
    ((uint8_t *)g_master)[0x170] = 0;
}

static int attribs_are(int32_t first, int32_t second) {
    int32_t got0 = 0;
    int32_t got1 = 0;
    return eu4_gfx_disabled_vertex_attrib_count() == 2 &&
        eu4_gfx_disabled_vertex_attrib(0, &got0) == 0 && got0 == first &&
        eu4_gfx_disabled_vertex_attrib(1, &got1) == 0 && got1 == second;
}

/* case:multiple-range, case:multiple-unchanged, case:multiple-lifecycle. The
   case:multiple-error paths run in the child modes below. */
__attribute__((noinline)) static void multiple_success(void) {
    if (g_master == NULL) {
        boot(1);
    }
    void *no_shader = NULL;
    memcpy((uint8_t *)g_master + 0x70, &no_shader, sizeof no_shader);

    /* case:multiple-range: two ranges start at byte 0 and at stride*2. */
    uint8_t initial[32];
    memset(initial, 0x11, sizeof initial);
    void *header = eu4_gfx_create_vertex_buffer(NULL, initial, 8, 4, 0);
    if (header == NULL || *counter() != 1) {
        fail("case:multiple-range setup");
    }
    eu4_gfx_set_vertex_buffers(&g_master, 1, &header, 0, NULL, NULL);
    void *same = eu4_gfx_vertex_slot_buffer(0);
    uint8_t first[8];
    uint8_t second[4];
    memset(first, 0x22, sizeof first);
    memset(second, 0x33, sizeof second);
    const void *parts[2] = {first, second};
    int32_t counts[2] = {2, 1};
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 2);
    uint8_t got[32];
    if (same == NULL || eu4_gfx_vertex_slot_buffer(0) != same ||
        eu4_gfx_vertex_buffer_length(header) != 32 ||
        eu4_gfx_vertex_buffer_read(header, got, 32) != 0 ||
        memcmp(got, first, 8) != 0 || memcmp(got + 8, second, 4) != 0 ||
        memcmp(got + 12, initial + 12, 20) != 0 ||
        ((uint8_t *)g_master)[0x170] != 1) {
        fail("case:multiple-range");
    }

    /* case:multiple-attribs: a populated shader records [9, 4], sets the dirty
       byte, and copies a payload the earlier range test did not write. */
    uint8_t shader[0x40];
    uint8_t attrib_first[8];
    uint8_t attrib_second[4];
    memset(attrib_first, 0x99, sizeof attrib_first);
    memset(attrib_second, 0xaa, sizeof attrib_second);
    const void *attrib_parts[2] = {attrib_first, attrib_second};
    int32_t attrib_counts[2] = {2, 1};
    empty_disabled_attribs();
    if (eu4_gfx_disabled_vertex_attrib_count() != 0) {
        fail("case:multiple-attribs clear");
    }
    install_two_attribs(shader);
    ((uint8_t *)g_master)[0x170] = 0;
    eu4_gfx_update_vertex_buffer_multiple(
        &g_master, header, attrib_parts, attrib_counts, 2);
    uint8_t attrib_got[32];
    if (eu4_gfx_vertex_buffer_read(header, attrib_got, 32) != 0 ||
        memcmp(attrib_got, attrib_first, 8) != 0 ||
        memcmp(attrib_got + 8, attrib_second, 4) != 0 ||
        memcmp(attrib_got + 12, initial + 12, 20) != 0 ||
        ((uint8_t *)g_master)[0x170] != 1 || !attribs_are(9, 4)) {
        fail("case:multiple-attribs");
    }

    /* case:multiple-zero-token: a zero presence token returns before every effect. */
    uint32_t saved_token = 0;
    memcpy(&saved_token, (uint8_t *)header + 8, 4);
    uint32_t zero_token = 0;
    memcpy((uint8_t *)header + 8, &zero_token, 4);
    ((uint8_t *)g_master)[0x170] = 0;
    uint8_t before_token[32];
    uint8_t after_token[32];
    uint8_t differ[8];
    memset(differ, 0xab, sizeof differ);
    const void *differ_parts[1] = {differ};
    int32_t differ_counts[1] = {2};
    if (eu4_gfx_vertex_buffer_read(header, before_token, 32) != 0) {
        fail("case:multiple-zero-token read");
    }
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, differ_parts, differ_counts, 1);
    if (eu4_gfx_vertex_buffer_read(header, after_token, 32) != 0 ||
        memcmp(before_token, after_token, 32) != 0 ||
        ((uint8_t *)g_master)[0x170] != 0 || !attribs_are(9, 4)) {
        memcpy((uint8_t *)header + 8, &saved_token, 4);
        fail("case:multiple-zero-token");
    }
    memcpy((uint8_t *)header + 8, &saved_token, 4);

    /* case:multiple-zero-range and a negative count skip the copy and still record attributes. */
    int32_t quiet_ranges[2] = {0, -1};
    for (int quiet = 0; quiet < 2; quiet++) {
        uint8_t before_quiet[32];
        uint8_t after_quiet[32];
        empty_disabled_attribs();
        if (eu4_gfx_disabled_vertex_attrib_count() != 0 || ((uint8_t *)g_master)[0x170] != 0) {
            fail("case:multiple-zero-range clear");
        }
        install_two_attribs(shader);
        if (eu4_gfx_vertex_buffer_read(header, before_quiet, 32) != 0) {
            fail("case:multiple-zero-range read");
        }
        eu4_gfx_update_vertex_buffer_multiple(
            &g_master, header, NULL, NULL, quiet_ranges[quiet]);
        if (eu4_gfx_vertex_buffer_read(header, after_quiet, 32) != 0 ||
            memcmp(before_quiet, after_quiet, 32) != 0 ||
            ((uint8_t *)g_master)[0x170] != 1 || !attribs_are(9, 4)) {
            fail(quiet == 0 ? "case:multiple-zero-range" : "case:multiple-negative-range");
        }
    }
    clear_shader_pointer();

    /* case:multiple-unchanged: a zero-size range does not advance the offset and
       the bytes past the ranges stay as they were. */
    uint8_t keep[16];
    memset(keep, 0x44, sizeof keep);
    void *unchanged = eu4_gfx_create_vertex_buffer(NULL, keep, 4, 4, 0);
    if (unchanged == NULL || *counter() != 2) {
        fail("case:multiple-unchanged setup");
    }
    uint8_t patch_a[4];
    uint8_t patch_b[4];
    memset(patch_a, 0x55, sizeof patch_a);
    memset(patch_b, 0x66, sizeof patch_b);
    const void *three[3] = {patch_a, NULL, patch_b};
    int32_t three_counts[3] = {1, 0, 1};
    eu4_gfx_update_vertex_buffer_multiple(&g_master, unchanged, three, three_counts, 3);
    uint8_t keep_got[16];
    if (eu4_gfx_vertex_buffer_read(unchanged, keep_got, 16) != 0 ||
        memcmp(keep_got, patch_a, 4) != 0 || memcmp(keep_got + 4, patch_b, 4) != 0 ||
        memcmp(keep_got + 8, keep + 8, 8) != 0) {
        fail("case:multiple-unchanged");
    }

    /* case:multiple-lifecycle: update keeps the buffer, release drops only the
       buffer, and destroy removes the header and counter. */
    uint8_t life_bytes[16];
    memset(life_bytes, 0x77, sizeof life_bytes);
    void *life = eu4_gfx_create_vertex_buffer(NULL, life_bytes, 4, 4, 0);
    if (life == NULL || *counter() != 3) {
        fail("case:multiple-lifecycle setup");
    }
    uint8_t life_patch[8];
    memset(life_patch, 0x88, sizeof life_patch);
    const void *life_parts[1] = {life_patch};
    int32_t life_counts[1] = {2};
    eu4_gfx_update_vertex_buffer_multiple(&g_master, life, life_parts, life_counts, 1);
    if (eu4_gfx_vertex_buffer_read(life, got, 16) != 0 || memcmp(got, life_patch, 8) != 0 ||
        memcmp(got + 8, life_bytes + 8, 8) != 0) {
        fail("case:multiple-lifecycle update");
    }
    eu4_gfx_release_vertex_buffers(eu4_gfx_backend_token(), (const void *)multiple_success);
    if (eu4_gfx_vertex_records() != 3 || eu4_gfx_vertex_gpus() != 0 || *counter() != 3 ||
        eu4_gfx_vertex_slot_buffer(0) != NULL) {
        fail("case:multiple-lifecycle release");
    }
    eu4_gfx_destroy_vertex_buffer(life);
    eu4_gfx_destroy_vertex_buffer(unchanged);
    eu4_gfx_destroy_vertex_buffer(header);
    if (*counter() != 0 || eu4_gfx_vertex_records() != 0) {
        fail("case:multiple-lifecycle destroy");
    }
    eu4_gfx_shutdown(g_master);
    g_master = NULL;
}

__attribute__((noinline)) static void run_vb_range(void) {
    /* Safety policy in the research note: a negative count stops with range before any store. */
    boot(1);
    arm((const void *)run_vb_range, 0, 0, 0);
    eu4_gfx_set_vertex_buffers(&g_master, -1, NULL, 0, NULL, NULL);
    fail("vertex negative count returned");
}

__attribute__((noinline)) static void run_vb_slot4(void) {
    /* Slot 4's pointer store would land on the offset array at +0x150. */
    boot(1);
    uint8_t bytes[4] = {1, 2, 3, 4};
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 1, 4, 0);
    if (header == NULL) {
        fail("slot4 setup");
    }
    memcpy(g_master_span, (uint8_t *)g_master + 0x130, sizeof g_master_span);
    g_check_master_span = 1;
    arm((const void *)run_vb_slot4, 1, 1, 1);
    eu4_gfx_set_vertex_buffers(&g_master, 1, &header, 4, NULL, NULL);
    fail("vertex slot 4 returned");
}

__attribute__((noinline)) static void run_vb_attrib(void) {
    /* Safety policy in the research note: an attribute count above 32 stops with attrib before any store. */
    boot(1);
    uint8_t shader[0x20];
    memset(shader, 0, sizeof shader);
    int32_t attribs = 33;
    void *base = shader;
    memcpy(shader + 0x10, &base, sizeof base);
    memcpy(shader + 0x18, &attribs, 4);
    void *shader_ptr = shader;
    memcpy((uint8_t *)g_master + 0x70, &shader_ptr, sizeof shader_ptr);
    arm((const void *)run_vb_attrib, 0, 0, 0);
    eu4_gfx_set_vertex_buffers(&g_master, 0, NULL, 0, NULL, NULL);
    fail("vertex attrib cap returned");
}

__attribute__((noinline)) static void *wrong_vertex_set(void *unused) {
    (void)unused;
    eu4_gfx_set_vertex_buffers(&g_master, 0, NULL, 0, NULL, NULL);
    return NULL;
}

__attribute__((noinline)) static void run_vb_thread(void) {
    /* Safety policy in the research note: a thread other than the _GfxInit thread stops with thread. */
    boot(1);
    arm((const void *)wrong_vertex_set, 0, 0, 0);
    pthread_t thread;
    if (pthread_create(&thread, NULL, wrong_vertex_set, NULL) != 0) {
        fail("pthread");
    }
    pthread_join(thread, NULL);
    fail("vertex wrong thread returned");
}

__attribute__((noinline)) static void run_multiple_missing(void) {
    /* case:multiple-error: a nonzero token outside the registry stops missing. */
    boot(1);
    uint8_t raw[12];
    memset(raw, 0, sizeof raw);
    int32_t stride = 4, count = 1;
    uint32_t token = 1;
    memcpy(raw, &stride, 4);
    memcpy(raw + 4, &count, 4);
    memcpy(raw + 8, &token, 4);
    uint8_t byte = 0x11;
    const void *parts[1] = {&byte};
    int32_t counts[1] = {1};
    arm((const void *)run_multiple_missing, 0, 0, 0);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, raw, parts, counts, 1);
    fail("case:multiple-error missing returned");
}

__attribute__((noinline)) static void run_multiple_range(void) {
    /* case:multiple-error: a positive range count needs both arrays. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error range setup");
    }
    arm((const void *)run_multiple_range, 1, 1, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, NULL, NULL, 1);
    fail("case:multiple-error range returned");
}

__attribute__((noinline)) static void run_multiple_size(void) {
    /* case:multiple-error: stride * count above 0x7fffffff stops size. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error size setup");
    }
    uint8_t patch = 0x22;
    const void *parts[1] = {&patch};
    int32_t counts[1] = {0x40000000};
    g_kept = header;
    g_kept_byte = 0x11;
    g_check_master_flag = 1;
    g_expect_master_flag = 0;
    ((uint8_t *)g_master)[0x170] = 0;
    arm((const void *)run_multiple_size, 1, 1, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 1);
    fail("case:multiple-error size returned");
}

__attribute__((noinline)) static void run_multiple_bounds(void) {
    /* case:multiple-error: a range past the registered length stops bounds before
       changing the buffer or the master flag. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error bounds setup");
    }
    uint8_t patch[16];
    memset(patch, 0x22, sizeof patch);
    const void *parts[1] = {patch};
    int32_t counts[1] = {4};
    g_kept = header;
    g_kept_byte = 0x11;
    g_check_master_flag = 1;
    g_expect_master_flag = 0;
    ((uint8_t *)g_master)[0x170] = 0;
    arm((const void *)run_multiple_bounds, 1, 1, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 1);
    fail("case:multiple-error bounds returned");
}

__attribute__((noinline)) static void run_multiple_null_data(void) {
    /* case:multiple-error: a nonzero range needs a data pointer. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error null-data setup");
    }
    const void *parts[1] = {NULL};
    int32_t counts[1] = {1};
    g_kept = header;
    g_kept_byte = 0x11;
    g_check_master_flag = 1;
    g_expect_master_flag = 0;
    ((uint8_t *)g_master)[0x170] = 0;
    arm((const void *)run_multiple_null_data, 1, 1, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 1);
    fail("case:multiple-error null-data returned");
}

__attribute__((noinline)) static void run_multiple_released(void) {
    /* case:multiple-error: a released entry stops before touching the master. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error released setup");
    }
    eu4_gfx_release_vertex_buffers(eu4_gfx_backend_token(), __builtin_return_address(0));
    if (eu4_gfx_vertex_records() != 1 || eu4_gfx_vertex_gpus() != 0) {
        fail("case:multiple-error released drop");
    }
    uint8_t patch = 0x22;
    const void *parts[1] = {&patch};
    int32_t counts[1] = {1};
    g_check_master_flag = 1;
    g_expect_master_flag = 0;
    ((uint8_t *)g_master)[0x170] = 0;
    arm((const void *)run_multiple_released, 1, 0, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 1);
    fail("case:multiple-error released returned");
}

__attribute__((noinline)) static void run_multiple_attrib(void) {
    /* case:multiple-error: an attribute count above 32 stops before the copy. */
    boot(1);
    uint8_t bytes[8];
    memset(bytes, 0x11, sizeof bytes);
    void *header = eu4_gfx_create_vertex_buffer(NULL, bytes, 2, 4, 0);
    if (header == NULL) {
        fail("case:multiple-error attrib setup");
    }
    uint8_t shader[0x20];
    memset(shader, 0, sizeof shader);
    int32_t attribs = 33;
    void *base = shader;
    memcpy(shader + 0x10, &base, sizeof base);
    memcpy(shader + 0x18, &attribs, 4);
    void *shader_ptr = shader;
    memcpy((uint8_t *)g_master + 0x70, &shader_ptr, sizeof shader_ptr);
    uint8_t patch = 0x22;
    const void *parts[1] = {&patch};
    int32_t counts[1] = {1};
    g_kept = header;
    g_kept_byte = 0x11;
    g_check_master_flag = 1;
    g_expect_master_flag = 0;
    ((uint8_t *)g_master)[0x170] = 0;
    arm((const void *)run_multiple_attrib, 1, 1, 1);
    eu4_gfx_update_vertex_buffer_multiple(&g_master, header, parts, counts, 1);
    fail("case:multiple-error attrib returned");
}

static void *g_multiple_header;

__attribute__((noinline)) static void *wrong_multiple(void *unused) {
    (void)unused;
    uint8_t patch = 0x22;
    const void *parts[1] = {&patch};
    int32_t counts[1] = {1};
    eu4_gfx_update_vertex_buffer_multiple(&g_master, g_multiple_header, parts, counts, 1);
    return NULL;
}

__attribute__((noinline)) static void run_multiple_thread(void) {
    /* case:multiple-error: another thread stops with thread before any effect. */
    boot(1);
    uint8_t bytes[4] = {0x11, 0x11, 0x11, 0x11};
    g_multiple_header = eu4_gfx_create_vertex_buffer(NULL, bytes, 1, 4, 0);
    if (g_multiple_header == NULL) {
        fail("case:multiple-error thread setup");
    }
    arm((const void *)wrong_multiple, 1, 1, 1);
    pthread_t thread;
    if (pthread_create(&thread, NULL, wrong_multiple, NULL) != 0) {
        fail("pthread");
    }
    pthread_join(thread, NULL);
    fail("case:multiple-error thread returned");
}

static void read_named(const char *name, char *body, size_t cap) {
    char path[96];
    snprintf(path, sizeof path, "%s/%s", g_parent_dir, name);
    FILE *file = fopen(path, "r");
    if (file == NULL || fgets(body, (int)cap, file) == NULL) {
        fail(name);
    }
    fclose(file);
}

__attribute__((noinline)) static void index_success(void);

static void expect_index_caller(const char *body) {
    char symbol_bit[] = "\"symbol\":\"_GfxCreateIndexBuffer\"";
    char success_bit[] = "\"success\":1";
    char pid_bit[32];
    snprintf(pid_bit, sizeof pid_bit, "\"pid\":%d", (int)getpid());
    const char *address_bit = strstr(body, "\"address\":\"0x");
    unsigned long long address = 0;
    if (strstr(body, symbol_bit) == NULL || strstr(body, success_bit) == NULL ||
        strstr(body, pid_bit) == NULL || strstr(body, "\"resource\":\"0x") == NULL ||
        address_bit == NULL || sscanf(address_bit, "\"address\":\"0x%llx\"", &address) != 1) {
        fail("index create fields");
    }
    Dl_info info;
    memset(&info, 0, sizeof info);
    if (dladdr((void *)(uintptr_t)address, &info) == 0 || info.dli_saddr != (void *)index_success) {
        fail("index create caller");
    }
}

__attribute__((noinline)) static void index_success(void) {
    boot(1);
    if (*ib_counter() != 0 || eu4_gfx_index_records() != 0) {
        fail("index counter did not start at zero");
    }
    uint16_t initial[4] = {1, 2, 3, 4};
    void *header = eu4_gfx_create_index_buffer((void *)0x1, initial, 4);
    char first_receipt[1280];
    read_named("index_create.json", first_receipt, sizeof first_receipt);
    expect_index_caller(first_receipt);
    expect_index_header(header, 4);
    uint16_t got[4];
    if (*ib_counter() != 1 || eu4_gfx_index_records() != 1 || eu4_gfx_index_gpus() != 1 ||
        !eu4_gfx_index_buffer_shared(header) || eu4_gfx_index_buffer_length(header) != 8 ||
        eu4_gfx_index_buffer_read(header, got, 8) != 0 || memcmp(got, initial, 8) != 0) {
        fail("index create bytes");
    }
    uint16_t same[4] = {5, 6, 7, 8};
    eu4_gfx_update_index_buffer(NULL, header, same, 4);
    expect_index_header(header, 4);
    if (eu4_gfx_index_buffer_length(header) != 8 || eu4_gfx_index_buffer_read(header, got, 8) != 0 ||
        memcmp(got, same, 8) != 0) {
        fail("index equal replace");
    }
    uint16_t stored[4] = {9, 10, 11, 12};
    eu4_gfx_update_index_buffer(NULL, header, stored, 0);
    expect_index_header(header, 4);
    if (eu4_gfx_index_buffer_read(header, got, 8) != 0 || memcmp(got, stored, 8) != 0) {
        fail("index stored count");
    }
    uint16_t shorter[2] = {13, 14};
    eu4_gfx_update_index_buffer(NULL, header, shorter, 2);
    expect_index_header(header, 4);
    if (eu4_gfx_index_buffer_length(header) != 4 || eu4_gfx_index_buffer_read(header, got, 4) != 0 ||
        memcmp(got, shorter, 4) != 0) {
        fail("index shorter replace");
    }
    int32_t written = 0;
    memcpy((uint8_t *)g_master + 0x68, &written, 4);
    eu4_gfx_set_index_buffer(&g_master, header);
    memcpy(&written, (uint8_t *)g_master + 0x68, 4);
    if (written != 4 || eu4_gfx_current_index_header() != header) {
        fail("index bind");
    }
    uint16_t rebound[4] = {40, 41, 42, 43};
    eu4_gfx_update_index_buffer(NULL, header, rebound, 4);
    if (eu4_gfx_current_index_header() != header || eu4_gfx_index_buffer_length(header) != 8 ||
        eu4_gfx_index_buffer_read(header, got, 8) != 0 || memcmp(got, rebound, 8) != 0 ||
        eu4_gfx_current_index_read(got, 8) != 0 || memcmp(got, rebound, 8) != 0 ||
        eu4_gfx_current_index_read(NULL, 8) == 0) {
        fail("index bound replace");
    }
    written = 4;
    eu4_gfx_set_index_buffer(&g_master, NULL);
    memcpy(&written, (uint8_t *)g_master + 0x68, 4);
    if (written != 4 || eu4_gfx_current_index_header() != header) {
        fail("index null bind");
    }
    uint32_t name = 0;
    memcpy(&name, (uint8_t *)header + 4, 4);
    uint32_t zero = 0;
    memcpy((uint8_t *)header + 4, &zero, 4);
    uint16_t ignored[2] = {15, 16};
    eu4_gfx_update_index_buffer(NULL, header, ignored, 2);
    if (eu4_gfx_index_buffer_length(header) != 8 || eu4_gfx_index_buffer_read(header, got, 8) != 0 ||
        memcmp(got, rebound, 8) != 0) {
        fail("index zero name update");
    }
    memcpy((uint8_t *)header + 4, &name, 4);
    eu4_gfx_destroy_index_buffer(NULL);
    if (*ib_counter() != 1 || eu4_gfx_index_records() != 1) {
        fail("index null destroy");
    }
    eu4_gfx_destroy_index_buffer(header);
    int32_t top = 0;
    void *cached = NULL;
    memcpy(&top, g_image + kIndexCacheTopRva, 4);
    memcpy(&cached, g_image + kIndexCacheRva, sizeof cached);
    if (*ib_counter() != 0 || eu4_gfx_index_records() != 0 || eu4_gfx_index_gpus() != 0 ||
        eu4_gfx_current_index_header() != NULL || top != 1 || cached != header) {
        fail("index destroy recycle");
    }
    uint16_t again[4] = {21, 22, 23, 24};
    void *recycled = eu4_gfx_create_index_buffer(NULL, again, 4);
    if (recycled != header || eu4_gfx_index_buffer_read(recycled, got, 8) != 0 ||
        memcmp(got, again, 8) != 0 || *ib_counter() != 1) {
        fail("index recycle");
    }
    void *guard = mmap(NULL, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (guard == MAP_FAILED) {
        fail("index guard");
    }
    void *empty = eu4_gfx_create_index_buffer(NULL, guard, 0);
    expect_index_header(empty, 0);
    if (eu4_gfx_index_buffer_length(empty) != 0 || !eu4_gfx_index_buffer_shared(empty)) {
        fail("index zero length");
    }
    eu4_gfx_set_index_buffer(&g_master, empty);
    if (eu4_gfx_current_index_header() != empty || eu4_gfx_current_index_read(got, 0) != 0 ||
        eu4_gfx_current_index_read(NULL, 0) != 0 || eu4_gfx_current_index_read(got, 1) == 0) {
        fail("index empty binding");
    }
    void *blank = eu4_gfx_create_index_buffer(NULL, NULL, 3);
    expect_index_header(blank, 3);
    if (eu4_gfx_index_buffer_length(blank) != 6 || !eu4_gfx_index_buffer_shared(blank) ||
        *ib_counter() != 3) {
        fail("index null data");
    }
    char later_receipt[1280];
    read_named("index_create.json", later_receipt, sizeof later_receipt);
    if (strcmp(first_receipt, later_receipt) != 0) {
        fail("index create overwritten");
    }
    int32_t saved = 0;
    memcpy(&saved, recycled, 4);
    eu4_gfx_shutdown(g_master);
    int32_t after = 0;
    memcpy(&after, recycled, 4);
    if (after != saved || eu4_gfx_index_records() != 3 || eu4_gfx_index_gpus() != 0 ||
        *ib_counter() != 3 || eu4_gfx_current_index_header() != NULL) {
        fail("index shutdown release");
    }
    g_master = NULL;
    uint8_t ok = 0;
    uint8_t handle[0x20];
    memset(handle, 0, sizeof handle);
    uint32_t window_id = 7, source = 9;
    memcpy(handle, &window_id, 4);
    memcpy(handle + 0x18, &source, 4);
    g_master = eu4_gfx_init(handle, &ok);
    if (ok != 1 || g_master == NULL) {
        fail("index reinit");
    }
    uint16_t fresh_bytes[1] = {30};
    void *fresh = eu4_gfx_create_index_buffer(NULL, fresh_bytes, 1);
    if (fresh == NULL || eu4_gfx_index_buffer_read(fresh, got, 2) != 0 || got[0] != 30 ||
        eu4_gfx_index_records() != 4 || *ib_counter() != 4) {
        fail("index create after reinit");
    }
    memcpy(&after, recycled, 4);
    if (after != saved || eu4_gfx_index_buffer_length(recycled) != 8 ||
        eu4_gfx_index_buffer_read(recycled, got, 8) == 0) {
        fail("index retained header");
    }
    eu4_gfx_destroy_index_buffer(recycled);
    eu4_gfx_destroy_index_buffer(empty);
    eu4_gfx_destroy_index_buffer(blank);
    eu4_gfx_destroy_index_buffer(fresh);
    if (*ib_counter() != 0 || eu4_gfx_index_records() != 0) {
        fail("index cleanup");
    }
}

int main(int argc, char **argv) {
    g_argv0 = argv[0];
    if (argc == 3) {
        eu4_metal_events_set_dir(argv[2]);
        eu4_gfx_set_terminate(child_stop);
        if (strcmp(argv[1], "fail-metal") == 0) {
            run_fail_metal();
        } else if (strcmp(argv[1], "fail-metal-throw") == 0) {
            run_fail_metal_throw();
        } else if (strcmp(argv[1], "fail-entry") == 0) {
            run_fail_entry();
        } else if (strcmp(argv[1], "fail-insert") == 0) {
            run_fail_insert();
        } else if (strcmp(argv[1], "fail-header") == 0) {
            run_fail_header();
        } else if (strcmp(argv[1], "bad-size") == 0) {
            run_bad_size();
        } else if (strcmp(argv[1], "bounds") == 0) {
            run_bounds();
        } else if (strcmp(argv[1], "null-data") == 0) {
            run_null_data();
        } else if (strcmp(argv[1], "after-shutdown") == 0) {
            run_after_shutdown();
        } else if (strcmp(argv[1], "double-destroy") == 0) {
            run_double_destroy();
        } else if (strcmp(argv[1], "no-backend") == 0) {
            run_no_backend();
        } else if (strcmp(argv[1], "wrong-thread") == 0) {
            run_wrong_thread();
        } else if (strcmp(argv[1], "wrong-shutdown") == 0) {
            run_wrong_shutdown();
        } else if (strcmp(argv[1], "ib-size") == 0) {
            run_ib_size();
        } else if (strcmp(argv[1], "ib-overflow") == 0) {
            run_ib_overflow();
        } else if (strcmp(argv[1], "ib-metal") == 0) {
            run_ib_metal();
        } else if (strcmp(argv[1], "ib-double") == 0) {
            run_ib_double();
        } else if (strcmp(argv[1], "ib-released") == 0) {
            run_ib_released();
        } else if (strcmp(argv[1], "ib-thread") == 0) {
            run_ib_thread();
        } else if (strcmp(argv[1], "ib-missing") == 0) {
            run_ib_missing();
        } else if (strcmp(argv[1], "ib-bound-metal") == 0) {
            run_ib_bound_metal();
        } else if (strcmp(argv[1], "ib-unknown") == 0) {
            run_ib_unknown();
        } else if (strcmp(argv[1], "ib-released-set") == 0) {
            run_ib_released_set();
        } else if (strcmp(argv[1], "vb-range") == 0) {
            run_vb_range();
        } else if (strcmp(argv[1], "vb-slot4") == 0) {
            run_vb_slot4();
        } else if (strcmp(argv[1], "vb-attrib") == 0) {
            run_vb_attrib();
        } else if (strcmp(argv[1], "vb-thread") == 0) {
            run_vb_thread();
        } else if (strcmp(argv[1], "multiple-missing") == 0) {
            run_multiple_missing();
        } else if (strcmp(argv[1], "multiple-range") == 0) {
            run_multiple_range();
        } else if (strcmp(argv[1], "multiple-size") == 0) {
            run_multiple_size();
        } else if (strcmp(argv[1], "multiple-bounds") == 0) {
            run_multiple_bounds();
        } else if (strcmp(argv[1], "multiple-null-data") == 0) {
            run_multiple_null_data();
        } else if (strcmp(argv[1], "multiple-released") == 0) {
            run_multiple_released();
        } else if (strcmp(argv[1], "multiple-attrib") == 0) {
            run_multiple_attrib();
        } else if (strcmp(argv[1], "multiple-thread") == 0) {
            run_multiple_thread();
        }
        fail("unknown child");
    }
    char dir[] = "/tmp/eu4-vb-parent-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("parent dir");
    }
    eu4_metal_events_set_dir(dir);
    snprintf(g_parent_dir, sizeof g_parent_dir, "%s", dir);
    eu4_gfx_set_terminate(parent_stop);
    success();
    index_success();
    vertex_set_success();
    multiple_success();
    expect_child("fail-metal", "_GfxCreateVertexBuffer", "metal", "after_init");
    expect_child("fail-metal-throw", "_GfxUpdateVertexBuffer", "metal", "after_init");
    expect_child("fail-entry", "_GfxCreateVertexBuffer", "entry", "after_init");
    expect_child("fail-insert", "_GfxCreateVertexBuffer", "insert", "after_init");
    expect_child("fail-header", "_GfxCreateVertexBuffer", "header", "after_init");
    expect_child("bad-size", "_GfxCreateVertexBuffer", "size", "after_init");
    expect_child("bounds", "_GfxUpdateVertexBuffer", "bounds", "after_init");
    expect_child("null-data", "_GfxUpdateVertexBuffer", "null_data", "after_init");
    expect_child("after-shutdown", "_GfxUpdateVertexBuffer", "released", "after_init");
    expect_child("double-destroy", "_GfxDestroyVertexBuffer", "missing", "after_init");
    expect_child("no-backend", "_GfxCreateVertexBuffer", "no_backend", "before_init");
    expect_child("wrong-thread", "_GfxCreateVertexBuffer", "thread", "after_init");
    expect_child("wrong-shutdown", "_GfxShutdown", "thread", "after_init");
    expect_child("ib-size", "_GfxCreateIndexBuffer", "size", "after_init");
    expect_child("ib-overflow", "_GfxCreateIndexBuffer", "size", "after_init");
    expect_child("ib-metal", "_GfxCreateIndexBuffer", "metal", "after_init");
    expect_child("ib-double", "_GfxDestroyIndexBuffer", "missing", "after_init");
    expect_child("ib-released", "_GfxUpdateIndexBuffer", "released", "after_init");
    expect_child("ib-thread", "_GfxCreateIndexBuffer", "thread", "after_init");
    expect_child("ib-missing", "_GfxSetIndexBuffer", "missing", "after_init");
    expect_child("ib-bound-metal", "_GfxUpdateIndexBuffer", "metal", "after_init");
    expect_child("ib-unknown", "_GfxUpdateIndexBuffer", "missing", "after_init");
    expect_child("ib-released-set", "_GfxSetIndexBuffer", "released", "after_init");
    expect_child("vb-range", "_GfxSetVertexBuffers", "range", "after_init");
    expect_child("vb-slot4", "_GfxSetVertexBuffers", "range", "after_init");
    expect_child("vb-attrib", "_GfxSetVertexBuffers", "attrib", "after_init");
    expect_child("vb-thread", "_GfxSetVertexBuffers", "thread", "after_init");
    expect_child("multiple-missing", "_GfxUpdateVertexBufferMultiple", "missing", "after_init");
    expect_child("multiple-range", "_GfxUpdateVertexBufferMultiple", "range", "after_init");
    expect_child("multiple-size", "_GfxUpdateVertexBufferMultiple", "size", "after_init");
    expect_child("multiple-bounds", "_GfxUpdateVertexBufferMultiple", "bounds", "after_init");
    expect_child("multiple-null-data", "_GfxUpdateVertexBufferMultiple", "null_data", "after_init");
    expect_child("multiple-released", "_GfxUpdateVertexBufferMultiple", "released", "after_init");
    expect_child("multiple-attrib", "_GfxUpdateVertexBufferMultiple", "attrib", "after_init");
    expect_child("multiple-thread", "_GfxUpdateVertexBufferMultiple", "thread", "after_init");
    return 0;
}
