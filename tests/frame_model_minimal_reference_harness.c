#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
    unsigned (*violations)(void) = dlsym(library, "eu4_frame_model_test_loaded_disabled_accounting_violations");
    void (*reset)(void) = dlsym(library, "eu4_frame_model_test_reset_published_frame_count");
    void (*arm)(unsigned) = dlsym(library, "eu4_frame_model_test_arm");
    void (*frame)(void (*)(void)) = dlsym(library, "eu4_frame_model_test_frame");
    if (!published || !violations || !reset || !arm || !frame) return 3;

    int fd = open(getenv("EU4_FRAME_MODEL_CONTROL"), O_RDWR);
    if (fd < 0) return 4;
    uint32_t *control = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (control == MAP_FAILED) return 5;
    control[0] = 3;
    control[1] = 2;
    control[2] = 7;
    control[3] = 1;
    control[4] = 0;
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
    arm(0);
    for (unsigned i = 0; i < 4; i++) frame(workload);
    if (published() != 0) {
        fprintf(stderr, "minimal-reference published %u frames (expected 0)\n", published());
        return 11;
    }
    if ((control[4] & 2u) == 0) {
        fprintf(stderr, "minimal-reference cleared MEASURE_ENABLED (flags=%u)\n", control[4]);
        return 12;
    }
    if (violations() != 0) {
        fprintf(stderr, "minimal-reference loaded-disabled accounting violations=%u (expected 0)\n", violations());
        return 13;
    }
    puts("minimal-reference v1: MEASURE_ENABLED, 0 published, REFERENCE hooks without scopes/events/publish");
    munmap(control, 4096);
    close(fd);
    dlclose(library);
    return 0;
}
