"""Replay-host dependency enumeration and incremental build regressions."""

from __future__ import annotations

import hashlib
import os
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
        metal = self.root / "renderer/metal"
        engine = self.root / "renderer/engine"
        metal.mkdir(parents=True)
        engine.mkdir()
        self.semantics = engine / "nested.h"
        self.semantics.write_text("// engine semantics\n")
        header = metal / "eu4_gfx_metal_replay.h"
        header.write_text('#include "../engine/nested.h"\n')
        for relative in replay_host_dependencies.REPLAY_SOURCES:
            (self.root / relative).write_text(
                '#include "eu4_gfx_metal_replay.h"\n#include <stdio.h>\n')

    def tearDown(self):
        self.temporary.cleanup()

    def test_closure_includes_headers_and_ignores_system_includes(self):
        native = replay_host_dependencies.dependency_paths(self.root, strict=True)
        with mock.patch.object(replay_host_dependencies, "_native_dependency_scan",
                               return_value=False):
            portable = replay_host_dependencies.dependency_paths(self.root, strict=True)
            (self.root / "benchmark").mkdir()
            (self.root / "benchmark/nested.h").write_text("// obsolete path\n")
            self.semantics.unlink()
            with self.assertRaisesRegex(OSError, "unattested source include"):
                replay_host_dependencies.dependency_paths(self.root, strict=True)
        self.assertEqual(native, portable)
        self.assertEqual(set(portable), {
            *replay_host_dependencies.REPLAY_SOURCES,
            "renderer/metal/eu4_gfx_metal_replay.h", "renderer/engine/nested.h",
        })
        with self.assertRaisesRegex(OSError, "unattested source include"):
            replay_host_dependencies.dependency_paths(self.root, strict=True)

    def test_header_change_changes_the_fingerprint(self):
        first = replay_host_dependencies.build_identity(self.root)
        self.semantics.write_text("// changed\n")
        second = replay_host_dependencies.build_identity(self.root)
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_build_identity_accounts_for_flags_and_compiler(self):
        with mock.patch.object(
                replay_host_dependencies, "compiler_identity",
                return_value={"path": "/usr/bin/clang", "version": "one"}):
            first = replay_host_dependencies.build_identity(
                self.root)
            second = replay_host_dependencies.build_identity(
                self.root, flags=("-O0",))
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        with mock.patch.object(
                replay_host_dependencies, "compiler_identity",
                return_value={"path": "/usr/bin/clang", "version": "two"}):
            third = replay_host_dependencies.build_identity(
                self.root)
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


class BuildEnvironmentTests(unittest.TestCase):
    def test_include_and_loader_injection_variables_are_dropped(self):
        injected = {
            "CPATH": "/tmp/injected",
            "CPLUS_INCLUDE_PATH": "/tmp/injected",
            "OBJC_INCLUDE_PATH": "/tmp/injected",
            "DYLD_INSERT_LIBRARIES": "/tmp/injected.dylib",
            "LD_PRELOAD": "/tmp/injected.so",
            "PATH": "/usr/bin:/bin",
        }
        with mock.patch.dict(os.environ, injected, clear=False):
            environment = replay_host_dependencies.build_environment()
        self.assertEqual(environment.get("PATH"), "/usr/bin:/bin")
        for name in ("CPATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
                     "DYLD_INSERT_LIBRARIES", "LD_PRELOAD"):
            self.assertNotIn(name, environment)


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
            if "-MM" in command:
                return self._dependency_result(command)
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

    def _dependency_result(self, command):
        source = Path(command[-1])
        dependencies = [source]
        for line in source.read_text().splitlines():
            if '"' in line and ("include" in line or "import" in line):
                name = line.split('"')[1]
                candidate = source.parent / name
                if candidate.is_file():
                    dependencies.append(candidate)
        return subprocess.CompletedProcess(
            command, 0,
            "dep: " + " ".join(str(item) for item in dependencies) + "\n",
            "")

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

    def test_producer_identity_exposes_the_source_revision_field(self):
        m6_frame.build_replay_host()
        producer = m6_frame.replay_host_producer()
        self.assertIn("source_revision", producer)

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
        def timeout_run(command, **kwargs):
            if "-MM" in command:
                return self._dependency_result(command)
            raise subprocess.TimeoutExpired(cmd="clang", timeout=180)

        with mock.patch.object(
                m6_frame.subprocess, "run", side_effect=timeout_run):
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.build_replay_host(force=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stamp.exists())
        self.assertFalse(list(self.output.parent.glob(".*.tmp-*")))

    def test_overlapping_builds_serialize_under_one_lock(self):
        self.calls.clear()

        def slow_run(command, **kwargs):
            if "-MM" in command:
                return self._dependency_result(command)
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
            if "-MM" in command:
                return self._dependency_result(command)
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
