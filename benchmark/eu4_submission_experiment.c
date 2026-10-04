// Profiler-off submission experiment — protocol v2 control plane (GOG EU IV 1.37.5).
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
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
static pthread_mutex_t header_lock = PTHREAD_MUTEX_INITIALIZER;
static void *control_map = MAP_FAILED;
static bool candidate_hook_installed = false;

bool eu4_submission_try_install_mesh_hook(void);
bool eu4_submission_mesh_hook_is_installed(void);

static uint32_t compiled_capability(void) {
    return (uint32_t)EU4_SUBMISSION_COMPILED_CAPABILITY;
}

static _Atomic uint64_t *map_counters(void) {
    return (_Atomic uint64_t *)(control_map + 32);
}

static void try_install_candidate_hook(void) {
    if (candidate_hook_installed) {
        return;
    }
    if (eu4_submission_try_install_mesh_hook()) {
        candidate_hook_installed = true;
        return;
    }
    const char *test_stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (test_stub && test_stub[0] == '1') {
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
        pthread_mutex_lock(&header_lock);
        uint32_t *words = (uint32_t *)control_map;
        words[7] = compiled_capability();
        pthread_mutex_unlock(&header_lock);
    }
}

void eu4_submission_record_candidate_site_entry(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    atomic_fetch_add_explicit(&map_counters()[COUNTER_CANDIDATE_SITE_ENTRIES], 1, memory_order_relaxed);
}

void eu4_submission_record_eligible_pair_hit(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    atomic_fetch_add_explicit(&map_counters()[COUNTER_ELIGIBLE_PAIR_HITS], 1, memory_order_relaxed);
}

void eu4_submission_record_effective_action(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    atomic_fetch_add_explicit(&map_counters()[COUNTER_EFFECTIVE_ACTIONS], 1, memory_order_relaxed);
}

bool eu4_submission_candidate_predicate_active(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return false;
    }
    uint32_t *words = (uint32_t *)control_map;
    if (words[0] != MAGIC || words[1] != PROTOCOL_VERSION) {
        return false;
    }
    return words[5] == MODE_CANDIDATE && words[7] == compiled_capability();
}

static void poll_command_ack(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&header_lock);
    uint32_t *words = (uint32_t *)control_map;
    if (words[0] != MAGIC || words[1] != PROTOCOL_VERSION) {
        pthread_mutex_unlock(&header_lock);
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
    pthread_mutex_unlock(&header_lock);
    atomic_fetch_add_explicit(&map_counters()[COUNTER_CONTROL_TICKS], 1, memory_order_relaxed);
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
