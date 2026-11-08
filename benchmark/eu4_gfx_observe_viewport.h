#ifndef EU4_GFX_OBSERVE_VIEWPORT_H
#define EU4_GFX_OBSERVE_VIEWPORT_H

#include "eu4_gfx_observe.h"

void eu4_gfx_observe_viewport_entry(eu4_gfx_capture_frame *frame,
                                    eu4_gfx_observe_call *call);
void eu4_gfx_observe_viewport_exit(eu4_gfx_capture_frame *frame,
                                   eu4_gfx_observe_call *call,
                                   eu4_gfx_observe_record *record);

#endif
