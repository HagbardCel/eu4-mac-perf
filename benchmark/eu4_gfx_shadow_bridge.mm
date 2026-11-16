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

/* The C ABI keeps its v2 layout; replay.json carries the structured failure
   and the error string starts with the same stage.  Read both so the shadow
   report never degrades a specific native failure to "metal_replay". */
static NSDictionary *shadow_native_failure(const eu4_metal_replay_result *result,
                                           NSString **stage_out) {
    NSDictionary *document = nil;
    if (result->result_json[0] != 0) {
        NSData *data = [NSData dataWithContentsOfFile:@(result->result_json)];
        id value = data != nil
            ? [NSJSONSerialization JSONObjectWithData:data options:0 error:NULL]
            : nil;
        if ([value isKindOfClass:[NSDictionary class]]) {
            document = value;
        }
    }
    NSMutableDictionary *failure = [NSMutableDictionary dictionary];
    id structured = document[@"failure"];
    if ([structured isKindOfClass:[NSDictionary class]]) {
        [failure addEntriesFromDictionary:structured];
    }
    NSString *stage = document[@"stage"];
    if (stage.length == 0) {
        stage = failure[@"stage"];
    }
    NSString *error_text = @(result->error);
    NSInteger operation_index = -1;
    if (stage.length == 0 && error_text.length > 0) {
        NSRange colon = [error_text rangeOfString:@":"];
        NSString *prefix = colon.location != NSNotFound
            ? [error_text substringToIndex:colon.location]
            : error_text;
        NSRange operation = [prefix rangeOfString:@" (operation "];
        if (operation.location != NSNotFound) {
            stage = [prefix substringToIndex:operation.location];
            NSString *tail = [prefix substringFromIndex:NSMaxRange(operation)];
            NSRange close = [tail rangeOfString:@")"];
            if (close.location != NSNotFound) {
                NSString *digits = [tail substringToIndex:close.location];
                NSScanner *scanner = [NSScanner scannerWithString:digits];
                NSInteger parsed = -1;
                if ([scanner scanInteger:&parsed] && scanner.isAtEnd &&
                    parsed >= 0) {
                    operation_index = parsed;
                }
            }
        } else {
            stage = prefix;
        }
    }
    if (stage.length == 0) {
        stage = @"metal_replay";
    }
    if (failure.count == 0) {
        failure[@"stage"] = stage;
        failure[@"operation"] = @(operation_index);
        failure[@"event"] = @(operation_index);
        failure[@"kind"] = @"frame";
        failure[@"timed_out"] = @(result->timed_out != 0);
        failure[@"command_status"] = @(result->command_status);
        failure[@"error"] = error_text.length > 0
            ? error_text
            : @"Metal replay failed";
    }
    if (stage_out != NULL) {
        *stage_out = stage;
    }
    return failure;
}

static NSDictionary *shadow_failure_object(NSString *stage, NSString *error,
                                           NSInteger operation, NSString *kind,
                                           BOOL timed_out,
                                           NSInteger command_status) {
    return @{
        @"stage": stage.length > 0 ? stage : @"metal_replay",
        @"operation": @(operation),
        @"event": @(operation),
        @"kind": kind.length > 0 ? kind : @"frame",
        @"caller": @"",
        @"error": error.length > 0 ? error : @"Metal replay failed",
        @"timed_out": @(timed_out),
        @"command_status": @(command_status),
    };
}

/* Authoritative capture identity shared by every report path.  Normal replay,
   shader-receipt/session-creation failures and Objective-C exceptions must all
   carry the same sealed identity so the coordinator can distinguish a genuine
   provenance mismatch from an execution failure. */
static NSMutableDictionary *shadow_report_base(
    const eu4_frame_capture_descriptor *capture, NSString *shader_path) {
    return [@{
        @"schema": @"m7-shadow-report/v1",
        @"mode": @"single-frame",
        @"capture_frame_id": @(capture->capture_frame_id),
        @"pid": @(capture->pid),
        @"session_id": @(capture->session_id),
        @"boundary": @(capture->boundary),
        @"capture_dir": @(capture->capture_dir),
        @"shader_dir": shader_path.length > 0 ? shader_path : @"",
        @"gl_reference_sha256": @(capture->reference_sha256),
        @"trace_manifest_sha256": @(capture->manifest_sha256),
        @"operation_count": @(capture->operation_count),
        @"draw_count": @(capture->draw_count),
        @"clear_count": @(capture->clear_count),
        @"copy_count": @(capture->copy_count),
        @"width": @(capture->width),
        @"height": @(capture->height),
        @"qualified": @NO,
        @"status": @"failed",
        @"stage": @"shader_wait",
        @"error": @"",
        @"failure": @{},
    } mutableCopy];
}

static NSDictionary *shadow_report_for_failure(
    const eu4_frame_capture_descriptor *capture, NSString *shader_path,
    NSString *stage, NSString *error, BOOL timed_out, uint64_t started_ns,
    NSString *checkpoint_file, NSString *checkpoint_sha) {
    NSMutableDictionary *report = shadow_report_base(capture, shader_path);
    NSString *stage_text = stage.length > 0 ? stage : @"metal_replay";
    NSString *error_text = error.length > 0 ? error : @"Metal replay failed";
    report[@"status"] = @"failed";
    report[@"stage"] = stage_text;
    report[@"error"] = error_text;
    report[@"timed_out"] = @(timed_out);
    report[@"failure"] = shadow_failure_object(stage_text, error_text, -1,
                                               @"frame", timed_out, 0);
    report[@"total_duration_ns"] = @(shadow_now_ns() - started_ns);
    report[@"checkpoint_request_file"] = checkpoint_file ?: @"";
    report[@"checkpoint_request_sha256"] = checkpoint_sha ?: @"";
    return report;
}

static NSDictionary *shadow_report_for_exception(
    const eu4_frame_capture_descriptor *capture, NSString *shader_path,
    NSString *reason, uint64_t started_ns) {
    NSString *description = reason.length > 0
        ? reason
        : @"unknown Objective-C exception";
    return shadow_report_for_failure(capture, shader_path,
                                     @"internal_exception", description, NO,
                                     started_ns, @"", @"");
}

static NSDictionary *shadow_report_for_replay(
    const eu4_frame_capture_descriptor *capture, NSString *shader_path,
    const eu4_metal_replay_result *result, int code, uint64_t started_ns,
    NSString *checkpoint_file, NSString *checkpoint_sha) {
    NSString *native_stage = @"complete";
    NSDictionary *native_failure = @{};
    NSString *native_error = @"";
    if (!result->ok) {
        native_failure = shadow_native_failure(result, &native_stage);
        /* Prefer the C ABI's stage-prefixed error; replay.json still carries
           the structured operation/detail. */
        native_error = result->error[0] != 0
            ? @(result->error)
            : native_failure[@"error"];
        if (native_error.length == 0) {
            native_error = @"Metal replay failed";
        }
        if (native_stage.length == 0) {
            native_stage = @"metal_replay";
        }
    }
    NSMutableDictionary *report = shadow_report_base(capture, shader_path);
    report[@"status"] = result->ok ? @"ok" : @"failed";
    report[@"stage"] = result->ok ? @"complete" : native_stage;
    report[@"error"] = result->ok ? @"" : native_error;
    report[@"failure"] = result->ok ? @{} : native_failure;
    report[@"timed_out"] = @(result->timed_out);
    report[@"command_status"] = @(result->command_status);
    report[@"duration_ns"] = @(result->duration_ns);
    report[@"total_duration_ns"] = @(shadow_now_ns() - started_ns);
    report[@"checkpoint_request_file"] = checkpoint_file ?: @"";
    report[@"checkpoint_request_sha256"] = checkpoint_sha ?: @"";
    report[@"metal"] = @{
        @"ok": @(result->ok),
        @"code": @(code),
        @"events": @(result->events),
        @"encoded": @(result->encoded),
        @"failed": @(result->failed),
        @"timed_out": @(result->timed_out),
        @"partial": @(result->partial),
        @"readback_ok": @(result->readback_ok),
        @"command_status": @(result->command_status),
        @"duration_ns": @(result->duration_ns),
        @"metal_frame_sha256": @(result->metal_frame_sha256),
        @"manifest_reference_sha256": @(result->manifest_reference_sha256),
        @"result_json": @(result->result_json),
        @"metal_frame": @(result->metal_frame),
        @"provenance_jsonl": @(result->provenance_jsonl),
        @"error": result->ok ? @"" : native_error,
        @"stage": result->ok ? @"complete" : native_stage,
        @"failure": result->ok ? @{} : native_failure,
    };
    return report;
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
    NSString *shader_dir, char shader_json_sha[65],
    char checkpoint_request_name[256], char checkpoint_request_sha[65],
    NSString **failure) {
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
    id request_name = receipt[@"checkpoint_request_file"];
    if (![request_name isKindOfClass:[NSString class]] ||
        [request_name length] == 0 || [request_name length] >= 256 ||
        [request_name containsString:@"/"] ||
        [request_name isEqualToString:@".."] ||
        [request_name isEqualToString:@"."]) {
        *failure = @"checkpoint request file is missing or invalid";
        return NO;
    }
    NSString *request_path = [shadow_dir stringByAppendingPathComponent:request_name];
    NSData *request_data = [NSData dataWithContentsOfFile:request_path];
    NSDictionary *request = request_data != nil
        ? [NSJSONSerialization JSONObjectWithData:request_data options:0
                                           error:NULL]
        : nil;
    if (![request isKindOfClass:[NSDictionary class]] ||
        ![request[@"schema"] isEqualToString:@"gfx-frame-checkpoint-request/v1"] ||
        ![request[@"checkpoints"] isKindOfClass:[NSArray class]]) {
        *failure = @"checkpoint request is missing or invalid";
        return NO;
    }
    id declared_count = receipt[@"checkpoint_count"];
    if (![declared_count isKindOfClass:[NSNumber class]] ||
        [declared_count unsignedLongValue] !=
            [(NSArray *)request[@"checkpoints"] count]) {
        *failure = @"checkpoint request count disagrees with the receipt";
        return NO;
    }
    char request_sha[65] = {0};
    if (!shadow_sha256_file(request_path.fileSystemRepresentation, request_sha) ||
        ![receipt[@"checkpoint_request_sha256"] isEqualToString:@(request_sha)]) {
        *failure = @"checkpoint request digest mismatch";
        return NO;
    }
    snprintf(checkpoint_request_name, 256, "%s",
             [(NSString *)request_name UTF8String]);
    snprintf(checkpoint_request_sha, 65, "%s", request_sha);
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
    int status = 1;
    eu4_metal_replay_session *active_session = NULL;
    BOOL detailed_report_written = NO;
    NSString *report_stage = @"shader_wait";
    char checkpoint_request_name[256] = {0};
    char checkpoint_request_sha[65] = {0};
    NSString *report_error = @"";
    BOOL report_timed_out = NO;
    @autoreleasepool {
        char output[PATH_MAX];
        char shaders[PATH_MAX];
        default_paths(capture, output, sizeof(output), shaders,
                      sizeof(shaders));
        NSString *output_path = @(output);
        NSString *shader_path = @(shaders);
        NSString *report_path =
            [output_path stringByAppendingPathComponent:@"shadow-report.json"];
        @try {
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
                report_stage = @"shader_wait";
                report_error = @"shader_translation_timeout";
                report_timed_out = YES;
            } else {
                char shader_json_sha[65] = {0};
                NSString *receipt_failure = @"";
                if (!shadow_verify_receipt(capture, output_path, shader_path,
                                           shader_json_sha,
                                           checkpoint_request_name,
                                           checkpoint_request_sha,
                                           &receipt_failure)) {
                    report_stage = @"shader_receipt";
                    report_error = receipt_failure.length > 0
                        ? receipt_failure
                        : @"shader receipt verification failed";
                    report_timed_out = NO;
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
                    config.expected_reference_sha256 =
                        capture->reference_sha256;
                    config.expected_shader_json_sha256 = shader_json_sha;
                    char checkpoint_request_path[PATH_MAX];
                    snprintf(checkpoint_request_path,
                             sizeof(checkpoint_request_path), "%s/%s", output,
                             checkpoint_request_name);
                    config.checkpoint_path = checkpoint_request_path;
                    char create_error[512] = {0};
                    active_session = eu4_metal_replay_create(
                        &config, create_error, sizeof(create_error));
                    if (active_session == NULL) {
                        report_stage = @"metal_create";
                        report_error = create_error[0] != 0
                            ? @(create_error)
                            : @"Metal replay session creation failed";
                        report_timed_out = NO;
                    } else {
                        eu4_metal_replay_result result;
                        int code = eu4_metal_replay_frame(active_session,
                                                          &config, &result);
                        NSDictionary *report = shadow_report_for_replay(
                            capture, shader_path, &result, code,
                            g_started_ns, @(checkpoint_request_name),
                            @(checkpoint_request_sha));
                        report_stage = report[@"stage"] ?: @"metal_replay";
                        report_error = report[@"error"] ?: @"";
                        report_timed_out =
                            [report[@"timed_out"] boolValue];
                        BOOL report_written = write_report(report_path, report);
                        detailed_report_written = report_written;
                        if (result.ok && report_written) {
                            status = 0;
                        } else if (result.ok) {
                            status = 1;
                            report_stage = @"report_write";
                            report_error = @"shadow-report.json write failed";
                            report_timed_out = NO;
                        }
                    }
                }
            }
            if (status != 0 && !detailed_report_written) {
                NSDictionary *report = shadow_report_for_failure(
                    capture, shader_path, report_stage, report_error,
                    report_timed_out, g_started_ns,
                    @(checkpoint_request_name), @(checkpoint_request_sha));
                detailed_report_written = write_report(report_path, report);
            }
        } @catch (NSException *exception) {
            status = 2;
            NSString *reason = exception.reason;
            if (reason.length == 0) {
                reason = exception.name;
            }
            if (reason.length == 0) {
                reason = @"unknown Objective-C exception";
            }
            report_stage = @"internal_exception";
            report_error = reason;
            report_timed_out = NO;
            NSDictionary *report = shadow_report_for_exception(
                capture, shader_path, reason, g_started_ns);
            detailed_report_written = write_report(report_path, report);
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

char *eu4_gfx_shadow_test_report_json(
    const eu4_frame_capture_descriptor *capture,
    const eu4_metal_replay_result *result, int code,
    const char *failure_stage, const char *failure_error, int timed_out,
    const char *exception_reason) {
    if (capture == NULL) {
        return NULL;
    }
    @autoreleasepool {
        NSDictionary *report = nil;
        if (exception_reason != NULL) {
            report = shadow_report_for_exception(
                capture, nil, exception_reason != NULL ? @(exception_reason)
                                                       : nil,
                shadow_now_ns());
        } else if (result != NULL) {
            report = shadow_report_for_replay(capture, nil, result, code,
                                              shadow_now_ns(), @"", @"");
        } else {
            report = shadow_report_for_failure(
                capture, nil,
                failure_stage != NULL ? @(failure_stage) : @"shader_wait",
                failure_error != NULL ? @(failure_error) : @"",
                timed_out != 0, shadow_now_ns(), @"", @"");
        }
        NSData *json = [NSJSONSerialization dataWithJSONObject:report
                                                       options:0
                                                         error:NULL];
        if (json == nil) {
            return NULL;
        }
        char *text = (char *)malloc(json.length + 1);
        if (text == NULL) {
            return NULL;
        }
        memcpy(text, json.bytes, json.length);
        text[json.length] = 0;
        return text;
    }
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
