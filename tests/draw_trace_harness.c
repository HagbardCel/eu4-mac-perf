#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <stdint.h>
#include <stdio.h>

static volatile int completed_draws;
__attribute__((noinline)) static void synthetic_engine_draw(int n) {
    glBindTexture(GL_TEXTURE_2D,(GLuint)(n&1));
    glDrawArrays(GL_TRIANGLES,0,3);
    completed_draws++;
}

int main(void) {
    CGLPixelFormatAttribute attributes[]={kCGLPFAAccelerated,0};
    CGLPixelFormatObj format=NULL;
    CGLContextObj context=NULL;
    GLint formats=0;
    if (CGLChoosePixelFormat(attributes,&format,&formats)!=kCGLNoError || !format) return 2;
    if (CGLCreateContext(format,NULL,&context)!=kCGLNoError || !context) return 3;
    CGLSetCurrentContext(context);
    CGLFlushDrawable(context); // establish the rendering thread
    for (int frame=0;frame<40;frame++) {
        for (int draw=0;draw<5;draw++) synthetic_engine_draw(draw);
        CGLFlushDrawable(context);
    }
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    puts("draw harness complete");
    return 0;
}
