#import <AppKit/AppKit.h>
#import <OpenGL/OpenGL.h>

#include "eu4_gfx_observe_framebuffer.h"

/* Returns the Cocoa view's backing-store extent for the current NSOpenGL
 * context. This is the actual drawable pixel size, which can differ from the
 * engine's logical viewport on a Retina display. It is informational evidence:
 * the captured region is always the GL viewport the draw wrote. */
int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width == NULL || height == NULL) {
        return 0;
    }
    @autoreleasepool {
        NSOpenGLContext *context = [NSOpenGLContext currentContext];
        if (context == nil) {
            return 0;
        }
        NSView *view = [context view];
        if (view == nil || [view window] == nil) {
            return 0;
        }
        NSRect backing = [view convertRectToBacking:[view bounds]];
        if (backing.size.width < 1.0 || backing.size.height < 1.0) {
            return 0;
        }
        *width = (uint32_t)(backing.size.width + 0.5);
        *height = (uint32_t)(backing.size.height + 0.5);
    }
    return 1;
}
