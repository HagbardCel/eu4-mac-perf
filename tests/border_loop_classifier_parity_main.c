#include "border_loop_classifier.h"
#include "border_loop_classifier_parity_cases.h"

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

static void emit_result(const char *case_name, const char *policy, const eu4_border_prefix_result_t *r) {
    printf(
        "{\"case\":\"%s\",\"policy\":\"%s\",\"run_length\":%u,\"batch_eligible\":%s,\"elim\":%u,"
        "\"termination\":\"%s\",\"stop_reason_mask\":%u,\"boundary_reason_mask\":%u}\n",
        case_name,
        policy,
        r->run_length,
        r->batch_eligible ? "true" : "false",
        r->eligible_draw_calls_eliminable,
        term_name(r->termination_kind),
        r->stop_reason_mask,
        r->boundary_reason_mask);
}

int main(void) {
    for (uint32_t i = 0; i < EU4_BORDER_PARITY_CASE_COUNT; i++) {
        const eu4_border_parity_case_t *c = EU4_BORDER_PARITY_CASES[i];
        eu4_border_prefix_result_t semantic;
        eu4_border_prefix_result_t implementable;
        eu4_border_classify_batchable_prefix(
            &c->ctx,
            c->records,
            c->ibos,
            c->record_count,
            c->entries,
            c->side_count,
            c->ibo_topology,
            0,
            true,
            c->walk_exhausted,
            &semantic);
        eu4_border_classify_batchable_prefix(
            &c->ctx,
            c->records,
            c->ibos,
            c->record_count,
            c->entries,
            c->side_count,
            c->ibo_topology,
            EU4_BORDER_V1_SCAN_CAP,
            false,
            c->walk_exhausted,
            &implementable);
        emit_result(c->name, "semantic", &semantic);
        emit_result(c->name, "implementable", &implementable);
    }
    return 0;
}
