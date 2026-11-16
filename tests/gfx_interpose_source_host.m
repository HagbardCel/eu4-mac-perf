/* Native source-metadata regression for the copy interposer.

   The read framebuffer attachment is an 8x6 RGBA8 texture while the currently
   bound GL_TEXTURE_2D is a 4x4 texture.  The recorder must report the
   attachment's extent/format and restore the active unit, binding, framebuffer
   and read buffer.  A cube-face attachment must be marked unsupported. */
#define GL_SILENCE_DEPRECATION 1

#import <AppKit/AppKit.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include "eu4_gfx_frame_trace.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

static eu4_gfx_copy_event g_event;
static int g_events;
static int g_mutations;

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) *width = 8;
    if (height != NULL) *height = 6;
    return 0;
}

int eu4_gfx_frame_trace_copy_mode(void) { return 1; }
void eu4_gfx_frame_trace_texture_deleted(int count, const unsigned *names) {
    (void)count;
    (void)names;
}

void eu4_gfx_frame_trace_texture_mutated(unsigned name, int level,
                                         int sub_image, int texture_target) {
    (void)name; (void)level; (void)sub_image; (void)texture_target;
    g_mutations++;
}

int eu4_gfx_frame_trace_copy_event(const eu4_gfx_copy_event *event) {
    if (event != NULL) {
        g_event = *event;
        g_events++;
    }
    return 1;
}

void eu4_gfx_interpose_test_set_resolvers(void *(*next)(const char *),
                                          void *(*framework)(const char *));
void eu4_gfx_interpose_test_invoke(GLenum target, GLint level, GLint xoffset,
                                   GLint yoffset, GLint x, GLint y,
                                   GLsizei width, GLsizei height);
void eu4_gfx_interpose_test_mutate(GLenum target, GLint level, GLint xoffset,
                                   GLint yoffset, GLsizei width,
                                   GLsizei height, const void *pixels);

static void fake_original(GLenum target, GLint level, GLint xoffset,
                          GLint yoffset, GLint x, GLint y, GLsizei width,
                          GLsizei height) {
    (void)target; (void)level; (void)xoffset; (void)yoffset;
    (void)x; (void)y; (void)width; (void)height;
}

static void *resolver_fake(const char *name) {
    (void)name;
    return (void *)fake_original;
}

static void *resolver_missing(const char *name) {
    (void)name;
    return NULL;
}

static GLuint texture(GLenum target, GLint internal, GLsizei width,
                      GLsizei height) {
    GLuint name = 0;
    glGenTextures(1, &name);
    glBindTexture(target, name);
    glTexParameteri(target, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(target, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    if (target == GL_TEXTURE_2D) {
        glTexImage2D(target, 0, internal, width, height, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, NULL);
    } else {
        for (int face = 0; face < 6; face++) {
            glTexImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face, 0, internal,
                         width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
        }
    }
    return name;
}

static int run_case(int unsupported) {
    GLuint attachment = unsupported ? 0
        : texture(GL_TEXTURE_2D, GL_RGBA8, 8, 6);
    GLuint renderbuffer = 0;
    if (unsupported) {
        glGenRenderbuffers(1, &renderbuffer);
        glBindRenderbuffer(GL_RENDERBUFFER, renderbuffer);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_RGBA8, 8, 6);
    }
    GLuint bound = texture(GL_TEXTURE_2D, GL_RGB8, 4, 4);
    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    if (unsupported) {
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                  GL_RENDERBUFFER, renderbuffer);
    } else {
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                               GL_TEXTURE_2D, attachment, 0);
    }
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "framebuffer incomplete\n");
        return 0;
    }
    glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    glActiveTexture(GL_TEXTURE2);
    glBindTexture(GL_TEXTURE_2D, bound);
    unsigned char patch[4] = {1, 2, 3, 4};
    g_mutations = 0;
    eu4_gfx_interpose_test_mutate(GL_TEXTURE_2D, 0, 0, 0, 1, 1, patch);

    while (glGetError() != GL_NO_ERROR) {
    }
    glEnable(0xFFFF);
    g_events = 0;
    memset(&g_event, 0, sizeof(g_event));
    eu4_gfx_interpose_test_invoke(GL_TEXTURE_2D, 0, 0, 0, 0, 0, 8, 6);
    int pending = glGetError();

    GLint active = 0, binding = 0, read_fbo = 0, read_buffer = 0;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &binding);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_fbo);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);

    int ok = pending == 0x0500;
    ok = ok && g_events == 1;
    ok = ok && active == GL_TEXTURE2;
    ok = ok && binding == (GLint)bound;
    ok = ok && read_fbo == (GLint)fbo;
    ok = ok && read_buffer == GL_COLOR_ATTACHMENT0;
    ok = ok && g_mutations >= 1;
    if (unsupported) {
        ok = ok && !g_event.source_resolved;
    } else {
        ok = ok && g_event.source_resolved;
        ok = ok && g_event.source_width == 8 && g_event.source_height == 6;
        ok = ok && g_event.source_internal_format == 0x8058;
        ok = ok && g_event.source_attachment == GL_COLOR_ATTACHMENT0;
        ok = ok && g_event.read_buffer == GL_COLOR_ATTACHMENT0;
    }
    glDeleteFramebuffers(1, &fbo);
    if (attachment) {
        glDeleteTextures(1, &attachment);
    }
    if (renderbuffer) {
        glDeleteRenderbuffers(1, &renderbuffer);
    }
    glDeleteTextures(1, &bound);
    return ok;
}

int main(void) {
    [NSApplication sharedApplication];
    NSOpenGLPixelFormatAttribute attributes[] = {
        NSOpenGLPFAAccelerated, NSOpenGLPFAColorSize, 24,
        NSOpenGLPFAAlphaSize, 8, NSOpenGLPFADoubleBuffer, 0,
    };
    NSOpenGLPixelFormat *format =
        [[NSOpenGLPixelFormat alloc] initWithAttributes:attributes];
    NSOpenGLView *view =
        [[NSOpenGLView alloc] initWithFrame:NSMakeRect(0, 0, 8, 8)
                                pixelFormat:format];
    NSWindow *window =
        [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 8, 8)
                                    styleMask:NSWindowStyleMaskBorderless
                                      backing:NSBackingStoreBuffered
                                        defer:NO];
    [window setContentView:view];
    [window setFrameOrigin:NSMakePoint(-10000, -10000)];
    [window makeKeyAndOrderFront:nil];
    NSOpenGLContext *context = [view openGLContext];
    [context setView:view];
    [context update];
    [context makeCurrentContext];
    eu4_gfx_interpose_test_set_resolvers(resolver_fake, resolver_missing);
    int ok = run_case(0);
    printf("source_metadata=%d\n", ok);
    ok = run_case(1) && ok;
    printf("source_metadata_unsupported=%d\n", ok);
    return ok ? 0 : 1;
}
