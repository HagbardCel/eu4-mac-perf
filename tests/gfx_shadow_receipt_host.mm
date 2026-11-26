/* Host test for the in-process shadow receipt contract.
 *
 * It includes the production bridge so the exact verifier the engine uses is
 * exercised, without starting EU4 or creating a Metal device.  Dead-code
 * stripping removes the delivery path; only shadow_verify_receipt is linked.
 */
#import <Foundation/Foundation.h>

#include "../renderer/integration/eu4_gfx_shadow_bridge.mm"

#include <stdio.h>
#include <string.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 9) {
            fprintf(stderr,
                    "usage: %s <shadow> <shaders> <capture-id> <pid> <session> "
                    "<manifest-sha> <event-sha> <event-bytes>\n", argv[0]);
            return 2;
        }
        eu4_frame_capture_descriptor capture;
        memset(&capture, 0, sizeof(capture));
        capture.struct_size = (uint32_t)sizeof(capture);
        capture.abi_version = EU4_FRAME_CAPTURE_ABI_VERSION;
        capture.capture_frame_id = strtoull(argv[3], NULL, 10);
        capture.pid = (int32_t)strtol(argv[4], NULL, 10);
        snprintf(capture.session_id, sizeof(capture.session_id), "%s", argv[5]);
        snprintf(capture.manifest_sha256, sizeof(capture.manifest_sha256),
                 "%s", argv[6]);
        snprintf(capture.event_stream_sha256,
                 sizeof(capture.event_stream_sha256), "%s", argv[7]);
        capture.event_stream_bytes = strtoull(argv[8], NULL, 10);

        char shader_sha[65] = {0};
        char request_name[256] = {0};
        char request_sha[65] = {0};
        NSString *failure = nil;
        BOOL ok = shadow_verify_receipt(
            &capture, @(argv[1]), @(argv[2]), shader_sha, request_name,
            request_sha, &failure);
        NSDictionary *document = @{
            @"ok": @(ok),
            @"failure": failure ?: @"",
            @"shader_json_sha256": @(shader_sha),
            @"checkpoint_request_file": @(request_name),
            @"checkpoint_request_sha256": @(request_sha),
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:document
                                                       options:0
                                                         error:NULL];
        printf("%s\n", [[[NSString alloc] initWithData:json
                                              encoding:NSUTF8StringEncoding]
                           UTF8String]);
    }
    return 0;
}
