/* The Oracle P4b synthetic graphics subject. See gfx.h.
 *
 * Every operation is byte-level and integer-only, and every byte it writes is
 * written explicitly, so the frame is a pure function of the captured resource
 * contents and the operation timeline. That is what makes an independent replay
 * possible at all: nothing here may depend on a pointer, a clock or a locale.
 */
#include "gfx.h"

static int slot_of(const gfx_world *w, gfx_handle handle, uint32_t *slot)
{
    if (handle.slot >= GFX_SLOTS) {
        return GFX_STALE;
    }
    if (!w->slots[handle.slot].live) {
        return GFX_STALE;
    }
    if (w->slots[handle.slot].generation != handle.generation) {
        return GFX_STALE;
    }
    *slot = handle.slot;
    return GFX_OK;
}

void gfx_world_init(gfx_world *w)
{
    uint32_t i;
    uint32_t j;
    w->creates = 0;
    w->destroys = 0;
    w->ops = 0;
    for (i = 0; i < GFX_SLOTS; i++) {
        w->slots[i].live = 0;
        w->slots[i].kind = 0;
        w->slots[i].generation = 0;
        w->slots[i].version = 0;
        w->slots[i].size = 0;
        for (j = 0; j < GFX_CAPACITY; j++) {
            w->slots[i].data[j] = 0;
        }
    }
}

int gfx_create(gfx_world *w, uint32_t kind, uint32_t size, const uint8_t *contents,
               gfx_handle *out)
{
    uint32_t i;
    if (kind != GFX_TEXTURE && kind != GFX_VERTEX_BUFFER) {
        return GFX_BAD_ARG;
    }
    if (size == 0 || size > GFX_CAPACITY) {
        return GFX_BAD_ARG;
    }
    if (contents == 0) {
        return GFX_BAD_ARG;
    }
    for (i = 0; i < GFX_SLOTS; i++) {
        if (!w->slots[i].live) {
            gfx_slot *s = &w->slots[i];
            uint32_t j;
            s->live = 1;
            s->kind = kind;
            s->size = size;
            s->version = 0;
            for (j = 0; j < GFX_CAPACITY; j++) {
                s->data[j] = j < size ? contents[j] : 0;
            }
            w->creates += 1;
            if (out != 0) {
                out->slot = i;
                out->generation = s->generation;
            }
            gfx_effect(GFX_EFFECT_CREATE, i, s->generation);
            return GFX_OK;
        }
    }
    return GFX_NO_SLOT;
}

int gfx_destroy(gfx_world *w, gfx_handle handle)
{
    uint32_t slot;
    gfx_slot *s;
    uint32_t j;
    int rc = slot_of(w, handle, &slot);
    if (rc != GFX_OK) {
        return rc;
    }
    s = &w->slots[slot];
    s->live = 0;
    s->size = 0;
    /* Reuse: the next create into this slot gets a new generation. */
    s->generation += 1;
    for (j = 0; j < GFX_CAPACITY; j++) {
        s->data[j] = 0;
    }
    w->destroys += 1;
    gfx_effect(GFX_EFFECT_DESTROY, slot, s->generation);
    return GFX_OK;
}

int gfx_fill(gfx_world *w, gfx_handle target, uint32_t offset, uint32_t span, uint32_t pattern)
{
    uint32_t slot;
    gfx_slot *s;
    uint32_t i;
    int rc = slot_of(w, target, &slot);
    if (rc != GFX_OK) {
        return rc;
    }
    s = &w->slots[slot];
    if (s->kind != GFX_TEXTURE) {
        return GFX_TYPE;
    }
    if (span == 0 || offset > s->size || span > s->size - offset) {
        return GFX_BAD_ARG;
    }
    for (i = 0; i < span; i++) {
        s->data[offset + i] = (uint8_t)(pattern + i);
    }
    s->version += 1;
    w->ops += 1;
    gfx_effect(GFX_EFFECT_FILL, slot, s->version);
    return GFX_OK;
}

int gfx_blit(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source)
{
    uint32_t target_slot;
    uint32_t source_slot;
    gfx_slot *dst;
    const gfx_slot *src;
    uint32_t i;
    uint32_t n;
    int rc = slot_of(w, target, &target_slot);
    if (rc != GFX_OK) {
        return rc;
    }
    rc = slot_of(w, source, &source_slot);
    if (rc != GFX_OK) {
        return rc;
    }
    dst = &w->slots[target_slot];
    src = &w->slots[source_slot];
    if (dst->kind != GFX_TEXTURE || src->kind != GFX_VERTEX_BUFFER) {
        return GFX_TYPE;
    }
    if (offset > dst->size) {
        return GFX_BAD_ARG;
    }
    n = src->size;
    if (n > dst->size - offset) {
        n = dst->size - offset;
    }
    for (i = 0; i < n; i++) {
        dst->data[offset + i] = (uint8_t)(dst->data[offset + i] ^ src->data[i]);
    }
    dst->version += 1;
    w->ops += 1;
    gfx_effect(GFX_EFFECT_BLIT, target_slot, dst->version);
    return GFX_OK;
}

int gfx_mix(gfx_world *w, gfx_handle target, uint32_t offset, gfx_handle source,
            uint32_t rounds)
{
    uint32_t target_slot;
    uint32_t source_slot;
    gfx_slot *dst;
    const gfx_slot *src;
    uint32_t i;
    uint32_t r;
    uint32_t n;
    int rc = slot_of(w, target, &target_slot);
    if (rc != GFX_OK) {
        return rc;
    }
    rc = slot_of(w, source, &source_slot);
    if (rc != GFX_OK) {
        return rc;
    }
    dst = &w->slots[target_slot];
    src = &w->slots[source_slot];
    if (dst->kind != GFX_TEXTURE || src->kind != GFX_TEXTURE) {
        return GFX_TYPE;
    }
    if (offset > dst->size) {
        return GFX_BAD_ARG;
    }
    n = src->size;
    if (n > dst->size - offset) {
        n = dst->size - offset;
    }
    for (r = 0; r < rounds; r++) {
        for (i = 0; i < n; i++) {
            dst->data[offset + i] = (uint8_t)(dst->data[offset + i] + src->data[i]
                                              + (uint8_t)r);
        }
    }
    dst->version += 1;
    w->ops += 1;
    gfx_effect(GFX_EFFECT_MIX, target_slot, dst->version);
    return GFX_OK;
}

int gfx_present(const gfx_world *w, gfx_handle target, uint32_t *digest_out)
{
    uint32_t slot;
    const gfx_slot *s;
    int rc = slot_of(w, target, &slot);
    if (rc != GFX_OK) {
        return rc;
    }
    s = &w->slots[slot];
    if (s->kind != GFX_TEXTURE) {
        return GFX_TYPE;
    }
    if (digest_out != 0) {
        *digest_out = gfx_digest_bytes(s->data, s->size);
    }
    gfx_effect(GFX_EFFECT_PRESENT, slot, s->version);
    return GFX_OK;
}

uint32_t gfx_digest_bytes(const uint8_t *data, uint32_t size)
{
    uint32_t i;
    uint32_t hash = 2166136261u; /* FNV-1a, 32-bit */
    for (i = 0; i < size; i++) {
        hash = (hash ^ data[i]) * 16777619u;
    }
    return hash;
}
