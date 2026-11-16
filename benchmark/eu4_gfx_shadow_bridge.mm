/* In-process single-frame Metal shadow bridge. */

#import <Foundation/Foundation.h>

#include "eu4_gfx_shadow_bridge.h"
#include "eu4_gfx_frame_trace.h"
#include "eu4_gfx_metal_replay.h"

#include <CommonCrypto/CommonDigest.h>
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

static BOOL write_report(NSString *path, NSDictionary *report) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:report
                                                   options:NSJSONWritingPrettyPrinted
                                                     error:&error];
    if (data == nil) {
        return NO;
    }
    return [data writeToFile:path atomically:YES];
}


static BOOL shadow_sha256_file(const char *path, char out[65]) {
    out[0] = 0;
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NO;
    }
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    uint8_t buffer[64 * 1024];
    size_t read_bytes = 0;
    while ((read_bytes = fread(buffer, 1, sizeof(buffer), file)) > 0) {
        CC_SHA256_Update(&context, buffer, (CC_LONG)read_bytes);
    }
    BOOL failed = ferror(file) != 0;
    fclose(file);
    if (failed) {
        return NO;
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    static const char hex[] = "0123456789abcdef";
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0xf];
    }
    out[64] = 0;
    return YES;
}

static NSString *shadow_artifact_digest(NSString *directory) {
    NSFileManager *manager = [NSFileManager defaultManager];
    NSArray<NSString *> *paths =
        [[manager subpathsAtPath:directory] sortedArrayUsingSelector:
                                                   @selector(compare:)];
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    for (NSString *relative in paths) {
        NSString *name = relative.lastPathComponent;
        if ([name isEqualToString:@"ready"] ||
            [name isEqualToString:@"shader-receipt.json"]) {
            continue;
        }
        NSString *full = [directory stringByAppendingPathComponent:relative];
        BOOL is_directory = NO;
        if (![manager fileExistsAtPath:full isDirectory:&is_directory] ||
            is_directory) {
            continue;
        }
        char sha[65] = {0};
        if (!shadow_sha256_file(full.fileSystemRepresentation, sha)) {
            return nil;
        }
        NSData *relative_data = [relative dataUsingEncoding:NSUTF8StringEncoding];
        CC_SHA256_Update(&context, relative_data.bytes,
                         (CC_LONG)relative_data.length);
        CC_SHA256_Update(&context, "\0", 1);
        CC_SHA256_Update(&context, sha, 64);
        CC_SHA256_Update(&context, "\n", 1);
    }
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    static const char hex[] = "0123456789abcdef";
    char output[65];
    for (int i = 0; i < CC_SHA256_DIGEST_LENGTH; i++) {
        output[i * 2] = hex[digest[i] >> 4];
        output[i * 2 + 1] = hex[digest[i] & 0xf];
    }
    output[64] = 0;
    return @(output);
}

static BOOL shadow_verify_receipt(
    const eu4_frame_capture_descriptor *capture, NSString *shadow_dir,
    NSString *shader_dir, char shader_json_sha[65], NSString **failure) {
    NSString *receipt_path =
        [shadow_dir stringByAppendingPathComponent:@"shader-receipt.json"];
    NSData *data = [NSData dataWithContentsOfFile:receipt_path];
    NSDictionary *receipt =
        data != nil ? [NSJSONSerialization JSONObjectWithData:data options:0
                                                       error:NULL]
                    : nil;
    if (![receipt isKindOfClass:[NSDictionary class]] ||
        ![receipt[@"schema"] isEqualToString:@"m7-shadow-shader-receipt/v1"]) {
        *failure = @"missing or invalid shader receipt";
        return NO;
    }
    if (![receipt[@"capture_frame_id"] isEqual:@(capture->capture_frame_id)] ||
        ![receipt[@"pid"] isEqual:@(capture->pid)] ||
        ![receipt[@"session_id"] isEqual:@(capture->session_id)] ||
        ![receipt[@"trace_manifest_sha256"]
            isEqualToString:@(capture->manifest_sha256)] ||
        ![receipt[@"event_stream_sha256"]
            isEqualToString:@(capture->event_stream_sha256)] ||
        ![receipt[@"event_stream_bytes"] isEqual:@(capture->event_stream_bytes)]) {
        *failure = @"shader receipt identity mismatch";
        return NO;
    }
    NSString *shaders_json =
        [shader_dir stringByAppendingPathComponent:@"shaders.json"];
    char sha[65] = {0};
    if (!shadow_sha256_file(shaders_json.fileSystemRepresentation, sha) ||
        ![receipt[@"shaders_json_sha256"] isEqualToString:@(sha)]) {
        *failure = @"shaders.json digest mismatch";
        return NO;
    }
    NSString *artifacts = shadow_artifact_digest(shader_dir);
    if (artifacts == nil || ![receipt[@"artifacts_sha256"] isEqualToString:artifacts]) {
        *failure = @"shader artifact digest mismatch";
        return NO;
    }
    snprintf(shader_json_sha, 65, "%s", sha);
    return YES;
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
    __block eu4_metal_replay_session *active_session = NULL;
    __block BOOL detailed_report_written = NO;
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
                char shader_json_sha[65] = {0};
                if (!shadow_verify_receipt(capture, output_path, shader_path,
                                           shader_json_sha, &failure)) {
                    failure = [NSString stringWithFormat:
                                            @"shader_receipt_failed: %@",
                                            failure];
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
                config.capture_frame_id = capture->capture_frame_id;
                config.session_id = capture->session_id;
                config.expected_event_stream_sha256 =
                    capture->event_stream_sha256;
                config.expected_event_stream_bytes =
                    capture->event_stream_bytes;
                config.expected_manifest_sha256 = capture->manifest_sha256;
                config.expected_reference_sha256 = capture->reference_sha256;
                config.expected_shader_json_sha256 = shader_json_sha;
                char create_error[512] = {0};
                active_session = eu4_metal_replay_create(
                    &config, create_error, sizeof(create_error));
                if (active_session == NULL) {
                    failure = [NSString stringWithFormat:
                                            @"metal_create_failed: %s",
                                            create_error];
                } else {
                    eu4_metal_replay_result result;
                    int code = eu4_metal_replay_frame(active_session, &config,
                                                      &result);
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
                            @"timed_out": @(result.timed_out),
                            @"partial": @(result.partial),
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
                        @"stage": result.ok ? @"complete" : @"metal_replay",
                        @"qualified": @NO,
                        @"error": @(result.error),
                        @"duration_ns": @(result.duration_ns),
                        @"total_duration_ns":
                            @(shadow_now_ns() - g_started_ns),
                    } mutableCopy];
                    if (!result.ok) {
                        failure = [NSString stringWithFormat:
                                                @"metal_replay_failed: %s",
                                                result.error];
                    }
                    BOOL report_written = write_report(
                        [output_path stringByAppendingPathComponent:
                                        @"shadow-report.json"],
                        report);
                    detailed_report_written = report_written;
                    if (result.ok && report_written) {
                        status = 0;
                    } else if (result.ok) {
                        status = 1;
                        failure = @"shadow_report_write_failed";
                    }
                }
                }
            }
            if (status != 0 && !detailed_report_written) {
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
                    @"stage": ({
                        NSString *stage = @"shader_wait";
                        if (status == 2) {
                            stage = @"objective_c_exception";
                        } else if ([failure hasPrefix:@"shader_receipt_failed"]) {
                            stage = @"shader_receipt";
                        } else if ([failure hasPrefix:@"metal_create_failed"]) {
                            stage = @"metal_create";
                        } else if ([failure hasPrefix:@"metal_replay_failed"]) {
                            stage = @"metal_replay";
                        } else if ([failure isEqualToString:@"shadow_report_write_failed"]) {
                            stage = @"report_write";
                        }
                        stage;
                    }),
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
        } @finally {
            if (active_session != NULL) {
                eu4_metal_replay_destroy(active_session);
                active_session = NULL;
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
    const char *wait = getenv("EU4_GFX_SHADOW_WAIT_MS");
    const char *timeout = getenv("EU4_GFX_SHADOW_TIMEOUT_MS");
    char *end = NULL;
    unsigned long long wait_value = wait != NULL ? strtoull(wait, &end, 10) : 120000;
    if (wait != NULL && (end == wait || *end != 0)) {
        return -1;
    }
    end = NULL;
    unsigned long long timeout_value =
        timeout != NULL ? strtoull(timeout, &end, 10) : 120000;
    if (timeout != NULL && (end == timeout || *end != 0)) {
        return -1;
    }
    if (wait_value == 0 || wait_value > 3600000ull || timeout_value == 0 ||
        timeout_value > 3600000ull) {
        return -1;
    }
    if (eu4_gfx_frame_sealed_subscribe(shadow_callback, NULL) != 0) {
        return -1;
    }
    return 1;
}

int eu4_gfx_shadow_enabled(void) { return g_enabled; }
