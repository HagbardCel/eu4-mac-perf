import unittest

import submission_multi_hypothesis as mh


def _frozen_meta(**extra: int) -> dict[str, int]:
    base = {
        "protocol_version": 3,
        "counter_schema_version": 1,
        "counter_count": 17,
        "observation_ack_state": 2,
        "supported_hypothesis_mask": 0x0b,
        "safety_qualified_hypothesis_mask": 0,
    }
    base.update(extra)
    return base


class SubmissionMultiHypothesisTests(unittest.TestCase):
    def test_invariants_pass_partition(self):
        totals = {
            "same_parent_pairs": 3,
            "cross_parent_pairs": 7,
            "adjacent_draw_pairs_total": 10,
            "candidate_site_entries": 25,
            "candidate_nonempty_invocations": 15,
        }
        self.assertEqual(mh.check_candidate_invariants(totals), [])

    def test_invariants_fail_partition(self):
        totals = {
            "same_parent_pairs": 1,
            "cross_parent_pairs": 2,
            "adjacent_draw_pairs_total": 10,
            "candidate_site_entries": 20,
            "candidate_nonempty_invocations": 10,
        }
        errors = mh.check_candidate_invariants(totals)
        self.assertTrue(any("partition" in e for e in errors))

    def test_frozen_bank_absolute_self_contained(self):
        """ARM→FREEZE totals are read once; no live post-ARM baseline subtraction."""
        frozen = _frozen_meta(
            same_parent_pairs=1,
            cross_parent_pairs=0,
            adjacent_draw_pairs_total=1,
            candidate_site_entries=2,
            candidate_nonempty_invocations=1,
            same_parent_buffer_signature=0,
            candidate_swaps=10,
        )
        health_start = {"renderbuckets_invocations": 0, "site_entries": 0, "effective_actions": 0}
        health_end = {"renderbuckets_invocations": 2, "site_entries": 2, "effective_actions": 0}
        report = mh.evaluate_b_phase(
            frozen,
            health_start=health_start,
            health_end=health_end,
            measurement_paused_swaps=10,
            measurement_wall_seconds=5.0,
        )
        self.assertEqual(report["candidate_interpretation"], "absolute_arm_to_freeze")
        self.assertTrue(report["harness_ok"])
        self.assertEqual(report["hypotheses"]["same_parent_buffer_signature"]["status"], "evaluated_no_recurrence")

    def test_torn_delta_baseline_would_fail_but_absolute_passes(self):
        """A coherent frozen bank must not be evaluated via inconsistent candidate deltas."""
        frozen = _frozen_meta(
            same_parent_pairs=2,
            cross_parent_pairs=3,
            adjacent_draw_pairs_total=5,
            candidate_site_entries=10,
            candidate_nonempty_invocations=5,
            candidate_swaps=4,
        )
        self.assertEqual(mh.check_candidate_invariants(mh._candidate_totals(frozen)), [])
        torn_delta = mh._candidate_totals(frozen)
        torn_delta["same_parent_pairs"] = 0
        self.assertNotEqual(mh.check_candidate_invariants(torn_delta), [])
        health_start = {"renderbuckets_invocations": 0, "site_entries": 0, "effective_actions": 0}
        health_end = {"renderbuckets_invocations": 1, "site_entries": 1, "effective_actions": 0}
        self.assertTrue(
            mh.evaluate_b_phase(
                frozen,
                health_start=health_start,
                health_end=health_end,
                measurement_paused_swaps=1,
                measurement_wall_seconds=1.0,
            )["harness_ok"]
        )


if __name__ == "__main__":
    unittest.main()
