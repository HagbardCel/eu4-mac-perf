#ifndef EU4_GFX_STARTUP_TRACE_H
#define EU4_GFX_STARTUP_TRACE_H

#include "eu4_gfx_capture_detour.h"

#include <stdbool.h>
#include <stdint.h>

#define EU4_GFX_STARTUP_SCHEMA "gfx-startup/v0"
#define EU4_GFX_STARTUP_VERSION "1"
#define EU4_GFX_STARTUP_EVENT_CAP 256u
#define EU4_GFX_STARTUP_TLS_DEPTH 8u
#define EU4_GFX_STARTUP_SAMPLE_CAP 64u
#define EU4_GFX_STARTUP_SAMPLE_SIZE 12u
#define EU4_GFX_STARTUP_WINDOW_SIZE 32u
#define EU4_GFX_STARTUP_MASTER_SIZE 0x198u

#define EU4_GFX_STARTUP_RVA_SETTINGS 0x15ed388ull
#define EU4_GFX_STARTUP_RVA_INIT 0x15e8ea0ull

enum {
    EU4_GFX_STARTUP_SITE_SETTINGS = 0,
    EU4_GFX_STARTUP_SITE_INIT = 1,
    EU4_GFX_STARTUP_SITE_COUNT = 2,
};

enum {
    EU4_GFX_STARTUP_ARMED = 0,
    EU4_GFX_STARTUP_DRAINING = 1,
    EU4_GFX_STARTUP_SEALED = 2,
};

typedef struct {
    uint64_t seq;
    uint64_t call_id;
    uint32_t site;
    uint32_t phase; /* 0 entry, 1 exit */
    uint64_t thread_id;
    uint64_t caller_rva; /* 0 if unknown */
    int caller_known;
    int fields_ok;
    int admitted;

    /* settings */
    int32_t display;
    int32_t windowed;
    int32_t sample_count;
    int settings_present;
    uint8_t settings_byte10;
    uint32_t anisotropy;
    uint64_t span;
    uint32_t constant;
    uint8_t sample_flags[EU4_GFX_STARTUP_SAMPLE_CAP];
    uint32_t sample_values[EU4_GFX_STARTUP_SAMPLE_CAP];
    int samples_ok;
    int64_t return_value;

    /* init */
    uint8_t window[EU4_GFX_STARTUP_WINDOW_SIZE];
    int window_ok;
    int success_dest_readable;
    int success_readable;
    uint8_t success;
    int master_non_null;
    int32_t primitive;
    int32_t width;
    int32_t height;
    int32_t source;
    int32_t viewport[6];
    uint8_t dirty;
    uint8_t lod;
    int32_t max_texture;
    int blend_present;
    int depth_present;
    int raster_present;
    int gl_context_present;
    int master_ok;
} eu4_gfx_startup_event;

typedef struct {
    uint32_t state;
    uint64_t next_seq;
    uint64_t next_call_id;
    uint32_t event_count;
    uint32_t overflow;
    uint32_t unmatched;
    uint32_t refused;
    uint32_t unavailable;
    uint32_t in_flight;
    uint32_t settings_calls;
    uint32_t init_calls;
    uint32_t qualifying_init;
    int complete;
    eu4_gfx_startup_event events[EU4_GFX_STARTUP_EVENT_CAP];
} eu4_gfx_startup_snapshot;

bool eu4_gfx_startup_install(void *settings_site, void *init_site,
                             const uint8_t settings_expected[EU4_GFX_CAPTURE_PATCH_SIZE],
                             const uint8_t init_expected[EU4_GFX_CAPTURE_PATCH_SIZE]);
bool eu4_gfx_startup_installed(void);
void eu4_gfx_startup_arm(void);
void eu4_gfx_startup_request_drain(void);
uint32_t eu4_gfx_startup_state(void);
bool eu4_gfx_startup_wait_sealed(unsigned timeout_ms);
void eu4_gfx_startup_copy_snapshot(eu4_gfx_startup_snapshot *out);
bool eu4_gfx_startup_finalize(const char *run_dir, const char *session,
                              const char *executable_sha, int pid,
                              uintptr_t image_base);

/* Test hooks. */
void eu4_gfx_startup_test_force_unsafe_read(int on);
void eu4_gfx_startup_test_reset(void);
void eu4_gfx_startup_set_image_range(uintptr_t image_base, uintptr_t text_begin,
                                     uintptr_t text_end);

extern void eu4_gfx_startup_gateway_settings(void);
extern void eu4_gfx_startup_gateway_init(void);
extern void *eu4_gfx_startup_trampoline_settings;
extern void *eu4_gfx_startup_trampoline_init;
extern eu4_gfx_capture_hook eu4_gfx_startup_entry_settings;
extern eu4_gfx_capture_hook eu4_gfx_startup_exit_settings;
extern eu4_gfx_capture_hook eu4_gfx_startup_entry_init;
extern eu4_gfx_capture_hook eu4_gfx_startup_exit_init;

#endif
