/* One-draw capture. Attribution is the patched Gfx call, local to this file.
   glDrawArrays / glDrawElements are recorded only while that call is on the stack. */
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

enum { kMesh = 0, kPost = 1, kMaxAttrib = 4, kMaxUni = 32, kMaxTex = 2, kMaxBytes = 1 << 20 };

static const uint32_t kMeshRva = 0x14c8404u;
static const uint32_t kPostRva = 0xed7ba4u;
static const uint32_t kDrawRva = 0x15ea789u;
static const uint32_t kIndexedRva = 0x15eaa77u;
static const uint8_t kMeshExpected[5] = {0xe8, 0x6e, 0x26, 0x12, 0x00};
static const uint8_t kPostExpected[5] = {0xe8, 0xe0, 0x2b, 0x71, 0x00};

void *g_b2_original[2];
static void *g_installed_site[2];
static uint8_t g_saved_bytes[2][5];

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

typedef struct {
    int enabled, size, type, normalized, stride, offset, buffer_slot;
} Attrib;

typedef struct {
    int site, elements, mode, first, count, index_type, index_offset;
    int width, height, stencil_enable, depth_test, depth_write, depth_func;
    int depth_matches_b1, blend, filter_linear, cull, front_ccw, scissor;
    int scissor_box[4], stride, attrib_count, tex_count, uni_count;
    int vert_bytes[2], index_bytes, uniform_bytes;
    Attrib attribs[kMaxAttrib];
    void *verts[2];
    void *indices;
    void *uniforms;
    char *vert_src;
    char *frag_src;
    int tex_w[kMaxTex], tex_h[kMaxTex], tex_linear[kMaxTex], tex_wrap_s[kMaxTex], tex_wrap_t[kMaxTex];
    void *tex[kMaxTex];
    int tex_unit[kMaxTex];
} Capture;

static _Thread_local Capture g_cap;

typedef struct {
    GLint fbo, viewport[4], scissor_box[4], scissor, program, vao;
    GLint array_buf, elem_buf, active_tex, tex0, tex1;
    GLint blend, blend_src, blend_dst, blend_eq, depth_test, depth_mask, depth_func;
    GLint stencil, cull, cull_mode, front, pack;
    GLboolean color_mask[4];
} Saved;

int eu4_b2_claim(int site) {
    (void)site;
    if (g_depth != 0) {
        return 0;
    }
    g_depth = 1;
    return 1;
}

void eu4_b2_release(void) {
    g_depth = 0;
    g_in_original = 0;
}

void eu4_b2_prepare(int site) {
    if (g_cap.vert_src) free(g_cap.vert_src);
    if (g_cap.frag_src) free(g_cap.frag_src);
    for (int i = 0; i < 2; i++) free(g_cap.verts[i]);
    free(g_cap.indices);
    free(g_cap.uniforms);
    for (int i = 0; i < kMaxTex; i++) free(g_cap.tex[i]);
    memset(&g_cap, 0, sizeof g_cap);
    g_cap.site = site;
    g_reject = NULL;
    g_draws = 0;
}

void eu4_b2_arm_original(void) {
    g_in_original = 1;
}

void eu4_b2_disarm_original(void) {
    g_in_original = 0;
}

__asm__(
    ".globl _eu4_b2_enter_mesh\n"
    "_eu4_b2_enter_mesh:\n"
    "    xorl %eax, %eax\n"
    "    jmp L_b2_common\n"
    ".globl _eu4_b2_enter_post\n"
    "_eu4_b2_enter_post:\n"
    "    movl $1, %eax\n"
    "L_b2_common:\n"
    "    pushq %rbp\n"
    "    movq %rsp, %rbp\n"
    "    pushq %rax\n"
    "    subq $0x158, %rsp\n"
    "    movq %rdi, -0x10(%rbp)\n"
    "    movq %rsi, -0x18(%rbp)\n"
    "    movq %rdx, -0x20(%rbp)\n"
    "    movq %rcx, -0x28(%rbp)\n"
    "    movq %r8, -0x30(%rbp)\n"
    "    movq %r9, -0x38(%rbp)\n"
    "    movq %r10, -0x40(%rbp)\n"
    "    movq %r11, -0x48(%rbp)\n"
    "    movaps %xmm0, -0x60(%rbp)\n"
    "    movaps %xmm1, -0x70(%rbp)\n"
    "    movaps %xmm2, -0x80(%rbp)\n"
    "    movaps %xmm3, -0x90(%rbp)\n"
    "    movaps %xmm4, -0xa0(%rbp)\n"
    "    movaps %xmm5, -0xb0(%rbp)\n"
    "    movaps %xmm6, -0xc0(%rbp)\n"
    "    movaps %xmm7, -0xd0(%rbp)\n"
    "    movl -8(%rbp), %edi\n"
    "    call _eu4_b2_claim\n"
    "    testl %eax, %eax\n"
    "    jz L_b2_reenter\n"
    "    movl -8(%rbp), %edi\n"
    "    call _eu4_b2_prepare\n"
    "    call _eu4_b2_arm_original\n"
    "    movq -0x10(%rbp), %rdi\n"
    "    movq -0x18(%rbp), %rsi\n"
    "    movq -0x20(%rbp), %rdx\n"
    "    movq -0x28(%rbp), %rcx\n"
    "    movq -0x30(%rbp), %r8\n"
    "    movq -0x38(%rbp), %r9\n"
    "    movq -0x40(%rbp), %r10\n"
    "    movq -0x48(%rbp), %r11\n"
    "    movaps -0x60(%rbp), %xmm0\n"
    "    movaps -0x70(%rbp), %xmm1\n"
    "    movaps -0x80(%rbp), %xmm2\n"
    "    movaps -0x90(%rbp), %xmm3\n"
    "    movaps -0xa0(%rbp), %xmm4\n"
    "    movaps -0xb0(%rbp), %xmm5\n"
    "    movaps -0xc0(%rbp), %xmm6\n"
    "    movaps -0xd0(%rbp), %xmm7\n"
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
    "    movslq -8(%rbp), %rax\n"
    "    leaq _g_b2_original(%rip), %rcx\n"
    "    callq *(%rcx,%rax,8)\n"
    "    movq %rax, -0xe0(%rbp)\n"
    "    call _eu4_b2_disarm_original\n"
    "    movl -8(%rbp), %edi\n"
    "    call _eu4_b2_after\n"
    "    call _eu4_b2_release\n"
    "    movq -0xe0(%rbp), %rax\n"
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
    "    movq -0x40(%rbp), %r10\n"
    "    movq -0x48(%rbp), %r11\n"
    "    movaps -0x60(%rbp), %xmm0\n"
    "    movaps -0x70(%rbp), %xmm1\n"
    "    movaps -0x80(%rbp), %xmm2\n"
    "    movaps -0x90(%rbp), %xmm3\n"
    "    movaps -0xa0(%rbp), %xmm4\n"
    "    movaps -0xb0(%rbp), %xmm5\n"
    "    movaps -0xc0(%rbp), %xmm6\n"
    "    movaps -0xd0(%rbp), %xmm7\n"
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
    "    movslq -8(%rbp), %rax\n"
    "    leaq _g_b2_original(%rip), %rcx\n"
    "    callq *(%rcx,%rax,8)\n"
    "    movq %rbp, %rsp\n"
    "    popq %rbp\n"
    "    ret\n"
);

extern void eu4_b2_enter_mesh(void);
extern void eu4_b2_enter_post(void);
void eu4_b2_after(int site);

static void note_reject(const char *reason) {
    if (g_reject == NULL) {
        snprintf(g_reject_text, sizeof g_reject_text, "%s", reason);
        g_reject = g_reject_text;
    }
    if (g_dir[0] != '/') {
        return;
    }
    char path[1100];
    snprintf(path, sizeof path, "%s/rejects.log", g_dir);
    FILE *out = fopen(path, "a");
    if (out == NULL) {
        return;
    }
    fprintf(out, "site %d: %s\n", g_cap.site, reason);
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
        if (data == NULL) {
            note_reject("out of memory");
        } else {
            glGetBufferSubData(target, 0, size, data);
        }
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
    GLint program = 0, count = 0;
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
    if (g_cap.vert_src == NULL || g_cap.frag_src == NULL) {
        note_reject("missing stage");
    }
    GLint uniforms = 0;
    glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS, &uniforms);
    if (uniforms < 0 || uniforms > kMaxUni) {
        note_reject("uniform count");
        return;
    }
    g_cap.uni_count = uniforms;
    g_cap.uniform_bytes = uniforms * (64 + 16 * 4);
    if (g_cap.uniform_bytes > 65536) {
        note_reject("uniforms over 64 KiB");
        return;
    }
    if (uniforms == 0) {
        return;
    }
    g_cap.uniforms = calloc((size_t)uniforms, 64 + 64);
    if (g_cap.uniforms == NULL) {
        note_reject("out of memory");
    }
}

typedef struct {
    char name[64];
    int kind, elems;
    float data[16];
    int unit;
} UniRec;

static UniRec *uni_at(int index) {
    return (UniRec *)((uint8_t *)g_cap.uniforms + (size_t)index * sizeof(UniRec));
}

static void read_uniforms(void) {
    GLint program = 0;
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    for (int i = 0; i < g_cap.uni_count; i++) {
        char name[64];
        GLsizei length = 0;
        GLint size = 0;
        GLenum type = 0;
        glGetActiveUniform((GLuint)program, (GLuint)i, 63, &length, &size, &type, name);
        name[63] = 0;
        if (size != 1) {
            note_reject("uniform array");
            return;
        }
        UniRec *rec = uni_at(i);
        snprintf(rec->name, sizeof rec->name, "%s", name);
        GLint loc = glGetUniformLocation((GLuint)program, name);
        if (type == GL_FLOAT || type == GL_FLOAT_VEC2 || type == GL_FLOAT_VEC3 || type == GL_FLOAT_VEC4 ||
            type == GL_FLOAT_MAT4) {
            int n = type == GL_FLOAT ? 1 : type == GL_FLOAT_VEC2 ? 2 : type == GL_FLOAT_VEC3 ? 3
                    : type == GL_FLOAT_VEC4 ? 4 : 16;
            rec->kind = type == GL_FLOAT_MAT4 ? 16 : n;
            rec->elems = n;
            glGetUniformfv((GLuint)program, loc, rec->data);
        } else if (type == GL_SAMPLER_2D || type == GL_INT) {
            int value = 0;
            glGetUniformiv((GLuint)program, loc, &value);
            rec->kind = type == GL_SAMPLER_2D ? 100 : 1;
            rec->unit = value;
            rec->elems = 1;
            if (type == GL_SAMPLER_2D && g_cap.tex_count < kMaxTex) {
                g_cap.tex_unit[g_cap.tex_count++] = value;
            } else if (type == GL_SAMPLER_2D) {
                note_reject("more than two textures");
            }
        } else {
            note_reject("uniform type");
            return;
        }
    }
}

static int wrap_ok(GLint wrap) {
    return wrap == GL_CLAMP_TO_EDGE || wrap == GL_REPEAT || wrap == GL_CLAMP;
}

static void snapshot_textures(void) {
    GLint active = 0, prev0 = 0;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &active);
    for (int i = 0; i < g_cap.tex_count; i++) {
        glActiveTexture(GL_TEXTURE0 + g_cap.tex_unit[i]);
        GLint name = 0, w = 0, h = 0, internal = 0, minf = 0, magf = 0, wraps = 0, wrapt = 0;
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &name);
        if (name == 0) {
            note_reject("missing texture");
            break;
        }
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &w);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &h);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &internal);
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, &minf);
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, &magf);
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, &wraps);
        glGetTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, &wrapt);
        int rgba = internal == GL_RGBA || internal == GL_RGBA8 || internal == GL_BGRA;
        if (!rgba || w < 1 || h < 1 || w > 2048 || h > 2048) {
            note_reject("texture format or size");
            break;
        }
        if ((minf != GL_NEAREST && minf != GL_LINEAR) || (magf != GL_NEAREST && magf != GL_LINEAR) ||
            !wrap_ok(wraps) || !wrap_ok(wrapt)) {
            note_reject("sampler state");
            break;
        }
        size_t bytes = (size_t)w * (size_t)h * 4u;
        void *pixels = malloc(bytes);
        if (pixels == NULL) {
            note_reject("out of memory");
            break;
        }
        glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
        g_cap.tex_w[i] = w;
        g_cap.tex_h[i] = h;
        g_cap.tex_linear[i] = minf == GL_LINEAR || magf == GL_LINEAR;
        g_cap.tex_wrap_s[i] = wraps;
        g_cap.tex_wrap_t[i] = wrapt;
        g_cap.tex[i] = pixels;
        if (g_cap.tex_linear[i]) g_cap.filter_linear = 1;
        (void)prev0;
    }
    glActiveTexture((GLenum)active);
}

static void snapshot_attribs(void) {
    GLint stride_seen = -1;
    int buffers[2] = {0, 0};
    int buffer_count = 0;
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
        int slot = -1;
        for (int b = 0; b < buffer_count; b++) {
            if (buffers[b] == binding) slot = b;
        }
        if (slot < 0) {
            if (buffer_count >= 2) {
                note_reject("more than two vertex buffers");
                return;
            }
            slot = buffer_count;
            buffers[buffer_count++] = binding;
            g_cap.verts[slot] = copy_buffer(GL_ARRAY_BUFFER, (GLuint)binding, &g_cap.vert_bytes[slot]);
        }
        if (stride == 0) stride = size * type_size(type);
        if (stride_seen < 0) stride_seen = stride;
        attrib->enabled = 1;
        attrib->size = size;
        attrib->type = type;
        attrib->normalized = norm;
        attrib->stride = stride;
        attrib->offset = (int)(uintptr_t)pointer;
        attrib->buffer_slot = slot;
        g_cap.attrib_count++;
    }
    if (g_cap.attrib_count == 0) note_reject("no attributes");
    g_cap.stride = stride_seen < 0 ? 0 : stride_seen;
}

static void snapshot_state(void) {
    GLint viewport[4] = {0}, samples = 0, srgb = 0, scissor = 0;
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
    g_cap.depth_test = glIsEnabled(GL_DEPTH_TEST) ? 1 : 0;
    glGetIntegerv(GL_DEPTH_WRITEMASK, &g_cap.depth_write);
    glGetIntegerv(GL_DEPTH_FUNC, &g_cap.depth_func);
    g_cap.stencil_enable = glIsEnabled(GL_STENCIL_TEST) ? 1 : 0;
    if (g_cap.stencil_enable) {
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

static void snapshot_draw(int elements, GLenum mode, GLint first, GLsizei count, GLenum index_type,
                          const void *indices) {
    if (g_draws > 0) {
        g_draws++;
        note_reject("multiple GL draws");
        return;
    }
    g_draws = 1;
    while (glGetError() != GL_NO_ERROR) {
    }
    if (mode != GL_TRIANGLES && mode != GL_TRIANGLE_STRIP) {
        note_reject("primitive");
        return;
    }
    if (count <= 0) {
        note_reject("empty draw");
        return;
    }
    g_cap.elements = elements;
    g_cap.mode = (int)mode;
    g_cap.first = first;
    g_cap.count = count;
    snapshot_state();
    snapshot_program();
    if (g_reject == NULL && g_cap.uni_count > 0 && g_cap.uniforms != NULL) read_uniforms();
    if (g_reject == NULL) snapshot_textures();
    if (g_reject == NULL) snapshot_attribs();
    if (elements) {
        if (index_type != GL_UNSIGNED_SHORT && index_type != GL_UNSIGNED_INT) {
            note_reject("index type");
            return;
        }
        GLint element = 0;
        glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &element);
        g_cap.index_type = (int)index_type;
        g_cap.index_offset = (int)(uintptr_t)indices;
        g_cap.indices = copy_buffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)element, &g_cap.index_bytes);
    }
    int64_t bytes = (int64_t)g_cap.width * g_cap.height * 8;
    bytes += g_cap.vert_bytes[0] + g_cap.vert_bytes[1] + g_cap.index_bytes;
    for (int i = 0; i < g_cap.tex_count; i++) {
        bytes += (int64_t)g_cap.tex_w[i] * g_cap.tex_h[i] * 4;
    }
    if (bytes > (64 << 20)) note_reject("fixture over 64 MiB");
    if (glGetError() != GL_NO_ERROR) note_reject("GL error during snapshot");
}

static void hooked_arrays(GLenum mode, GLint first, GLsizei count) {
    if (g_in_original) snapshot_draw(0, mode, first, count, 0, NULL);
    glDrawArrays(mode, first, count);
}

static void hooked_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    if (g_in_original) snapshot_draw(1, mode, 0, count, type, indices);
    glDrawElements(mode, count, type, indices);
}

#define INTERPOSE(repl, orig) \
    __attribute__((used, section("__DATA,__interpose"))) \
    static const struct { const void *replacement; const void *replacee; } \
        interpose_##orig = {(const void *)(repl), (const void *)(orig)}

INTERPOSE(hooked_arrays, glDrawArrays);
INTERPOSE(hooked_elements, glDrawElements);

static void save_bindings(Saved *saved) {
    memset(saved, 0, sizeof *saved);
    glGetIntegerv(GL_FRAMEBUFFER_BINDING, &saved->fbo);
    glGetIntegerv(GL_VIEWPORT, saved->viewport);
    glGetIntegerv(GL_SCISSOR_BOX, saved->scissor_box);
    saved->scissor = glIsEnabled(GL_SCISSOR_TEST);
    glGetIntegerv(GL_CURRENT_PROGRAM, &saved->program);
    glGetIntegerv(GL_VERTEX_ARRAY_BINDING_APPLE, &saved->vao);
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &saved->array_buf);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &saved->elem_buf);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &saved->active_tex);
    glActiveTexture(GL_TEXTURE0);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &saved->tex0);
    glActiveTexture(GL_TEXTURE1);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &saved->tex1);
    glActiveTexture((GLenum)saved->active_tex);
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
    glBindFramebuffer(GL_FRAMEBUFFER, (GLuint)saved->fbo);
    glViewport(saved->viewport[0], saved->viewport[1], saved->viewport[2], saved->viewport[3]);
    if (saved->scissor) glEnable(GL_SCISSOR_TEST);
    else glDisable(GL_SCISSOR_TEST);
    glScissor(saved->scissor_box[0], saved->scissor_box[1], saved->scissor_box[2], saved->scissor_box[3]);
    glUseProgram((GLuint)saved->program);
    glBindVertexArrayAPPLE((GLuint)saved->vao);
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)saved->array_buf);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, (GLuint)saved->elem_buf);
    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, (GLuint)saved->tex0);
    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, (GLuint)saved->tex1);
    glActiveTexture((GLenum)saved->active_tex);
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
        write_bytes("draw.frag.glsl", g_cap.frag_src, (int)strlen(g_cap.frag_src))) {
        return -1;
    }
    if (g_cap.vert_bytes[0] && write_bytes("vertices.bin", g_cap.verts[0], g_cap.vert_bytes[0])) return -1;
    if (g_cap.vert_bytes[1] && write_bytes("vertices1.bin", g_cap.verts[1], g_cap.vert_bytes[1])) return -1;
    if (g_cap.index_bytes && write_bytes("indices.bin", g_cap.indices, g_cap.index_bytes)) return -1;
    for (int i = 0; i < g_cap.tex_count; i++) {
        char name[32];
        snprintf(name, sizeof name, "texture%d.bin", i);
        if (write_bytes(name, g_cap.tex[i], g_cap.tex_w[i] * g_cap.tex_h[i] * 4)) return -1;
    }
    if (g_cap.uni_count > 0) {
        if (write_bytes("uniforms.bin", g_cap.uniforms, g_cap.uni_count * (int)sizeof(UniRec))) return -1;
    }
    char path[1100];
    snprintf(path, sizeof path, "%s/fixture.json", g_dir);
    FILE *out = fopen(path, "w");
    if (out == NULL) return -1;
    const char *site = g_cap.site == kMesh ? "0x14c8404" : "0xed7ba4";
    int tolerance = (g_cap.blend || g_cap.filter_linear) ? 1 : 0;
    const char *why = tolerance ? "blend or linear filter; declared before the Metal run"
                                : "no blend and no linear filter; declared before the Metal run";
    fprintf(out,
            "{\n"
            "  \"callsite\": \"%s\",\n"
            "  \"width\": %d,\n"
            "  \"height\": %d,\n"
            "  \"clear\": [0, 0, 0, 0],\n"
            "  \"depth_clear\": 1.0,\n"
            "  \"elements\": %d,\n"
            "  \"mode\": \"%s\",\n"
            "  \"first\": %d,\n"
            "  \"count\": %d,\n"
            "  \"index_type\": \"%s\",\n"
            "  \"index_offset\": %d,\n"
            "  \"stride\": %d,\n"
            "  \"attrib_count\": %d,\n",
            site, g_cap.width, g_cap.height, g_cap.elements,
            g_cap.mode == GL_TRIANGLE_STRIP ? "triangle_strip" : "triangles", g_cap.first, g_cap.count,
            g_cap.elements ? (g_cap.index_type == GL_UNSIGNED_SHORT ? "uint16" : "uint32") : "none",
            g_cap.index_offset, g_cap.stride, g_cap.attrib_count);
    for (int i = 0; i < g_cap.attrib_count; i++) {
        const char *format = attrib_format(&g_cap.attribs[i]);
        if (format == NULL) {
            fclose(out);
            note_reject("attribute format");
            return -1;
        }
        fprintf(out,
                "  \"attrib%d_format\": \"%s\",\n"
                "  \"attrib%d_offset\": %d,\n"
                "  \"attrib%d_buffer\": %d,\n",
                i, format, i, g_cap.attribs[i].offset, i, g_cap.attribs[i].buffer_slot);
    }
    fprintf(out,
            "  \"texture_count\": %d,\n"
            "  \"stencil_enable\": %d,\n"
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
            "  \"tolerance_justification\": \"%s\",\n"
            "  \"uniform_count\": %d,\n"
            "  \"tex0_width\": %d,\n"
            "  \"tex0_height\": %d,\n"
            "  \"tex0_linear\": %d,\n"
            "  \"tex0_wrap_s\": %d,\n"
            "  \"tex0_wrap_t\": %d\n"
            "}\n",
            g_cap.tex_count, g_cap.stencil_enable, g_cap.depth_test, g_cap.depth_write, g_cap.depth_func,
            g_cap.depth_matches_b1, g_cap.blend, g_cap.cull, g_cap.front_ccw, g_cap.scissor,
            g_cap.scissor_box[0], g_cap.scissor_box[1], g_cap.scissor_box[2], g_cap.scissor_box[3],
            tolerance, why, g_cap.uni_count, g_cap.tex_w[0], g_cap.tex_h[0], g_cap.tex_linear[0],
            g_cap.tex_wrap_s[0], g_cap.tex_wrap_t[0]);
    fclose(out);
    return 0;
}

static int replay(uint8_t **reference, uint8_t **clear_pixels, int *pixel_bytes) {
    GLuint fbo = 0, color = 0, depth = 0, program = 0, vbo[2] = {0, 0}, ibo = 0, textures[kMaxTex] = {0, 0};
    GLuint vert = compile_stage(GL_VERTEX_SHADER, g_cap.vert_src);
    GLuint frag = compile_stage(GL_FRAGMENT_SHADER, g_cap.frag_src);
    if (vert == 0 || frag == 0) return -1;
    program = glCreateProgram();
    glAttachShader(program, vert);
    glAttachShader(program, frag);
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        note_reject("replay link");
        return -1;
    }
    glGenTextures(1, &color);
    glBindTexture(GL_TEXTURE_2D, color);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, g_cap.width, g_cap.height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
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
    for (int slot = 0; slot < 2; slot++) {
        if (g_cap.vert_bytes[slot] == 0) continue;
        glGenBuffers(1, &vbo[slot]);
        glBindBuffer(GL_ARRAY_BUFFER, vbo[slot]);
        glBufferData(GL_ARRAY_BUFFER, g_cap.vert_bytes[slot], g_cap.verts[slot], GL_STATIC_DRAW);
    }
    if (g_cap.index_bytes) {
        glGenBuffers(1, &ibo);
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, ibo);
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, g_cap.index_bytes, g_cap.indices, GL_STATIC_DRAW);
    }
    glBindVertexArrayAPPLE(0);
    glUseProgram(program);
    for (int i = 0; i < 16; i++) glDisableVertexAttribArray(i);
    for (int i = 0; i < g_cap.attrib_count; i++) {
        Attrib *attrib = &g_cap.attribs[i];
        glBindBuffer(GL_ARRAY_BUFFER, vbo[attrib->buffer_slot]);
        glEnableVertexAttribArray((GLuint)i);
        glVertexAttribPointer((GLuint)i, attrib->size, (GLenum)attrib->type, (GLboolean)attrib->normalized,
                              attrib->stride, (void *)(uintptr_t)attrib->offset);
    }
    for (int i = 0; i < g_cap.uni_count; i++) {
        UniRec *rec = uni_at(i);
        GLint loc = glGetUniformLocation(program, rec->name);
        if (loc < 0) continue;
        if (rec->kind == 100) glUniform1i(loc, rec->unit);
        else if (rec->kind == 16) glUniformMatrix4fv(loc, 1, GL_FALSE, rec->data);
        else if (rec->kind == 1) glUniform1fv(loc, 1, rec->data);
        else if (rec->kind == 2) glUniform2fv(loc, 1, rec->data);
        else if (rec->kind == 3) glUniform3fv(loc, 1, rec->data);
        else if (rec->kind == 4) glUniform4fv(loc, 1, rec->data);
    }
    for (int i = 0; i < g_cap.tex_count; i++) {
        glGenTextures(1, &textures[i]);
        glActiveTexture(GL_TEXTURE0 + g_cap.tex_unit[i]);
        glBindTexture(GL_TEXTURE_2D, textures[i]);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, g_cap.tex_w[i], g_cap.tex_h[i], 0, GL_RGBA, GL_UNSIGNED_BYTE,
                     g_cap.tex[i]);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, g_cap.tex_linear[i] ? GL_LINEAR : GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, g_cap.tex_linear[i] ? GL_LINEAR : GL_NEAREST);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, g_cap.tex_wrap_s[i]);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, g_cap.tex_wrap_t[i]);
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
    if (g_cap.elements) {
        glDrawElements((GLenum)g_cap.mode, g_cap.count, (GLenum)g_cap.index_type,
                       (void *)(uintptr_t)g_cap.index_offset);
    } else {
        glDrawArrays((GLenum)g_cap.mode, g_cap.first, g_cap.count);
    }
    glReadPixels(0, 0, g_cap.width, g_cap.height, GL_RGBA, GL_UNSIGNED_BYTE, drawn);
    int differs = memcmp(drawn, cleared, (size_t)pixels) != 0;
    glDeleteProgram(program);
    glDeleteShader(vert);
    glDeleteShader(frag);
    glDeleteFramebuffers(1, &fbo);
    glDeleteTextures(1, &color);
    if (depth) glDeleteRenderbuffers(1, &depth);
    for (int slot = 0; slot < 2; slot++) {
        if (vbo[slot]) glDeleteBuffers(1, &vbo[slot]);
    }
    if (ibo) glDeleteBuffers(1, &ibo);
    for (int i = 0; i < g_cap.tex_count; i++) {
        if (textures[i]) glDeleteTextures(1, &textures[i]);
    }
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

static void restore_site(int kind) {
    if (kind < 0 || kind > 1 || g_installed_site[kind] == NULL) return;
    if (protect_page(g_installed_site[kind], 1) != 0) return;
    memcpy(g_installed_site[kind], g_saved_bytes[kind], 5);
    protect_page(g_installed_site[kind], 0);
    g_installed_site[kind] = NULL;
}

void eu4_b2_after(int site) {
    if (g_reject != NULL || g_draws != 1) {
        if (g_reject == NULL) note_reject("no GL draw");
        restore_site(site);
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
        restore_site(site);
        return;
    }
    if (write_fixture(reference, cleared, pixels) != 0) {
        note_reject("fixture write");
        free(reference);
        free(cleared);
        restore_site(site);
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

int eu4_b2_install_site(void *site, const uint8_t expected[5], void *expected_target, int kind) {
    if (site == NULL || expected == NULL || expected_target == NULL || kind < 0 || kind > 1) return 1;
    if (memcmp(site, expected, 5) != 0) return 2;
    if (decode_target(site) != expected_target) return 3;
    void *wrapper = kind == kMesh ? (void *)eu4_b2_enter_mesh : (void *)eu4_b2_enter_post;
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
    g_b2_original[kind] = expected_target;
    g_installed_site[kind] = site;
    memcpy(g_saved_bytes[kind], expected, 5);
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
        if (memcmp(base + kMeshRva, kMeshExpected, 5) == 0 && memcmp(base + kPostRva, kPostExpected, 5) == 0) {
            return (const struct mach_header_64 *)header;
        }
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
    void *mesh = base + kMeshRva;
    void *post = base + kPostRva;
    int mesh_status = eu4_b2_install_site(mesh, kMeshExpected, base + kIndexedRva, kMesh);
    int post_status = eu4_b2_install_site(post, kPostExpected, base + kDrawRva, kPost);
    if (mesh_status != 0) {
        char reason[64];
        snprintf(reason, sizeof reason, "mesh install status %d", mesh_status);
        note_reject(reason);
    }
    if (post_status != 0) {
        char reason[64];
        snprintf(reason, sizeof reason, "post install status %d", post_status);
        note_reject(reason);
    }
}

__attribute__((constructor)) static void eu4_b2_capture_init(void) {
    const char *dir = getenv("EU4_B2_CAPTURE_DIR");
    if (dir == NULL || dir[0] != '/') return;
    snprintf(g_dir, sizeof g_dir, "%s", dir);
    install_eu4();
}

static int g_original_calls;
static int g_inner_result;
static void *g_patched;
static GLuint g_prog;
static GLuint g_vbo;
static int (*g_issue_draw)(void);
static int (*g_smoke_post)(void);

static int smoke_original(void) {
    g_original_calls++;
    if (g_original_calls == 1) {
        g_inner_result = ((int (*)(void))g_patched)();
        return 7;
    }
    glUseProgram(g_prog);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 8, (void *)0);
    GLint saved[4];
    glGetIntegerv(GL_VIEWPORT, saved);
    glViewport(0, 0, 16, 16);
    int issued = g_issue_draw();
    glViewport(saved[0], saved[1], saved[2], saved[3]);
    return issued == 0 ? 7 : 9;
}

static int smoke_post(void) {
    return g_smoke_post();
}

static GLuint smoke_shader(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    return shader;
}

int eu4_b2_capture_smoke(const char *dir, int (*issue_draw)(void), int (*post_draw)(void)) {
    g_issue_draw = issue_draw;
    g_smoke_post = post_draw;
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
    GLuint vs = smoke_shader(GL_VERTEX_SHADER, vert);
    GLuint fs = smoke_shader(GL_FRAGMENT_SHADER, frag);
    g_prog = glCreateProgram();
    glAttachShader(g_prog, vs);
    glAttachShader(g_prog, fs);
    glBindAttribLocation(g_prog, 0, "in_pos");
    glLinkProgram(g_prog);
    float triangle[] = {-0.8f, -0.8f, 0.8f, -0.8f, 0.0f, 0.8f};
    glGenBuffers(1, &g_vbo);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBufferData(GL_ARRAY_BUFFER, sizeof triangle, triangle, GL_STATIC_DRAW);
    long page = sysconf(_SC_PAGESIZE);
    uint8_t *mesh_page = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    uint8_t *post_page = mmap(NULL, (size_t)page, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    if (mesh_page == MAP_FAILED || post_page == MAP_FAILED) return 1;
    int32_t mesh_rel = (int32_t)((uintptr_t)smoke_original - ((uintptr_t)mesh_page + 5));
    int32_t post_rel = (int32_t)((uintptr_t)smoke_post - ((uintptr_t)post_page + 5));
    mesh_page[0] = 0xe8;
    memcpy(mesh_page + 1, &mesh_rel, 4);
    mesh_page[5] = 0xc3;
    post_page[0] = 0xe8;
    memcpy(post_page + 1, &post_rel, 4);
    post_page[5] = 0xc3;
    uint8_t mesh_expected[5], post_expected[5];
    memcpy(mesh_expected, mesh_page, 5);
    memcpy(post_expected, post_page, 5);
    if (eu4_b2_install_site(mesh_page, mesh_expected, (void *)smoke_original, kMesh) != 0) return 2;
    if (eu4_b2_install_site(post_page, post_expected, (void *)smoke_post, kPost) != 0) return 3;
    g_patched = mesh_page;
    int post_result = ((int (*)(void))post_page)();
    if (post_result != 4) return 4;
    glViewport(3, 5, 7, 9);
    glUseProgram(0);
    int mesh_result = ((int (*)(void))mesh_page)();
    GLint viewport[4] = {0};
    GLint program = 0;
    glGetIntegerv(GL_VIEWPORT, viewport);
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    if (mesh_result != 7 || g_original_calls != 2 || g_inner_result != 7) return 5;
    if (viewport[0] != 3 || viewport[1] != 5 || viewport[2] != 7 || viewport[3] != 9) return 6;
    if (program != (GLint)g_prog) return 7;
    char fixture_path[1100];
    snprintf(fixture_path, sizeof fixture_path, "%s/fixture.json", dir);
    FILE *fixture = fopen(fixture_path, "r");
    if (fixture == NULL) return 8;
    fclose(fixture);
    FILE *reference = fopen((snprintf(fixture_path, sizeof fixture_path, "%s/reference.rgba", dir), fixture_path), "rb");
    FILE *clear = NULL;
    if (reference == NULL) return 9;
    fseek(reference, 0, SEEK_END);
    long ref_size = ftell(reference);
    fseek(reference, 0, SEEK_SET);
    uint8_t *ref_bytes = malloc((size_t)ref_size);
    if (ref_bytes == NULL || fread(ref_bytes, 1, (size_t)ref_size, reference) != (size_t)ref_size) return 10;
    fclose(reference);
    snprintf(fixture_path, sizeof fixture_path, "%s/clear.rgba", dir);
    clear = fopen(fixture_path, "rb");
    if (clear == NULL) return 11;
    uint8_t *clear_bytes = malloc((size_t)ref_size);
    if (clear_bytes == NULL || fread(clear_bytes, 1, (size_t)ref_size, clear) != (size_t)ref_size) return 12;
    fclose(clear);
    if (memcmp(ref_bytes, clear_bytes, (size_t)ref_size) == 0) return 13;
    int red = 0;
    for (long i = 0; i < ref_size; i += 4) {
        if (ref_bytes[i] == 255 && ref_bytes[i + 1] == 0 && ref_bytes[i + 2] == 0) red = 1;
    }
    free(ref_bytes);
    free(clear_bytes);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    return red ? 0 : 14;
}
