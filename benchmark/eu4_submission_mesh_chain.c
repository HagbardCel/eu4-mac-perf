#include "eu4_submission_mesh_chain.h"

#include "submission_counter_schema.h"

#include <string.h>

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);
void eu4_submission_record_eligible_pair_hit(void);

static _Thread_local eu4_mesh_invocation_accounting_t invocation_accounting;
static _Thread_local eu4_mesh_pair_chain_t pair_chain;

void eu4_submission_reset_invocation_accounting(void) {
    memset(&invocation_accounting, 0, sizeof(invocation_accounting));
}

void eu4_submission_reset_pair_chain(void) {
    memset(&pair_chain, 0, sizeof(pair_chain));
}

void eu4_submission_mesh_on_renderbuckets_entry(uint64_t epoch, bool count_armed_renderbuckets) {
    eu4_submission_reset_pair_chain();
    if (count_armed_renderbuckets) {
        eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_RENDERBUCKETS_INVOCATIONS, 1);
    }
    (void)epoch;
}

static void record_nonempty_for_epoch(uint64_t epoch) {
    if (!invocation_accounting.have_counted_epoch || invocation_accounting.counted_nonempty_epoch != epoch) {
        eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_NONEMPTY_RENDERBUCKETS, 1);
        invocation_accounting.counted_nonempty_epoch = epoch;
        invocation_accounting.have_counted_epoch = true;
    }
}

void eu4_submission_mesh_on_site(
    uint64_t epoch,
    uint32_t entry_layer,
    uint32_t entry_flush_kind,
    const uint8_t *parent,
    const uint8_t *sub,
    const eu4_buffer_bind_signature_t *curr_buffer,
    bool comparator_active) {
    const uint64_t parent_id = (uint64_t)(uintptr_t)parent;
    const uintptr_t sub_pointer = (uintptr_t)sub;
    record_nonempty_for_epoch(epoch);

    if (pair_chain.have_prev_site_in_epoch && comparator_active) {
        eu4_subrecord_context_t prev_ctx = {
            .parent_sflushdata_id = pair_chain.prev_parent_id,
            .layer_index = entry_layer,
            .flush_array_kind = entry_flush_kind,
            .same_parent_boundary = pair_chain.prev_parent_id == parent_id,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_subrecord_context_t curr_ctx = {
            .parent_sflushdata_id = parent_id,
            .layer_index = entry_layer,
            .flush_array_kind = entry_flush_kind,
            .same_parent_boundary = pair_chain.prev_parent_id == parent_id,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_predicate_reason_t reason = EU4_PRED_OK;

        eu4_submission_counter_add(EU4_COUNTER_ADJACENT_WITHIN_INVOCATION, 1);
        if (pair_chain.prev_parent_id == parent_id) {
            eu4_submission_counter_add(EU4_COUNTER_SAME_PARENT_PAIRS, 1);
        } else {
            eu4_submission_counter_add(EU4_COUNTER_CROSS_PARENT_PAIRS, 1);
        }

        if (pair_chain.prev_parent_id == parent_id
            && eu4_buffer_bind_elision_eligible(
                   parent,
                   parent,
                   (const uint8_t *)pair_chain.prev_sub_pointer,
                   sub,
                   &prev_ctx,
                   &curr_ctx,
                   &reason)) {
            eu4_submission_record_eligible_pair_hit();
        }

        if (pair_chain.prev_parent_id != parent_id
            && eu4_cross_parent_buffer_contexts_comparable(&prev_ctx, &curr_ctx, &reason)) {
            if (pair_chain.prev_sub_pointer == sub_pointer) {
                eu4_submission_counter_add(EU4_COUNTER_SAME_SUBRECORD_POINTER_CROSS_PARENT, 1);
            }
            if (eu4_buffer_bind_signatures_equal(&pair_chain.prev_buffer, curr_buffer)) {
                eu4_submission_counter_add(EU4_COUNTER_CROSS_PARENT_BUFFER_SIGNATURE, 1);
            }
        } else if (pair_chain.prev_parent_id == parent_id
                   && eu4_buffer_bind_signatures_equal(&pair_chain.prev_buffer, curr_buffer)) {
            eu4_submission_counter_add(EU4_COUNTER_SAME_PARENT_BUFFER_SIGNATURE, 1);
        }
    }

    pair_chain.have_prev_site_in_epoch = true;
    pair_chain.prev_parent_id = parent_id;
    pair_chain.prev_sub_pointer = sub_pointer;
    pair_chain.prev_buffer = *curr_buffer;
}
