#include "eu4_gfx_startup_trace.h"

#include "eu4_gfx_observe.h"
#include "eu4_gfx_observe_buffers.h"
#include "eu4_gfx_observe_draw.h"
#include "eu4_gfx_observe_viewport.h"
#include "eu4_gfx_site_patch.h"

#include <mach-o/dyld.h>
#include <mach-o/getsect.h>
#include <mach-o/loader.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

extern void gfx_startup_stub_settings(void);
extern void gfx_startup_stub_init(void);
extern void gfx_startup_stub_init_fail(void);
extern uint8_t gfx_startup_stub_master[];
extern void gfx_startup_stub_viewport(void);
extern void gfx_startup_stub_scalar(void);
extern void gfx_startup_stub_vb_create(void);
extern void gfx_startup_stub_vb_update(void);
extern void gfx_startup_stub_vb_bind(void);
extern uint8_t gfx_startup_stub_vb_header[];
extern void gfx_startup_stub_draw(void);
extern void gfx_startup_stub_draw_caller(void);
extern void gfx_startup_stub_draw_caller_return(void);
extern void gfx_startup_stub_primitive(void);
extern int gfx_startup_stub_draw_calls;
extern int gfx_startup_stub_primitive_calls;
extern int gfx_startup_stub_primitive_value;

static const uint8_t kExpected[EU4_GFX_CAPTURE_PATCH_SIZE] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54, 0x53,
};

typedef int (*settings_fn)(int, int, int, void *);
typedef void *(*init_fn)(void *, uint8_t *);

static int fail(const char *msg) {
    fprintf(stderr, "%s\n", msg);
    return 1;
}

static void fill_master(void) {
    memset(gfx_startup_stub_master, 0, 0x198);
    int32_t primitive = 4;
    int32_t width = 800;
    int32_t height = 600;
    int32_t source = 9;
    int32_t max_texture = 8192;
    memcpy(gfx_startup_stub_master + 8, &primitive, 4);
    memcpy(gfx_startup_stub_master + 0xa0, &width, 4);
    memcpy(gfx_startup_stub_master + 0xa4, &height, 4);
    memcpy(gfx_startup_stub_master + 0xa8, &source, 4);
    memcpy(gfx_startup_stub_master + 0x188, &max_texture, 4);
    void *token = (void *)0x11;
    memcpy(gfx_startup_stub_master + 0x80, &token, sizeof(token));
    memcpy(gfx_startup_stub_master + 0x88, &token, sizeof(token));
    memcpy(gfx_startup_stub_master + 0x90, &token, sizeof(token));
    memcpy(gfx_startup_stub_master + 0x180, &token, sizeof(token));
}

static int arm_and_install(void *settings, void *init) {
    eu4_gfx_startup_test_reset();
    fill_master();
    eu4_gfx_startup_arm();
    if (!eu4_gfx_startup_install(settings, init, kExpected, kExpected)) {
        return fail("install");
    }
    return 0;
}

static void make_settings(uint8_t settings[0x28], uint8_t *records, int count) {
    memset(settings, 0, 0x28);
    memset(records, 0, (size_t)count * 12);
    for (int i = 0; i < count; i++) {
        uint32_t sample = (uint32_t)(i + 2);
        memcpy(records + (size_t)i * 12, &sample, 4);
    }
    void *array = records;
    memcpy(settings + 8, &array, sizeof(array));
    settings[0x10] = 1;
    uint32_t anisotropy = 0;
    uint64_t span = 0x30000000300ull;
    uint32_t constant = 0x400;
    memcpy(settings + 0x14, &anisotropy, 4);
    memcpy(settings + 0x18, &span, 8);
    memcpy(settings + 0x20, &constant, 4);
}

static int scenario_basic(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    int rc = ((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    if (rc != 7 || records[4] != 1) {
        return fail("settings body");
    }
    uint8_t window[32] = {0};
    uint32_t window_id = 7, source = 9;
    memcpy(window, &window_id, 4);
    memcpy(window + 0x18, &source, 4);
    uint8_t success = 0;
    void *master = ((init_fn)gfx_startup_stub_init)(window, &success);
    if (master != gfx_startup_stub_master || success != 1) {
        return fail("init body");
    }
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("not sealed");
    }
    if (!eu4_gfx_startup_finalize(dir, "host-basic", "test", getpid(), 0)) {
        return fail("finalize");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (!snap.complete || snap.qualifying_init != 1 || snap.settings_calls != 1 ||
        snap.event_count != 4 || !snap.events_copied) {
        return fail("snapshot incomplete");
    }
    if (snap.events[0].sample_captured != 1 || snap.events[0].sample_values[0] != 2) {
        return fail("sample values");
    }
    return 0;
}

static int scenario_fail_init(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init_fail) != 0) {
        return 1;
    }
    uint8_t window[32] = {0};
    uint8_t success = 1;
    (void)((init_fn)gfx_startup_stub_init_fail)(window, &success);
    eu4_gfx_startup_request_drain();
    if (!eu4_gfx_startup_wait_sealed(500)) {
        return fail("fail-init seal");
    }
    if (!eu4_gfx_startup_finalize(dir, "host-fail", "test", getpid(), 0)) {
        return fail("finalize fail");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.complete || snap.qualifying_init != 0) {
        return fail("fail init should not qualify");
    }
    return 0;
}

static int scenario_drain_refuse(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("drain wait");
    }
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    if (!eu4_gfx_startup_finalize(dir, "host-drain", "test", getpid(), 0)) {
        return fail("finalize drain");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.unmatched != 0 || snap.refused == 0 || !snap.complete) {
        return fail("drain refuse");
    }
    return 0;
}

typedef struct {
    uint8_t settings[0x28];
    uint8_t records[12];
} pause_args;

static void *paused_settings_thread(void *arg) {
    pause_args *a = arg;
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, a->settings);
    return NULL;
}

static int scenario_pause_drain(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    pause_args args;
    make_settings(args.settings, args.records, 1);
    eu4_gfx_startup_test_pause_after_admit(1);
    pthread_t thread;
    if (pthread_create(&thread, NULL, paused_settings_thread, &args) != 0) {
        return fail("pthread");
    }
    eu4_gfx_startup_test_wait_admit_paused(2000);
    if (eu4_gfx_startup_in_flight() < 1) {
        return fail("expected in flight");
    }
    eu4_gfx_startup_request_drain();
    if (eu4_gfx_startup_state() != EU4_GFX_STARTUP_DRAINING) {
        return fail("expected draining");
    }
    if (eu4_gfx_startup_in_flight() < 1) {
        return fail("still in flight while paused");
    }
    eu4_gfx_startup_test_release_admit_pause();
    (void)pthread_join(thread, NULL);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    /* After settings exit, still draining; init is refused. Force seal via
       empty in-flight after settings completed, then we need a qualifying init
       before drain for complete=true. Do a fresh reset path: request already
       draining. Call init while draining is refused. Seal when in_flight 0. */
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("pause seal");
    }
    if (!eu4_gfx_startup_finalize(dir, "host-pause", "test", getpid(), 0)) {
        return fail("finalize pause");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.state != EU4_GFX_STARTUP_SEALED || snap.settings_calls != 1 ||
        snap.unmatched != 0) {
        return fail("pause snapshot");
    }
    /* No qualifying init in this scenario: incomplete is expected. */
    if (snap.complete) {
        return fail("pause should not be complete without init");
    }
    (void)window;
    (void)success;
    return 0;
}

static int scenario_tls_full(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    eu4_gfx_startup_test_force_tls_full(1);
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    if (eu4_gfx_startup_in_flight() != 0) {
        return fail("tls full left in flight");
    }
    eu4_gfx_startup_request_drain();
    if (!eu4_gfx_startup_wait_sealed(500)) {
        return fail("tls seal");
    }
    if (!eu4_gfx_startup_finalize(dir, "host-tls", "test", getpid(), 0)) {
        return fail("finalize tls");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.unmatched != 0 || snap.refused == 0 || snap.in_flight != 0) {
        return fail("tls overflow accounting");
    }
    return 0;
}

static int scenario_null_settings(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 0, NULL);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    (void)eu4_gfx_startup_wait_sealed(2000);
    if (!eu4_gfx_startup_finalize(dir, "host-null", "test", getpid(), 0)) {
        return fail("finalize null");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.complete || snap.unavailable == 0) {
        return fail("null settings should be incomplete");
    }
    return 0;
}

static int scenario_oversize(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 65, settings);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    (void)eu4_gfx_startup_wait_sealed(2000);
    if (!eu4_gfx_startup_finalize(dir, "host-oversize", "test", getpid(), 0)) {
        return fail("finalize oversize");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.complete) {
        return fail("oversize complete");
    }
    int found = 0;
    for (uint32_t i = 0; i < snap.event_count; i++) {
        if (snap.events[i].site == EU4_GFX_STARTUP_SITE_SETTINGS &&
            snap.events[i].phase == 0) {
            if (snap.events[i].sample_count != 65 ||
                snap.events[i].sample_captured != 0) {
                return fail("oversize counts");
            }
            found = 1;
        }
    }
    if (!found) {
        return fail("missing oversize entry");
    }
    return 0;
}

typedef struct {
    const char *dir;
    int ok;
} finalize_args;

static void *finalize_thread(void *arg) {
    finalize_args *a = arg;
    a->ok = eu4_gfx_startup_finalize(a->dir, "host-dual", "test", getpid(), 0) ? 1
                                                                               : 0;
    return NULL;
}

static int scenario_dual_finalize(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("dual seal");
    }
    finalize_args a = {.dir = dir, .ok = 0};
    finalize_args b = {.dir = dir, .ok = 0};
    pthread_t t1, t2;
    if (pthread_create(&t1, NULL, finalize_thread, &a) != 0 ||
        pthread_create(&t2, NULL, finalize_thread, &b) != 0) {
        return fail("dual pthread");
    }
    (void)pthread_join(t1, NULL);
    (void)pthread_join(t2, NULL);
    if (!a.ok || !b.ok) {
        return fail("dual finalize result");
    }
    return 0;
}

static int scenario_unsealed_finalize(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    /* Still ARMED: finalize without seal. */
    if (!eu4_gfx_startup_finalize(dir, "host-unsealed", "test", getpid(), 0)) {
        return fail("unsealed finalize write");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.events_copied || snap.complete ||
        strcmp(snap.reason, "drain_timeout") != 0) {
        return fail("unsealed diagnostic");
    }
    return 0;
}

typedef struct {
    uint8_t settings[0x28];
    uint8_t records[12];
} parallel_args;

static void *parallel_settings_thread(void *arg) {
    parallel_args *a = arg;
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, a->settings);
    return NULL;
}

static int scenario_parallel_seq(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    parallel_args a, b;
    make_settings(a.settings, a.records, 1);
    make_settings(b.settings, b.records, 1);
    pthread_t t1, t2;
    if (pthread_create(&t1, NULL, parallel_settings_thread, &a) != 0 ||
        pthread_create(&t2, NULL, parallel_settings_thread, &b) != 0) {
        return fail("parallel pthread");
    }
    (void)pthread_join(t1, NULL);
    (void)pthread_join(t2, NULL);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("parallel seal");
    }
    if (!eu4_gfx_startup_finalize(dir, "host-parallel", "test", getpid(), 0)) {
        return fail("finalize parallel");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (!snap.events_copied || snap.event_count < 4) {
        return fail("parallel events");
    }
    for (uint32_t i = 0; i < snap.event_count; i++) {
        if (snap.events[i].seq != (uint64_t)i + 1) {
            return fail("parallel seq");
        }
    }
    return 0;
}

static int scenario_write_fail(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("write-fail seal");
    }
    eu4_gfx_startup_test_fail_next_writes(1);
    if (eu4_gfx_startup_finalize(dir, "host-write-fail", "test", getpid(), 0)) {
        return fail("expected finalize failure");
    }
    /* No complete summary should be published. */
    char path[1024];
    snprintf(path, sizeof(path), "%s/summary.json", dir);
    FILE *file = fopen(path, "r");
    if (file != NULL) {
        fclose(file);
        return fail("summary should not exist");
    }
    return 0;
}

static int scenario_bad_bytes(void) {
    eu4_gfx_startup_test_reset();
    eu4_gfx_startup_arm();
    uint8_t wrong[EU4_GFX_CAPTURE_PATCH_SIZE];
    memset(wrong, 0x90, sizeof(wrong));
    if (eu4_gfx_startup_install((void *)gfx_startup_stub_settings,
                               (void *)gfx_startup_stub_init, wrong, kExpected)) {
        return fail("bad bytes installed");
    }
    return 0;
}

static int scenario_unsafe_read(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    eu4_gfx_startup_test_force_unsafe_read(1);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    eu4_gfx_startup_request_drain();
    (void)eu4_gfx_startup_wait_sealed(500);
    if (!eu4_gfx_startup_finalize(dir, "host-unsafe", "test", getpid(), 0)) {
        return fail("finalize unsafe");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.complete || snap.unavailable == 0) {
        return fail("unsafe should be incomplete");
    }
    return 0;
}


static const uint8_t kViewportPrefix[8] = {
    0x55, 0x48, 0x89, 0xe5, 0xf3, 0x0f, 0x6f, 0x06,
};
static const uint8_t kScalarPrefix[5] = {
    0x55, 0x48, 0x89, 0xe5, 0x53,
};

static void observe_text_range(uintptr_t *lo, uintptr_t *hi) {
    const struct segment_command_64 *text = getsegbyname("__TEXT");
    intptr_t slide = _dyld_get_image_vmaddr_slide(0);
    *lo = (uintptr_t)(text->vmaddr + slide);
    *hi = *lo + (uintptr_t)text->vmsize;
}

static void scalar_entry(eu4_gfx_capture_frame *frame,
                         eu4_gfx_observe_call *call) {
    uint32_t value = (uint32_t)frame->rdi;
    eu4_gfx_observe_set_input(call, &value, sizeof(value));
}

static void scalar_exit(eu4_gfx_capture_frame *frame,
                        eu4_gfx_observe_call *call,
                        eu4_gfx_observe_record *record) {
    (void)record;
    eu4_gfx_observe_set_return(call, (int64_t)frame->rax);
}

static const eu4_gfx_observe_spec kViewportSpec = {
    "_GfxSetViewPort",
    (uint64_t)(uintptr_t)gfx_startup_stub_viewport,
    kViewportPrefix,
    sizeof(kViewportPrefix),
    EU4_GFX_OBSERVE_RELOC_SAFE,
    eu4_gfx_observe_viewport_entry,
    eu4_gfx_observe_viewport_exit,
};

static const eu4_gfx_observe_spec kScalarSpec = {
    "_GfxObserveScalar",
    (uint64_t)(uintptr_t)gfx_startup_stub_scalar,
    kScalarPrefix,
    sizeof(kScalarPrefix),
    EU4_GFX_OBSERVE_RELOC_SAFE,
    scalar_entry,
    scalar_exit,
};

static int arm_observe(const eu4_gfx_observe_spec *specs, size_t count) {
    eu4_gfx_observe_test_reset();
    uintptr_t lo = 0;
    uintptr_t hi = 0;
    observe_text_range(&lo, &hi);
    if (!eu4_gfx_observe_install_specs(specs, count, 0, lo, hi)) {
        return fail("observe install");
    }
    return 0;
}

static int run_startup(void) {
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    int rc = ((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    if (rc != 7 || records[4] != 1) {
        return fail("settings body");
    }
    uint8_t window[32] = {0};
    uint32_t window_id = 7, source = 9;
    memcpy(window, &window_id, 4);
    memcpy(window + 0x18, &source, 4);
    uint8_t success = 0;
    void *master = ((init_fn)gfx_startup_stub_init)(window, &success);
    if (master != gfx_startup_stub_master || success != 1) {
        return fail("init body");
    }
    return 0;
}

static void call_viewport(const int32_t rect[6]) {
    void (*fn)(void *, const void *) =
        (void (*)(void *, const void *))gfx_startup_stub_viewport;
    fn(gfx_startup_stub_master, rect);
}

static int call_scalar(int value) {
    int (*fn)(int) = (int (*)(int))gfx_startup_stub_scalar;
    return fn(value);
}

static int finalize_observe(const char *dir, const char *name) {
    if (!eu4_gfx_startup_finalize(dir, name, "test", getpid(), 0)) {
        return fail("observe finalize");
    }
    return 0;
}

static int finish_observe(const char *dir, const char *name) {
    eu4_gfx_startup_request_drain();
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("observe not sealed");
    }
    return finalize_observe(dir, name);
}

static long file_size(const char *dir, const char *name) {
    char path[1024];
    if (snprintf(path, sizeof(path), "%s/%s", dir, name) >= (int)sizeof(path)) {
        return -1;
    }
    struct stat st;
    if (stat(path, &st) != 0) {
        return -1;
    }
    return (long)st.st_size;
}

static const uint8_t kBufferPrefix[6] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57,
};

static const eu4_gfx_observe_spec kBufferSpecs[3] = {
    {"_GfxCreateVertexBuffer",
     (uint64_t)(uintptr_t)gfx_startup_stub_vb_create,
     kBufferPrefix,
     sizeof(kBufferPrefix),
     EU4_GFX_OBSERVE_RELOC_SAFE,
     eu4_gfx_observe_vb_create_entry,
     eu4_gfx_observe_vb_create_exit},
    {"_GfxUpdateVertexBuffer",
     (uint64_t)(uintptr_t)gfx_startup_stub_vb_update,
     kBufferPrefix,
     sizeof(kBufferPrefix),
     EU4_GFX_OBSERVE_RELOC_SAFE,
     eu4_gfx_observe_vb_update_entry,
     NULL},
    {"_GfxSetVertexBuffers",
     (uint64_t)(uintptr_t)gfx_startup_stub_vb_bind,
     kBufferPrefix,
     sizeof(kBufferPrefix),
     EU4_GFX_OBSERVE_RELOC_SAFE,
     eu4_gfx_observe_vb_bind_entry,
     NULL},
};

static int scenario_observe_bind_explicit(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0 ||
        arm_observe(kBufferSpecs, 3) != 0 || run_startup() != 0) {
        return 1;
    }
    uint8_t data[96];
    for (size_t i = 0; i < sizeof(data); i++) {
        data[i] = (uint8_t)i;
    }
    int32_t stride = 24, count = 4;
    memcpy(gfx_startup_stub_vb_header, &stride, 4);
    memcpy(gfx_startup_stub_vb_header + 4, &count, 4);
    void *(*create)(void *, const void *, int32_t, int32_t, int32_t) =
        (void *(*)(void *, const void *, int32_t, int32_t, int32_t))
            gfx_startup_stub_vb_create;
    void (*bind)(void *, int32_t, void **, int32_t, const int32_t *,
                 const int32_t *) =
        (void (*)(void *, int32_t, void **, int32_t, const int32_t *,
                  const int32_t *))gfx_startup_stub_vb_bind;
    void *header = create(NULL, data, 4, 24, 0);
    int32_t offsets[1] = {0};
    int32_t words[1] = {0};
    void *headers[1] = {header};
    bind(NULL, 1, headers, 0, offsets, words);
    if (finish_observe(dir, "host-bind-explicit") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 2 ||
        snap.records[1].kind != EU4_GFX_OBSERVE_KIND_VERTEX_BIND ||
        strstr(snap.records[1].meta, "\"words_null\":false") == NULL ||
        strstr(snap.records[1].meta, "\"offsets_null\":false") == NULL) {
        return fail("observe bind explicit");
    }
    return 0;
}

static int scenario_observe_unknown_update(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0 ||
        arm_observe(kBufferSpecs, 3) != 0 || run_startup() != 0) {
        return 1;
    }
    uint8_t full[16];
    uint8_t partial[8];
    for (size_t i = 0; i < sizeof(full); i++) {
        full[i] = (uint8_t)(0x10u + i);
    }
    for (size_t i = 0; i < sizeof(partial); i++) {
        partial[i] = (uint8_t)(0x40u + i);
    }
    int32_t stride = 8, count = 2;
    memcpy(gfx_startup_stub_vb_header, &stride, 4);
    memcpy(gfx_startup_stub_vb_header + 4, &count, 4);
    void *(*create)(void *, const void *, int32_t, int32_t, int32_t) =
        (void *(*)(void *, const void *, int32_t, int32_t, int32_t))
            gfx_startup_stub_vb_create;
    void (*update)(void *, void *, const void *, int32_t, uint32_t, int32_t) =
        (void (*)(void *, void *, const void *, int32_t, uint32_t, int32_t))
            gfx_startup_stub_vb_update;
    void *header = create(NULL, NULL, 2, 8, 0);
    update(NULL, header, full, 2, 0, 0);
    void *header2 = create(NULL, NULL, 2, 8, 0);
    update(NULL, header2, partial, 1, 0, 1);
    if (finish_observe(dir, "host-unknown-update") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 4) {
        return fail("observe unknown update");
    }
    if (snap.records[0].contents_known || snap.records[1].kind !=
            EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE || !snap.records[1].contents_known ||
        strcmp(snap.records[0].resource_id, "vb_1") != 0 ||
        strcmp(snap.records[1].resource_id, "vb_1") != 0) {
        return fail("observe unknown update first");
    }
    if (snap.records[2].contents_known ||
        strcmp(snap.records[2].resource_id, "vb_2") != 0 ||
        snap.records[3].kind != EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE ||
        snap.records[3].contents_known ||
        strcmp(snap.records[3].resource_id, "vb_2") != 0) {
        return fail("observe unknown update second");
    }
    return 0;
}

static int scenario_observe_zero_length(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0 ||
        arm_observe(kBufferSpecs, 3) != 0 || run_startup() != 0) {
        return 1;
    }
    void *(*create)(void *, const void *, int32_t, int32_t, int32_t) =
        (void *(*)(void *, const void *, int32_t, int32_t, int32_t))
            gfx_startup_stub_vb_create;
    (void)create(NULL, NULL, 0, 0, 0);
    if (finish_observe(dir, "host-zero-length") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 1 ||
        snap.records[0].kind != EU4_GFX_OBSERVE_KIND_VERTEX_CREATE ||
        !snap.records[0].contents_known || snap.records[0].payload_size != 0) {
        return fail("observe zero length");
    }
    return 0;
}

static int scenario_observe_buffers(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(kBufferSpecs, 3) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    uint8_t create_data[256];
    uint8_t update_data[64];
    for (size_t i = 0; i < sizeof(create_data); i++) {
        create_data[i] = (uint8_t)i;
    }
    for (size_t i = 0; i < sizeof(update_data); i++) {
        update_data[i] = (uint8_t)(0x80u + i);
    }
    void *(*create)(void *, const void *, int32_t, int32_t, int32_t) =
        (void *(*)(void *, const void *, int32_t, int32_t, int32_t))
            gfx_startup_stub_vb_create;
    void (*update)(void *, void *, const void *, int32_t, uint32_t, int32_t) =
        (void (*)(void *, void *, const void *, int32_t, uint32_t, int32_t))
            gfx_startup_stub_vb_update;
    void (*bind)(void *, int32_t, void **, int32_t, const int32_t *,
                 const int32_t *) =
        (void (*)(void *, int32_t, void **, int32_t, const int32_t *,
                  const int32_t *))gfx_startup_stub_vb_bind;
    int32_t stride = 32, count = 8;
    memcpy(gfx_startup_stub_vb_header, &stride, 4);
    memcpy(gfx_startup_stub_vb_header + 4, &count, 4);
    void *header = create(NULL, create_data, 8, 32, 0);
    if (header != gfx_startup_stub_vb_header) {
        return fail("vb create body");
    }
    update(NULL, header, update_data, 2, 32, 1);
    void *headers[1] = {header};
    bind(NULL, 1, headers, 0, NULL, NULL);
    if (finish_observe(dir, "host-buffers") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 3) {
        return fail("observe buffers incomplete");
    }
    if (snap.records[0].kind != EU4_GFX_OBSERVE_KIND_VERTEX_CREATE ||
        strcmp(snap.records[0].resource_id, "vb_1") != 0 ||
        snap.records[0].payload_size != 256 ||
        !snap.records[0].contents_known) {
        return fail("observe buffers create");
    }
    if (snap.records[1].kind != EU4_GFX_OBSERVE_KIND_VERTEX_UPDATE ||
        strcmp(snap.records[1].resource_id, "vb_1") != 0 ||
        snap.records[1].payload_size != 64) {
        return fail("observe buffers update");
    }
    if (snap.records[2].kind != EU4_GFX_OBSERVE_KIND_VERTEX_BIND ||
        strstr(snap.records[2].meta, "\"vb_1\"") == NULL) {
        return fail("observe buffers bind");
    }
    return 0;
}

static int scenario_observe_basic(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    int32_t first[6] = {1920, 1080, 0, 0, 10, 20};
    int32_t second[6] = {1728, 1117, 0, 0, 0, 0};
    call_viewport(first);
    call_viewport(second);
    if (finish_observe(dir, "host-observe") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 2 || !snap.init_seen ||
        snap.overlaps != 0) {
        return fail("observe basic incomplete");
    }
    if (snap.records[0].call_id != 3 || snap.records[1].call_id != 4 ||
        snap.records[0].completion_seq != 1 ||
        snap.records[1].completion_seq != 2) {
        return fail("observe ordering");
    }
    if (!snap.records[0].fields_ok || snap.records[0].input_size != 24 ||
        snap.records[0].output_size != 24 ||
        memcmp(snap.records[0].input, first, 24) != 0 ||
        memcmp(snap.records[0].output, first, 24) != 0 ||
        memcmp(snap.records[1].input, second, 24) != 0 ||
        memcmp(snap.records[1].output, second, 24) != 0) {
        return fail("observe words");
    }
    if (!snap.records[0].caller_known) {
        return fail("observe caller");
    }
    if (memcmp(gfx_startup_stub_master + 0xac, second, 24) != 0) {
        return fail("original body did not run");
    }
    eu4_gfx_startup_snapshot v0;
    eu4_gfx_startup_copy_snapshot(&v0);
    if (!v0.complete || v0.event_count != 4) {
        return fail("v0 context");
    }
    return 0;
}

static int scenario_observe_freeze(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    uint8_t settings[0x28];
    uint8_t records[12];
    make_settings(settings, records, 1);
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    uint8_t window[32] = {0};
    uint8_t success = 0;
    (void)((init_fn)gfx_startup_stub_init)(window, &success);
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (finish_observe(dir, "host-freeze") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 1) {
        return fail("observe freeze ops");
    }
    eu4_gfx_startup_snapshot v0;
    eu4_gfx_startup_copy_snapshot(&v0);
    if (!v0.complete || v0.event_count != 4 || v0.settings_calls != 1 ||
        v0.init_calls != 1) {
        return fail("observe freeze startup stream");
    }
    return 0;
}

static int scenario_observe_quota(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    eu4_gfx_observe_test_set_limits(1, 10000);
    if (run_startup() != 0) {
        return 1;
    }
    int32_t first[6] = {800, 600, 0, 0, 0, 0};
    int32_t second[6] = {640, 480, 0, 0, 0, 0};
    call_viewport(first);
    call_viewport(second);
    if (finish_observe(dir, "host-quota") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 1 || snap.refused < 1) {
        return fail("observe quota");
    }
    if (memcmp(snap.records[0].input, first, 24) != 0) {
        return fail("observe quota record");
    }
    return 0;
}

static int scenario_observe_unpaired(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    eu4_gfx_observe_test_force_unpaired();
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (finish_observe(dir, "host-unpaired") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (snap.events != 0 || snap.unmatched != 1 || snap.integrity_ok ||
        snap.records_copied) {
        return fail("observe unpaired");
    }
    if (file_size(dir, "operations.jsonl") != 0) {
        return fail("unpaired completed stream");
    }
    return 0;
}

static int scenario_observe_invalid_output(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    eu4_gfx_observe_test_fail_next_output_read();
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (finish_observe(dir, "host-invalid") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (snap.events != 1 || snap.unavailable < 1 || snap.integrity_ok ||
        snap.records_copied || snap.records[0].fields_ok) {
        return fail("observe invalid output");
    }
    if (file_size(dir, "operations.jsonl") != 0 ||
        file_size(dir, "operations.partial.jsonl") <= 0) {
        return fail("observe partial stream");
    }
    return 0;
}

static int scenario_observe_scalar(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    const eu4_gfx_observe_spec specs[2] = {kViewportSpec, kScalarSpec};
    if (arm_observe(specs, 2) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (call_scalar(5) != 12) {
        return fail("scalar body");
    }
    if (finish_observe(dir, "host-scalar") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 2) {
        return fail("observe scalar events");
    }
    if (snap.records[1].slot != 1 || snap.records[1].input_size != 4 ||
        snap.records[1].return_value != 12 || !snap.records[1].return_valid) {
        return fail("observe scalar record");
    }
    uint32_t value = 0;
    memcpy(&value, snap.records[1].input, 4);
    if (value != 5) {
        return fail("observe scalar input");
    }
    return 0;
}

static int scenario_observe_rollback(void) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    eu4_gfx_observe_test_reset();
    uintptr_t lo = 0;
    uintptr_t hi = 0;
    observe_text_range(&lo, &hi);
    const eu4_gfx_observe_spec specs[2] = {kViewportSpec, kScalarSpec};
    eu4_gfx_observe_test_force_install_failure(1);
    if (eu4_gfx_observe_install_specs(specs, 2, 0, lo, hi)) {
        return fail("rollback install should fail");
    }
    if (memcmp((const void *)gfx_startup_stub_viewport, kViewportPrefix,
               sizeof(kViewportPrefix)) != 0) {
        return fail("rollback did not restore");
    }
    if (eu4_gfx_observe_test_events() != 0) {
        return fail("rollback produced events");
    }
    return 0;
}

static int scenario_observe_duration(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    eu4_gfx_observe_test_set_limits(100, 20);
    if (run_startup() != 0) {
        return 1;
    }
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (!eu4_gfx_startup_wait_sealed(2000)) {
        return fail("duration did not seal");
    }
    if (finalize_observe(dir, "host-duration") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.events != 1) {
        return fail("observe duration");
    }
    return 0;
}

static int scenario_observe_reject(void) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    eu4_gfx_observe_test_reset();
    uintptr_t lo = 0;
    uintptr_t hi = 0;
    observe_text_range(&lo, &hi);
    eu4_gfx_observe_spec unsafe = kViewportSpec;
    unsafe.relocation = EU4_GFX_OBSERVE_RELOC_UNSAFE;
    if (eu4_gfx_observe_install_specs(&unsafe, 1, 0, lo, hi)) {
        return fail("unsafe relocation accepted");
    }
    static const uint8_t wrong_prefix[8] = {
        0x55, 0x48, 0x89, 0xe5, 0xf3, 0x0f, 0x6f, 0x07,
    };
    eu4_gfx_observe_spec wrong = kViewportSpec;
    wrong.prefix = wrong_prefix;
    if (eu4_gfx_observe_install_specs(&wrong, 1, 0, lo, hi)) {
        return fail("wrong prefix accepted");
    }
    if (eu4_gfx_observe_test_events() != 0) {
        return fail("rejected install produced events");
    }
    return 0;
}

static int scenario_observe_rx_failure(void) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    eu4_gfx_observe_test_reset();
    uintptr_t lo = 0;
    uintptr_t hi = 0;
    observe_text_range(&lo, &hi);
    eu4_gfx_site_test_fail_next_rx();
    if (eu4_gfx_observe_install_specs(&kViewportSpec, 1, 0, lo, hi)) {
        return fail("rx failure accepted");
    }
    if (memcmp((const void *)gfx_startup_stub_viewport, kViewportPrefix,
               sizeof(kViewportPrefix)) != 0) {
        return fail("rx failure did not restore");
    }
    if (eu4_gfx_observe_test_events() != 0) {
        return fail("rx failure produced events");
    }
    return 0;
}

static int scenario_observe_overlap(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    if (arm_observe(&kViewportSpec, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    eu4_gfx_observe_test_force_overlap();
    int32_t rect[6] = {800, 600, 0, 0, 0, 0};
    call_viewport(rect);
    if (finish_observe(dir, "host-overlap") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (snap.overlaps != 1 || !snap.records[0].overlap ||
        !snap.integrity_ok) {
        return fail("observe overlap");
    }
    return 0;
}


static const uint8_t kDrawPrefix[6] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57,
};
static const uint8_t kPrimitivePrefix[5] = {
    0x55, 0x48, 0x89, 0xe5, 0x53,
};

static const eu4_gfx_observe_spec kDrawSpec = {
    "_GfxDraw",
    (uint64_t)(uintptr_t)gfx_startup_stub_draw,
    kDrawPrefix,
    sizeof(kDrawPrefix),
    EU4_GFX_OBSERVE_RELOC_SAFE,
    eu4_gfx_observe_draw_entry,
    eu4_gfx_observe_draw_exit,
};

static const eu4_gfx_observe_spec kPrimitiveSpec = {
    "_GfxSetPrimitiveType",
    (uint64_t)(uintptr_t)gfx_startup_stub_primitive,
    kPrimitivePrefix,
    sizeof(kPrimitivePrefix),
    EU4_GFX_OBSERVE_RELOC_SAFE,
    eu4_gfx_observe_primitive_entry,
    eu4_gfx_observe_primitive_exit,
};

static uint64_t draw_expected_caller(void) {
    return (uint64_t)(uintptr_t)gfx_startup_stub_draw_caller_return;
}

static void fill_draw_master(int count, int mode, int shader_non_null) {
    memset(gfx_startup_stub_master, 0, 0x198);
    int32_t width = 800;
    int32_t height = 600;
    int32_t value = mode;
    memcpy(gfx_startup_stub_master + 0x08, &value, 4);
    value = count;
    memcpy(gfx_startup_stub_master + 0x64, &value, 4);
    uint64_t shader = shader_non_null ? 0x1234u : 0;
    memcpy(gfx_startup_stub_master + 0x70, &shader, sizeof(shader));
    memcpy(gfx_startup_stub_master + 0xa0, &width, 4);
    memcpy(gfx_startup_stub_master + 0xa4, &height, 4);
    value = height;
    memcpy(gfx_startup_stub_master + 0xac, &value, 4);
    value = width;
    memcpy(gfx_startup_stub_master + 0xb0, &value, 4);
}

static void call_primitive(int primitive) {
    void *handle = gfx_startup_stub_master;
    void (*fn)(void *, int) =
        (void (*)(void *, int))gfx_startup_stub_primitive;
    fn(&handle, primitive);
}

static void call_draw(int count, int first) {
    void *handle = gfx_startup_stub_master;
    void (*fn)(void *, int, int) =
        (void (*)(void *, int, int))gfx_startup_stub_draw_caller;
    fn(&handle, count, first);
}

static const eu4_gfx_observe_spec kDrawPrimitiveSpecs[2] = {
    kDrawSpec, kPrimitiveSpec};
static const eu4_gfx_observe_spec kDrawScalarSpecs[2] = {
    kDrawSpec, kScalarSpec};

static int arm_draw_observe(uint32_t stop_policy, uint64_t target_caller,
                            uint32_t retention, int with_scalar) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    const eu4_gfx_observe_spec *specs =
        with_scalar ? kDrawScalarSpecs : kDrawPrimitiveSpecs;
    if (arm_observe(specs, 2) != 0) {
        return 1;
    }
    if (!eu4_gfx_observe_configure_policy(stop_policy, target_caller,
                                          retention)) {
        return fail("draw policy");
    }
    return 0;
}

static int scenario_observe_draw_qualified(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(4, 5, 1);
    call_primitive(4);
    call_draw(0, 0);
    if (!eu4_gfx_observe_should_stop()) {
        return fail("qualified draw did not stop");
    }
    if (gfx_startup_stub_draw_calls != 1 ||
        gfx_startup_stub_primitive_value != 4) {
        return fail("draw or primitive body did not execute");
    }
    if (finish_observe(dir, "host-draw-qualified") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || !snap.target_draw_qualified ||
        snap.events != 1 || snap.observed != 2 ||
        snap.draw_observed != 1 || snap.draw_qualified != 1 ||
        snap.primitive_sets != 1 || snap.records[0].kind !=
                                         EU4_GFX_OBSERVE_KIND_DRAW ||
        snap.records[0].payload_size != EU4_GFX_OBSERVE_DRAW_MASTER_SIZE ||
        snap.target_draw_call_id != snap.records[0].call_id) {
        return fail("qualified draw snapshot");
    }
    if (strstr(snap.records[0].meta, "\"effective_count\":4") == NULL ||
        strstr(snap.records[0].meta, "\"mode\":5") == NULL ||
        strstr(snap.records[0].meta, "\"primitive_enum\":4") == NULL ||
        strstr(snap.records[0].meta, "\"qualified\":true") == NULL) {
        return fail("qualified draw meta");
    }
    return 0;
}

static int scenario_observe_draw_two_qualified(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(4, 5, 1);
    call_draw(0, 0);
    call_draw(0, 0);
    if (!eu4_gfx_observe_should_stop()) {
        return fail("two qualified draws did not stop");
    }
    if (finish_observe(dir, "host-draw-two-qualified") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || !snap.target_draw_qualified ||
        snap.events != 1 || snap.draw_observed != 2 ||
        snap.draw_qualified != 2 ||
        snap.target_draw_call_id != snap.records[0].call_id ||
        strstr(snap.records[0].meta, "\"qualified\":true") == NULL) {
        return fail("two qualified draw snapshot");
    }
    return 0;
}

static int scenario_observe_draw_insufficient_then_qualified(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(2, 5, 1);
    call_draw(0, 0);
    if (eu4_gfx_observe_should_stop()) {
        return fail("insufficient count stopped the capture");
    }
    fill_draw_master(4, 5, 1);
    call_draw(0, 0);
    if (!eu4_gfx_observe_should_stop()) {
        return fail("second draw did not qualify");
    }
    if (finish_observe(dir, "host-draw-two") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || !snap.target_draw_qualified ||
        snap.events != 2 || snap.draw_observed != 2 ||
        snap.draw_qualified != 1 ||
        strstr(snap.records[0].meta, "\"qualified\":false") == NULL ||
        strstr(snap.records[1].meta, "\"qualified\":true") == NULL) {
        return fail("two-draw capture");
    }
    return 0;
}

static int scenario_observe_draw_nonqualified(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(2, 5, 1);
    call_draw(0, 0);
    if (eu4_gfx_observe_should_stop()) {
        return fail("nonqualifying draw stopped the capture");
    }
    if (finish_observe(dir, "host-draw-none") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.target_draw_qualified ||
        snap.events != 1 || snap.draw_observed != 1 ||
        snap.draw_at_caller != 1 || snap.draw_qualified != 0) {
        return fail("nonqualified draw snapshot");
    }
    return 0;
}

static int scenario_observe_draw_caller_filter(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller() + 0x10000,
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(4, 5, 1);
    call_draw(0, 0);
    if (eu4_gfx_observe_should_stop()) {
        return fail("wrong caller qualified");
    }
    if (finish_observe(dir, "host-draw-caller") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.target_draw_qualified ||
        snap.draw_observed != 1 || snap.draw_at_caller != 0 ||
        strstr(snap.records[0].meta, "\"caller_match\":false") == NULL) {
        return fail("caller filter");
    }
    return 0;
}

static int scenario_observe_draw_mode_mismatch(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(4, 4, 1);
    call_draw(0, 0);
    if (eu4_gfx_observe_should_stop()) {
        return fail("mode 4 qualified");
    }
    if (finish_observe(dir, "host-draw-mode") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.target_draw_qualified ||
        snap.draw_observed != 1 ||
        strstr(snap.records[0].meta, "\"mode\":4") == NULL) {
        return fail("mode mismatch");
    }
    return 0;
}

static int scenario_observe_draw_budget(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 0) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    fill_draw_master(2, 5, 1);
    for (int i = 0; i < 65; i++) {
        call_draw(0, 0);
    }
    if (!eu4_gfx_observe_should_stop() ||
        !eu4_gfx_observe_test_budget_exhausted()) {
        return fail("retention budget not exhausted");
    }
    if (finish_observe(dir, "host-draw-budget") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || snap.target_draw_qualified ||
        !snap.retention_budget_exhausted || snap.events != 65) {
        return fail("draw budget snapshot");
    }
    return 0;
}

static int scenario_observe_state_retention(const char *dir) {
    if (arm_draw_observe(EU4_GFX_OBSERVE_STOP_TARGET_DRAW,
                         draw_expected_caller(),
                         EU4_GFX_OBSERVE_RETENTION_STATE, 1) != 0) {
        return 1;
    }
    if (run_startup() != 0) {
        return 1;
    }
    for (int i = 0; i < 2000; i++) {
        (void)call_scalar(i);
    }
    fill_draw_master(4, 5, 1);
    call_draw(0, 0);
    if (!eu4_gfx_observe_should_stop()) {
        return fail("retention draw did not stop");
    }
    if (eu4_gfx_observe_test_observed() < 2001 ||
        eu4_gfx_observe_test_events() != 1) {
        return fail("state retention counters");
    }
    if (finish_observe(dir, "host-draw-retention") != 0) {
        return 1;
    }
    eu4_gfx_observe_snapshot snap;
    eu4_gfx_observe_copy_snapshot(&snap);
    if (!snap.integrity_ok || !snap.target_draw_qualified ||
        snap.events != 1 || snap.observed < 2001 ||
        snap.draw_observed != 1 || snap.retention !=
                                      EU4_GFX_OBSERVE_RETENTION_STATE) {
        return fail("state retention snapshot");
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        return fail("usage: host <scenario> <dir>");
    }
    const char *scenario = argv[1];
    const char *dir = argv[2];
    if (strcmp(scenario, "basic") == 0) {
        return scenario_basic(dir);
    }
    if (strcmp(scenario, "fail-init") == 0) {
        return scenario_fail_init(dir);
    }
    if (strcmp(scenario, "drain-refuse") == 0) {
        return scenario_drain_refuse(dir);
    }
    if (strcmp(scenario, "pause-drain") == 0) {
        return scenario_pause_drain(dir);
    }
    if (strcmp(scenario, "tls-full") == 0) {
        return scenario_tls_full(dir);
    }
    if (strcmp(scenario, "null-settings") == 0) {
        return scenario_null_settings(dir);
    }
    if (strcmp(scenario, "oversize") == 0) {
        return scenario_oversize(dir);
    }
    if (strcmp(scenario, "dual-finalize") == 0) {
        return scenario_dual_finalize(dir);
    }
    if (strcmp(scenario, "unsealed-finalize") == 0) {
        return scenario_unsealed_finalize(dir);
    }
    if (strcmp(scenario, "parallel-seq") == 0) {
        return scenario_parallel_seq(dir);
    }
    if (strcmp(scenario, "write-fail") == 0) {
        return scenario_write_fail(dir);
    }
    if (strcmp(scenario, "bad-bytes") == 0) {
        return scenario_bad_bytes();
    }
    if (strcmp(scenario, "observe-basic") == 0) {
        return scenario_observe_basic(dir);
    }
    if (strcmp(scenario, "observe-freeze") == 0) {
        return scenario_observe_freeze(dir);
    }
    if (strcmp(scenario, "observe-quota") == 0) {
        return scenario_observe_quota(dir);
    }
    if (strcmp(scenario, "observe-unpaired") == 0) {
        return scenario_observe_unpaired(dir);
    }
    if (strcmp(scenario, "observe-invalid-output") == 0) {
        return scenario_observe_invalid_output(dir);
    }
    if (strcmp(scenario, "observe-scalar") == 0) {
        return scenario_observe_scalar(dir);
    }
    if (strcmp(scenario, "observe-rollback") == 0) {
        return scenario_observe_rollback();
    }
    if (strcmp(scenario, "observe-duration") == 0) {
        return scenario_observe_duration(dir);
    }
    if (strcmp(scenario, "observe-overlap") == 0) {
        return scenario_observe_overlap(dir);
    }
    if (strcmp(scenario, "observe-reject") == 0) {
        return scenario_observe_reject();
    }
    if (strcmp(scenario, "observe-rx-failure") == 0) {
        return scenario_observe_rx_failure();
    }
    if (strcmp(scenario, "observe-buffers") == 0) {
        return scenario_observe_buffers(dir);
    }
    if (strcmp(scenario, "observe-bind-explicit") == 0) {
        return scenario_observe_bind_explicit(dir);
    }
    if (strcmp(scenario, "observe-unknown-update") == 0) {
        return scenario_observe_unknown_update(dir);
    }
    if (strcmp(scenario, "observe-zero-length") == 0) {
        return scenario_observe_zero_length(dir);
    }
    if (strcmp(scenario, "unsafe-read") == 0) {
        return scenario_unsafe_read(dir);
    }
    if (strcmp(scenario, "observe-draw-qualified") == 0) {
        return scenario_observe_draw_qualified(dir);
    }
    if (strcmp(scenario, "observe-draw-two") == 0) {
        return scenario_observe_draw_insufficient_then_qualified(dir);
    }
    if (strcmp(scenario, "observe-draw-two-qualified") == 0) {
        return scenario_observe_draw_two_qualified(dir);
    }
    if (strcmp(scenario, "observe-draw-none") == 0) {
        return scenario_observe_draw_nonqualified(dir);
    }
    if (strcmp(scenario, "observe-draw-caller") == 0) {
        return scenario_observe_draw_caller_filter(dir);
    }
    if (strcmp(scenario, "observe-draw-mode") == 0) {
        return scenario_observe_draw_mode_mismatch(dir);
    }
    if (strcmp(scenario, "observe-draw-budget") == 0) {
        return scenario_observe_draw_budget(dir);
    }
    if (strcmp(scenario, "observe-draw-retention") == 0) {
        return scenario_observe_state_retention(dir);
    }
    return fail("unknown scenario");
}
