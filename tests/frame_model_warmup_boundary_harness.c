#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

static void workload(void) {
    static void (*draw)(GLenum, GLint, GLsizei) = NULL;
    if (!draw) draw = dlsym(RTLD_DEFAULT, "glDrawArrays");
    glEnable(GL_BLEND);
    if (draw) draw(GL_TRIANGLES, 0, 3);
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    void *library = dlopen(argv[1], RTLD_NOW);
    if (!library) return 2;
    unsigned (*published)(void) = dlsym(library, "eu4_frame_model_test_published_frame_count");
    void (*reset)(void) = dlsym(library, "eu4_frame_model_test_reset_published_frame_count");
    void (*arm)(unsigned) = dlsym(library, "eu4_frame_model_test_arm");
    void (*frame)(void (*)(void)) = dlsym(library, "eu4_frame_model_test_frame");
    if (!published || !reset || !arm || !frame) return 3;

    int fd = open(getenv("EU4_FRAME_MODEL_CONTROL"), O_RDWR);
    if (fd < 0) return 4;
    uint32_t *control = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (control == MAP_FAILED) return 5;
    control[0] = 3;
    control[1] = 2;
    control[2] = 1;
    control[3] = 1;
    control[4] = 1;
    control[5] = 0;

    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, kCGLPFAOpenGLProfile,
                                       (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core, 0};
    CGLPixelFormatObj pf = NULL;
    CGLContextObj ctx = NULL;
    GLint n = 0;
    if (CGLChoosePixelFormat(attrs, &pf, &n) != kCGLNoError || !pf) return 6;
    if (CGLCreateContext(pf, NULL, &ctx) != kCGLNoError || !ctx) return 7;
    if (CGLSetCurrentContext(ctx) != kCGLNoError) return 8;

    reset();
    frame(workload);
    if (published() != 0) {
        fprintf(stderr, "warm-up published %u frames (expected 0)\n", published());
        return 10;
    }
    arm(0);
    for (unsigned i = 0; i < 4; i++) frame(workload);
    if (published() != 4) {
        fprintf(stderr, "measured %u published frames (expected 4)\n", published());
        return 11;
    }
    puts("warm-up boundary: 0 published before arm, 4 after");
    munmap(control, 4096);
    close(fd);
    dlclose(library);
    return 0;
}
