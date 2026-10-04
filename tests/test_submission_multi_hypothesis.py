import unittest

import submission_multi_hypothesis as mh


class SubmissionMultiHypothesisTests(unittest.TestCase):
    def test_invariants_pass_partition(self):
        delta = {
            "same_parent_pairs": 3,
            "cross_parent_pairs": 7,
            "adjacent_draw_pairs_total": 10,
            "candidate_site_entries": 25,
            "candidate_nonempty_invocations": 15,
        }
        self.assertEqual(mh.check_candidate_invariants(delta), [])

    def test_invariants_fail_partition(self):
        delta = {
            "same_parent_pairs": 1,
            "cross_parent_pairs": 2,
            "adjacent_draw_pairs_total": 10,
            "candidate_site_entries": 20,
            "candidate_nonempty_invocations": 10,
        }
        errors = mh.check_candidate_invariants(delta)
        self.assertTrue(any("partition" in e for e in errors))

    def test_evaluate_b_phase_no_invariant_errors(self):
        start = {name: 0 for name in ("same_parent_pairs", "cross_parent_pairs", "adjacent_draw_pairs_total")}
        end = dict(start)
        end.update(
            {
                "same_parent_pairs": 1,
                "cross_parent_pairs": 0,
                "adjacent_draw_pairs_total": 1,
                "candidate_site_entries": 2,
                "candidate_nonempty_invocations": 1,
                "same_parent_buffer_signature": 0,
            }
        )
        report = mh.evaluate_b_phase(start, end, measurement_paused_swaps=10, measurement_wall_seconds=5.0)
        self.assertTrue(report["harness_ok"])
        self.assertEqual(report["hypotheses"]["same_parent_buffer_signature"]["status"], "evaluated_no_recurrence")


if __name__ == "__main__":
    unittest.main()
