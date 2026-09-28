import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_state_cache as cache  # noqa: E402


class StateCacheTests(unittest.TestCase):
    def test_report_recovers_complete_phases_after_postcapture_save_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.jsonl"
            events = []
            for i, (name, mode) in enumerate(cache.PHASES):
                events.extend(({"event": "phase_start", "phase": name, "mode": mode,
                                "monotonic_ns": i*20+1},
                               {"event": "phase_end", "phase": name,
                                "monotonic_ns": i*20+20}))
            path.write_text("\n".join(json.dumps(item) for item in events) + "\n")
            phases = cache.phases_from_events(path)
            self.assertEqual(len(phases), 6)
            self.assertEqual(phases[3], {"name": "c", "mode": 2,
                                          "start_ns": 61, "end_ns": 80})

    def test_parser_aligns_modes_and_drops_first_three_seconds(self):
        anchor = {"monotonic_ns": 100_000_000_000,
                  "wall_ns": 1_800_000_000_000_000_000}
        phases = [{"name": name, "mode": mode,
                   "start_ns": anchor["monotonic_ns"] + i*20_000_000_000,
                   "end_ns": anchor["monotonic_ns"] + (i+1)*20_000_000_000}
                  for i, (name, mode) in enumerate(cache.PHASES)]
        raw = []
        for i, (_, mode) in enumerate(cache.PHASES):
            for second in range(1, 20):
                elapsed = i*20_000_000_000+second*1_000_000_000
                raw.append({"clock_ns": 50_000_000_000+elapsed,
                            "wall_ns": anchor["wall_ns"]+elapsed,
                            "mode": mode, "interval_ns": 1_000_000_000,
                            "swaps": 80, "texture_forwarded": 100,
                            "texture_suppressed": 25 if mode else 0,
                            "vertex_forwarded": 100,
                            "vertex_suppressed": 20 if mode >= 2 else 0,
                            "uniform_forwarded": 100,
                            "uniform_suppressed": 10 if mode >= 3 else 0,
                            "multiple_contexts": 0, "overflow": 0,
                            "context_switches": 1 if mode else 0,
                            "other_thread_calls": 0})
        summary = cache.summarize_rows(raw, phases, anchor)
        self.assertEqual(summary["b"]["samples"], 17)
        self.assertEqual(summary["b"]["texture_suppressed"], 425)
        self.assertEqual(summary["a2"]["texture_suppressed"], 0)
        self.assertEqual(summary["d"]["uniform_suppressed"], 170)
        self.assertEqual(summary["d"]["context_switches"], 17)

    def test_report_uses_adjacent_baselines_and_quality_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            phases = [{"name": name, "mode": mode, "start_ns": 0, "end_ns": 1}
                      for name, mode in cache.PHASES]
            gl = {name: {"samples": 17, "median_swaps_s": 80,
                         "texture_suppressed": 100 if mode else 0,
                         "vertex_suppressed": 100 if mode >= 2 else 0,
                         "uniform_suppressed": 100 if mode >= 3 else 0,
                         "multiple_contexts": False, "shadow_overflow": 0,
                         "context_switches": 0, "other_thread_gl_calls": 0}
                  for name, mode in cache.PHASES}
            cpu = {"a1": 1000, "b": 900, "a2": 1005,
                   "c": 880, "a3": 1010, "d": 875}
            power = {name: {"samples": 17, "eu4_cputime_ms_per_s": value,
                            "cpu_w": value/100, "gpu_w": 2,
                            "combined_w": value/100+2}
                     for name, value in cpu.items()}
            with mock.patch.object(cache, "summarize_rows", return_value=gl), \
                 mock.patch.object(cache, "rows", return_value=[]), \
                 mock.patch.object(cache.diagnostic, "summarize_power", return_value=power), \
                 mock.patch.object(cache.base, "read_plist_stream", return_value=[]):
                report = cache.write_report(root, phases,
                                            {"monotonic_ns": 0, "wall_ns": 0}, 42, False)
            self.assertEqual(report["decision"], {"b": "promising", "c": "promising", "d": "promising"})
            self.assertEqual(report["comparison"]["b"]["eu4_cputime_ms_per_s"]["baseline"], 1002.5)
            self.assertEqual(json.loads((root/"validation.json").read_text())["decision"]["c"], "promising")


if __name__ == "__main__":
    unittest.main()
