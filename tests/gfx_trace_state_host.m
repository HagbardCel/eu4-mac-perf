/* Original-GL observer state-restoration regression.

   The frame tracer's texture readback binds a texture on the currently active
   unit. This host verifies that the probe used by that path restores the
   active unit's bindings and leaves the other units untouched. */

#import <AppKit/AppKit.h>
#import <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include "eu4_gfx_frame_trace.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) *width = 4;
    if (height != NULL) *height = 4;
    return 0;
}

static GLint binding_of(GLenum target) {
    GLint value = 0;
    glGetIntegerv(target, &value);
    return value;
}

static GLuint make_texture(GLenum target) {
    GLuint name = 0;
    glGenTextures(1, &name);
    glBindTexture(target, name);
    glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    if (target == GL_TEXTURE_2D) {
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, 4, 4, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, NULL);
    }
    return name;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <dir>\n", argv[0]);
        return 2;
    }
    NSOpenGLPixelFormatAttribute attributes[] = {
        NSOpenGLPFAAccelerated,
        NSOpenGLPFAColorSize, 24,
        NSOpenGLPFAAlphaSize, 8,
        NSOpenGLPFADoubleBuffer,
        0,
    };
    NSOpenGLPixelFormat *format =
        [[NSOpenGLPixelFormat alloc] initWithAttributes:attributes];
    if (format == nil) {
        fprintf(stderr, "pixel format\n");
        return 1;
    }
    [NSApplication sharedApplication];
    NSOpenGLView *view =
        [[NSOpenGLView alloc] initWithFrame:NSMakeRect(0, 0, 8, 8)
                                pixelFormat:format];
    if (view == nil) {
        fprintf(stderr, "view\n");
        return 1;
    }
    NSWindow *window =
        [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 8, 8)
                                    styleMask:NSWindowStyleMaskBorderless
                                      backing:NSBackingStoreBuffered
                                        defer:NO];
    [window setContentView:view];
    [window setFrameOrigin:NSMakePoint(-10000, -10000)];
    [window makeKeyAndOrderFront:nil];
    [view reshape];
    NSOpenGLContext *context = [view openGLContext];
    if (context == nil) {
        fprintf(stderr, "context\n");
        return 1;
    }
    [context setView:view];
    [context update];
    [context makeCurrentContext];

    GLuint a = make_texture(GL_TEXTURE_2D);
    GLuint b = make_texture(GL_TEXTURE_2D);
    GLuint c = make_texture(GL_TEXTURE_2D);
    GLuint cube_e = make_texture(GL_TEXTURE_CUBE_MAP);
    GLuint cube_f = make_texture(GL_TEXTURE_CUBE_MAP);

    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, a);
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, b);
    glBindTexture(GL_TEXTURE_CUBE_MAP, cube_e);

    /* A pack state that the engine may legitimately leave set at draw time.
       The readback must restore all of it, including a bound pack buffer. */
    glPixelStorei(GL_PACK_ALIGNMENT, 8);
    glPixelStorei(GL_PACK_ROW_LENGTH, 33);
    glPixelStorei(GL_PACK_SKIP_ROWS, 2);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 3);
    GLuint pack_buffer = 0;
    glGenBuffers(1, &pack_buffer);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pack_buffer);

    int ok = 1;
    if (eu4_gfx_frame_trace_texture_probe(GL_TEXTURE_2D, c) != 0) {
        ok = 0;
    }
    GLint active = 0;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    if (active != GL_TEXTURE3) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_2D) != (GLint)b) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_CUBE_MAP) != (GLint)cube_e) {
        ok = 0;
    }
    glActiveTexture(GL_TEXTURE0);
    if (binding_of(GL_TEXTURE_BINDING_2D) != (GLint)a) {
        ok = 0;
    }
    glActiveTexture(GL_TEXTURE3);

    GLint pack_alignment = 0, pack_row = 0, pack_skip_rows = 0;
    GLint pack_skip_pixels = 0, pack_bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &pack_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &pack_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &pack_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &pack_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_bound);
    if (pack_alignment != 8 || pack_row != 33 || pack_skip_rows != 2 ||
        pack_skip_pixels != 3 || pack_bound != (GLint)pack_buffer) {
        ok = 0;
    }

    /* A cube probe on the active unit restores both target bindings there. */
    if (eu4_gfx_frame_trace_texture_probe(GL_TEXTURE_CUBE_MAP, cube_f) != 0) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_CUBE_MAP) != (GLint)cube_e ||
        binding_of(GL_TEXTURE_BINDING_2D) != (GLint)b) {
        ok = 0;
    }
    /* Draw the intended default back buffer with a known image, then leave a
       different offscreen framebuffer selected as READ/DRAW.  The final
       reference must come from the default back buffer, not attachment zero of
       the selected FBO. */
    glBindFramebuffer(GL_FRAMEBUFFER, 0);
    glDrawBuffer(GL_BACK);
    glReadBuffer(GL_BACK);
    glViewport(0, 0, 4, 4);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();

    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, c, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "offscreen framebuffer incomplete\n");
        return 1;
    }
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glClearColor(1.0f, 0.0f, 0.0f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    glFinish();

    /* Plant one known sentinel error without querying it first.  The observer
       must not drain the engine's queue; glGetError is destructive. */
    while (glGetError() != GL_NO_ERROR) {
    }
    glEnable(0xFFFF);
    int pending = 0x0500;
    if (eu4_gfx_frame_trace_test_reference(argv[1]) != 0) {
        ok = 0;
    }
    int after = glGetError();
    if (after != pending) {
        ok = 0;
    }
    GLint draw_after = 0, read_after = 0, read_buffer_after = 0;
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_after);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_after);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer_after);
    if (draw_after != (GLint)fbo || read_after != (GLint)fbo ||
        read_buffer_after != GL_COLOR_ATTACHMENT0) {
        ok = 0;
    }
    GLint final_alignment = 0, final_row = 0, final_skip_rows = 0;
    GLint final_skip_pixels = 0, final_bound = 0;
    glGetIntegerv(GL_PACK_ALIGNMENT, &final_alignment);
    glGetIntegerv(GL_PACK_ROW_LENGTH, &final_row);
    glGetIntegerv(GL_PACK_SKIP_ROWS, &final_skip_rows);
    glGetIntegerv(GL_PACK_SKIP_PIXELS, &final_skip_pixels);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &final_bound);
    if (final_alignment != 8 || final_row != 33 || final_skip_rows != 2 ||
        final_skip_pixels != 3 || final_bound != (GLint)pack_buffer) {
        ok = 0;
    }

    char reference_path[1024];
    snprintf(reference_path, sizeof(reference_path),
             "%s/frame-reference.bgra", argv[1]);
    FILE *reference = fopen(reference_path, "rb");
    int reference_ok = 0;
    if (reference != NULL) {
        uint8_t pixels[4 * 4 * 4];
        size_t got = fread(pixels, 1, sizeof(pixels), reference);
        int extra = fgetc(reference);
        fclose(reference);
        reference_ok = got == sizeof(pixels) && extra == EOF;
        for (size_t i = 0; reference_ok && i < 16; i++) {
            const uint8_t expected[4] = {191, 128, 64, 255};
            if (memcmp(pixels + i * 4, expected, 4) != 0) {
                reference_ok = 0;
            }
        }
    }
    if (!reference_ok) {
        ok = 0;
    }
    printf("trace_state=%d pending=0x%x after=0x%x reference=%d\n", ok,
           pending, after, reference_ok);
    return ok ? 0 : 1;
}
