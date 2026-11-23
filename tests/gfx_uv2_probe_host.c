/* Focused host for the opt-in uv2 diagnostic probe.
 *
 * Creates a real legacy OpenGL context, compiles a synthetic original
 * program, restores captured-style vertex buffers and render state, executes
 * the production probe code, reads back the diagnostic outputs and verifies:
 *   - the declared RGBA32F output layout and lower-left orientation,
 *   - the interpolated uv2 values,
 *   - the original-shader control output,
 *   - that every GL binding the probe touches is restored,
 *   - that a non-selected event produces no diagnostic output.
 *
 * No game process and no captured evidence are involved.
 */
#define GL_SILENCE_DEPRECATION 1
#define GL_GLEXT_PROTOTYPES 1

#include "eu4_gfx_frame_trace.h"

#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <string.h>

/* The trace module links without the passive observation stream. */
bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src;
    (void)dest;
    (void)n;
    return false;
}

void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) {
        call->kind = kind;
    }
}

int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) {
        *width = 4;
    }
    if (height != NULL) {
        *height = 4;
    }
    return 0;
}

static const char *kVertexSource =
    "#version 120\n"
    "attribute vec4 in_POSITION;\n"
    "attribute vec2 in_TEXCOORD0;\n"
    "varying vec2 v_TEXCOORD1;\n"
    "uniform float u_offset;\n"
    "void main() {\n"
    "    v_TEXCOORD1 = in_TEXCOORD0;\n"
    "    gl_Position = vec4(in_POSITION.xy + vec2(u_offset, 0.0), 0.0, 1.0);\n"
    "}\n";

static const char *kFragmentSource =
    "#version 120\n"
    "varying vec2 v_TEXCOORD1;\n"
    "void main() {\n"
    "    gl_FragColor = vec4(v_TEXCOORD1.x, v_TEXCOORD1.y, 0.5, 1.0);\n"
    "}\n";

static GLuint compile(GLenum type, const char *source) {
    GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, NULL);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[2048] = {0};
        glGetShaderInfoLog(shader, sizeof(log) - 1, NULL, log);
        fprintf(stderr, "shader compile failed: %s\n", log);
        exit(2);
    }
    return shader;
}

typedef struct {
    GLint draw_fbo;
    GLint read_fbo;
    GLint read_buffer;
    GLint program;
    GLint viewport[4];
    GLint scissor_test;
    GLint scissor_box[4];
    GLint cull;
    GLint cull_mode;
    GLint front;
    GLint blend;
    GLint blend_src;
    GLint blend_dst;
    GLint depth_test;
    GLint depth_func;
    GLboolean depth_mask;
    GLboolean color_mask[4];
    GLfloat clear_color[4];
    GLint active_texture;
    GLint texture_2d;
    GLint array_buffer;
    GLint element_buffer;
    GLint attrib_enabled[2];
    GLint attrib_buffer[2];
    GLint attrib_size[2];
    GLint attrib_type[2];
    GLint attrib_stride[2];
    GLvoid *attrib_pointer[2];
    GLint pack_alignment;
    GLint unpack_alignment;
    GLint unpack_row_length;
    GLint unpack_skip_rows;
    GLint unpack_skip_pixels;
    GLint unpack_buffer;
    GLboolean unpack_swap_bytes;
    GLboolean unpack_lsb_first;
    GLint stencil_test;
    GLint alpha_test;
    GLint color_logic_op;
} state_snapshot;

static void snapshot(state_snapshot *snapshot) {
    memset(snapshot, 0, sizeof(*snapshot));
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &snapshot->draw_fbo);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &snapshot->read_fbo);
    glGetIntegerv(GL_READ_BUFFER, &snapshot->read_buffer);
    glGetIntegerv(GL_CURRENT_PROGRAM, &snapshot->program);
    glGetIntegerv(GL_VIEWPORT, snapshot->viewport);
    glGetIntegerv(GL_SCISSOR_TEST, &snapshot->scissor_test);
    glGetIntegerv(GL_SCISSOR_BOX, snapshot->scissor_box);
    glGetIntegerv(GL_CULL_FACE, &snapshot->cull);
    glGetIntegerv(GL_CULL_FACE_MODE, &snapshot->cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &snapshot->front);
    glGetIntegerv(GL_BLEND, &snapshot->blend);
    glGetIntegerv(GL_BLEND_SRC_RGB, &snapshot->blend_src);
    glGetIntegerv(GL_BLEND_DST_RGB, &snapshot->blend_dst);
    glGetIntegerv(GL_DEPTH_TEST, &snapshot->depth_test);
    glGetIntegerv(GL_DEPTH_FUNC, &snapshot->depth_func);
    glGetBooleanv(GL_DEPTH_WRITEMASK, &snapshot->depth_mask);
    glGetBooleanv(GL_COLOR_WRITEMASK, snapshot->color_mask);
    glGetFloatv(GL_COLOR_CLEAR_VALUE, snapshot->clear_color);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &snapshot->active_texture);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &snapshot->texture_2d);
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &snapshot->array_buffer);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &snapshot->element_buffer);
    for (int i = 0; i < 2; i++) {
        glGetVertexAttribiv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_ENABLED,
                            &snapshot->attrib_enabled[i]);
        glGetVertexAttribiv((GLuint)i,
                            GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING,
                            &snapshot->attrib_buffer[i]);
        glGetVertexAttribiv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_SIZE,
                            &snapshot->attrib_size[i]);
        glGetVertexAttribiv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_TYPE,
                            &snapshot->attrib_type[i]);
        glGetVertexAttribiv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_STRIDE,
                            &snapshot->attrib_stride[i]);
        glGetVertexAttribPointerv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_POINTER,
                                  &snapshot->attrib_pointer[i]);
    }
    glGetIntegerv(GL_PACK_ALIGNMENT, &snapshot->pack_alignment);
    glGetIntegerv(GL_UNPACK_ALIGNMENT, &snapshot->unpack_alignment);
    glGetIntegerv(GL_UNPACK_ROW_LENGTH, &snapshot->unpack_row_length);
    glGetIntegerv(GL_UNPACK_SKIP_ROWS, &snapshot->unpack_skip_rows);
    glGetIntegerv(GL_UNPACK_SKIP_PIXELS, &snapshot->unpack_skip_pixels);
    glGetIntegerv(GL_PIXEL_UNPACK_BUFFER_BINDING, &snapshot->unpack_buffer);
    glGetBooleanv(GL_UNPACK_SWAP_BYTES, &snapshot->unpack_swap_bytes);
    glGetBooleanv(GL_UNPACK_LSB_FIRST, &snapshot->unpack_lsb_first);
    glGetIntegerv(GL_STENCIL_TEST, &snapshot->stencil_test);
    glGetIntegerv(GL_ALPHA_TEST, &snapshot->alpha_test);
    glGetIntegerv(GL_COLOR_LOGIC_OP, &snapshot->color_logic_op);
}

static int same_state(const state_snapshot *before,
                      const state_snapshot *after) {
    int ok = 1;
#define CHECK_INT(field)                                                       \
    do {                                                                       \
        if (before->field != after->field) {                                   \
            fprintf(stderr, "state %s: %d != %d\n", #field,                    \
                    (int)before->field, (int)after->field);                    \
            ok = 0;                                                            \
        }                                                                      \
    } while (0)
    CHECK_INT(draw_fbo);
    CHECK_INT(read_fbo);
    CHECK_INT(read_buffer);
    CHECK_INT(program);
    for (int i = 0; i < 4; i++) {
        CHECK_INT(viewport[i]);
        CHECK_INT(scissor_box[i]);
    }
    CHECK_INT(scissor_test);
    CHECK_INT(cull);
    CHECK_INT(cull_mode);
    CHECK_INT(front);
    CHECK_INT(blend);
    CHECK_INT(blend_src);
    CHECK_INT(blend_dst);
    CHECK_INT(depth_test);
    CHECK_INT(depth_func);
    if (before->depth_mask != after->depth_mask) {
        fprintf(stderr, "state depth_mask differs\n");
        ok = 0;
    }
    for (int i = 0; i < 4; i++) {
        if (before->color_mask[i] != after->color_mask[i]) {
            fprintf(stderr, "state color_mask[%d] differs\n", i);
            ok = 0;
        }
        if (fabsf(before->clear_color[i] - after->clear_color[i]) > 1e-6f) {
            fprintf(stderr, "state clear_color[%d] differs\n", i);
            ok = 0;
        }
    }
    CHECK_INT(active_texture);
    CHECK_INT(texture_2d);
    CHECK_INT(array_buffer);
    CHECK_INT(element_buffer);
    for (int i = 0; i < 2; i++) {
        CHECK_INT(attrib_enabled[i]);
        CHECK_INT(attrib_buffer[i]);
        CHECK_INT(attrib_size[i]);
        CHECK_INT(attrib_type[i]);
        CHECK_INT(attrib_stride[i]);
        if (before->attrib_pointer[i] != after->attrib_pointer[i]) {
            fprintf(stderr, "state attrib_pointer[%d] differs\n", i);
            ok = 0;
        }
    }
    CHECK_INT(pack_alignment);
    CHECK_INT(unpack_alignment);
    CHECK_INT(unpack_row_length);
    CHECK_INT(unpack_skip_rows);
    CHECK_INT(unpack_skip_pixels);
    CHECK_INT(unpack_buffer);
    if (before->unpack_swap_bytes != after->unpack_swap_bytes ||
        before->unpack_lsb_first != after->unpack_lsb_first) {
        fprintf(stderr, "unpack boolean state differs\n");
        ok = 0;
    }
    CHECK_INT(stencil_test);
    CHECK_INT(alpha_test);
    CHECK_INT(color_logic_op);
#undef CHECK_INT
    return ok;
}

static uint8_t *read_file(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    long length = ftell(file);
    if (length < 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    uint8_t *data = malloc((size_t)length + 1);
    if (data == NULL) {
        fclose(file);
        return NULL;
    }
    size_t got = fread(data, 1, (size_t)length, file);
    fclose(file);
    data[got] = 0;
    *size = got;
    return data;
}

static int close_to(float value, float expected, float tolerance) {
    return fabsf(value - expected) <= tolerance;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <out-dir>\n", argv[0]);
        return 2;
    }
    const char *out_dir = argv[1];
    char second_dir[1024];
    snprintf(second_dir, sizeof(second_dir), "%s/not-selected", out_dir);
    mkdir(out_dir, 0755);

    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8, kCGLPFADepthSize,
        (CGLPixelFormatAttribute)24, 0};
    CGLPixelFormatObj format = NULL;
    GLint format_count = 0;
    if (CGLChoosePixelFormat(attributes, &format, &format_count) !=
            kCGLNoError ||
        format == NULL) {
        fprintf(stderr, "no legacy pixel format\n");
        return 2;
    }
    CGLContextObj context = NULL;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError ||
        context == NULL) {
        fprintf(stderr, "no context\n");
        return 2;
    }
    CGLSetCurrentContext(context);

    GLuint target_texture = 0;
    glGenTextures(1, &target_texture);
    glBindTexture(GL_TEXTURE_2D, target_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, 4, 4, 0, GL_RGB,
                 GL_UNSIGNED_BYTE, NULL);
    GLuint depth_buffer = 0;
    glGenRenderbuffers(1, &depth_buffer);
    glBindRenderbuffer(GL_RENDERBUFFER, depth_buffer);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, 4, 4);
    GLuint target_fbo = 0;
    glGenFramebuffers(1, &target_fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, target_fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                           target_texture, 0);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                              GL_RENDERBUFFER, depth_buffer);
    glDrawBuffer(GL_COLOR_ATTACHMENT0);
    glReadBuffer(GL_COLOR_ATTACHMENT0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "target framebuffer incomplete\n");
        return 2;
    }

    GLuint vertex_shader = compile(GL_VERTEX_SHADER, kVertexSource);
    GLuint fragment_shader = compile(GL_FRAGMENT_SHADER, kFragmentSource);
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex_shader);
    glAttachShader(program, fragment_shader);
    glBindAttribLocation(program, 0, "in_POSITION");
    glBindAttribLocation(program, 1, "in_TEXCOORD0");
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        fprintf(stderr, "program link failed\n");
        return 2;
    }
    glUseProgram(program);
    glUniform1f(glGetUniformLocation(program, "u_offset"), 0.0f);

    static const float positions[4][4] = {
        {-1.0f, -1.0f, 0.0f, 1.0f},
        {1.0f, -1.0f, 0.0f, 1.0f},
        {-1.0f, 1.0f, 0.0f, 1.0f},
        {1.0f, 1.0f, 0.0f, 1.0f},
    };
    static const float texcoords[4][2] = {
        {0.0f, 0.0f}, {1.0f, 0.0f}, {0.0f, 1.0f}, {1.0f, 1.0f}};
    static const uint16_t indices[6] = {0, 1, 2, 2, 1, 3};
    GLuint index_buffer = 0;
    glGenBuffers(1, &index_buffer);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, index_buffer);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(indices), indices,
                 GL_STATIC_DRAW);
    /* Client arrays make the attribute base addresses authoritative; a
       VBO-backed array would need the engine element table. */
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, 0, positions);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 0, texcoords);
    glEnableVertexAttribArray(1);

    /* Representative engine state: colour/depth writes, no blending. */
    glViewport(0, 0, 4, 4);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_BLEND);
    glDisable(GL_ALPHA_TEST);
    glDisable(GL_COLOR_LOGIC_OP);
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LESS);
    glDepthMask(GL_TRUE);
    glDepthRange(0.0, 1.0);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDisable(GL_DITHER);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    glActiveTexture(GL_TEXTURE3);
    glBindTexture(GL_TEXTURE_2D, 0);
    glActiveTexture(GL_TEXTURE15);
    /* Non-default pixel-unpack state must not change the diagnostic seeds and
       must be restored.  A bound unpack buffer would otherwise redirect the
       CPU uploads into PBO storage. */
    GLuint unpack_buffer = 0;
    glGenBuffers(1, &unpack_buffer);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, unpack_buffer);
    glBufferData(GL_PIXEL_UNPACK_BUFFER, 64, NULL, GL_STREAM_DRAW);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 8);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, 7);
    glPixelStorei(GL_UNPACK_SKIP_ROWS, 1);
    glPixelStorei(GL_UNPACK_SKIP_PIXELS, 2);

    state_snapshot before;
    snapshot(&before);

    int configured = eu4_gfx_uv2_probe_test_configure(
        out_dir, 1, (int)program, (int)target_fbo, (int)target_texture);
    int ran = configured == 0
                  ? eu4_gfx_uv2_probe_test_run(1, 1, GL_TRIANGLES, 6, 0, 1, 0, 0)
                  : -1;

    state_snapshot after;
    snapshot(&after);
    int restored = same_state(&before, &after);

    char json_path[1200];
    char raw_path[1200];
    char control_path[1200];
    snprintf(json_path, sizeof(json_path), "%s/uv2-probe.json", out_dir);
    snprintf(raw_path, sizeof(raw_path), "%s/uv2-probe.rgba32f", out_dir);
    snprintf(control_path, sizeof(control_path), "%s/uv2-control.bgra", out_dir);
    size_t json_size = 0;
    size_t raw_size = 0;
    size_t control_size = 0;
    uint8_t *json = read_file(json_path, &json_size);
    uint8_t *raw = read_file(raw_path, &raw_size);
    uint8_t *control = read_file(control_path, &control_size);

    int layout = json != NULL && raw_size == 4 * 4 * 16 &&
                 control_size == 4 * 4 * 4;
    if (!layout) {
        fprintf(stderr, "descriptor/readback layout mismatch\n");
    }
    if (raw_size != 4 * 4 * 16 || control_size != 4 * 4 * 4) {
        fprintf(stderr, "unexpected output sizes raw=%zu control=%zu\n",
                raw_size, control_size);
        layout = 0;
    }

    int readback = 1;
    if (raw != NULL && raw_size == 4 * 4 * 16) {
        const float *values = (const float *)raw;
        if (!close_to(values[0], 0.125f, 2e-6f) ||
            !close_to(values[1], 0.125f, 2e-6f) ||
            !close_to(values[2], 0.0f, 0.0f) ||
            !close_to(values[3], 1.0f, 0.0f)) {
            fprintf(stderr, "lower-left uv2 mismatch: %.9g %.9g %.9g %.9g\n",
                    values[0], values[1], values[2], values[3]);
            readback = 0;
        }
    }
    /* The original-shader control must reproduce the synthetic authentic
       output: colour (uv.x, uv.y, 0.5, 1.0) in BGRA byte order. */
    int control_ok = 1;
    if (control != NULL && control_size == 4 * 4 * 4) {
        const uint8_t *pixel = control;
        if (abs((int)pixel[2] - 32) > 1 || abs((int)pixel[1] - 32) > 1 ||
            abs((int)pixel[0] - 128) > 1 || pixel[3] != 255) {
            fprintf(stderr, "control pixel mismatch: %u %u %u %u\n", pixel[0],
                    pixel[1], pixel[2], pixel[3]);
            control_ok = 0;
        }
        if (strstr((const char *)json, "\"digest_ok\":true") == NULL) {
            fprintf(stderr, "attribute digest was not recorded\n");
            control_ok = 0;
        }
    } else {
        control_ok = 0;
    }

    free(json);
    free(raw);
    free(control);
    glDeleteBuffers(1, &index_buffer);
    glDeleteBuffers(1, &unpack_buffer);
    glDeleteProgram(program);
    glDeleteShader(vertex_shader);
    glDeleteShader(fragment_shader);
    glDeleteFramebuffers(1, &target_fbo);
    glDeleteRenderbuffers(1, &depth_buffer);
    glDeleteTextures(1, &target_texture);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);

    printf("probe=%d\n", ran == 1 ? 1 : 0);
    printf("layout=%d\n", layout);
    printf("readback=%d\n", readback);
    printf("control=%d\n", control_ok);
    printf("restore=%d\n", restored);
    return (ran == 1 && layout && readback && control_ok && restored) ? 0 : 1;
}
