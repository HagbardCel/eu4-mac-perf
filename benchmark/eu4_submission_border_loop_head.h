#ifndef EU4_SUBMISSION_BORDER_LOOP_HEAD_H
#define EU4_SUBMISSION_BORDER_LOOP_HEAD_H

#include "border_loop_classifier.h"

#include <stdbool.h>
#include <stdint.h>

bool eu4_border_loop_head_install(void);
bool eu4_border_loop_head_hook_installed(void);

void eu4_border_loop_head_epoch_reset(void);
void eu4_border_loop_head_flush_pending(void);

void eu4_border_loop_head_on_armed_hit(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uint32_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end);

void eu4_border_loop_head_record_structural_submission(bool mode0_positive_count);

/* Test / harness entry without live detour. */
void eu4_border_loop_head_test_reset_tls(void);
uint64_t eu4_border_loop_head_test_tls_semantic_eliminations(void);
uint64_t eu4_border_loop_head_test_tls_implementable_eliminations(void);

#endif
