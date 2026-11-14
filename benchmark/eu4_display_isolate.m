/* Narrow focus and placement guard for background EU4 sessions.
 *
 * Loaded only when EU4_DISPLAY_MODE=background.  It does not inject input,
 * move unrelated windows or take focus.  It:
 *
 *   - prevents the game from activating itself (SDL calls
 *     -[NSApplication activateIgnoringOtherApps:] during startup);
 *   - forces the accessory activation policy so the game cannot become the
 *     frontmost application;
 *   - places each large game window on the selected BetterDisplay virtual
 *     screen immediately before it is ordered on screen.
 *
 * The window's size is never changed: renderer geometry must remain the
 * extent the capture declares.
 */

#import <AppKit/AppKit.h>
#import <CoreGraphics/CoreGraphics.h>

#include <objc/runtime.h>
#include <stdlib.h>
#include <string.h>

static BOOL g_enabled;
static CGDirectDisplayID g_target_display;
static CGFloat g_margin = 24.0;
static BOOL g_placed;

typedef BOOL (*ActivateIgnoringIMP)(id, SEL, BOOL);
typedef void (*ActivateIMP)(id, SEL);
typedef BOOL (*SetPolicyIMP)(id, SEL, NSApplicationActivationPolicy);
typedef void (*OrderFrontIMP)(id, SEL, id);
typedef void (*MakeKeyIMP)(id, SEL, id);
typedef BOOL (*RunningActivateIMP)(id, SEL, NSApplicationActivationOptions);

static ActivateIgnoringIMP g_activate_ignoring_original;
static ActivateIMP g_activate_original;
static SetPolicyIMP g_set_policy_original;
static OrderFrontIMP g_order_front_original;
static MakeKeyIMP g_make_key_original;
static RunningActivateIMP g_running_activate_original;

static NSScreen *screen_for_display(CGDirectDisplayID display) {
    for (NSScreen *screen in [NSScreen screens]) {
        NSNumber *number = screen.deviceDescription[@"NSScreenNumber"];
        if (number != nil && number.unsignedIntValue == display) {
            return screen;
        }
    }
    return nil;
}

static void place_window(NSWindow *window) {
    if (!g_enabled || window == nil || g_placed) {
        return;
    }
    NSRect frame = window.frame;
    /* Ignore panels and small helper windows.  The game window is the large
       one that carries the drawable; its size is preserved exactly. */
    if (frame.size.width < 800.0 || frame.size.height < 600.0) {
        return;
    }
    NSScreen *screen = screen_for_display(g_target_display);
    if (screen == nil) {
        return;
    }
    NSRect bounds = screen.frame;
    /* Only move if the window does not already fit entirely on the target. */
    if (NSContainsRect(bounds, frame)) {
        g_placed = YES;
        return;
    }
    NSPoint origin = NSMakePoint(bounds.origin.x + g_margin,
                                 bounds.origin.y + g_margin);
    CGFloat max_x = bounds.origin.x + bounds.size.width - frame.size.width;
    CGFloat max_y = bounds.origin.y + bounds.size.height - frame.size.height;
    if (origin.x > max_x) origin.x = max_x;
    if (origin.y > max_y) origin.y = max_y;
    frame.origin = origin;
    [window setFrame:frame display:NO];
    g_placed = YES;
}

static BOOL guard_activate_ignoring(id self, SEL _cmd, BOOL ignoring) {
    (void)self;
    (void)_cmd;
    (void)ignoring;
    return YES;
}

static void guard_activate(id self, SEL _cmd) {
    (void)self;
    (void)_cmd;
}

static BOOL guard_set_policy(id self, SEL _cmd, NSApplicationActivationPolicy policy) {
    (void)policy;
    if (g_enabled) {
        return g_set_policy_original(self, _cmd,
                                     NSApplicationActivationPolicyAccessory);
    }
    return g_set_policy_original(self, _cmd, policy);
}

static BOOL guard_running_activate(id self, SEL _cmd,
                                   NSApplicationActivationOptions options) {
    (void)self;
    (void)_cmd;
    (void)options;
    return YES;
}

static void guard_order_front(id self, SEL _cmd, id sender) {
    place_window(self);
    g_order_front_original(self, _cmd, sender);
}

static void guard_make_key(id self, SEL _cmd, id sender) {
    place_window(self);
    g_make_key_original(self, _cmd, sender);
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

__attribute__((constructor)) static void install_guard(void) {
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
    g_enabled = YES;
    swizzle_instance([NSApplication class], @selector(setActivationPolicy:),
                     (IMP)guard_set_policy, (IMP *)&g_set_policy_original);
}
