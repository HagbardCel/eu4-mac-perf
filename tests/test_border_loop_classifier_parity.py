#!/usr/bin/env python3
import json
import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"
sys_path = ROOT / "analysis" / "tools"
import sys

sys.path.insert(0, str(sys_path))

from border_loop_classifier import (  # noqa: E402
    BarrierMask,
    IndexTableEntry,
    LoopContext,
    RecordView,
    TerminationKind,
    classify_batchable_prefix,
)


class BorderLoopClassifierParityTests(unittest.TestCase):
    def _build_runner(self) -> Path:
        out = BENCH / ".build" / "border_loop_classifier_parity"
        cmd = ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(BENCH), "-o", str(out)]
        cmd += [
            str(BENCH / "border_loop_classifier.c"),
            str(TESTS / "border_loop_classifier_parity_main.c"),
        ]
        subprocess.run(cmd, check=True)
        return out

    def test_c_matches_python_semantic_and_implementable(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("parity harness requires macOS clang")
        runner = self._build_runner()
        exec_cmd = [str(runner)]
        if platform.machine() != "x86_64":
            exec_cmd = ["arch", "-x86_64", *exec_cmd]
        result = subprocess.run(exec_cmd, capture_output=True, text=True, check=True)
        cases = json.loads(result.stdout)
        ctx = LoopContext(
            mode=0,
            cached_color=1,
            cached_vbo_index=1,
            skip_or_visibility_mask=0xFF,
            outer_batch_index=8,
            bound_ibo_identity=100,
            ibo_known=True,
        )
        records = [RecordView(10 + i, 4, 1) for i in range(8)]
        ibos = [100] * 8
        entries = [IndexTableEntry(i, 0xFF, 1) for i in range(8)]
        py_sem = classify_batchable_prefix(ctx, records, ibos, entries, max_scan_steps=None)
        py_impl = classify_batchable_prefix(ctx, records, ibos, entries, max_scan_steps=128)
        self.assertEqual(py_impl.eligible_draw_calls_eliminable, py_sem.eligible_draw_calls_eliminable)
        self.assertLessEqual(py_impl.run_length, py_sem.run_length)
        for row in cases:
            self.assertEqual(row["run_length"], py_sem.run_length if row["policy"] == "semantic" else py_impl.run_length)
            self.assertEqual(row["batch_eligible"], py_sem.batch_eligible if row["policy"] == "semantic" else py_impl.batch_eligible)
            self.assertEqual(row["elim"], py_sem.eligible_draw_calls_eliminable if row["policy"] == "semantic" else py_impl.eligible_draw_calls_eliminable)
            self.assertEqual(row["termination"], py_sem.termination_kind.name if row["policy"] == "semantic" else py_impl.termination_kind.name)


if __name__ == "__main__":
    unittest.main()
