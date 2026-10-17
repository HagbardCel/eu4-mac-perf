/* Scenario driver for the Oracle P4 native recorder core.
 *
 * Runs a fixed subject script through the recorder's hook layer and prints a
 * canonical transcript of the resulting accounting. It is the "golden output" of
 * P4.3: fixed-width unsigned arithmetic, no pointers, no clock, no locale, so the
 * transcript reproduces byte-for-byte across gcc and clang.
 *
 * The driver is the trusted host: it owns the hook layer (the stand-in for real
 * interposition, which the synthetic target cannot prove) and the subject's
 * effect sink. It never launches EU IV.
 */
#include <inttypes.h>
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
 * part of each capture's context; here it only confirms the subject ran. */
static uint64_t g_effects = 0;

void synth_effect(uint32_t op, uint32_t a, uint32_t b)
{
    (void)op;
    (void)a;
    (void)b;
    g_effects += 1;
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

static int configure(uint32_t mode, uint32_t capacity, const recorder_policy *policy)
{
    recorder_config config;
    config.mode = mode;
    config.capacity = capacity;
    config.max_args = 4;
    config.policy = policy;
    return recorder_init(&config);
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

static void print_counters(const char *name)
{
    const recorder_counters *c = recorder_counters_view();
    printf("SCENARIO %s\n", name);
    printf("COUNTERS intercepted=%" PRIu64 " selected=%" PRIu64 " unselected=%" PRIu64
           " captured=%" PRIu64 " incomplete=%" PRIu64 " lost=%" PRIu64
           " reentrant=%" PRIu64 " rejected=%" PRIu64 "\n",
           c->intercepted, c->selected, c->unselected, c->captured, c->incomplete,
           c->lost, c->reentrant, c->rejected);
    printf("ACCOUNTING %s\n", recorder_accounting_holds(c) ? "ok" : "VIOLATED");
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

    printf("END effects=%" PRIu64 "\n", g_effects);
    recorder_shutdown();
    return 0;
}
