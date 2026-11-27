/* Trusted observation endpoint for the candidate selected-draw executor.
 * Only the candidate translation unit is compiled with renamed GL entry
 * points. This observer and the candidate share a native address space. */
#define GL_SILENCE_DEPRECATION
#include <CommonCrypto/CommonDigest.h>
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int eu4_candidate_draw_main(int argc, char **argv);

static const char *output;
static unsigned width, height;
static int omit_draw;
static unsigned transition;
static GLint target_texture;

static void digest(const void *bytes, size_t length) {
    unsigned char hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(bytes, (CC_LONG)length, hash);
    putchar('"');
    for (size_t i = 0; i < sizeof(hash); i++) printf("%02x", hash[i]);
    putchar('"');
}

static void observe_shaders(GLuint program) {
    GLuint shaders[2] = {0};
    GLint attached = 0;
    glGetProgramiv(program, GL_ATTACHED_SHADERS, &attached);
    if (attached != 2) exit(7);
    glGetAttachedShaders(program, 2, NULL, shaders);
    printf(",\"shader_sha256\":{");
    for (size_t i = 0; i < 2; i++) {
        GLint type = 0, size = 0;
        GLsizei length = 0;
        glGetShaderiv(shaders[i], GL_SHADER_TYPE, &type);
        glGetShaderiv(shaders[i], GL_SHADER_SOURCE_LENGTH, &size);
        if ((type != GL_VERTEX_SHADER && type != GL_FRAGMENT_SHADER) || size <= 1 ||
            size > 4 * 1024 * 1024) exit(7);
        char *source = malloc((size_t)size);
        if (!source) exit(7);
        glGetShaderSource(shaders[i], size, &length, source);
        if (length != size - 1 || glGetError() != GL_NO_ERROR) exit(7);
        printf("%s\"%s\":", i ? "," : "", type == GL_VERTEX_SHADER ? "vertex" : "pixel");
        digest(source, (size_t)length);
        free(source);
    }
    putchar('}');
}

static void observe_texture(GLint unit) {
    GLint previous = 0, binding = 0;
    glGetIntegerv(GL_ACTIVE_TEXTURE, &previous);
    if (unit < 0 || unit >= 32) exit(7);
    glActiveTexture(GL_TEXTURE0 + (GLenum)unit);
    glGetIntegerv(GL_TEXTURE_BINDING_2D, &binding);
    if (binding == target_texture) exit(7);
    GLint width = 0, height = 0, format = 0;
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH, &width);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT, &height);
    glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_INTERNAL_FORMAT, &format);
    printf("{\"unit\":%d,\"width\":%d,\"height\":%d,\"format\":%d",
           unit, width, height, format);
    const GLenum params[] = {
        GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_TEXTURE_WRAP_S,
        GL_TEXTURE_WRAP_T, GL_TEXTURE_BASE_LEVEL, GL_TEXTURE_MAX_LEVEL,
        GL_TEXTURE_COMPARE_MODE, GL_TEXTURE_COMPARE_FUNC, GL_TEXTURE_MIN_LOD,
        GL_TEXTURE_MAX_LOD, GL_TEXTURE_LOD_BIAS, GL_TEXTURE_MAX_ANISOTROPY_EXT,
    };
    printf(",\"sampler\":[");
    for (size_t i = 0; i < sizeof(params) / sizeof(params[0]); i++) {
        GLfloat value = 0;
        glGetTexParameterfv(GL_TEXTURE_2D, params[i], &value);
        printf("%s%.9g", i ? "," : "", value);
    }
    putchar(']');
    if (binding && format == GL_RGBA8 && width > 0 && width <= 8192
            && height > 0 && height <= 8192) {
        size_t size = (size_t)width * (size_t)height * 4;
        void *bytes = malloc(size);
        if (!bytes) exit(6);
        glGetTexImage(GL_TEXTURE_2D, 0, GL_RGBA, GL_UNSIGNED_BYTE, bytes);
        printf(",\"sha256\":");
        digest(bytes, size);
        free(bytes);
    }
    glActiveTexture((GLenum)previous);
    putchar('}');
}

static void observe_inputs(GLuint program, GLint uniforms, GLint attributes) {
    printf(",\"uniforms\":{");
    for (GLint i = 0; i < uniforms; i++) {
        char name[256];
        GLint count = 0;
        GLenum type = 0;
        glGetActiveUniform(program, (GLuint)i, sizeof(name), NULL, &count, &type, name);
        printf("%s\"%s\":", i ? "," : "", name);
        if (type == GL_SAMPLER_2D && count == 1) {
            GLint unit = -1;
            glGetUniformiv(program, glGetUniformLocation(program, name), &unit);
            observe_texture(unit);
            continue;
        }
        int components = type == GL_FLOAT ? 1 : type == GL_FLOAT_VEC2 ? 2 :
                         type == GL_FLOAT_VEC3 ? 3 : type == GL_FLOAT_VEC4 ? 4 : 0;
        if (!components || count < 1 || count > 512) {
            printf("null");
            continue;
        }
        GLfloat values[512 * 4] = {0};
        const char *suffix = strstr(name, "[0]");
        if (count > 1 && !suffix) exit(7);
        for (GLint n = 0; n < count; n++) {
            char element[280];
            if (suffix) snprintf(element, sizeof(element), "%.*s[%d]",
                                 (int)(suffix - name), name, n);
            else snprintf(element, sizeof(element), "%s", name);
            GLint location = glGetUniformLocation(program, element);
            if (location < 0) exit(7);
            glGetUniformfv(program, location, values + n * components);
        }
        printf("{\"components\":%d,\"elements\":%d,\"sha256\":", components, count);
        digest(values, (size_t)count * (size_t)components * sizeof(GLfloat));
        putchar('}');
    }
    GLint previous_buffer = 0;
    glGetIntegerv(GL_ARRAY_BUFFER_BINDING, &previous_buffer);
    printf("},\"attributes\":{");
    for (GLint i = 0; i < attributes; i++) {
        char name[256];
        glGetActiveAttrib(program, (GLuint)i, sizeof(name), NULL, NULL, NULL, name);
        GLint location = glGetAttribLocation(program, name);
        if (location < 0) exit(7);
        GLuint index = (GLuint)location;
        GLint enabled = 0, count = 0, type = 0, stride = 0, normalized = 0, binding = 0;
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_ENABLED, &enabled);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_SIZE, &count);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_TYPE, &type);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_STRIDE, &stride);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_NORMALIZED, &normalized);
        glGetVertexAttribiv(index, GL_VERTEX_ATTRIB_ARRAY_BUFFER_BINDING, &binding);
        void *pointer = NULL;
        glGetVertexAttribPointerv(index, GL_VERTEX_ATTRIB_ARRAY_POINTER, &pointer);
        printf("%s\"%s\":{\"location\":%d,\"enabled\":%d,\"components\":%d,"
               "\"type\":%d,\"stride\":%d,\"normalized\":%d,\"offset\":%zu",
               i ? "," : "", name, location, enabled, count, type, stride, normalized,
               (size_t)(uintptr_t)pointer);
        if (binding) {
            glBindBuffer(GL_ARRAY_BUFFER, (GLuint)binding);
            GLint length = 0;
            glGetBufferParameteriv(GL_ARRAY_BUFFER, GL_BUFFER_SIZE, &length);
            if (length <= 0 || length > 64 * 1024 * 1024) exit(7);
            void *bytes = malloc((size_t)length);
            if (!bytes) exit(6);
            glGetBufferSubData(GL_ARRAY_BUFFER, 0, length, bytes);
            printf(",\"sha256\":");
            digest(bytes, (size_t)length);
            free(bytes);
        }
        putchar('}');
    }
    glBindBuffer(GL_ARRAY_BUFFER, (GLuint)previous_buffer);
    putchar('}');
}

static void observe(const char *phase, GLenum mode, GLint first, GLsizei count,
                    GLenum indexed_type, int submitted) {
    GLint viewport[4] = {0}, scissor[4] = {0}, program = 0;
    GLint active_attributes = 0, active_uniforms = 0, link = 0, buffer = 0;
    GLint read_target = 0, draw_target = 0, read_buffer = 0;
    GLboolean color_mask[4] = {0};
    const unsigned error = glGetError();
    glGetIntegerv(GL_VIEWPORT, viewport);
    glGetIntegerv(GL_SCISSOR_BOX, scissor);
    glGetIntegerv(GL_CURRENT_PROGRAM, &program);
    glGetIntegerv(GL_DRAW_BUFFER, &buffer);
    glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING, &draw_target);
    glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &read_target);
    glGetIntegerv(GL_READ_BUFFER, &read_buffer);
    if (read_target != draw_target) exit(7);
    glGetBooleanv(GL_COLOR_WRITEMASK, color_mask);
    glGetProgramiv((GLuint)program, GL_ACTIVE_ATTRIBUTES, &active_attributes);
    glGetProgramiv((GLuint)program, GL_ACTIVE_UNIFORMS, &active_uniforms);
    glGetProgramiv((GLuint)program, GL_LINK_STATUS, &link);
    printf("{\"phase\":\"%s\",\"transition\":%u,\"mode\":%u,\"first\":%d,\"count\":%d,"
           "\"index_type\":%u,\"submitted\":%d,\"gl_error\":%u,"
           "\"framebuffer_status\":%u,\"draw_buffer\":%d,\"read_buffer\":%d,"
           "\"viewport\":[%d,%d,%d,%d],\"scissor\":[%d,%d,%d,%d],"
           "\"scissor_enabled\":%d,\"blend\":%d,\"depth_test\":%d,"
           "\"stencil_test\":%d,\"cull\":%d,\"dither\":%d,"
           "\"color_mask\":[%d,%d,%d,%d],\"linked\":%d,"
           "\"active_attributes\":%d,\"active_uniforms\":%d",
           phase, transition, mode, first, count, indexed_type, submitted, error,
           glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER), buffer, read_buffer,
           viewport[0], viewport[1], viewport[2], viewport[3],
           scissor[0], scissor[1], scissor[2], scissor[3],
           glIsEnabled(GL_SCISSOR_TEST), glIsEnabled(GL_BLEND),
           glIsEnabled(GL_DEPTH_TEST), glIsEnabled(GL_STENCIL_TEST),
           glIsEnabled(GL_CULL_FACE), glIsEnabled(GL_DITHER),
           color_mask[0], color_mask[1], color_mask[2], color_mask[3],
           link, active_attributes, active_uniforms);
    observe_shaders((GLuint)program);
    GLint cull_mode = 0, front_face = 0, polygon[2] = {0}, logic = 0;
    glGetIntegerv(GL_CULL_FACE_MODE, &cull_mode);
    glGetIntegerv(GL_FRONT_FACE, &front_face);
    glGetIntegerv(GL_POLYGON_MODE, polygon);
    glGetIntegerv(GL_LOGIC_OP_MODE, &logic);
    printf(",\"cull_mode\":%d,\"front_face\":%d,\"polygon_mode\":[%d,%d],"
           "\"polygon_offset\":%d,\"logic_enabled\":%d,\"logic_mode\":%d,"
           "\"multisample\":%d,\"framebuffer_srgb\":%d,\"alpha_test\":%d",
           cull_mode, front_face, polygon[0], polygon[1], glIsEnabled(GL_POLYGON_OFFSET_FILL),
           glIsEnabled(GL_COLOR_LOGIC_OP), logic, glIsEnabled(GL_MULTISAMPLE),
           glIsEnabled(GL_FRAMEBUFFER_SRGB), glIsEnabled(GL_ALPHA_TEST));
    if (transition == 0 && !strcmp(phase, "restore")) {
        char path[4096];
        if (snprintf(path, sizeof(path), "%s/observed-context.txt", output) >= (int)sizeof(path))
            exit(8);
        FILE *file = fopen(path, "wb");
        GLint profile = 0;
        CGLContextObj context = CGLGetCurrentContext();
        if (file == NULL || context == NULL ||
            CGLDescribePixelFormat(CGLGetPixelFormat(context), 0,
                                   kCGLPFAOpenGLProfile, &profile) != kCGLNoError)
            exit(8);
        fprintf(file, "%s\n%s\n%s\n%d\n",
                (const char *)glGetString(GL_VENDOR),
                (const char *)glGetString(GL_RENDERER),
                (const char *)glGetString(GL_VERSION), profile);
        if (fclose(file) != 0) exit(8);
    }
    GLenum keys[] = {GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_PIXELS,
                     GL_PACK_SKIP_ROWS, GL_PACK_SWAP_BYTES, GL_PIXEL_PACK_BUFFER_BINDING};
    GLint previous[6] = {0};
    for (size_t i = 0; i < 6; i++) glGetIntegerv(keys[i], &previous[i]);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    for (size_t i = 0; i < 5; i++) glPixelStorei(keys[i], i == 0 ? 1 : 0);
    GLint color_type = 0, color_level = -1, depth_type = 0, stencil_type = 0;
    glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, (GLenum)buffer,
                                          GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &color_type);
    target_texture = 0;
    GLint color_format = 0, color_width = 0, color_height = 0, samples = 0;
    if (color_type == GL_TEXTURE) {
        glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, (GLenum)buffer,
                                              GL_FRAMEBUFFER_ATTACHMENT_TEXTURE_LEVEL, &color_level);
        glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, (GLenum)buffer,
                                              GL_FRAMEBUFFER_ATTACHMENT_OBJECT_NAME, &target_texture);
        GLint previous_texture = 0;
        glGetIntegerv(GL_TEXTURE_BINDING_2D, &previous_texture);
        glBindTexture(GL_TEXTURE_2D, (GLuint)target_texture);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level, GL_TEXTURE_INTERNAL_FORMAT, &color_format);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level, GL_TEXTURE_WIDTH, &color_width);
        glGetTexLevelParameteriv(GL_TEXTURE_2D, color_level, GL_TEXTURE_HEIGHT, &color_height);
        glBindTexture(GL_TEXTURE_2D, (GLuint)previous_texture);
    }
    glGetIntegerv(GL_SAMPLES, &samples);
    glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                                          GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &depth_type);
    glGetFramebufferAttachmentParameteriv(GL_DRAW_FRAMEBUFFER, GL_STENCIL_ATTACHMENT,
                                          GL_FRAMEBUFFER_ATTACHMENT_OBJECT_TYPE, &stencil_type);
    printf(",\"attachments\":{\"color_type\":%d,\"color_level\":%d,"
           "\"depth_type\":%d,\"stencil_type\":%d,\"format\":%d,"
           "\"width\":%d,\"height\":%d,\"samples\":%d}", color_type, color_level,
           depth_type, stencil_type, color_format, color_width, color_height, samples);
    observe_inputs((GLuint)program, active_uniforms, active_attributes);
    printf("}\n");
    fflush(stdout);
    glReadBuffer((GLenum)buffer);
    size_t size = (size_t)width * height * 4;
    void *pixels = calloc(size, 1);
    if (pixels == NULL) exit(6);
    glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, GL_BGRA, GL_UNSIGNED_BYTE, pixels);
    GLenum readback_error = glGetError();
    for (size_t i = 0; i < 5; i++) glPixelStorei(keys[i], previous[i]);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)previous[5]);
    glReadBuffer((GLenum)read_buffer);
    if (readback_error != GL_NO_ERROR) exit(7);
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/observed-%u-%s.bgra", output, transition, phase)
            >= (int)sizeof(path))
        exit(8);
    FILE *file = fopen(path, "wb");
    if (file == NULL || fwrite(pixels, 1, size, file) != size || fclose(file) != 0)
        exit(8);
    free(pixels);
}

void eu4_observed_draw_arrays(GLenum mode, GLint first, GLsizei count) {
    observe("restore", mode, first, count, 0, 0);
    if (!omit_draw) glDrawArrays(mode, first, count);
    glFinish();
    observe("post", mode, first, count, 0, !omit_draw);
    transition++;
}

void eu4_observed_draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    observe("restore", mode, 0, count, type, 0);
    if (!omit_draw) glDrawElements(mode, count, type, indices);
    glFinish();
    observe("post", mode, 0, count, type, !omit_draw);
    transition++;
}

int main(int argc, char **argv) {
    if (argc != 8 || strcmp(argv[4], "--control") || strcmp(argv[6], "--out"))
        return 2;
    if (strcmp(argv[1], "draw") && strcmp(argv[1], "omit"))
        return 2;
    omit_draw = !strcmp(argv[1], "omit");
    width = (unsigned)strtoul(argv[2], NULL, 10);
    height = (unsigned)strtoul(argv[3], NULL, 10);
    if (width == 0 || width > 8192 || height == 0 || height > 8192)
        return 2;
    output = argv[7];
    return eu4_candidate_draw_main(argc - 3, argv + 3);
}
