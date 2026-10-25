#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>
#import <Foundation/Foundation.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static id jsonNull(void) { return [NSNull null]; }

static NSString *activationPolicyName(NSApplicationActivationPolicy policy) {
    switch (policy) {
        case NSApplicationActivationPolicyRegular: return @"regular";
        case NSApplicationActivationPolicyAccessory: return @"accessory";
        case NSApplicationActivationPolicyProhibited: return @"prohibited";
        default: return @"unknown";
    }
}

static int emitInspect(pid_t pid) {
    NSRunningApplication *app =
        [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
    BOOL registered = app != nil && !app.terminated;
    NSRunningApplication *front =
        [[NSWorkspace sharedWorkspace] frontmostApplication];

    NSMutableDictionary *payload = [NSMutableDictionary dictionary];
    payload[@"target_pid"] = @(pid);
    payload[@"target_registered"] = @(registered);
    if (registered) {
        payload[@"target_bundle_id"] = app.bundleIdentifier ?: jsonNull();
        payload[@"target_localized_name"] = app.localizedName ?: jsonNull();
        payload[@"target_activation_policy"] =
            activationPolicyName(app.activationPolicy);
    } else {
        payload[@"target_bundle_id"] = jsonNull();
        payload[@"target_localized_name"] = jsonNull();
        payload[@"target_activation_policy"] = jsonNull();
    }
    if (front) {
        payload[@"frontmost_pid"] = @(front.processIdentifier);
        payload[@"frontmost_bundle_id"] = front.bundleIdentifier ?: jsonNull();
        payload[@"frontmost_localized_name"] = front.localizedName ?: jsonNull();
    } else {
        payload[@"frontmost_pid"] = jsonNull();
        payload[@"frontmost_bundle_id"] = jsonNull();
        payload[@"frontmost_localized_name"] = jsonNull();
    }
    int check_rc;
    if (!registered) {
        check_rc = 2;
    } else if (!front || front.processIdentifier != pid) {
        check_rc = 4;
    } else {
        check_rc = 0;
    }
    payload[@"check_rc"] = @(check_rc);

    NSData *json = [NSJSONSerialization dataWithJSONObject:payload
                                                 options:0
                                                   error:NULL];
    if (!json) return 7;
    if (write(STDOUT_FILENO, json.bytes, json.length) < 0) return 7;
    if (write(STDOUT_FILENO, "\n", 1) < 0) return 7;
    return 0;
}

int main(int argc,char **argv) {
    if (argc!=3) return 64;
    char *end=NULL;
    long requested=strtol(argv[2],&end,10);
    if (!end || *end || requested<=0 || requested>INT_MAX) return 64;
    @autoreleasepool {
        pid_t pid=(pid_t)requested;
        if (!strcmp(argv[1],"inspect")) {
            return emitInspect(pid);
        }
        NSRunningApplication *app=
            [NSRunningApplication runningApplicationWithProcessIdentifier:pid];
        if (!app || app.terminated) return 2;
        if (!strcmp(argv[1],"activate")) {
            if (![app activateWithOptions:NSApplicationActivateIgnoringOtherApps])
                return 3;
        } else if (!strcmp(argv[1],"check")) {
            NSRunningApplication *front=
                [NSWorkspace sharedWorkspace].frontmostApplication;
            if (!front || front.processIdentifier!=pid) return 4;
        } else if (!strcmp(argv[1],"park") || !strcmp(argv[1],"interior")) {
            NSRunningApplication *front=
                [NSWorkspace sharedWorkspace].frontmostApplication;
            if (!front || front.processIdentifier!=pid) return 4;
            CGRect bounds=CGDisplayBounds(CGMainDisplayID());
            if (!strcmp(argv[1],"park")) {
                CGPoint center=CGPointMake(CGRectGetMidX(bounds),
                                           CGRectGetMidY(bounds));
                if (CGWarpMouseCursorPosition(center)!=kCGErrorSuccess) return 6;
            } else {
                CGEventRef event=CGEventCreate(NULL);
                if (!event) return 7;
                CGPoint point=CGEventGetLocation(event);
                CFRelease(event);
                CGRect interior=CGRectInset(bounds,0.15*bounds.size.width,
                                            0.15*bounds.size.height);
                if (!CGRectContainsPoint(interior,point)) return 8;
            }
        } else if (!strcmp(argv[1],"terminate")) {
            if (![app terminate]) return 5;
        } else return 64;
    }
    return 0;
}
