/* Thin command-line frontend for the reusable Metal replay core.
 *
 * The CLI contract is unchanged: --frame/--shaders/--out plus the optional
 * isolation, checkpoint and capture flags.  All renderer state and execution
 * live in eu4_gfx_frame_replay.mm; this file only parses arguments and prints
 * the replay report.
 */
#import <Foundation/Foundation.h>
#import <CommonCrypto/CommonDigest.h>

#include "eu4_gfx_metal_replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <string>

static bool sha256_file(NSString *path, NSString **out) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        return false;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(data.bytes, (CC_LONG)data.length, digest);
    NSMutableString *text = [NSMutableString string];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        [text appendFormat:@"%02x", digest[i]];
    }
    if (out != NULL) {
        *out = text;
    }
    return true;
}

static NSDictionary *read_json_document(NSString *path) {
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (data == nil) {
        return nil;
    }
    id value = [NSJSONSerialization JSONObjectWithData:data options:0
                                                error:NULL];
    return [value isKindOfClass:[NSDictionary class]] ? value : nil;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *frame_path = NULL;
        const char *shader_path = NULL;
        const char *output_path = NULL;
        const char *checkpoint_path = NULL;
        const char *sequence = NULL;
        const char *sequence_checkpoints = NULL;
        const char *control_dir = NULL;
        uint64_t control_timeout_ms = 120000;
        uint64_t frame_timeout_ms = 120000;
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
            } else if (strcmp(argv[index], "--sequence-checkpoints") == 0 &&
                       index + 1 < argc) {
                sequence_checkpoints = argv[++index];
            } else if (strcmp(argv[index], "--control-dir") == 0 &&
                       index + 1 < argc) {
                control_dir = argv[++index];
            } else if (strcmp(argv[index], "--control-timeout-ms") == 0 &&
                       index + 1 < argc) {
                control_timeout_ms = strtoull(argv[++index], NULL, 10);
            } else if (strcmp(argv[index], "--frame-timeout-ms") == 0 &&
                       index + 1 < argc) {
                frame_timeout_ms = strtoull(argv[++index], NULL, 10);
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
                        "sequence does not accept single-frame isolation/"
                        "checkpoint flags; use --sequence-checkpoints\n");
                return 2;
            }
            if (frame_timeout_ms == 0) {
                fprintf(stderr,
                        "sequence requires a positive --frame-timeout-ms\n");
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
                NSString *request_path = nil;
                NSString *request_sha = nil;
                if (sequence_checkpoints != NULL) {
                    NSString *source_dir = [@(sequence_checkpoints)
                        stringByAppendingPathComponent:
                            [NSString stringWithFormat:@"frame-%03d", index]];
                    NSString *source_request = [source_dir
                        stringByAppendingPathComponent:
                            @"checkpoint-request.json"];
                    NSString *source_receipt = [source_dir
                        stringByAppendingPathComponent:@"shader-receipt.json"];
                    if (![[NSFileManager defaultManager]
                            fileExistsAtPath:source_request] ||
                        ![[NSFileManager defaultManager]
                            fileExistsAtPath:source_receipt]) {
                        fprintf(stderr,
                                "sequence frame %d has no published "
                                "checkpoint request/receipt\n", index);
                        result = 5;
                        break;
                    }
                    request_path = [@(output)
                        stringByAppendingPathComponent:
                            @"checkpoint-request.json"];
                    NSString *output_receipt = [@(output)
                        stringByAppendingPathComponent:@"shader-receipt.json"];
                    [[NSFileManager defaultManager] removeItemAtPath:
                        request_path error:NULL];
                    [[NSFileManager defaultManager] removeItemAtPath:
                        output_receipt error:NULL];
                    if (![[NSFileManager defaultManager]
                            copyItemAtPath:source_request
                                    toPath:request_path error:NULL] ||
                        ![[NSFileManager defaultManager]
                            copyItemAtPath:source_receipt
                                    toPath:output_receipt error:NULL]) {
                        fprintf(stderr,
                                "sequence frame %d checkpoint publication "
                                "failed\n", index);
                        result = 5;
                        break;
                    }
                    if (!sha256_file(request_path, &request_sha)) {
                        fprintf(stderr,
                                "sequence frame %d request digest failed\n",
                                index);
                        result = 5;
                        break;
                    }
                }
                eu4_metal_replay_config config;
                memset(&config, 0, sizeof(config));
                config.struct_size = (uint32_t)sizeof(config);
                config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
                config.frame_dir = frame;
                config.shader_dir = effective_shader_dir;
                config.output_dir = output;
                config.checkpoint_path =
                    request_path != nil ? request_path.UTF8String : NULL;
                config.stop_after = -1;
                config.only_event = -1;
                config.frame_sequence = (uint64_t)index;
                config.persistent_history = 1;
                config.timeout_ms = frame_timeout_ms;
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
                if (sequence_checkpoints != NULL && request_path != nil) {
                    /* Publish the established shadow-report contract for the
                       offline sequence so PR3 diagnosis can verify the sealed
                       execution identity and request binding without a live
                       coordinator. */
                    NSDictionary *seal = read_json_document(
                        [@(frame) stringByAppendingPathComponent:
                            @"frame-seal.json"]);
                    if (seal != nil) {
                        NSMutableDictionary *report = [@{
                            @"schema": @"m7-shadow-report/v1",
                            @"mode": @"continuous-sequence",
                            @"capture_frame_id":
                                seal[@"capture_frame_id"] ?: @0,
                            @"pid": seal[@"pid"] ?: @0,
                            @"session_id": seal[@"session_id"] ?: @"",
                            @"boundary": seal[@"boundary"] ?: @0,
                            @"capture_dir": @(frame),
                            @"shader_dir": @(effective_shader_dir),
                            @"gl_reference_sha256":
                                seal[@"reference_sha256"] ?: @"",
                            @"trace_manifest_sha256":
                                seal[@"manifest_sha256"] ?: @"",
                            @"operation_count":
                                seal[@"operation_count"] ?: @0,
                            @"draw_count": seal[@"draw_count"] ?: @0,
                            @"clear_count": seal[@"clear_count"] ?: @0,
                            @"copy_count": seal[@"copy_count"] ?: @0,
                            @"status": frame_result.ok ? @"ok" : @"failed",
                            @"stage": frame_result.ok ? @"complete"
                                                      : @"metal_replay",
                            @"error": frame_result.ok
                                ? @"" : @(frame_result.error),
                            @"checkpoint_request_file":
                                request_path.lastPathComponent,
                            @"checkpoint_request_sha256": request_sha ?: @"",
                            @"sequence": @(index),
                        } mutableCopy];
                        NSData *report_json = [NSJSONSerialization
                            dataWithJSONObject:report options:0 error:NULL];
                        [report_json writeToFile:[@(output)
                            stringByAppendingPathComponent:
                                @"shadow-report.json"] atomically:YES];
                    }
                }
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
                                id decided_sequence = value[@"sequence"];
                                if (decided_sequence != nil &&
                                    ![decided_sequence isKindOfClass:
                                        [NSNumber class]]) {
                                    fprintf(stderr,
                                            "control decision sequence is "
                                            "invalid\n");
                                    result = 4;
                                    decided = -1;
                                    break;
                                }
                                if (decided_sequence != nil &&
                                    [decided_sequence intValue] != index) {
                                    fprintf(stderr,
                                            "control decision belongs to "
                                            "frame %d, not %d\n",
                                            [decided_sequence intValue],
                                            index);
                                    result = 4;
                                    decided = -1;
                                    break;
                                }
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
