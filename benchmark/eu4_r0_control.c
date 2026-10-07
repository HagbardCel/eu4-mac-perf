#include "eu4_r0_control.h"

#include <stdatomic.h>
#include <string.h>

void eu4_r0_control_publish(void *page, uint32_t generation, uint32_t mode, uint32_t scenario_id) {
    if (!page) {
        return;
    }
    volatile eu4_r0_control_page_t *control = (volatile eu4_r0_control_page_t *)page;
    uint32_t seq = atomic_load_explicit((atomic_uint *)&control->seq, memory_order_relaxed);
    atomic_store_explicit((atomic_uint *)&control->seq, seq + 1u, memory_order_release);
    control->magic = EU4_R0_CONTROL_MAGIC;
    control->generation = generation;
    control->mode = mode;
    control->scenario_id = scenario_id;
    atomic_store_explicit((atomic_uint *)&control->seq, seq + 2u, memory_order_release);
}

bool eu4_r0_control_consume(const volatile eu4_r0_control_page_t *control,
                            eu4_r0_control_snapshot_t *out) {
    if (!control || !out) {
        return false;
    }
    for (int attempt = 0; attempt < 8; attempt++) {
        uint32_t seq1 = atomic_load_explicit((atomic_uint *)&control->seq, memory_order_acquire);
        if (seq1 & 1u) {
            continue;
        }
        eu4_r0_control_snapshot_t snap = {
            .magic = control->magic,
            .generation = control->generation,
            .mode = control->mode,
            .scenario_id = control->scenario_id,
        };
        uint32_t seq2 = atomic_load_explicit((atomic_uint *)&control->seq, memory_order_acquire);
        if (seq1 == seq2 && !(seq2 & 1u)) {
            *out = snap;
            return snap.magic == EU4_R0_CONTROL_MAGIC;
        }
    }
    return false;
}
