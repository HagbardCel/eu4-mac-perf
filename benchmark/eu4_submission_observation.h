#ifndef EU4_SUBMISSION_OBSERVATION_H
#define EU4_SUBMISSION_OBSERVATION_H

#include <stdbool.h>
#include <stdint.h>

void eu4_submission_on_renderbuckets_entry(void);
void eu4_submission_observation_arm_reset_chain(void);
uint64_t eu4_submission_current_epoch(void);
bool eu4_submission_observation_bank_active(void);

#endif
