#include "eu4_r0_control.h"

#include <stdatomic.h>

uint64_t eu4_r0_control_pack(uint32_t generation, uint32_t scenario_id, uint32_t mode, uint32_t version) {
    return ((uint64_t)version << 56) | ((uint64_t)mode << 48) | ((uint64_t)scenario_id << 32) |
           (uint64_t)generation;
}

bool eu4_r0_control_unpack(uint64_t packed, eu4_r0_control_snapshot_t *out) {
    if (!out) {
        return false;
    }
    out->generation = (uint32_t)(packed & 0xffffffffu);
    out->scenario_id = (uint32_t)((packed >> 32) & 0xffffu);
    out->mode = (uint32_t)((packed >> 48) & 0xffu);
    out->version = (uint32_t)((packed >> 56) & 0xffu);
    out->valid = false;
    if (out->version == 0 || out->version != EU4_R0_CONTROL_VERSION) {
        return false;
    }
    if (out->mode > EU4_R0_MODE_MAX || out->scenario_id > EU4_R0_SCENARIO_MAX) {
        return false;
    }
    if (out->mode != EU4_R0_MODE_PASSIVE && out->mode != EU4_R0_MODE_CENSUS &&
        out->mode != EU4_R0_MODE_CENSUS_CAPTURE) {
        return false;
    }
    out->valid = true;
    return true;
}

bool eu4_r0_control_atomics_supported(void) {
    _Atomic uint64_t word = 0;
    return atomic_is_lock_free(&word);
}

void eu4_r0_control_publish(void *page, uint32_t generation, uint32_t mode, uint32_t scenario_id) {
    if (!page || !eu4_r0_control_atomics_supported()) {
        return;
    }
    eu4_r0_control_page_t *control = (eu4_r0_control_page_t *)page;
    uint64_t packed = eu4_r0_control_pack(generation, scenario_id, mode, EU4_R0_CONTROL_VERSION);
    atomic_store_explicit(&control->word, packed, memory_order_release);
}

bool eu4_r0_control_consume(const volatile eu4_r0_control_page_t *control, eu4_r0_control_snapshot_t *out) {
    if (!control || !out) {
        return false;
    }
    uint64_t packed =
        atomic_load_explicit((const atomic_uint_fast64_t *)&control->word, memory_order_acquire);
    return eu4_r0_control_unpack(packed, out) && out->valid;
}
