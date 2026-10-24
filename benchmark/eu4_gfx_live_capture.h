#ifndef EU4_GFX_LIVE_CAPTURE_H
#define EU4_GFX_LIVE_CAPTURE_H

#include <stdbool.h>

#define EU4_GFX_LIVE_CAPTURE_QUOTA 64u
#define EU4_GFX_LIVE_CAPTURE_THREADS 64u

bool eu4_gfx_live_capture_start(void *site, const char *run_dir,
                                const char *control_path,
                                const char *session_id,
                                unsigned deadline_seconds,
                                bool restore_on_stop);
void eu4_gfx_live_capture_stop(void);

#endif
