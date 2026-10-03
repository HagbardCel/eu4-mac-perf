#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <OpenGL/gl3.h>
#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <sys/mman.h>
#include <unistd.h>

enum { PROFILE = 1, REFERENCE = 7 };

static GLuint program;
static GLuint vao_a, vao_b, vao_c;
static CGLContextObj ctx_a, ctx_b, ctx_c;

static GLuint shader(GLenum type, const char *source) {
    GLuint id = glCreateShader(type);
    glShaderSource(id, 1, &source, NULL);
    glCompileShader(id);
    GLint ok = 0;
    glGetShaderiv(id, GL_COMPILE_STATUS, &ok);
    if (!ok) exit(7);
    return id;
}

static void init_gl_objects(CGLContextObj ctx) {
    if (CGLSetCurrentContext(ctx) != kCGLNoError) exit(8);
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
    if (!ok) exit(9);
    GLuint *slot = NULL;
    if (ctx == ctx_a) slot = &vao_a;
    else if (ctx == ctx_b) slot = &vao_b;
    else if (ctx == ctx_c) slot = &vao_c;
    if (!slot) return;
    if (!*slot) glGenVertexArrays(1, slot);
    glBindVertexArray(*slot);
}

static void touch_shadow_wrapper(void) {
    static void (*use_program)(GLuint) = NULL;
    static void (*bind_vao)(GLuint) = NULL;
    if (!use_program) use_program = dlsym(RTLD_DEFAULT, "glUseProgram");
    if (!bind_vao) bind_vao = dlsym(RTLD_DEFAULT, "glBindVertexArray");
    if (use_program) use_program(program);
    if (bind_vao) {
        GLuint vao = 0;
        CGLContextObj ctx = CGLGetCurrentContext();
        if (ctx == ctx_a) vao = vao_a;
        else if (ctx == ctx_b) vao = vao_b;
        else if (ctx == ctx_c) vao = vao_c;
        if (vao) bind_vao(vao);
    }
}

static GLuint build_program(CGLContextObj ctx) {
    if (CGLSetCurrentContext(ctx) != kCGLNoError) exit(8);
    const char *vs = "#version 150\nin vec2 position; void main(){gl_Position=vec4(position,0,1);}";
    const char *fs = "#version 150\nout vec4 result; void main(){result=vec4(0.5,0,0,1);}";
    GLuint vert = shader(GL_VERTEX_SHADER, vs);
    GLuint frag = shader(GL_FRAGMENT_SHADER, fs);
    GLuint linked = glCreateProgram();
    glAttachShader(linked, vert);
    glAttachShader(linked, frag);
    glBindAttribLocation(linked, 0, "position");
    glLinkProgram(linked);
    GLint ok = 0;
    glGetProgramiv(linked, GL_LINK_STATUS, &ok);
    if (!ok) exit(9);
    return linked;
}

typedef struct {
    CGLContextObj ctx;
    GLuint alt_program;
    int rc;
} MigrateArgs;

static void *migrate_worker(void *arg) {
    MigrateArgs *args = (MigrateArgs *)arg;
    static void (*use_program)(GLuint) = NULL;
    if (!use_program) use_program = dlsym(RTLD_DEFAULT, "glUseProgram");
    if (CGLSetCurrentContext(args->ctx) != kCGLNoError) {
        args->rc = 22;
        return NULL;
    }
    if (use_program) use_program(args->alt_program);
    CGLSetCurrentContext(NULL);
    args->rc = 0;
    return NULL;
}

static void publish_mode(uint32_t *control, uint32_t mode) {
    uint32_t seq = atomic_load_explicit((_Atomic uint32_t *)&control[1], memory_order_acquire);
    atomic_store_explicit((_Atomic uint32_t *)&control[1], seq | 1u, memory_order_release);
    control[2] = mode;
    atomic_store_explicit((_Atomic uint32_t *)&control[1], (seq | 1u) + 1u, memory_order_release);
}

int main(int argc, char **argv) {
    if (argc != 2) return 1;
    void *library = dlopen(argv[1], RTLD_NOW);
    if (!library) return 2;
    void (*simulate_seeded)(void) = dlsym(library, "eu4_frame_model_test_simulate_seeded_shadow");
    unsigned (*seeded)(void) = dlsym(library, "eu4_frame_model_test_state_seeded");
    unsigned (*cached)(void) = dlsym(library, "eu4_frame_model_test_cached_shadow_contexts");
    unsigned (*observe)(void) = dlsym(library, "eu4_frame_model_test_observe_unowned");
    void (*touch_vao)(unsigned) = dlsym(library, "eu4_frame_model_test_touch_shadow_vao");
    uint64_t (*lifetime_stamp)(uintptr_t) = dlsym(library, "eu4_frame_model_test_lifetime_stamp");
    uintptr_t (*tls_program)(uintptr_t) = dlsym(library, "eu4_frame_model_test_tls_shadow_program");
    if (!simulate_seeded || !seeded || !cached || !observe || !touch_vao || !lifetime_stamp || !tls_program)
        return 3;

    int fd = open(getenv("EU4_FRAME_MODEL_CONTROL"), O_RDWR);
    if (fd < 0) return 4;
    uint32_t *control = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (control == MAP_FAILED) return 5;
    control[0] = 3;
    control[1] = 2;
    control[2] = PROFILE;
    control[3] = 0;
    control[4] = 1;
    control[5] = 0;
    atomic_store_explicit((_Atomic uint32_t *)&control[1], 3, memory_order_release);

    CGLPixelFormatAttribute attrs[] = {kCGLPFAAccelerated, kCGLPFAOpenGLProfile,
                                       (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core, 0};
    CGLPixelFormatObj pf = NULL;
    GLint n = 0;
    if (CGLChoosePixelFormat(attrs, &pf, &n) != kCGLNoError || !pf) return 6;
    if (CGLCreateContext(pf, NULL, &ctx_a) != kCGLNoError || !ctx_a) return 7;
    if (CGLCreateContext(pf, ctx_a, &ctx_b) != kCGLNoError || !ctx_b) return 7;
    if (CGLCreateContext(pf, ctx_a, &ctx_c) != kCGLNoError || !ctx_c) return 7;

    init_gl_objects(ctx_a);
    simulate_seeded();
    init_gl_objects(ctx_b);
    simulate_seeded();
    init_gl_objects(ctx_c);
    simulate_seeded();
    if (cached() < 3) {
        fprintf(stderr, "expected three cached shadow contexts, got %u\n", cached());
        return 10;
    }

    publish_mode(control, REFERENCE);
    touch_shadow_wrapper();
    if (cached() != 0 || seeded()) {
        fprintf(stderr, "shared REFERENCE transition must clear all shadow caches\n");
        return 11;
    }

    if (CGLSetCurrentContext(ctx_c) != kCGLNoError) return 12;
    touch_shadow_wrapper();
    if (CGLSetCurrentContext(ctx_b) != kCGLNoError) return 12;
    touch_shadow_wrapper();

    publish_mode(control, PROFILE);
    if ((observe() & 0xff) != PROFILE) {
        fprintf(stderr, "observe did not refresh to PROFILE via control page (got %u)\n", observe() & 0xff);
        return 13;
    }
    if (seeded()) {
        fprintf(stderr, "PROFILE refresh must not resurrect pre-REFERENCE seeded cache\n");
        return 14;
    }

    if (CGLSetCurrentContext(ctx_c) != kCGLNoError) return 15;
    touch_vao(vao_c);
    if (!seeded() || cached() == 0) {
        fprintf(stderr,
                "expected fresh shadow cache after PROFILE GL wrapper on context C (seeded=%u cached=%u observe=0x%x)\n",
                seeded(), cached(), observe());
        return 16;
    }

    uintptr_t ctx_key = (uintptr_t)ctx_c;
    if (tls_program(ctx_key) != (uintptr_t)program) {
        fprintf(stderr, "expected thread-A shadow program %u before migration\n", program);
        return 17;
    }
    uint64_t stamp_before = lifetime_stamp(ctx_key);
    GLuint alt_program = build_program(ctx_c);
    publish_mode(control, REFERENCE);
    MigrateArgs migrate = {.ctx = ctx_c, .alt_program = alt_program, .rc = -1};
    pthread_t worker;
    if (pthread_create(&worker, NULL, migrate_worker, &migrate)) return 18;
    pthread_join(worker, NULL);
    if (migrate.rc) return migrate.rc;
    if (lifetime_stamp(ctx_key) <= stamp_before) {
        fprintf(stderr, "REFERENCE context migration must advance global lifetime stamp\n");
        return 19;
    }
    publish_mode(control, PROFILE);
    if (CGLSetCurrentContext(ctx_c) != kCGLNoError) return 20;
    touch_vao(vao_c);
    if (tls_program(ctx_key) != (uintptr_t)alt_program) {
        fprintf(stderr, "thread A must rebuild shadow after cross-thread REFERENCE migration\n");
        return 21;
    }

    puts("REFERENCE transition: shared-control mode changes clear multi-context shadow");
    munmap(control, 4096);
    close(fd);
    dlclose(library);
    return 0;
}
