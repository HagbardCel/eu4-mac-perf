#!/usr/bin/env python3
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_startup_trace as startup  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "startup trace host is macOS x86_64")
class GfxStartupTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = startup.build_host()

    def _run(self, scenario: str, directory: Path) -> subprocess.CompletedProcess:
        command = [str(self.host), scenario, str(directory)]
        if platform.machine() != "x86_64":
            command = ["arch", "-x86_64", *command]
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_patch_bytes_match_pinned_windows(self):
        evidence = startup.preflight()
        self.assertEqual(evidence["settings_patch"], startup.PATCH_BYTES.hex())
        self.assertEqual(evidence["init_patch"], startup.PATCH_BYTES.hex())
        self.assertEqual(sum(startup.PATCH_LENGTHS), len(startup.PATCH_BYTES))

    def test_basic_complete_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("basic", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = (directory / "events.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(events), 4)
            parsed = [json.loads(line) for line in events]
            self.assertEqual([e["seq"] for e in parsed], [1, 2, 3, 4])
            self.assertEqual(parsed[0]["site"], "settings")
            self.assertEqual(parsed[0]["phase"], "entry")
            self.assertEqual(parsed[1]["phase"], "exit")
            self.assertEqual(parsed[0]["call_id"], parsed[1]["call_id"])
            self.assertEqual(parsed[0]["sample_count"], 1)
            self.assertEqual(parsed[0]["sample_captured"], 1)
            self.assertEqual(parsed[0]["sample_values"], [2])
            self.assertEqual(parsed[1]["sample_flags"], [1])
            self.assertEqual(parsed[2]["site"], "init")
            self.assertEqual(parsed[2]["call_id"], parsed[3]["call_id"])
            self.assertEqual(parsed[3]["success"], 1)
            self.assertTrue(parsed[3]["master_non_null"])
            self.assertTrue(parsed[3]["gl_context_present"])
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["settings"], "observed")
            self.assertEqual(summary["qualifying_init"], 1)
            self.assertTrue(summary["events_copied"])

    def test_failed_init_does_not_qualify(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("fail-init", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["qualifying_init"], 0)

    def test_drain_refuse_keeps_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("drain-refuse", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])
            self.assertGreaterEqual(summary["refused"], 1)
            self.assertEqual(summary["unmatched"], 0)

    def test_pause_drain_stays_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("pause-drain", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["unmatched"], 0)
            self.assertEqual(summary["settings_calls"], 1)

    def test_tls_full_releases_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("tls-full", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertEqual(summary["unmatched"], 0)
            self.assertEqual(summary["in_flight"], 0)
            self.assertGreaterEqual(summary["refused"], 1)

    def test_null_settings_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("null-settings", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertGreaterEqual(summary["unavailable"], 1)

    def test_oversize_samples_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("oversize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            entry = next(
                e for e in events if e["site"] == "settings" and e["phase"] == "entry"
            )
            self.assertEqual(entry["sample_count"], 65)
            self.assertEqual(entry["sample_captured"], 0)
            self.assertEqual(entry["sample_values"], [])
            self.assertEqual(entry["sample_flags"], [])

    def test_dual_finalize_same_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("dual-finalize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])

    def test_unsealed_finalize_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("unsealed-finalize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertFalse(summary["events_copied"])
            self.assertEqual(summary["reason"], "drain_timeout")
            self.assertEqual(summary["event_count"], 0)
            events = (directory / "events.jsonl").read_text().strip()
            self.assertEqual(events, "")

    def test_write_fail_no_complete_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("write-fail", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertFalse((directory / "summary.json").exists())

    def test_parallel_seq(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("parallel-seq", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            seqs = [e["seq"] for e in events]
            self.assertEqual(seqs, sorted(seqs))
            self.assertEqual(len(seqs), len(set(seqs)))

    def test_bad_bytes_refuse_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("bad-bytes", Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_unsafe_read_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("unsafe-read", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertGreaterEqual(summary["unavailable"], 1)

    def test_authorize_live_required(self):
        with self.assertRaises(startup.StartupError):
            startup.run(authorize_live=False)
