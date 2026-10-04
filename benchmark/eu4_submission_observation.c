#include "eu4_submission_observation.h"

#include "eu4_submission_mesh_chain.h"

#include <stdint.h>

void eu4_submission_record_renderbuckets_invocation(void);
bool eu4_submission_observation_bank_active(void);

static _Thread_local uint64_t tls_epoch = 0;
static _Thread_local uint32_t tls_entry_layer = 0;
static _Thread_local uint32_t tls_entry_arg_r8_bool = 0;
static _Thread_local uint32_t tls_entry_arg_r9_bool = 0;

void eu4_submission_on_renderbuckets_entry(uint32_t layer, uint32_t arg_r8_bool, uint32_t arg_r9_bool) {
    tls_epoch++;
    tls_entry_layer = layer;
    tls_entry_arg_r8_bool = arg_r8_bool & 1u;
    tls_entry_arg_r9_bool = arg_r9_bool & 1u;
    eu4_submission_record_renderbuckets_invocation();
    const bool armed = eu4_submission_observation_bank_active();
    eu4_submission_mesh_on_renderbuckets_entry(tls_epoch, armed);
}

void eu4_submission_observation_arm_reset(void) {
    eu4_submission_reset_invocation_accounting();
    eu4_submission_reset_pair_chain();
}

uint64_t eu4_submission_current_epoch(void) {
    return tls_epoch;
}

uint32_t eu4_submission_entry_layer(void) {
    return tls_entry_layer;
}

uint32_t eu4_submission_entry_arg_r8_bool(void) {
    return tls_entry_arg_r8_bool;
}

uint32_t eu4_submission_entry_flush_array_kind(void) {
    return tls_entry_arg_r9_bool;
}
