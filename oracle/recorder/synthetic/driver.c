/* Deterministic scenario driver for the Oracle P4 synthetic target.
 *
 * Runs a fixed script that exercises every property the P4 recorder must handle
 * and prints one canonical transcript line per step. The transcript is the
 * "native golden output": it depends only on C semantics - fixed-width unsigned
 * arithmetic and explicit byte encoding - never on the compiler, the host word
 * size, the clock or a pointer value, so it reproduces byte-for-byte.
 *
 * The driver is the trusted host. It implements `synth_effect` (collecting the
 * ordered effect stream) and inspects world state directly, which is exactly the
 * role P4.3's recorder will take over. It never launches EU IV.
 */
#include <stdint.h>
#include <stdio.h>

#include "synth.h"

static uint32_t effect_count = 0;

void synth_effect(uint32_t op, uint32_t a, uint32_t b)
{
    effect_count += 1;
    printf("EFF id=%u op=%u slot=%u arg=%u\n",
           (unsigned)effect_count, (unsigned)op, (unsigned)a, (unsigned)b);
}

static const char *rc_name(int rc)
{
    switch (rc) {
    case SYNTH_OK:      return "ok";
    case SYNTH_STALE:   return "stale";
    case SYNTH_NO_SLOT: return "no_slot";
    case SYNTH_BAD_ARG: return "bad_arg";
    default:            return "unknown";
    }
}

static void print_bytes(const synth_world *w, uint32_t slot)
{
    uint32_t j;
    printf("DATA slot=%u bytes=", (unsigned)slot);
    for (j = 0; j < SYNTH_RESOURCE_CAPACITY; j++) {
        printf("%02x", (unsigned)w->slots[slot].data[j]);
    }
    printf("\n");
}

static void print_read(int rc, synth_handle handle, uint32_t offset, uint32_t value)
{
    printf("CALL read rc=%s slot=%u gen=%u offset=%u value=",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation,
           (unsigned)offset);
    if (rc == SYNTH_OK) {
        printf("%u\n", (unsigned)value);
    } else {
        printf("-\n");
    }
}

int main(void)
{
    synth_world world;
    synth_handle handle = {0u, 0u};
    synth_handle alias = {0u, 0u};
    uint32_t value = 0;
    uint32_t folded;
    uint32_t stale_reads = 0;
    int rc;

    synth_world_init(&world);
    printf("SYNTH v1\n");

    /* 1-2. create a 4-byte resource and show its zeroed initial state. */
    rc = synth_create(&world, 4, &handle);
    printf("CALL create rc=%s size=4 slot=%u gen=%u\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation);
    print_bytes(&world, handle.slot);

    /* 3-4. update through the handle, then show the bytes. */
    rc = synth_update(&world, handle, 5, 4);
    printf("CALL update rc=%s slot=%u gen=%u version=%u seed=5 span=4\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation,
           (unsigned)world.slots[handle.slot].version);
    print_bytes(&world, handle.slot);

    /* 5. read one byte. */
    rc = synth_read(&world, handle, 0, &value);
    print_read(rc, handle, 0, value);

    /* 6-8. alias the resource, update through the alias, then read back through
     * the original handle: two handles, one resource. */
    rc = synth_share(&world, handle, &alias);
    printf("CALL share rc=%s slot=%u gen=%u\n",
           rc_name(rc), (unsigned)alias.slot, (unsigned)alias.generation);
    rc = synth_update(&world, alias, 1, 2);
    printf("CALL update rc=%s slot=%u gen=%u version=%u seed=1 span=2\n",
           rc_name(rc), (unsigned)alias.slot, (unsigned)alias.generation,
           (unsigned)world.slots[alias.slot].version);
    print_bytes(&world, alias.slot);
    rc = synth_read(&world, handle, 1, &value);
    print_read(rc, handle, 1, value);

    /* 9. bounded recursion over the shared state. */
    folded = synth_fold(&world, handle, 3, 1);
    printf("CALL fold rc=ok depth=3 value=%u\n", (unsigned)folded);

    /* 10-11. destroy, then read through the now-stale handle. */
    rc = synth_destroy(&world, handle);
    printf("CALL destroy rc=%s slot=%u gen=%u\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation);
    value = 0;
    rc = synth_read(&world, handle, 0, &value);
    if (rc != SYNTH_OK) {
        stale_reads += 1;
    }
    print_read(rc, handle, 0, value);

    /* 12-14. reuse the slot (new generation), then show that equal arguments
     * over a different pre-state give a different result: `seed=5 span=4`
     * produced 05060708 above, but here it follows a `seed=9 span=4` write. */
    rc = synth_create(&world, 4, &handle);
    printf("CALL create rc=%s size=4 slot=%u gen=%u\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation);
    rc = synth_update(&world, handle, 9, 4);
    printf("CALL update rc=%s slot=%u gen=%u version=%u seed=9 span=4\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation,
           (unsigned)world.slots[handle.slot].version);
    print_bytes(&world, handle.slot);
    rc = synth_update(&world, handle, 5, 4);
    printf("CALL update rc=%s slot=%u gen=%u version=%u seed=5 span=4\n",
           rc_name(rc), (unsigned)handle.slot, (unsigned)handle.generation,
           (unsigned)world.slots[handle.slot].version);
    print_bytes(&world, handle.slot);

    /* 15. final world digest and counters. */
    printf("CALL checksum value=%u\n", (unsigned)synth_checksum(&world));
    printf("END creates=%u updates=%u destroys=%u stale_reads=%u effects=%u\n",
           (unsigned)world.creates, (unsigned)world.updates, (unsigned)world.destroys,
           (unsigned)stale_reads, (unsigned)effect_count);
    return 0;
}
