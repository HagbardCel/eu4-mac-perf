#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int eu4_metal_draw_render(const void *vertices, int vertex_bytes, int stride, int color_offset,
                          int color_is_byte, const float *uniform, int vertex_count, int width,
                          int height, int as_strip, int cull_mode, int front_ccw, unsigned char *pixels_out);
int eu4_metal_draw_copy_shader_source(unsigned shader, char **text, int *length);

static int triangle(int cull_mode, int front_ccw) {
    float vertices[] = {
        -0.5f, -0.5f, 1, 0, 0, 1,
         0.5f, -0.5f, 1, 0, 0, 1,
         0.0f,  0.5f, 1, 0, 0, 1,
    };
    float uniform[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    return eu4_metal_draw_render(vertices, (int)sizeof vertices, 24, 8, 0, uniform, 3, 64, 64, 0,
                                 cull_mode, front_ccw, NULL);
}

static int shader_round_trip(void) {
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint npix = 0;
    CGLPixelFormatAttribute attrs[] = {kCGLPFAAllRenderers, 0};
    if (CGLChoosePixelFormat(attrs, &format, &npix) != kCGLNoError || format == NULL) {
        fprintf(stderr, "no pixel format\n");
        return 1;
    }
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || CGLSetCurrentContext(context) != kCGLNoError) {
        fprintf(stderr, "no gl context\n");
        return 1;
    }
    GLuint shader = glCreateShader(GL_VERTEX_SHADER);
    const char *source = "void main(){ gl_Position = vec4(0.0); }\n";
    glShaderSource(shader, 1, &source, NULL);
    char *text = NULL;
    char *ignored = NULL;
    int length = 0;
    int ignored_length = 0;
    int copied = eu4_metal_draw_copy_shader_source(shader, &text, &length);
    int empty = eu4_metal_draw_copy_shader_source(0, &ignored, &ignored_length);
    int ok = copied == 0 && text != NULL && length == (int)strlen(source) && strcmp(text, source) == 0 && empty != 0;
    if (!ok) {
        fprintf(stderr, "shader copy failed copied=%d length=%d\n", copied, length);
    }
    free(text);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    return ok ? 0 : 1;
}

int main(void) {
    int plain = triangle(0, 1);
    int kept = triangle(1, 1);
    int culled = triangle(1, 0);
    if (plain <= 0 || kept <= 0 || culled != 0) {
        fprintf(stderr, "pixels plain=%d kept=%d culled=%d\n", plain, kept, culled);
        return 1;
    }
    if (shader_round_trip() != 0) {
        return 1;
    }
    printf("%d\n", plain);
    return 0;
}
