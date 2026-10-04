#include "eu4_submission_observation.h"

#include <stdatomic.h>
#include <stdint.h>

void eu4_submission_mesh_reset_chain(void);
void eu4_submission_record_renderbuckets_invocation(void);

static _Thread_local uint64_t tls_epoch = 0;

void eu4_submission_on_renderbuckets_entry(void) {
    tls_epoch++;
    eu4_submission_record_renderbuckets_invocation();
}

void eu4_submission_observation_arm_reset_chain(void) {
    eu4_submission_mesh_reset_chain();
}

uint64_t eu4_submission_current_epoch(void) {
    return tls_epoch;
}
