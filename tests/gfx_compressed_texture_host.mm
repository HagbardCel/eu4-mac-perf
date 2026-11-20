/* Calls the production compressed-texture selection/upload path on a synthetic
 * texture dictionary.  No EU4 process or frame capture is involved. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <stdio.h>

extern "C" int eu4_gfx_frame_replay_test_compressed_texture(
    const char *frame_dir, const char *json_path, int *out_format,
    int *out_levels, char *out_error, size_t error_capacity);

int main(int argc, char **argv) {
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
