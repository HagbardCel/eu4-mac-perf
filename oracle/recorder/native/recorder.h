/* Oracle P4 native recorder core: exact, thread-safe capture accounting.
 *
 * Function-agnostic: it knows nothing about the subject, only about hooks. It
 * counts exactly what it intercepted, what its policy selected for deep capture,
 * what it completed, what was left incomplete, and what was lost to the bounded
 * capture buffer. It never silently drops an event, and it detects reentrant
 * observation rather than assuming it away.
 *
 * It is safe under concurrent observation. Every counter is atomic, the bounded
 * buffer hands out slots with one atomic fetch-add, and each captured event
 * records the observing thread and that thread's own sequence number - so the
 * offline side can reconstruct a partial happens-before order instead of
 * inventing a total one. No lock is taken on the hot path.
 *
 * It performs no hashing, relocation or deep comparison on the hot path - those
 * are offline (P4.5). It never launches EU IV, opens a game process or attaches to
 * a binary.
 */
#ifndef EU4_ORACLE_RECORDER_NATIVE_RECORDER_H
#define EU4_ORACLE_RECORDER_NATIVE_RECORDER_H

#include <stdint.h>

#include "policy.h"

#define RECORDER_MAX_EVENTS 64u
#define RECORDER_MAX_ARGS 8u
#define RECORDER_MAX_DEPTH 32u
#define RECORDER_MAX_CONTEXT 8u

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
    uint32_t thread_id; /* the observing thread, as declared by the driver */
    uint64_t sequence;  /* that thread's own, contiguous sequence number */
    uint32_t phase;     /* declared capture phase, split by an explicit sync edge */
    uint32_t hook_id;
    uint32_t nargs;
    uint64_t args[RECORDER_MAX_ARGS];
    /* Opaque observation context, declared by the hook layer at exit. The core
     * never interprets it - it is how a subject-specific host states which resource
     * an observation addressed, without teaching the function-agnostic core what a
     * resource is. */
    uint32_t ncontext;
    uint64_t context[RECORDER_MAX_CONTEXT];
    uint64_t result;
    int complete;
} recorder_event;

/* Returns 0 on success, -1 on an invalid configuration. */
int recorder_init(const recorder_config *config);
void recorder_shutdown(void);
void recorder_reset(void);

/* Declare the calling thread's identity and reset its sequence. Threads that never
 * call this are thread 0. */
void recorder_thread_begin(uint32_t thread_id);
/* Declare the calling thread's current capture phase. */
void recorder_set_phase(uint32_t phase);

/* Observe one hook entry. Returns a token for the matching `recorder_exit`; a
 * negative token means no frame was pushed (over-deep or uninitialised). */
int64_t recorder_enter(uint32_t hook_id, const uint64_t *args, uint32_t nargs);
/* End observation of `token`. `complete` is 0 when the observation's declared
 * scope was not fully observable, e.g. a call that operated on no live resource.
 * `context`/`ncontext` are an opaque, hook-declared record of what the observation
 * addressed; the core stores them verbatim and never interprets them. */
void recorder_exit(int64_t token, uint64_t result, int complete,
                   const uint64_t *context, uint32_t ncontext);

/* Copy the counters out atomically. Call only once all observing threads have
 * stopped. */
void recorder_snapshot(recorder_counters *out);
const recorder_event *recorder_event_at(uint32_t index);
uint32_t recorder_event_count(void);

/* Holds when intercepted == selected + unselected and
 * selected == captured + incomplete + lost. */
int recorder_accounting_holds(const recorder_counters *counters);

#endif /* EU4_ORACLE_RECORDER_NATIVE_RECORDER_H */
