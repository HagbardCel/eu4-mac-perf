#include "eu4_gfx_init.h"
#include "eu4_metal_events.h"

#define GL_SILENCE_DEPRECATION
#include <OpenGL/gl.h>
#include <stdint.h>
#include <string.h>
#include <dlfcn.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <unistd.h>

/* Symbols the six original bodies call, plus the next clear, viewport, program,
   buffer, and draw entries. Not every GL entry, and not a pointer the game
   cached before this interpose was bound. */

int eu4_metal_gl_should_forward(int mode, int phase) {
    /* before_init GL is startup such as SDL_GL_ExtensionSupported, not one of the six replacements. */
    if (mode != 3) {
        return 1;
    }
    return phase == 0;
}

static int g_test_mode = -1;
static int g_test_phase = -1;

void eu4_metal_gl_test_set_mode(int mode) { g_test_mode = mode; }
void eu4_metal_gl_test_set_phase(int phase) { g_test_phase = phase; }

#ifdef EU4_METAL_GL_NO_INTERPOSE
int eu4_gfx_phase(void) { return 0; }
#else
int eu4_metal_mode(void);
#endif

static int current_mode(void) {
    if (g_test_mode >= 0) {
        return g_test_mode;
    }
#ifdef EU4_METAL_GL_NO_INTERPOSE
    return 0;
#else
    return eu4_metal_mode();
#endif
}

static int forwarding(void) {
    int phase = g_test_phase >= 0 ? g_test_phase : eu4_gfx_phase();
    return eu4_metal_gl_should_forward(current_mode(), phase);
}

__attribute__((noreturn)) static void abort_gl(const char *name, const void *caller) {
    const char *phase = "before_init";
    int value = g_test_phase >= 0 ? g_test_phase : eu4_gfx_phase();
    if (value == 1) {
        phase = "during_init";
    } else if (value == 2) {
        phase = "after_init";
    }
    eu4_metal_events_stop(phase, name, "gl", caller);
    _exit(2);
}

#ifndef EU4_METAL_GL_NO_INTERPOSE
#define GL_BIND(repl, orig)                                                                   \
    __attribute__((used)) static const struct {                                               \
        const void *replacement;                                                              \
        const void *replacee;                                                                 \
    } _interpose_##orig __attribute__((section("__DATA,__interpose"))) = {                    \
        (const void *)(unsigned long)&(repl), (const void *)(unsigned long)&(orig)};
#else
#define GL_BIND(repl, orig)
#endif

#define GL_VOID1(name, T)                                                                     \
    static void (*real_##name)(T);                                                            \
    __attribute__((used)) static void eu4_##name(T a) {                                       \
        if (forwarding()) {                                                                   \
            if (real_##name != NULL) {                                                        \
                real_##name(a);                                                               \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name, __builtin_return_address(0));                                         \
    }                                                                                         \
    GL_BIND(eu4_##name, name)

#define GL_VOID2(name, T, U)                                                                  \
    static void (*real_##name)(T, U);                                                         \
    __attribute__((used)) static void eu4_##name(T a, U b) {                                  \
        if (forwarding()) {                                                                   \
            if (real_##name != NULL) {                                                        \
                real_##name(a, b);                                                            \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name, __builtin_return_address(0));                                         \
    }                                                                                         \
    GL_BIND(eu4_##name, name)

#define GL_VOID3(name, T, U, V)                                                               \
    static void (*real_##name)(T, U, V);                                                      \
    __attribute__((used)) static void eu4_##name(T a, U b, V c) {                             \
        if (forwarding()) {                                                                   \
            if (real_##name != NULL) {                                                        \
                real_##name(a, b, c);                                                         \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name, __builtin_return_address(0));                                         \
    }                                                                                         \
    GL_BIND(eu4_##name, name)

#define GL_VOID4(name, T, U, V, W)                                                            \
    static void (*real_##name)(T, U, V, W);                                                   \
    __attribute__((used)) static void eu4_##name(T a, U b, V c, W d) {                        \
        if (forwarding()) {                                                                   \
            if (real_##name != NULL) {                                                        \
                real_##name(a, b, c, d);                                                      \
            }                                                                                 \
            return;                                                                           \
        }                                                                                     \
        abort_gl(#name, __builtin_return_address(0));                                         \
    }                                                                                         \
    GL_BIND(eu4_##name, name)

GL_VOID1(glEnable, GLenum)
GL_VOID1(glDisable, GLenum)
GL_VOID1(glBlendEquation, GLenum)
GL_VOID1(glDepthFunc, GLenum)
GL_VOID1(glDepthMask, GLboolean)
GL_VOID1(glStencilMask, GLuint)
GL_VOID1(glCullFace, GLenum)
GL_VOID1(glFrontFace, GLenum)
GL_VOID1(glClear, GLbitfield)
GL_VOID1(glUseProgram, GLuint)
GL_VOID2(glBlendFunc, GLenum, GLenum)
GL_VOID2(glBlendEquationSeparate, GLenum, GLenum)
GL_VOID2(glPolygonMode, GLenum, GLenum)
GL_VOID2(glBindTexture, GLenum, GLuint)
GL_VOID2(glGetIntegerv, GLenum, GLint *)
GL_VOID2(glGetFloatv, GLenum, GLfloat *)
GL_VOID2(glGenTextures, GLsizei, GLuint *)
GL_VOID2(glGenBuffers, GLsizei, GLuint *)
GL_VOID2(glBindBuffer, GLenum, GLuint)
GL_VOID3(glTexParameteri, GLenum, GLenum, GLint)
GL_VOID3(glStencilFunc, GLenum, GLint, GLuint)
GL_VOID3(glStencilOp, GLenum, GLenum, GLenum)
GL_VOID4(glBlendFuncSeparate, GLenum, GLenum, GLenum, GLenum)
GL_VOID4(glColorMask, GLboolean, GLboolean, GLboolean, GLboolean)
GL_VOID4(glClearColor, GLfloat, GLfloat, GLfloat, GLfloat)
GL_VOID4(glScissor, GLint, GLint, GLsizei, GLsizei)
GL_VOID4(glViewport, GLint, GLint, GLsizei, GLsizei)
GL_VOID4(glDrawElements, GLenum, GLsizei, GLenum, const void *)

static const GLubyte *(*real_glGetString)(GLenum);
__attribute__((used)) static const GLubyte *eu4_glGetString(GLenum name) {
    if (forwarding()) {
        return real_glGetString != NULL ? real_glGetString(name) : NULL;
    }
    abort_gl("glGetString", __builtin_return_address(0));
}
GL_BIND(eu4_glGetString, glGetString);

static void (*real_glTexImage2D)(GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *);
__attribute__((used)) static void eu4_glTexImage2D(GLenum target, GLint level, GLint internal, GLsizei width,
                             GLsizei height, GLint border, GLenum format, GLenum type,
                             const void *pixels) {
    if (forwarding()) {
        if (real_glTexImage2D != NULL) {
            real_glTexImage2D(target, level, internal, width, height, border, format, type, pixels);
        }
        return;
    }
    abort_gl("glTexImage2D", __builtin_return_address(0));
}
GL_BIND(eu4_glTexImage2D, glTexImage2D);

static void (*real_glBufferData)(GLenum, GLsizeiptr, const void *, GLenum);
__attribute__((used)) static void eu4_glBufferData(GLenum target, GLsizeiptr size, const void *data, GLenum usage) {
    if (forwarding()) {
        if (real_glBufferData != NULL) {
            real_glBufferData(target, size, data, usage);
        }
        return;
    }
    abort_gl("glBufferData", __builtin_return_address(0));
}
GL_BIND(eu4_glBufferData, glBufferData);

void (*eu4_metal_gl_test_clear(void))(GLbitfield) { return eu4_glClear; }

static uint64_t read_uleb(const uint8_t **cursor, const uint8_t *end) {
    uint64_t value = 0;
    int shift = 0;
    while (*cursor < end && shift < 64) {
        uint8_t byte = *(*cursor)++;
        value |= (uint64_t)(byte & 0x7fu) << shift;
        if ((byte & 0x80u) == 0) {
            return value;
        }
        shift += 7;
    }
    return 0;
}

static const uint8_t *export_payload(const uint8_t *start, const uint8_t *end, const uint8_t *node,
                                     const char *symbol) {
    if (node < start || node >= end) {
        return NULL;
    }
    const uint8_t *cursor = node;
    uint64_t terminal = read_uleb(&cursor, end);
    const uint8_t *edges = cursor + terminal;
    if (symbol[0] == '\0') {
        return terminal != 0 ? cursor : NULL;
    }
    if (edges >= end) {
        return NULL;
    }
    uint8_t count = *edges++;
    for (uint8_t i = 0; i < count; i++) {
        const char *edge = (const char *)edges;
        size_t length = strnlen(edge, (size_t)(end - edges));
        edges += length + 1;
        uint64_t next = read_uleb(&edges, end);
        size_t common = 0;
        while (common < length && symbol[common] != '\0' && edge[common] == symbol[common]) {
            common++;
        }
        if (common == length) {
            return export_payload(start, end, start + next, symbol + common);
        }
    }
    return NULL;
}

static void *gl_symbol(const char *name) {
    /* dlsym and NSLookupSymbolInImage both return this interpose. Read libGL's export trie. */
    dlopen("/System/Library/Frameworks/OpenGL.framework/OpenGL", RTLD_LAZY | RTLD_LOCAL);
    const struct mach_header_64 *header = NULL;
    uint32_t index = 0;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *image = _dyld_get_image_name(i);
        if (image != NULL && strstr(image, "/libGL.dylib") != NULL) {
            header = (const struct mach_header_64 *)_dyld_get_image_header(i);
            index = i;
            break;
        }
    }
    uint32_t size = 0;
    if (header == NULL || header->magic != MH_MAGIC_64) {
        return NULL;
    }
    const uint8_t *trie = NULL;
    const uint8_t *command = (const uint8_t *)(header + 1);
    uint32_t offset = 0;
    uint64_t link_vm = 0;
    uint64_t link_file = 0;
    for (uint32_t i = 0; i < header->ncmds; i++) {
        const struct load_command *load = (const struct load_command *)command;
        if (load->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *segment = (const struct segment_command_64 *)command;
            if (strcmp(segment->segname, "__LINKEDIT") == 0) {
                link_vm = segment->vmaddr;
                link_file = segment->fileoff;
            }
        } else if (load->cmd == LC_DYLD_EXPORTS_TRIE) {
            const struct linkedit_data_command *data = (const struct linkedit_data_command *)command;
            offset = data->dataoff;
            size = data->datasize;
        } else if ((load->cmd == LC_DYLD_INFO || load->cmd == LC_DYLD_INFO_ONLY) && offset == 0) {
            const struct dyld_info_command *info = (const struct dyld_info_command *)command;
            offset = info->export_off;
            size = info->export_size;
        }
        command += load->cmdsize;
    }
    if (offset == 0 || size == 0) {
        return NULL;
    }
    trie = (const uint8_t *)(uintptr_t)(link_vm + (uint64_t)_dyld_get_image_vmaddr_slide(index) + offset - link_file);
    char decorated[64];
    size_t length = strlen(name);
    if (length + 2 > sizeof decorated) {
        return NULL;
    }
    decorated[0] = '_';
    memcpy(decorated + 1, name, length + 1);
    const uint8_t *payload = export_payload(trie, trie + size, trie, decorated);
    if (payload == NULL) {
        return NULL;
    }
    const uint8_t *cursor = payload;
    uint64_t flags = read_uleb(&cursor, trie + size);
    if ((flags & 0x18u) != 0) {
        return NULL;
    }
    return (void *)((uintptr_t)header + read_uleb(&cursor, trie + size));
}

static void bind_next(void *slot, const char *name) {
    void *sym = gl_symbol(name);
    Dl_info info;
    if (sym == NULL || dladdr(sym, &info) == 0 ||
        (info.dli_sname != NULL && strncmp(info.dli_sname, "eu4_gl", 6) == 0)) {
        sym = NULL;
    }
    memcpy(slot, &sym, sizeof sym);
}

#define BIND_NEXT(name) bind_next(&real_##name, #name)

/* Resolve once from libGL's export trie. dlsym returns this interpose. */
__attribute__((constructor)) static void eu4_gl_resolve(void) {
    BIND_NEXT(glEnable);
    BIND_NEXT(glDisable);
    BIND_NEXT(glBlendEquation);
    BIND_NEXT(glDepthFunc);
    BIND_NEXT(glDepthMask);
    BIND_NEXT(glStencilMask);
    BIND_NEXT(glCullFace);
    BIND_NEXT(glFrontFace);
    BIND_NEXT(glClear);
    BIND_NEXT(glUseProgram);
    BIND_NEXT(glBlendFunc);
    BIND_NEXT(glBlendEquationSeparate);
    BIND_NEXT(glPolygonMode);
    BIND_NEXT(glBindTexture);
    BIND_NEXT(glGetIntegerv);
    BIND_NEXT(glGetFloatv);
    BIND_NEXT(glGenTextures);
    BIND_NEXT(glGenBuffers);
    BIND_NEXT(glBindBuffer);
    BIND_NEXT(glTexParameteri);
    BIND_NEXT(glStencilFunc);
    BIND_NEXT(glStencilOp);
    BIND_NEXT(glBlendFuncSeparate);
    BIND_NEXT(glColorMask);
    BIND_NEXT(glClearColor);
    BIND_NEXT(glScissor);
    BIND_NEXT(glViewport);
    BIND_NEXT(glDrawElements);
    BIND_NEXT(glGetString);
    BIND_NEXT(glTexImage2D);
    BIND_NEXT(glBufferData);
}
