#ifndef EU4_SUBMISSION_MESH_CHAIN_H
#define EU4_SUBMISSION_MESH_CHAIN_H

#include <stdbool.h>
#include <stdint.h>

#include "subrecord_equivalence.h"

typedef struct {
    bool have_counted_epoch;
    uint64_t counted_nonempty_epoch;
    bool have_prev_site_in_epoch;
    uint64_t prev_parent_id;
    uintptr_t prev_sub_pointer;
    eu4_buffer_bind_signature_t prev_buffer;
} eu4_mesh_pair_chain_t;

typedef struct {
    bool have_counted_epoch;
    uint64_t counted_nonempty_epoch;
} eu4_mesh_invocation_accounting_t;

void eu4_submission_reset_invocation_accounting(void);
void eu4_submission_reset_pair_chain(void);

void eu4_submission_mesh_on_renderbuckets_entry(uint64_t epoch, bool count_armed_renderbuckets);

void eu4_submission_mesh_on_site(
    uint64_t epoch,
    uint32_t entry_layer,
    uint32_t entry_flush_kind,
    const uint8_t *parent,
    const uint8_t *sub,
    const eu4_buffer_bind_signature_t *curr_buffer,
    bool comparator_active);

#endif
