/* Legacy GL-tail interposer; not linked into LIBRARY_BORDER (capability 2 slim-down). */
#define GL_SILENCE_DEPRECATION 1
#include "eu4_submission_border.h"

#include <dlfcn.h>
#include <OpenGL/OpenGL.h>
#include <stddef.h>
#include <stdint.h>

static uint32_t border_site_from_return_address(void *return_addr) {
    Dl_info info;
    if (!dladdr(return_addr, &info) || info.dli_fbase == NULL) {
        return UINT32_MAX;
    }
    const uintptr_t base = (uintptr_t)info.dli_fbase;
    const uintptr_t ret = (uintptr_t)return_addr;
    if (ret < base) {
        return UINT32_MAX;
    }
    const uintptr_t off = ret - base;
    if (off == 0x10cc2edu) {
        return 0;
    }
    if (off == 0x10cc357u) {
        return 1;
    }
    if (off == 0x10cc3bdu) {
        return 2;
    }
    return UINT32_MAX;
}

static void interposed_glDrawElementsBaseVertex(
    GLenum mode,
    GLsizei count,
    GLenum type,
    const void *indices,
    GLint basevertex) {
    eu4_border_init_gl_apis();
    const uint32_t site = border_site_from_return_address(__builtin_return_address(0));
    if (site == UINT32_MAX) {
        eu4_border_flush_pending();
        typedef void (*fn)(GLenum, GLsizei, GLenum, const void *, GLint);
        fn real = (fn)dlsym(RTLD_NEXT, "glDrawElementsBaseVertex");
        if (real) {
            real(mode, count, type, indices, basevertex);
        }
        return;
    }
    eu4_border_on_draw_elements_base_vertex(mode, count, type, indices, basevertex, site);
}

extern void glDrawElementsBaseVertex(GLenum, GLsizei, GLenum, const void *, GLint);

__attribute__((used, section("__DATA,__interpose"))) static const struct {
    const void *replacement;
    const void *replacee;
} eu4_border_gl_interpose[] = {
    {(const void *)interposed_glDrawElementsBaseVertex, (const void *)glDrawElementsBaseVertex},
};
