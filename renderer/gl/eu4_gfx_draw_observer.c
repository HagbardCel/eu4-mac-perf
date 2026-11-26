/* Trusted observation endpoint for the candidate selected-draw executor.
 * Only the candidate translation unit is compiled with renamed GL entry
 * points. This observer and the candidate share a native address space. */
#define GL_SILENCE_DEPRECATION
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <OpenGL/glext.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int eu4_candidate_draw_main(int argc, char **argv);

static const char *output;
static unsigned width, height;
static int omit_draw;

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
    printf("{\"phase\":\"%s\",\"mode\":%u,\"first\":%d,\"count\":%d,"
           "\"index_type\":%u,\"submitted\":%d,\"gl_error\":%u,"
           "\"framebuffer_status\":%u,\"draw_buffer\":%d,"
           "\"viewport\":[%d,%d,%d,%d],\"scissor\":[%d,%d,%d,%d],"
           "\"scissor_enabled\":%d,\"blend\":%d,\"depth_test\":%d,"
           "\"stencil_test\":%d,\"cull\":%d,\"dither\":%d,"
           "\"color_mask\":[%d,%d,%d,%d],\"linked\":%d,"
           "\"active_attributes\":%d,\"active_uniforms\":%d}\n",
           phase, mode, first, count, indexed_type, submitted, error,
           glCheckFramebufferStatus(GL_DRAW_FRAMEBUFFER), buffer,
           viewport[0], viewport[1], viewport[2], viewport[3],
           scissor[0], scissor[1], scissor[2], scissor[3],
           glIsEnabled(GL_SCISSOR_TEST), glIsEnabled(GL_BLEND),
           glIsEnabled(GL_DEPTH_TEST), glIsEnabled(GL_STENCIL_TEST),
           glIsEnabled(GL_CULL_FACE), glIsEnabled(GL_DITHER),
           color_mask[0], color_mask[1], color_mask[2], color_mask[3],
           link, active_attributes, active_uniforms);
    if (!strcmp(phase, "restore")) {
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
    fflush(stdout);

    GLenum keys[] = {GL_PACK_ALIGNMENT, GL_PACK_ROW_LENGTH, GL_PACK_SKIP_PIXELS,
                     GL_PACK_SKIP_ROWS, GL_PIXEL_PACK_BUFFER_BINDING};
    GLint previous[5] = {0};
    for (size_t i = 0; i < 5; i++) glGetIntegerv(keys[i], &previous[i]);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glReadBuffer((GLenum)buffer);
    for (size_t i = 0; i < 4; i++) glPixelStorei(keys[i], i == 0 ? 1 : 0);
    size_t size = (size_t)width * height * 4;
    void *pixels = calloc(size, 1);
    if (pixels == NULL) exit(6);
    glReadPixels(0, 0, (GLsizei)width, (GLsizei)height, GL_BGRA, GL_UNSIGNED_BYTE, pixels);
    GLenum readback_error = glGetError();
    for (size_t i = 0; i < 4; i++) glPixelStorei(keys[i], previous[i]);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, (GLuint)previous[4]);
    glReadBuffer((GLenum)read_buffer);
    if (readback_error != GL_NO_ERROR) exit(7);
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/observed-%s.bgra", output, phase) >= (int)sizeof(path))
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
}

void eu4_observed_draw_elements(GLenum mode, GLsizei count, GLenum type, const void *indices) {
    observe("restore", mode, 0, count, type, 0);
    if (!omit_draw) glDrawElements(mode, count, type, indices);
    glFinish();
    observe("post", mode, 0, count, type, !omit_draw);
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
