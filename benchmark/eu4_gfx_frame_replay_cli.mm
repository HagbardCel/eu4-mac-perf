/* Thin command-line frontend for the reusable Metal replay core.
 *
 * The CLI contract is unchanged: --frame/--shaders/--out plus the optional
 * isolation, checkpoint and capture flags.  All renderer state and execution
 * live in eu4_gfx_frame_replay.mm; this file only parses arguments and prints
 * the replay report.
 */
#import <Foundation/Foundation.h>

#include "eu4_gfx_metal_replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *frame_path = NULL;
        const char *shader_path = NULL;
        const char *output_path = NULL;
        const char *checkpoint_path = NULL;
        int stop_after = -1;
        int only_event = -1;
        int seed_marked = 0;
        int capture_post_images = 0;
        int capture_pre_images = 0;
        int depth_from_prefix = 0;
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "--frame") == 0 && index + 1 < argc) {
                frame_path = argv[++index];
            } else if (strcmp(argv[index], "--shaders") == 0 &&
                       index + 1 < argc) {
                shader_path = argv[++index];
            } else if (strcmp(argv[index], "--out") == 0 && index + 1 < argc) {
                output_path = argv[++index];
            } else if (strcmp(argv[index], "--stop-after") == 0 &&
                       index + 1 < argc) {
                stop_after = atoi(argv[++index]);
            } else if (strcmp(argv[index], "--only-event") == 0 &&
                       index + 1 < argc) {
                only_event = atoi(argv[++index]);
            } else if (strcmp(argv[index], "--seed-marked") == 0) {
                seed_marked = 1;
            } else if (strcmp(argv[index], "--capture-post") == 0) {
                capture_post_images = 1;
            } else if (strcmp(argv[index], "--capture-pre") == 0) {
                capture_pre_images = 1;
            } else if (strcmp(argv[index], "--depth-from-prefix") == 0) {
                depth_from_prefix = 1;
            } else if (strcmp(argv[index], "--checkpoints") == 0 &&
                       index + 1 < argc) {
                checkpoint_path = argv[++index];
            }
        }
        if (frame_path == NULL || shader_path == NULL || output_path == NULL) {
            fprintf(stderr,
                    "usage: %s --frame <dir> --shaders <dir> --out <dir> "
                    "[--stop-after N]\n",
                    argv[0]);
            return 2;
        }
        eu4_metal_replay_config config;
        memset(&config, 0, sizeof(config));
        config.struct_size = (uint32_t)sizeof(config);
        config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
        config.frame_dir = frame_path;
        config.shader_dir = shader_path;
        config.output_dir = output_path;
        config.checkpoint_path = checkpoint_path;
        config.stop_after = (int32_t)stop_after;
        config.only_event = (int32_t)only_event;
        config.seed_marked = (uint32_t)seed_marked;
        config.capture_post_images = (uint32_t)capture_post_images;
        config.capture_pre_images = (uint32_t)capture_pre_images;
        config.depth_from_prefix = (uint32_t)depth_from_prefix;
        char create_error[512] = {0};
        eu4_metal_replay_session *session =
            eu4_metal_replay_create(&config, create_error, sizeof(create_error));
        if (session == NULL) {
            fprintf(stderr, "create: %s\n", create_error);
            return 3;
        }
        eu4_metal_replay_result result;
        int code = eu4_metal_replay_frame(session, &config, &result);
        if (result.result_json[0] != 0) {
            NSData *data = [NSData dataWithContentsOfFile:@(result.result_json)];
            if (data != nil) {
                NSString *text = [[NSString alloc] initWithData:data
                                                       encoding:NSUTF8StringEncoding];
                if (text != nil) {
                    printf("%s\n", text.UTF8String);
                }
            }
        }
        if (!result.ok) {
            fprintf(stderr, "replay: %s\n",
                    result.error[0] != 0 ? result.error
                                         : eu4_metal_replay_last_error(session));
        }
        eu4_metal_replay_destroy(session);
        return result.ok ? 0 : (code != 0 ? code : 1);
    }
}
