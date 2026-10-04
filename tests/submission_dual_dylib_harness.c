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
#define CONTROL_SIZE 4096u

int main(void) {
    const char *control_path = getenv("EU4_SUBMISSION_CONTROL");
    const char *probe_log = getenv("EU4_AUTO_PROBE_LOG");
    if (!control_path || !probe_log) {
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
    words[0] = MAGIC;
    words[1] = 1;
    words[2] = 0;
    words[3] = 1;
    words[4] = 0;
    words[5] = 0;
    words[6] = 42;

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
    void (*record_effective)(void) = dlsym(RTLD_DEFAULT, "eu4_submission_record_effective_action");
    for (int i = 0; i < 140; i++) {
        if (i == 0 || i == 70) {
            words[2]++;
            words[3] = (i < 70) ? 0u : 1u;
        }
        CGLFlushDrawable(context);
        if (record_effective && i >= 70) {
            record_effective();
        }
        usleep(10000);
    }
    uint64_t *counters = (uint64_t *)((char *)map + 32);
    printf("hook_attempts=%llu effective_actions=%llu ack_generation=%u ack_mode=%u\n",
           (unsigned long long)counters[0], (unsigned long long)counters[1], words[4], words[5]);
    CGLSetCurrentContext(NULL);
    CGLDestroyContext(context);
    CGLDestroyPixelFormat(format);
    munmap(map, CONTROL_SIZE);
    return 0;
}
