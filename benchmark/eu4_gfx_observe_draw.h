#ifndef EU4_GFX_OBSERVE_DRAW_H
#define EU4_GFX_OBSERVE_DRAW_H

#include "eu4_gfx_observe.h"

void eu4_gfx_observe_draw_configure(uint64_t target_caller);
void eu4_gfx_observe_draw_reset(void);

void eu4_gfx_observe_primitive_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call);
void eu4_gfx_observe_primitive_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record);

void eu4_gfx_observe_draw_entry(eu4_gfx_capture_frame *frame,
                                eu4_gfx_observe_call *call);
void eu4_gfx_observe_draw_exit(eu4_gfx_capture_frame *frame,
                               eu4_gfx_observe_call *call,
                               eu4_gfx_observe_record *record);

#endif
