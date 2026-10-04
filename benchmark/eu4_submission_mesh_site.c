#include "subrecord_equivalence.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

void eu4_submission_record_candidate_site_entry(void);
void eu4_submission_record_eligible_pair_hit(void);
bool eu4_submission_candidate_predicate_active(void);

static uint8_t *prev_parent;
static uint8_t *prev_sub;
static uint64_t prev_parent_id;
static uint32_t prev_layer;
static uint32_t prev_flush_kind;
static bool have_prev;

void eu4_submission_mesh_reset_chain(void) {
    have_prev = false;
    prev_parent = NULL;
    prev_sub = NULL;
    prev_parent_id = 0;
    prev_layer = 0;
    prev_flush_kind = 0;
}

void eu4_submission_mesh_observer_noop(void) {}

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

    if (!eu4_submission_candidate_predicate_active()) {
        eu4_submission_mesh_reset_chain();
        return;
    }

    uint64_t parent_id = (uint64_t)(uintptr_t)parent;
    if (have_prev
        && (parent_id != prev_parent_id || layer_index != prev_layer || flush_array_kind != prev_flush_kind)) {
        eu4_submission_mesh_reset_chain();
    }

    if (have_prev && prev_parent && prev_sub) {
        eu4_subrecord_context_t prev_ctx = {
            .parent_sflushdata_id = prev_parent_id,
            .layer_index = prev_layer,
            .flush_array_kind = prev_flush_kind,
            .same_parent_boundary = true,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_subrecord_context_t curr_ctx = {
            .parent_sflushdata_id = parent_id,
            .layer_index = layer_index,
            .flush_array_kind = flush_array_kind,
            .same_parent_boundary = true,
            .chain_broken = false,
            .has_immediate_predecessor = true,
        };
        eu4_predicate_reason_t reason = EU4_PRED_OK;
        if (eu4_buffer_bind_elision_eligible(
                prev_parent, parent, prev_sub, sub, &prev_ctx, &curr_ctx, &reason)) {
            eu4_submission_record_eligible_pair_hit();
        }
    }

    prev_parent = parent;
    prev_sub = sub;
    prev_parent_id = parent_id;
    prev_layer = layer_index;
    prev_flush_kind = flush_array_kind;
    have_prev = true;
}
