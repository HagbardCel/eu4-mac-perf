// Profiler-off submission experiment ack + counter plane (GOG EU IV 1.37.5).
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MAGIC 0x53425545u
#define PROTOCOL_VERSION 1u
#define CONTROL_SIZE 4096u
#define MODE_REFERENCE 0u
#define MODE_CANDIDATE 1u

static pthread_once_t setup_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void *control_map = MAP_FAILED;

static void setup(void) {
    const char *path = getenv("EU4_SUBMISSION_CONTROL");
    if (!path || path[0] != '/') {
        return;
    }
    int fd = open(path, O_RDWR);
    if (fd < 0) {
        return;
    }
    control_map = mmap(NULL, CONTROL_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
}

static void poll_command_ack(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&lock);
    uint32_t *words = (uint32_t *)control_map;
    if (words[0] != MAGIC || words[1] != PROTOCOL_VERSION) {
        pthread_mutex_unlock(&lock);
        return;
    }
    uint32_t command_generation = words[2];
    uint32_t requested_mode = words[3];
    uint32_t ack_generation = words[4];
    uint32_t ack_mode = words[5];
    uint64_t *counters = (uint64_t *)(control_map + 32);
    if (command_generation > ack_generation) {
        ack_mode = requested_mode;
        ack_generation = command_generation;
        words[4] = ack_generation;
        words[5] = ack_mode;
    }
    counters[0]++;
    pthread_mutex_unlock(&lock);
}

void eu4_submission_record_effective_action(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&lock);
    uint64_t *counters = (uint64_t *)(control_map + 32);
    counters[1]++;
    pthread_mutex_unlock(&lock);
}

static CGLError submission_flush(CGLContextObj context) {
    CGLError result = CGLFlushDrawable(context);
    poll_command_ack();
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement, *replacee;
} interpose = {(const void *)submission_flush, (const void *)CGLFlushDrawable};
