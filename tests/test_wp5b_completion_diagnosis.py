import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class Wp5bCompletionDiagnosisTests(unittest.TestCase):
    def test_parse_workload_harness_metrics_reads_completion_fields(self):
        stdout = (
            "elapsed_ns=1000 cpu_ns=24 draws=40 valid=1 pixel_alpha=255 "
            "preparation_wall_ns=5000 preparation_cpu_ns=100 "
            "submission_elapsed_ns=1000 submission_cpu_ns=24 "
            "post_window_drain_elapsed_ns=200 post_window_drain_cpu_ns=5 "
            "submission_plus_drain_elapsed_ns=1200 submission_plus_drain_cpu_ns=29"
        )
        metrics = model.parse_workload_harness_metrics(stdout)
        self.assertEqual(metrics["submission_plus_drain_elapsed_ns"], 1200)
        model._require_completion_timing_metrics(metrics)

    def test_require_completion_timing_rejects_mismatched_submission(self):
        metrics = model.parse_workload_harness_metrics(
            "elapsed_ns=1 cpu_ns=1 submission_elapsed_ns=2 submission_cpu_ns=1 "
            "post_window_drain_elapsed_ns=0 post_window_drain_cpu_ns=0 "
            "submission_plus_drain_elapsed_ns=2 submission_plus_drain_cpu_ns=1",
        )
        with self.assertRaises(model.base.BenchmarkError):
            model._require_completion_timing_metrics(metrics)

    def test_completion_stage_comparisons_shape(self):
        trials = [
            {
                "stages": {
                    "bare": {axis: 1000 + i for axis in model.COMPLETION_TIMING_METRICS},
                    "reference": {axis: 1100 + i for axis in model.COMPLETION_TIMING_METRICS},
                    "counters": {axis: 1050 + i for axis in model.COMPLETION_TIMING_METRICS},
                },
            }
            for i in range(7)
        ]
        comparisons = model._completion_stage_comparisons(trials)
        self.assertIn("reference_submission_plus_drain_elapsed_ns", comparisons)
        self.assertIn("counters_post_window_drain_cpu_ns", comparisons)


if __name__ == "__main__":
    unittest.main()
