#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MAGIC 0x53425545u
#define PROTOCOL_VERSION 3u
#define CONTROL_SIZE 4096u
#define MODE_REFERENCE 0u
#define MODE_CANDIDATE 1u

static uint32_t compiled_capability(void) {
    const char *env = getenv("EU4_SUBMISSION_COMPILED_CAPABILITY");
    if (!env || !*env) {
        return 2u;
    }
    return (uint32_t)strtoul(env, NULL, 10);
}

static uint32_t install_attempts(void) {
    uint32_t (*counter)(void) =
        (uint32_t (*)(void))dlsym(RTLD_DEFAULT, "eu4_submission_test_install_attempt_count");
    if (!counter) {
        fprintf(stderr, "missing eu4_submission_test_install_attempt_count\n");
        exit(20);
    }
    return counter();
}

static void flush_context(CGLContextObj context) {
    CGLFlushDrawable(context);
    usleep(5000);
}

static void bump_mode(uint32_t *words, uint32_t mode, uint32_t requested_cap) {
    words[2]++;
    words[3] = mode;
    words[6] = requested_cap;
}

int main(void) {
    const char *control_path = getenv("EU4_SUBMISSION_CONTROL");
    if (!control_path) {
        return 2;
    }
    int fd = open(control_path, O_RDWR);
    if (fd < 0) {
        return 3;
    }
    void *map = mmap(NULL, CONTROL_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (map == MAP_FAILED) {
        return 4;
    }
    uint32_t *words = (uint32_t *)map;
    const uint32_t compiled = compiled_capability();
    words[0] = MAGIC;
    words[1] = PROTOCOL_VERSION;
    words[2] = 0;
    words[3] = MODE_REFERENCE;
    words[4] = 0;
    words[5] = MODE_REFERENCE;
    words[6] = compiled;
    words[7] = 0;

    CGLPixelFormatAttribute attributes[] = {kCGLPFAAccelerated, 0};
    CGLPixelFormatObj format = NULL;
    CGLContextObj context = NULL;
    GLint formats = 0;
    if (CGLChoosePixelFormat(attributes, &format, &formats) != kCGLNoError || !format) {
        return 5;
    }
    if (CGLCreateContext(format, NULL, &context) != kCGLNoError || !context) {
        return 6;
    }
    CGLSetCurrentContext(context);

    for (int i = 0; i < 25; i++) {
        flush_context(context);
    }
    if (install_attempts() != 0) {
        fprintf(stderr, "expected 0 install attempts after reference flushes, got %u\n", install_attempts());
        return 10;
    }

    bump_mode(words, MODE_REFERENCE, compiled);
    flush_context(context);
    if (install_attempts() != 0) {
        fprintf(stderr, "expected 0 install attempts after MODE_REFERENCE command\n");
        return 11;
    }

    bump_mode(words, MODE_CANDIDATE, compiled + 1u);
    flush_context(context);
    if (install_attempts() != 0) {
        fprintf(stderr, "expected 0 install attempts for wrong capability candidate\n");
        return 12;
    }

    bump_mode(words, MODE_CANDIDATE, compiled);
    flush_context(context);
    if (install_attempts() != 1) {
        fprintf(stderr, "expected 1 install attempt after matching candidate, got %u\n", install_attempts());
        return 13;
    }
    if (words[5] != MODE_CANDIDATE) {
        fprintf(stderr, "expected ack_mode candidate, got %u\n", words[5]);
        return 14;
    }

    bump_mode(words, MODE_CANDIDATE, compiled);
    flush_context(context);
    if (install_attempts() != 1) {
        fprintf(stderr, "expected install attempts to remain 1, got %u\n", install_attempts());
        return 15;
    }

    printf("deferred_install_ok attempts=%u\n", install_attempts());
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    munmap(map, CONTROL_SIZE);
    return 0;
}
