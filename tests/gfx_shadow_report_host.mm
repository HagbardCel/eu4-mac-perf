/* Native host for the shadow-report test seam.
 *
 * It constructs the same reports the in-process bridge publishes for a
 * successful replay, a native replay failure, a shader-receipt failure, a
 * session-creation failure and an Objective-C exception.  No EU4 process,
 * callback subscription, shader translation or Metal device is involved.
 */
#import <Foundation/Foundation.h>

#include "../renderer/integration/eu4_gfx_shadow_bridge.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The bridge only uses this symbol for its live subscription path. */
int eu4_gfx_frame_sealed_subscribe(eu4_frame_sealed_callback callback,
                                   void *context) {
    (void)callback;
    (void)context;
    return 0;
}

static void print_report(NSString *scenario, char *json) {
    if (json == NULL) {
        printf("{\"scenario\":\"%s\",\"report\":null}\n",
               scenario.UTF8String);
        return;
    }
    NSData *data = [NSData dataWithBytes:json length:strlen(json)];
    id report = [NSJSONSerialization JSONObjectWithData:data options:0
                                                 error:NULL];
    NSDictionary *line = @{
        @"scenario": scenario,
        @"report": report ?: @{},
    };
    NSData *line_data = [NSJSONSerialization dataWithJSONObject:line
                                                        options:0
                                                          error:NULL];
    NSString *line_text = line_data != nil
        ? [[NSString alloc] initWithData:line_data
                                encoding:NSUTF8StringEncoding]
        : nil;
    printf("%s\n", line_text.UTF8String ?: "{}");
    free(json);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *replay_json = NULL;
        for (int index = 1; index < argc; index++) {
            if (strcmp(argv[index], "--replay-json") == 0 &&
                index + 1 < argc) {
                replay_json = argv[++index];
            }
        }

        eu4_frame_capture_descriptor capture;
        memset(&capture, 0, sizeof(capture));
        capture.struct_size = (uint32_t)sizeof(capture);
        capture.abi_version = 2;
        capture.capture_frame_id = 42;
        snprintf(capture.session_id, sizeof(capture.session_id),
                 "%s", "session-1");
        capture.pid = 4242;
        snprintf(capture.boundary, sizeof(capture.boundary), "%s", "scene");
        capture.operation_count = 7;
        capture.draw_count = 5;
        capture.clear_count = 2;
        capture.copy_count = 0;
        capture.width = 4;
        capture.height = 4;
        snprintf(capture.capture_dir, sizeof(capture.capture_dir),
                 "%s", "/tmp/capture");
        snprintf(capture.reference_sha256, sizeof(capture.reference_sha256),
                 "%s", "reference-sha");
        snprintf(capture.manifest_sha256, sizeof(capture.manifest_sha256),
                 "%s", "manifest-sha");

        eu4_metal_replay_result success;
        memset(&success, 0, sizeof(success));
        success.struct_size = (uint32_t)sizeof(success);
        success.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
        success.ok = 1;
        success.events = 326;
        success.encoded = 326;
        success.failed = 0;
        success.command_status = 4;
        success.readback_ok = 1;
        success.duration_ns = 123456;
        snprintf(success.manifest_reference_sha256,
                 sizeof(success.manifest_reference_sha256), "%s",
                 "reference-sha");
        snprintf(success.metal_frame_sha256,
                 sizeof(success.metal_frame_sha256), "%s", "frame-sha");
        print_report(@"success",
                     eu4_gfx_shadow_test_report_json(
                         &capture, &success, 0, NULL, NULL, 0, NULL));

        eu4_metal_replay_result failure;
        memset(&failure, 0, sizeof(failure));
        failure.struct_size = (uint32_t)sizeof(failure);
        failure.abi_version = EU4_METAL_REPLAY_ABI_VERSION;
        failure.ok = 0;
        failure.events = 9;
        failure.encoded = 3;
        failure.failed = 1;
        failure.timed_out = 1;
        failure.command_status = 2;
        failure.duration_ns = 4242;
        snprintf(failure.error, sizeof(failure.error), "%s",
                 "event_boundary (operation 3): event boundary exceeded "
                 "the replay deadline");
        if (replay_json != NULL) {
            snprintf(failure.result_json, sizeof(failure.result_json), "%s",
                     replay_json);
        }
        print_report(@"replay_failure",
                     eu4_gfx_shadow_test_report_json(
                         &capture, &failure, 1, NULL, NULL, 0, NULL));

        eu4_metal_replay_result failure_no_json = failure;
        failure_no_json.result_json[0] = 0;
        print_report(@"replay_failure_no_json",
                     eu4_gfx_shadow_test_report_json(
                         &capture, &failure_no_json, 1, NULL, NULL, 0, NULL));

        print_report(@"shader_wait_timeout",
                     eu4_gfx_shadow_test_report_json(
                         &capture, NULL, 0, "shader_wait",
                         "shader_translation_timeout", 1, NULL));

        print_report(@"shader_receipt",
                     eu4_gfx_shadow_test_report_json(
                         &capture, NULL, 0, "shader_receipt",
                         "shader receipt identity mismatch", 0, NULL));

        print_report(@"metal_create",
                     eu4_gfx_shadow_test_report_json(
                         &capture, NULL, 0, "metal_create",
                         "no Metal device", 0, NULL));

        print_report(@"internal_exception",
                     eu4_gfx_shadow_test_report_json(
                         &capture, NULL, 0, NULL, NULL, 0,
                         "controlled bridge exception"));
    }
    return 0;
}
