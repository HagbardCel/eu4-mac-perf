#include "eu4_submission_mesh_chain.h"
#include "subrecord_equivalence.h"
#include "submission_counter_schema.h"

void eu4_submission_counter_add(eu4_submission_counter_slot_t slot, uint64_t delta);

#include <stdint.h>
#include <stdio.h>
#include <string.h>

void reset_test_counters(void);
extern uint64_t g_test_counter_values[EU4_SUBMISSION_COUNTER_COUNT];

static uint8_t parent_a[EU4_SFLUSHDATA_SIZE];
static uint8_t parent_b[EU4_SFLUSHDATA_SIZE];
static uint8_t parent_c[EU4_SFLUSHDATA_SIZE];
static uint8_t sub_1[EU4_MESH_DRAW_SUBRECORD_SIZE];
static uint8_t sub_2[EU4_MESH_DRAW_SUBRECORD_SIZE];
static uint8_t sub_3[EU4_MESH_DRAW_SUBRECORD_SIZE];
static uint8_t sub_4[EU4_MESH_DRAW_SUBRECORD_SIZE];

static eu4_buffer_bind_signature_t zero_sig;

static int expect_u64(const char *label, uint64_t got, uint64_t want) {
    if (got != want) {
        fprintf(stderr, "%s: got %llu want %llu\n", label, (unsigned long long)got, (unsigned long long)want);
        return 1;
    }
    return 0;
}

static int run_site_entry_counter_wrapper(void) {
    eu4_submission_counter_add(EU4_COUNTER_CANDIDATE_SITE_ENTRIES, 1);
    return 0;
}

static int run_full_sequence(void) {
    int err = 0;
    reset_test_counters();
    eu4_submission_reset_invocation_accounting();
    eu4_submission_reset_pair_chain();

    eu4_submission_mesh_on_renderbuckets_entry(1, true);
    run_site_entry_counter_wrapper();
    eu4_submission_mesh_on_site(1, 0, 0, parent_a, sub_1, &zero_sig, true);
    run_site_entry_counter_wrapper();
    eu4_submission_mesh_on_site(1, 0, 0, parent_b, sub_2, &zero_sig, true);
    run_site_entry_counter_wrapper();
    eu4_submission_mesh_on_site(1, 0, 0, parent_b, sub_3, &zero_sig, true);

    eu4_submission_mesh_on_renderbuckets_entry(2, true);
    run_site_entry_counter_wrapper();
    eu4_submission_mesh_on_site(2, 0, 0, parent_c, sub_4, &zero_sig, true);

    eu4_submission_mesh_on_renderbuckets_entry(3, true);

    err |= expect_u64(
        "candidate_renderbuckets_invocations",
        g_test_counter_values[EU4_COUNTER_CANDIDATE_RENDERBUCKETS_INVOCATIONS],
        3);
    err |= expect_u64(
        "candidate_nonempty_renderbuckets",
        g_test_counter_values[EU4_COUNTER_CANDIDATE_NONEMPTY_RENDERBUCKETS],
        2);
    err |= expect_u64("candidate_site_entries", g_test_counter_values[EU4_COUNTER_CANDIDATE_SITE_ENTRIES], 4);
    err |= expect_u64(
        "adjacent_within_invocation",
        g_test_counter_values[EU4_COUNTER_ADJACENT_WITHIN_INVOCATION],
        2);
    err |= expect_u64("same_parent_pairs", g_test_counter_values[EU4_COUNTER_SAME_PARENT_PAIRS], 1);
    err |= expect_u64("cross_parent_pairs", g_test_counter_values[EU4_COUNTER_CROSS_PARENT_PAIRS], 1);
    return err;
}

static int parent_change_does_not_reset_adjacency(void) {
    int err = 0;
    reset_test_counters();
    eu4_submission_reset_invocation_accounting();
    eu4_submission_reset_pair_chain();
    eu4_submission_mesh_on_renderbuckets_entry(10, true);
    eu4_submission_mesh_on_site(10, 0, 0, parent_a, sub_1, &zero_sig, true);
    eu4_submission_mesh_on_site(10, 0, 0, parent_b, sub_2, &zero_sig, true);
    err |= expect_u64(
        "cross_after_parent_change",
        g_test_counter_values[EU4_COUNTER_CROSS_PARENT_PAIRS],
        1);
    err |= expect_u64(
        "adjacent_after_parent_change",
        g_test_counter_values[EU4_COUNTER_ADJACENT_WITHIN_INVOCATION],
        1);
    return err;
}

int main(void) {
    int err = 0;
    memset(&zero_sig, 0, sizeof(zero_sig));
    err |= run_full_sequence();
    err |= parent_change_does_not_reset_adjacency();
    return err;
}
