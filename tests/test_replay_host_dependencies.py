"""Replay-host dependency enumeration and incremental build regressions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from automation import case_baseline, m6_frame, replay_host_dependencies


class DependencyClosureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.bench = self.root / "benchmark"
        self.bench.mkdir()
        self.semantics = self.bench / "eu4_gfx_replay_semantics.h"
        self.nested = self.bench / "nested.h"
        self.metal = self.bench / "eu4_gfx_metal_replay.h"
        self.replay = self.bench / "eu4_gfx_frame_replay.mm"
        self.cli = self.bench / "eu4_gfx_frame_replay_cli.mm"
        self.semantics.write_text('#include "nested.h"\n')
        self.nested.write_text("nested\n")
        self.metal.write_text("metal\n")
        self.replay.write_text(
            '#include "eu4_gfx_replay_semantics.h"\n'
            '#include "eu4_gfx_metal_replay.h"\n')
        self.cli.write_text(
            '#include "eu4_gfx_metal_replay.h"\n'
            "#include <stdio.h>\n")

    def tearDown(self):
        self.temporary.cleanup()

    def _sources(self):
        return (self.replay, self.cli)

    def test_closure_includes_headers_and_ignores_system_includes(self):
        paths = replay_host_dependencies.dependency_paths(
            self.root, self._sources())
        self.assertEqual(set(paths), {
            "benchmark/eu4_gfx_frame_replay.mm",
            "benchmark/eu4_gfx_frame_replay_cli.mm",
            "benchmark/eu4_gfx_metal_replay.h",
            "benchmark/eu4_gfx_replay_semantics.h",
            "benchmark/nested.h",
        })

    def test_header_change_changes_the_fingerprint(self):
        first = replay_host_dependencies.relative_dependencies(
            self.root, self._sources())
        self.semantics.write_text('#include "nested.h"\n// changed\n')
        second = replay_host_dependencies.relative_dependencies(
            self.root, self._sources())
        self.assertNotEqual(first["benchmark/eu4_gfx_replay_semantics.h"],
                            second["benchmark/eu4_gfx_replay_semantics.h"])
        self.assertNotEqual(first, second)

    def test_build_identity_accounts_for_flags_and_compiler(self):
        with mock.patch.object(
                replay_host_dependencies, "compiler_identity",
                return_value={"path": "/usr/bin/clang", "version": "one"}):
            first = replay_host_dependencies.build_identity(
                self.root, self._sources())
            second = replay_host_dependencies.build_identity(
                self.root, self._sources(), flags=("-O0",))
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        with mock.patch.object(
                replay_host_dependencies, "compiler_identity",
                return_value={"path": "/usr/bin/clang", "version": "two"}):
            third = replay_host_dependencies.build_identity(
                self.root, self._sources())
        self.assertNotEqual(first["fingerprint"], third["fingerprint"])

    def test_baseline_closure_matches_build_closure(self):
        self.assertEqual(case_baseline.RENDERER_ROOTS,
                         replay_host_dependencies.REPLAY_SOURCES)
        # The real checkout closure is the authoritative one for both.
        self.assertEqual(case_baseline._renderer_closure(),
                         replay_host_dependencies.relative_dependencies())


class SnapshotIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.host = self.root / "gfx_frame_replay"
        self.host.write_bytes(b"binary-a")

    def tearDown(self):
        self.temporary.cleanup()

    def test_snapshot_tracks_executable_actually_run(self):
        calls = []
        first = hashlib.sha256(b"binary-a").hexdigest()
        second = hashlib.sha256(b"binary-b").hexdigest()

        def producer(path):
            calls.append(str(path))
            if len(calls) == 1:
                self.host.write_bytes(b"binary-b")
                return {"executable": str(self.host),
                        "executable_sha256": first,
                        "build_fingerprint": "a" * 64}
            return {"executable": str(self.host),
                    "executable_sha256": second,
                    "build_fingerprint": "b" * 64}

        with mock.patch.object(m6_frame, "replay_host_producer",
                               side_effect=producer):
            snapshot, value = m6_frame.snapshot_replay_host(self.host)
        try:
            self.assertEqual(value["executable_sha256"],
                             hashlib.sha256(snapshot.read_bytes()).hexdigest())
            self.assertEqual(value["executable_sha256"], second)
            self.assertEqual(len(calls), 2)
        finally:
            snapshot.unlink()

    def test_snapshot_refuses_a_stale_producer_identity(self):
        stale = {"executable": str(self.host),
                 "executable_sha256": hashlib.sha256(b"binary-a").hexdigest(),
                 "build_fingerprint": "a" * 64}
        self.host.write_bytes(b"binary-b")
        with mock.patch.object(m6_frame, "replay_host_producer",
                               return_value=stale):
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.snapshot_replay_host(self.host, attempts=2)


class IncrementalBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.bench = self.root / "benchmark"
        self.bench.mkdir()
        self.semantics = self.bench / "eu4_gfx_replay_semantics.h"
        self.semantics.write_text("first semantics\n")
        self.replay = self.bench / "eu4_gfx_frame_replay.mm"
        self.cli = self.bench / "eu4_gfx_frame_replay_cli.mm"
        self.replay.write_text('#include "eu4_gfx_replay_semantics.h"\n')
        self.cli.write_text("// cli\n")
        self.output = self.bench / ".build" / "gfx_frame_replay"
        self.stamp = Path(str(self.output) + ".deps.json")
        self.calls = []

        def fake_run(command, **kwargs):
            self.calls.append(list(command))
            output = Path(command[command.index("-o") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"binary")
            return subprocess.CompletedProcess(command, 0, "", "")

        self.patches = [
            mock.patch.object(m6_frame, "ROOT", self.root),
            mock.patch.object(m6_frame, "BENCH", self.bench),
            mock.patch.object(m6_frame, "REPLAY_HOST_SOURCE", self.replay),
            mock.patch.object(m6_frame, "REPLAY_HOST_CLI_SOURCE", self.cli),
            mock.patch.object(m6_frame, "REPLAY_HOST", self.output),
            mock.patch.object(m6_frame.subprocess, "run", side_effect=fake_run),
            mock.patch.object(
                replay_host_dependencies, "compiler_identity",
                return_value={"path": "/usr/bin/clang", "version": "test"}),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.temporary.cleanup()

    def test_header_change_triggers_rebuild_and_unchanged_does_not(self):
        m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.stamp.is_file())
        m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        self.semantics.write_text("changed semantics\n")
        m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)
        stamp = json.loads(self.stamp.read_text())
        self.assertEqual(stamp["schema"], "gfx-replay-host-build/v1")
        self.assertIn("benchmark/eu4_gfx_replay_semantics.h",
                      stamp["dependencies"])

    def test_stamp_records_binary_digest_and_tampering_rebuilds(self):
        m6_frame.build_replay_host()
        stamp = json.loads(self.stamp.read_text())
        digest = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.assertEqual(stamp["binary_sha256"], digest)
        self.assertEqual(len(self.calls), 1)
        self.output.write_bytes(b"tampered")
        m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            json.loads(self.stamp.read_text())["binary_sha256"],
            hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_timeout_removes_outputs_and_receipt(self):
        m6_frame.build_replay_host()
        with mock.patch.object(
                m6_frame.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="clang",
                                                      timeout=180)):
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.build_replay_host(force=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stamp.exists())
        self.assertFalse(list(self.output.parent.glob(".*.tmp-*")))

    def test_overlapping_builds_serialize_under_one_lock(self):
        self.calls.clear()

        def slow_run(command, **kwargs):
            self.calls.append(list(command))
            output = Path(command[command.index("-o") + 1])
            time.sleep(0.1)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"binary")
            return subprocess.CompletedProcess(command, 0, "", "")

        results = []
        errors = []

        def build():
            try:
                results.append(m6_frame.build_replay_host())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=slow_run):
            threads = [threading.Thread(target=build) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.calls), 1)

    def test_build_flag_change_triggers_rebuild(self):
        m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        with mock.patch.object(replay_host_dependencies, "BUILD_FLAGS",
                               ("-O0",)):
            m6_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)

    def test_failed_build_removes_binary_and_stamp(self):
        m6_frame.build_replay_host()
        self.assertTrue(self.output.is_file())
        self.assertTrue(self.stamp.is_file())
        self.semantics.write_text("failing semantics\n")

        def failing_run(command, **kwargs):
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 1, "", "compile failed")

        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=failing_run):
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.build_replay_host(force=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stamp.exists())


if __name__ == "__main__":
    unittest.main()
