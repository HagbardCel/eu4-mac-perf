#include "eu4_gfx_startup_trace.h"

#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

extern void gfx_startup_stub_settings(void);
extern void gfx_startup_stub_init(void);
extern void gfx_startup_stub_init_fail(void);
extern uint8_t gfx_startup_stub_master[];

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
    if (strcmp(scenario, "unsafe-read") == 0) {
        return scenario_unsafe_read(dir);
    }
    return fail("unknown scenario");
}
