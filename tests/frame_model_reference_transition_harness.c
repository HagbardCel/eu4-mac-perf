#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

enum { PROFILE = 1, REFERENCE = 7 };

static GLuint program;

static GLuint shader(GLenum type, const char *source) {
    GLuint id = glCreateShader(type);
    glShaderSource(id, 1, &source, NULL);
    glCompileShader(id);
    GLint ok = 0;
    glGetShaderiv(id, GL_COMPILE_STATUS, &ok);
    if (!ok) exit(7);
    return id;
}

static void init_gl_objects(void) {
    const char *vs = "#version 150\nin vec2 position; void main(){gl_Position=vec4(position,0,1);}";
    const char *fs = "#version 150\nout vec4 result; void main(){result=vec4(1);}";
    GLuint vert = shader(GL_VERTEX_SHADER, vs);
    GLuint frag = shader(GL_FRAGMENT_SHADER, fs);
    program = glCreateProgram();
    glAttachShader(program, vert);
    glAttachShader(program, frag);
    glBindAttribLocation(program, 0, "position");
    glLinkProgram(program);
    GLint ok = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &ok);
    if (!ok) exit(8);
    GLuint vao;
    glGenVertexArrays(1, &vao);
    glBindVertexArray(vao);
}

static void workload(void) {
    static void (*use_program)(GLuint) = NULL;
    static void (*draw)(GLenum, GLint, GLsizei) = NULL;
    if (!use_program) use_program = dlsym(RTLD_DEFAULT, "glUseProgram");
    if (!draw) draw = dlsym(RTLD_DEFAULT, "glDrawArrays");
    if (use_program) use_program(program);
    if (draw) draw(GL_TRIANGLES, 0, 3);
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    void *library = dlopen(argv[1], RTLD_NOW);
    if (!library) return 2;
    void (*arm)(unsigned) = dlsym(library, "eu4_frame_model_test_arm");
    void (*frame)(void (*)(void)) = dlsym(library, "eu4_frame_model_test_frame");
    void (*apply_mode)(unsigned) = dlsym(library, "eu4_frame_model_test_apply_mode");
    void (*simulate_seeded)(void) = dlsym(library, "eu4_frame_model_test_simulate_seeded_shadow");
    unsigned (*seeded)(void) = dlsym(library, "eu4_frame_model_test_state_seeded");
    unsigned (*observe)(void) = dlsym(library, "eu4_frame_model_test_observe_unowned");
    if (!arm || !frame || !apply_mode || !simulate_seeded || !seeded || !observe) return 3;

    int fd = open(getenv("EU4_FRAME_MODEL_CONTROL"), O_RDWR);
    if (fd < 0) return 4;
    uint32_t *control = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (control == MAP_FAILED) return 5;
    control[0] = 3;
    control[1] = 2;
    control[2] = PROFILE;
    control[3] = 1;
    control[4] = 3;
    control[5] = 0;
    __atomic_store_n(&control[1], 3, __ATOMIC_RELEASE);

    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, kCGLPFAOpenGLProfile,
                                       (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core, 0};
    CGLPixelFormatObj pf = NULL;
    CGLContextObj ctx = NULL;
    GLint n = 0;
    if (CGLChoosePixelFormat(attrs, &pf, &n) != kCGLNoError || !pf) return 6;
    if (CGLCreateContext(pf, NULL, &ctx) != kCGLNoError || !ctx) return 7;
    if (CGLSetCurrentContext(ctx) != kCGLNoError) return 8;
    init_gl_objects();

    arm(0);
    simulate_seeded();
    if (!seeded()) {
        fprintf(stderr, "expected simulated PROFILE shadow seed\n");
        return 10;
    }
    apply_mode(REFERENCE);
    if (seeded()) {
        fprintf(stderr, "shadow cache must be dropped entering REFERENCE\n");
        return 11;
    }
    workload();
    if (seeded()) {
        fprintf(stderr, "REFERENCE pass-through must not re-seed shadow\n");
        return 12;
    }
    apply_mode(PROFILE);
    if ((observe() & 0xff) != PROFILE) {
        fprintf(stderr, "unowned observe did not refresh to PROFILE (got %u)\n", observe() & 0xff);
        return 13;
    }
    if (seeded()) {
        fprintf(stderr, "PROFILE after REFERENCE must not reuse stale seeded cache\n");
        return 14;
    }
    puts("REFERENCE transition: shadow invalidated across mode changes");
    munmap(control, 4096);
    close(fd);
    dlclose(library);
    return 0;
}
