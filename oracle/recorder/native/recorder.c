/* Implementation of the Oracle P4 native recorder core. See recorder.h. */
#include <stdatomic.h>
#include <string.h>

#include "recorder.h"

typedef struct {
    int active;
    int selected;
    int slot_ok;
    uint32_t slot;
    uint32_t thread_id;
    uint64_t sequence;
    uint32_t phase;
    uint32_t hook_id;
    uint32_t nargs;
    uint64_t args[RECORDER_MAX_ARGS];
    /* The instrumentation fault detected at entry, if any: resolved against the
     * faults the exit side finds, so one malformed observation is counted once. */
    uint32_t fault;
} recorder_frame;

static recorder_config g_config;
static int g_initialised;

static _Atomic uint64_t g_intercepted;
static _Atomic uint64_t g_selected;
static _Atomic uint64_t g_unselected;
static _Atomic uint64_t g_captured;
static _Atomic uint64_t g_incomplete;
static _Atomic uint64_t g_lost;
static _Atomic uint64_t g_reentrant;
static _Atomic uint64_t g_rejected;

static _Atomic uint32_t g_event_slot;
static recorder_event g_events[RECORDER_MAX_EVENTS];

/* The content arena, separate from the event buffer so the two budgets and the
 * two loss accounts never conflate. `g_content_next` is a monotone high-water mark
 * handed out by compare-and-swap, so a failed span reserves nothing and no
 * capacity is wasted. */
static uint8_t g_content[RECORDER_MAX_CONTENT_BYTES];
static _Atomic uint64_t g_content_next;
static _Atomic uint64_t g_content_declared;
static _Atomic uint64_t g_content_captured;
static _Atomic uint64_t g_content_lost;
static _Atomic uint64_t g_content_spans_lost;

/* Observation state is per thread, so one thread's nesting and sequence never
 * corrupt another's. The counters above are the single shared surface. */
static _Thread_local recorder_frame g_stack[RECORDER_MAX_DEPTH];
static _Thread_local uint32_t g_depth;
static _Thread_local uint32_t tls_thread_id;
static _Thread_local uint64_t tls_sequence;
static _Thread_local uint32_t tls_phase;

static void bump(_Atomic uint64_t *counter)
{
    atomic_fetch_add_explicit(counter, 1u, memory_order_relaxed);
}

/* Reserve `length` bytes from the content arena and return their offset, or -1
 * when the span does not fit - in which case nothing is reserved. The high-water
 * mark only ever advances towards the configured capacity, so `next + length`
 * cannot overflow: next <= RECORDER_MAX_CONTENT_BYTES (2^10) and length is 32-bit.
 * That is what makes the bound a proof rather than a hope. */
static int64_t content_reserve(uint32_t length)
{
    uint64_t next = atomic_load_explicit(&g_content_next, memory_order_relaxed);
    for (;;) {
        uint64_t end = next + (uint64_t)length;
        if (end > (uint64_t)g_config.content_capacity) {
            return -1;
        }
        if (atomic_compare_exchange_weak_explicit(&g_content_next, &next, end,
                                                  memory_order_acq_rel,
                                                  memory_order_relaxed)) {
            return (int64_t)next;
        }
    }
}

int recorder_init(const recorder_config *config)
{
    if (config == 0) {
        return -1;
    }
    if (config->capacity > RECORDER_MAX_EVENTS) {
        return -1;
    }
    if (config->max_args > RECORDER_MAX_ARGS) {
        return -1;
    }
    if (config->content_capacity > RECORDER_MAX_CONTENT_BYTES) {
        return -1;
    }
    g_config = *config;
    g_initialised = 1;
    recorder_reset();
    recorder_thread_begin(0u);
    return 0;
}

void recorder_shutdown(void)
{
    g_initialised = 0;
}

void recorder_reset(void)
{
    atomic_store_explicit(&g_intercepted, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_selected, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_unselected, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_captured, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_incomplete, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_lost, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_reentrant, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_rejected, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_event_slot, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_content_next, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_content_declared, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_content_captured, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_content_lost, 0u, memory_order_relaxed);
    atomic_store_explicit(&g_content_spans_lost, 0u, memory_order_relaxed);
    g_depth = 0u;
}

void recorder_thread_begin(uint32_t thread_id)
{
    tls_thread_id = thread_id;
    tls_sequence = 0u;
    tls_phase = 0u;
}

void recorder_set_phase(uint32_t phase)
{
    tls_phase = phase;
}

int64_t recorder_enter(uint32_t hook_id, const uint64_t *args, uint32_t nargs)
{
    recorder_frame *frame;
    uint32_t i;
    int64_t token;

    if (!g_initialised || g_depth >= RECORDER_MAX_DEPTH) {
        bump(&g_rejected);
        return -1;
    }

    bump(&g_intercepted);
    if (g_depth > 0) {
        bump(&g_reentrant);
    }
    tls_sequence += 1u;

    frame = &g_stack[g_depth];
    frame->active = 1;
    frame->hook_id = hook_id;
    frame->nargs = nargs;
    frame->slot_ok = 0;
    frame->slot = 0u;
    frame->thread_id = tls_thread_id;
    frame->sequence = tls_sequence;
    frame->phase = tls_phase;
    frame->fault = RECORDER_FAULT_NONE;
    if (nargs > g_config.max_args || nargs > RECORDER_MAX_ARGS
        || (nargs > 0u && args == 0)) {
        /* A declaration is judged before any of its carriers are read: the declared
         * count says nothing about how many the array actually holds, so a malformed
         * one is never dereferenced - reading an undersized array would be the very
         * memory error the fault exists to record. The diagnostic slots stay zero;
         * the exit side resolves and counts the single fault. */
        frame->fault = RECORDER_FAULT_ARGS;
        for (i = 0; i < RECORDER_MAX_ARGS; i++) {
            frame->args[i] = 0u;
        }
    } else {
        for (i = 0; i < nargs; i++) {
            frame->args[i] = args[i];
        }
        for (; i < RECORDER_MAX_ARGS; i++) {
            frame->args[i] = 0u;
        }
    }

    if (g_config.mode == RECORDER_MODE_DISABLED) {
        frame->selected = 0;
        bump(&g_unselected);
    } else if (policy_rule_for(g_config.policy, hook_id) == RECORDER_RULE_DEEP) {
        uint32_t slot;
        frame->selected = 1;
        bump(&g_selected);
        slot = atomic_fetch_add_explicit(&g_event_slot, 1u, memory_order_relaxed);
        if (slot < g_config.capacity) {
            frame->slot = slot;
            frame->slot_ok = 1;
        } else {
            bump(&g_lost); /* bounded capture: reported, never silent */
        }
    } else {
        frame->selected = 0;
        bump(&g_unselected);
    }

    token = (int64_t)g_depth;
    g_depth += 1u;
    return token;
}

void recorder_exit(int64_t token, uint64_t result, int complete,
                   const uint64_t *context, uint32_t ncontext)
{
    /* No content, so no origin: an observation that declares no content need not
     * invent one. */
    recorder_exit_content(token, result, complete, context, ncontext, 0, 0u,
                          RECORDER_POINT_EXIT, 0, 0u);
}

void recorder_exit_content(int64_t token, uint64_t result, int complete,
                           const uint64_t *context, uint32_t ncontext,
                           const recorder_span *spans, uint32_t nspans,
                           uint32_t point,
                           const uint64_t *origin, uint32_t norigin)
{
    recorder_frame *frame;
    uint32_t index;
    uint32_t fault;

    if (!g_initialised) {
        return;
    }
    if (g_depth == 0) {
        bump(&g_rejected); /* an exit with no matching enter */
        return;
    }

    index = g_depth - 1u;
    if (token != (int64_t)index) {
        bump(&g_rejected);
    }
    frame = &g_stack[index];
    g_depth = index;

    if (!frame->active) {
        return;
    }
    frame->active = 0;

    /* One malformed declaration is one instrumentation fault, however many symptoms
     * it shows: the first cause in RECORDER_FAULT_* order is the one named. A fault
     * is resolved here, before the policy's selection is consulted, so a malformed
     * declaration is counted whether or not the policy chose to observe it. */
    fault = frame->fault;
    if (fault == RECORDER_FAULT_NONE && ncontext > 0u
        && (context == 0 || ncontext > RECORDER_MAX_CONTEXT)) {
        /* Declared context words with no array are as malformed as too many: the
         * unavailable context is never read as absent-but-fine. */
        fault = RECORDER_FAULT_CONTEXT;
    }
    if (fault == RECORDER_FAULT_NONE && point != RECORDER_POINT_ENTRY
        && point != RECORDER_POINT_EXIT) {
        fault = RECORDER_FAULT_POINT;
    }
    if (fault == RECORDER_FAULT_NONE && nspans > RECORDER_MAX_SPANS) {
        fault = RECORDER_FAULT_SPANS;
    }
    if (fault == RECORDER_FAULT_NONE && nspans > 0u && spans == 0) {
        fault = RECORDER_FAULT_SPANS;
    }

    if (!frame->selected || !frame->slot_ok) {
        /* already accounted as unselected or lost at entry; its content is not
         * declared, counted or copied - the policy chose not to observe it */
        if (fault != RECORDER_FAULT_NONE) {
            bump(&g_rejected); /* malformed instrumentation is counted even if unselected */
        }
        return;
    }

    {
        recorder_event *event = &g_events[frame->slot];
        uint64_t declared = 0u;
        uint64_t captured = 0u;
        uint64_t lost = 0u;
        uint64_t spans_lost = 0u;
        uint32_t stored = 0u;
        uint32_t spans_stored = 0u;
        uint32_t origin_stored = 0u;
        uint32_t i;
        uint32_t s;

        event->thread_id = frame->thread_id;
        event->sequence = frame->sequence;
        event->phase = frame->phase;
        event->hook_id = frame->hook_id;
        event->nargs = frame->nargs;
        for (i = 0; i < RECORDER_MAX_ARGS; i++) {
            event->args[i] = frame->args[i];
        }
        if (context != 0 && ncontext <= RECORDER_MAX_CONTEXT) {
            stored = ncontext;
            for (i = 0; i < stored; i++) {
                event->context[i] = context[i];
            }
        }
        /* An over-long context is already a fault above: it is stored as nothing
         * rather than truncated, so no consumer can mistake a prefix for the whole. */
        event->ncontext = stored;
        for (i = stored; i < RECORDER_MAX_CONTEXT; i++) {
            event->context[i] = 0u;
        }
        event->result = result;
        event->point = point;
        event->fault = fault;
        event->nspans = 0u;
        event->norigin = 0u;
        for (i = 0; i < RECORDER_MAX_SPANS; i++) {
            event->span_offset[i] = 0u;
            event->span_length[i] = 0u;
            event->span_lost[i] = 0u;
        }
        for (i = 0; i < RECORDER_MAX_CONTEXT; i++) {
            event->origin[i] = 0u;
        }

        /* Content capture off (content_capacity == 0) means undeclared, not lost: a
         * hook may name spans, and the recorder counts and copies none of them. This
         * is what keeps the default behaviour - and every existing golden - exact. */
        if (g_config.content_capacity > 0u && fault == RECORDER_FAULT_NONE) {
            uint32_t limit = nspans < RECORDER_MAX_SPANS ? nspans : RECORDER_MAX_SPANS;
            const uint64_t *from = 0;
            uint32_t nfrom = 0u;

            /* What did the hook declare, and is every declared byte addressable? A
             * span list is read only after it was found to be well formed. */
            for (s = 0; s < limit; s++) {
                uint32_t length = spans[s].length;
                if (length == 0u) {
                    continue; /* nothing declared: never a loss */
                }
                if (spans[s].bytes == 0) {
                    fault = RECORDER_FAULT_BYTES; /* bytes promised, none addressable */
                    break;
                }
                declared += (uint64_t)length;
            }
            /* A captured blob must be attributable. An explicit origin wins; a hook
             * that captured the state its own context already names need not repeat
             * it. The declared count is authoritative, so no explicit origin means
             * `norigin == 0` whatever the array pointer is. Content declared with no
             * usable origin is void, never captured. */
            if (fault == RECORDER_FAULT_NONE && declared > 0u) {
                if (norigin > RECORDER_MAX_CONTEXT) {
                    fault = RECORDER_FAULT_ORIGIN; /* more origin than is recordable */
                } else if (norigin > 0u) {
                    if (origin == 0) {
                        fault = RECORDER_FAULT_ORIGIN; /* origin words, no array */
                    } else {
                        from = origin;
                        nfrom = norigin;
                    }
                } else if (stored > 0u) {
                    from = event->context; /* no explicit origin: the context names it */
                    nfrom = stored;
                } else {
                    fault = RECORDER_FAULT_ORIGIN; /* content no provenance can name */
                }
            }
            if (fault == RECORDER_FAULT_NONE) {
                if (nfrom > 0u) {
                    origin_stored = nfrom;
                    for (i = 0; i < nfrom; i++) {
                        event->origin[i] = from[i];
                    }
                }
                for (s = 0; s < limit; s++) {
                    uint32_t length = spans[s].length;
                    int64_t at;
                    if (length == 0u) {
                        continue;
                    }
                    at = content_reserve(length);
                    if (at < 0) {
                        /* whole-span loss, never a partial capture */
                        lost += (uint64_t)length;
                        spans_lost += 1u;
                        event->span_lost[spans_stored] = length;
                    } else {
                        memcpy(&g_content[(uint32_t)at], spans[s].bytes, length);
                        captured += (uint64_t)length;
                        event->span_offset[spans_stored] = (uint32_t)at;
                        event->span_length[spans_stored] = length;
                    }
                    spans_stored += 1u;
                }
                /* A required span that was not captured means the observation's declared
                 * scope was not observable: it is incomplete, whatever the call returned. */
                if (lost > 0u) {
                    complete = 0;
                }
            }
        }
        if (fault != RECORDER_FAULT_NONE) {
            /* A malformed declaration declares no content at all: nothing is counted
             * and nothing is copied, so no consumer can mistake a prefix of a
             * malformed declaration for the whole, and the fault is counted once
             * however many symptoms it showed. */
            declared = 0u;
            captured = 0u;
            lost = 0u;
            spans_lost = 0u;
            spans_stored = 0u;
            origin_stored = 0u;
            complete = 0;
            event->fault = fault;
            bump(&g_rejected);
        }
        if (g_config.content_capacity > 0u) {
            atomic_fetch_add_explicit(&g_content_declared, declared, memory_order_relaxed);
            atomic_fetch_add_explicit(&g_content_captured, captured, memory_order_relaxed);
            atomic_fetch_add_explicit(&g_content_lost, lost, memory_order_relaxed);
            atomic_fetch_add_explicit(&g_content_spans_lost, spans_lost, memory_order_relaxed);
        }
        event->nspans = spans_stored;
        event->norigin = origin_stored;
        event->complete = complete ? 1 : 0;
    }
    if (complete) {
        bump(&g_captured);
    } else {
        bump(&g_incomplete);
    }
}

void recorder_snapshot(recorder_counters *out)
{
    if (out == 0) {
        return;
    }
    out->intercepted = atomic_load_explicit(&g_intercepted, memory_order_relaxed);
    out->selected = atomic_load_explicit(&g_selected, memory_order_relaxed);
    out->unselected = atomic_load_explicit(&g_unselected, memory_order_relaxed);
    out->captured = atomic_load_explicit(&g_captured, memory_order_relaxed);
    out->incomplete = atomic_load_explicit(&g_incomplete, memory_order_relaxed);
    out->lost = atomic_load_explicit(&g_lost, memory_order_relaxed);
    out->reentrant = atomic_load_explicit(&g_reentrant, memory_order_relaxed);
    out->rejected = atomic_load_explicit(&g_rejected, memory_order_relaxed);
    out->content_bytes_declared = atomic_load_explicit(&g_content_declared, memory_order_relaxed);
    out->content_bytes_captured = atomic_load_explicit(&g_content_captured, memory_order_relaxed);
    out->content_bytes_lost = atomic_load_explicit(&g_content_lost, memory_order_relaxed);
    out->content_spans_lost = atomic_load_explicit(&g_content_spans_lost, memory_order_relaxed);
}

const recorder_event *recorder_event_at(uint32_t index)
{
    if (index >= recorder_event_count()) {
        return 0;
    }
    return &g_events[index];
}

const uint8_t *recorder_content_at(uint32_t offset, uint32_t length)
{
    if ((uint64_t)offset + (uint64_t)length > (uint64_t)g_config.content_capacity) {
        return 0;
    }
    return &g_content[offset];
}

uint32_t recorder_event_count(void)
{
    uint32_t count = atomic_load_explicit(&g_event_slot, memory_order_relaxed);
    return count < g_config.capacity ? count : g_config.capacity;
}

int recorder_accounting_holds(const recorder_counters *counters)
{
    if (counters == 0) {
        return 0;
    }
    if (counters->intercepted != counters->selected + counters->unselected) {
        return 0;
    }
    if (counters->selected != counters->captured + counters->incomplete + counters->lost) {
        return 0;
    }
    if (counters->content_bytes_declared
        != counters->content_bytes_captured + counters->content_bytes_lost) {
        return 0;
    }
    return 1;
}
