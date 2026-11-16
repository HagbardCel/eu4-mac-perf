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
#include <time.h>
#include <unistd.h>
#include <string>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *frame_path = NULL;
        const char *shader_path = NULL;
        const char *output_path = NULL;
        const char *checkpoint_path = NULL;
        const char *sequence = NULL;
        const char *control_dir = NULL;
        uint64_t control_timeout_ms = 120000;
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
            } else if (strcmp(argv[index], "--sequence") == 0 &&
                       index + 1 < argc) {
                sequence = argv[++index];
            } else if (strcmp(argv[index], "--control-dir") == 0 &&
                       index + 1 < argc) {
                control_dir = argv[++index];
            } else if (strcmp(argv[index], "--control-timeout-ms") == 0 &&
                       index + 1 < argc) {
                control_timeout_ms = strtoull(argv[++index], NULL, 10);
            }
        }
        if (sequence != NULL) {
            if (shader_path == NULL || output_path == NULL) {
                fprintf(stderr, "sequence requires --shaders and --out\n");
                return 2;
            }
            if (stop_after >= 0 || only_event >= 0 || checkpoint_path != NULL ||
                seed_marked || capture_post_images || capture_pre_images ||
                depth_from_prefix) {
                fprintf(stderr,
                        "sequence does not accept isolation/checkpoint flags\n");
                return 2;
            }
            char *frames = strdup(sequence);
            if (frames == NULL) {
                return 2;
            }
            eu4_metal_replay_session *session = NULL;
            int result = 0;
            int index = 0;
            char *save = NULL;
            for (char *frame = strtok_r(frames, ",", &save); frame != NULL;
                 frame = strtok_r(NULL, ",", &save)) {
                index++;
                char output[1024];
                snprintf(output, sizeof(output), "%s/out-%03d",
                         output_path, index);
                [[NSFileManager defaultManager]
                    createDirectoryAtPath:@(output)
              withIntermediateDirectories:YES
                               attributes:nil
                                    error:NULL];
                char frame_shader_dir[1024];
                snprintf(frame_shader_dir, sizeof(frame_shader_dir),
                         "%s/shaders", frame);
                const char *effective_shader_dir =
                    access((std::string(frame_shader_dir) + "/shaders.json").c_str(),
                           R_OK) == 0
                        ? frame_shader_dir : shader_path;
                eu4_metal_replay_config config;
                memset(&config, 0, sizeof(config));
                config.struct_size = (uint32_t)sizeof(config);
                config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
                config.frame_dir = frame;
                config.shader_dir = effective_shader_dir;
                config.output_dir = output;
                config.stop_after = -1;
                config.only_event = -1;
                config.frame_sequence = (uint64_t)index;
                config.persistent_history = 1;
                char create_error[512] = {0};
                if (session == NULL) {
                    session = eu4_metal_replay_create(
                        &config, create_error, sizeof(create_error));
                    if (session == NULL) {
                        fprintf(stderr, "create: %s\n", create_error);
                        result = 3;
                        break;
                    }
                }
                eu4_metal_replay_result frame_result;
                int code = eu4_metal_replay_frame(session, &config,
                                                  &frame_result);
                NSMutableDictionary *document = [@{
                    @"sequence": @(index),
                    @"frame_dir": @(frame),
                    @"output_dir": @(output),
                    @"code": @(code),
                    @"ok": @(frame_result.ok),
                    @"events": @(frame_result.events),
                    @"encoded": @(frame_result.encoded),
                    @"failed": @(frame_result.failed),
                    @"history_status": @(frame_result.history_status),
                    @"carried_bindings": @(frame_result.carried_bindings),
                    @"missing_history": @(frame_result.missing_history),
                    @"stale_history": @(frame_result.stale_history),
                    @"duration_ns": @(frame_result.duration_ns),
                    @"error": @(frame_result.error),
                    @"result_json": @(frame_result.result_json),
                    @"metal_frame_sha256":
                        @(frame_result.metal_frame_sha256),
                } mutableCopy];
                if (frame_result.result_json[0] != 0) {
                    NSData *data = [NSData dataWithContentsOfFile:
                                        @(frame_result.result_json)];
                    id report = data != nil
                        ? [NSJSONSerialization JSONObjectWithData:data
                                                           options:0
                                                             error:NULL]
                        : nil;
                    if ([report isKindOfClass:[NSDictionary class]]) {
                        document[@"stage"] = report[@"stage"] ?: @"";
                        document[@"failure"] = report[@"failure"] ?: @{};
                        document[@"history"] = report[@"history"] ?: @{};
                    }
                }
                NSData *json = [NSJSONSerialization dataWithJSONObject:document
                                                               options:0
                                                                 error:NULL];
                if (json != nil) {
                    printf("%s\n", [[[NSString alloc]
                        initWithData:json encoding:NSUTF8StringEncoding]
                        UTF8String]);
                }
                fflush(stdout);
                if (!frame_result.ok && result == 0) {
                    result = code != 0 ? code : 1;
                    break;
                }
                if (control_dir != NULL && frame_result.ok) {
                    char decision_path[1024];
                    snprintf(decision_path, sizeof(decision_path),
                             "%s/decision", control_dir);
                    uint64_t deadline = 0;
                    struct timespec now;
                    clock_gettime(CLOCK_MONOTONIC, &now);
                    deadline = (uint64_t)now.tv_sec * 1000ull +
                               (uint64_t)now.tv_nsec / 1000000ull +
                               control_timeout_ms;
                    int decided = 0;
                    while (!decided) {
                        NSData *data = [NSData dataWithContentsOfFile:
                                            @(decision_path)];
                        if (data != nil) {
                            id value = [NSJSONSerialization
                                JSONObjectWithData:data options:0 error:NULL];
                            if ([value isKindOfClass:[NSDictionary class]] &&
                                [value[@"continue"] isKindOfClass:
                                    [NSNumber class]]) {
                                if (![value[@"continue"] boolValue]) {
                                    result = 0;
                                    decided = -1;
                                } else {
                                    decided = 1;
                                }
                            }
                        }
                        if (decided != 0) {
                            break;
                        }
                        clock_gettime(CLOCK_MONOTONIC, &now);
                        uint64_t current = (uint64_t)now.tv_sec * 1000ull +
                                           (uint64_t)now.tv_nsec / 1000000ull;
                        if (current >= deadline) {
                            fprintf(stderr, "control decision timed out\n");
                            result = 4;
                            decided = -1;
                            break;
                        }
                        usleep(10000);
                    }
                    unlink(decision_path);
                    if (decided < 0) {
                        break;
                    }
                }
            }
            eu4_metal_replay_destroy(session);
            free(frames);
            return result;
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
