/* The hook layer's observation context for the Oracle P4 synthetic subject.
 *
 * The recorder core stores context verbatim and never interprets it; this is the
 * subject-specific convention a hook layer uses to state which resource an
 * observation addressed. Declared once, here, so the offline side resolves
 * lifetimes against a single layout instead of two that could drift.
 *
 *   [0] ref_valid  [1] ref_slot  [2] ref_gen
 *   [3] live       [4] slot_gen  [5] version
 *
 * `(ref_slot, ref_gen)` is the referenced resource's lifetime; `(live, slot_gen,
 * version)` is that slot's state as the observation saw it - at entry for the
 * handle-addressing calls (so a destroy's resource is still live and a stale
 * handle's is not), and after creation for create.
 */
#ifndef EU4_ORACLE_RECORDER_NATIVE_SYNTH_CONTEXT_H
#define EU4_ORACLE_RECORDER_NATIVE_SYNTH_CONTEXT_H

#include <stdint.h>

#include "synth.h"

static void slot_state(const synth_world *w, uint32_t slot, uint32_t *live,
                       uint32_t *generation, uint32_t *version)
{
    if (slot < SYNTH_SLOTS) {
        *live = w->slots[slot].live;
        *generation = w->slots[slot].generation;
        *version = w->slots[slot].version;
    } else {
        *live = 0u;
        *generation = 0u;
        *version = 0u;
    }
}

static uint32_t reference_context(uint32_t valid, uint32_t slot, uint32_t generation,
                                  uint32_t live, uint32_t slot_generation, uint32_t version,
                                  uint64_t *context)
{
    context[0] = valid ? 1u : 0u;
    context[1] = valid ? slot : 0u;
    context[2] = valid ? generation : 0u;
    context[3] = live;
    context[4] = slot_generation;
    context[5] = version;
    return 6u;
}

#endif /* EU4_ORACLE_RECORDER_NATIVE_SYNTH_CONTEXT_H */
