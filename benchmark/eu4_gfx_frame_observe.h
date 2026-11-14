#ifndef EU4_GFX_FRAME_OBSERVE_H
#define EU4_GFX_FRAME_OBSERVE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Shared, backend-agnostic per-frame observation stream.
 *
 * Both the original-OpenGL observer and the reconstructed Metal backend call
 * this module at their logical frame boundary.  A record is lightweight: frame
 * identity, extent, full-frame SHA-256, a small preview, draw composition and
 * health flags.  Full framebuffers are retained only in a bounded ring so a
 * first divergence can be dumped on demand without writing every frame.
 *
 * All functions are safe to call when the stream is disabled.
 */

#define EU4_GFX_FRAME_OBSERVE_SCHEMA "gfx-frame-observe/v1"

int eu4_gfx_frame_observe_init(const char *backend, const char *dir,
                               const char *session);
int eu4_gfx_frame_observe_enabled(void);
void eu4_gfx_frame_observe_note_draw(uint64_t caller_rva);
void eu4_gfx_frame_observe_take_stats(uint32_t *draws, uint64_t *signature,
                                      uint32_t *distinct_callers);
uint32_t eu4_gfx_frame_observe_dropped(void);
int eu4_gfx_frame_observe_lockstep_enabled(void);
int eu4_gfx_frame_observe_lockstep_wait(uint32_t frame_index,
                                        uint32_t timeout_ms);
int eu4_gfx_frame_observe_wants(uint32_t frame_index);
int eu4_gfx_frame_observe_emit_metadata(uint32_t width, uint32_t height,
                                        const char *origin,
                                        uint32_t frame_index, uint32_t draws,
                                        uint64_t signature,
                                        uint32_t distinct_callers,
                                        int gpu_completed, const char *error);
int eu4_gfx_frame_observe_emit(const uint8_t *bgra, uint32_t width,
                               uint32_t height, const char *origin,
                               uint32_t frame_index, uint32_t draws,
                               uint64_t signature, uint32_t distinct_callers,
                               int gpu_completed, int capture_valid,
                               const char *error);

#ifdef __cplusplus
}
#endif

#endif
