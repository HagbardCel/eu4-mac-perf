#include "eu4_gfx_startup_trace.h"

#include <stdatomic.h>
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

static int scenario_basic(const char *dir) {
    if (arm_and_install((void *)gfx_startup_stub_settings,
                        (void *)gfx_startup_stub_init) != 0) {
        return 1;
    }
    uint8_t settings[0x28];
    uint8_t records[12];
    memset(settings, 0, sizeof(settings));
    memset(records, 0, sizeof(records));
    uint32_t sample = 2;
    memcpy(records, &sample, 4);
    void *array = records;
    memcpy(settings + 8, &array, sizeof(array));
    settings[0x10] = 1;
    uint32_t anisotropy = 0;
    uint64_t span = 0x30000000300ull;
    uint32_t constant = 0x400;
    memcpy(settings + 0x14, &anisotropy, 4);
    memcpy(settings + 0x18, &span, 8);
    memcpy(settings + 0x20, &constant, 4);

    settings_fn settings_call = (settings_fn)gfx_startup_stub_settings;
    init_fn init_call = (init_fn)gfx_startup_stub_init;
    int rc = settings_call(1, 0, 1, settings);
    if (rc != 7) {
        return fail("settings return");
    }
    if (records[4] != 1) {
        return fail("settings body");
    }
    uint8_t window[32];
    memset(window, 0, sizeof(window));
    uint32_t window_id = 7;
    uint32_t source = 9;
    memcpy(window, &window_id, 4);
    memcpy(window + 0x18, &source, 4);
    uint8_t success = 0;
    void *master = init_call(window, &success);
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
        snap.event_count != 4) {
        return fail("snapshot incomplete");
    }
    if (snap.events[0].call_id != snap.events[1].call_id ||
        snap.events[2].call_id != snap.events[3].call_id) {
        return fail("call id mismatch");
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
    if (success != 0) {
        return fail("fail init success");
    }
    sleep(0);
    eu4_gfx_startup_request_drain();
    (void)eu4_gfx_startup_wait_sealed(500);
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

typedef struct {
    _Atomic int entered;
    _Atomic int release;
    uint8_t *success;
    void *window;
} hold_state;

static hold_state g_hold;

/* Slow init body: signal entered, wait for release, then succeed. */
static void *held_init(void *window, uint8_t *success) {
    (void)window;
    atomic_store(&g_hold.entered, 1);
    while (!atomic_load(&g_hold.release)) {
        usleep(1000);
    }
    if (success) {
        *success = 1;
    }
    return gfx_startup_stub_master;
}

static void *held_thread(void *arg) {
    (void)arg;
    init_fn call = (init_fn)gfx_startup_stub_init;
    /* Replace trampoline target temporarily? Better: install held via custom page.
       For this host we call through the hooked stub_init which completes quickly.
       Instead drive drain with request_drain while a settings call is nested via
       a second thread calling settings during DRAINING. */
    uint8_t settings[0x28] = {0};
    uint8_t records[12] = {0};
    void *array = records;
    memcpy(settings + 8, &array, sizeof(array));
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    return NULL;
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
    /* After seal, another settings call is refused and must not unmatched. */
    uint8_t settings[0x28] = {0};
    uint8_t records[12] = {0};
    void *array = records;
    memcpy(settings + 8, &array, sizeof(array));
    (void)((settings_fn)gfx_startup_stub_settings)(1, 0, 1, settings);
    if (!eu4_gfx_startup_finalize(dir, "host-drain", "test", getpid(), 0)) {
        return fail("finalize drain");
    }
    eu4_gfx_startup_snapshot snap;
    eu4_gfx_startup_copy_snapshot(&snap);
    if (snap.unmatched != 0) {
        return fail("drain refuse unmatched");
    }
    if (snap.refused == 0) {
        return fail("expected refuse");
    }
    if (!snap.complete) {
        return fail("drain should stay complete");
    }
    (void)held_init;
    (void)held_thread;
    (void)g_hold;
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
    if (strcmp(scenario, "bad-bytes") == 0) {
        return scenario_bad_bytes();
    }
    if (strcmp(scenario, "unsafe-read") == 0) {
        return scenario_unsafe_read(dir);
    }
    return fail("unknown scenario");
}
