#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from border_loop_classifier import (  # noqa: E402
    BarrierMask,
    LoopContext,
    ResolvedStep,
    TerminationKind,
    classify_resolved_prefix,
)


class ResolvedCoreParityTests(unittest.TestCase):
    def test_invalid_resolved_valid(self) -> None:
        ctx = LoopContext(
            mode=0,
            cached_color=1,
            cached_vbo_index=1,
            skip_or_visibility_mask=0xFF,
            outer_batch_index=8,
        )
        steps = [
            ResolvedStep(0, 0xFF, 1, False),
        ]
        out = classify_resolved_prefix(ctx, steps)
        self.assertEqual(out.termination_kind, TerminationKind.INVALID)

    def test_invisible_skip_no_deref(self) -> None:
        ctx = LoopContext(mode=0, cached_color=1, cached_vbo_index=1, skip_or_visibility_mask=0x0)
        steps = [ResolvedStep(0, 0x00, 1, True)]
        out = classify_resolved_prefix(ctx, steps)
        self.assertEqual(out.stop_reason_mask, BarrierMask.SKIP)


if __name__ == "__main__":
    unittest.main()
