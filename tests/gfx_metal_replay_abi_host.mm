/* Native Stage A host for the reusable Metal replay C ABI.
 *
 * It creates one session, executes one frame, reports the structured result and
 * destroys the session.  It never creates an OpenGL context.
 */
#import <Foundation/Foundation.h>

#include "eu4_gfx_metal_replay.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int run_once(const char *frame, const char *shaders, const char *out,
                    int iteration, uint64_t timeout_ms,
                    const char *event_sha, uint64_t event_bytes,
                    const char *manifest_sha, const char *reference_sha,
                    const char *shader_sha, uint32_t inject_failure) {
    eu4_metal_replay_config config;
    memset(&config, 0, sizeof(config));
    config.struct_size = (uint32_t)sizeof(config);
    config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
    config.frame_dir = frame;
    config.shader_dir = shaders;
    config.output_dir = out;
    config.stop_after = -1;
    config.only_event = -1;
    config.timeout_ms = timeout_ms;
    config.expected_event_stream_sha256 = event_sha;
    config.expected_event_stream_bytes = event_bytes;
    config.expected_manifest_sha256 = manifest_sha;
    config.expected_reference_sha256 = reference_sha;
    config.expected_shader_json_sha256 = shader_sha;
    config.test_inject_failure = inject_failure;
    char error[512] = {0};
    eu4_metal_replay_session *session =
        eu4_metal_replay_create(&config, error, sizeof(error));
    if (session == NULL) {
        fprintf(stderr, "create: %s\n", error);
        return 2;
    }
    eu4_metal_replay_result result;
    int code = eu4_metal_replay_frame(session, &config, &result);
    NSDictionary *document = @{
        @"iteration": @(iteration),
        @"code": @(code),
        @"ok": @(result.ok),
        @"command_status": @(result.command_status),
        @"readback_ok": @(result.readback_ok),
        @"timed_out": @(result.timed_out),
        @"partial": @(result.partial),
        @"events": @(result.events),
        @"encoded": @(result.encoded),
        @"failed": @(result.failed),
        @"duration_ns": @(result.duration_ns),
        @"error": @(result.error),
        @"result_json": @(result.result_json),
        @"metal_frame": @(result.metal_frame),
        @"provenance_jsonl": @(result.provenance_jsonl),
        @"manifest_reference_sha256": @(result.manifest_reference_sha256),
        @"metal_frame_sha256": @(result.metal_frame_sha256),
        @"last_error": @(eu4_metal_replay_last_error(session)),
    };
    NSData *json = [NSJSONSerialization dataWithJSONObject:document
                                                   options:0
                                                     error:NULL];
    printf("%s\n", [[[NSString alloc] initWithData:json
                                          encoding:NSUTF8StringEncoding] UTF8String]);
    eu4_metal_replay_destroy(session);
    return result.ok ? 0 : 1;
}

static int run_reuse(const char *frame, const char *shaders, const char *out,
                     int iterations) {
    eu4_metal_replay_config config;
    memset(&config, 0, sizeof(config));
    config.struct_size = (uint32_t)sizeof(config);
    config.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
    config.frame_dir = frame;
    config.shader_dir = shaders;
    config.output_dir = out;
    config.stop_after = -1;
    config.only_event = -1;
    char error[512] = {0};
    eu4_metal_replay_session *session =
        eu4_metal_replay_create(&config, error, sizeof(error));
    if (session == NULL) {
        fprintf(stderr, "create: %s\n", error);
        return 2;
    }
    int result = 0;
    for (int iteration = 0; iteration < iterations; iteration++) {
        eu4_metal_replay_result frame_result;
        int code = eu4_metal_replay_frame(session, &config, &frame_result);
        NSDictionary *document = @{
            @"iteration": @(iteration),
            @"code": @(code),
            @"ok": @(frame_result.ok),
            @"command_status": @(frame_result.command_status),
            @"events": @(frame_result.events),
            @"encoded": @(frame_result.encoded),
            @"failed": @(frame_result.failed),
            @"metal_frame_sha256": @(frame_result.metal_frame_sha256),
            @"error": @(frame_result.error),
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:document
                                                       options:0
                                                         error:NULL];
        printf("%s\n", [[[NSString alloc] initWithData:json
                                              encoding:NSUTF8StringEncoding] UTF8String]);
        if (!frame_result.ok && result == 0) {
            result = code != 0 ? code : 1;
        }
    }
    eu4_metal_replay_destroy(session);
    return result;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *frame = NULL;
        const char *shaders = NULL;
        const char *out = NULL;
        int loops = 1;
        int bad_config = 0;
        int reuse_session = 0;
        uint64_t timeout_ms = 0;
        const char *event_sha = NULL;
        uint64_t event_bytes = 0;
        const char *manifest_sha = NULL;
        const char *reference_sha = NULL;
        const char *shader_sha = NULL;
        uint32_t inject_failure = 0;
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "--frame") == 0 && index + 1 < argc) {
                frame = argv[++index];
            } else if (strcmp(argv[index], "--shaders") == 0 &&
                       index + 1 < argc) {
                shaders = argv[++index];
            } else if (strcmp(argv[index], "--out") == 0 && index + 1 < argc) {
                out = argv[++index];
            } else if (strcmp(argv[index], "--loops") == 0 &&
                       index + 1 < argc) {
                loops = atoi(argv[++index]);
            } else if (strcmp(argv[index], "--bad-config") == 0) {
                bad_config = 1;
            } else if (strcmp(argv[index], "--reuse-session") == 0) {
                reuse_session = 1;
            } else if (strcmp(argv[index], "--timeout-ms") == 0 &&
                       index + 1 < argc) {
                timeout_ms = (uint64_t)strtoull(argv[++index], NULL, 10);
            } else if (strcmp(argv[index], "--event-sha") == 0 &&
                       index + 1 < argc) {
                event_sha = argv[++index];
            } else if (strcmp(argv[index], "--event-bytes") == 0 &&
                       index + 1 < argc) {
                event_bytes = (uint64_t)strtoull(argv[++index], NULL, 10);
            } else if (strcmp(argv[index], "--manifest-sha") == 0 &&
                       index + 1 < argc) {
                manifest_sha = argv[++index];
            } else if (strcmp(argv[index], "--reference-sha") == 0 &&
                       index + 1 < argc) {
                reference_sha = argv[++index];
            } else if (strcmp(argv[index], "--shader-sha") == 0 &&
                       index + 1 < argc) {
                shader_sha = argv[++index];
            } else if (strcmp(argv[index], "--inject-failure") == 0 &&
                       index + 1 < argc) {
                inject_failure = (uint32_t)strtoul(argv[++index], NULL, 10);
            }
        }
        if (bad_config) {
            eu4_metal_replay_config invalid;
            memset(&invalid, 0, sizeof(invalid));
            invalid.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
            char error[256] = {0};
            eu4_metal_replay_session *session =
                eu4_metal_replay_create(&invalid, error, sizeof(error));
            printf("{\"create_failed\":%s}\n",
                   session == NULL ? "true" : "false");
            eu4_metal_replay_destroy(session);
            return session == NULL ? 0 : 1;
        }
        if (frame == NULL || shaders == NULL || out == NULL || loops < 1) {
            fprintf(stderr,
                    "usage: %s --frame <dir> --shaders <dir> --out <dir> "
                    "[--loops N]\n",
                    argv[0]);
            return 2;
        }
        if (reuse_session) {
            return run_reuse(frame, shaders, out, loops);
        }
        int result = 0;
        for (int iteration = 0; iteration < loops; iteration++) {
            int code = run_once(frame, shaders, out, iteration, timeout_ms,
                                event_sha, event_bytes, manifest_sha,
                                reference_sha, shader_sha, inject_failure);
            if (code != 0 && result == 0) {
                result = code;
            }
        }
        return result;
    }
}
