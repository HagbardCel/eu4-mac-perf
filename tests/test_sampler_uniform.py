import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_sampler_uniform as sampler  # noqa: E402


class SamplerUniformTests(unittest.TestCase):
    def test_counter_rows_reject_partial_and_inconsistent_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sampler.csv"
            path.write_text("S,1,2,3,4,5\n"
                            "U,1,2,1000000000,1,100,10,90,0\n"
                            "U,2,3,1000000000,1,100,10,80,0\n"
                            "U,3,4,1000000000,0,100,100\n")
            observed = sampler.rows(path)
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["suppressed"], 90)

    def test_paired_report_distinguishes_gain_and_bad_quality(self):
        phases = {}
        for name, mode in sampler.PHASES:
            phases[name] = {"eu4_cputime_ms_per_s": 1000.0,
                            "combined_w": 10.0, "median_swaps_s": 52.0,
                            "attempted": 200_000,
                            "suppressed": 0 if mode == 0 else 150_000,
                            "unsafe": 0}
        phases["u1"]["eu4_cputime_ms_per_s"] = 940.0
        phases["u2"]["eu4_cputime_ms_per_s"] = 940.0
        result = sampler.report(phases, visual_ok=True)
        self.assertEqual(result["decision"], "strong_positive")
        self.assertEqual(result["paired_change_pct"]["u1"]["eu4_cputime_ms_per_s"], -6.0)
        phases["u2"]["unsafe"] = 1
        self.assertEqual(sampler.report(phases, visual_ok=True)["decision"], "inconclusive")
        phases["u2"]["unsafe"] = 0
        self.assertEqual(sampler.report(phases)["decision"], "inconclusive")

    def test_no_suppression_with_expected_calls_is_negative(self):
        phases = {name: {"eu4_cputime_ms_per_s": 1000.0,
                         "combined_w": 10.0, "median_swaps_s": 52.0,
                         "attempted": 200_000, "suppressed": 0, "unsafe": 0}
                  for name, _ in sampler.PHASES}
        self.assertEqual(sampler.report(phases, visual_ok=True)["decision"],
                         "no_major_benefit")

    def test_phase_samples_use_only_full_settled_paused_buckets(self):
        phases = []
        swap_rows, uniform_rows, power = [], [], {}
        for index, (name, mode) in enumerate(sampler.PHASES):
            start = index*30_000_000_000
            phases.append({"name": name, "mode": mode,
                           "start_ns": start, "end_ns": start+30_000_000_000})
            power[name] = {"samples": 22, "eu4_cputime_ms_per_s": 1000,
                           "combined_w": 10, "cpu_w": 8, "gpu_w": 2}
            for second in range(1, 29):
                when = start+second*1_000_000_000
                swap_rows.append({"monotonic_ns": when, "interval_ns": 1_000_000_000,
                                  "swaps_s": 52, "swaps": 52, "paused_swaps": 52})
                uniform_rows.append({"wall_ns": when, "interval_ns": 1_000_000_000,
                                     "mode": mode, "attempted": 100_000,
                                     "forwarded": 50_000 if mode else 100_000,
                                     "suppressed": 50_000 if mode else 0,
                                     "unsafe": 0})
        with patch.object(sampler.auto, "probe_rows", return_value=swap_rows), \
             patch.object(sampler, "rows", return_value=uniform_rows), \
             patch.object(sampler.diagnostic, "summarize_power", return_value=power):
            result = sampler.phase_samples(Path("unused"), phases,
                                           {"monotonic_ns": 0, "wall_ns": 0}, [], 42)
        self.assertEqual(result["u1"]["swap_samples"], 23)
        self.assertEqual(result["u1"]["suppressed"], 1_150_000)
        self.assertEqual(result["a1"]["suppressed"], 0)


if __name__ == "__main__":
    unittest.main()
