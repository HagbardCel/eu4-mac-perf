/* Interposes glCopyTexSubImage2D for the original-GL observer.

   The engine's _GfxResolve is glCopyTexSubImage2D into the composite's sampled
   texture; the source is whatever framebuffer is bound as READ at that moment.
   The engine binds it outside the replaced _Gfx* entries, so this interposer
   records the READ/DRAW framebuffer bindings and the source color attachment
   for every copy. Evidence only; the copy itself is forwarded unchanged. */

#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef void (*copy_fn)(GLenum, GLint, GLint, GLint, GLint, GLint, GLsizei,
                        GLsizei);

static copy_fn g_real;

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
        g_real = (copy_fn)dlsym(RTLD_NEXT, "glCopyTexSubImage2D");
        if (g_real == NULL) {
            return;
        }
    }
    GLint read_fbo = 0, draw_fbo = 0, fbo = 0;
    GLint bound_texture = 0;
    glGetIntegerv(0x8CAA, &read_fbo);
    glGetIntegerv(0x8CA6, &draw_fbo);
    glGetIntegerv(0x8D40, &fbo);
    glGetIntegerv(0x8069 /* GL_TEXTURE_BINDING_2D */, &bound_texture);
    GLint color_type = 0, color_name = 0, color_level = 0;
    if (read_fbo != 0) {
        glBindFramebuffer(0x8CA8, (GLuint)read_fbo);
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD0,
                                              &color_type);
        glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD1,
                                              &color_name);
        if (color_type == 0x1702) {
            glGetFramebufferAttachmentParameteriv(0x8CA8, 0x8CE0, 0x8CD2,
                                                  &color_level);
        }
    }
    char body[320];
    int length = snprintf(
        body, sizeof body,
        "{\"read_fbo\":%d,\"draw_fbo\":%d,\"fbo\":%d,\"color_type\":%d,"
        "\"color_name\":%d,\"color_level\":%d,\"texture\":%d,"
        "\"x\":%d,\"y\":%d,\"width\":%d,\"height\":%d}\n",
        read_fbo, draw_fbo, fbo, color_type, color_name, color_level,
        bound_texture, x, y, width, height);
    if (length > 0 && (size_t)length < sizeof body) {
        write_line(body);
    }
    g_real(target, level, xoffset, yoffset, x, y, width, height);
}

__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} eu4_gfx_copy_interpose __attribute__((section("__DATA,__interpose"))) = {
    (const void *)eu4_gfx_interposed_copy_tex_sub_image,
    (const void *)glCopyTexSubImage2D,
};
