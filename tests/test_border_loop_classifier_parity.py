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

from border_loop_classifier_corpus import parity_cases, write_c_parity_fixture  # noqa: E402

FIELDS = (
    "run_length",
    "batch_eligible",
    "elim",
    "termination",
    "stop_reason_mask",
    "boundary_reason_mask",
)


class BorderLoopClassifierParityTests(unittest.TestCase):
    def _build_runner(self) -> Path:
        fixture = TESTS / "border_loop_classifier_parity_cases.h"
        write_c_parity_fixture(fixture)
        out = BENCH / ".build" / "border_loop_classifier_parity"
        cmd = [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(BENCH),
            "-I",
            str(TESTS),
            "-o",
            str(out),
            str(BENCH / "border_loop_classifier.c"),
            str(TESTS / "border_loop_classifier_parity_main.c"),
        ]
        subprocess.run(cmd, check=True)
        return out

    def test_c_matches_python_all_corpus_cases(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("parity harness requires macOS clang")
        runner = self._build_runner()
        exec_cmd = [str(runner)]
        if platform.machine() != "x86_64":
            exec_cmd = ["arch", "-x86_64", *exec_cmd]
        result = subprocess.run(exec_cmd, capture_output=True, text=True, check=True)
        c_rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        by_key = {(r["case"], r["policy"]): r for r in c_rows}
        for case in parity_cases():
            py_sem, py_impl = case.run_python()
            for policy, py_row in (("semantic", py_sem), ("implementable", py_impl)):
                c_row = by_key.get((case.name, policy))
                self.assertIsNotNone(c_row, msg=f"missing C output for {case.name}/{policy}")
                for field in FIELDS:
                    self.assertEqual(
                        c_row[field],
                        py_row[field],
                        msg=f"{case.name}/{policy}/{field}",
                    )

    def test_corpus_python_table_adapter(self) -> None:
        for case in parity_cases():
            sem, impl = case.run_python()
            for row in (sem, impl):
                for field in FIELDS:
                    self.assertIn(field, row)


if __name__ == "__main__":
    unittest.main()
