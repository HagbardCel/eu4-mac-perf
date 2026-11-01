#ifndef EU4_B2_DRAW_H
#define EU4_B2_DRAW_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Renders the fixture in dir into pixels (RGBA8, width * height * 4).
   Returns 0 on success. The Metal shaders are draw.vert.metal and draw.frag.metal
   in that directory, entry points main0. Depth uses the B1 factory only when
   fixture.json says depth_matches_b1; those bytes are generated here and are not
   a captured engine description. */
int eu4_b2_draw_fixture(const char *dir, uint8_t *pixels, int capacity);

#ifdef __cplusplus
}
#endif

#endif
