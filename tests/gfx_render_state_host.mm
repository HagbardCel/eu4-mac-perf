/* Calls the production sampler-identity and raster-state projections. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

extern "C" int eu4_gfx_frame_replay_test_render_state(
    const char *json_path, char *key_out, size_t key_capacity,
    int *cull_mode, int *cull_empty, int *scissor_empty,
    int *scissor_x, int *scissor_y, int *scissor_w, int *scissor_h,
    double *viewport_x, double *viewport_y, double *viewport_w,
    double *viewport_h);
extern "C" int eu4_gfx_frame_replay_test_cull_render(
    int gl_cull_mode, int cull_enabled, int primitive_mode, int no_cull,
    int *left_pixels, int *right_pixels);

int main(int argc, char **argv) {
    if (argc == 6 && strcmp(argv[1], "--cull") == 0) {
        int left = -1;
        int right = -1;
        int result = eu4_gfx_frame_replay_test_cull_render(
            atoi(argv[2]), atoi(argv[3]), atoi(argv[4]), atoi(argv[5]),
            &left, &right);
        if (result != 0) {
            fprintf(stderr, "cull render failed\n");
            return 1;
        }
        printf("left=%d right=%d\n", left, right);
        return 0;
    }
    if (argc != 2) {
        fprintf(stderr, "usage: %s <state.json>\n", argv[0]);
        return 2;
    }
    char key[4096] = "";
    int mode = -1;
    int cull_empty = -1;
    int scissor_empty = -1;
    int x = 0, y = 0, w = 0, h = 0;
    double vx = 0, vy = 0, vw = 0, vh = 0;
    int result = eu4_gfx_frame_replay_test_render_state(
        argv[1], key, sizeof key, &mode, &cull_empty, &scissor_empty,
        &x, &y, &w, &h, &vx, &vy, &vw, &vh);
    if (result != 0) {
        fprintf(stderr, "render-state query failed\n");
        return 1;
    }
    printf("key=%s\n", key);
    printf("cull=%d empty=%d scissor_empty=%d rect=%d,%d,%d,%d\n",
           mode, cull_empty, scissor_empty, x, y, w, h);
    printf("viewport=%.17g,%.17g,%.17g,%.17g\n", vx, vy, vw, vh);
    return 0;
}
