/* Implementation of the Oracle P4 native recorder core. See recorder.h. */
#include "recorder.h"

typedef struct {
    int active;
    int selected;
    int slot_ok;
    uint32_t hook_id;
    uint32_t nargs;
    uint64_t args[RECORDER_MAX_ARGS];
} recorder_frame;

static recorder_config g_config;
static recorder_counters g_counters;
static recorder_event g_events[RECORDER_MAX_EVENTS];
static uint32_t g_event_count;
static int g_initialised;

/* Observation depth is per thread, so one thread's nesting never corrupts
 * another's. The counters are plain fields here; P4.7 makes the cross-thread
 * aggregation atomic. */
static _Thread_local recorder_frame g_stack[RECORDER_MAX_DEPTH];
static _Thread_local uint32_t g_depth;

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
    return 0;
}

void recorder_shutdown(void)
{
    g_initialised = 0;
}

void recorder_reset(void)
{
    g_counters.intercepted = 0;
    g_counters.selected = 0;
    g_counters.unselected = 0;
    g_counters.captured = 0;
    g_counters.incomplete = 0;
    g_counters.lost = 0;
    g_counters.reentrant = 0;
    g_counters.rejected = 0;
    g_event_count = 0;
    g_depth = 0;
}

int64_t recorder_enter(uint32_t hook_id, const uint64_t *args, uint32_t nargs)
{
    recorder_frame *frame;
    uint32_t i;
    int64_t token;

    if (!g_initialised || g_depth >= RECORDER_MAX_DEPTH) {
        g_counters.rejected += 1;
        return -1;
    }

    g_counters.intercepted += 1;
    if (g_depth > 0) {
        g_counters.reentrant += 1;
    }

    frame = &g_stack[g_depth];
    frame->active = 1;
    frame->hook_id = hook_id;
    frame->nargs = nargs;
    frame->slot_ok = 0;
    for (i = 0; i < RECORDER_MAX_ARGS; i++) {
        frame->args[i] = (args != 0 && i < nargs) ? args[i] : 0u;
    }
    if (nargs > g_config.max_args || nargs > RECORDER_MAX_ARGS) {
        g_counters.rejected += 1; /* instrumentation captured more than the policy allows */
    }

    if (g_config.mode == RECORDER_MODE_DISABLED) {
        frame->selected = 0;
        g_counters.unselected += 1;
    } else if (policy_rule_for(g_config.policy, hook_id) == RECORDER_RULE_DEEP) {
        frame->selected = 1;
        g_counters.selected += 1;
        if (g_event_count < g_config.capacity) {
            frame->slot_ok = 1;
        } else {
            g_counters.lost += 1; /* bounded capture: reported, never silent */
        }
    } else {
        frame->selected = 0;
        g_counters.unselected += 1;
    }

    token = (int64_t)g_depth;
    g_depth += 1;
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
        g_counters.rejected += 1; /* an exit with no matching enter */
        return;
    }

    index = g_depth - 1;
    if (token != (int64_t)index) {
        g_counters.rejected += 1;
    }
    frame = &g_stack[index];
    g_depth = index;

    if (!frame->active) {
        return;
    }
    frame->active = 0;
    if (!frame->selected) {
        return; /* already accounted as unselected at entry */
    }
    if (!frame->slot_ok) {
        return; /* already accounted as lost at entry */
    }

    {
        recorder_event *event = &g_events[g_event_count];
        uint32_t i;
        event->hook_id = frame->hook_id;
        event->nargs = frame->nargs;
        for (i = 0; i < RECORDER_MAX_ARGS; i++) {
            event->args[i] = frame->args[i];
        }
        event->result = result;
        event->complete = complete ? 1 : 0;
        g_event_count += 1;
    }
    if (complete) {
        g_counters.captured += 1;
    } else {
        g_counters.incomplete += 1;
    }
}

const recorder_counters *recorder_counters_view(void)
{
    return &g_counters;
}

const recorder_event *recorder_event_at(uint32_t index)
{
    if (index >= g_event_count) {
        return 0;
    }
    return &g_events[index];
}

uint32_t recorder_event_count(void)
{
    return g_event_count;
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
