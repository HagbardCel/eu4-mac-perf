/* Focused host for the opt-in uv2 diagnostic probe.
 *
 * Creates a real legacy OpenGL context, a synthetic original program and draw
 * target, and executes the production probe.  It verifies the declared output
 * layout, the lower-left uv2 value, the original-shader control and that the
 * changed GL state (including nondefault pixel-unpack/PBO state) is restored.
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
#include <string.h>
#include <sys/stat.h>

bool eu4_gfx_observe_read(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n; return false;
}
bool eu4_gfx_observe_read_bytes(const void *src, void *dest, size_t n) {
    (void)src; (void)dest; (void)n; return false;
}
void eu4_gfx_observe_set_kind(eu4_gfx_observe_call *call, uint32_t kind) {
    if (call != NULL) call->kind = kind;
}
int eu4_gfx_gl_drawable_extent(uint32_t *width, uint32_t *height) {
    if (width != NULL) *width = 4;
    if (height != NULL) *height = 4;
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
    GLint draw_fbo, read_fbo, program;
    GLint viewport[4], scissor_test, scissor_box[4], cull, blend;
    GLint depth_test, active_texture, texture_2d, array_buffer, element_buffer;
    GLint attrib_enabled[2];
    GLint unpack_alignment, unpack_row_length, unpack_skip_rows;
    GLint unpack_skip_pixels, unpack_buffer;
    GLboolean color_mask[4];
    GLfloat clear_color[4];
} state_snapshot;

static void snapshot(state_snapshot *s) {
    memset(s, 0, sizeof(*s));
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &s->draw_fbo);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &s->read_fbo);
    glGetIntegerv(GL_CURRENT_PROGRAM, &s->program);
    glGetIntegerv(GL_VIEWPORT, s->viewport);
    glGetIntegerv(GL_SCISSOR_TEST, &s->scissor_test);
    glGetIntegerv(GL_SCISSOR_BOX, s->scissor_box);
    glGetIntegerv(GL_CULL_FACE, &s->cull);
    glGetIntegerv(GL_BLEND, &s->blend);
    glGetIntegerv(GL_DEPTH_TEST, &s->depth_test);
    glGetIntegerv(GL_ACTIVE_TEXTURE, &s->active_texture);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &s->texture_2d);
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &s->array_buffer);
    glGetIntegerv(GL_ELEMENT_ARRAY_BUFFER_BINDING, &s->element_buffer);
    for (int i = 0; i < 2; i++) {
        glGetVertexAttribiv((GLuint)i, GL_VERTEX_ATTRIB_ARRAY_ENABLED,
                            &s->attrib_enabled[i]);
    }
    glGetIntegerv(GL_UNPACK_ALIGNMENT, &s->unpack_alignment);
    glGetIntegerv(GL_UNPACK_ROW_LENGTH, &s->unpack_row_length);
    glGetIntegerv(GL_UNPACK_SKIP_ROWS, &s->unpack_skip_rows);
    glGetIntegerv(GL_UNPACK_SKIP_PIXELS, &s->unpack_skip_pixels);
    glGetIntegerv(GL_PIXEL_UNPACK_BUFFER_BINDING, &s->unpack_buffer);
    glGetBooleanv(GL_COLOR_WRITEMASK, s->color_mask);
    glGetFloatv(GL_COLOR_CLEAR_VALUE, s->clear_color);
}

static int restored(const state_snapshot *a, const state_snapshot *b) {
    int ok = 1;
#define CHECK(field)                                                           \
    if (a->field != b->field) {                                                \
        fprintf(stderr, "state %s changed\n", #field);                         \
        ok = 0;                                                               \
    }
    CHECK(program);
    CHECK(blend);
    CHECK(depth_test);
    CHECK(cull);
    CHECK(active_texture);
    CHECK(texture_2d);
    CHECK(array_buffer);
    CHECK(element_buffer);
    CHECK(unpack_alignment);
    CHECK(unpack_row_length);
    CHECK(unpack_skip_rows);
    CHECK(unpack_skip_pixels);
    CHECK(unpack_buffer);
    CHECK(attrib_enabled[0]);
    CHECK(attrib_enabled[1]);
    for (int i = 0; i < 4; i++) {
        CHECK(viewport[i]);
        CHECK(scissor_box[i]);
        if (a->color_mask[i] != b->color_mask[i]) {
            fprintf(stderr, "colour mask changed\n");
            ok = 0;
        }
        if (fabsf(a->clear_color[i] - b->clear_color[i]) > 1e-6f) {
            fprintf(stderr, "clear colour changed\n");
            ok = 0;
        }
    }
    if (a->draw_fbo != b->draw_fbo || a->read_fbo != b->read_fbo) {
        fprintf(stderr, "framebuffer binding changed\n");
        ok = 0;
    }
#undef CHECK
    return ok;
}

static uint8_t *read_file(const char *path, size_t *size) {
    FILE *file = fopen(path, "rb");
    if (file == NULL) return NULL;
    if (fseek(file, 0, SEEK_END) != 0) { fclose(file); return NULL; }
    long length = ftell(file);
    if (length < 0 || fseek(file, 0, SEEK_SET) != 0) { fclose(file); return NULL; }
    uint8_t *data = malloc((size_t)length + 1);
    if (data == NULL) { fclose(file); return NULL; }
    size_t got = fread(data, 1, (size_t)length, file);
    fclose(file);
    *size = got;
    return data;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <out-dir>\n", argv[0]);
        return 2;
    }
    const char *out_dir = argv[1];
    mkdir(out_dir, 0755);

    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_Legacy,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)24,
        kCGLPFAAlphaSize, (CGLPixelFormatAttribute)8,
        kCGLPFADepthSize, (CGLPixelFormatAttribute)24, 0};
    CGLPixelFormatObj format = NULL;
    GLint format_count = 0;
    if (CGLChoosePixelFormat(attributes, &format, &format_count) != kCGLNoError ||
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

    GLuint target_texture = 0, depth_buffer = 0, target_fbo = 0;
    glGenTextures(1, &target_texture);
    glBindTexture(GL_TEXTURE_2D, target_texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB8, 4, 4, 0, GL_RGB,
                 GL_UNSIGNED_BYTE, NULL);
    glGenRenderbuffers(1, &depth_buffer);
    glBindRenderbuffer(GL_RENDERBUFFER, depth_buffer);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH_COMPONENT24, 4, 4);
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
        {-1.0f, -1.0f, 0.0f, 1.0f}, {1.0f, -1.0f, 0.0f, 1.0f},
        {-1.0f, 1.0f, 0.0f, 1.0f}, {1.0f, 1.0f, 0.0f, 1.0f}};
    static const float texcoords[4][2] = {
        {0.0f, 0.0f}, {1.0f, 0.0f}, {0.0f, 1.0f}, {1.0f, 1.0f}};
    static const uint16_t indices[6] = {0, 1, 2, 2, 1, 3};
    GLuint index_buffer = 0;
    glGenBuffers(1, &index_buffer);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, index_buffer);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(indices), indices,
                 GL_STATIC_DRAW);
    /* Client arrays make the attribute bases authoritative. */
    glVertexAttribPointer(0, 4, GL_FLOAT, GL_FALSE, 0, positions);
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 0, texcoords);
    glEnableVertexAttribArray(1);

    glViewport(0, 0, 4, 4);
    glDisable(GL_SCISSOR_TEST);
    glDisable(GL_CULL_FACE);
    glDisable(GL_BLEND);
    glEnable(GL_DEPTH_TEST);
    glDepthFunc(GL_LESS);
    glDepthMask(GL_TRUE);
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE);
    glDisable(GL_DITHER);
    glClearColor(0.25f, 0.5f, 0.75f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT);
    GLuint unpack_buffer = 0;
    glGenBuffers(1, &unpack_buffer);
    glBindBuffer(GL_PIXEL_UNPACK_BUFFER, unpack_buffer);
    glBufferData(GL_PIXEL_UNPACK_BUFFER, 64, NULL, GL_STREAM_DRAW);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 8);
    glPixelStorei(GL_UNPACK_ROW_LENGTH, 7);
    glPixelStorei(GL_UNPACK_SKIP_ROWS, 1);
    glPixelStorei(GL_UNPACK_SKIP_PIXELS, 2);

    state_snapshot before, after;
    snapshot(&before);
    int configured = eu4_gfx_uv2_probe_test_configure(
        out_dir, 1, (int)program, (int)target_fbo, (int)target_texture);
    int ran = configured == 0
                  ? eu4_gfx_uv2_probe_test_run(1, 1, GL_TRIANGLES, 6, 0, 1, 0, 0)
                  : -1;
    snapshot(&after);
    int state_ok = restored(&before, &after);

    char raw_path[1200], control_path[1200];
    snprintf(raw_path, sizeof(raw_path), "%s/uv2-probe.rgba32f", out_dir);
    snprintf(control_path, sizeof(control_path), "%s/uv2-control.bgra", out_dir);
    size_t raw_size = 0, control_size = 0;
    uint8_t *raw = read_file(raw_path, &raw_size);
    uint8_t *control = read_file(control_path, &control_size);
    int layout = raw_size == 4 * 4 * 16 && control_size == 4 * 4 * 4;
    int readback = 0;
    if (raw != NULL && raw_size == 4 * 4 * 16) {
        const float *values = (const float *)raw;
        /* Lower-left pixel (0,0): uv2 = (0.125, 0.125), z=0, w=1. */
        readback = fabsf(values[0] - 0.125f) <= 2e-6f &&
                   fabsf(values[1] - 0.125f) <= 2e-6f &&
                   values[2] == 0.0f && values[3] == 1.0f;
    }
    int control_ok = 0;
    if (control != NULL && control_size == 4 * 4 * 4) {
        control_ok = abs((int)control[2] - 32) <= 1 &&
                     abs((int)control[1] - 32) <= 1 &&
                     abs((int)control[0] - 128) <= 1 && control[3] == 255;
    }
    printf("probe=%d\n", ran == 1 ? 1 : 0);
    printf("layout=%d\n", layout);
    printf("readback=%d\n", readback);
    printf("control=%d\n", control_ok);
    printf("restore=%d\n", state_ok);
    int ok = ran == 1 && layout && readback && control_ok && state_ok;
    free(raw); free(control);
    glDeleteProgram(program);
    glDeleteFramebuffers(1, &target_fbo);
    glDeleteRenderbuffers(1, &depth_buffer);
    glDeleteTextures(1, &target_texture);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    return ok ? 0 : 1;
}
