#include "eu4_submission_mesh_chain.h"
#include "eu4_submission_observation.h"
#include "submission_counter_schema.h"

#include <stdbool.h>
#include <stdint.h>

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);
void eu4_submission_record_health_site_entry(void);
void eu4_submission_record_candidate_site_entry(void);

void eu4_submission_mesh_site_from_frame(void *rbp) {
    if (!rbp) {
        return;
    }
    eu4_submission_counter_add(EU4_COUNTER_SITE_ENTRIES, 1);
    if (!eu4_submission_observation_bank_active()) {
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
    const uint32_t entry_layer = eu4_submission_entry_layer();
    const uint32_t entry_flush_kind = eu4_submission_entry_flush_array_kind();
    eu4_buffer_bind_signature_t curr_buffer;
    eu4_buffer_bind_signature_read(sub, &curr_buffer);

    eu4_submission_mesh_on_site(
        epoch,
        entry_layer,
        entry_flush_kind,
        parent,
        sub,
        &curr_buffer,
        eu4_submission_candidate_predicate_active());
}
