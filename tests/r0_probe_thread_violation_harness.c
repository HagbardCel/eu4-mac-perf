#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static CGLContextObj owner_context;
static CGLContextObj worker_context;
static volatile bool worker_done;

static GLuint fbo_with_texture(CGLContextObj context, int width, int height) {
    (void)context;
    GLuint texture = 0;
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, NULL);
    GLuint fbo = 0;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fprintf(stderr, "framebuffer incomplete\n");
        exit(2);
    }
    return fbo;
}

static void *worker_flush(void *arg) {
    (void)arg;
    CGLSetCurrentContext(worker_context);
    for (int frame = 0; frame < 3; frame++) {
        glClearColor(0.2f, 0.3f, 0.4f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        CGLFlushDrawable(worker_context);
        usleep(2000);
    }
    worker_done = true;
    return NULL;
}

int main(void) {
    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, (CGLPixelFormatAttribute)0};
    CGLPixelFormatObj format = NULL;
    GLint count = 0;
    if (CGLChoosePixelFormat(attrs, &format, &count) != kCGLNoError || !format) {
        return 2;
    }
    if (CGLCreateContext(format, NULL, &owner_context) != kCGLNoError || !owner_context) {
        return 3;
    }
    if (CGLCreateContext(format, owner_context, &worker_context) != kCGLNoError || !worker_context) {
        return 4;
    }
    CGLSetCurrentContext(owner_context);
    const int width = 64;
    const int height = 64;
    GLuint owner_fbo = fbo_with_texture(owner_context, width, height);
    glBindFramebuffer(GL_FRAMEBUFFER, owner_fbo);
    glViewport(0, 0, width, height);

    CGLSetCurrentContext(worker_context);
    GLuint worker_fbo = fbo_with_texture(worker_context, width, height);
    glBindFramebuffer(GL_FRAMEBUFFER, worker_fbo);
    glViewport(0, 0, width, height);

    pthread_t thread;
    worker_done = false;
    pthread_create(&thread, NULL, worker_flush, NULL);

    CGLSetCurrentContext(owner_context);
    glBindFramebuffer(GL_FRAMEBUFFER, owner_fbo);
    for (int frame = 0; frame < 6; frame++) {
        glClearColor(0.1f, 0.2f, 0.3f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        CGLFlushDrawable(owner_context);
        usleep(2000);
    }
    pthread_join(thread, NULL);
    return 0;
}
