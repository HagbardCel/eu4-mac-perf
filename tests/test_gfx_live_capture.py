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

    def _run(self, mode: str, *, read_only_captures: bool = False,
             max_unique: int | None = None
             ) -> tuple[Path, subprocess.CompletedProcess[str]]:
        root = Path(tempfile.mkdtemp(prefix=f"gfx-live-{mode}-"))
        self.addCleanup(shutil.rmtree, root, True)
        if read_only_captures:
            captures = root / "captures"
            captures.mkdir()
            captures.chmod(0o500)
            self.addCleanup(captures.chmod, 0o700)
        control = root / "control.bin"
        env = os.environ.copy()
        if max_unique is not None:
            env["EU4_GFX_TEST_MAX_UNIQUE"] = str(max_unique)
        result = subprocess.run(
            [*self._runner(), str(self.harness), mode, str(root),
             str(control)],
            capture_output=True, text=True, check=False, timeout=90,
            env=env,
        )
        return root, result

    def _summary(self, root: Path) -> dict[str, str]:
        return live._parse_table(root / "summary.native", live.EXACT_SUMMARY)

    def _captures(self, root: Path) -> list[Path]:
        return list((root / "captures").glob("*.capture"))

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
        self.assertEqual(summary["complete_observations"], "4")
        self.assertEqual(summary["unique_retained"], "3")
        self.assertEqual(summary["duplicates_discarded"], "1")
        self.assertEqual(summary["incomplete_observations"], "0")
        self.assertEqual(summary["storage_failed"], "0")
        self.assertEqual(summary["undrained_observations"], "0")
        self.assertEqual(summary["buffer_refused"], "0")
        self.assertEqual(
            int(summary["intercepted"]),
            int(summary["not_admitted"]) + int(summary["buffer_refused"]) +
            int(summary["incomplete_observations"]) +
            int(summary["complete_observations"]) +
            int(summary["undrained_observations"]))
        self.assertEqual(len(self._captures(root)), 3)

    def test_hook_produces_live_a2_fixtures(self) -> None:
        root, result = self._run("hook")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["unique_retained"], "2")
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
        self.assertEqual(summary["incomplete_observations"], "2")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(summary["storage_failed"], "0")
        self.assertEqual(summary["undrained_observations"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_extent_crossing_protected_page_is_incomplete(self) -> None:
        root, result = self._run("extent-cross")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["incomplete_observations"], "1")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_unreadable_required_buffer_is_incomplete(self) -> None:
        root, result = self._run("buffer-guard")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["incomplete_observations"], "1")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_unreadable_exit_state_is_incomplete(self) -> None:
        root, result = self._run("exit-guard")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["incomplete_observations"], "1")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_thread_table_exhaustion_refuses_once(self) -> None:
        root, result = self._run("thread-full")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["threads"], "64")
        self.assertEqual(summary["not_admitted_slot"], "63")
        self.assertEqual(summary["not_admitted_thread_limit"], "1")
        self.assertEqual(summary["not_admitted"], "64")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_close_waits_for_reserved_capture_and_nested_exit(self) -> None:
        root, result = self._run("nested")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["state"], "sealed")
        self.assertEqual(summary["quiescence"], "ok")
        self.assertEqual(summary["unique_retained"], "1")
        self.assertEqual(summary["duplicates_discarded"], "0")
        self.assertEqual(summary["incomplete_observations"], "0")
        self.assertEqual(len(self._captures(root)), 1)

    def test_identical_calls_retain_one_record(self) -> None:
        root, result = self._run("identical")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["unique_retained"], "1")
        self.assertEqual(summary["duplicates_discarded"], "99")
        self.assertEqual(summary["complete_observations"], "100")
        self.assertEqual(summary["buffer_refused"], "0")
        self.assertEqual(len(self._captures(root)), 1)

    def test_distinct_pre_state_byte_retains_two(self) -> None:
        root, result = self._run("distinct-byte")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["unique_retained"], "2")
        self.assertEqual(summary["duplicates_discarded"], "0")
        self.assertEqual(len(self._captures(root)), 2)

    def test_hash_collision_does_not_discard_distinct_bytes(self) -> None:
        root, result = self._run("collide")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["unique_retained"], "2")
        self.assertEqual(summary["duplicates_discarded"], "0")
        self.assertEqual(len(self._captures(root)), 2)

    def test_max_unique_128_with_smaller_pool(self) -> None:
        root, result = self._run("many-unique", max_unique=128)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["max_unique"], "128")
        self.assertEqual(summary["unique_retained"], "128")
        self.assertEqual(summary["buffer_refused"], "0")
        self.assertEqual(len(self._captures(root)), 128)
        report = live.report(root)
        self.assertEqual(report["status"], "failed", msg=report["problems"])
        self.assertTrue(
            any("startup.status" in problem for problem in report["problems"]),
            msg=report["problems"])

    def test_in_flight_uniques_past_the_limit_are_discarded(self) -> None:
        root, result = self._run("inflight-limit", max_unique=1)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["max_unique"], "1")
        self.assertEqual(summary["unique_retained"], "1")
        self.assertEqual(summary["unique_limit_discarded"], "2")
        self.assertEqual(summary["not_admitted_closed"], "1")
        self.assertEqual(summary["state"], "sealed")
        self.assertEqual(len(self._captures(root)), 1)

    def test_full_pool_refuses_without_consuming_uniques(self) -> None:
        root, result = self._run("pool-full")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["buffer_refused"], "1")
        self.assertEqual(summary["unique_retained"], "1")
        self.assertEqual(summary["duplicates_discarded"], "63")
        self.assertEqual(summary["complete_observations"], "64")
        self.assertEqual(summary["intercepted"], "65")
        self.assertEqual(len(self._captures(root)), 1)

    def test_failed_write_then_retry_retains_once(self) -> None:
        root, result = self._run("retry-write")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["storage_failed"], "1")
        self.assertEqual(summary["unique_retained"], "1")
        self.assertEqual(summary["duplicates_discarded"], "0")
        self.assertEqual(len(self._captures(root)), 1)
        self.assertEqual(list((root / "captures").glob("*.partial")), [])

    def test_native_and_python_canonical_bytes_match(self) -> None:
        root, result = self._run("canon")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        captures = sorted(self._captures(root))
        self.assertEqual(len(captures), 2)
        scenarios = set()
        for path in captures:
            parsed = fixture.parse_capture(path.read_text(encoding="utf-8"))
            scenarios.add(parsed["capture"]["scenario"])
            sidecar = path.with_suffix(".canon")
            self.assertEqual(sidecar.read_bytes(),
                             live.canonical_observation(parsed))
        self.assertEqual(scenarios, {"live:length-from-buffer",
                                     "live:explicit-length"})

    def test_late_publication_loses_to_terminalization(self) -> None:
        root, result = self._run("late-publish")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["state"], "partial")
        self.assertEqual(summary["quiescence"], "timeout")
        self.assertEqual(summary["undrained_observations"], "1")
        self.assertEqual(summary["incomplete_observations"], "0")
        self.assertEqual(summary["complete_observations"], "0")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(len(self._captures(root)), 0)

    def test_thread_table_names_capturing_threads(self) -> None:
        root, result = self._run("threads")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["threads"], "3")
        self.assertEqual(summary["unique_retained"], "2")
        self.assertEqual(summary["complete_observations"], "2")
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
        self.assertEqual(summary["storage_failed"], "64")
        self.assertEqual(summary["unique_retained"], "0")
        self.assertEqual(summary["duplicates_discarded"], "0")
        self.assertEqual(summary["undrained_observations"], "0")
        self.assertEqual(summary["incomplete_observations"], "0")
        self.assertEqual(summary["complete_observations"], "64")
        self.assertEqual(len(self._captures(root)), 0)
        self.assertEqual(list((root / "captures").glob("*.partial")), [])

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
