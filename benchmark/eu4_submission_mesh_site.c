#include "subrecord_equivalence.h"
#include "submission_counter_schema.h"
#include "eu4_submission_observation.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);
void eu4_submission_record_candidate_site_entry(void);
void eu4_submission_record_eligible_pair_hit(void);
void eu4_submission_record_nonempty_invocation(void);
bool eu4_submission_candidate_predicate_active(void);

typedef struct {
    bool have_prev;
    uint64_t prev_parent_id;
    uintptr_t prev_sub_pointer;
    uint32_t prev_layer;
    uint32_t prev_flush_kind;
    uint64_t prev_epoch;
    eu4_buffer_bind_signature_t prev_buffer;
    bool epoch_nonempty_recorded;
} eu4_mesh_chain_state_t;

static _Thread_local eu4_mesh_chain_state_t chain;

void eu4_submission_mesh_reset_chain(void) {
    memset(&chain, 0, sizeof(chain));
}

void eu4_submission_mesh_site_from_frame(void *rbp, uint32_t layer_index, uint32_t flush_array_kind) {
    if (!rbp) {
        return;
    }
    char *frame = (char *)rbp;
    uint8_t *parent = *(uint8_t **)(frame - 0x128);
    uint8_t *sub = *(uint8_t **)(frame - 0xb8);
    if (!parent || !sub) {
        return;
    }

    eu4_submission_record_candidate_site_entry();

    const uint64_t epoch = eu4_submission_current_epoch();
    if (chain.have_prev
        && (chain.prev_epoch != epoch || chain.prev_layer != layer_index
            || chain.prev_flush_kind != flush_array_kind)) {
        eu4_submission_mesh_reset_chain();
    }
    if (!chain.epoch_nonempty_recorded) {
        eu4_submission_record_nonempty_invocation();
        chain.epoch_nonempty_recorded = true;
    }

    const uint64_t parent_id = (uint64_t)(uintptr_t)parent;
    eu4_buffer_bind_signature_t curr_buffer;
    eu4_buffer_bind_signature_read(sub, &curr_buffer);
    const bool predicate_active = eu4_submission_candidate_predicate_active();

    if (chain.have_prev && predicate_active) {
        eu4_subrecord_context_t prev_ctx = {
            .parent_sflushdata_id = chain.prev_parent_id,
            .layer_index = chain.prev_layer,
            .flush_array_kind = chain.prev_flush_kind,
            .same_parent_boundary = chain.prev_parent_id == parent_id,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_subrecord_context_t curr_ctx = {
            .parent_sflushdata_id = parent_id,
            .layer_index = layer_index,
            .flush_array_kind = flush_array_kind,
            .same_parent_boundary = chain.prev_parent_id == parent_id,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_predicate_reason_t reason = EU4_PRED_OK;

        eu4_submission_counter_add(EU4_COUNTER_ADJACENT_DRAW_PAIRS_TOTAL, 1);
        if (chain.prev_parent_id == parent_id) {
            eu4_submission_counter_add(EU4_COUNTER_SAME_PARENT_PAIRS, 1);
        } else {
            eu4_submission_counter_add(EU4_COUNTER_CROSS_PARENT_PAIRS, 1);
        }

        if (chain.prev_parent_id == parent_id
            && eu4_buffer_bind_elision_eligible(
                   parent, parent, (const uint8_t *)(uintptr_t)chain.prev_sub_pointer, sub, &prev_ctx, &curr_ctx, &reason)) {
            eu4_submission_record_eligible_pair_hit();
        }

        if (chain.prev_parent_id != parent_id
            && eu4_cross_parent_buffer_contexts_comparable(&prev_ctx, &curr_ctx, &reason)) {
            if (chain.prev_sub_pointer == (uintptr_t)sub) {
                eu4_submission_counter_add(EU4_COUNTER_SAME_SUBRECORD_POINTER_CROSS_PARENT, 1);
            }
            if (eu4_buffer_bind_signatures_equal(&chain.prev_buffer, &curr_buffer)) {
                eu4_submission_counter_add(EU4_COUNTER_CROSS_PARENT_BUFFER_SIGNATURE, 1);
            }
        } else if (chain.prev_parent_id == parent_id
                   && eu4_buffer_bind_signatures_equal(&chain.prev_buffer, &curr_buffer)) {
            eu4_submission_counter_add(EU4_COUNTER_SAME_PARENT_BUFFER_SIGNATURE, 1);
        }
    }

    chain.have_prev = true;
    chain.prev_parent_id = parent_id;
    chain.prev_sub_pointer = (uintptr_t)sub;
    chain.prev_layer = layer_index;
    chain.prev_flush_kind = flush_array_kind;
    chain.prev_epoch = epoch;
    chain.prev_buffer = curr_buffer;

    if (!predicate_active) {
        eu4_submission_mesh_reset_chain();
    }
}
