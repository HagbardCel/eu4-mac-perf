#ifndef EU4_GFX_SHADOW_BRIDGE_H
#define EU4_GFX_SHADOW_BRIDGE_H

#include "eu4_gfx_frame_seal.h"
#include "eu4_gfx_metal_replay.h"

/* Opt-in in-process single-frame Metal shadow bridge.
 *
 * Enabled only by EU4_GFX_SHADOW_MODE=single-frame.  It subscribes to PR 1's
 * sealed-frame callback, waits (bounded) for the external translator to publish
 * immutable shader artifacts, executes exactly one replay through the reusable
 * Metal core, and writes a shadow report.  It never starts another EU4 process,
 * never opens an OpenGL context, and never touches the engine's framebuffer.
 */

#ifdef __cplusplus
extern "C" {
#endif

/* Returns 1 when enabled, 0 when disabled, -1 on a configuration/subscription
 * failure.  Safe to call once during instrumentation initialization. */
int eu4_gfx_shadow_configure_from_env(void);

int eu4_gfx_shadow_enabled(void);

/* Host-test only: build the exact report the bridge publishes without
 * subscribing to a live frame, waiting for shaders or creating a Metal
 * device.  `exception_reason` selects the internal_exception path; otherwise
 * `result` selects the normal success/failure path; otherwise
 * `failure_stage`/`failure_error` select a non-replay bridge failure.  Returns
 * a malloc'ed JSON string (caller frees) or NULL on allocation failure. */
char *eu4_gfx_shadow_test_report_json(
    const eu4_frame_capture_descriptor *capture,
    const eu4_metal_replay_result *result, int code,
    const char *failure_stage, const char *failure_error,
    const char *exception_reason);

#ifdef __cplusplus
}
#endif

#endif
