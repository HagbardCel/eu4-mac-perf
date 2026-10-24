#ifndef EU4_GFX_SET_CONSTANT_BUFFER_CAPTURE_H
#define EU4_GFX_SET_CONSTANT_BUFFER_CAPTURE_H

#include "eu4_gfx_capture_detour.h"

#include <stdbool.h>

void eu4_gfx_set_constant_buffer_capture_entry(eu4_gfx_capture_frame *frame);
void eu4_gfx_set_constant_buffer_capture_exit(eu4_gfx_capture_frame *frame);
bool eu4_gfx_set_constant_buffer_capture_install(void *site);

#endif
