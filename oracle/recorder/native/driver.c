/* Scenario driver for the Oracle P4 native recorder core.
 *
 * Runs fixed subject scripts through the recorder's hook layer - single-threaded
 * accounting and policy scenarios, then concurrent observation across four
 * threads with an explicit rendezvous - and prints a canonical transcript.
 *
 * The transcript is the "golden output" of P4.3-P4.7: fixed-width unsigned
 * arithmetic, no pointers, no clock, no locale. Concurrency does not make it
 * nondeterministic because it prints *accounting* and per-thread (thread,
 * sequence, phase) event records, never the interleaving itself.
 *
 * The driver is the trusted host: it owns the hook layer (the stand-in for real
 * interposition, which the synthetic target cannot prove), the subject's effect
 * sink, and the declared sync edge. It never launches EU IV.
 */
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>

#include "recorder.h"
#include "synth.h"

enum {
    HOOK_CREATE = 1,
    HOOK_UPDATE = 2,
    HOOK_READ = 3,
    HOOK_SHARE = 4,
    HOOK_DESTROY = 5
};

#define CONCURRENT_THREADS 4u

/* Two declared policies that qualify the same behavior differently. Rules are
 * canonicalized by hook id, so `POLICY_OBSERVE_DATA`'s declaration order (read
 * before update) does not change its digest - the canonical bytes list update
 * (hook 2) before read (hook 3). */
static const recorder_policy POLICY_OBSERVE_DATA = {
    1u, RECORDER_RULE_COUNT_ONLY, 2u,
    { {HOOK_READ, RECORDER_RULE_DEEP}, {HOOK_UPDATE, RECORDER_RULE_DEEP} }
};

static const recorder_policy POLICY_OBSERVE_FLOW = {
    1u, RECORDER_RULE_DEEP, 2u,
    { {HOOK_CREATE, RECORDER_RULE_COUNT_ONLY}, {HOOK_DESTROY, RECORDER_RULE_COUNT_ONLY} }
};

/* The subject's effect sink. The recorder will use the ordered effect stream as
 * part of each capture's context; here it only confirms the subject ran. It is
 * atomic because concurrent scenarios share it. */
static _Atomic uint64_t g_effects;

void synth_effect(uint32_t op, uint32_t a, uint32_t b)
{
    (void)op;
    (void)a;
    (void)b;
    atomic_fetch_add_explicit(&g_effects, 1u, memory_order_relaxed);
}

/* Hook layer: enter, call the subject, exit. `complete` is 0 when the call
 * operated on no live resource, so the observation's declared scope was not
 * observable. */
static int hook_create(synth_world *w, uint32_t size, synth_handle *out)
{
    uint64_t args[1];
    int64_t token;
    int rc;
    args[0] = size;
    token = recorder_enter(HOOK_CREATE, args, 1);
    rc = synth_create(w, size, out);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK);
    return rc;
}

static int hook_update(synth_world *w, synth_handle handle, uint32_t seed, uint32_t span)
{
    uint64_t args[3];
    int64_t token;
    int rc;
    args[0] = handle.slot;
    args[1] = seed;
    args[2] = span;
    token = recorder_enter(HOOK_UPDATE, args, 3);
    rc = synth_update(w, handle, seed, span);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK);
    return rc;
}

static int hook_read(const synth_world *w, synth_handle handle, uint32_t offset, uint32_t *out)
{
    uint64_t args[2];
    int64_t token;
    int rc;
    args[0] = handle.slot;
    args[1] = offset;
    token = recorder_enter(HOOK_READ, args, 2);
    rc = synth_read(w, handle, offset, out);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK);
    return rc;
}

static int hook_share(const synth_world *w, synth_handle handle, synth_handle *out)
{
    uint64_t args[1];
    int64_t token;
    int rc;
    args[0] = handle.slot;
    token = recorder_enter(HOOK_SHARE, args, 1);
    rc = synth_share(w, handle, out);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK);
    return rc;
}

static int hook_destroy(synth_world *w, synth_handle handle)
{
    uint64_t args[1];
    int64_t token;
    int rc;
    args[0] = handle.slot;
    token = recorder_enter(HOOK_DESTROY, args, 1);
    rc = synth_destroy(w, handle);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK);
    return rc;
}

/* Ten hook calls: two are incomplete - a stale read and a create that failed, so
 * neither has a live resource whose state could be captured. */
static void run_script(void)
{
    synth_world world;
    synth_handle handle = {0u, 0u};
    synth_handle alias = {0u, 0u};
    uint32_t value = 0;

    synth_world_init(&world);
    (void)hook_create(&world, 4, &handle);
    (void)hook_update(&world, handle, 5, 4);
    (void)hook_read(&world, handle, 0, &value);
    (void)hook_share(&world, handle, &alias);
    (void)hook_update(&world, alias, 1, 2);
    (void)hook_destroy(&world, handle);
    (void)hook_read(&world, handle, 0, &value); /* stale -> incomplete */
    (void)hook_create(&world, 0, &handle);      /* bad arg -> incomplete */
    (void)hook_create(&world, 4, &handle);      /* ok (slot reuse) */
    (void)hook_update(&world, handle, 9, 4);
}

/* --- concurrent observation --------------------------------------------------- */

typedef struct {
    synth_world world;
    synth_handle handle;
    synth_handle alias;
    uint32_t value;
} subject_state;

/* A tiny portable rendezvous: macOS has no pthread_barrier_t. */
typedef struct {
    _Atomic uint32_t arrived;
    uint32_t expected;
} spin_barrier;

static void barrier_wait(spin_barrier *barrier)
{
    atomic_fetch_add_explicit(&barrier->arrived, 1u, memory_order_acq_rel);
    while (atomic_load_explicit(&barrier->arrived, memory_order_acquire) < barrier->expected) {
        /* spin: the rendezvous is short and the thread count is fixed */
    }
}

/* Phase 0: four calls, all on a live resource. */
static void subject_phase0(subject_state *s)
{
    synth_world_init(&s->world);
    s->value = 0;
    (void)hook_create(&s->world, 4, &s->handle);
    (void)hook_update(&s->world, s->handle, 5, 4);
    (void)hook_read(&s->world, s->handle, 0, &s->value);
    (void)hook_share(&s->world, s->handle, &s->alias);
}

/* Phase 1: four more calls, all on a live resource. */
static void subject_phase1(subject_state *s)
{
    (void)hook_update(&s->world, s->alias, 1, 2);
    (void)hook_read(&s->world, s->alias, 1, &s->value);
    (void)hook_update(&s->world, s->handle, 7, 4);
    (void)hook_destroy(&s->world, s->handle);
}

/* The same split, but two calls per thread operate on no live resource. */
static void subject_mixed_phase0(subject_state *s)
{
    synth_world_init(&s->world);
    s->value = 0;
    (void)hook_create(&s->world, 4, &s->handle);
    (void)hook_update(&s->world, s->handle, 5, 4);
    (void)hook_read(&s->world, s->handle, 0, &s->value);
    (void)hook_share(&s->world, s->handle, &s->alias);
    (void)hook_update(&s->world, s->alias, 1, 2);
}

static void subject_mixed_phase1(subject_state *s)
{
    (void)hook_destroy(&s->world, s->handle);
    (void)hook_read(&s->world, s->handle, 0, &s->value); /* stale -> incomplete */
    (void)hook_create(&s->world, 0, &s->handle);         /* bad arg -> incomplete */
    (void)hook_create(&s->world, 4, &s->handle);         /* ok */
    (void)hook_update(&s->world, s->handle, 9, 4);
}

typedef struct {
    uint32_t thread_id;
    int mixed;
    spin_barrier *barrier;
} worker_args;

static void *worker(void *arg)
{
    worker_args *wa = (worker_args *)arg;
    subject_state state;

    recorder_thread_begin(wa->thread_id);
    recorder_set_phase(0u);
    if (wa->mixed) {
        subject_mixed_phase0(&state);
    } else {
        subject_phase0(&state);
    }

    barrier_wait(wa->barrier); /* the one explicit sync edge */

    recorder_set_phase(1u);
    if (wa->mixed) {
        subject_mixed_phase1(&state);
    } else {
        subject_phase1(&state);
    }
    return 0;
}

static int configure(uint32_t mode, uint32_t capacity, const recorder_policy *policy)
{
    recorder_config config;
    config.mode = mode;
    config.capacity = capacity;
    config.max_args = 4;
    config.policy = policy;
    return recorder_init(&config);
}

static int run_concurrent(uint32_t capacity, int mixed)
{
    pthread_t threads[CONCURRENT_THREADS];
    worker_args args[CONCURRENT_THREADS];
    spin_barrier barrier;
    uint32_t i;

    if (configure(RECORDER_MODE_ENABLED, capacity, 0) != 0) {
        return -1;
    }
    atomic_store_explicit(&barrier.arrived, 0u, memory_order_relaxed);
    barrier.expected = CONCURRENT_THREADS;
    for (i = 0; i < CONCURRENT_THREADS; i++) {
        args[i].thread_id = i;
        args[i].mixed = mixed;
        args[i].barrier = &barrier;
        if (pthread_create(&threads[i], 0, worker, &args[i]) != 0) {
            return -1;
        }
    }
    for (i = 0; i < CONCURRENT_THREADS; i++) {
        (void)pthread_join(threads[i], 0);
    }
    return 0;
}

/* --- transcript --------------------------------------------------------------- */

static void print_counters(const char *name)
{
    recorder_counters counters;
    recorder_snapshot(&counters);
    printf("SCENARIO %s\n", name);
    printf("COUNTERS intercepted=%" PRIu64 " selected=%" PRIu64 " unselected=%" PRIu64
           " captured=%" PRIu64 " incomplete=%" PRIu64 " lost=%" PRIu64
           " reentrant=%" PRIu64 " rejected=%" PRIu64 "\n",
           counters.intercepted, counters.selected, counters.unselected, counters.captured,
           counters.incomplete, counters.lost, counters.reentrant, counters.rejected);
    printf("ACCOUNTING %s\n", recorder_accounting_holds(&counters) ? "ok" : "VIOLATED");
}

/* Print captured events in (thread, sequence) order. Slot assignment under
 * concurrency is arbitrary, so the buffer order is not printed - only an order
 * the events themselves define. */
static void print_events(void)
{
    uint32_t count = recorder_event_count();
    uint32_t order[RECORDER_MAX_EVENTS];
    uint32_t i;

    for (i = 0; i < count; i++) {
        order[i] = i;
    }
    for (i = 1; i < count; i++) {
        uint32_t key = order[i];
        const recorder_event *ke = recorder_event_at(key);
        uint32_t j = i;
        while (j > 0) {
            const recorder_event *prev = recorder_event_at(order[j - 1u]);
            int after = (prev->thread_id > ke->thread_id)
                        || (prev->thread_id == ke->thread_id && prev->sequence > ke->sequence);
            if (!after) {
                break;
            }
            order[j] = order[j - 1u];
            j -= 1u;
        }
        order[j] = key;
    }

    printf("EVENTS count=%u\n", (unsigned)count);
    for (i = 0; i < count; i++) {
        const recorder_event *e = recorder_event_at(order[i]);
        printf("EV thread=%u seq=%" PRIu64 " phase=%u hook=%u result=%" PRIu64
               " complete=%d\n",
               (unsigned)e->thread_id, e->sequence, (unsigned)e->phase,
               (unsigned)e->hook_id, e->result, e->complete);
    }
}

static void print_policy(const char *name, const recorder_policy *policy)
{
    uint8_t bytes[128];
    uint32_t n = policy_canonical(policy, bytes, (uint32_t)sizeof(bytes));
    uint32_t i;
    printf("POLICY %s version=%u default=%u rules=%u canonical=", name,
           (unsigned)policy->version, (unsigned)policy->default_rule,
           (unsigned)policy->rule_count);
    for (i = 0; i < n; i++) {
        printf("%02x", (unsigned)bytes[i]);
    }
    printf("\n");
}

int main(void)
{
    printf("RECORDER v1\n");
    print_policy("observe-data", &POLICY_OBSERVE_DATA);
    print_policy("observe-flow", &POLICY_OBSERVE_FLOW);

    /* Disabled: count interceptions only, select nothing. */
    if (configure(RECORDER_MODE_DISABLED, 16, 0) != 0) {
        return 2;
    }
    run_script();
    print_counters("disabled");

    /* Enabled with ample capacity and no policy: every interception is captured. */
    (void)configure(RECORDER_MODE_ENABLED, 16, 0);
    run_script();
    print_counters("enabled");

    /* Enabled with capacity below the number of selected events: loss is exact. */
    (void)configure(RECORDER_MODE_ENABLED, 4, 0);
    run_script();
    print_counters("bounded");

    /* A policy that deep-captures only the data operations. */
    (void)configure(RECORDER_MODE_ENABLED, 16, &POLICY_OBSERVE_DATA);
    run_script();
    print_counters("policy-observe-data");

    /* A different policy: deep-capture everything except the lifecycle calls. */
    (void)configure(RECORDER_MODE_ENABLED, 16, &POLICY_OBSERVE_FLOW);
    run_script();
    print_counters("policy-observe-flow");

    /* Reentrant observation: one hook's capture nests another's. */
    (void)configure(RECORDER_MODE_ENABLED, 16, 0);
    {
        uint64_t args[1];
        int64_t outer;
        int64_t inner;
        args[0] = 1;
        outer = recorder_enter(HOOK_UPDATE, args, 1);
        inner = recorder_enter(HOOK_READ, args, 1);
        recorder_exit(inner, 0u, 1);
        recorder_exit(outer, 0u, 1);
    }
    print_counters("reentrant");

    /* Misuse: an exit with no matching enter is rejected, never silently accepted. */
    (void)configure(RECORDER_MODE_ENABLED, 16, 0);
    recorder_exit(0, 0u, 1);
    print_counters("misuse");

    /* Four threads, one rendezvous, ample capacity: every event is captured. */
    if (run_concurrent(64u, 0) != 0) {
        return 3;
    }
    print_counters("concurrent-ample");
    printf("SYNC barrier=1 participants=0,1,2,3 phase=0->1\n");
    print_events();

    /* Four threads into a buffer smaller than the capture: loss is exact. */
    if (run_concurrent(12u, 0) != 0) {
        return 3;
    }
    print_counters("concurrent-bounded");

    /* Four threads, two incomplete captures per thread. */
    if (run_concurrent(64u, 1) != 0) {
        return 3;
    }
    print_counters("concurrent-mixed");
    print_events();

    printf("END effects=%" PRIu64 "\n", atomic_load_explicit(&g_effects, memory_order_relaxed));
    recorder_shutdown();
    return 0;
}
