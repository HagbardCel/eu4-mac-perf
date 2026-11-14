/* Native host for the shared per-frame observation stream.

   Writes a deterministic sequence of small frames so a Python test can verify
   hashes, previews, gap accounting and on-demand ring dumps without EU4. */

#include "eu4_gfx_frame_observe.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void fill(uint8_t *pixels, int width, int height, int frame) {
    for (int y = 0; y < height; y++) {
        for (int x = 0; x < width; x++) {
            uint8_t *pixel = pixels + ((size_t)y * width + x) * 4;
            pixel[0] = (uint8_t)(frame * 7 + x);
            pixel[1] = (uint8_t)(frame * 11 + y);
            pixel[2] = (uint8_t)(frame * 13 + x * y);
            pixel[3] = 255;
        }
    }
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <dir> <mode>\n", argv[0]);
        return 2;
    }
    const char *mode = argv[2];
    if (strcmp(mode, "lockstep-timeout") == 0) {
        setenv("EU4_GFX_FRAME_LOCKSTEP", "1", 1);
        setenv("EU4_GFX_FRAME_LOCKSTEP_TIMEOUT_MS", "100", 1);
    }
    const int width = 8, height = 6;
    uint8_t pixels[8 * 6 * 4];
    if (strcmp(mode, "disabled") == 0) {
        printf("enabled=%d\n", eu4_gfx_frame_observe_enabled());
        return 0;
    }
    if (eu4_gfx_frame_observe_init("opengl", argv[1], "session-test") != 0) {
        fprintf(stderr, "init failed\n");
        return 3;
    }
    char request[1024];
    snprintf(request, sizeof request,
             "%s/capture-request.json", argv[1]);
    FILE *file = fopen(request, "wb");
    if (file != NULL) {
        fputs("{\"schema\":\"gfx-frame-request/v1\",\"frames\":[3,9]}\n", file);
        fclose(file);
    }
    int frames = strcmp(mode, "gap") == 0 ? 4 : 10;
    if (strcmp(mode, "lockstep-timeout") == 0) {
        eu4_gfx_frame_observe_note_draw(0xedfcd1);
        uint32_t draws = 0, distinct = 0;
        uint64_t signature = 0;
        eu4_gfx_frame_observe_take_stats(&draws, &signature, &distinct);
        if (eu4_gfx_frame_observe_emit_metadata(
                1728, 1117, "lower_left", 1, draws, signature, distinct, 1, "") != 0) {
            return 6;
        }
        printf("waited=%d\n", eu4_gfx_frame_observe_lockstep_wait(1, 50));
        return 0;
    }
    if (strcmp(mode, "metadata") == 0) {
        for (int index = 1; index <= 5; index++) {
            eu4_gfx_frame_observe_note_draw(0xedfcd1);
            uint32_t draws = 0, distinct = 0;
            uint64_t signature = 0;
            eu4_gfx_frame_observe_take_stats(&draws, &signature, &distinct);
            if (eu4_gfx_frame_observe_emit_metadata(
                    1728, 1117, "lower_left", (uint32_t)index, draws,
                    signature, distinct, 1, "") != 0) {
                return 5;
            }
        }
        printf("wants3=%d wants4=%d dropped=%u\n",
               eu4_gfx_frame_observe_wants(3),
               eu4_gfx_frame_observe_wants(4),
               eu4_gfx_frame_observe_dropped());
        return 0;
    }
    for (int index = 1; index <= frames; index++) {
        int logical = strcmp(mode, "gap") == 0 && index == 3 ? 5 : index;
        fill(pixels, width, height, logical);
        eu4_gfx_frame_observe_note_draw(0xedfcd1);
        eu4_gfx_frame_observe_note_draw(0xedfcd1);
        eu4_gfx_frame_observe_note_draw(0x14f6c1b);
        uint32_t draws = 0, distinct = 0;
        uint64_t signature = 0;
        eu4_gfx_frame_observe_take_stats(&draws, &signature, &distinct);
        int rc = eu4_gfx_frame_observe_emit(
            pixels, width, height, "lower_left", (uint32_t)logical,
            draws, signature, distinct, 1, 1, "");
        if (rc != 0) {
            fprintf(stderr, "emit %d failed\n", logical);
            return 4;
        }
    }
    printf("enabled=%d dropped=%u\n", eu4_gfx_frame_observe_enabled(),
           eu4_gfx_frame_observe_dropped());
    return 0;
}
