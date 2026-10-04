#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from border_loop_classifier import (  # noqa: E402
    BarrierMask,
    IndexTableEntry,
    LoopContext,
    RecordView,
    TerminationKind,
    classify_batchable_prefix,
)

MAX_TEST_PREFIX_LEN = 32


class BorderLoopClassifierTests(unittest.TestCase):
    def _ctx(self, **kwargs) -> LoopContext:
        defaults = {
            "mode": 0,
            "cached_color": 5,
            "cached_vbo_index": 1,
            "skip_or_visibility_mask": 0x1,
        }
        defaults.update(kwargs)
        return LoopContext(**defaults)

    def _tables(self, n: int, vbo: int = 1, ibo: int = 100, varying_ibo: bool = False):
        records = [RecordView(10 + i, 4, vbo) for i in range(n)]
        ibos = [ibo + (i % 3) for i in range(n)] if varying_ibo else [ibo] * n
        return records, ibos

    def _entries(self, indices: list[int], color: int = 5, vis: int = 3) -> list[IndexTableEntry]:
        return [IndexTableEntry(i, vis, color) for i in indices]

    def test_homogeneous_prefix_of_five(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(8)
        entries = self._entries([0, 1, 2, 3, 4])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 5)
        self.assertEqual(out.eligible_draw_calls_eliminable, 4)
        self.assertTrue(out.batch_eligible)
        self.assertEqual(out.termination_kind, TerminationKind.WALK_END)

    def test_vbo_barrier_safe_prefix(self) -> None:
        ctx = self._ctx()
        records = [
            RecordView(1, 4, 1),
            RecordView(2, 4, 1),
            RecordView(3, 4, 1),
            RecordView(4, 4, 2),
        ]
        ibos = [100, 100, 100, 100]
        entries = self._entries([0, 1, 2, 3])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 3)
        self.assertTrue(out.batch_eligible)
        self.assertIn(BarrierMask.VBO_TRANSITION, out.boundary_reason_mask)

    def test_three_safe_then_skip_boundary(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(5)
        entries = [
            IndexTableEntry(0, 3, 5),
            IndexTableEntry(1, 3, 5),
            IndexTableEntry(2, 3, 5),
            IndexTableEntry(3, 0, 5),
        ]
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 3)
        self.assertTrue(out.batch_eligible)
        self.assertEqual(out.boundary_reason_mask, BarrierMask.SKIP)

    def test_three_safe_then_zero_triangles(self) -> None:
        ctx = self._ctx()
        records = [
            RecordView(1, 4, 1),
            RecordView(2, 4, 1),
            RecordView(3, 4, 1),
            RecordView(4, 0, 1),
        ]
        ibos = [100] * 4
        entries = self._entries([0, 1, 2, 3])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 3)
        self.assertTrue(out.batch_eligible)
        self.assertIn(BarrierMask.OTHER_SIDE_EFFECT, out.boundary_reason_mask)

    def test_visibility_matrix(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(3)
        for vis, run_len in ((0, 0), (2, 0), (3, 1)):
            entries = [IndexTableEntry(0, vis, 5)]
            out = classify_batchable_prefix(ctx, records, ibos, entries)
            self.assertEqual(out.run_length, run_len)

    def test_non_monotonic_indices(self) -> None:
        ctx = self._ctx()
        records = [RecordView(i, 4, 1) for i in range(20)]
        ibos = [1000 + i for i in range(20)]
        ibos[2] = ibos[7] = ibos[11] = 4242
        entries = self._entries([7, 2, 11])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 3)
        self.assertEqual(out.batch_key.ibo_identity, 4242)

    def test_ibo_boundary(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(4, ibo=10)
        ibos[3] = 99
        entries = self._entries([0, 1, 2, 3])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 3)
        self.assertIn(BarrierMask.IBO_TRANSITION, out.boundary_reason_mask)

    def test_color_and_vbo_boundary_bits(self) -> None:
        ctx = self._ctx()
        records = [
            RecordView(1, 4, 1),
            RecordView(2, 4, 1),
            RecordView(3, 4, 2),
        ]
        ibos = [100] * 3
        entries = [
            IndexTableEntry(0, 3, 5),
            IndexTableEntry(1, 3, 5),
            IndexTableEntry(2, 3, 9),
        ]
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 2)
        mask = out.boundary_reason_mask
        self.assertIn(BarrierMask.COLOR_TRANSITION, mask)
        self.assertIn(BarrierMask.VBO_TRANSITION, mask)

    def test_walk_end_terminal(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(3)
        entries = self._entries([0, 1, 2])
        out = classify_batchable_prefix(ctx, records, ibos, entries, walk_exhausted=True)
        self.assertEqual(out.run_length, 3)
        self.assertTrue(out.batch_eligible)
        self.assertEqual(out.termination_kind, TerminationKind.WALK_END)

    def test_scan_cap_not_walk_end(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(200)
        entries = self._entries(list(range(200)))
        out = classify_batchable_prefix(
            ctx,
            records,
            ibos,
            entries,
            max_scan_steps=128,
            walk_exhausted=False,
        )
        self.assertEqual(out.run_length, 128)
        self.assertEqual(out.termination_kind, TerminationKind.SCAN_CAP)
        self.assertTrue(out.batch_eligible)

    def test_oob_record_index(self) -> None:
        ctx = self._ctx()
        records, ibos = self._tables(2)
        entries = [IndexTableEntry(99, 3, 5)]
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 0)
        self.assertEqual(out.termination_kind, TerminationKind.INVALID)

    def test_zero_bind_ibo_mismatch(self) -> None:
        ctx = self._ctx(bound_ibo_identity=50, ibo_known=True)
        records, ibos = self._tables(3)
        entries = self._entries([0, 1])
        out = classify_batchable_prefix(ctx, records, ibos, entries, ibo_bind_topology="ZERO_BIND")
        self.assertEqual(out.run_length, 0)
        self.assertTrue(out.v1_fall_through_recommended)

    def test_zero_bind_ibo_unknown(self) -> None:
        ctx = self._ctx(ibo_known=False)
        records, ibos = self._tables(3)
        entries = self._entries([0, 1])
        out = classify_batchable_prefix(ctx, records, ibos, entries, ibo_bind_topology="ZERO_BIND")
        self.assertEqual(out.run_length, 0)

    def test_deferred_attrib_at_hook(self) -> None:
        ctx = self._ctx(deferred_attrib_upload_pending=True)
        records, ibos = self._tables(3)
        entries = self._entries([0, 1])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.run_length, 0)

    def test_unsupported_mode(self) -> None:
        ctx = self._ctx(mode=1)
        records, ibos = self._tables(3)
        entries = self._entries([0, 1])
        out = classify_batchable_prefix(ctx, records, ibos, entries)
        self.assertEqual(out.stop_reason_mask, BarrierMask.UNSUPPORTED_MODE)

    def test_no_fixture_length_943(self) -> None:
        self.assertLessEqual(MAX_TEST_PREFIX_LEN, 32)
        import re

        src = (ROOT / "tests" / "test_border_loop_classifier.py").read_text()
        self.assertIsNone(re.search(r"range\(\s*943\s*\)", src))
        self.assertIsNone(re.search(r"range\(\s*9\d{2}\s*\)", src))


if __name__ == "__main__":
    unittest.main()
