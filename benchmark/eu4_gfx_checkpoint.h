#ifndef EU4_GFX_CHECKPOINT_H
#define EU4_GFX_CHECKPOINT_H

#include <stddef.h>
#include <stdint.h>

#include "eu4_gfx_draw_capture.h"
#include "eu4_gfx_simple_draw.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Bounded diagnostic checkpoints. The selector is a diagnostic address, not a
   rendering rule: the same implementation serves after-clear, after-draw and
   before-present. A disabled checkpoint is a no-op. */
void eu4_gfx_checkpoint_configure(const char *operation, uint64_t caller_rva,
                                  int occurrence, const char *output_dir,
                                  int stop_after_capture);
int eu4_gfx_checkpoint_configure_from_env(void);
int eu4_gfx_checkpoint_enabled(void);
/* Test/diagnostic queries; no selector state is mutated. */
int eu4_gfx_checkpoint_test_enabled(void);
int eu4_gfx_checkpoint_test_occurrence(void);

void eu4_gfx_checkpoint_after_draw(void *master, const eu4_gfx_draw_raw *raw,
                                   const eu4_gfx_simple_draw_state *state);
void eu4_gfx_checkpoint_after_clear(void *master, const void *caller);
void eu4_gfx_checkpoint_before_present(void *master, const void *caller);
void eu4_gfx_checkpoint_after_present(void *master, const void *caller);
/* Apply the caller filter and occurrence selection without touching the
   drawable. Returns 1 only for the selected presentation invocation. */
int eu4_gfx_checkpoint_present_select(uint64_t caller_rva);

#ifdef __cplusplus
}
#endif

#endif
