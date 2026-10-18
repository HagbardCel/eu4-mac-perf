/* Oracle P4 native recorder core: exact, thread-safe capture accounting.
 *
 * Function-agnostic: it knows nothing about the subject, only about hooks. It
 * counts exactly what it intercepted, what its policy selected for deep capture,
 * what it completed, what was left incomplete, and what was lost to the bounded
 * capture buffer. It never silently drops an event, and it detects reentrant
 * observation rather than assuming it away.
 *
 * It also captures declared *content*: byte spans a hook names as the observation's
 * resource contents. Content is bounded by its own arena, separate from the event
 * buffer and off by default (`content_capacity == 0`), so the two budgets and the
 * two loss accounts never conflate. Content is whole-span or nothing - a span that
 * does not fit is lost in full and the observation is downgraded to incomplete,
 * never partially captured. Byte and event accounting are independent identities.
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
#define RECORDER_MAX_CONTENT_BYTES 1024u
#define RECORDER_MAX_SPANS 4u

enum {
    RECORDER_MODE_DISABLED = 0, /* count interceptions only; select nothing */
    RECORDER_MODE_ENABLED = 1   /* select every interception for deep capture */
};

/* When an observation's declared content was captured, relative to the call. The
 * recorder stores this verbatim; it is the hook layer's statement of whether the
 * bytes are the state the call saw on entry or the state it left behind. */
enum {
    RECORDER_POINT_EXIT = 0, /* after the call returned (the default) */
    RECORDER_POINT_ENTRY = 1 /* as the call was entered */
};

/* One declared content span. `length == 0` declares nothing. `bytes` is only read
 * while `length > 0` and only when the observation is selected for deep capture;
 * the core copies it into its own arena and never retains the pointer. */
typedef struct {
    const void *bytes;
    uint32_t length;
} recorder_span;

typedef struct {
    uint32_t mode;
    uint32_t capacity; /* capture slots; must be <= RECORDER_MAX_EVENTS */
    uint32_t max_args; /* argument slots a hook may record; <= RECORDER_MAX_ARGS */
    uint32_t content_capacity; /* content arena bytes; 0 disables content capture;
                                * must be <= RECORDER_MAX_CONTENT_BYTES */
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
    /* Content bytes, scoped to selected entries that earned an event slot. A span
     * is captured whole or lost whole; a single incomplete event may account for
     * several lost spans. declared == captured + lost. */
    uint64_t content_bytes_declared;
    uint64_t content_bytes_captured;
    uint64_t content_bytes_lost;
    uint64_t content_spans_lost;
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
    /* Declared content, in declaration order. For span `i` either
     * `span_length[i] > 0` and the bytes live in the arena at `span_offset[i]`, or
     * `span_lost[i] > 0` and `length` bytes were declared but not captured. */
    uint32_t point;  /* RECORDER_POINT_* */
    uint32_t nspans; /* non-empty declared spans, captured or lost */
    uint32_t span_offset[RECORDER_MAX_SPANS];
    uint32_t span_length[RECORDER_MAX_SPANS];
    uint32_t span_lost[RECORDER_MAX_SPANS];
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
 * addressed; the core stores them verbatim and never interprets them. Declares no
 * content and assumes the post-call point. */
void recorder_exit(int64_t token, uint64_t result, int complete,
                   const uint64_t *context, uint32_t ncontext);

/* As `recorder_exit`, but also declares the observation's content: up to
 * RECORDER_MAX_SPANS byte spans, copied into the recorder's own arena at the
 * moment of exit. Copies happen only when the observation was selected for deep
 * capture and earned an event slot; otherwise the spans are neither counted nor
 * copied. A span of `length == 0` declares nothing. A span that does not fit is
 * lost in full, never partially captured, and downgrades the observation from
 * complete to incomplete. `point` is a RECORDER_POINT_* value.
 *
 * `spans`/`bytes` need only be valid for the duration of this call. The caller
 * states a point in its own lifetime at which those bytes are valid: this is the
 * trusted hook layer's contract, and it does not make copying arbitrary process
 * pointers safe. */
void recorder_exit_content(int64_t token, uint64_t result, int complete,
                           const uint64_t *context, uint32_t ncontext,
                           const recorder_span *spans, uint32_t nspans,
                           uint32_t point);

/* Copy the counters out atomically. Call only once all observing threads have
 * stopped. */
void recorder_snapshot(recorder_counters *out);
const recorder_event *recorder_event_at(uint32_t index);
uint32_t recorder_event_count(void);

/* The bytes of a captured content span: the arena at `offset`, valid for `length`
 * bytes, or NULL when the range is outside the configured arena. */
const uint8_t *recorder_content_at(uint32_t offset, uint32_t length);

/* Holds when intercepted == selected + unselected,
 * selected == captured + incomplete + lost, and
 * content_bytes_declared == content_bytes_captured + content_bytes_lost. */
int recorder_accounting_holds(const recorder_counters *counters);

#endif /* EU4_ORACLE_RECORDER_NATIVE_RECORDER_H */
