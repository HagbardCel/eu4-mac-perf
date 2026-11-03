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
static int g_expect_index_records;
static int g_expect_index_gpus;
static int32_t g_expect_ib;
static int g_check_master_count;
static int32_t g_expect_master_count;

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
    uint32_t name = 1;
    memcpy(&got_stride, header, 4);
    memcpy(&got_count, (uint8_t *)header + 4, 4);
    memcpy(&name, (uint8_t *)header + 8, 4);
    if (got_stride != stride || got_count != count || name != 0) {
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
    if (g_kept != NULL) {
        uint8_t back = 0;
        if (eu4_gfx_vertex_buffer_read(g_kept, &back, 1) != 0 || back != g_kept_byte) {
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
    if (eu4_gfx_index_buffer_read(header, got, 4) != 0 || memcmp(got, shorter, 4) != 0) {
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
    return 0;
}
