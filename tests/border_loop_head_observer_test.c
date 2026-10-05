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
    return 0;
}
