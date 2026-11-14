/* Narrow focus and placement guard for background EU4 sessions.
 *
 * Loaded only when EU4_DISPLAY_MODE=background.  It never injects input and
 * never moves unrelated windows.  The guard:
 *
 *   - intercepts SDL's own window show/raise path (NSWindow order-*), placing
 *     the game window on the selected BetterDisplay virtual screen;
 *   - suppresses the explicit foreground activation SDL performs on raise;
 *   - leaves window creation and rendering untouched.
 *
 * Set EU4_DISPLAY_TRACE to a path to append a JSON-lines trace of the
 * intercepted AppKit calls.
 */

#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>

#include <dispatch/dispatch.h>
#include <objc/runtime.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static BOOL g_enabled;
static BOOL g_ready;
static CGDirectDisplayID g_target_display;
static CGFloat g_margin = 24.0;
static NSMutableSet *g_placed_windows;
static FILE *g_trace;

typedef BOOL (*ActivateIgnoringIMP)(id, SEL, BOOL);
typedef void (*ActivateIMP)(id, SEL);
typedef BOOL (*RunningActivateIMP)(id, SEL, NSApplicationActivationOptions);
typedef BOOL (*SetPolicyIMP)(id, SEL, NSApplicationActivationPolicy);
typedef void (*WindowOrderIMP)(id, SEL, id);
typedef void (*SetFrameIMP)(id, SEL, NSRect, BOOL);
typedef void (*OrderRegardlessIMP)(id, SEL, id);
typedef BOOL (*CanBecomeIMP)(id, SEL);

static ActivateIgnoringIMP g_activate_ignoring_original;
static ActivateIMP g_activate_original;
static RunningActivateIMP g_running_activate_original;
static SetPolicyIMP g_set_policy_original;
static WindowOrderIMP g_order_front_original;
static WindowOrderIMP g_make_key_original;
static WindowOrderIMP g_order_front_regardless_original;
static SetFrameIMP g_set_frame_original;
static OrderRegardlessIMP g_order_regardless_original;
static CanBecomeIMP g_can_become_key_original;
static CanBecomeIMP g_can_become_main_original;
static BOOL g_in_place;
static NSRunningApplication *g_previous_application;

static void trace(const char *event, const char *detail) {
    if (g_trace == NULL) {
        return;
    }
    fprintf(g_trace, "{\"event\":\"%s\",\"detail\":\"%s\"}\n", event,
            detail != NULL ? detail : "");
    fflush(g_trace);
}

static NSScreen *screen_for_display(CGDirectDisplayID display) {
    for (NSScreen *screen in [NSScreen screens]) {
        NSNumber *number = screen.deviceDescription[@"NSScreenNumber"];
        if (number != nil && number.unsignedIntValue == display) {
            return screen;
        }
    }
    return nil;
}

static BOOL place_window(NSWindow *window, const char *reason) {
    if (!g_enabled || window == nil) {
        return NO;
    }
    if (g_placed_windows == nil) {
        g_placed_windows = [NSMutableSet set];
    }
    NSValue *identity = [NSValue valueWithNonretainedObject:window];
    if ([g_placed_windows containsObject:identity]) {
        return NO;
    }
    NSRect frame = window.frame;
    if (frame.size.width < 800.0 || frame.size.height < 600.0) {
        return NO;
    }
    if (g_in_place) {
        return NO;
    }
    NSScreen *screen = screen_for_display(g_target_display);
    char detail[512];
    snprintf(detail, sizeof detail,
             "%s frame=%.0f,%.0f %.0fx%.0f screen=%s", reason,
             frame.origin.x, frame.origin.y, frame.size.width, frame.size.height,
             screen != nil ? "target" : "missing");
    trace("window", detail);
    if (screen == nil) {
        return NO;
    }
    NSRect bounds = screen.frame;
    if (NSContainsRect(bounds, frame)) {
        [g_placed_windows addObject:identity];
        return YES;
    }
    NSPoint origin = NSMakePoint(bounds.origin.x + g_margin,
                                 bounds.origin.y + g_margin);
    CGFloat max_x = bounds.origin.x + bounds.size.width - frame.size.width;
    CGFloat max_y = bounds.origin.y + bounds.size.height - frame.size.height;
    if (origin.x > max_x) origin.x = max_x;
    if (origin.y > max_y) origin.y = max_y;
    frame.origin = origin;
    g_in_place = YES;
    if (g_set_frame_original != NULL) {
        g_set_frame_original(window, @selector(setFrame:display:), frame, NO);
    } else {
        [window setFrame:frame display:NO];
    }
    g_in_place = NO;
    [g_placed_windows addObject:identity];
    snprintf(detail, sizeof detail, "moved to %.0f,%.0f", origin.x, origin.y);
    trace("place", detail);
    return YES;
}

static BOOL guard_activate_ignoring(id self, SEL _cmd, BOOL ignoring) {
    trace("activateIgnoringOtherApps", ignoring ? "YES" : "NO");
    /* The window is already ordered on screen; foreground activation is not
       needed for rendering and is the operation that steals the desktop. */
    return YES;
}

static void guard_activate(id self, SEL _cmd) {
    trace("activate", "");
}

static BOOL guard_running_activate(id self, SEL _cmd,
                                   NSApplicationActivationOptions options) {
    trace("runningActivate", "");
    return YES;
}

static BOOL guard_set_policy(id self, SEL _cmd, NSApplicationActivationPolicy policy) {
    char detail[64];
    snprintf(detail, sizeof detail, "%ld", (long)policy);
    trace("setActivationPolicy", detail);
    if (g_set_policy_original != NULL) {
        return g_set_policy_original(self, _cmd, policy);
    }
    return NO;
}

static void guard_order_front(id self, SEL _cmd, id sender) {
    place_window(self, "orderFront");
    if (g_order_front_original != NULL) {
        g_order_front_original(self, _cmd, sender);
    }
}

static BOOL guard_can_become_key(id self, SEL _cmd) {
    return g_enabled ? NO : (g_can_become_key_original != NULL
                                 ? g_can_become_key_original(self, _cmd)
                                 : YES);
}

static BOOL guard_can_become_main(id self, SEL _cmd) {
    return g_enabled ? NO : (g_can_become_main_original != NULL
                                 ? g_can_become_main_original(self, _cmd)
                                 : YES);
}

static void guard_make_key(id self, SEL _cmd, id sender) {
    place_window(self, "makeKey");
    if (g_make_key_original != NULL) {
        g_make_key_original(self, _cmd, sender);
    }
    /* SDL makes the window key; on macOS that also makes a regular
       application frontmost.  Return focus to the application the user was
       using.  This is a narrow focus safeguard, not desktop automation: no
       input event and no unrelated window is touched. */
    NSRunningApplication *previous = g_previous_application;
    if (previous != nil && previous.processIdentifier != getpid()) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                     (int64_t)(0.25 * NSEC_PER_SEC)),
                       dispatch_get_main_queue(), ^{
            if ([NSApp isActive]) {
                trace("yieldFocus", "");
                [previous activateWithOptions:0];
            }
        });
    }
}

static void guard_set_frame(id self, SEL _cmd, NSRect frame, BOOL display) {
    if (g_enabled && !g_in_place && self != nil &&
        frame.size.width >= 800.0 && frame.size.height >= 600.0) {
        NSScreen *screen = screen_for_display(g_target_display);
        if (screen != nil) {
            NSRect bounds = screen.frame;
            if (!NSContainsRect(bounds, frame)) {
                frame.origin.x = bounds.origin.x + g_margin;
                frame.origin.y = bounds.origin.y + g_margin;
                CGFloat max_x = bounds.origin.x + bounds.size.width
                    - frame.size.width;
                CGFloat max_y = bounds.origin.y + bounds.size.height
                    - frame.size.height;
                if (frame.origin.x > max_x) frame.origin.x = max_x;
                if (frame.origin.y > max_y) frame.origin.y = max_y;
            }
        }
    }
    if (g_set_frame_original != NULL) {
        g_set_frame_original(self, _cmd, frame, display);
    }
}

static void guard_order_front_regardless(id self, SEL _cmd, id sender) {
    place_window(self, "orderFrontRegardless");
    if (g_order_front_regardless_original != NULL) {
        g_order_front_regardless_original(self, _cmd, sender);
    }
}

static void swizzle_instance(Class cls, SEL selector, IMP replacement,
                             IMP *original) {
    Method method = class_getInstanceMethod(cls, selector);
    if (method == NULL) {
        return;
    }
    *original = method_getImplementation(method);
    method_setImplementation(method, replacement);
}

/* AppKit is loaded lazily; install once NSApplication exists. */
static void install_guard(void) {
    if (g_ready) {
        return;
    }
    g_ready = YES;
    swizzle_instance([NSApplication class],
                     @selector(activateIgnoringOtherApps:),
                     (IMP)guard_activate_ignoring,
                     (IMP *)&g_activate_ignoring_original);
    swizzle_instance([NSApplication class], @selector(activate),
                     (IMP)guard_activate, (IMP *)&g_activate_original);
    swizzle_instance([NSApplication class], @selector(setActivationPolicy:),
                     (IMP)guard_set_policy, (IMP *)&g_set_policy_original);
    swizzle_instance([NSRunningApplication class], @selector(activateWithOptions:),
                     (IMP)guard_running_activate,
                     (IMP *)&g_running_activate_original);
    swizzle_instance([NSWindow class], @selector(orderFront:),
                     (IMP)guard_order_front,
                     (IMP *)&g_order_front_original);
    swizzle_instance([NSWindow class], @selector(makeKeyAndOrderFront:),
                     (IMP)guard_make_key,
                     (IMP *)&g_make_key_original);
    swizzle_instance([NSWindow class], @selector(orderFrontRegardless),
                     (IMP)guard_order_front_regardless,
                     (IMP *)&g_order_front_regardless_original);
    trace("installed", "");
}

__attribute__((constructor)) static void initialize_guard(void) {
    const char *mode = getenv("EU4_DISPLAY_MODE");
    if (mode == NULL || strcmp(mode, "background") != 0) {
        return;
    }
    const char *display = getenv("EU4_DISPLAY_ID");
    if (display == NULL || display[0] == 0) {
        return;
    }
    g_target_display = (CGDirectDisplayID)strtoul(display, NULL, 10);
    const char *margin = getenv("EU4_DISPLAY_MARGIN");
    if (margin != NULL && margin[0] != 0) {
        g_margin = (CGFloat)strtod(margin, NULL);
    }
    const char *trace_path = getenv("EU4_DISPLAY_TRACE");
    if (trace_path != NULL && trace_path[0] != 0) {
        g_trace = fopen(trace_path, "a");
    }
    g_previous_application =
        [[NSWorkspace sharedWorkspace] frontmostApplication];
    g_enabled = YES;
    install_guard();
}
