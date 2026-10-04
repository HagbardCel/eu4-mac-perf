#ifndef EU4_SUBMISSION_OBSERVATION_H
#define EU4_SUBMISSION_OBSERVATION_H

#include <stdbool.h>
#include <stdint.h>

void eu4_submission_on_renderbuckets_entry(uint32_t layer, uint32_t arg_r8_bool, uint32_t arg_r9_bool);
void eu4_submission_observation_arm_reset(void);
uint64_t eu4_submission_current_epoch(void);
uint32_t eu4_submission_entry_layer(void);
uint32_t eu4_submission_entry_arg_r8_bool(void);
uint32_t eu4_submission_entry_flush_array_kind(void);
bool eu4_submission_observation_bank_active(void);
bool eu4_submission_candidate_predicate_active(void);

#endif
