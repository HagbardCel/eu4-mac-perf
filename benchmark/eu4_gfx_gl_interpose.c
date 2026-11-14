/* Interposes glCopyTexSubImage2D for the original-GL observer.

   The engine's _GfxResolve is glCopyTexSubImage2D into the composite's sampled
   texture; the source is whatever framebuffer is bound as READ at that moment.
   The engine binds it outside the replaced _Gfx* entries, so this interposer
   records the effective READ/DRAW framebuffer bindings, read buffer, source
   attachment and destination level/extent for every copy.  Evidence only; the
   copy itself is forwarded unchanged. */

#define GL_SILENCE_DEPRECATION 1

#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "eu4_gfx_frame_trace.h"

typedef void (*copy_fn)(GLenum, GLint, GLint, GLint, GLint, GLint, GLsizei,
                        GLsizei);

static copy_fn g_real;
static uint64_t g_sequence;

static void eu4_gfx_interposed_copy_tex_sub_image(GLenum target, GLint level,
                                                  GLint xoffset, GLint yoffset,
                                                  GLint x, GLint y,
                                                  GLsizei width, GLsizei height);

/* Resolve the framework implementation, never this interposer.  RTLD_NEXT can
   return the replacement itself on macOS when the symbol is interposed, which
   would recurse and stall the engine's RenderPostEffects. */
static void *g_opengl_handle;

static copy_fn resolve_real_copy(void) {
    copy_fn fn = (copy_fn)dlsym(RTLD_NEXT, "glCopyTexSubImage2D");
    if (fn != NULL &&
        fn != (copy_fn)eu4_gfx_interposed_copy_tex_sub_image) {
        return fn;
    }
    if (g_opengl_handle == NULL) {
        g_opengl_handle = dlopen(
            "/System/Library/Frameworks/OpenGL.framework/OpenGL",
            RTLD_LAZY | RTLD_LOCAL);
    }
    if (g_opengl_handle == NULL) {
        return NULL;
    }
    fn = (copy_fn)dlsym(g_opengl_handle, "glCopyTexSubImage2D");
    if (fn == (copy_fn)eu4_gfx_interposed_copy_tex_sub_image) {
        return NULL;
    }
    return fn;
}

static uint64_t monotonic_ns(void) {
    struct timespec spec;
    clock_gettime(CLOCK_MONOTONIC, &spec);
    return (uint64_t)spec.tv_sec * 1000000000ull + (uint64_t)spec.tv_nsec;
}

static void write_line(const char *text) {
    const char *dir = getenv("EU4_GFX_FRAME_TRACE_DIR");
    if (dir == NULL || dir[0] == 0 || text == NULL) {
        return;
    }
    char path[1024];
    if (snprintf(path, sizeof path, "%s/copy-tex-sub.jsonl", dir) <= 0) {
        return;
    }
    FILE *file = fopen(path, "ab");
    if (file == NULL) {
        return;
    }
    fputs(text, file);
    fclose(file);
}

static void eu4_gfx_interposed_copy_tex_sub_image(GLenum target, GLint level,
                                                  GLint xoffset, GLint yoffset,
                                                  GLint x, GLint y,
                                                  GLsizei width, GLsizei height) {
    if (g_real == NULL) {
        g_real = resolve_real_copy();
        if (g_real == NULL) {
            return;
        }
    }
    int record = eu4_gfx_frame_trace_copy_mode();
    if (!record) {
        g_real(target, level, xoffset, yoffset, x, y, width, height);
        return;
    }
    GLint read_fbo = 0, draw_fbo = 0, fbo = 0;
    GLint read_buffer = 0, draw_buffer = 0, double_buffered = 0;
    GLint bound_texture = 0;
    glGetIntegerv(0x8CAA, &read_fbo); /* GL_READ_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8CA6, &draw_fbo); /* GL_DRAW_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x8D40, &fbo);      /* GL_FRAMEBUFFER_BINDING */
    glGetIntegerv(0x0C02, &read_buffer); /* GL_READ_BUFFER */
    glGetIntegerv(0x0C01, &draw_buffer); /* GL_DRAW_BUFFER */
    glGetIntegerv(0x0C32, &double_buffered); /* GL_DOUBLEBUFFER */
    glGetIntegerv(0x8069, &bound_texture);   /* GL_TEXTURE_BINDING_2D */
    GLint color_type = 0, color_name = 0, color_level = 0;
    GLint source_width = 0, source_height = 0, source_format = 0;
    if (read_fbo != 0) {
        GLint previous = 0;
        glGetIntegerv(0x8CAA, &previous);
        glBindFramebuffer(0x8CA8, (GLuint)read_fbo); /* GL_READ_FRAMEBUFFER */
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD0,
                                              &color_type);
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD1,
                                              &color_name);
        if (color_type == 0x1702) { /* GL_TEXTURE */
            glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD2,
                                                  &color_level);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                     GL_TEXTURE_WIDTH, &source_width);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                     GL_TEXTURE_HEIGHT, &source_height);
            glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level,
                                     GL_TEXTURE_INTERNAL_FORMAT,
                                     &source_format);
        }
        glBindFramebuffer(0x8CA8, (GLuint)previous);
    } else {
        source_width = (GLint)width;
        source_height = (GLint)height;
    }
    GLint destination_width = 0, destination_height = 0;
    GLint destination_format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_WIDTH,
                             &destination_width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_HEIGHT,
                             &destination_height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, level, GL_TEXTURE_INTERNAL_FORMAT,
                             &destination_format);
    eu4_gfx_copy_event event;
    memset(&event, 0, sizeof(event));
    event.is_blit = 0;
    event.source_framebuffer = read_fbo;
    event.read_buffer = read_buffer;
    event.draw_buffer = draw_buffer;
    event.double_buffered = double_buffered;
    event.source_color_type = color_type;
    event.source_color_name = color_name;
    event.source_color_level = color_level;
    event.source_texture = color_name;
    event.source_level = color_level;
    event.source_width = source_width;
    event.source_height = source_height;
    event.source_internal_format = source_format;
    event.source_resolved =
        read_fbo == 0 || (color_type == 0x1702 && color_name != 0);
    event.destination_resolved =
        target == GL_TEXTURE_2D && bound_texture != 0 && level == 0;
    event.destination_texture = bound_texture;
    event.destination_level = level;
    event.destination_width = destination_width;
    event.destination_height = destination_height;
    event.destination_internal_format = destination_format;
    event.x = x;
    event.y = y;
    event.width = width;
    event.height = height;
    event.dx = xoffset;
    event.dy = yoffset;
    char body[640];
    uint64_t sequence = ++g_sequence;
    int length = snprintf(
        body, sizeof body,
        "{\"seq\":%llu,\"pid\":%d,\"monotonic_ns\":%llu,"
        "\"read_fbo\":%d,\"draw_fbo\":%d,\"fbo\":%d,"
        "\"read_buffer\":%d,\"draw_buffer\":%d,\"double_buffered\":%d,"
        "\"color_type\":%d,\"color_name\":%d,\"color_level\":%d,"
        "\"source_width\":%d,\"source_height\":%d,\"source_format\":%d,"
        "\"source_resolved\":%d,\"texture\":%d,\"level\":%d,"
        "\"destination_width\":%d,\"destination_height\":%d,"
        "\"destination_format\":%d,"
        "\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d,"
        "\"xoffset\":%d,\"yoffset\":%d}\n",
        (unsigned long long)sequence, (int)getpid(),
        (unsigned long long)monotonic_ns(), read_fbo, draw_fbo, fbo,
        read_buffer, draw_buffer, double_buffered, color_type, color_name,
        color_level, source_width, source_height, source_format,
        event.source_resolved, bound_texture, level, destination_width,
        destination_height, destination_format, x, y, width, height, xoffset,
        yoffset);
    if (length > 0 && (size_t)length < sizeof body) {
        write_line(body);
    }
    (void)eu4_gfx_frame_trace_copy_event(&event);
    g_real(target, level, xoffset, yoffset, x, y, width, height);
}

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} eu4_gfx_copy_interpose __attribute__((section("__DATA,__interpose"))) = {
    (const void *)eu4_gfx_interposed_copy_tex_sub_image,
    (const void *)glCopyTexSubImage2D,
};
