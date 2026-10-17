/* Oracle P4 native recorder core: exact capture accounting.
 *
 * Function-agnostic: it knows nothing about the subject, only about hooks. It
 * counts exactly what it intercepted, what its policy selected for deep capture,
 * what it completed, what was left incomplete, and what was lost to the bounded
 * capture buffer. It never silently drops an event, and it detects reentrant
 * observation rather than assuming it away.
 *
 * This is the portable core the P4r-B harness links against. It performs no
 * hashing, relocation or deep comparison on the hot path - those are offline
 * (P4.5). It never launches EU IV, opens a game process or attaches to a binary.
 */
#ifndef EU4_ORACLE_RECORDER_NATIVE_RECORDER_H
#define EU4_ORACLE_RECORDER_NATIVE_RECORDER_H

#include <stdint.h>

#include "policy.h"

#define RECORDER_MAX_EVENTS 64u
#define RECORDER_MAX_ARGS 8u
#define RECORDER_MAX_DEPTH 32u

enum {
    RECORDER_MODE_DISABLED = 0, /* count interceptions only; select nothing */
    RECORDER_MODE_ENABLED = 1   /* select every interception for deep capture */
};

typedef struct {
    uint32_t mode;
    uint32_t capacity; /* capture slots; must be <= RECORDER_MAX_EVENTS */
    uint32_t max_args; /* argument slots a hook may record; <= RECORDER_MAX_ARGS */
    const recorder_policy *policy; /* NULL selects every hook for deep capture */
} recorder_config;

typedef struct {
    uint64_t intercepted; /* hook entries observed */
    uint64_t selected;    /* entries the policy selected for deep capture */
    uint64_t unselected;  /* entries counted only (== intercepted - selected) */
    uint64_t captured;    /* selected entries with a complete observation */
    uint64_t incomplete;  /* selected entries whose declared scope was not observable */
    uint64_t lost;        /* selected entries dropped by the bounded buffer */
    uint64_t reentrant;   /* entries observed while already inside an observation */
    uint64_t rejected;    /* instrumentation errors (over-deep, mismatched exit) */
} recorder_counters;

typedef struct {
    uint32_t hook_id;
    uint32_t nargs;
    uint64_t args[RECORDER_MAX_ARGS];
    uint64_t result;
    int complete;
} recorder_event;

/* Returns 0 on success, -1 on an invalid configuration. */
int recorder_init(const recorder_config *config);
void recorder_shutdown(void);
void recorder_reset(void);

/* Observe one hook entry. Returns a token for the matching `recorder_exit`; a
 * negative token means no frame was pushed (over-deep or uninitialised). */
int64_t recorder_enter(uint32_t hook_id, const uint64_t *args, uint32_t nargs);
/* End observation of `token`. `complete` is 0 when the observation's declared
 * scope was not fully observable, e.g. a call that operated on no live resource. */
void recorder_exit(int64_t token, uint64_t result, int complete);

const recorder_counters *recorder_counters_view(void);
const recorder_event *recorder_event_at(uint32_t index);
uint32_t recorder_event_count(void);

/* Holds when intercepted == selected + unselected and
 * selected == captured + incomplete + lost. */
int recorder_accounting_holds(const recorder_counters *counters);

#endif /* EU4_ORACLE_RECORDER_NATIVE_RECORDER_H */
