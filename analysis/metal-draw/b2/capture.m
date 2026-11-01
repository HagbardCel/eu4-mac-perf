/* One mesh draw. The wrapper is local to this file and is not a hook framework.
   Only an ordinary glDrawElements is recorded, and only while the patched call is on the stack. */
#define GL_SILENCE_DEPRECATION 1
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl.h>
#include <mach-o/dyld.h>
#include <mach-o/loader.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <libkern/OSCacheControl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#ifndef GL_READ_FRAMEBUFFER
#define GL_READ_FRAMEBUFFER 0x8CA8
#define GL_DRAW_FRAMEBUFFER 0x8CA9
#define GL_READ_FRAMEBUFFER_BINDING 0x8CAA
#define GL_DRAW_FRAMEBUFFER_BINDING 0x8CAB
#endif
#ifndef GL_PIXEL_PACK_BUFFER_BINDING
#define GL_PIXEL_PACK_BUFFER_BINDING 0x88ED
#endif
#ifndef GL_PRIMITIVE_RESTART
#define GL_PRIMITIVE_RESTART 0x8F9D
#endif

enum { kMaxAttrib = 4, kMaxBytes = 1 << 20 };

static const uint32_t kMeshRva = 0x14c8404u;
static const uint32_t kIndexedRva = 0x15eaa77u;
static const uint8_t kMeshExpected[5] = {0xe8, 0x6e, 0x26, 0x12, 0x00};

void *g_b2_original;
static void *g_installed_site;
static uint8_t g_saved_bytes[5];

static void *decode_target(const void *site) {
    int32_t relative = 0;
    memcpy(&relative, (const uint8_t *)site + 1, 4);
    return (void *)((uintptr_t)site + 5 + (intptr_t)relative);
}

static _Thread_local int g_depth;
static _Thread_local int g_in_original;
static char g_dir[1024];
static int g_smoke;
static const char *g_reject;
static char g_reject_text[96];
static int g_draws;
static int g_prepare_count;

typedef struct {
    int location, size, type, normalized, stride, offset;
    char name[64];
} Attrib;

typedef struct {
    int mode, count, index_type, index_offset;
    int width, height, depth_test, depth_write, depth_func;
    int depth_matches_b1, blend, cull, front_ccw, scissor;
    int scissor_box[4], stride, attrib_count;
    int vert_bytes, index_bytes;
    Attrib attribs[kMaxAttrib];
    void *verts;
    void *indices;
    char *vert_src;
    char *frag_src;
} Capture;

static _Thread_local Capture g_cap;

typedef struct {
    GLint read_fbo, draw_fbo, viewport[4], scissor_box[4], scissor, program, vao;
    GLint array_buf, elem_buf, active_tex, tex_binding, rbo;
    GLint blend, blend_src, blend_dst, blend_eq, depth_test, depth_mask, depth_func;
    GLint stencil, cull, cull_mode, front, pack;
    GLfloat clear_color[4];
    GLdouble clear_depth;
    GLboolean color_mask[4];
} Saved;

int eu4_b2_claim(void) {
    if (g_depth != 0) return 0;
    g_depth = 1;
    return 1;
}

void eu4_b2_release(void) {
    g_depth = 0;
    g_in_original = 0;
}

void eu4_b2_prepare(void) {
    g_prepare_count++;
    free(g_cap.vert_src);
    free(g_cap.frag_src);
    free(g_cap.verts);
    free(g_cap.indices);
    memset(&g_cap, 0, sizeof g_cap);
    g_reject = NULL;
    g_draws = 0;
}

void eu4_b2_arm_original(void) {
    g_in_original = 1;
}

void eu4_b2_disarm_original(void) {
    g_in_original = 0;
}

/* %r10 is caller-saved and is not an argument register. %rcx stays the fourth argument. */
__asm__(
    ".globl _eu4_b2_enter_mesh\n"
    "_eu4_b2_enter_mesh:\n"
    "    pushq %rbp\n"
    "    movq %rsp, %rbp\n"
    "    subq $0x160, %rsp\n"
    "    movq %rdi, -0x10(%rbp)\n"
    "    movq %rsi, -0x18(%rbp)\n"
    "    movq %rdx, -0x20(%rbp)\n"
    "    movq %rcx, -0x28(%rbp)\n"
    "    movq %r8, -0x30(%rbp)\n"
    "    movq %r9, -0x38(%rbp)\n"
    "    movaps %xmm0, -0x50(%rbp)\n"
    "    movaps %xmm1, -0x60(%rbp)\n"
    "    movaps %xmm2, -0x70(%rbp)\n"
    "    movaps %xmm3, -0x80(%rbp)\n"
    "    movaps %xmm4, -0x90(%rbp)\n"
    "    movaps %xmm5, -0xa0(%rbp)\n"
    "    movaps %xmm6, -0xb0(%rbp)\n"
    "    movaps %xmm7, -0xc0(%rbp)\n"
    "    call _eu4_b2_claim\n"
    "    testl %eax, %eax\n"
    "    jz L_b2_reenter\n"
    "    call _eu4_b2_prepare\n"
    "    call _eu4_b2_arm_original\n"
    "    movq -0x10(%rbp), %rdi\n"
    "    movq -0x18(%rbp), %rsi\n"
    "    movq -0x20(%rbp), %rdx\n"
    "    movq -0x28(%rbp), %rcx\n"
    "    movq -0x30(%rbp), %r8\n"
    "    movq -0x38(%rbp), %r9\n"
    "    movaps -0x50(%rbp), %xmm0\n"
    "    movaps -0x60(%rbp), %xmm1\n"
    "    movaps -0x70(%rbp), %xmm2\n"
    "    movaps -0x80(%rbp), %xmm3\n"
    "    movaps -0x90(%rbp), %xmm4\n"
    "    movaps -0xa0(%rbp), %xmm5\n"
    "    movaps -0xb0(%rbp), %xmm6\n"
    "    movaps -0xc0(%rbp), %xmm7\n"
    "    movq 16(%rbp), %rax\n"
    "    movq %rax, -0x160(%rbp)\n"
    "    movq 24(%rbp), %rax\n"
    "    movq %rax, -0x158(%rbp)\n"
    "    movq 32(%rbp), %rax\n"
    "    movq %rax, -0x150(%rbp)\n"
    "    movq 40(%rbp), %rax\n"
    "    movq %rax, -0x148(%rbp)\n"
    "    movq 48(%rbp), %rax\n"
    "    movq %rax, -0x140(%rbp)\n"
    "    movq 56(%rbp), %rax\n"
    "    movq %rax, -0x138(%rbp)\n"
    "    movq 64(%rbp), %rax\n"
    "    movq %rax, -0x130(%rbp)\n"
    "    movq 72(%rbp), %rax\n"
    "    movq %rax, -0x128(%rbp)\n"
    "    leaq _g_b2_original(%rip), %r10\n"
    "    callq *(%r10)\n"
    "    movq %rax, -0xd0(%rbp)\n"
    "    call _eu4_b2_disarm_original\n"
    "    call _eu4_b2_after\n"
    "    call _eu4_b2_release\n"
    "    movq -0xd0(%rbp), %rax\n"
    "    movq %rbp, %rsp\n"
    "    popq %rbp\n"
    "    ret\n"
    "L_b2_reenter:\n"
    "    movq -0x10(%rbp), %rdi\n"
    "    movq -0x18(%rbp), %rsi\n"
    "    movq -0x20(%rbp), %rdx\n"
    "    movq -0x28(%rbp), %rcx\n"
    "    movq -0x30(%rbp), %r8\n"
    "    movq -0x38(%rbp), %r9\n"
    "    movaps -0x50(%rbp), %xmm0\n"
    "    movaps -0x60(%rbp), %xmm1\n"
    "    movaps -0x70(%rbp), %xmm2\n"
    "    movaps -0x80(%rbp), %xmm3\n"
    "    movaps -0x90(%rbp), %xmm4\n"
    "    movaps -0xa0(%rbp), %xmm5\n"
    "    movaps -0xb0(%rbp), %xmm6\n"
    "    movaps -0xc0(%rbp), %xmm7\n"
    "    movq 16(%rbp), %rax\n"
    "    movq %rax, -0x160(%rbp)\n"
    "    movq 24(%rbp), %rax\n"
    "    movq %rax, -0x158(%rbp)\n"
    "    movq 32(%rbp), %rax\n"
    "    movq %rax, -0x150(%rbp)\n"
    "    movq 40(%rbp), %rax\n"
    "    movq %rax, -0x148(%rbp)\n"
    "    movq 48(%rbp), %rax\n"
    "    movq %rax, -0x140(%rbp)\n"
    "    movq 56(%rbp), %rax\n"
    "    movq %rax, -0x138(%rbp)\n"
    "    movq 64(%rbp), %rax\n"
    "    movq %rax, -0x130(%rbp)\n"
    "    movq 72(%rbp), %rax\n"
    "    movq %rax, -0x128(%rbp)\n"
    "    leaq _g_b2_original(%rip), %r10\n"
    "    callq *(%r10)\n"
    "    movq %rbp, %rsp\n"
    "    popq %rbp\n"
    "    ret\n"
);

extern uintptr_t eu4_b2_enter_mesh(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t);
void eu4_b2_after(void);

static void note_reject(const char *reason) {
    if (g_reject == NULL) {
        snprintf(g_reject_text, sizeof g_reject_text, "%s", reason);
        g_reject = g_reject_text;
    }
    if (g_dir[0] != '/') return;
    char path[1100];
    snprintf(path, sizeof path, "%s/rejects.log", g_dir);
    FILE *out = fopen(path, "a");
    if (out == NULL) return;
    fprintf(out, "mesh: %s\n", reason);
    fclose(out);
}

static int type_size(int type) {
    if (type == GL_FLOAT) return 4;
    if (type == GL_UNSIGNED_BYTE) return 1;
    return 0;
}

static void *copy_buffer(GLenum target, GLuint id, int *out_size) {
    GLenum binding_enum = target == GL_ELEMENT_ARRAY_BUFFER ? GL_ELEMENT_ARRAY_BUFFER_BINDING
                                                            : GL_ARRAY_BUFFER_BINDING;
    GLint prev = 0, size = 0, mapped = 0;
    glGetIntegerv(binding_enum, &prev);
    glBindBuffer(target, id);
    glGetBufferParameteriv(target, GL_BUFFER_SIZE, &size);
    glGetBufferParameteriv(target, GL_BUFFER_MAPPED, &mapped);
    void *data = NULL;
    if (id == 0 || mapped || size <= 0 || size > kMaxBytes) {
        note_reject(id == 0 ? "client vertex memory" : mapped ? "mapped buffer" : "buffer over 1 MiB");
    } else {
        data = malloc((size_t)size);
        if (data == NULL) note_reject("out of memory");
        else glGetBufferSubData(target, 0, size, data);
    }
    glBindBuffer(target, (GLuint)prev);
    *out_size = data ? size : 0;
    return data;
}

static char *shader_source(GLuint shader) {
    GLint length = 0;
    glGetShaderiv(shader, GL_SHADER_SOURCE_LENGTH, &length);
    if (length <= 1 || length > 262144) {
        note_reject("shader source length");
        return NULL;
    }
    char *text = malloc((size_t)length);
    if (text == NULL) {
        note_reject("out of memory");
        return NULL;
    }
    glGetShaderSource(shader, length, NULL, text);
    return text;
}

static void snapshot_program(void) {
    GLint program = 0, count = 0, uniforms = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    if (program == 0) {
        note_reject("no program");
        return;
    }
    GLuint shaders[8];
    glGetAttachedShaders((GLuint)program, 8, &count, shaders);
    if (count != 2) {
        note_reject("shader count");
        return;
    }
    for (int i = 0; i < count; i++) {
        GLint type = 0;
        glGetShaderiv(shaders[i], GL_SHADER_TYPE, &type);
        char *text = shader_source(shaders[i]);
        if (type == GL_VERTEX_SHADER) g_cap.vert_src = text;
        else if (type == GL_FRAGMENT_SHADER) g_cap.frag_src = text;
        else note_reject("shader stage");
    }
    if (g_cap.vert_src == NULL || g_cap.frag_src == NULL) note_reject("missing stage");
    glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS, &uniforms);
    if (uniforms != 0) note_reject("uniforms");
}

static void snapshot_attribs(void) {
    GLint stride_seen = -1;
    int binding_seen = 0;
    for (int index = 0; index < 16; index++) {
        GLint enabled = 0;
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_ENABLED, &enabled);
        if (!enabled) continue;
        if (g_cap.attrib_count >= kMaxAttrib) {
            note_reject("too many attributes");
            return;
        }
        Attrib *attrib = &g_cap.attribs[g_cap.attrib_count];
        GLint size = 0, type = 0, norm = 0, stride = 0, binding = 0;
        void *pointer = NULL;
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_SIZE, &size);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_TYPE, &type);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_NORMALIZED, &norm);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_STRIDE, &stride);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, &binding);
        glGetVertexAttribPointerv(index, GL_VERTEX_ATTRIB_ARRAY_POINTER, &pointer);
        if ((type != GL_FLOAT && type != GL_UNSIGNED_BYTE) || size < 1 || size > 4 || binding == 0) {
            note_reject("attribute format");
            return;
        }
        if (binding_seen == 0) {
            binding_seen = binding;
            g_cap.verts = copy_buffer(GL_ARRAY_BUFFER, (GLuint)binding, &g_cap.vert_bytes);
        } else if (binding != binding_seen) {
            note_reject("more than one vertex buffer");
            return;
        }
        if (stride == 0) stride = size * type_size(type);
        if (stride_seen < 0) stride_seen = stride;
        else if (stride != stride_seen) {
            note_reject("stride mismatch");
            return;
        }
        attrib->location = index;
        attrib->size = size;
        attrib->type = type;
        attrib->normalized = norm;
        attrib->stride = stride;
        attrib->offset = (int)(uintptr_t)pointer;
        GLint program = 0, active = 0;
        glGetIntegerv(GL_CURRENT_PROGRAM, &program);
        glGetProgramiv((GLuint)program, GL_ACTIVE_ATTRIBUTES, &active);
        for (int a = 0; a < active; a++) {
            GLsizei length = 0;
            GLint asize = 0;
            GLenum atype = 0;
            glGetActiveAttrib((GLuint)program, (GLuint)a, (GLsizei)sizeof attrib->name, &length, &asize, &atype,
                              attrib->name);
            if (glGetAttribLocation((GLuint)program, attrib->name) == index) break;
            attrib->name[0] = 0;
        }
        if (attrib->name[0] == 0) {
            note_reject("attribute name");
            return;
        }
        g_cap.attrib_count++;
    }
    if (g_cap.attrib_count == 0) note_reject("no attributes");
    g_cap.stride = stride_seen < 0 ? 0 : stride_seen;
}

static void snapshot_state(void) {
    GLint viewport[4] = {0}, samples = 0, srgb = 0, scissor = 0;
    GLdouble depth_range[2] = {0, 0};
    glGetIntegerv(GL_VIEWPORT, viewport);
    glGetIntegerv(GL_SAMPLE_BUFFERS, &samples);
    glGetIntegerv(GL_FRAMEBUFFER_SRGB, &srgb);
    if (samples != 0 || srgb) {
        note_reject(samples ? "multisample" : "srgb");
        return;
    }
    if (viewport[2] < 1 || viewport[3] < 1 || viewport[2] > 4096 || viewport[3] > 4096) {
        note_reject("viewport");
        return;
    }
    g_cap.width = viewport[2];
    g_cap.height = viewport[3];
    /* GL 2.1 raises INVALID_ENUM for this query, and that error cannot be put back. */
    const char *version = (const char *)glGetString(GL_VERSION);
    if (version != NULL && version[0] >= '3' && glIsEnabled(GL_PRIMITIVE_RESTART)) {
        note_reject("primitive restart");
        return;
    }
    glGetDoublev(GL_DEPTH_RANGE, depth_range);
    if (depth_range[0] != 0.0 || depth_range[1] != 1.0) {
        note_reject("depth range");
        return;
    }
    if (glIsEnabled(GL_POLYGON_OFFSET_FILL)) {
        note_reject("polygon offset");
        return;
    }
    g_cap.depth_test = glIsEnabled(GL_DEPTH_TEST) ? 1 : 0;
    glGetIntegerv(GL_DEPTH_WRITEMASK, &g_cap.depth_write);
    glGetIntegerv(GL_DEPTH_FUNC, &g_cap.depth_func);
    if (glIsEnabled(GL_STENCIL_TEST)) {
        note_reject("stencil test enabled");
        return;
    }
    g_cap.depth_matches_b1 = g_cap.depth_test && g_cap.depth_write && g_cap.depth_func == GL_LEQUAL;
    g_cap.blend = glIsEnabled(GL_BLEND) ? 1 : 0;
    if (g_cap.blend) {
        GLint src = 0, dst = 0, eq = 0, srca = 0, dsta = 0, eqa = 0;
        glGetIntegerv(GL_BLEND_SRC_RGB, &src);
        glGetIntegerv(GL_BLEND_DST_RGB, &dst);
        glGetIntegerv(GL_BLEND_EQUATION_RGB, &eq);
        glGetIntegerv(GL_BLEND_SRC_ALPHA, &srca);
        glGetIntegerv(GL_BLEND_DST_ALPHA, &dsta);
        glGetIntegerv(GL_BLEND_EQUATION_ALPHA, &eqa);
        if (src != GL_SRC_ALPHA || dst != GL_ONE_MINUS_SRC_ALPHA || eq != GL_FUNC_ADD || srca != src ||
            dsta != dst || eqa != eq) {
            note_reject("blend factors");
            return;
        }
    }
    GLboolean mask[4] = {1, 1, 1, 1};
    glGetBooleanv(GL_COLOR_WRITEMASK, mask);
    if (!mask[0] || !mask[1] || !mask[2] || !mask[3]) {
        note_reject("color mask");
        return;
    }
    g_cap.cull = glIsEnabled(GL_CULL_FACE) ? 1 : 0;
    GLint mode = GL_BACK, face = GL_CCW;
    glGetIntegerv(GL_CULL_FACE_MODE, &mode);
    glGetIntegerv(GL_FRONT_FACE, &face);
    if (g_cap.cull && mode != GL_BACK && mode != GL_FRONT) {
        note_reject("cull mode");
        return;
    }
    g_cap.cull = g_cap.cull ? (mode == GL_FRONT ? 2 : 1) : 0;
    g_cap.front_ccw = face != GL_CW;
    glGetIntegerv(GL_SCISSOR_TEST, &scissor);
    g_cap.scissor = scissor ? 1 : 0;
    if (g_cap.scissor) glGetIntegerv(GL_SCISSOR_BOX, g_cap.scissor_box);
}

static int index_range_ok(uintptr_t offset, int count, int index_size, int bytes) {
    if (count <= 0 || index_size <= 0 || bytes < 0 || offset > (uintptr_t)bytes) return 0;
    uint64_t need = (uint64_t)count * (uint64_t)index_size;
    return need <= (uint64_t)bytes - offset;
}

static void snapshot_draw(GLenum mode, GLsizei count, GLenum index_type, const void *indices) {
    if (g_draws > 0) {
        g_draws++;
        note_reject("multiple GL draws");
        return;
    }
    g_draws = 1;
    if (mode != GL_TRIANGLES || count <= 0) {
        note_reject(mode != GL_TRIANGLES ? "primitive" : "empty draw");
        return;
    }
    if (index_type != GL_UNSIGNED_SHORT && index_type != GL_UNSIGNED_INT) {
        note_reject("index type");
        return;
    }
    snapshot_state();
    if (g_reject == NULL) snapshot_program();
    if (g_reject == NULL) snapshot_attribs();
    if (g_reject != NULL) return;
    GLint element = 0;
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &element);
    g_cap.mode = (int)mode;
    g_cap.count = count;
    g_cap.index_type = (int)index_type;
    g_cap.indices = copy_buffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element, &g_cap.index_bytes);
    int index_size = index_type == GL_UNSIGNED_SHORT ? 2 : 4;
    uintptr_t offset = (uintptr_t)indices;
    if (g_reject == NULL && !index_range_ok(offset, count, index_size, g_cap.index_bytes)) {
        note_reject("index range");
    }
    if (offset > 0x7fffffff) note_reject("index range");
    g_cap.index_offset = (int)offset;
    int64_t bytes = (int64_t)g_cap.width * g_cap.height * 8 + g_cap.vert_bytes + g_cap.index_bytes;
    if (bytes > (64 << 20)) note_reject("fixture over 64 MiB");
}

static void hooked_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    if (g_in_original) snapshot_draw(mode, count, type, indices);
    glDrawElements(mode, count, type, indices);
}

#define INTERPOSE(repl, orig) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##orig = {(const void *)(repl), (const void *)(orig)}

INTERPOSE(hooked_elements, glDrawElements);

static void save_bindings(Saved *saved) {
    memset(saved, 0, sizeof *saved);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &saved->read_fbo);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &saved->draw_fbo);
    glGetIntegerv(GL_VIEWPORT, saved->viewport);
    glGetIntegerv(GL_SCISSOR_BOX, saved->scissor_box);
    saved->scissor = glIsEnabled(GL_SCISSOR_TEST);
    glGetIntegerv(GL_CURRENT_PROGRAM, &saved->program);
    glGetIntegerv(GL_VERTEX_ARRAY_BINDING_APPLE, &saved->vao);
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &saved->array_buf);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &saved->elem_buf);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &saved->active_tex);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &saved->tex_binding);
    glGetIntegerv(GL_RENDERBUFFER_BINDING, &saved->rbo);
    glGetFloatv(GL_COLOR_CLEAR_VALUE, saved->clear_color);
    glGetDoublev(GL_DEPTH_CLEAR_VALUE, &saved->clear_depth);
    saved->blend = glIsEnabled(GL_BLEND);
    glGetIntegerv(GL_BLEND_SRC_RGB, &saved->blend_src);
    glGetIntegerv(GL_BLEND_DST_RGB, &saved->blend_dst);
    glGetIntegerv(GL_BLEND_EQUATION_RGB, &saved->blend_eq);
    saved->depth_test = glIsEnabled(GL_DEPTH_TEST);
    glGetIntegerv(GL_DEPTH_WRITEMASK, &saved->depth_mask);
    glGetIntegerv(GL_DEPTH_FUNC, &saved->depth_func);
    saved->stencil = glIsEnabled(GL_STENCIL_TEST);
    saved->cull = glIsEnabled(GL_CULL_FACE);
    glGetIntegerv(GL_CULL_FACE_MODE, &saved->cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &saved->front);
    glGetIntegerv(GL_PACK_ALIGNMENT, &saved->pack);
    glGetBooleanv(GL_COLOR_WRITEMASK, saved->color_mask);
}

static void restore_bindings(const Saved *saved) {
    glBindFramebuffer(GL_READ_FRAMEBUFFER, (GLuint)saved->read_fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, (GLuint)saved->draw_fbo);
    glViewport(saved->viewport[0], saved->viewport[1], saved->viewport[2], saved->viewport[3]);
    if (saved->scissor) glEnable(GL_SCISSOR_TEST);
    else glDisable(GL_SCISSOR_TEST);
    glScissor(saved->scissor_box[0], saved->scissor_box[1], saved->scissor_box[2], saved->scissor_box[3]);
    glUseProgram((GLuint)saved->program);
    glBindVertexArrayAPPLE((GLuint)saved->vao);
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)saved->array_buf);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)saved->elem_buf);
    glActiveTexture((GLenum)saved->active_tex);
    glBindTexture(GL_TEXTURE_2D, (GLuint)saved->tex_binding);
    glBindRenderbuffer(GL_RENDERBUFFER, (GLuint)saved->rbo);
    glClearColor(saved->clear_color[0], saved->clear_color[1], saved->clear_color[2], saved->clear_color[3]);
    glClearDepth(saved->clear_depth);
    if (saved->blend) glEnable(GL_BLEND);
    else glDisable(GL_BLEND);
    glBlendFunc(saved->blend_src, saved->blend_dst);
    glBlendEquation(saved->blend_eq);
    if (saved->depth_test) glEnable(GL_DEPTH_TEST);
    else glDisable(GL_DEPTH_TEST);
    glDepthMask((GLboolean)saved->depth_mask);
    glDepthFunc(saved->depth_func);
    if (saved->stencil) glEnable(GL_STENCIL_TEST);
    else glDisable(GL_STENCIL_TEST);
    if (saved->cull) glEnable(GL_CULL_FACE);
    else glDisable(GL_CULL_FACE);
    glCullFace(saved->cull_mode);
    glFrontFace(saved->front);
    glPixelStorei(GL_PACK_ALIGNMENT, saved->pack);
    glColorMask(saved->color_mask[0], saved->color_mask[1], saved->color_mask[2], saved->color_mask[3]);
}

static GLuint compile_stage(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        note_reject("replay shader compile");
        glDeleteShader(shader);
        return 0;
    }
    return shader;
}

static int write_bytes(const char *name, const void *bytes, int size) {
    char path[1100];
    snprintf(path, sizeof path, "%s/%s", g_dir, name);
    FILE *out = fopen(path, "wb");
    if (out == NULL) return -1;
    int ok = size == 0 || fwrite(bytes, 1, (size_t)size, out) == (size_t)size;
    fclose(out);
    return ok ? 0 : -1;
}

static const char *attrib_format(const Attrib *attrib) {
    if (attrib->type == GL_FLOAT && attrib->size == 2) return "float2";
    if (attrib->type == GL_FLOAT && attrib->size == 3) return "float3";
    if (attrib->type == GL_FLOAT && attrib->size == 4) return "float4";
    if (attrib->type == GL_UNSIGNED_BYTE && attrib->size == 4 && attrib->normalized) return "uchar4_norm";
    return NULL;
}

static int write_fixture(const uint8_t *reference, const uint8_t *clear, int pixels) {
    if (write_bytes("reference.rgba", reference, pixels) || write_bytes("clear.rgba", clear, pixels) ||
        write_bytes("draw.vert.glsl", g_cap.vert_src, (int)strlen(g_cap.vert_src)) ||
        write_bytes("draw.frag.glsl", g_cap.frag_src, (int)strlen(g_cap.frag_src)) ||
        write_bytes("vertices.bin", g_cap.verts, g_cap.vert_bytes) ||
        write_bytes("indices.bin", g_cap.indices, g_cap.index_bytes)) {
        return -1;
    }
    char path[1100];
    snprintf(path, sizeof path, "%s/fixture.json", g_dir);
    FILE *out = fopen(path, "w");
    if (out == NULL) return -1;
    int tolerance = g_cap.blend ? 1 : 0;
    const char *why = tolerance ? "blend; declared before the Metal run" : "no blend and no linear filter; declared before the Metal run";
    fprintf(out,
            "{\n"
            "  \"callsite\": \"0x14c8404\",\n"
            "  \"width\": %d,\n"
            "  \"height\": %d,\n"
            "  \"clear\": [0, 0, 0, 0],\n"
            "  \"depth_clear\": 1.0,\n"
            "  \"elements\": 1,\n"
            "  \"mode\": \"triangles\",\n"
            "  \"first\": 0,\n"
            "  \"count\": %d,\n"
            "  \"index_type\": \"%s\",\n"
            "  \"index_offset\": %d,\n"
            "  \"stride\": %d,\n"
            "  \"attrib_count\": %d,\n",
            g_cap.width, g_cap.height, g_cap.count,
            g_cap.index_type == GL_UNSIGNED_SHORT ? "uint16" : "uint32", g_cap.index_offset, g_cap.stride,
            g_cap.attrib_count);
    for (int i = 0; i < g_cap.attrib_count; i++) {
        const char *format = attrib_format(&g_cap.attribs[i]);
        if (format == NULL) {
            fclose(out);
            note_reject("attribute format");
            return -1;
        }
        fprintf(out,
                "  \"attrib%d_format\": \"%s\",\n"
                "  \"attrib%d_location\": %d,\n"
                "  \"attrib%d_offset\": %d,\n"
                "  \"attrib%d_buffer\": 0,\n",
                i, format, i, g_cap.attribs[i].location, i, g_cap.attribs[i].offset, i);
    }
    fprintf(out,
            "  \"stencil_enable\": 0,\n"
            "  \"depth_test\": %d,\n"
            "  \"depth_write\": %d,\n"
            "  \"depth_func\": %d,\n"
            "  \"depth_evidence\": \"gl\",\n"
            "  \"depth_matches_b1\": %d,\n"
            "  \"blend\": %d,\n"
            "  \"cull\": %d,\n"
            "  \"front_ccw\": %d,\n"
            "  \"scissor\": %d,\n"
            "  \"scissor_box\": [%d, %d, %d, %d],\n"
            "  \"max_channel_abs\": %d,\n"
            "  \"tolerance_justification\": \"%s\"\n"
            "}\n",
            g_cap.depth_test, g_cap.depth_write, g_cap.depth_func, g_cap.depth_matches_b1, g_cap.blend, g_cap.cull,
            g_cap.front_ccw, g_cap.scissor, g_cap.scissor_box[0], g_cap.scissor_box[1], g_cap.scissor_box[2],
            g_cap.scissor_box[3], tolerance, why);
    fclose(out);
    return 0;
}

static int replay(uint8_t **reference, uint8_t **clear_pixels, int *pixel_bytes) {
    GLuint fbo = 0, color = 0, depth = 0, program = 0, vbo = 0, ibo = 0, vao = 0;
    GLuint vert = compile_stage(GL_VERTEX_SHADER, g_cap.vert_src);
    GLuint frag = compile_stage(GL_FRAGMENT_SHADER, g_cap.frag_src);
    if (vert == 0 || frag == 0) return -1;
    program = glCreateProgram();
    glAttachShader(program, vert);
    glAttachShader(program, frag);
    for (int i = 0; i < g_cap.attrib_count; i++) {
        glBindAttribLocation(program, (GLuint)g_cap.attribs[i].location, g_cap.attribs[i].name);
    }
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        note_reject("replay link");
        return -1;
    }
    for (int i = 0; i < g_cap.attrib_count; i++) {
        if (glGetAttribLocation(program, g_cap.attribs[i].name) != g_cap.attribs[i].location) {
            note_reject("attrib location");
            return -1;
        }
    }
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, g_cap.width, g_cap.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_DRAW_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    if (g_cap.depth_test) {
        glGenRenderbuffers(1, &depth);
        glBindRenderbuffer(GL_RENDERBUFFER, depth);
        glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, g_cap.width, g_cap.height);
        glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT, GL_RENDERBUFFER, depth);
    }
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        note_reject("replay framebuffer");
        return -1;
    }
    glGenVertexArraysAPPLE(1, &vao);
    glBindVertexArrayAPPLE(vao);
    glGenBuffers(1, &vbo);
    glBindBuffer(GL_ARRAY_BUFFER, vbo);
    glBufferData(GL_ARRAY_BUFFER, g_cap.vert_bytes, g_cap.verts, GL_STATIC_DRAW);
    glGenBuffers(1, &ibo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, g_cap.index_bytes, g_cap.indices, GL_STATIC_DRAW);
    glUseProgram(program);
    int uses_location0 = 0;
    for (int i = 0; i < g_cap.attrib_count; i++) {
        Attrib *attrib = &g_cap.attribs[i];
        if (attrib->location == 0) uses_location0 = 1;
        glEnableVertexAttribArray((GLuint)attrib->location);
        glVertexAttribPointer((GLuint)attrib->location, attrib->size, (GLenum)attrib->type,
                              (GLboolean)attrib->normalized, attrib->stride, (void *)(uintptr_t)attrib->offset);
    }
    /* This driver fetches no generic attribute unless location 0's array is enabled. */
    if (!uses_location0) {
        Attrib *attrib = &g_cap.attribs[0];
        glEnableVertexAttribArray(0);
        glVertexAttribPointer(0, attrib->size, (GLenum)attrib->type, (GLboolean)attrib->normalized, attrib->stride,
                              (void *)(uintptr_t)attrib->offset);
    }
    if (g_cap.blend) {
        glEnable(GL_BLEND);
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
        glBlendEquation(GL_FUNC_ADD);
    } else {
        glDisable(GL_BLEND);
    }
    if (g_cap.cull) {
        glEnable(GL_CULL_FACE);
        glCullFace(g_cap.cull == 2 ? GL_FRONT : GL_BACK);
    } else {
        glDisable(GL_CULL_FACE);
    }
    glFrontFace(g_cap.front_ccw ? GL_CCW : GL_CW);
    if (g_cap.depth_test) {
        glEnable(GL_DEPTH_TEST);
        glDepthMask(g_cap.depth_write ? GL_TRUE : GL_FALSE);
        glDepthFunc((GLenum)g_cap.depth_func);
    } else {
        glDisable(GL_DEPTH_TEST);
    }
    glDisable(GL_STENCIL_TEST);
    glViewport(0, 0, g_cap.width, g_cap.height);
    int pixels = g_cap.width * g_cap.height * 4;
    uint8_t *drawn = malloc((size_t)pixels);
    uint8_t *cleared = malloc((size_t)pixels);
    if (drawn == NULL || cleared == NULL) {
        note_reject("out of memory");
        glDeleteVertexArraysAPPLE(1, &vao);
        return -1;
    }
    glDisable(GL_SCISSOR_TEST);
    glClearColor(0, 0, 0, 0);
    glClearDepth(1.0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1);
    GLbitfield bits = GL_COLOR_BUFFER_BIT;
    if (g_cap.depth_test) bits |= GL_DEPTH_BUFFER_BIT;
    glClear(bits);
    glReadPixels(0, 0, g_cap.width, g_cap.height, GL_RGBA, GL_UNSIGNED_BYTE, cleared);
    if (g_cap.scissor) {
        glEnable(GL_SCISSOR_TEST);
        glScissor(g_cap.scissor_box[0], g_cap.scissor_box[1], g_cap.scissor_box[2], g_cap.scissor_box[3]);
    }
    glDrawElements(GL_TRIANGLES, g_cap.count, (GLenum)g_cap.index_type, (void *)(uintptr_t)g_cap.index_offset);
    glReadPixels(0, 0, g_cap.width, g_cap.height, GL_RGBA, GL_UNSIGNED_BYTE, drawn);
    int differs = memcmp(drawn, cleared, (size_t)pixels) != 0;
    glDeleteVertexArraysAPPLE(1, &vao);
    glDeleteProgram(program);
    glDeleteShader(vert);
    glDeleteShader(frag);
    glDeleteFramebuffers(1, &fbo);
    glDeleteTextures(1, &color);
    if (depth) glDeleteRenderbuffers(1, &depth);
    glDeleteBuffers(1, &vbo);
    glDeleteBuffers(1, &ibo);
    if (!differs) {
        free(drawn);
        free(cleared);
        note_reject("reference matches clear");
        return -1;
    }
    *reference = drawn;
    *clear_pixels = cleared;
    *pixel_bytes = pixels;
    return 0;
}

static int protect_page(void *address, int writable);

static void restore_site(void) {
    if (g_installed_site == NULL) return;
    if (protect_page(g_installed_site, 1) != 0) return;
    memcpy(g_installed_site, g_saved_bytes, 5);
    protect_page(g_installed_site, 0);
    g_installed_site = NULL;
}

void eu4_b2_after(void) {
    if (g_reject != NULL || g_draws != 1) {
        if (g_reject == NULL) note_reject("no GL draw");
        restore_site();
        return;
    }
    GLint pack_buffer = 0;
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_buffer);
    if (pack_buffer != 0) {
        note_reject("pixel pack buffer");
        restore_site();
        return;
    }
    Saved saved;
    save_bindings(&saved);
    uint8_t *reference = NULL, *cleared = NULL;
    int pixels = 0;
    int failed = replay(&reference, &cleared, &pixels);
    restore_bindings(&saved);
    if (failed || reference == NULL) {
        free(reference);
        free(cleared);
        if (g_reject == NULL) note_reject("replay failed");
        restore_site();
        return;
    }
    if (write_fixture(reference, cleared, pixels) != 0) {
        note_reject("fixture write");
        free(reference);
        free(cleared);
        restore_site();
        return;
    }
    free(reference);
    free(cleared);
    if (!g_smoke) _exit(0);
}

static int rel32_fits(const void *site, const void *dest) {
    int64_t delta = (int64_t)(intptr_t)dest - ((int64_t)(intptr_t)site + 5);
    return delta >= INT32_MIN && delta <= INT32_MAX;
}

static void *allocate_near(uintptr_t site) {
    static const int64_t offsets[] = {0x01000000, -0x01000000, 0x04000000, -0x04000000,
                                      0x10000000, -0x10000000, 0x40000000, -0x40000000};
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) return NULL;
    for (size_t i = 0; i < sizeof offsets / sizeof offsets[0]; i++) {
        uintptr_t hint = ((uintptr_t)((int64_t)site + offsets[i])) & ~((uintptr_t)page - 1);
        void *mapping = mmap((void *)hint, (size_t)page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        if (mapping == MAP_FAILED) continue;
        if (rel32_fits((void *)site, mapping)) return mapping;
        munmap(mapping, (size_t)page);
    }
    return NULL;
}

static int protect_page(void *address, int writable) {
    long page = sysconf(_SC_PAGESIZE);
    if (page <= 0) return -1;
    uintptr_t begin = (uintptr_t)address & ~((uintptr_t)page - 1);
    vm_prot_t prot = writable ? (VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE | VM_PROT_COPY)
                              : (VM_PROT_READ | VM_PROT_EXECUTE);
    kern_return_t result =
        mach_vm_protect(mach_task_self(), (mach_vm_address_t)begin, (mach_vm_size_t)page, false, prot);
    if (result != KERN_SUCCESS && writable) {
        prot = VM_PROT_READ | VM_PROT_WRITE;
        result = mach_vm_protect(mach_task_self(), (mach_vm_address_t)begin, (mach_vm_size_t)page, false, prot);
    }
    if (result != KERN_SUCCESS) return -1;
    if (!writable) sys_icache_invalidate((void *)begin, (size_t)page);
    return 0;
}

int eu4_b2_install_site(void *site, const uint8_t expected[5], void *expected_target) {
    if (site == NULL || expected == NULL || expected_target == NULL) return 1;
    if (memcmp(site, expected, 5) != 0) return 2;
    if (decode_target(site) != expected_target) return 3;
    void *wrapper = (void *)eu4_b2_enter_mesh;
    void *dest = wrapper;
    void *relay = NULL;
    if (!rel32_fits(site, wrapper)) {
        relay = allocate_near((uintptr_t)site);
        if (relay == NULL) return 4;
        uint8_t *bytes = relay;
        bytes[0] = 0xff;
        bytes[1] = 0x25;
        memset(bytes + 2, 0, 4);
        memcpy(bytes + 6, &wrapper, sizeof wrapper);
        if (protect_page(relay, 0) != 0) return 5;
        dest = relay;
    }
    uint8_t encoded[5];
    if (!rel32_fits(site, dest)) return 4;
    int32_t relative = (int32_t)((int64_t)(intptr_t)dest - ((int64_t)(intptr_t)site + 5));
    encoded[0] = 0xe8;
    memcpy(encoded + 1, &relative, 4);
    g_b2_original = expected_target;
    g_installed_site = site;
    memcpy(g_saved_bytes, expected, 5);
    if (protect_page(site, 1) != 0) return 6;
    memcpy(site, encoded, 5);
    if (protect_page(site, 0) != 0) return 6;
    return 0;
}

static const struct mach_header_64 *eu4_header(void) {
    uint32_t count = _dyld_image_count();
    for (uint32_t i = 0; i < count; i++) {
        const struct mach_header *header = _dyld_get_image_header(i);
        if (header == NULL || header->magic != MH_MAGIC_64 || header->cputype != CPU_TYPE_X86_64) continue;
        const uint8_t *base = (const uint8_t *)header;
        if (memcmp(base + kMeshRva, kMeshExpected, 5) != 0) continue;
        if (decode_target(base + kMeshRva) != (void *)(base + kIndexedRva)) continue;
        return (const struct mach_header_64 *)header;
    }
    return NULL;
}

static void install_eu4(void) {
    const struct mach_header_64 *header = eu4_header();
    if (header == NULL) {
        note_reject("eu4 image not found");
        return;
    }
    uint8_t *base = (uint8_t *)header;
    int status = eu4_b2_install_site(base + kMeshRva, kMeshExpected, base + kIndexedRva);
    if (status != 0) {
        char reason[64];
        snprintf(reason, sizeof reason, "mesh install status %d", status);
        note_reject(reason);
    }
}

__attribute__((constructor)) static void eu4_b2_capture_init(void) {
    const char *dir = getenv("EU4_B2_CAPTURE_DIR");
    if (dir == NULL || dir[0] != '/') return;
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    install_eu4();
}

int g_b2_smoke_count = 3;
int g_b2_smoke_base_vertex = 0;

static int g_original_calls;
static int g_case;
static GLuint g_prog;
static GLuint g_bad_prog;
static GLuint g_vbo;
static GLuint g_ibo;
static int (*g_issue_draw)(void);

static int smoke_original(void) {
    g_original_calls++;
    glUseProgram(g_case == 1 ? g_bad_prog : g_prog);
    if (g_case == 2) glEnable(GL_PRIMITIVE_RESTART);
    if (g_case == 3) glDepthRange(0.2, 0.8);
    if (g_case == 4) glEnable(GL_POLYGON_OFFSET_FILL);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_ibo);
    glEnableVertexAttribArray(3);
    glVertexAttribPointer(3, 2, GL_FLOAT, GL_FALSE, 8, (void *)0);
    GLint saved[4];
    glGetIntegerv(GL_VIEWPORT, saved);
    glViewport(0, 0, 16, 16);
    g_b2_smoke_count = g_case == 5 ? 100 : 3;
    g_b2_smoke_base_vertex = g_case == 6;
    int issued = g_issue_draw();
    glViewport(saved[0], saved[1], saved[2], saved[3]);
    if (g_case == 2) glDisable(GL_PRIMITIVE_RESTART);
    if (g_case == 3) glDepthRange(0.0, 1.0);
    if (g_case == 4) glDisable(GL_POLYGON_OFFSET_FILL);
    return issued == 0 ? 7 : 9;
}

static GLuint smoke_shader(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    return shader;
}

static int fixture_exists(const char *dir) {
    char path[1100];
    snprintf(path, sizeof path, "%s/fixture.json", dir);
    FILE *file = fopen(path, "r");
    if (file == NULL) return 0;
    fclose(file);
    return 1;
}

static int reject_case(uint8_t *page, const uint8_t expected[5], const char *dir, int cse) {
    char path[1100];
    snprintf(path, sizeof path, "%s/fixture.json", dir);
    unlink(path);
    g_case = cse;
    if (eu4_b2_install_site(page, expected, (void *)smoke_original) != 0) return 2;
    if (((int (*)(void))page)() != 7) return 40 + cse;
    if (fixture_exists(dir)) return 50 + cse;
    return 0;
}

static int abi_calls;
static uintptr_t abi_args[2][7];
static void *abi_page;

static uintptr_t abi_original(uintptr_t a, uintptr_t b, uintptr_t c, uintptr_t d, uintptr_t e, uintptr_t f,
                              uintptr_t stack) {
    int n = abi_calls++;
    if (n < 2) {
        abi_args[n][0] = a;
        abi_args[n][1] = b;
        abi_args[n][2] = c;
        abi_args[n][3] = d;
        abi_args[n][4] = e;
        abi_args[n][5] = f;
        abi_args[n][6] = stack;
    }
    if (n == 0) {
        uintptr_t (*site)(uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t, uintptr_t) = abi_page;
        return site(a, b, c, d, e, f, stack);
    }
    return 0xA11C;
}

int eu4_b2_abi_check(void) {
    /* The patched EU4 instruction is the call into the wrapper, so the check calls that entry.
       A stub that then calls the wrapper would shift the stack argument by one return address. */
    abi_page = (void *)eu4_b2_enter_mesh;
    abi_calls = 0;
    g_prepare_count = 0;
    g_b2_original = (void *)abi_original;
    uintptr_t got = eu4_b2_enter_mesh(0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77);
    if (got != 0xA11C) return 2;
    if (abi_calls != 2 || g_prepare_count != 1) return 3;
    static const uintptr_t want[7] = {0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77};
    for (int n = 0; n < 2; n++) {
        for (int i = 0; i < 7; i++) {
            if (abi_args[n][i] != want[i]) return 4;
        }
    }
    return 0;
}

int eu4_b2_capture_smoke(const char *dir, int (*issue_draw)(void)) {
    g_issue_draw = issue_draw;
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    g_smoke = 1;
    CGLPixelFormatAttribute attrs[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        0};
    CGLPixelFormatObj format = NULL;
    GLint formats = 0;
    if (CGLChoosePixelFormat(attrs, &format, &formats) != kCGLNoError) return 1;
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError) return 1;
    CGLSetCurrentContext(context);
    const char *vert = "attribute vec2 in_pos;\nvoid main(){ gl_Position = vec4(in_pos, 0.0, 1.0); }\n";
    const char *frag = "void main(){ gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0); }\n";
    const char *bad = "uniform vec4 a;\nuniform vec4 b;\nvoid main(){ gl_FragColor = a + b; }\n";
    GLuint vs = smoke_shader(GL_VERTEX_SHADER, vert);
    GLuint fs = smoke_shader(GL_FRAGMENT_SHADER, frag);
    GLuint bad_fs = smoke_shader(GL_FRAGMENT_SHADER, bad);
    g_prog = glCreateProgram();
    glAttachShader(g_prog, vs);
    glAttachShader(g_prog, fs);
    glBindAttribLocation(g_prog, 3, "in_pos");
    glLinkProgram(g_prog);
    g_bad_prog = glCreateProgram();
    glAttachShader(g_bad_prog, vs);
    glAttachShader(g_bad_prog, bad_fs);
    glLinkProgram(g_bad_prog);
    float triangle[] = {-1.f, -1.f, 3.f, -1.f, -1.f, 3.f};
    uint16_t indices[3] = {0, 1, 2};
    glGenBuffers(1, &g_vbo);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof triangle, triangle, GL_STATIC_DRAW);
    glGenBuffers(1, &g_ibo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof indices, indices, GL_STATIC_DRAW);
    long page_size = sysconf(_SC_PAGESIZE);
    uint8_t *page = mmap(NULL, (size_t)page_size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (page == MAP_FAILED || !rel32_fits(page, (void *)smoke_original)) return 1;
    int32_t relative = (int32_t)((uintptr_t)smoke_original - ((uintptr_t)page + 5));
    page[0] = 0xe8;
    memcpy(page + 1, &relative, 4);
    page[5] = 0xc3;
    uint8_t expected[5];
    memcpy(expected, page, 5);

#ifndef GL_PIXEL_PACK_BUFFER
#define GL_PIXEL_PACK_BUFFER 0x88EB
#endif
    GLuint pack = 0, sentinel_vao = 0, sentinel_ibo = 0, unit2 = 0, read_fbo = 0, draw_fbo = 0, rbo = 0;
    glGenBuffers(1, &pack);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, pack);
    glBufferData(GL_PIXEL_PACK_BUFFER, 4, indices, GL_STATIC_DRAW);
    GLint pack_binding = 0;
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_binding);
    if (pack_binding != (GLint)pack) return 31;
    int rejected = reject_case(page, expected, dir, 0);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    if (rejected != 0) return rejected;

    glEnable(GL_PRIMITIVE_RESTART);
    int restart_sticks = glIsEnabled(GL_PRIMITIVE_RESTART);
    glDisable(GL_PRIMITIVE_RESTART);
    while (glGetError() != GL_NO_ERROR) {
    }
    for (int cse = 1; cse <= 6; cse++) {
        if (cse == 2 && !restart_sticks) continue;
        rejected = reject_case(page, expected, dir, cse);
        if (rejected != 0) return rejected;
    }

    glGenVertexArraysAPPLE(1, &sentinel_vao);
    glBindVertexArrayAPPLE(sentinel_vao);
    glGenBuffers(1, &sentinel_ibo);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, sentinel_ibo);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof indices, indices, GL_STATIC_DRAW);
    glBindVertexArrayAPPLE(0);
    glGenTextures(1, &unit2);
    glActiveTexture(GL_TEXTURE2);
    glBindTexture(GL_TEXTURE_2D, unit2);
    glActiveTexture(GL_TEXTURE0);
    glGenFramebuffers(1, &read_fbo);
    glGenFramebuffers(1, &draw_fbo);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, read_fbo);
    glBindFramebuffer(GL_DRAW_FRAMEBUFFER, draw_fbo);
    glGenRenderbuffers(1, &rbo);
    glBindRenderbuffer(GL_RENDERBUFFER, rbo);
    glClearColor(0.25f, 0.5f, 0.75f, 1.f);
    glClearDepth(0.25);
    glViewport(3, 5, 7, 9);
    glUseProgram(0);
    GLint read_bound = 0, draw_bound = 0;
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_bound);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_bound);
    if (read_bound != (GLint)read_fbo || draw_bound != (GLint)draw_fbo || read_fbo == draw_fbo) return 32;
    g_case = 7;
    if (eu4_b2_install_site(page, expected, (void *)smoke_original) != 0) return 2;
    if (((int (*)(void))page)() != 7) return 5;

    GLint viewport[4] = {0}, program = 0, rbo_bound = 0, unit2_bound = 0, sentinel_elem = 0;
    GLfloat clear_color[4] = {0, 0, 0, 0};
    GLdouble clear_depth = 0;
    glGetIntegerv(GL_VIEWPORT, viewport);
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_bound);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_bound);
    glGetIntegerv(GL_RENDERBUFFER_BINDING, &rbo_bound);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &pack_binding);
    glGetFloatv(GL_COLOR_CLEAR_VALUE, clear_color);
    glGetDoublev(GL_DEPTH_CLEAR_VALUE, &clear_depth);
    glActiveTexture(GL_TEXTURE2);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &unit2_bound);
    glActiveTexture(GL_TEXTURE0);
    glBindVertexArrayAPPLE(sentinel_vao);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &sentinel_elem);
    if (viewport[0] != 3 || viewport[1] != 5 || viewport[2] != 7 || viewport[3] != 9) return 6;
    if (program != (GLint)g_prog) return 7;
    if (read_bound != (GLint)read_fbo || draw_bound != (GLint)draw_fbo) return 8;
    if (rbo_bound != (GLint)rbo || pack_binding != 0 || sentinel_elem != (GLint)sentinel_ibo) return 9;
    if (unit2_bound != (GLint)unit2) return 10;
    if (clear_color[0] != 0.25f || clear_color[1] != 0.5f || clear_color[2] != 0.75f || clear_color[3] != 1.f) return 11;
    if (clear_depth != 0.25) return 12;
    if (!fixture_exists(dir)) return 13;
    char path[1100];
    snprintf(path, sizeof path, "%s/reference.rgba", dir);
    FILE *reference = fopen(path, "rb");
    if (reference == NULL) return 14;
    fseek(reference, 0, SEEK_END);
    long ref_size = ftell(reference);
    fseek(reference, 0, SEEK_SET);
    uint8_t *ref_bytes = malloc((size_t)ref_size);
    if (ref_bytes == NULL || fread(ref_bytes, 1, (size_t)ref_size, reference) != (size_t)ref_size) return 15;
    fclose(reference);
    snprintf(path, sizeof path, "%s/clear.rgba", dir);
    FILE *cleared = fopen(path, "rb");
    uint8_t *clear_bytes = malloc((size_t)ref_size);
    if (cleared == NULL || clear_bytes == NULL || fread(clear_bytes, 1, (size_t)ref_size, cleared) != (size_t)ref_size) return 16;
    fclose(cleared);
    int same = memcmp(ref_bytes, clear_bytes, (size_t)ref_size) == 0;
    free(ref_bytes);
    free(clear_bytes);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    return same ? 17 : 0;
}
