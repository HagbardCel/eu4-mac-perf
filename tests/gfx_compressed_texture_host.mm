/* Calls the production compressed-texture selection/upload path on a synthetic
 * texture dictionary.  No EU4 process or frame capture is involved. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdio.h>

extern "C" int eu4_gfx_frame_replay_test_compressed_texture(
    const char *frame_dir, const char *json_path, int *out_format,
    int *out_levels, char *out_error, size_t error_capacity);
extern "C" int eu4_gfx_frame_replay_test_sample_texture(
    const char *frame_dir, const char *json_path, unsigned char *out_pixels,
    int width, int height, char *out_error, size_t error_capacity);

int main(int argc, char **argv) {
    if (argc == 4 && strcmp(argv[1], "--sample") == 0) {
        unsigned char pixels[8 * 8 * 4] = {0};
        char error[2048] = "";
        int result = eu4_gfx_frame_replay_test_sample_texture(
            argv[2], argv[3], pixels, 8, 8, error, sizeof error);
        if (result != 0) {
            fprintf(stderr, "%s\n", error[0] ? error : "sampling failed");
            return 1;
        }
        for (int index = 0; index < 8 * 8; index++) {
            printf("%d,%d,%d,%d\n", pixels[index * 4],
                   pixels[index * 4 + 1], pixels[index * 4 + 2],
                   pixels[index * 4 + 3]);
        }
        return 0;
    }
    if (argc != 3) {
        fprintf(stderr, "usage: %s <frame-dir> <texture.json>\n", argv[0]);
        return 2;
    }
    int format = 0;
    int levels = 0;
    char error[2048] = "";
    int result = eu4_gfx_frame_replay_test_compressed_texture(
        argv[1], argv[2], &format, &levels, error, sizeof error);
    if (result != 0) {
        fprintf(stderr, "%s\n", error[0] ? error : "texture build failed");
        return 1;
    }
    printf("format=%d levels=%d\n", format, levels);
    return 0;
}
