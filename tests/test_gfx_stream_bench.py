"""Draw-stream backend benchmark (PR P0/P2/P3 structural counters).

The host is the same native live-draw host used by the GPU integration suite;
this test only exercises its synthetic ``--bench`` mode. It is a backend
overhead harness, not a gameplay benchmark.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "benchmark" / "eu4_gfx_stream_bench.py"


def load_benchmark():
    spec = importlib.util.spec_from_file_location("eu4_gfx_stream_bench", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(sys.platform == "darwin", "Metal draw-stream benchmark runs on macOS")
class GfxStreamBenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bench = load_benchmark()

    def test_percentile_nearest_rank(self) -> None:
        values = [float(value) for value in range(1, 101)]
        self.assertEqual(self.bench.percentile(values, 0.5), 50.0)
        self.assertEqual(self.bench.percentile(values, 0.95), 95.0)
        self.assertEqual(self.bench.percentile([7.0], 0.95), 7.0)

    def test_simple_stream_counters(self) -> None:
        measured = self.bench.run_kind("simple", 40, 2, force_build=True)
        summary = measured["summary"]
        self.assertEqual(summary["samples"], 2)
        perf = summary["counters"]
        # Every draw is encoded, in draw order.
        self.assertEqual(perf["draws_encoded"]["median"], 40)
        self.assertEqual(perf["draws_simple"]["median"], 40)
        # Baseline submission model: one draw-stream command buffer per draw,
        # plus the clear and present submissions.
        self.assertGreaterEqual(perf["command_buffers_created"]["median"], 40)
        self.assertEqual(perf["render_encoders_created"]["median"], 40)
        self.assertEqual(perf["index_temp_allocs"]["median"], 0)
        # Fully warmed frames do not compile pipelines.
        self.assertEqual(perf["pipeline_misses"]["median"], 0)
        self.assertGreater(summary["cpu_encode_ns"]["median"], 0)
        self.assertGreaterEqual(summary["wall_ns"]["median"], summary["cpu_encode_ns"]["median"])

    def test_indexed_stream_reuses_engine_buffer(self) -> None:
        measured = self.bench.run_kind("indexed", 40, 2)
        summary = measured["summary"]
        perf = summary["counters"]
        self.assertEqual(perf["draws_encoded"]["median"], 40)
        self.assertEqual(perf["draws_generic"]["median"], 40)
        # The optimized path binds the engine's Metal index buffer directly:
        # no temporary allocation and no CPU copy per draw.
        self.assertEqual(perf["index_temp_allocs"]["median"], 0)
        self.assertEqual(perf["index_temp_bytes"]["median"], 0)

    def test_mixed_state_changes_within_stream(self) -> None:
        measured = self.bench.run_kind("mixed", 60, 2)
        perf = measured["summary"]["counters"]
        self.assertEqual(perf["draws_encoded"]["median"], 60)
        self.assertEqual(perf["pipeline_misses"]["median"], 0)


if __name__ == "__main__":
    unittest.main()
