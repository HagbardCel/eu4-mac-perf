// Profiler-off submission experiment — protocol v3 control plane (GOG EU IV 1.37.5).
#define GL_SILENCE_DEPRECATION 1
#include <OpenGL/OpenGL.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "submission_counter_schema.h"
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
#include "eu4_submission_border.h"
#include "eu4_submission_border_loop_head.h"
#include "eu4_submission_border_observer_active.h"
#include "eu4_submission_test_hooks.h"
void eu4_submission_observation_arm_reset(void);
#else
#include "eu4_submission_observation.h"
#endif

#define MAGIC 0x53425545u
#define PROTOCOL_VERSION 3u
#define CONTROL_SIZE 4096u
#define MODE_REFERENCE 0u
#define MODE_CANDIDATE 1u

#define HEADER_WORDS 8u
#define META_WORDS 4u
#define OBS_WORDS 4u
#define BORDER_FLAGS_OFFSET 56u
#define COUNTER_REGION_OFFSET 64u

#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
uint32_t g_eu4_border_control_flags = 0;
_Atomic unsigned char g_eu4_border_observer_active = 0;
#endif

#if defined(EU4_SUBMISSION_TRACK_INSTALL_ATTEMPTS)
static uint32_t g_install_attempt_count = 0;

uint32_t eu4_submission_test_install_attempt_count(void) {
    return g_install_attempt_count;
}
#endif

#define OBS_DISARMED 0u
#define OBS_ARM 1u
#define OBS_FREEZE 2u
#define OBS_ACK_DISARMED 0u
#define OBS_ACK_ARMED 1u
#define OBS_ACK_FROZEN 2u

#ifndef EU4_SUBMISSION_COMPILED_CAPABILITY
#define EU4_SUBMISSION_COMPILED_CAPABILITY 1u
#endif

static pthread_once_t setup_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t header_lock = PTHREAD_MUTEX_INITIALIZER;
static void *control_map = MAP_FAILED;
static bool hooks_installed = false;
static uint32_t last_observed_ack_mode = MODE_REFERENCE;
static bool observation_frozen = false;

bool eu4_submission_try_install_all_hooks(void);
bool eu4_submission_hooks_are_installed(void);

static uint32_t compiled_capability(void) {
    return (uint32_t)EU4_SUBMISSION_COMPILED_CAPABILITY;
}

static _Atomic uint64_t *map_counters(void) {
    return (_Atomic uint64_t *)(control_map + COUNTER_REGION_OFFSET);
}

static void reset_armed_bank_counters(void) {
    for (uint32_t slot = 0; slot < EU4_SUBMISSION_COUNTER_COUNT; slot++) {
        if (eu4_submission_counter_slot_requires_armed((eu4_submission_counter_slot_t)slot)) {
            atomic_store_explicit(&map_counters()[slot], 0, memory_order_relaxed);
        }
    }
}

static void try_install_hooks(void) {
    if (hooks_installed) {
        return;
    }
#if defined(EU4_SUBMISSION_TRACK_INSTALL_ATTEMPTS)
    g_install_attempt_count++;
#endif
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    if (eu4_border_loop_head_install_fatal()) {
        abort();
    }
#endif
    if (eu4_submission_try_install_all_hooks()) {
        hooks_installed = true;
        return;
    }
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    if (eu4_border_loop_head_install_fatal()) {
        abort();
    }
#endif
    const char *test_stub = getenv("EU4_SUBMISSION_TEST_STUB_HOOK");
    if (test_stub && test_stub[0] == '1') {
        hooks_installed = true;
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
    if (control_map != MAP_FAILED) {
        pthread_mutex_lock(&header_lock);
        uint32_t *words = (uint32_t *)control_map;
        words[7] = compiled_capability();
        words[8] = EU4_SUBMISSION_COUNTER_SCHEMA_VERSION;
        words[9] = EU4_SUBMISSION_COUNTER_COUNT;
        words[10] = EU4_SUBMISSION_SUPPORTED_HYPOTHESIS_MASK_DEFAULT;
        words[11] = EU4_SUBMISSION_SAFETY_HYPOTHESIS_MASK_DEFAULT;
        pthread_mutex_unlock(&header_lock);
    }
}

static bool protocol_ok(const uint32_t *words) {
    return words[0] == MAGIC && words[1] == PROTOCOL_VERSION;
}

static bool candidate_mode_active(const uint32_t *words) {
    return words[5] == MODE_CANDIDATE && words[7] == compiled_capability();
}

static bool observation_bank_writable(const uint32_t *words) {
    if (!candidate_mode_active(words)) {
        return false;
    }
    if (observation_frozen) {
        return false;
    }
    if (words[15] != OBS_ACK_ARMED) {
        return false;
    }
    return true;
}

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED || slot >= EU4_SUBMISSION_COUNTER_COUNT) {
        return;
    }
    uint32_t *words = (uint32_t *)control_map;
    if (!protocol_ok(words)) {
        return;
    }
    if (eu4_submission_counter_slot_requires_armed(slot)) {
        if (!observation_bank_writable(words)) {
            return;
        }
    }
    atomic_fetch_add_explicit(&map_counters()[slot], delta, memory_order_relaxed);
}

void eu4_submission_record_candidate_site_entry(void) {
    eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_SITE_ENTRIES, 1);
}

void eu4_submission_record_renderbuckets_invocation(void) {
    eu4_submission_counter_add(EU4_COUNTER_RENDERBUCKETS_INVOCATIONS, 1);
}

void eu4_submission_record_eligible_pair_hit(void) {
    eu4_submission_counter_add(EU4_COUNTER_LEGACY_ELIGIBLE_PAIR_HITS, 1);
}

void eu4_submission_record_effective_action(void) {
    eu4_submission_counter_add(EU4_COUNTER_EFFECTIVE_ACTIONS, 1);
}

bool eu4_submission_candidate_predicate_active(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return false;
    }
    uint32_t *words = (uint32_t *)control_map;
    if (!protocol_ok(words)) {
        return false;
    }
    return candidate_mode_active(words) && observation_bank_writable(words);
}

bool eu4_submission_observation_bank_active(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return false;
    }
    uint32_t *words = (uint32_t *)control_map;
    return protocol_ok(words) && observation_bank_writable(words);
}

static void handle_observation_command(uint32_t *words) {
    uint32_t obs_gen = words[12];
    uint32_t obs_req = words[13];
    uint32_t obs_ack_gen = words[14];
    if (obs_gen <= obs_ack_gen) {
        return;
    }
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    if (obs_req == OBS_ARM) {
        atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
        reset_armed_bank_counters();
        eu4_submission_observation_arm_reset();
        observation_frozen = false;
        const bool activate = candidate_mode_active(words);
        words[15] = OBS_ACK_ARMED;
        atomic_store_explicit(&g_eu4_border_observer_active, activate ? 1u : 0u, memory_order_release);
        words[14] = obs_gen;
    } else if (obs_req == OBS_FREEZE) {
        atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
        if (observation_bank_writable(words)) {
            eu4_border_loop_head_capture_freeze_state();
        }
        observation_frozen = true;
        words[15] = OBS_ACK_FROZEN;
        words[14] = obs_gen;
    } else if (obs_req == OBS_DISARMED) {
        atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
        observation_frozen = true;
        words[15] = OBS_ACK_DISARMED;
        words[14] = obs_gen;
    }
#else
    if (obs_req == OBS_ARM) {
        reset_armed_bank_counters();
        eu4_submission_observation_arm_reset();
        observation_frozen = false;
        words[14] = obs_gen;
        words[15] = OBS_ACK_ARMED;
    } else if (obs_req == OBS_FREEZE) {
        observation_frozen = true;
        words[14] = obs_gen;
        words[15] = OBS_ACK_FROZEN;
    } else if (obs_req == OBS_DISARMED) {
        observation_frozen = true;
        words[14] = obs_gen;
        words[15] = OBS_ACK_DISARMED;
    }
#endif
}

static void poll_command_ack(void) {
    pthread_once(&setup_once, setup);
    if (control_map == MAP_FAILED) {
        return;
    }
    pthread_mutex_lock(&header_lock);
    uint32_t *words = (uint32_t *)control_map;
    if (!protocol_ok(words)) {
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
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
        if (requested_mode == MODE_REFERENCE) {
            atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
            eu4_submission_observation_arm_reset();
        }
#endif
        ack_mode = requested_mode;
        if (requested_mode == MODE_CANDIDATE) {
            if (requested_capability == compiled && advertised_capability == compiled) {
                try_install_hooks();
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
                atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
#endif
            }
            if (requested_capability != compiled || advertised_capability != compiled || !hooks_installed) {
                ack_mode = MODE_REFERENCE;
            }
        }
        ack_generation = command_generation;
        words[4] = ack_generation;
        words[5] = ack_mode;
    }
    handle_observation_command(words);
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    g_eu4_border_control_flags = *(uint32_t *)(control_map + BORDER_FLAGS_OFFSET);
#endif
    if (words[5] != last_observed_ack_mode) {
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
        if (words[5] == MODE_REFERENCE) {
            atomic_store_explicit(&g_eu4_border_observer_active, 0, memory_order_release);
            eu4_submission_observation_arm_reset();
        }
#else
        if (words[5] == MODE_REFERENCE) {
            eu4_submission_observation_arm_reset();
        }
#endif
        last_observed_ack_mode = words[5];
    }
    pthread_mutex_unlock(&header_lock);
    eu4_submission_counter_add(EU4_COUNTER_CONTROL_TICKS, 1);
}

static CGLError submission_flush(CGLContextObj context) {
    pthread_once(&setup_once, setup);
#if EU4_SUBMISSION_COMPILED_CAPABILITY != 2
    bool was_observing = false;
    if (control_map != MAP_FAILED) {
        uint32_t *words = (uint32_t *)control_map;
        if (protocol_ok(words)) {
            was_observing = observation_bank_writable(words);
        }
    }
#endif
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    eu4_border_flush_pending();
#endif
    CGLError result = CGLFlushDrawable(context);
#if EU4_SUBMISSION_COMPILED_CAPABILITY == 2
    if (control_map != MAP_FAILED) {
        uint32_t *words = (uint32_t *)control_map;
        if (protocol_ok(words) && candidate_mode_active(words)) {
            eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_SWAPS, 1);
        }
    }
#else
    if (was_observing) {
        eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_SWAPS, 1);
    }
#endif
    poll_command_ack();
    return result;
}

__attribute__((used, section("__DATA,__interpose")))
static const struct {
    const void *replacement, *replacee;
} interpose = {(const void *)submission_flush, (const void *)CGLFlushDrawable};
