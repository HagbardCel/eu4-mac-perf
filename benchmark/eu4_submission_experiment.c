// Profiler-off submission experiment — protocol v2 control plane (GOG EU IV 1.37.5).
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
#define PROTOCOL_VERSION 2u
#define CONTROL_SIZE 4096u
#define MODE_REFERENCE 0u
#define MODE_CANDIDATE 1u

#ifndef EU4_SUBMISSION_COMPILED_CAPABILITY
#define EU4_SUBMISSION_COMPILED_CAPABILITY 1u
#endif

enum {
    COUNTER_CONTROL_TICKS = 0,
    COUNTER_CANDIDATE_SITE_ENTRIES = 1,
    COUNTER_ELIGIBLE_PAIR_HITS = 2,
    COUNTER_EFFECTIVE_ACTIONS = 3,
    COUNTER_COUNT = 4,
};

static pthread_once_t setup_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void *control_map = MAP_FAILED;
static bool candidate_hook_installed = false;

static uint32_t compiled_capability(void) {
    const char *env = getenv("EU4_SUBMISSION_COMPILED_CAPABILITY");
    if (!env || !*env) {
        return EU4_SUBMISSION_COMPILED_CAPABILITY;
    }
    return (uint32_t)strtoul(env, NULL, 10);
}

static void try_install_candidate_hook(void) {
    if (candidate_hook_installed) {
        return;
    }
    const char *stub = getenv("EU4_SUBMISSION_STUB_CANDIDATE_HOOK");
    if (stub && stub[0] == '1') {
        candidate_hook_installed = true;
    }
}

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
    try_install_candidate_hook();
    if (control_map != MAP_FAILED) {
        pthread_mutex_lock(&lock);
        uint32_t *words = (uint32_t *)control_map;
        words[7] = compiled_capability();
        pthread_mutex_unlock(&lock);
    }
}

static uint64_t *counters(void) {
    return (uint64_t *)(control_map + 32);
}

void eu4_submission_record_candidate_site_entry(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&lock);
    counters()[COUNTER_CANDIDATE_SITE_ENTRIES]++;
    pthread_mutex_unlock(&lock);
}

void eu4_submission_record_eligible_pair_hit(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&lock);
    counters()[COUNTER_ELIGIBLE_PAIR_HITS]++;
    pthread_mutex_unlock(&lock);
}

void eu4_submission_record_effective_action(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&lock);
    counters()[COUNTER_EFFECTIVE_ACTIONS]++;
    pthread_mutex_unlock(&lock);
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
    uint32_t requested_capability = words[6];
    uint32_t advertised_capability = words[7];
    uint32_t compiled = compiled_capability();
    if (advertised_capability == 0) {
        advertised_capability = compiled;
        words[7] = advertised_capability;
    }
    if (command_generation > ack_generation) {
        ack_mode = requested_mode;
        if (requested_mode == MODE_CANDIDATE) {
            if (requested_capability != compiled || advertised_capability != compiled || !candidate_hook_installed) {
                ack_mode = MODE_REFERENCE;
            }
        }
        ack_generation = command_generation;
        words[4] = ack_generation;
        words[5] = ack_mode;
    }
    counters()[COUNTER_CONTROL_TICKS]++;
    if (getenv("EU4_SUBMISSION_STUB_CANDIDATE_SITE") && getenv("EU4_SUBMISSION_STUB_CANDIDATE_SITE")[0] == '1') {
        counters()[COUNTER_CANDIDATE_SITE_ENTRIES]++;
    }
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
