#include "border_loop_classifier.h"

#include <stdbool.h>
#include <stdio.h>

static const char *term_name(eu4_border_termination_kind_t k) {
    switch (k) {
    case EU4_BORDER_TERM_BARRIER:
        return "BARRIER";
    case EU4_BORDER_TERM_WALK_END:
        return "WALK_END";
    case EU4_BORDER_TERM_SCAN_CAP:
        return "SCAN_CAP";
    case EU4_BORDER_TERM_INVALID:
        return "INVALID";
    }
    return "UNKNOWN";
}

int main(void) {
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
        .deferred_attrib_upload_pending = false,
        .secondary_upload_pending = false,
    };
    eu4_border_record_view_t records[8];
    uint32_t ibos[8];
    eu4_border_index_entry_t entries[8];
    for (int i = 0; i < 8; i++) {
        records[i].arg3_u16_at_plus_04 = (uint16_t)(10 + i);
        records[i].triangle_count = 4;
        records[i].vbo_table_index = 1;
        ibos[i] = 100;
        entries[i].record_index = i;
        entries[i].visibility_byte = 0xFF;
        entries[i].color_byte = 1;
    }

    eu4_border_prefix_result_t semantic;
    eu4_border_prefix_result_t implementable;
    eu4_border_classify_batchable_prefix(
        &ctx, records, ibos, 8, entries, 8, EU4_BORDER_IBO_ONE_BIND, 0, true, true, &semantic);
    eu4_border_classify_batchable_prefix(
        &ctx,
        records,
        ibos,
        8,
        entries,
        8,
        EU4_BORDER_IBO_ONE_BIND,
        EU4_BORDER_V1_SCAN_CAP,
        false,
        true,
        &implementable);

    printf(
        "[{\"policy\":\"semantic\",\"run_length\":%u,\"batch_eligible\":%s,\"elim\":%u,\"termination\":\"%s\"},"
        "{\"policy\":\"implementable\",\"run_length\":%u,\"batch_eligible\":%s,\"elim\":%u,\"termination\":\"%s\"}]\n",
        semantic.run_length,
        semantic.batch_eligible ? "true" : "false",
        semantic.eligible_draw_calls_eliminable,
        term_name(semantic.termination_kind),
        implementable.run_length,
        implementable.batch_eligible ? "true" : "false",
        implementable.eligible_draw_calls_eliminable,
        term_name(implementable.termination_kind));
    return 0;
}
