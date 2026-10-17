/* The Oracle P4 synthetic subject, implemented as a portable C11 library.
 *
 * Every value is a fixed-width unsigned type, and every byte is written explicitly,
 * so the observable behavior - and therefore the driver's golden transcript - does
 * not depend on the compiler or the host word size. There is no undefined behavior
 * to make that a promise instead of a hope.
 */
#include "synth.h"

/* Resolve a handle to a live slot in the current generation, or SYNTH_STALE. */
static int slot_of(const synth_world *w, synth_handle handle, uint32_t *slot)
{
    if (handle.slot >= SYNTH_SLOTS) {
        return SYNTH_STALE;
    }
    if (!w->slots[handle.slot].live) {
        return SYNTH_STALE;
    }
    if (w->slots[handle.slot].generation != handle.generation) {
        return SYNTH_STALE;
    }
    *slot = handle.slot;
    return SYNTH_OK;
}

void synth_world_init(synth_world *w)
{
    uint32_t i;
    uint32_t j;
    w->creates = 0;
    w->updates = 0;
    w->destroys = 0;
    for (i = 0; i < SYNTH_SLOTS; i++) {
        w->slots[i].live = 0;
        w->slots[i].generation = 0;
        w->slots[i].version = 0;
        w->slots[i].size = 0;
        for (j = 0; j < SYNTH_RESOURCE_CAPACITY; j++) {
            w->slots[i].data[j] = 0;
        }
    }
}

int synth_create(synth_world *w, uint32_t size, synth_handle *out)
{
    uint32_t i;
    if (size == 0 || size > SYNTH_RESOURCE_CAPACITY) {
        return SYNTH_BAD_ARG;
    }
    for (i = 0; i < SYNTH_SLOTS; i++) {
        if (!w->slots[i].live) {
            synth_slot *s = &w->slots[i];
            uint32_t j;
            s->live = 1;
            s->size = size;
            s->version = 0;
            for (j = 0; j < SYNTH_RESOURCE_CAPACITY; j++) {
                s->data[j] = 0;
            }
            w->creates += 1;
            if (out != 0) {
                out->slot = i;
                out->generation = s->generation;
            }
            synth_effect(SYNTH_EFFECT_CREATE, i, s->generation);
            return SYNTH_OK;
        }
    }
    return SYNTH_NO_SLOT;
}

int synth_update(synth_world *w, synth_handle handle, uint32_t seed, uint32_t span)
{
    uint32_t slot;
    synth_slot *s;
    uint32_t i;
    int rc = slot_of(w, handle, &slot);
    if (rc != SYNTH_OK) {
        return rc;
    }
    if (span == 0 || span > SYNTH_RESOURCE_CAPACITY) {
        return SYNTH_BAD_ARG;
    }
    s = &w->slots[slot];
    /* Advance each byte by the running seed, relative to what is already there:
     * the same arguments over a different pre-state give a different result. */
    for (i = 0; i < span; i++) {
        s->data[i] = (uint8_t)(s->data[i] + (uint8_t)(seed + i));
    }
    s->version += 1;
    w->updates += 1;
    synth_effect(SYNTH_EFFECT_UPDATE, slot, s->version);
    return SYNTH_OK;
}

int synth_share(const synth_world *w, synth_handle handle, synth_handle *out)
{
    uint32_t slot;
    int rc = slot_of(w, handle, &slot);
    if (rc != SYNTH_OK) {
        return rc;
    }
    if (out != 0) {
        out->slot = slot;
        out->generation = w->slots[slot].generation;
    }
    return SYNTH_OK;
}

int synth_read(const synth_world *w, synth_handle handle, uint32_t offset, uint32_t *out)
{
    uint32_t slot;
    int rc = slot_of(w, handle, &slot);
    if (rc != SYNTH_OK) {
        return rc;
    }
    if (offset >= w->slots[slot].size) {
        return SYNTH_BAD_ARG;
    }
    if (out != 0) {
        *out = w->slots[slot].data[offset];
    }
    synth_effect(SYNTH_EFFECT_READ, slot, offset);
    return SYNTH_OK;
}

int synth_destroy(synth_world *w, synth_handle handle)
{
    uint32_t slot;
    synth_slot *s;
    uint32_t j;
    int rc = slot_of(w, handle, &slot);
    if (rc != SYNTH_OK) {
        return rc;
    }
    s = &w->slots[slot];
    s->live = 0;
    s->size = 0;
    /* Reuse: the next create into this slot gets a new generation, so a stale
     * handle to the old lifetime stays invalid. */
    s->generation += 1;
    for (j = 0; j < SYNTH_RESOURCE_CAPACITY; j++) {
        s->data[j] = 0;
    }
    w->destroys += 1;
    synth_effect(SYNTH_EFFECT_DESTROY, slot, s->generation);
    return SYNTH_OK;
}

uint32_t synth_fold(synth_world *w, synth_handle handle, uint32_t depth, uint32_t seed)
{
    uint32_t slot;
    uint32_t acc;
    int rc = slot_of(w, handle, &slot);
    if (rc != SYNTH_OK) {
        return seed ^ 0xffffffffu;
    }
    acc = seed + w->slots[slot].version;
    if (depth == 0) {
        return acc;
    }
    return synth_fold(w, handle, depth - 1, acc * 31u + depth);
}

uint32_t synth_checksum(const synth_world *w)
{
    uint32_t i;
    uint32_t j;
    uint32_t hash = 2166136261u; /* FNV-1a */
    for (i = 0; i < SYNTH_SLOTS; i++) {
        const synth_slot *s = &w->slots[i];
        hash = (hash ^ s->live) * 16777619u;
        hash = (hash ^ s->generation) * 16777619u;
        hash = (hash ^ s->version) * 16777619u;
        hash = (hash ^ s->size) * 16777619u;
        for (j = 0; j < SYNTH_RESOURCE_CAPACITY; j++) {
            hash = (hash ^ s->data[j]) * 16777619u;
        }
    }
    return hash;
}
