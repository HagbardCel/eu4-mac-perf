"""P4.2: the portable C11 synthetic target and its deterministic golden output.

The transcript is a function of C semantics only - fixed-width unsigned arithmetic
and explicit byte encoding - so gcc on Linux and clang on macOS must agree on it
byte-for-byte. The structural checks then prove the target actually exercises every
property the recorder will have to handle, rather than merely compiling.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from oracle.recorder import harness


class SyntheticTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="oracle-p4-synthetic-")
        cls.exe = harness.build(Path(cls._tmp) / "build")
        cls.transcript = harness.run_golden(cls.exe)
        cls.parsed = harness.parse_transcript(cls.transcript)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_reproduces_the_committed_golden_transcript(self):
        self.assertEqual(self.transcript, harness.golden_transcript())

    def test_output_is_deterministic(self):
        self.assertEqual(harness.run_golden(self.exe), self.transcript)

    def test_transcript_is_well_formed(self):
        self.assertEqual(self.parsed["version"], "v1")
        self.assertIsNotNone(self.parsed["end"])

    def test_handle_reuse_advances_the_generation(self):
        creates = [c for c in self.parsed["calls"] if c["op"] == "create"]
        self.assertEqual(len(creates), 2)
        first, second = creates
        self.assertEqual(first["slot"], second["slot"])
        self.assertEqual(int(second["gen"]), int(first["gen"]) + 1)

    def test_a_stale_handle_is_rejected(self):
        reads = [c for c in self.parsed["calls"] if c["op"] == "read"]
        self.assertIn("stale", [r["rc"] for r in reads])

    def test_aliasing_shares_one_resource(self):
        self.assertEqual(len([c for c in self.parsed["calls"] if c["op"] == "share"]), 1)
        # A write through the alias is visible to a read through the original handle.
        alias_reads = [r for r in self.parsed["calls"]
                       if r["op"] == "read" and r["offset"] == "1" and r["rc"] == "ok"]
        self.assertTrue(alias_reads)
        self.assertEqual(alias_reads[-1]["value"], "8")

    def test_equal_arguments_over_different_pre_state_differ(self):
        updates = [c for c in self.parsed["calls"] if c["op"] == "update"]
        same_arguments = [u for u in updates if u["seed"] == "5" and u["span"] == "4"]
        self.assertEqual(len(same_arguments), 2)
        byte_strings = [d["bytes"] for d in self.parsed["data"]]
        self.assertIn("05060708000000000000000000000000", byte_strings)
        self.assertIn("0e101214000000000000000000000000", byte_strings)

    def test_bounded_recursion_reports_a_value(self):
        folds = [c for c in self.parsed["calls"] if c["op"] == "fold"]
        self.assertEqual(len(folds), 1)
        self.assertTrue(folds[0]["value"].isdigit())

    def test_effect_stream_is_ordered_and_complete(self):
        effects = self.parsed["effects"]
        self.assertEqual([int(e["id"]) for e in effects], list(range(1, len(effects) + 1)))
        self.assertTrue(all(e["op"] in {"1", "2", "3", "4"} for e in effects))
        self.assertEqual(self.parsed["end"]["effects"], str(len(effects)))

    def test_counters_are_exact(self):
        end = self.parsed["end"]
        calls = self.parsed["calls"]
        self.assertEqual(end["creates"], str(len([c for c in calls if c["op"] == "create"])))
        self.assertEqual(end["destroys"], str(len([c for c in calls if c["op"] == "destroy"])))
        self.assertEqual(end["stale_reads"], "1")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
