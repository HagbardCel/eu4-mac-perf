/* Integration export driver for the Oracle P4 native recorder core (P4.9).
 *
 * The accounting driver (native/driver.c) prints the released `RECORDER v1`
 * transcript, which carries counters and per-observation identity but no ABI
 * arguments and no resource contents. This driver prints the versioned
 * observation *export* the offline materializer consumes: for every captured
 * observation, the hook it addressed, its arguments, its opaque resource context,
 * the declared capture point, and its declared content - each span either captured
 * whole or reported by its exact lost byte count.
 *
 * The export is a *driver-level* contract. The released accounting transcript is
 * untouched by construction, because a different driver authors it: adding a field
 * to the recorder core cannot change bytes this driver does not print.
 *
 * The subject is still the portable P4.2 synthetic library, exercised through the
 * same context convention (synth_context.h). It never launches EU IV, opens a game
 * process or attaches to a binary.
 */
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "recorder.h"
#include "spin_barrier.h"
#include "synth.h"
#include "synth_context.h"

enum {
    HOOK_CREATE = 1,
    HOOK_UPDATE = 2,
    HOOK_READ = 3,
    HOOK_SHARE = 4,
    HOOK_DESTROY = 5,
    HOOK_PAIR = 6 /* one observation that declares two content spans */
};

#define CONCURRENT_THREADS 4u

/* Deep-capture the mutations only: the creates and reads stay counted-only, so the
 * content they declare is neither counted nor copied. That is how a policy choice
 * stays distinct from a budget loss. */
static const recorder_policy POLICY_UPDATES_ONLY = {
    1u, RECORDER_RULE_COUNT_ONLY, 1u,
    { {HOOK_UPDATE, RECORDER_RULE_DEEP} }
};

static _Atomic uint64_t g_effects;
static uint32_t g_content_capacity;

static int configure_content(uint32_t capacity, uint32_t content_capacity,
                             const recorder_policy *policy);

void synth_effect(uint32_t op, uint32_t a, uint32_t b)
{
    (void)op;
    (void)a;
    (void)b;
    atomic_fetch_add_explicit(&g_effects, 1u, memory_order_relaxed);
}

/* --- the hook layer --------------------------------------------------------- */

/* Declares the resource's initial contents, after creation. */
static int hook_create(synth_world *w, uint32_t size, synth_handle *out)
{
    uint64_t args[1];
    uint64_t context[6];
    recorder_span spans[1];
    uint32_t live = 0u;
    uint32_t generation = 0u;
    uint32_t version = 0u;
    synth_handle produced = {0u, 0u};
    int64_t token;
    int rc;

    args[0] = size;
    token = recorder_enter(HOOK_CREATE, args, 1);
    rc = synth_create(w, size, out);
    spans[0].bytes = 0;
    spans[0].length = 0u;
    if (rc == SYNTH_OK && out != 0) {
        produced = *out;
        slot_state(w, produced.slot, &live, &generation, &version); /* after creation */
        spans[0].bytes = w->slots[produced.slot].data;
        spans[0].length = w->slots[produced.slot].size;
    }
    recorder_exit_content(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK, context,
                          reference_context(rc == SYNTH_OK ? 1u : 0u, produced.slot,
                                            produced.generation, live, generation,
                                            version, context),
                          spans, 1u, RECORDER_POINT_EXIT);
    return rc;
}

/* Declares the mutated span, after the update. */
static int hook_update(synth_world *w, synth_handle handle, uint32_t seed, uint32_t span)
{
    uint64_t args[3];
    uint64_t context[6];
    recorder_span spans[1];
    uint32_t live = 0u;
    uint32_t generation = 0u;
    uint32_t version = 0u;
    int64_t token;
    int rc;

    slot_state(w, handle.slot, &live, &generation, &version); /* at entry */
    args[0] = handle.slot;
    args[1] = seed;
    args[2] = span;
    token = recorder_enter(HOOK_UPDATE, args, 3);
    rc = synth_update(w, handle, seed, span);
    spans[0].bytes = 0;
    spans[0].length = 0u;
    if (rc == SYNTH_OK) {
        spans[0].bytes = w->slots[handle.slot].data;
        spans[0].length = span;
    }
    recorder_exit_content(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK, context,
                          reference_context(1u, handle.slot, handle.generation, live,
                                            generation, version, context),
                          spans, 1u, RECORDER_POINT_EXIT);
    return rc;
}

/* Declares the single byte the read observed, as the call was entered. Reading a
 * byte out of the resource is the observation's declared scope. */
static int hook_read(const synth_world *w, synth_handle handle, uint32_t offset, uint32_t *out)
{
    uint64_t args[2];
    uint64_t context[6];
    recorder_span spans[1];
    uint32_t live = 0u;
    uint32_t generation = 0u;
    uint32_t version = 0u;
    uint8_t observed = 0u;
    int have = 0;
    int64_t token;
    int rc;

    slot_state(w, handle.slot, &live, &generation, &version); /* at entry */
    if (handle.slot < SYNTH_SLOTS && w->slots[handle.slot].live
        && w->slots[handle.slot].generation == handle.generation
        && offset < w->slots[handle.slot].size) {
        observed = w->slots[handle.slot].data[offset];
        have = 1;
    }
    args[0] = handle.slot;
    args[1] = offset;
    token = recorder_enter(HOOK_READ, args, 2);
    rc = synth_read(w, handle, offset, out);
    spans[0].bytes = have ? &observed : 0;
    spans[0].length = have ? 1u : 0u;
    recorder_exit_content(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK, context,
                          reference_context(1u, handle.slot, handle.generation, live,
                                            generation, version, context),
                          spans, 1u, RECORDER_POINT_ENTRY);
    return rc;
}

/* One observation that declares two spans: the resource's low and high halves. The
 * first can be captured while the second is lost, so partial evidence stays
 * inspectable and the observation is still incomplete. */
static int hook_pair(const synth_world *w, synth_handle handle)
{
    uint64_t args[1];
    uint64_t context[6];
    recorder_span spans[2];
    uint32_t live = 0u;
    uint32_t generation = 0u;
    uint32_t version = 0u;
    uint32_t value = 0u;
    uint32_t size;
    int64_t token;
    int rc;

    slot_state(w, handle.slot, &live, &generation, &version); /* at entry */
    args[0] = handle.slot;
    token = recorder_enter(HOOK_PAIR, args, 1);
    rc = synth_read(w, handle, 0u, &value);
    spans[0].bytes = 0;
    spans[0].length = 0u;
    spans[1].bytes = 0;
    spans[1].length = 0u;
    if (rc == SYNTH_OK) {
        size = w->slots[handle.slot].size;
        spans[0].bytes = &w->slots[handle.slot].data[0];
        spans[0].length = size >= 2u ? 2u : 0u;
        spans[1].bytes = &w->slots[handle.slot].data[2];
        spans[1].length = size > 2u ? size - 2u : 0u;
    }
    recorder_exit_content(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK, context,
                          reference_context(1u, handle.slot, handle.generation, live,
                                            generation, version, context),
                          spans, 2u, RECORDER_POINT_ENTRY);
    return rc;
}

/* Declares no content: a destroy has no post-state left to observe. */
static int hook_destroy(synth_world *w, synth_handle handle)
{
    uint64_t args[1];
    uint64_t context[6];
    uint32_t live = 0u;
    uint32_t generation = 0u;
    uint32_t version = 0u;
    int64_t token;
    int rc;

    slot_state(w, handle.slot, &live, &generation, &version); /* at entry */
    args[0] = handle.slot;
    token = recorder_enter(HOOK_DESTROY, args, 1);
    rc = synth_destroy(w, handle);
    recorder_exit(token, (uint64_t)(uint32_t)rc, rc == SYNTH_OK, context,
                  reference_context(1u, handle.slot, handle.generation, live, generation,
                                    version, context));
    return rc;
}

/* --- scripts ------------------------------------------------------------------ */

/* Eight observations over one resource, twelve declared content bytes:
 * create 4, update 4, read 1, read 1, update 2, then a failed create, a destroy
 * and a stale read that declare nothing. The two reads share a context and differ
 * only in their arguments - the case a translation table must keep distinct. */
static void run_script(void)
{
    synth_world world;
    synth_handle handle = {0u, 0u};
    uint32_t value = 0;

    synth_world_init(&world);
    (void)hook_create(&world, 4u, &handle);      /* 4 bytes  */
    (void)hook_update(&world, handle, 5u, 4u);   /* 4 bytes  */
    (void)hook_read(&world, handle, 0u, &value); /* 1 byte   */
    (void)hook_read(&world, handle, 3u, &value); /* 1 byte   */
    (void)hook_update(&world, handle, 1u, 2u);   /* 2 bytes  */
    (void)hook_create(&world, 0u, &handle);      /* bad arg -> incomplete */
    (void)hook_destroy(&world, handle);
    (void)hook_read(&world, handle, 0u, &value); /* stale -> incomplete */
}

/* Four declared bytes, then two spans of two: with a six-byte arena the first span
 * fits and the second is lost whole. */
static void run_pair_script(void)
{
    synth_world world;
    synth_handle handle = {0u, 0u};

    synth_world_init(&world);
    (void)hook_create(&world, 4u, &handle);
    (void)hook_pair(&world, handle);
}

/* An observation that declares a zero-length span twice: nothing is declared, so
 * nothing is counted and nothing can be lost. */
static void run_zero_script(void)
{
    synth_world world;
    synth_handle handle = {0u, 0u};
    recorder_span spans[2];
    uint32_t nothing = 0u;
    int64_t token;

    synth_world_init(&world);
    (void)hook_create(&world, 4u, &handle);
    spans[0].bytes = &nothing;
    spans[0].length = 0u;
    spans[1].bytes = 0;
    spans[1].length = 0u;
    token = recorder_enter(HOOK_READ, 0, 0u);
    recorder_exit_content(token, 0u, 1, 0, 0u, spans, 2u, RECORDER_POINT_ENTRY);
}

/* --- concurrent observation --------------------------------------------------- */

typedef struct {
    synth_world world;
    synth_handle handle;
    uint32_t value;
} subject_state;

static void subject_phase0(subject_state *s, uint32_t seed)
{
    synth_world_init(&s->world);
    s->value = 0u;
    (void)hook_create(&s->world, 4u, &s->handle);         /* 4 bytes */
    (void)hook_update(&s->world, s->handle, seed, 4u);    /* 4 bytes */
}

static void subject_phase1(subject_state *s)
{
    (void)hook_read(&s->world, s->handle, 0u, &s->value); /* 1 byte */
}

typedef struct {
    uint32_t thread_id;
    spin_barrier *barrier;
} worker_args;

static void *worker(void *arg)
{
    worker_args *wa = (worker_args *)arg;
    subject_state state;

    recorder_thread_begin(wa->thread_id);
    recorder_set_phase(0u);
    /* A distinct seed per thread: each thread's declared bytes differ, while the
     * accounting totals do not. */
    subject_phase0(&state, wa->thread_id + 1u);

    barrier_wait(wa->barrier); /* the one explicit sync edge */

    recorder_set_phase(1u);
    subject_phase1(&state);
    return 0;
}

static int run_concurrent(uint32_t capacity, uint32_t content_capacity)
{
    pthread_t threads[CONCURRENT_THREADS];
    worker_args args[CONCURRENT_THREADS];
    spin_barrier barrier;
    uint32_t i;

    if (configure_content(capacity, content_capacity, 0) != 0) {
        return -1;
    }
    atomic_store_explicit(&barrier.arrived, 0u, memory_order_relaxed);
    barrier.expected = CONCURRENT_THREADS;
    for (i = 0; i < CONCURRENT_THREADS; i++) {
        args[i].thread_id = i;
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

static void print_arena(void)
{
    uint32_t count = recorder_event_count();
    uint64_t used = 0;
    uint32_t intervals = 0u;
    uint32_t overlaps = 0u;
    uint32_t out_of_range = 0u;
    uint32_t i;
    uint32_t j;

    for (i = 0; i < count; i++) {
        const recorder_event *e = recorder_event_at(i);
        uint32_t s;
        for (s = 0; s < e->nspans; s++) {
            uint32_t a_len = e->span_length[s];
            uint32_t a_at = e->span_offset[s];
            if (a_len == 0u) {
                continue; /* declared and lost: no bytes in the arena */
            }
            intervals += 1u;
            used += (uint64_t)a_len;
            if (recorder_content_at(a_at, a_len) == 0) {
                out_of_range += 1u;
            }
            for (j = 0; j < count; j++) {
                const recorder_event *o = recorder_event_at(j);
                uint32_t t;
                for (t = 0; t < o->nspans; t++) {
                    uint32_t b_len = o->span_length[t];
                    uint32_t b_at = o->span_offset[t];
                    if (b_len == 0u) {
                        continue;
                    }
                    if (j == i && t == s) {
                        continue;
                    }
                    if ((uint64_t)a_at < (uint64_t)b_at + (uint64_t)b_len
                        && (uint64_t)b_at < (uint64_t)a_at + (uint64_t)a_len) {
                        overlaps += 1u;
                    }
                }
            }
        }
    }
    /* Every captured span is a distinct arena interval: `used` counts each once and
     * `overlaps` is a pairwise double count, so it is even when there is none. */
    printf("ARENA capacity=%u used=%" PRIu64 " intervals=%u overlaps=%u out_of_range=%u\n",
           (unsigned)g_content_capacity, used, (unsigned)intervals, (unsigned)overlaps,
           (unsigned)out_of_range);
}

/* Print captured observations in (thread, sequence) order. Slot assignment and
 * arena placement under concurrency are arbitrary, so neither is printed - only the
 * per-thread records the events themselves define. Arena placement is reported
 * structurally by ARENA instead. */
static void print_observations(void)
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

    printf("OBSERVATIONS count=%u\n", (unsigned)count);
    for (i = 0; i < count; i++) {
        const recorder_event *e = recorder_event_at(order[i]);
        uint32_t nargs = e->nargs < RECORDER_MAX_ARGS ? e->nargs : RECORDER_MAX_ARGS;
        uint32_t a;
        uint32_t c;
        uint32_t s;

        printf("OBS thread=%u seq=%" PRIu64 " phase=%u hook=%u point=%u result=%" PRIu64
               " complete=%d nargs=%u args=",
               (unsigned)e->thread_id, e->sequence, (unsigned)e->phase,
               (unsigned)e->hook_id, (unsigned)e->point, e->result, e->complete,
               (unsigned)nargs);
        for (a = 0; a < nargs; a++) {
            printf("%s%" PRIu64, a == 0u ? "" : ",", e->args[a]);
        }
        printf(" ncontext=%u context=", (unsigned)e->ncontext);
        for (c = 0; c < e->ncontext; c++) {
            printf("%s%" PRIu64, c == 0u ? "" : ",", e->context[c]);
        }
        printf(" nspans=%u content=", (unsigned)e->nspans);
        for (s = 0; s < e->nspans; s++) {
            if (e->span_length[s] > 0u) {
                const uint8_t *bytes = recorder_content_at(e->span_offset[s], e->span_length[s]);
                uint32_t b;
                printf("%sc:%u:", s == 0u ? "" : ",", (unsigned)e->span_length[s]);
                for (b = 0; b < e->span_length[s]; b++) {
                    printf("%02x", (unsigned)bytes[b]);
                }
            } else {
                printf("%sl:%u", s == 0u ? "" : ",", (unsigned)e->span_lost[s]);
            }
        }
        printf("\n");
    }
}

static void print_counters(const char *name, int with_observations)
{
    recorder_counters counters;
    recorder_snapshot(&counters);
    printf("SCENARIO %s\n", name);
    printf("COUNTERS intercepted=%" PRIu64 " selected=%" PRIu64 " unselected=%" PRIu64
           " captured=%" PRIu64 " incomplete=%" PRIu64 " lost=%" PRIu64
           " reentrant=%" PRIu64 " rejected=%" PRIu64
           " content_bytes_declared=%" PRIu64 " content_bytes_captured=%" PRIu64
           " content_bytes_lost=%" PRIu64 " content_spans_lost=%" PRIu64 "\n",
           counters.intercepted, counters.selected, counters.unselected, counters.captured,
           counters.incomplete, counters.lost, counters.reentrant, counters.rejected,
           counters.content_bytes_declared, counters.content_bytes_captured,
           counters.content_bytes_lost, counters.content_spans_lost);
    printf("ACCOUNTING %s\n", recorder_accounting_holds(&counters) ? "ok" : "VIOLATED");
    print_arena();
    if (with_observations) {
        print_observations();
    }
}

static int configure_content(uint32_t capacity, uint32_t content_capacity,
                             const recorder_policy *policy)
{
    recorder_config config = {0};
    int rc;
    config.mode = RECORDER_MODE_ENABLED;
    config.capacity = capacity;
    config.max_args = 4u;
    config.content_capacity = content_capacity;
    config.policy = policy;
    rc = recorder_init(&config);
    if (rc == 0) {
        g_content_capacity = content_capacity;
    }
    return rc;
}

int main(void)
{
    printf("RECORDER-EXPORT v1\n");

    /* Off by default: content_capacity == 0 records accounting only and declares
     * not a single byte. */
    if (configure_content(16u, 0u, 0) != 0) {
        return 2;
    }
    run_script();
    print_counters("content-off", 1);

    /* An ample arena: every declared span is captured whole. */
    (void)configure_content(16u, 64u, 0);
    run_script();
    print_counters("content-ample", 1);

    /* Exactly the declared total of twelve bytes: nothing is lost. */
    (void)configure_content(16u, 12u, 0);
    run_script();
    print_counters("content-exact", 1);

    /* One byte short: the last declared span is lost whole, and the observation
     * that declared it becomes incomplete. */
    (void)configure_content(16u, 11u, 0);
    run_script();
    print_counters("content-short", 1);

    /* One observation declares two spans and only the first fits. */
    (void)configure_content(16u, 6u, 0);
    run_pair_script();
    print_counters("content-two-spans", 1);

    /* The same observation with both spans failing: several lost spans under a
     * single incomplete event. */
    (void)configure_content(16u, 4u, 0);
    run_pair_script();
    print_counters("content-two-spans-lost", 1);

    /* A zero-length span declares nothing and is never a loss. */
    (void)configure_content(16u, 8u, 0);
    run_zero_script();
    print_counters("content-zero-length", 1);

    /* A policy that deep-captures the updates only: the content the other hooks
     * declare is not selected, so it is neither counted nor copied. */
    (void)configure_content(16u, 64u, &POLICY_UPDATES_ONLY);
    run_script();
    print_counters("content-policy", 1);

    /* More content than the recorder can own is rejected, never truncated. */
    {
        recorder_config config = {0};
        int rejected;
        config.mode = RECORDER_MODE_ENABLED;
        config.capacity = 16u;
        config.max_args = 4u;
        config.content_capacity = RECORDER_MAX_CONTENT_BYTES + 1u;
        rejected = recorder_init(&config) != 0;
        printf("CONFIG oversize_content_rejected=%d\n", rejected ? 1 : 0);
    }

    /* Four threads, ample capacity: every declared span is captured. */
    if (run_concurrent(64u, 64u) != 0) {
        return 3;
    }
    print_counters("content-concurrent", 1);

    /* Four threads into eight arena bytes: the arena ends exactly full, loss is
     * exact, and no two spans share a byte. */
    if (run_concurrent(64u, 8u) != 0) {
        return 3;
    }
    print_counters("content-concurrent-bounded", 0);

    printf("END effects=%" PRIu64 "\n", atomic_load_explicit(&g_effects, memory_order_relaxed));
    recorder_shutdown();
    return 0;
}
