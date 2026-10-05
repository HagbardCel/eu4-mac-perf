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

sys.path.insert(0, str(TESTS))

from border_loop_classifier import classify_batchable_prefix  # noqa: E402
from border_loop_classifier_corpus import build_corpus  # noqa: E402


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
        corpus = build_corpus()
        hom = next(c for c in corpus if c.name == "homogeneous_eight")
        py_sem, py_impl = hom.run_python()
        self.assertEqual(py_impl["elim"], py_sem["elim"])
        self.assertLessEqual(py_impl["run_length"], py_sem["run_length"])
        for row in cases:
            self.assertEqual(row["run_length"], py_sem["run_length"] if row["policy"] == "semantic" else py_impl["run_length"])
            self.assertEqual(row["batch_eligible"], py_sem["batch_eligible"] if row["policy"] == "semantic" else py_impl["batch_eligible"])
            self.assertEqual(row["elim"], py_sem["elim"] if row["policy"] == "semantic" else py_impl["elim"])
            self.assertEqual(row["termination"], py_sem["termination"] if row["policy"] == "semantic" else py_impl["termination"])

    def test_corpus_python_table_adapter(self) -> None:
        for case in build_corpus():
            sem, impl = case.run_python()
            self.assertIn("stop_reason_mask", sem)
            self.assertIn("boundary_reason_mask", impl)
            if case.adapter_only:
                continue
            self.assertGreaterEqual(sem["run_length"], 0)


if __name__ == "__main__":
    unittest.main()
