/* Original-GL observer state-restoration regression.

   The frame tracer's texture readback binds a texture on the currently active
   unit. This host verifies that the probe used by that path restores the
   active unit's bindings and leaves the other units untouched. */

#import <AppKit/AppKit.h>
#import <OpenGL/gl.h>

#include "eu4_gfx_frame_trace.h"

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

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
    (void)width; (void)height;
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

int main(void) {
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
    NSOpenGLContext *context =
        [[NSOpenGLContext alloc] initWithFormat:format shareContext:nil];
    if (context == nil) {
        fprintf(stderr, "context\n");
        return 1;
    }
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

    /* A cube probe on the active unit restores both target bindings there. */
    if (eu4_gfx_frame_trace_texture_probe(GL_TEXTURE_CUBE_MAP, cube_f) != 0) {
        ok = 0;
    }
    if (binding_of(GL_TEXTURE_BINDING_CUBE_MAP) != (GLint)cube_e ||
        binding_of(GL_TEXTURE_BINDING_2D) != (GLint)b) {
        ok = 0;
    }
    printf("trace_state=%d\n", ok);
    return ok ? 0 : 1;
}
