#!/usr/bin/env python3
"""Native contract checks for the bounded PR D live capture backend."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"
BUILD = BENCH / ".build"
sys.path.insert(0, str(BENCH))

import eu4_gfx_live_capture as live  # noqa: E402
from oracle.function import fixture  # noqa: E402


class GfxLiveCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if platform.system() != "Darwin":
            raise unittest.SkipTest("requires macOS")
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.harness = BUILD / "gfx_live_capture_harness"
        cls.library = live.LIBRARY
        cls.probe = BUILD / "live_startup_probe"
        cls._compile(
            cls.harness,
            [
                BENCH / "eu4_gfx_capture_detour.c",
                BENCH / "eu4_gfx_capture_gateway.S",
                BENCH / "eu4_gfx_set_constant_buffer_capture.c",
                BENCH / "eu4_gfx_live_capture.c",
                TESTS / "gfx_capture_observation.S",
                TESTS / "gfx_set_constant_buffer_contract_stub.S",
                TESTS / "gfx_live_capture_harness.c",
            ],
            include=[str(BENCH), str(TESTS)],
        )
        live.build()
        cls._compile(
            cls.probe,
            [TESTS / "live_startup_probe.c"],
            include=[],
        )

    @classmethod
    def _compile(cls, output: Path, sources: list[Path],
                 include: list[str]) -> None:
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
            "-Werror", "-mno-avx", "-o", str(output),
            *(arg for path in include for arg in ("-I", path)),
            *(str(source) for source in sources),
        ]
        result = subprocess.run(command, capture_output=True, text=True,
                                check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    def _runner(self) -> list[str]:
        return []

    def _run(self, mode: str, *, read_only_captures: bool = False
             ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        root = Path(tempfile.mkdtemp(prefix=f"gfx-live-{mode}-"))
        self.addCleanup(shutil.rmtree, root, True)
        if read_only_captures:
            captures = root / "captures"
            captures.mkdir()
            captures.chmod(0o500)
            self.addCleanup(captures.chmod, 0o700)
        control = root / "control.bin"
        result = subprocess.run(
            [*self._runner(), str(self.harness), mode, str(root),
             str(control)],
            capture_output=True, text=True, check=False, timeout=90,
        )
        return root, result

    def _summary(self, root: Path) -> dict[str, str]:
        return live._parse_table(root / "summary.native",
                                 live.SUMMARY_HEADER)

    def test_admission_predicates_account_exactly(self) -> None:
        root, result = self._run("accept")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["state"], "sealed")
        self.assertEqual(summary["quiescence"], "ok")
        self.assertEqual(summary["intercepted"], "7")
        self.assertEqual(summary["not_admitted_busy"], "1")
        self.assertEqual(summary["not_admitted_slot"], "1")
        self.assertEqual(summary["not_admitted_pointer"], "1")
        self.assertEqual(summary["not_admitted_address"], "0")
        self.assertEqual(summary["admitted"], "4")
        self.assertEqual(summary["incomplete"], "0")
        self.assertEqual(summary["completed"], "4")
        self.assertEqual(summary["written"], "4")
        self.assertEqual(summary["write_failed"], "0")
        self.assertEqual(summary["undrained"], "0")
        self.assertEqual(summary["reserved"], "0")
        self.assertEqual(summary["ready"], "0")
        self.assertEqual(
            int(summary["intercepted"]),
            int(summary["admitted"]) + int(summary["not_admitted"]))
        self.assertEqual(
            int(summary["admitted"]),
            int(summary["incomplete"]) + int(summary["completed"]))
        self.assertEqual(
            int(summary["completed"]),
            int(summary["written"]) + int(summary["write_failed"]) +
            int(summary["undrained"]))
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 4)

    def test_hook_produces_live_a2_fixtures(self) -> None:
        root, result = self._run("hook")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["written"], "2")
        descriptor = fixture.load_descriptor()
        transcripts = sorted((root / "captures").glob("*.capture"))
        self.assertEqual(len(transcripts), 2)
        for path in transcripts:
            text = path.read_text(encoding="utf-8")
            parsed = fixture.parse_capture(text)
            capture = parsed["capture"]
            self.assertIs(capture["synthetic"], False)
            self.assertEqual(capture["capture"], "live-test")
            self.assertEqual(capture["epoch"], "p5a-live")
            self.assertIn(capture["scenario"],
                          {"live:length-from-buffer", "live:explicit-length"})
            materialized = fixture.materialize_capture(text, descriptor)
            self.assertEqual(materialized["manifest"]["verdict"], "qualified")
            self.assertIs(materialized["record"]["raw"]["synthetic"], False)

    def test_guard_pages_become_incomplete_not_crash(self) -> None:
        root, result = self._run("guard")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["admitted"], "2")
        self.assertEqual(summary["incomplete"], "2")
        self.assertEqual(summary["completed"], "0")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(summary["write_failed"], "0")
        self.assertEqual(summary["undrained"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

    def test_close_waits_for_reserved_capture_and_nested_exit(self) -> None:
        root, result = self._run("nested")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["state"], "sealed")
        self.assertEqual(summary["quiescence"], "ok")
        self.assertEqual(summary["admitted"], "1")
        self.assertEqual(summary["written"], "1")
        self.assertEqual(summary["incomplete"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 1)

    def test_quota_consumes_no_more_than_64_tickets(self) -> None:
        root, result = self._run("quota")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["quota"], "64")
        self.assertEqual(summary["admitted"], "64")
        self.assertEqual(summary["written"], "64")
        self.assertEqual(summary["not_admitted_quota"], "0")
        self.assertEqual(summary["reserved"], "0")
        self.assertEqual(summary["ready"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 64)
        names = {path.name for path in (root / "captures").glob("*.capture")}
        self.assertEqual(len(names), 64)

    def test_thread_table_names_capturing_threads(self) -> None:
        root, result = self._run("threads")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["threads"], "3")
        self.assertEqual(summary["admitted"], "2")
        self.assertEqual(summary["written"], "2")
        threads = set()
        for path in (root / "captures").glob("*.capture"):
            parsed = fixture.parse_capture(path.read_text(encoding="utf-8"))
            threads.add(parsed["capture"]["thread"])
        self.assertEqual(threads, {1, 2})

    def test_write_failure_is_quarantined_and_reported(self) -> None:
        root, result = self._run("quota", read_only_captures=True)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["admitted"], "64")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(summary["write_failed"], "64")
        self.assertEqual(summary["undrained"], "0")
        self.assertEqual(summary["incomplete"], "0")
        self.assertEqual(summary["completed"], "64")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

    def test_start_failure_does_not_report_a_capture_session(self) -> None:
        root, result = self._run("start-fail")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        self.assertIn("LIVE-START-FAIL started=0", result.stdout)
        self.assertFalse((root / "summary.native").exists())
        self.assertFalse((root / "session.done").exists())

    def _startup_probe(self, expected: Path, session: str) -> dict[str, str]:
        root = Path(tempfile.mkdtemp(prefix="gfx-live-startup-"))
        self.addCleanup(shutil.rmtree, root, True)
        control = root / "control.bin"
        control.write_bytes(bytes(4096))
        env = os.environ.copy()
        env.update({
            "DYLD_INSERT_LIBRARIES": str(self.library),
            "EU4_GFX_CAPTURE_DIR": str(root),
            "EU4_GFX_CAPTURE_CONTROL": str(control),
            "EU4_GFX_CAPTURE_ID": session,
            "EU4_GFX_EXPECTED_EXE": str(expected),
        })
        result = subprocess.run([*self._runner(), str(self.probe)], env=env,
                                capture_output=True, text=True, check=False,
                                timeout=30)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        return live._parse_table(root / "startup.status",
                                 live.STARTUP_HEADER)

    def test_startup_attestation_rejects_wrong_site(self) -> None:
        status = self._startup_probe(self.probe.resolve(), "startup-site")
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["session"], "startup-site")
        self.assertEqual(status["error"], "site-outside-main-text")

    def test_startup_attestation_rejects_wrong_executable(self) -> None:
        status = self._startup_probe(Path("/definitely/not/eu4"),
                                     "startup-exe")
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["error"], "expected-path-unresolved")


if __name__ == "__main__":
    unittest.main()
