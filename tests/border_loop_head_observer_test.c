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

static void census_hit(
    uint32_t mode,
    uint8_t visibility_byte,
    uint16_t triangle_count,
    bool walk_end) {
    eu4_border_loop_context_t ctx = {
        .mode = mode,
        .cached_color = 1,
        .cached_vbo_index = 1,
        .skip_or_visibility_mask = 0xFF,
        .outer_batch_index = 8,
        .bound_ibo_identity = 100,
        .ibo_known = true,
        .color_known = true,
        .vbo_known = true,
    };
    eu4_border_record_view_t records[1];
    uintptr_t ibos[1] = {100};
    eu4_border_index_entry_t entries[1];
    memset(&records[0], 0, sizeof(records[0]));
    records[0].triangle_count = triangle_count;
    records[0].vbo_table_index = 1;
    entries[0].record_index = 0;
    entries[0].visibility_byte = visibility_byte;
    entries[0].color_byte = 1;
    eu4_border_loop_head_on_armed_hit(&ctx, records, ibos, 1, entries, 1, walk_end);
}

static void test_domain_census_on_armed_hit(void) {
    g_armed = true;
    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));

    census_hit(0, 0xFF, 4, false);
    eu4_border_loop_head_flush_pending();
    assert(g_counter_values[EU4_COUNTER_BORDER_LOOP_HEAD_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_STRUCTURAL_DRAWS] == 0);

    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    census_hit(1, 0xFF, 4, true);
    eu4_border_loop_head_flush_pending();
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE1_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE1_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_STRUCTURAL_DRAWS] == 0);

    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    census_hit(2, 0xFF, 4, true);
    eu4_border_loop_head_flush_pending();
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE_OTHER_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE_OTHER_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 1);

    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    census_hit(0, 0x00, 4, true);
    eu4_border_loop_head_flush_pending();
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_ENTRIES] == 0);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 0);

    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    census_hit(0, 0xFF, 0, true);
    eu4_border_loop_head_flush_pending();
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 0);

    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    census_hit(0, 0xFF, 4, false);
    census_hit(1, 0xFF, 4, false);
    census_hit(2, 0xFF, 4, false);
    census_hit(0, 0x00, 4, false);
    census_hit(0, 0xFF, 0, true);
    eu4_border_loop_head_flush_pending();
    const uint64_t loop = g_counter_values[EU4_COUNTER_BORDER_LOOP_HEAD_ENTRIES];
    const uint64_t m0 = g_counter_values[EU4_COUNTER_BORDER_MODE0_ENTRIES];
    const uint64_t m1 = g_counter_values[EU4_COUNTER_BORDER_MODE1_ENTRIES];
    const uint64_t mo = g_counter_values[EU4_COUNTER_BORDER_MODE_OTHER_ENTRIES];
    assert(loop == 5);
    assert(m0 + m1 + mo == loop);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES] ==
           g_counter_values[EU4_COUNTER_BORDER_STRUCTURAL_DRAWS]);
}

static void test_domain_census_observe_frame(void) {
    uint8_t inner_obj[0x200];
    memset(inner_obj, 0, sizeof(inner_obj));
    void *inner_ptr = inner_obj;

    uint8_t frame[0x300];
    memset(frame, 0, sizeof(frame));
    void *rbp = frame + 0x198;
    *(void **)((uintptr_t)rbp - 0x60) = &inner_ptr;
    *(uint8_t *)((uintptr_t)rbp - 0x51) = 1;
    *(int32_t *)((uintptr_t)rbp - 0x64) = 1;
    *(uint32_t *)((uintptr_t)rbp - 0xe4) = 8;

    uint8_t r12_buf[0x100];
    memset(r12_buf, 0, sizeof(r12_buf));
    void *r12 = r12_buf;
    *(uint32_t *)(r12_buf + 0x24) = 0;

    uint8_t record_bytes[28];
    memset(record_bytes, 0, sizeof(record_bytes));
    *(uint16_t *)(record_bytes + 6) = 4;
    *(uint16_t *)(record_bytes + 0x18) = 1;
    *(uintptr_t *)(r12_buf + 0x78) = (uintptr_t)record_bytes;

    uintptr_t ibo_table[1] = {100};
    *(uintptr_t *)(r12_buf + 0x60) = (uintptr_t)ibo_table;

    uint8_t step_blob[6];
    memset(step_blob, 0, sizeof(step_blob));
    step_blob[2] = 1;
    step_blob[3] = 0xFF;
    uintptr_t cursor = (uintptr_t)&step_blob[2];
    *(uintptr_t *)((uintptr_t)rbp - 0x198) = cursor + 2;

    g_armed = true;
    eu4_border_loop_head_epoch_reset();
    memset(g_counter_values, 0, sizeof(g_counter_values));
    eu4_border_loop_head_observe_frame(rbp, r12, 0xFFu, (void *)cursor);
    eu4_border_loop_head_flush_pending();

    assert(g_counter_values[EU4_COUNTER_BORDER_LOOP_HEAD_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_ENTRIES] == 1);
    assert(g_counter_values[EU4_COUNTER_BORDER_MODE0_VISIBLE_NONZERO_TRIANGLE_ENTRIES] == 1);
}

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

    test_domain_census_on_armed_hit();
    test_domain_census_observe_frame();
    return 0;
}
