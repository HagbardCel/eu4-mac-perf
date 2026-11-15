/* In-process single-frame Metal shadow bridge. */

#import <Foundation/Foundation.h>

#include "eu4_gfx_shadow_bridge.h"
#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_metal_replay.h"

#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int g_enabled;
static int g_running;
static int g_done;
static uint64_t g_started_ns;

static uint64_t shadow_now_ns(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000000000ull + (uint64_t)spec.tv_nsec;
}

static uint64_t env_u64(const char *name, uint64_t fallback) {
    const char *text = getenv(name);
    if (text == NULL || text[0] == 0) {
        return fallback;
    }
    char *end = NULL;
    unsigned long long value = strtoull(text, &end, 10);
    if (end == text || *end != 0) {
        return fallback;
    }
    return (uint64_t)value;
}

static void default_paths(const eu4_frame_capture_descriptor *capture,
                          char *output, size_t output_size, char *shaders,
                          size_t shaders_size) {
    const char *configured_output = getenv("EU4_GFX_SHADOW_OUTPUT_DIR");
    if (configured_output != NULL && configured_output[0] == '/') {
        snprintf(output, output_size, "%s", configured_output);
    } else {
        char parent[PATH_MAX];
        snprintf(parent, sizeof(parent), "%s", capture->capture_dir);
        char *slash = strrchr(parent, '/');
        if (slash != NULL) {
            *slash = 0;
        }
        snprintf(output, output_size, "%s/shadow", parent);
    }
    const char *configured_shaders = getenv("EU4_GFX_SHADOW_SHADER_DIR");
    if (configured_shaders != NULL && configured_shaders[0] == '/') {
        snprintf(shaders, shaders_size, "%s", configured_shaders);
    } else {
        snprintf(shaders, shaders_size, "%s/shaders", output);
    }
}

static BOOL wait_for_file(const char *path, uint64_t timeout_ms) {
    uint64_t deadline = shadow_now_ns() + timeout_ms * 1000000ull;
    for (;;) {
        if (access(path, R_OK) == 0) {
            return YES;
        }
        if (shadow_now_ns() >= deadline) {
            return NO;
        }
        usleep(20000);
    }
}

static void write_report(NSString *path, NSDictionary *report) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:report
                                                   options:NSJSONWritingPrettyPrinted
                                                     error:&error];
    if (data == nil) {
        return;
    }
    [data writeToFile:path atomically:YES];
}

static int shadow_callback(const eu4_frame_capture_descriptor *capture,
                           void *context) {
    (void)context;
    if (g_done) {
        return 0;
    }
    if (g_running) {
        return 9;
    }
    g_running = 1;
    g_started_ns = shadow_now_ns();
    __block int status = 1;
    __block NSString *failure = @"";
    @autoreleasepool {
        @try {
            char output[PATH_MAX];
            char shaders[PATH_MAX];
            default_paths(capture, output, sizeof(output), shaders,
                          sizeof(shaders));
            NSString *output_path = @(output);
            NSString *shader_path = @(shaders);
            [[NSFileManager defaultManager]
                createDirectoryAtPath:output_path
          withIntermediateDirectories:YES
                           attributes:nil
                                error:NULL];
            [[NSFileManager defaultManager]
                createDirectoryAtPath:shader_path
          withIntermediateDirectories:YES
                           attributes:nil
                                error:NULL];
            char ready[PATH_MAX];
            snprintf(ready, sizeof(ready), "%s/ready", shaders);
            uint64_t wait_ms =
                env_u64("EU4_GFX_SHADOW_WAIT_MS", 120000);
            if (!wait_for_file(ready, wait_ms)) {
                failure = @"shader_translation_timeout";
            } else {
                eu4_metal_replay_config config;
                memset(&config, 0, sizeof(config));
                config.struct_size = (uint32_t)sizeof(config);
                config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
                config.frame_dir = capture->capture_dir;
                config.shader_dir = shaders;
                config.output_dir = output;
                config.stop_after = -1;
                config.only_event = -1;
                config.timeout_ms =
                    env_u64("EU4_GFX_SHADOW_TIMEOUT_MS", 120000);
                char create_error[512] = {0};
                eu4_metal_replay_session *session =
                    eu4_metal_replay_create(&config, create_error,
                                            sizeof(create_error));
                if (session == NULL) {
                    failure = [NSString stringWithFormat:
                                            @"metal_create_failed: %s",
                                            create_error];
                } else {
                    eu4_metal_replay_result result;
                    int code = eu4_metal_replay_frame(session, &config, &result);
                    NSMutableDictionary *report = [@{
                        @"schema": @"m7-shadow-report/v1",
                        @"mode": @"single-frame",
                        @"capture_frame_id":
                            @(capture->capture_frame_id),
                        @"pid": @(capture->pid),
                        @"session_id": @(capture->session_id),
                        @"boundary": @(capture->boundary),
                        @"capture_dir": @(capture->capture_dir),
                        @"shader_dir": shader_path,
                        @"gl_reference_sha256":
                            @(capture->reference_sha256),
                        @"trace_manifest_sha256":
                            @(capture->manifest_sha256),
                        @"operation_count":
                            @(capture->operation_count),
                        @"draw_count": @(capture->draw_count),
                        @"clear_count": @(capture->clear_count),
                        @"copy_count": @(capture->copy_count),
                        @"width": @(capture->width),
                        @"height": @(capture->height),
                        @"metal": @{
                            @"ok": @(result.ok),
                            @"code": @(code),
                            @"events": @(result.events),
                            @"encoded": @(result.encoded),
                            @"failed": @(result.failed),
                            @"readback_ok": @(result.readback_ok),
                            @"command_status": @(result.command_status),
                            @"duration_ns": @(result.duration_ns),
                            @"metal_frame_sha256":
                                @(result.metal_frame_sha256),
                            @"manifest_reference_sha256":
                                @(result.manifest_reference_sha256),
                            @"result_json": @(result.result_json),
                            @"metal_frame": @(result.metal_frame),
                            @"provenance_jsonl":
                                @(result.provenance_jsonl),
                            @"error": @(result.error),
                        },
                        @"status": result.ok ? @"ok" : @"failed",
                        @"error": @(result.error),
                        @"duration_ns": @(result.duration_ns),
                        @"total_duration_ns":
                            @(shadow_now_ns() - g_started_ns),
                    } mutableCopy];
                    if (!result.ok) {
                        failure = [NSString stringWithFormat:
                                                @"metal_replay_failed: %s",
                                                result.error];
                    } else {
                        status = 0;
                    }
                    write_report(
                        [output_path stringByAppendingPathComponent:
                                        @"shadow-report.json"],
                        report);
                    eu4_metal_replay_destroy(session);
                }
            }
            if (status != 0) {
                NSDictionary *report = @{
                    @"schema": @"m7-shadow-report/v1",
                    @"mode": @"single-frame",
                    @"capture_frame_id": @(capture->capture_frame_id),
                    @"pid": @(capture->pid),
                    @"session_id": @(capture->session_id),
                    @"capture_dir": @(capture->capture_dir),
                    @"gl_reference_sha256": @(capture->reference_sha256),
                    @"trace_manifest_sha256": @(capture->manifest_sha256),
                    @"status": @"failed",
                    @"error": failure,
                    @"total_duration_ns":
                        @(shadow_now_ns() - g_started_ns),
                };
                write_report(
                    [output_path stringByAppendingPathComponent:
                                    @"shadow-report.json"],
                    report);
            }
        } @catch (NSException *exception) {
            status = 2;
            failure = [NSString stringWithFormat:@"objective_c_exception: %@",
                                                 exception.reason ?: exception.name];
            const char *configured_output =
                getenv("EU4_GFX_SHADOW_OUTPUT_DIR");
            if (configured_output != NULL && configured_output[0] == '/') {
                NSString *path = [@(configured_output)
                    stringByAppendingPathComponent:@"shadow-report.json"];
                write_report(path, @{
                    @"schema": @"m7-shadow-report/v1",
                    @"mode": @"single-frame",
                    @"status": @"failed",
                    @"error": failure,
                    @"capture_frame_id": @(capture->capture_frame_id),
                });
            }
        }
    }
    g_running = 0;
    g_done = 1;
    return status;
}

int eu4_gfx_shadow_configure_from_env(void) {
    const char *mode = getenv("EU4_GFX_SHADOW_MODE");
    g_enabled = mode != NULL && strcmp(mode, "single-frame") == 0;
    g_running = 0;
    g_done = 0;
    if (!g_enabled) {
        return 0;
    }
    if (eu4_gfx_frame_sealed_subscribe(shadow_callback, NULL) != 0) {
        return -1;
    }
    return 1;
}

int eu4_gfx_shadow_enabled(void) { return g_enabled; }
