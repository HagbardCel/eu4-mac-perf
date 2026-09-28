#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

static unsigned long long monotonic_ns(void) {
    struct timespec now;
    clock_gettime(CLOCK_UPTIME_RAW, &now);
    return (unsigned long long)now.tv_sec * 1000000000ull + now.tv_nsec;
}

int main(int argc, char **argv) {
    CGLPixelFormatAttribute attributes[] = {kCGLPFAAccelerated, 0};
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint formats = 0;
    if (CGLChoosePixelFormat(attributes, &format, &formats) != kCGLNoError || !format)
        return 2;
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || !context)
        return 3;
    CGLSetCurrentContext(context);
    if (argc > 1 && strcmp(argv[1], "--stress") == 0) {
        for (int i = 0; i < 100000; ++i) glEnable(GL_BLEND);
        unsigned long long start = monotonic_ns();
        for (int i = 0; i < 1000000; ++i) glEnable(GL_BLEND);
        printf("stress_ns=%llu\n", monotonic_ns() - start);
        CGLSetCurrentContext(NULL);
        CGLDestroyContext(context);
        CGLDestroyPixelFormat(format);
        return 0;
    }
    void (*bind_framebuffer)(GLenum, GLuint) = dlsym(RTLD_DEFAULT, "glBindFramebuffer");
    if (!bind_framebuffer) return 4;
    struct timeval instant = {0, 0};
    select(0, NULL, NULL, NULL, &instant);
    poll(NULL, 0, 0);
    struct timespec brief = {0, 1000000};
    nanosleep(&brief, NULL);
    for (int i = 0; i < 160; ++i) {
        glEnable(GL_BLEND);
        glEnable(GL_BLEND);
        glBindTexture(GL_TEXTURE_2D, 0);
        bind_framebuffer(GL_FRAMEBUFFER, 0);
        glClear(GL_COLOR_BUFFER_BIT);
        glDrawArrays(GL_TRIANGLES, 0, 3);
        CGLFlushDrawable(context);
        usleep(10000);
    }
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    puts("harness complete");
    return 0;
}
