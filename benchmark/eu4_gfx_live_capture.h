#ifndef EU4_GFX_LIVE_CAPTURE_H
#define EU4_GFX_LIVE_CAPTURE_H

#include <stdbool.h>

#define EU4_GFX_LIVE_CAPTURE_POOL 64u
#define EU4_GFX_LIVE_CAPTURE_THREADS 64u
#define EU4_GFX_LIVE_CAPTURE_MAX_UNIQUE 4096u

bool eu4_gfx_live_capture_start(void *site, const char *run_dir,
                                const char *control_path,
                                const char *session_id,
                                unsigned deadline_seconds,
                                unsigned max_unique,
                                bool restore_on_stop);
void eu4_gfx_live_capture_stop(void);

/* Harness controls. The dylib does not call these. */
void eu4_gfx_live_capture_test_force_identical_hash(int enabled);
void eu4_gfx_live_capture_test_fail_next_writes(unsigned count);
void eu4_gfx_live_capture_test_dump_canonical(int enabled);
void eu4_gfx_live_capture_test_pause_drain(int enabled);
bool eu4_gfx_live_capture_wait_idle(unsigned timeout_ms);

#endif
