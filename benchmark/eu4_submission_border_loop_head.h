#ifndef EU4_SUBMISSION_BORDER_LOOP_HEAD_H
#define EU4_SUBMISSION_BORDER_LOOP_HEAD_H

#include "border_loop_classifier.h"

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    EU4_BORDER_LOOP_HEAD_INSTALL_OK = 0,
    EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_CLEAN = 1,
    EU4_BORDER_LOOP_HEAD_INSTALL_FAILED_FATAL = 2,
} eu4_border_loop_head_install_result_t;

eu4_border_loop_head_install_result_t eu4_border_loop_head_install_ex(void);
eu4_border_loop_head_install_result_t eu4_border_loop_head_try_install_at(void *site, uintptr_t image_base);
bool eu4_border_loop_head_install(void);
bool eu4_border_loop_head_hook_installed(void);
bool eu4_border_loop_head_install_fatal(void);

void eu4_border_loop_head_install_test_reset_hooks(void);
void eu4_border_loop_head_install_test_force_rx_fail(int attempts);
void eu4_border_loop_head_install_test_force_rollback_fail(int attempts);

void eu4_border_loop_head_epoch_reset(void);
void eu4_border_loop_head_flush_pending(void);
void eu4_border_loop_head_capture_freeze_state(void);

void eu4_border_loop_head_observe_frame(void *rbp, void *r12, uint64_t r13_full, void *rcx);

void eu4_border_loop_head_on_armed_hit(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uintptr_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end);

/* Test / harness entry without live detour. */
void eu4_border_loop_head_test_reset_tls(void);
uint64_t eu4_border_loop_head_test_tls_semantic_eliminations(void);
uint64_t eu4_border_loop_head_test_tls_implementable_eliminations(void);
uint64_t eu4_border_loop_head_test_tls_semantic_evaluations(void);
uint64_t eu4_border_loop_head_test_tls_implementable_evaluations(void);
uint64_t eu4_border_loop_head_test_tls_semantic_decisions(void);
uint64_t eu4_border_loop_head_test_tls_implementable_decisions(void);
bool eu4_border_loop_head_test_epoch_invalid(void);
void eu4_border_loop_head_test_on_armed_hit_suffix(
    const eu4_border_loop_context_t *ctx,
    const eu4_border_record_view_t *record_table,
    const uintptr_t *ibo_table,
    uint32_t record_count,
    const eu4_border_index_entry_t *side_entries,
    uint32_t side_count,
    bool walk_end);
bool eu4_border_loop_head_self_test_decode_geometry(void);
bool eu4_border_loop_head_self_test_thread_affinity(void);
bool eu4_border_loop_head_self_test_freeze_capture(void);

#endif
