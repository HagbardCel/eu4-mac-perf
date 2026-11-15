/* Read-only display/window/focus probe for background EU4 sessions.
 *
 * Emits one JSON object. It never moves a window, never activates an
 * application and never injects input: the harness uses it to observe the
 * virtual display and the EU4 window that SDL created.
 *
 *   eu4_display_probe snapshot [--pid PID]
 *   eu4_display_probe displays
 *
 * Window names require Screen Recording permission; bounds, layer, owner PID
 * and on-screen state do not, so the probe degrades to name=null rather than
 * failing.
 */

#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>

#include <stdlib.h>
#include <string.h>

static id jsonNull(void) { return [NSNull null]; }

static NSDictionary *displayRecord(CGDirectDisplayID display) {
    CGRect bounds = CGDisplayBounds(display);
    CGDisplayModeRef mode = CGDisplayCopyDisplayMode(display);
    size_t pixel_width = 0;
    size_t pixel_height = 0;
    size_t point_width = 0;
    size_t point_height = 0;
    double refresh = 0.0;
    if (mode != NULL) {
        pixel_width = CGDisplayModeGetPixelWidth(mode);
        pixel_height = CGDisplayModeGetPixelHeight(mode);
        point_width = CGDisplayModeGetWidth(mode);
        point_height = CGDisplayModeGetHeight(mode);
        refresh = CGDisplayModeGetRefreshRate(mode);
        CGDisplayModeRelease(mode);
    }
    return @{
        @"display_id": @(display),
        @"main": @(CGDisplayIsMain(display) != 0),
        @"active": @(CGDisplayIsActive(display) != 0),
        @"online": @(CGDisplayIsOnline(display) != 0),
        @"asleep": @(CGDisplayIsAsleep(display) != 0),
        @"origin_x": @(bounds.origin.x),
        @"origin_y": @(bounds.origin.y),
        @"width": @(bounds.size.width),
        @"height": @(bounds.size.height),
        @"pixel_width": @(pixel_width),
        @"pixel_height": @(pixel_height),
        @"mode_width": @(point_width),
        @"mode_height": @(point_height),
        @"refresh_hz": @(refresh),
        /* A display whose point size differs from its pixel size is HiDPI. */
        @"hidpi": @(pixel_width != 0 && point_width != 0 &&
                    pixel_width != point_width),
    };
}

static NSArray *activeDisplays(void) {
    uint32_t count = 0;
    if (CGGetActiveDisplayList(0, NULL, &count) != kCGErrorSuccess) {
        return @[];
    }
    CGDirectDisplayID *ids = calloc(count == 0 ? 1 : count, sizeof(CGDirectDisplayID));
    if (ids == NULL) {
        return @[];
    }
    NSMutableArray *result = [NSMutableArray arrayWithCapacity:count];
    if (CGGetActiveDisplayList(count, ids, &count) == kCGErrorSuccess) {
        for (uint32_t index = 0; index < count; index++) {
            [result addObject:displayRecord(ids[index])];
        }
    }
    free(ids);
    return result;
}

static NSArray *onScreenWindows(void) {
    CGWindowListOption options =
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements;
    NSArray *windows = CFBridgingRelease(
        CGWindowListCopyWindowInfo(options, kCGNullWindowID));
    if (![windows isKindOfClass:[NSArray class]]) {
        return @[];
    }
    NSMutableArray *result = [NSMutableArray array];
    for (NSDictionary *window in windows) {
        NSNumber *layer = window[(id)kCGWindowLayer];
        if (layer != nil && layer.intValue != 0) {
            continue; /* menu bar, Dock, overlays are not application windows */
        }
        NSDictionary *bounds = window[(id)kCGWindowBounds];
        NSMutableDictionary *record = [NSMutableDictionary dictionary];
        record[@"window_id"] = window[(id)kCGWindowNumber] ?: jsonNull();
        record[@"owner_pid"] = window[(id)kCGWindowOwnerPID] ?: jsonNull();
        record[@"owner_name"] = window[(id)kCGWindowOwnerName] ?: jsonNull();
        record[@"name"] = window[(id)kCGWindowName] ?: jsonNull();
        record[@"alpha"] = window[(id)kCGWindowAlpha] ?: jsonNull();
        record[@"onscreen"] = window[(id)kCGWindowIsOnscreen] ?: jsonNull();
        record[@"layer"] = window[(id)kCGWindowLayer] ?: jsonNull();
        if ([bounds isKindOfClass:[NSDictionary class]]) {
            record[@"x"] = bounds[@"X"] ?: jsonNull();
            record[@"y"] = bounds[@"Y"] ?: jsonNull();
            record[@"width"] = bounds[@"Width"] ?: jsonNull();
            record[@"height"] = bounds[@"Height"] ?: jsonNull();
        } else {
            record[@"x"] = jsonNull();
            record[@"y"] = jsonNull();
            record[@"width"] = jsonNull();
            record[@"height"] = jsonNull();
        }
        [result addObject:record];
    }
    return result;
}

static NSDictionary *focusRecord(void) {
    NSRunningApplication *front =
        [[NSWorkspace sharedWorkspace] frontmostApplication];
    if (front == nil) {
        return @{@"frontmost_pid": jsonNull(),
                 @"frontmost_bundle_id": jsonNull(),
                 @"frontmost_name": jsonNull()};
    }
    return @{
        @"frontmost_pid": @(front.processIdentifier),
        @"frontmost_bundle_id": front.bundleIdentifier ?: jsonNull(),
        @"frontmost_name": front.localizedName ?: jsonNull(),
    };
}

static int emit(NSDictionary *payload) {
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:payload
                                                   options:0
                                                     error:&error];
    if (json == nil) {
        return 7;
    }
    if (fwrite(json.bytes, 1, json.length, stdout) != json.length) {
        return 7;
    }
    fputc('\n', stdout);
    if (fflush(stdout) != 0) {
        return 7;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        return 64;
    }
    @autoreleasepool {
        NSString *operation = @(argv[1]);
        NSInteger pid = 0;
        for (int index = 2; index < argc; index++) {
            if (strcmp(argv[index], "--pid") == 0 && index + 1 < argc) {
                pid = strtol(argv[++index], NULL, 10);
            }
        }
        NSDictionary *displays = @{
            @"main_display_id": @(CGMainDisplayID()),
            @"displays": activeDisplays(),
        };
        if ([operation isEqualToString:@"displays"]) {
            return emit(displays);
        }
        if (![operation isEqualToString:@"snapshot"]) {
            return 64;
        }
        NSMutableDictionary *payload = [displays mutableCopy];
        NSArray *windows = onScreenWindows();
        if (pid > 0) {
            NSMutableArray *owned = [NSMutableArray array];
            for (NSDictionary *window in windows) {
                NSNumber *owner = window[@"owner_pid"];
                if ([owner isKindOfClass:[NSNumber class]] &&
                    owner.integerValue == pid) {
                    [owned addObject:window];
                }
            }
            payload[@"windows"] = owned;
            payload[@"target_pid"] = @(pid);
            NSRunningApplication *app =
                [NSRunningApplication runningApplicationWithProcessIdentifier:(pid_t)pid];
            payload[@"target_registered"] = @(app != nil && !app.terminated);
        } else {
            payload[@"windows"] = windows;
        }
        [payload addEntriesFromDictionary:focusRecord()];
        return emit(payload);
    }
}
