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
    classify_batchable_prefix,
)


class BorderLoopClassifierTests(unittest.TestCase):
    def _ctx(self, **kwargs) -> LoopContext:
        defaults = {
            "mode": 0,
            "cached_color": 5,
            "cached_vbo_index": 1,
            "current_ibo_key": 100,
            "skip_or_visibility_mask": 0x1,
        }
        defaults.update(kwargs)
        return LoopContext(**defaults)

    def test_homogeneous_prefix_of_five(self) -> None:
        ctx = self._ctx()
        records = [RecordView(10 + i, 4, 1) for i in range(5)]
        entries = [IndexTableEntry(i, False, 5) for i in range(5)]
        out = classify_batchable_prefix(ctx, records, entries)
        self.assertEqual(out.run_length, 5)
        self.assertEqual(out.eligible_draw_calls_eliminable, 4)
        self.assertTrue(out.batch_eligible)

    def test_homogeneous_prefix_of_three(self) -> None:
        ctx = self._ctx()
        records = [RecordView(i, 2, 1) for i in range(3)]
        entries = [IndexTableEntry(i, False, 5) for i in range(3)]
        out = classify_batchable_prefix(ctx, records, entries)
        self.assertEqual(out.run_length, 3)
        self.assertEqual(out.eligible_draw_calls_eliminable, 2)

    def test_vbo_barrier_ends_prefix_at_three(self) -> None:
        ctx = self._ctx()
        records = [
            RecordView(1, 4, 1),
            RecordView(2, 4, 1),
            RecordView(3, 4, 1),
            RecordView(4, 4, 2),
        ]
        entries = [IndexTableEntry(i, False, 5) for i in range(4)]
        out = classify_batchable_prefix(ctx, records, entries)
        self.assertEqual(out.run_length, 3)
        self.assertIn(BarrierMask.VBO_TRANSITION, out.stop_reason_mask)

    def test_skip_on_first_record_is_batch_boundary(self) -> None:
        ctx = self._ctx()
        records = [RecordView(1, 4, 1)]
        entries = [IndexTableEntry(0, True, 5)]
        out = classify_batchable_prefix(ctx, records, entries)
        self.assertEqual(out.run_length, 0)
        self.assertEqual(out.stop_reason_mask, BarrierMask.SKIP)

    def test_entry_color_mismatch_fall_through(self) -> None:
        ctx = self._ctx(cached_color=9)
        records = [RecordView(1, 4, 1), RecordView(2, 4, 1)]
        entries = [IndexTableEntry(0, False, 5), IndexTableEntry(1, False, 5)]
        out = classify_batchable_prefix(ctx, records, entries)
        self.assertEqual(out.run_length, 0)
        self.assertTrue(out.v1_fall_through_recommended)

    def test_no_fixture_length_943(self) -> None:
        self.assertNotIn(943, [3, 5, 17])


if __name__ == "__main__":
    unittest.main()
