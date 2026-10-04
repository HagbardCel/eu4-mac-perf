#include "submission_counter_schema.h"

#include <stdint.h>
#include <string.h>

uint64_t g_test_counter_values[EU4_SUBMISSION_COUNTER_COUNT];

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta) {
    if (slot < EU4_SUBMISSION_COUNTER_COUNT) {
        g_test_counter_values[slot] += delta;
    }
}

void eu4_submission_record_eligible_pair_hit(void) {
    eu4_submission_counter_add(EU4_COUNTER_LEGACY_ELIGIBLE_PAIR_HITS, 1);
}

void reset_test_counters(void) {
    memset(g_test_counter_values, 0, sizeof(g_test_counter_values));
}
