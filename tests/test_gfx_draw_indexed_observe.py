#!/usr/bin/env python3
"""Offline checks for the _GfxDrawIndexed entry observer."""

from __future__ import annotations

import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"
sys.path.insert(0, str(BENCH))

import eu4_gfx_draw_indexed_live as probe  # noqa: E402


class DrawIndexedObserveTests(unittest.TestCase):
    def test_derive_selected_uses_the_entry_rule(self) -> None:
        self.assertEqual(probe.derive_selected(3, 9), 3)
        self.assertEqual(probe.derive_selected(0, 9), 9)
        self.assertEqual(probe.derive_selected(0xFFFFFFFF, 4), 4)

    def test_classify_outcomes(self) -> None:
        self.assertEqual(probe.classify(
            startup_ok=True, ready=True, receipt_ok=True,
            post_ready=10, completed=10, refusals=0), "observed")
        self.assertEqual(probe.classify(
            startup_ok=True, ready=True, receipt_ok=True,
            post_ready=80, completed=64, refusals=0), "observed")
        self.assertEqual(probe.classify(
            startup_ok=True, ready=True, receipt_ok=True,
            post_ready=0, completed=0, refusals=0), "not-observed")
        self.assertEqual(probe.classify(
            startup_ok=True, ready=True, receipt_ok=True,
            post_ready=4, completed=4, refusals=1), "partial")
        self.assertEqual(probe.classify(
            startup_ok=True, ready=True, receipt_ok=True,
            post_ready=4, completed=2, refusals=0), "partial")
        self.assertEqual(probe.classify(
            startup_ok=False, ready=True, receipt_ok=True,
            post_ready=1, completed=1, refusals=0), "probe-failed")
        self.assertEqual(probe.classify(
            startup_ok=True, ready=False, receipt_ok=False,
            post_ready=0, completed=0, refusals=0), "probe-failed")

    def test_host_records_raw_fields_and_skips_unpublished_slots(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "gfx_draw_indexed_observe_host"
        out.parent.mkdir(parents=True, exist_ok=True)
        compiled = subprocess.run(
            [
                "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
                "-mno-avx", "-I", str(BENCH), "-o", str(out),
                str(BENCH / "eu4_gfx_draw_indexed_observe.c"),
                str(TESTS / "gfx_draw_indexed_observe_host.c"),
            ],
            capture_output=True, text=True, check=False)
        self.assertEqual(compiled.returncode, 0,
                         msg=compiled.stderr or compiled.stdout)
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        with tempfile.TemporaryDirectory() as tmp:
            samples = Path(tmp) / "samples.txt"
            result = subprocess.run(
                [*runner, str(samples)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertIn("gfx draw indexed observe: ok", result.stdout)
            text = samples.read_text(encoding="utf-8")
            self.assertNotIn("selected=", text)
            self.assertNotIn("1111111111111111", text)
            header, rows = probe._parse_samples(samples)
            self.assertEqual(header["completed"], "1")
            self.assertEqual(header["refusals"], "0")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["mode"], 4)
            self.assertEqual(rows[0]["stored_count"], 9)
            self.assertEqual(rows[0]["uniform"], 1)
            self.assertEqual(rows[0]["attrib"], 0)
            self.assertEqual(probe.derive_selected(rows[0]["rsi"], rows[0]["stored_count"]), 3)


if __name__ == "__main__":
    unittest.main()
