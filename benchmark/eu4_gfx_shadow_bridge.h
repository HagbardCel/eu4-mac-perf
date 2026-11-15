#ifndef EU4_GFX_SHADOW_BRIDGE_H
#define EU4_GFX_SHADOW_BRIDGE_H

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

#ifdef __cplusplus
}
#endif

#endif
