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
            self.assertEqual(parsed[0]["site"], "settings")
            self.assertEqual(parsed[0]["phase"], "entry")
            self.assertEqual(parsed[1]["phase"], "exit")
            self.assertEqual(parsed[0]["call_id"], parsed[1]["call_id"])
            self.assertEqual(parsed[2]["site"], "init")
            self.assertEqual(parsed[2]["call_id"], parsed[3]["call_id"])
            self.assertEqual(parsed[3]["success"], 1)
            self.assertTrue(parsed[3]["master_non_null"])
            self.assertTrue(parsed[3]["gl_context_present"])
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["settings"], "observed")
            self.assertEqual(summary["qualifying_init"], 1)

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
