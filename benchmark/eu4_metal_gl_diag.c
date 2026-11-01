#include "eu4_gfx_init.h"
#include "eu4_metal_events.h"

#define GL_SILENCE_DEPRECATION
#include <OpenGL/gl.h>
#include <dlfcn.h>
#include <unistd.h>

/* Symbols the six original bodies call. Not every GL entry, and not a
   pointer the game cached before this interpose was bound. */

int eu4_metal_gl_should_forward(int mode, int phase) {
    (void)phase;
    return mode != 3;
}

#ifndef EU4_METAL_GL_NO_INTERPOSE

int eu4_metal_mode(void);

static int forwarding(void) {
    return eu4_metal_gl_should_forward(eu4_metal_mode(), eu4_gfx_phase());
}

static void abort_gl(const char *name) {
    const char *phase = "before_init";
    int value = eu4_gfx_phase();
    if (value == 1) {
        phase = "during_init";
    } else if (value == 2) {
        phase = "after_init";
    }
    eu4_metal_events_stop(phase, name, "gl", __builtin_return_address(0));
    _exit(2);
}

#define DYLD_INTERPOSE(repl, orig)                                                            \
    __attribute__((used)) static const struct {                                               \
        const void *replacement;                                                              \
        const void *replacee;                                                                 \
    } _interpose_##orig __attribute__((section("__DATA,__interpose"))) = {                    \
        (const void *)(unsigned long)&(repl), (const void *)(unsigned long)&(orig)};

#define GL_VOID1(name, T)                                                                     \
    static void eu4_##name(T a) {                                                             \
        if (forwarding()) {                                                                   \
            void (*real)(T) = dlsym(RTLD_NEXT, #name);                                        \
            if (real != NULL) {                                                               \
                real(a);                                                                      \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name);                                                                      \
    }                                                                                         \
    DYLD_INTERPOSE(eu4_##name, name)

#define GL_VOID2(name, T, U)                                                                  \
    static void eu4_##name(T a, U b) {                                                        \
        if (forwarding()) {                                                                   \
            void (*real)(T, U) = dlsym(RTLD_NEXT, #name);                                     \
            if (real != NULL) {                                                               \
                real(a, b);                                                                   \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name);                                                                      \
    }                                                                                         \
    DYLD_INTERPOSE(eu4_##name, name)

#define GL_VOID3(name, T, U, V)                                                               \
    static void eu4_##name(T a, U b, V c) {                                                   \
        if (forwarding()) {                                                                   \
            void (*real)(T, U, V) = dlsym(RTLD_NEXT, #name);                                  \
            if (real != NULL) {                                                               \
                real(a, b, c);                                                                \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name);                                                                      \
    }                                                                                         \
    DYLD_INTERPOSE(eu4_##name, name)

#define GL_VOID4(name, T, U, V, W)                                                            \
    static void eu4_##name(T a, U b, V c, W d) {                                              \
        if (forwarding()) {                                                                   \
            void (*real)(T, U, V, W) = dlsym(RTLD_NEXT, #name);                               \
            if (real != NULL) {                                                               \
                real(a, b, c, d);                                                             \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name);                                                                      \
    }                                                                                         \
    DYLD_INTERPOSE(eu4_##name, name)

GL_VOID1(glEnable, GLenum)
GL_VOID1(glDisable, GLenum)
GL_VOID1(glBlendEquation, GLenum)
GL_VOID1(glDepthFunc, GLenum)
GL_VOID1(glDepthMask, GLboolean)
GL_VOID1(glStencilMask, GLuint)
GL_VOID1(glCullFace, GLenum)
GL_VOID1(glFrontFace, GLenum)
GL_VOID2(glBlendFunc, GLenum, GLenum)
GL_VOID2(glBlendEquationSeparate, GLenum, GLenum)
GL_VOID2(glPolygonMode, GLenum, GLenum)
GL_VOID2(glBindTexture, GLenum, GLuint)
GL_VOID2(glGetIntegerv, GLenum, GLint *)
GL_VOID2(glGetFloatv, GLenum, GLfloat *)
GL_VOID2(glGenTextures, GLsizei, GLuint *)
GL_VOID3(glTexParameteri, GLenum, GLenum, GLint)
GL_VOID3(glStencilFunc, GLenum, GLint, GLuint)
GL_VOID3(glStencilOp, GLenum, GLenum, GLenum)
GL_VOID4(glBlendFuncSeparate, GLenum, GLenum, GLenum, GLenum)
GL_VOID4(glColorMask, GLboolean, GLboolean, GLboolean, GLboolean)

static const GLubyte *eu4_glGetString(GLenum name) {
    if (forwarding()) {
        const GLubyte *(*real)(GLenum) = dlsym(RTLD_NEXT, "glGetString");
        return real != NULL ? real(name) : NULL;
    }
    abort_gl("glGetString");
}
DYLD_INTERPOSE(eu4_glGetString, glGetString);

static void eu4_glTexImage2D(GLenum target, GLint level, GLint internal, GLsizei width,
                             GLsizei height, GLint border, GLenum format, GLenum type,
                             const void *pixels) {
    if (forwarding()) {
        void (*real)(GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *) =
            dlsym(RTLD_NEXT, "glTexImage2D");
        if (real != NULL) {
            real(target, level, internal, width, height, border, format, type, pixels);
        }
        return;
    }
    abort_gl("glTexImage2D");
}
DYLD_INTERPOSE(eu4_glTexImage2D, glTexImage2D);

#endif
