#ifndef EU4_GFX_UV2_SPARSE_H
#define EU4_GFX_UV2_SPARSE_H

#include <stdlib.h>

#define EU4_UV2_MAX_PIXELS 64
#define EU4_UV2_VERTEX_SHA "2e8842a26b765c70f06594103eff681f8a149a49966c34b25d8648058f7a4b98"
#define EU4_UV2_FRAGMENT_SHA "3ecb11ae2fcd0c07bb9960e41fa7122a6cb7b5d873dcc9f95b9d55b765caf548"
#define EU4_UV2_SPARSE_FILE "uv2-fragments.json"
#define EU4_UV2_SPARSE_SCHEMA "gfx-uv2-fragments/v1"

typedef struct {
    unsigned x, y;
} eu4_uv2_pixel;

typedef struct {
    unsigned count;
    eu4_uv2_pixel pixels[EU4_UV2_MAX_PIXELS];
} eu4_uv2_selection;

/* CLI coordinates are pixel indices in the captured GL lower-left layout. */
static inline int eu4_uv2_parse_pixels(const char *text,
                                        eu4_uv2_selection *out) {
    out->count = 0;
    if (text == NULL)
        return 0;
    while (*text != '\0' && out->count < EU4_UV2_MAX_PIXELS) {
        char *end;
        if (*text < '0' || *text > '9')
            break;
        unsigned long x = strtoul(text, &end, 10);
        if (*end != ',' || x > 8191 || end[1] < '0' || end[1] > '9')
            break;
        unsigned long y = strtoul(end + 1, &end, 10);
        if (y > 8191 || (*end != '\0' && *end != ';'))
            break;
        for (unsigned i = 0; i < out->count; i++) {
            if (out->pixels[i].x == x && out->pixels[i].y == y)
                return -1;
        }
        out->pixels[out->count].x = (unsigned)x;
        out->pixels[out->count++].y = (unsigned)y;
        if (*end == '\0')
            return 0;
        text = end + 1;
    }
    return -1;
}

#endif
