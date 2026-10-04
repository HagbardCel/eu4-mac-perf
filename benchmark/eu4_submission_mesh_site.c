#include "subrecord_equivalence.h"

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

void eu4_submission_record_candidate_site_entry(void);
void eu4_submission_record_eligible_pair_hit(void);
bool eu4_submission_candidate_predicate_active(void);

static uint8_t prev_parent[EU4_SFLUSHDATA_SIZE];
static uint8_t prev_sub[EU4_MESH_DRAW_SUBRECORD_SIZE];
static eu4_subrecord_context_t prev_ctx;
static bool have_prev;

void eu4_submission_mesh_site_from_rbp(void *rbp) {
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
        return;
    }

    eu4_subrecord_context_t curr = {
        .parent_sflushdata_id = (uint64_t)(uintptr_t)parent,
        .layer_index = 0u,
        .flush_array_kind = 0u,
        .same_parent_boundary = true,
        .chain_broken = false,
        .has_immediate_predecessor = have_prev,
    };

    if (have_prev) {
        eu4_predicate_reason_t reason = EU4_PRED_OK;
        if (eu4_buffer_bind_elision_eligible(
                prev_parent, parent, prev_sub, sub, &prev_ctx, &curr, &reason)) {
            eu4_submission_record_eligible_pair_hit();
        }
    }

    memcpy(prev_parent, parent, sizeof(prev_parent));
    memcpy(prev_sub, sub, sizeof(prev_sub));
    prev_ctx = curr;
    prev_ctx.has_immediate_predecessor = true;
    have_prev = true;
}
