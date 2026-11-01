#ifndef EU4_GFX_DRAW_INDEXED_OBSERVE_H
#define EU4_GFX_DRAW_INDEXED_OBSERVE_H

#include "eu4_gfx_capture_detour.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#define EU4_GFX_DRAW_OBSERVE_SAMPLES 64u
#define EU4_GFX_DRAW_OBSERVE_CONTEXT_BYTES 0x171u

/* Raw registers and raw context fields. The selected count is not stored. */
typedef struct {
    uint64_t rdi;
    uint64_t rsi;
    uint64_t rdx;
    uint64_t rcx;
    uint32_t mode;
    uint32_t stored_count;
    uint64_t parent;
    uint8_t uniform_flag;
    uint8_t attrib_flag;
    uint8_t context_ok;
    uint8_t refusal;
} eu4_gfx_draw_observe_sample;

/* arm_word is a shared uint64. Nonzero arms detailed samples. Set the pointer
 * before install and do not retarget it while intercepted calls may still run.
 * Storing zero freezes admission. */
void eu4_gfx_draw_indexed_observe_bind(void *arm_word);
void eu4_gfx_draw_indexed_observe_entry(eu4_gfx_capture_frame *frame);
/* Store zero to the arm word, then wait until admitted calls have finished.
 * Returns false if that wait exceeds timeout_ms. */
bool eu4_gfx_draw_indexed_observe_freeze(unsigned timeout_ms);

bool eu4_gfx_draw_indexed_observe_write(FILE *file);
uint64_t eu4_gfx_draw_indexed_observe_hits(void);
uint64_t eu4_gfx_draw_indexed_observe_post_ready(void);
unsigned eu4_gfx_draw_indexed_observe_completed(void);
unsigned eu4_gfx_draw_indexed_observe_refusals(void);
unsigned eu4_gfx_draw_indexed_observe_uniform_nonzero(void);
unsigned eu4_gfx_draw_indexed_observe_attrib_nonzero(void);
unsigned eu4_gfx_draw_indexed_observe_slot_state(unsigned index);
bool eu4_gfx_draw_indexed_observe_copy_sample(unsigned index,
                                              eu4_gfx_draw_observe_sample *out);

#endif
