import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_diagnostic as diagnostic  # noqa: E402


class DiagnosticTests(unittest.TestCase):
    def test_root_cause_report_handles_missing_optional_power_and_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "telemetry.csv").write_text(
                "S,120,1,87,0,20\n"
                "F,120,42,glBindTexture,100,70,8000,2\n"
                "D,120,42,10,8,7,7,8,3,2,10,6\n"
                "P,120,42,3,2,10,6\n"
                "C,120,42,glBindTexture,eu4,0x123,2\n"
                "W,120,usleep,actual,2,5\n")
            phases = [{"name": "paused_idle", "start_ns": 100, "end_ns": 200}]
            report = diagnostic.write_report(root, phases,
                                             {"monotonic_ns": 100, "wall_ns": 0})
            self.assertEqual(report["phases"]["paused_idle"]["passes"]["3"]["draws"], 10)
            self.assertEqual(report["screen_images"]["status"], "unavailable")
            self.assertIn("Candidate ranking", (root / "root-cause.md").read_text())
            self.assertEqual(json.loads((root / "root-cause.json").read_text())["phases"]["paused_idle"]["snapshot_count"], 1)

    def test_phase_parser_keeps_counts_and_overflow_separate(self):
        phases = [{"name": "paused_idle", "start_ns": 100, "end_ns": 200}]
        rows = [
            ["S", "120", "1", "87", "0"],
            ["F", "120", "42", "glBindTexture", "100", "70", "8000", "2"],
            ["D", "120", "42", "10", "8", "7", "7", "8", "3", "2", "10", "6"],
            ["P", "120", "42", "3", "2", "10", "6"],
            ["V", "120", "42", "90"],
            ["R", "120", "42", "4", "10"],
            ["Z", "120", "42", "2", "10"],
            ["W", "120", "usleep", "requested", "2", "5"],
            ["Q", "120", "0", "1", "0"],
            ["U", "120", "glUnexpected", "1"],
            ["F", "220", "42", "glBindTexture", "1000", "0", "0", "0"],
        ]
        item = diagnostic.summarize_telemetry(rows, phases)["paused_idle"]
        self.assertEqual(item["calls"]["glBindTexture"], 100)
        self.assertEqual(item["redundant"]["glBindTexture"], 70)
        self.assertEqual(item["passes"]["3"]["repeated_draws"], 6)
        self.assertEqual(item["draw_modes"]["4"], 10)
        self.assertEqual(item["draw_sizes"]["2"], 10)
        self.assertEqual(item["submitted_vertices"], 90)
        self.assertEqual(item["wait_buckets"][("usleep", "requested", 2)], 5)
        self.assertEqual(item["overflow"]["shadow_state"], 1)
        self.assertEqual(item["unknown_gl"]["glUnexpected"], 1)

    def test_powermetrics_naive_utc_timestamp_aligns_with_monotonic_phase(self):
        wall = dt.datetime(2026, 9, 27, 6, 42, 14, tzinfo=dt.timezone.utc)
        anchor = {"wall_ns": int(wall.timestamp()*1e9), "monotonic_ns": 1_000_000_000}
        phases = [{"name": "paused_idle", "start_ns": 1_000_000_000,
                   "end_ns": 4_000_000_000}]
        raw = [{"timestamp": dt.datetime(2026, 9, 27, 6, 42, 15),
                "elapsed_ns": 1_000_000_000,
                "processor": {"cpu_power": 8000, "gpu_power": 1500,
                              "clusters": [{"name": "P0-Cluster", "freq_hz": 2_000_000_000}]},
                "gpu": {"freq_hz": 400, "idle_ratio": .5}}]
        item = diagnostic.summarize_power(raw, phases, anchor)["paused_idle"]
        self.assertEqual(item["samples"], 1)
        self.assertEqual(item["cpu_w"], 8)
        self.assertEqual(item["cpu_P0-Cluster_mhz"], 2000)
        self.assertEqual(item["gpu_mhz"], 400)

    def test_telemetry_wall_anchor_aligns_different_clock_epochs(self):
        anchor = {"wall_ns": 1_000_000_000_000, "monotonic_ns": 500_000_000_000}
        phases = [{"name": "paused_idle", "start_ns": 500_000_000_000,
                   "end_ns": 502_000_000_000}]
        c_clock = 900_000_000_000_000
        rows = [["S", str(c_clock), "1", "87", "0", "1000000000000"],
                ["F", str(c_clock+1_000_000_000), "42", "glDrawArrays", "100", "0", "0", "0"]]
        item = diagnostic.summarize_telemetry(rows, phases, anchor)["paused_idle"]
        self.assertEqual(item["frames"], [87])
        self.assertEqual(item["calls"]["glDrawArrays"], 100)

    def test_phase_boundary_drops_snapshot_containing_previous_mode(self):
        phase = [{"name": "paused_idle", "start_ns": 10_000_000_000,
                  "end_ns": 15_000_000_000}]
        rows = [["F", "11000000000", "42", "glBindTexture", "100", "0", "900", "10"],
                ["F", "12000000000", "42", "glBindTexture", "200", "0", "0", "0"]]
        item = diagnostic.summarize_telemetry(rows, phase)["paused_idle"]
        self.assertEqual(item["calls"]["glBindTexture"], 200)
        self.assertEqual(item["timed_samples"]["glBindTexture"], 0)

    def test_candidate_ranking_uses_per_function_redundancy_and_reports_uniforms(self):
        from collections import Counter
        telemetry = {"paused_idle": {
            "seconds": 1, "frames": [100], "draws": 0, "pass_draws": 0,
            "calls": Counter({"glBindTexture": 100, "glUseProgram": 100,
                              "glUniform1f": 50}),
            "redundant": Counter({"glBindTexture": 100, "glUniform1f": 40}),
            "timed_samples": Counter({"glBindTexture": 10, "glUseProgram": 10}),
            "timed_ns": Counter({"glBindTexture": 1_000_000,
                                 "glUseProgram": 10_000_000}),
            "overflow": Counter(), "unknown_gl": Counter()}}
        ranking = diagnostic.candidate_ranking(telemetry)
        state = next(item for item in ranking if item["family"] == "GL state suppression")
        uniform = next(item for item in ranking if item["family"] == "uniform update suppression")
        self.assertIn("0.10 ms/frame", state["reason"])
        self.assertEqual(uniform["signal"], .8)


if __name__ == "__main__":
    unittest.main()
