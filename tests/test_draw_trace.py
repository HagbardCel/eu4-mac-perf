import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_draw_trace as draw  # noqa: E402


def row(**changes):
    value = {"frame": 1, "ordinal": 0, "caller_offset": 100,
             "immediate_offset": 200, "context": 1, "texture_sig": 3,
             "uniform_sig": 4, "render_sig": 5, "vertex_sig": 6,
             "program": 7, "array_buffer": 8, "element_buffer": 9,
             "framebuffer": 0, "mode": 4, "count": 3, "index_offset": 0,
             "base_vertex": 0, "window": 1, "api": 1, "flags": 0}
    value.update(changes)
    return value


INVENTORY = {"direct_sites": [{"return_offset": 100, "category": "mesh_object",
                               "owner": "CPdxMeshObject::RenderBuckets"}]}


class DrawTraceTests(unittest.TestCase):
    def test_contiguous_batch_candidate_and_uniform_barrier(self):
        rows = [row(), row(ordinal=1, index_offset=6),
                row(ordinal=2, index_offset=12, uniform_sig=99),
                row(ordinal=3, index_offset=12, uniform_sig=100)]
        result = draw.analyze_records(rows, INVENTORY)
        self.assertEqual(result["contiguous_index_adjacent"], 1)
        self.assertEqual(result["multi_draw_screening_adjacent"], 1)
        self.assertEqual(result["instancing_screening_adjacent"], 1)

    def test_unknown_state_and_frame_boundary_do_not_merge(self):
        rows = [row(), row(ordinal=1, flags=1), row(frame=2, ordinal=0),
                row(frame=2, ordinal=1, render_sig=8)]
        result = draw.analyze_records(rows, INVENTORY)
        self.assertEqual(result["same_state_adjacent"], 0)
        self.assertEqual(result["unsafe_draws"], 1)

    def test_base_vertex_may_vary_in_multi_draw_screen(self):
        rows = [row(), row(ordinal=1, base_vertex=18)]
        result = draw.analyze_records(rows, INVENTORY)
        self.assertEqual(result["multi_draw_screening_adjacent"], 1)
        self.assertEqual(result["contiguous_index_adjacent"], 0)
        self.assertEqual(result["instancing_screening_adjacent"], 0)

    def test_tail_jump_immediate_return_identifies_caller(self):
        rows = [row(caller_offset=0, immediate_offset=100)]
        result = draw.analyze_records(rows, INVENTORY)
        self.assertEqual(result["known_caller_draws"], 1)
        self.assertEqual(result["categories"], {"mesh_object": 1})

    def test_header_rejects_missing_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.bin"
            path.write_bytes(bytes(draw.HEADER.size))
            with self.assertRaises(Exception):
                draw.read_header(path)

    def test_site_inventory_is_pinned(self):
        source = json.loads((Path(__file__).resolve().parents[1] /
                             "analysis/draw-callers.json").read_text())
        self.assertEqual(source["executable_sha256"], draw.static.EXPECTED_SHA256)
        self.assertGreaterEqual(len(source["direct_sites"]), 40)


if __name__ == "__main__":
    unittest.main()
