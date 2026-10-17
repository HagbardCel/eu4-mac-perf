/* Trusted interface for the Oracle P4 synthetic recorder target.
 *
 * A small, portable C11 library that models the observation problems the P4
 * recorder must handle:
 *
 *   - two operations sharing mutable state (update and read both traverse the
 *     same resource table, and read depends on writes done through another handle);
 *   - resources with create / update / destroy and handle reuse across
 *     generations (a destroyed slot is reused by the next create with a new
 *     generation, so old handles stay invalid);
 *   - aliasing (two live handles to one resource via `synth_share`);
 *   - results that depend on pre-state, so equal arguments are not equal behavior;
 *   - a bounded recursive traversal (`synth_fold`);
 *   - an ordered external-effect stream.
 *
 * This library is the *subject*. It is not instrumented here; P4.3 observes it.
 * The trusted host - the driver now, the recorder later - implements
 * `synth_effect`, so the effect stream is produced by the subject but collected by
 * the host. Nothing in this file launches EU IV or touches a game process.
 */
#ifndef EU4_ORACLE_RECORDER_SYNTH_H
#define EU4_ORACLE_RECORDER_SYNTH_H

#include <stdint.h>

#define SYNTH_SLOTS 4u
#define SYNTH_RESOURCE_CAPACITY 16u

/* Result codes. */
enum {
    SYNTH_OK = 0,
    SYNTH_STALE = 1,     /* the handle's generation no longer lives in its slot */
    SYNTH_NO_SLOT = 2,   /* no free slot */
    SYNTH_BAD_ARG = 3    /* size, span or offset out of range */
};

/* Ordered external-effect operation codes. */
enum {
    SYNTH_EFFECT_CREATE = 1,
    SYNTH_EFFECT_UPDATE = 2,
    SYNTH_EFFECT_READ = 3,
    SYNTH_EFFECT_DESTROY = 4
};

typedef struct {
    uint32_t slot;
    uint32_t generation;
} synth_handle;

typedef struct {
    uint32_t live;
    uint32_t generation;
    uint32_t version;
    uint32_t size;
    uint8_t  data[SYNTH_RESOURCE_CAPACITY];
} synth_slot;

typedef struct {
    uint32_t creates;
    uint32_t updates;
    uint32_t destroys;
    synth_slot slots[SYNTH_SLOTS];
} synth_world;

/* Trusted effect sink, implemented by the host (driver or, later, the recorder).
 * Called once per observable operation, in order. */
void synth_effect(uint32_t op, uint32_t a, uint32_t b);

void synth_world_init(synth_world *w);

/* Create a resource of `size` bytes in the first free slot; write its handle to
 * `out`. `size` must be in [1, SYNTH_RESOURCE_CAPACITY]. */
int synth_create(synth_world *w, uint32_t size, synth_handle *out);

/* Advance the first `span` bytes of the resource by a running seed. The result
 * depends on the current bytes, so equal (seed, span) over different pre-state
 * yield different post-state. */
int synth_update(synth_world *w, synth_handle handle, uint32_t seed, uint32_t span);

/* Copy a live handle to `out` so two handles alias one resource. */
int synth_share(const synth_world *w, synth_handle handle, synth_handle *out);

/* Read one byte at `offset`; `out` receives it only on SYNTH_OK. */
int synth_read(const synth_world *w, synth_handle handle, uint32_t offset, uint32_t *out);

/* Destroy the resource. The slot's generation advances, invalidating old handles. */
int synth_destroy(synth_world *w, synth_handle handle);

/* Recursive fold over the resource version; a bounded recursion/reentrancy subject. */
uint32_t synth_fold(synth_world *w, synth_handle handle, uint32_t depth, uint32_t seed);

/* Order-independent digest of the whole world, for a golden transcript. */
uint32_t synth_checksum(const synth_world *w);

#endif /* EU4_ORACLE_RECORDER_SYNTH_H */
