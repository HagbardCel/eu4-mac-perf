#ifndef EU4_GFX_OBSERVE_BUFFERS_H
#define EU4_GFX_OBSERVE_BUFFERS_H

#include "eu4_gfx_observe.h"

void eu4_gfx_observe_buffers_reset(void);

void eu4_gfx_observe_vb_create_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call);
void eu4_gfx_observe_vb_create_exit(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call,
                                    eu4_gfx_observe_record *record);
void eu4_gfx_observe_vb_update_entry(eu4_gfx_capture_frame *frame,
                                     eu4_gfx_observe_call *call);
void eu4_gfx_observe_vb_bind_entry(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call);

void eu4_gfx_observe_cb_entry(eu4_gfx_capture_frame *frame,
                              eu4_gfx_observe_call *call);
int eu4_gfx_observe_constant_matches(uintptr_t header, const uint8_t *bytes,
                                     size_t n, uint64_t *call_id);

#endif
