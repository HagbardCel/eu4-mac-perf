/* Implementation of the Oracle P4 native recorder core. See recorder.h. */
#include <stdatomic.h>

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
    for (i = 0; i < RECORDER_MAX_ARGS; i++) {
        frame->args[i] = (args != 0 && i < nargs) ? args[i] : 0u;
    }
    if (nargs > g_config.max_args || nargs > RECORDER_MAX_ARGS) {
        bump(&g_rejected); /* instrumentation captured more than the policy allows */
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

void recorder_exit(int64_t token, uint64_t result, int complete)
{
    recorder_frame *frame;
    uint32_t index;

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
    if (!frame->selected || !frame->slot_ok) {
        return; /* already accounted as unselected or lost at entry */
    }

    {
        recorder_event *event = &g_events[frame->slot];
        uint32_t i;
        event->thread_id = frame->thread_id;
        event->sequence = frame->sequence;
        event->phase = frame->phase;
        event->hook_id = frame->hook_id;
        event->nargs = frame->nargs;
        for (i = 0; i < RECORDER_MAX_ARGS; i++) {
            event->args[i] = frame->args[i];
        }
        event->result = result;
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
}

const recorder_event *recorder_event_at(uint32_t index)
{
    if (index >= recorder_event_count()) {
        return 0;
    }
    return &g_events[index];
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
    return 1;
}
