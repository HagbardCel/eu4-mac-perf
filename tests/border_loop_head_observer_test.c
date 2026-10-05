#include "eu4_submission_border_loop_head.h"
#include "submission_counter_schema.h"

#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

static bool g_armed = false;
static uint64_t g_counter_values[EU4_SUBMISSION_COUNTER_COUNT];

bool eu4_submission_observation_bank_active(void) {
    return g_armed;
}

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta) {
    if (slot < EU4_SUBMISSION_COUNTER_COUNT) {
        g_counter_values[slot] += delta;
    }
}

void eu4_border_init_gl_apis(void) {}

int main(void) {
    assert(eu4_border_loop_head_self_test_decode_geometry());
    assert(eu4_border_loop_head_self_test_thread_affinity());
    memset(g_counter_values, 0, sizeof(g_counter_values));
    eu4_border_loop_head_test_reset_tls();
    eu4_border_loop_head_capture_freeze_state();
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_PARTIAL_WALK] == 0);
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_SEMANTIC_SUPPRESS] == 0);
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_IMPLEMENTABLE_SUPPRESS] == 0);
    memset(g_counter_values, 0, sizeof(g_counter_values));
    assert(eu4_border_loop_head_self_test_freeze_capture());
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_PARTIAL_WALK] == 2);
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_SEMANTIC_SUPPRESS] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_ROI_FREEZE_IMPLEMENTABLE_SUPPRESS] == 3);

    eu4_border_loop_context_t ctx = {
        .mode = 0,
        .cached_color = 1,
        .cached_vbo_index = 1,
        .skip_or_visibility_mask = 0xFF,
        .outer_batch_index = 8,
        .bound_ibo_identity = 100,
        .ibo_known = true,
        .color_known = true,
        .vbo_known = true,
    };
    eu4_border_record_view_t records[4];
    uintptr_t ibos[4] = {100, 100, 100, 100};
    eu4_border_index_entry_t entries[4];
    for (int i = 0; i < 4; i++) {
        records[i].arg3_u16_at_plus_04 = 1;
        records[i].triangle_count = 4;
        records[i].vbo_table_index = 1;
        entries[i].record_index = i;
        entries[i].visibility_byte = 0xFF;
        entries[i].color_byte = 1;
    }

    g_armed = false;
    eu4_border_loop_head_test_reset_tls();
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, true);
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == 0);

    eu4_border_prefix_result_t prefix;
    eu4_border_classify_batchable_prefix(
        &ctx, records, ibos, 4, entries, 4, EU4_BORDER_IBO_ONE_BIND, 0, true, true, &prefix);
    assert(prefix.run_length == 4);
    assert(prefix.eligible_draw_calls_eliminable == 3);

    g_armed = true;
    eu4_border_loop_head_epoch_reset();
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, true);
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == 3);
    eu4_border_loop_head_flush_pending();
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == 0);

    g_armed = false;
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, true);
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == 0);

    g_armed = true;
    eu4_border_loop_head_epoch_reset();
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, true);
    assert(eu4_border_loop_head_test_tls_implementable_eliminations() <=
           eu4_border_loop_head_test_tls_semantic_eliminations());

    eu4_border_loop_head_epoch_reset();
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, true);
    const uint64_t after_first = eu4_border_loop_head_test_tls_semantic_eliminations();
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 4, entries, 4, false);
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == after_first);

    enum { SUFFIX_N = 8 };
    eu4_border_record_view_t big_records[SUFFIX_N];
    uintptr_t big_ibos[SUFFIX_N];
    eu4_border_index_entry_t big_entries[SUFFIX_N];
    for (int i = 0; i < SUFFIX_N; i++) {
        big_records[i].arg3_u16_at_plus_04 = 1;
        big_records[i].triangle_count = 4;
        big_records[i].vbo_table_index = 1;
        big_ibos[i] = 100;
        big_entries[i].record_index = i;
        big_entries[i].visibility_byte = 0xFF;
        big_entries[i].color_byte = 1;
    }
    eu4_border_loop_head_epoch_reset();
    for (int hook = 0; hook < SUFFIX_N; hook++) {
        const int side_count = SUFFIX_N - hook;
        const bool walk_end = hook == SUFFIX_N - 1;
        eu4_border_loop_head_test_on_armed_hit_suffix(
            &ctx, big_records, big_ibos, SUFFIX_N, &big_entries[hook], (uint32_t)side_count, walk_end);
    }
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == (uint64_t)(SUFFIX_N - 1));
    assert(eu4_border_loop_head_test_tls_semantic_evaluations() == 1);
    assert(eu4_border_loop_head_test_tls_implementable_evaluations() == 1);

    enum { LONG_N = 200 };
    eu4_border_record_view_t long_records[LONG_N];
    uintptr_t long_ibos[LONG_N];
    eu4_border_index_entry_t long_entries[LONG_N];
    for (int i = 0; i < LONG_N; i++) {
        long_records[i].arg3_u16_at_plus_04 = 1;
        long_records[i].triangle_count = 4;
        long_records[i].vbo_table_index = 1;
        long_ibos[i] = 100;
        long_entries[i].record_index = (int16_t)i;
        long_entries[i].visibility_byte = 0xFF;
        long_entries[i].color_byte = 1;
    }
    eu4_border_loop_head_epoch_reset();
    for (int hook = 0; hook < LONG_N; hook++) {
        const int side_count = LONG_N - hook;
        const bool walk_end = hook == LONG_N - 1;
        eu4_border_loop_head_test_on_armed_hit_suffix(
            &ctx, long_records, long_ibos, LONG_N, &long_entries[hook], (uint32_t)side_count, walk_end);
    }
    assert(eu4_border_loop_head_test_tls_semantic_eliminations() == (uint64_t)(LONG_N - 1));
    assert(eu4_border_loop_head_test_tls_semantic_evaluations() == 1);
    assert(eu4_border_loop_head_test_tls_implementable_eliminations() == (uint64_t)(LONG_N - 2));
    assert(eu4_border_loop_head_test_tls_implementable_evaluations() == 2);
    return 0;
}
