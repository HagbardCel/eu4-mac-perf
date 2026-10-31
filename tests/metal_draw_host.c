#include <stdio.h>
#include <stdlib.h>

int eu4_metal_draw_render(const void *vertices, int vertex_bytes, int stride, int color_offset,
                          int color_is_byte, const float *uniform, int vertex_count, int width,
                          int height, int as_strip, unsigned char *pixels_out);

int main(void) {
    float vertices[] = {
        -0.5f, -0.5f, 1, 0, 0, 1,
         0.5f, -0.5f, 1, 0, 0, 1,
         0.0f,  0.5f, 1, 0, 0, 1,
    };
    float uniform[16] = {
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1,
    };
    int colored = eu4_metal_draw_render(vertices, (int)sizeof vertices, 24, 8, 0, uniform, 3, 64, 64, 0, NULL);
    if (colored <= 0) {
        fprintf(stderr, "colored pixels: %d\n", colored);
        return 1;
    }
    printf("%d\n", colored);
    return 0;
}
