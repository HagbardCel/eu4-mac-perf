"""Replay-host dependency enumeration and incremental build regressions."""

from __future__ import annotations

import hashlib
import os
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from automation import case_baseline, gfx_frame, replay_host_dependencies


class DependencyClosureTests(unittest.TestCase):
    CURRENT = replay_host_dependencies.REPLAY_SOURCES
    LEGACY = tuple(f"benchmark/{Path(path).name}" for path in CURRENT)

    def _git(self, root, *args):
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", *args],
            stderr=subprocess.PIPE, text=True).strip()

    def _baseline(self, paths):
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self._git(root, "init", "-q")
        for relative in paths:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// committed source\n")
        self._git(root, "add", ".")
        self._git(root, "commit", "--allow-empty", "-qm", "fixture baseline")
        return root, self._git(root, "rev-parse", "HEAD")

    def _replay_headers(self, root, sources):
        semantics = root / "renderer/engine/nested.h"
        semantics.parent.mkdir(parents=True, exist_ok=True)
        semantics.write_text("#define FIXTURE 1\n")
        cli = root / sources[1]
        header = cli.with_name("fixture.h")
        header.write_text(f'#include "{os.path.relpath(semantics, header.parent)}"\n')
        cli.write_text('#include "fixture.h"\n#include <stdio.h>\nint main() { return FIXTURE; }\n')
        return header, semantics

    def test_source_relative_closure_and_header_fingerprint(self):
        for sources in (self.CURRENT, self.LEGACY):
            with self.subTest(sources=sources):
                root, _pin = self._baseline(sources)
                header, semantics = self._replay_headers(root, sources)
                build = replay_host_dependencies.build_identity(root, sources, strict=True)
                semantics.write_text("#define FIXTURE 2\n")
                updated = replay_host_dependencies.build_identity(root, sources, strict=True)
                self.assertNotEqual(build["fingerprint"], updated["fingerprint"])
                with mock.patch.object(replay_host_dependencies, "_native_dependency_scan",
                                       return_value=False):
                    portable = replay_host_dependencies.relative_dependencies(root, sources, strict=True)
                self.assertEqual(updated["dependencies"], portable)
                self.assertEqual(set(portable), {
                    *sources, header.relative_to(root).as_posix(), "renderer/engine/nested.h",
                })
                (root / "benchmark").mkdir(exist_ok=True)
                (root / "benchmark/nested.h").write_text("// obsolete fallback\n")
                semantics.unlink()
                for native in {False, replay_host_dependencies._native_dependency_scan()}:
                    with mock.patch.object(replay_host_dependencies, "_native_dependency_scan",
                                           return_value=native):
                        with self.assertRaisesRegex(OSError, "unattested source include"):
                            replay_host_dependencies.dependency_paths(root, sources, strict=True)

    def test_identity_binds_layout_flags_and_compiler(self):
        dependencies = dict.fromkeys(self.CURRENT + self.LEGACY, "same closure")
        with mock.patch.object(replay_host_dependencies, "relative_dependencies",
                               return_value=dependencies), \
                mock.patch.object(replay_host_dependencies, "compiler_identity") as compiler:
            compiler.return_value = {"path": "/usr/bin/clang", "version": "one"}
            current = replay_host_dependencies.build_identity()
            legacy = replay_host_dependencies.build_identity(sources=self.LEGACY)
            unoptimized = replay_host_dependencies.build_identity(flags=("-O0",))
            compiler.return_value = {"path": "/usr/bin/clang", "version": "two"}
            upgraded = replay_host_dependencies.build_identity()
        for changed in (legacy, unoptimized, upgraded):
            self.assertNotEqual(current["fingerprint"], changed["fingerprint"])

    def test_baseline_closure_matches_build_closure(self):
        self.assertEqual(case_baseline.RENDERER_ROOTS, self.CURRENT)
        self.assertEqual(case_baseline._renderer_closure(),
                         replay_host_dependencies.relative_dependencies())

    def test_refuses_incomplete_or_ambiguous_pinned_layouts(self):
        for paths in ((), self.CURRENT[:1], self.LEGACY[:1],
                      self.CURRENT + self.LEGACY[:1], self.CURRENT + self.LEGACY):
            with self.subTest(paths=paths):
                root, pin = self._baseline(paths)
                with self.assertRaisesRegex(OSError, "missing, incomplete or ambiguous"):
                    replay_host_dependencies.candidate_sources(root, pin)

    def test_candidate_files_cannot_supply_missing_source_or_redirect_sources(self):
        for sources, extra in ((self.CURRENT, self.LEGACY), (self.LEGACY, self.CURRENT)):
            with self.subTest(sources=sources):
                root, pin = self._baseline(sources)
                source = root / sources[0]
                source.rename(root / "unselected.mm")
                for relative in extra:
                    decoy = root / relative
                    decoy.parent.mkdir(parents=True, exist_ok=True)
                    decoy.write_text("// alternate layout\n")
                selected = replay_host_dependencies.candidate_sources(root, pin)
                self.assertEqual(selected, sources)
                with self.assertRaisesRegex(OSError, "replay source is absent"):
                    replay_host_dependencies.snapshot_sources(root, root / "snapshot", selected)
                source.symlink_to(root / "unselected.mm")
                with self.assertRaisesRegex(OSError, "unattested replay source path"):
                    replay_host_dependencies.snapshot_sources(root, root / "snapshot", selected)

    @unittest.skipUnless(sys.platform == "darwin", "native replay host requires macOS")
    def test_both_pinned_layouts_build_from_their_source_snapshots(self):
        for sources in (self.CURRENT, self.LEGACY):
            with self.subTest(sources=sources):
                root, pin = self._baseline(sources)
                out = Path(self.enterContext(tempfile.TemporaryDirectory()))
                self._replay_headers(root, sources)
                selected = replay_host_dependencies.candidate_sources(root, pin)
                snapshot = out / "snapshot"
                copied = replay_host_dependencies.snapshot_sources(root, snapshot, selected)
                built = replay_host_dependencies.build_snapshot_host(
                    snapshot, out / "gfx_frame_replay", sources=selected)
                self.assertEqual(built["source_digests"], copied["digests"])
                self.assertEqual(built["source_files"], copied["files"])
                self.assertEqual(subprocess.run([built["host_path"]], check=False).returncode, 1)


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

        with mock.patch.object(gfx_frame, "replay_host_producer",
                               side_effect=producer):
            snapshot, value = gfx_frame.snapshot_replay_host(self.host)
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
        with mock.patch.object(gfx_frame, "replay_host_producer",
                               return_value=stale):
            with self.assertRaises(gfx_frame.FrameError):
                gfx_frame.snapshot_replay_host(self.host, attempts=2)


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
            mock.patch.object(gfx_frame, "ROOT", self.root),
            mock.patch.object(gfx_frame, "BENCH", self.bench),
            mock.patch.object(gfx_frame, "REPLAY_HOST_SOURCE", self.replay),
            mock.patch.object(gfx_frame, "REPLAY_HOST_CLI_SOURCE", self.cli),
            mock.patch.object(gfx_frame, "REPLAY_HOST", self.output),
            mock.patch.object(gfx_frame.subprocess, "run", side_effect=fake_run),
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
        gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.stamp.is_file())
        gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        self.semantics.write_text("changed semantics\n")
        gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)
        stamp = json.loads(self.stamp.read_text())
        self.assertEqual(stamp["schema"], "gfx-replay-host-build/v1")
        self.assertIn("benchmark/eu4_gfx_replay_semantics.h",
                      stamp["dependencies"])

    def test_producer_identity_exposes_the_source_revision_field(self):
        gfx_frame.build_replay_host()
        producer = gfx_frame.replay_host_producer()
        self.assertIn("source_revision", producer)

    def test_stamp_records_binary_digest_and_tampering_rebuilds(self):
        gfx_frame.build_replay_host()
        stamp = json.loads(self.stamp.read_text())
        digest = hashlib.sha256(self.output.read_bytes()).hexdigest()
        self.assertEqual(stamp["binary_sha256"], digest)
        self.assertEqual(len(self.calls), 1)
        self.output.write_bytes(b"tampered")
        gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            json.loads(self.stamp.read_text())["binary_sha256"],
            hashlib.sha256(self.output.read_bytes()).hexdigest())

    def test_timeout_removes_outputs_and_receipt(self):
        gfx_frame.build_replay_host()
        def timeout_run(command, **kwargs):
            if "-MM" in command:
                return self._dependency_result(command)
            raise subprocess.TimeoutExpired(cmd="clang", timeout=180)

        with mock.patch.object(
                gfx_frame.subprocess, "run", side_effect=timeout_run):
            with self.assertRaises(gfx_frame.FrameError):
                gfx_frame.build_replay_host(force=True)
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
                results.append(gfx_frame.build_replay_host())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with mock.patch.object(gfx_frame.subprocess, "run",
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
        gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 1)
        with mock.patch.object(replay_host_dependencies, "BUILD_FLAGS",
                               ("-O0",)):
            gfx_frame.build_replay_host()
        self.assertEqual(len(self.calls), 2)

    def test_failed_build_removes_binary_and_stamp(self):
        gfx_frame.build_replay_host()
        self.assertTrue(self.output.is_file())
        self.assertTrue(self.stamp.is_file())
        self.semantics.write_text("failing semantics\n")

        def failing_run(command, **kwargs):
            if "-MM" in command:
                return self._dependency_result(command)
            self.calls.append(list(command))
            return subprocess.CompletedProcess(command, 1, "", "compile failed")

        with mock.patch.object(gfx_frame.subprocess, "run",
                               side_effect=failing_run):
            with self.assertRaises(gfx_frame.FrameError):
                gfx_frame.build_replay_host(force=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.stamp.exists())


if __name__ == "__main__":
    unittest.main()
