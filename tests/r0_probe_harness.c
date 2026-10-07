#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void clear_color(float r, float g, float b) {
    glClearColor(r, g, b, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
}

int main(void) {
    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, (CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || !format) {
        return 2;
    }
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || !context) {
        return 3;
    }
    if (CGLSetCurrentContext(context) != kCGLNoError) {
        return 4;
    }
    glViewport(0, 0, 64, 64);
    for (int frame = 0; frame < 6; frame++) {
        float shade = 0.1f + 0.1f * (float)frame;
        clear_color(shade, 0.2f, 0.3f);
        CGLFlushDrawable(context);
        usleep(1000);
    }
    return 0;
}
