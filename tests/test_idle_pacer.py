import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_idle_pacer as pacer  # noqa: E402


class IdlePacerTests(unittest.TestCase):
    def test_abab_parser_aligns_wall_clock_and_drops_mode_boundaries(self):
        anchor = {"monotonic_ns": 1_000_000_000_000,
                  "wall_ns": 1_800_000_000_000_000_000}
        names_modes = (("off_1", 0), ("on_1", 1), ("off_2", 0), ("on_2", 1))
        phases = [{"name": name, "mode": mode,
                   "start_ns": anchor["monotonic_ns"] + index*25_000_000_000,
                   "end_ns": anchor["monotonic_ns"] + (index+1)*25_000_000_000}
                  for index, (name, mode) in enumerate(names_modes)]
        clock_base = 500_000_000_000
        rows = []
        for index, (_, mode) in enumerate(names_modes):
            for second in range(1, 25):
                elapsed = index*25_000_000_000+second*1_000_000_000
                rows.append({"clock_ns": clock_base+elapsed,
                             "wall_ns": anchor["wall_ns"]+elapsed,
                             "mode": mode, "interval_ns": 1_000_000_000,
                             "swaps": 60 if mode else 85,
                             "eligible": 60 if mode else 0,
                             "slept_ns": 300_000_000 if mode else 0,
                             "rejected": 0})
        summary = pacer.summarize_swaps(rows, phases, anchor)
        self.assertEqual(summary["off_1"]["snapshots"], 23)
        self.assertEqual(summary["on_1"]["snapshots"], 21)
        self.assertEqual(summary["off_2"]["median_swaps_s"], 85)
        self.assertEqual(summary["on_2"]["median_swaps_s"], 60)

    def test_report_compares_both_off_and_on_phases(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pacer.csv").write_text("S,10,1010,0,1000000000,85,0,0,0\n")
            (root / "powermetrics.pliststream").write_bytes(b"")
            phases = [{"name": name, "mode": mode, "start_ns": 0, "end_ns": 1}
                      for name, mode in (("off_1", 0), ("on_1", 1),
                                         ("off_2", 0), ("on_2", 1))]
            swaps = {"off_1": {"snapshots": 20, "median_swaps_s": 85,
                                 "eligible_swaps": 0, "slept_ms": 0, "rejected_swaps": 0},
                     "on_1": {"snapshots": 20, "median_swaps_s": 60,
                                "eligible_swaps": 500, "slept_ms": 3000, "rejected_swaps": 0},
                     "off_2": {"snapshots": 20, "median_swaps_s": 83,
                                 "eligible_swaps": 0, "slept_ms": 0, "rejected_swaps": 0},
                     "on_2": {"snapshots": 20, "median_swaps_s": 61,
                                "eligible_swaps": 520, "slept_ms": 2900, "rejected_swaps": 0}}
            power = {name: {"samples": 21, "combined_w": 10 if name.startswith("off") else 8,
                            "cpu_w": 8 if name.startswith("off") else 6.5,
                            "gpu_w": 2 if name.startswith("off") else 1.5,
                            "eu4_cputime_ms_per_s": 1300 if name.startswith("off") else 1000}
                     for name in swaps}
            with mock.patch.object(pacer, "summarize_swaps", return_value=swaps), \
                 mock.patch.object(pacer.diagnostic, "summarize_power", return_value=power), \
                 mock.patch.object(pacer.base, "read_plist_stream", return_value=[]):
                result = pacer.write_report(root, phases,
                                            {"monotonic_ns": 0, "wall_ns": 0}, 42)
            self.assertEqual(result["comparison"]["combined_w"]["relative_change_pct"], -20)
            self.assertTrue(result["quality"]["adequate_samples"])
            self.assertTrue(result["quality"]["pacing_engaged"])
            self.assertIn("OFF → ON → OFF → ON", (root / "validation.md").read_text())
            self.assertEqual(json.loads((root / "validation.json").read_text())["phases"]["on_1"]["median_swaps_s"], 60)


if __name__ == "__main__":
    unittest.main()
