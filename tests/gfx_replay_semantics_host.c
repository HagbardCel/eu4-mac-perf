#include "../renderer/engine/eu4_gfx_replay_semantics.h"
#include <stdio.h>

int main(void) {
    struct { unsigned flags; int color, depth, stencil, advances; } cases[] = {
        {1, 1, 0, 0, 1},
        {2, 0, 1, 0, 0},
        {4, 0, 0, 1, 0},
        {3, 1, 1, 0, 1},
        {7, 1, 1, 1, 1},
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        if (eu4_gfx_clear_color(cases[i].flags) != cases[i].color ||
            eu4_gfx_clear_depth(cases[i].flags) != cases[i].depth ||
            eu4_gfx_clear_stencil(cases[i].flags) != cases[i].stencil ||
            eu4_gfx_clear_advances_color(cases[i].flags) != cases[i].advances) {
            return 1;
        }
    }
    puts("ok");
    return 0;
}
