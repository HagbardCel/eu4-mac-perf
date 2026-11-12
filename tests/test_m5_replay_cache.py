"""Replay-host cache invalidation includes local project headers."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import m5_replay  # noqa: E402


class ReplayHostCacheTests(unittest.TestCase):
    def test_project_headers_are_cache_dependencies(self) -> None:
        self.assertTrue(m5_replay.HEADERS)
        for header in m5_replay.BENCH.glob("*.h"):
            self.assertIn(header, m5_replay.HEADERS)

    def test_header_change_invalidates_the_cached_host(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m5-cache-") as tmp:
            root = Path(tmp)
            host = root / "gfx_m5_replay_host"
            header = root / "eu4_gfx_test.h"
            header.write_text("/* header */\n")
            calls: list[list[str]] = []

            def fake_run(command, **_kwargs):
                calls.append(list(command))
                output = Path(command[command.index("-o") + 1])
                output.write_bytes(b"binary")
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(m5_replay, "HOST", host), \
                    mock.patch.object(m5_replay, "HEADERS", [header]), \
                    mock.patch.object(m5_replay.subprocess, "run", side_effect=fake_run):
                m5_replay.build_host(force=True)
                self.assertEqual(len(calls), 1)
                m5_replay.build_host()
                self.assertEqual(len(calls), 1, "unchanged dependencies must reuse the host")
                future = time.time() + 10
                os.utime(header, (future, future))
                m5_replay.build_host()
                self.assertEqual(len(calls), 2, "a changed project header must rebuild")
                m5_replay.build_host(force=True)
                self.assertEqual(len(calls), 3, "force must always rebuild")


if __name__ == "__main__":
    unittest.main()
