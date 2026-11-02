#import "../benchmark/eu4_gfx_buffers.h"
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

static int32_t *counter(void) { return (int32_t *)(g_image + EU4_GFX_VB_COUNT_RVA); }

static void *fake_window(uint32_t window_id) { return window_id == 7 ? (void *)0x11 : NULL; }

static void fake_size(void *window, int *width, int *height) {
    (void)window;
    *width = 800;
    *height = 600;
}

static void write_depth(void *dest) { eu4_gfx_write_default_depth_stencil((uint8_t *)dest); }

static void boot(int do_init) {
    if (g_image == NULL) {
        size_t bytes = EU4_GFX_VB_COUNT_RVA + sizeof(int32_t);
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
        g_image == NULL || *counter() != g_expect_counter) {
        _exit(3);
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
}

__attribute__((noinline)) static void run_fail_metal(void) {
    boot(1);
    arm((const void *)run_fail_metal, 0, 0, 0);
    eu4_gfx_buffer_test_fail_metal(1);
    eu4_gfx_create_vertex_buffer(NULL, NULL, 4, 4, 0);
    fail("metal failure returned");
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

static void expect_child(const char *mode, const char *symbol, const char *reason) {
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
    snprintf(symbol_bit, sizeof symbol_bit, "\"symbol\":\"%s\"", symbol);
    snprintf(reason_bit, sizeof reason_bit, "\"reason\":\"%s\"", reason);
    if (strstr(body, symbol_bit) == NULL || strstr(body, reason_bit) == NULL) {
        fail("stop fields");
    }
}

static void success(void) {
    boot(1);
    if (*counter() != 0) {
        fail("counter did not start at zero");
    }
    uint8_t initial[16];
    memset(initial, 0x11, sizeof initial);
    void *header = eu4_gfx_create_vertex_buffer((void *)0x1, initial, 4, 4, 0);
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
}

int main(int argc, char **argv) {
    g_argv0 = argv[0];
    if (argc == 3) {
        eu4_metal_events_set_dir(argv[2]);
        eu4_gfx_set_terminate(child_stop);
        if (strcmp(argv[1], "fail-metal") == 0) {
            run_fail_metal();
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
        }
        fail("unknown child");
    }
    char dir[] = "/tmp/eu4-vb-parent-XXXXXX";
    if (mkdtemp(dir) == NULL) {
        fail("parent dir");
    }
    eu4_metal_events_set_dir(dir);
    eu4_gfx_set_terminate(parent_stop);
    success();
    expect_child("fail-metal", "_GfxCreateVertexBuffer", "metal");
    expect_child("fail-insert", "_GfxCreateVertexBuffer", "insert");
    expect_child("fail-header", "_GfxCreateVertexBuffer", "header");
    expect_child("bad-size", "_GfxCreateVertexBuffer", "size");
    expect_child("bounds", "_GfxUpdateVertexBuffer", "bounds");
    expect_child("null-data", "_GfxUpdateVertexBuffer", "null_data");
    expect_child("after-shutdown", "_GfxUpdateVertexBuffer", "released");
    expect_child("double-destroy", "_GfxDestroyVertexBuffer", "missing");
    expect_child("no-backend", "_GfxCreateVertexBuffer", "no_backend");
    expect_child("wrong-thread", "_GfxCreateVertexBuffer", "thread");
    expect_child("wrong-shutdown", "_GfxShutdown", "thread");
    return 0;
}
