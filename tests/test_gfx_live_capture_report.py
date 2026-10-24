#!/usr/bin/env python3
"""Portable report-contract tests for the PR D live capture evidence."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402
from oracle.function import fixture, synthetic  # noqa: E402

SESSION = "gfx-live-portable"


class GfxLiveReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = fixture.load_descriptor()
        cls.descriptor_sha = base.sha256(
            ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json")
        cls.binary_sha = cls.descriptor["binary"]["sha256"]
        cls.library_sha = base.sha256(live.LIBRARY) if live.LIBRARY.is_file() else None

    def _transcript(self, thread: int, *, synthetic_bad: bool = False) -> str:
        text = synthetic.capture_text(self.descriptor, "s03")
        text = text.replace(
            "capture=re0-pilot scenario=s03:length-from-buffer epoch=synthetic-a2",
            f"capture={SESSION} scenario=live:length-from-buffer epoch=p5a-live",
        )
        text = text.replace("thread=0 sequence=3", f"thread={thread} sequence=1")
        text = text.replace("synthetic=1", "synthetic=0")
        if synthetic_bad:
            text = text.replace("synthetic=0", "synthetic=1")
        return text

    def _run(self, *, file_count: int = 64, state: str = "sealed",
             quiescence: str = "ok", write_failed: int = 0,
             undrained: int = 0, incomplete: int = 0, admitted: int | None = None,
             startup_session: str = SESSION, startup_state: str = "installed",
             metadata_status: str = "captured", metadata_error: str | None = None,
             synthetic_bad: bool = False,
             summary_overrides: dict[str, str] | None = None,
             startup_overrides: dict[str, str] | None = None,
             metadata_overrides: dict | None = None) -> Path:
        root = Path(tempfile.mkdtemp(prefix="gfx-live-report-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, True))
        captures = root / "captures"
        captures.mkdir()
        for thread in range(file_count):
            (captures / f"{SESSION}.{thread}.capture").write_text(
                self._transcript(thread, synthetic_bad=synthetic_bad
                                 and thread == 0),
                encoding="utf-8",
            )
        admitted = admitted if admitted is not None else (
            file_count + write_failed + undrained + incomplete)
        completed = file_count + write_failed + undrained
        threads = max(admitted, 1)
        summary = {
            "session": SESSION,
            "pid": "123",
            "state": state,
            "closed_reason": "operator",
            "quiescence": quiescence,
            "started_ns": "1",
            "armed_ns": "2",
            "closed_ns": "3",
            "quota": "64",
            "threads": str(threads),
            "intercepted": str(admitted),
            "not_admitted": "0",
            "not_admitted_busy": "0",
            "not_admitted_slot": "0",
            "not_admitted_pointer": "0",
            "not_admitted_address": "0",
            "not_admitted_quota": "0",
            "not_admitted_closed": "0",
            "not_admitted_thread_limit": "0",
            "admitted": str(admitted),
            "incomplete": str(incomplete),
            "completed": str(completed),
            "written": str(file_count),
            "write_failed": str(write_failed),
            "undrained": str(undrained),
            "reserved": "0",
            "ready": "0",
        }
        summary.update(summary_overrides or {})
        startup = {
            "state": startup_state,
            "session": startup_session,
            "pid": "123",
            "executable": str(base.GOG_EXE),
            "arch": "x86_64",
            "image_base": hex(0x100000000),
            "site": hex(0x100000000 + live.TARGET_RVA),
            "module": self.binary_sha,
            "error": "" if startup_state == "installed" else "startup-failed",
        }
        startup.update(startup_overrides or {})
        metadata = {
            "status": metadata_status,
            "session_id": SESSION,
            "game_pid": 123,
            "capture_files": file_count,
            "save_sha256": "a" * 64,
            "save_after_sha256": "a" * 64,
            "descriptor_sha256": self.descriptor_sha,
            "library_sha256": self.library_sha,
        }
        if metadata_error is not None:
            metadata["error"] = metadata_error
        metadata.update(metadata_overrides or {})

        def table(header: str, values: dict[str, str]) -> str:
            return "\n".join([header, *(f"{key}={value}" for key, value in values.items()),
                              "END"]) + "\n"

        (root / "summary.native").write_text(
            table(live.SUMMARY_HEADER, summary), encoding="utf-8")
        (root / "startup.status").write_text(
            table(live.STARTUP_HEADER, startup), encoding="utf-8")
        (root / "session.done").write_text(
            table(live.DONE_HEADER, {"session": SESSION, "state": state}),
            encoding="utf-8")
        (root / "metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8")
        return root

    def test_complete_session_is_accepted(self) -> None:
        root = self._run()
        result = live.report(root)
        self.assertEqual(result["status"], "accepted")
        self.assertTrue(result["clean"])
        self.assertEqual(result["qualified"], 64)
        self.assertEqual(result["unqualified"], 0)
        self.assertEqual(result["malformed"], [])
        self.assertEqual(result["problems"], [])
        for name in ("session.json", "manifest.json", "summary.md"):
            self.assertTrue((root / name).is_file(), name)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["files"]), 64 + 4)

    def test_pid_consensus_is_required(self) -> None:
        def metadata_file(root: Path) -> Path:
            return root / "metadata.json"

        def load(root: Path) -> dict:
            return json.loads(metadata_file(root).read_text(encoding="utf-8"))

        def save(root: Path, value: object) -> None:
            metadata_file(root).write_text(json.dumps(value), encoding="utf-8")

        def set_pid(root: Path, value: object) -> None:
            metadata = load(root)
            metadata["game_pid"] = value
            save(root, metadata)

        def remove_pid(root: Path) -> None:
            metadata = load(root)
            del metadata["game_pid"]
            save(root, metadata)

        cases = (
            ("missing", {}, remove_pid, "game_pid"),
            ("null", {}, lambda root: set_pid(root, None), "game_pid"),
            ("non-integer", {}, lambda root: set_pid(root, "not-a-pid"),
             "game_pid"),
            ("boolean", {}, lambda root: set_pid(root, True), "game_pid"),
            ("non-object", {}, lambda root: save(root, []), "JSON object"),
            ("launch mismatch", {}, lambda root: set_pid(root, 999),
             "PIDs do not match"),
            ("startup mismatch", {"startup_overrides": {"pid": "999"}},
             lambda root: None, "PIDs do not match"),
            ("native mismatch", {"summary_overrides": {"pid": "999"}},
             lambda root: None, "PIDs do not match"),
        )
        for name, kwargs, mutate, expected in cases:
            with self.subTest(case=name):
                root = self._run(**kwargs)
                mutate(root)
                result = live.report(root)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["gates"]["pids"])
                self.assertTrue(
                    any(expected in problem for problem in result["problems"]),
                    result["problems"])

    def test_thread_table_capacity_is_enforced(self) -> None:
        cases = (
            ("zero", "0", "no registered threads"),
            ("over", "65", "exceeds the thread table"),
        )
        for name, threads, expected in cases:
            with self.subTest(case=name):
                root = self._run(summary_overrides={"threads": threads})
                result = live.report(root)
                self.assertEqual(result["status"], "failed")
                self.assertFalse(result["gates"]["numeric_invariants"])
                self.assertTrue(
                    any(expected in problem for problem in result["problems"]),
                    result["problems"])

    def test_manifest_is_created_once_and_verified(self) -> None:
        root = self._run(file_count=50)
        first = live.report(root)
        self.assertEqual(first["status"], "accepted")
        manifest_bytes = (root / "manifest.json").read_bytes()

        second = live.report(root)
        self.assertEqual(second["status"], "accepted")
        self.assertEqual((root / "manifest.json").read_bytes(), manifest_bytes)

    def test_tampered_evidence_is_refused_and_manifest_is_kept(self) -> None:
        root = self._run(file_count=50)
        first = live.report(root)
        self.assertEqual(first["status"], "accepted")
        manifest_bytes = (root / "manifest.json").read_bytes()

        capture = sorted((root / "captures").glob("*.capture"))[0]
        text = capture.read_text(encoding="utf-8")
        capture.write_text(
            text.replace("thread=0 sequence=1", "thread=0 sequence=2", 1),
            encoding="utf-8")

        second = live.report(root)
        self.assertEqual(second["status"], "failed")
        self.assertEqual(second["qualified"], 50)
        self.assertTrue(any("Existing manifest" in problem
                            for problem in second["problems"]))
        self.assertEqual((root / "manifest.json").read_bytes(), manifest_bytes)

    def test_native_formula_mismatch_is_failed(self) -> None:
        root = self._run(summary_overrides={"completed": "63"})
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("completed" in problem for problem in result["problems"]))

    def test_admitted_over_quota_is_failed(self) -> None:
        root = self._run(
            file_count=64,
            summary_overrides={
                "admitted": "65",
                "incomplete": "1",
                "intercepted": "65",
            },
        )
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["gates"]["numeric_invariants"])
        self.assertTrue(any("admitted exceeds quota" in problem
                            for problem in result["problems"]))

    def test_negative_counter_is_failed(self) -> None:
        root = self._run(
            file_count=64,
            summary_overrides={
                "not_admitted": "-1",
                "not_admitted_thread_limit": "-1",
                "intercepted": "63",
            },
        )
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["gates"]["numeric_invariants"])
        self.assertTrue(any("is negative" in problem
                            for problem in result["problems"]))

    def test_ticket_filename_outside_quota_is_malformed(self) -> None:
        root = self._run(file_count=50)
        capture = sorted((root / "captures").glob("*.capture"))[0]
        capture.rename(capture.with_name(f"{SESSION}.64.capture"))

        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["malformed"]), 1)
        self.assertTrue(any("outside 0..63" in item["error"]
                            for item in result["malformed"]))

    def test_quiescence_timeout_is_partial_not_accepted(self) -> None:
        root = self._run(state="partial", quiescence="timeout")
        result = live.report(root)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["clean"])
        self.assertTrue(any("partial diagnostic" in problem
                            for problem in result["problems"]))

    def test_zero_armed_epoch_is_failed(self) -> None:
        root = self._run(summary_overrides={"armed_ns": "0"})
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["gates"]["epoch"])
        self.assertTrue(any("epoch" in problem.lower()
                            for problem in result["problems"]))

    def test_fewer_than_50_qualified_is_insufficient(self) -> None:
        root = self._run(file_count=49)
        result = live.report(root)
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["qualified"], 49)
        self.assertFalse(result["gates"]["at_least_50"])

    def test_reported_write_loss_is_not_accepted(self) -> None:
        root = self._run(file_count=50, write_failed=14, admitted=64)
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["clean"])
        self.assertEqual(result["loss"]["write_failed"], 14)
        self.assertFalse(result["gates"]["no_loss"])
        self.assertTrue(any("lost persisted captures" in problem
                            for problem in result["problems"]))

    def test_stale_startup_session_is_failed(self) -> None:
        root = self._run(startup_session="gfx-live-other")
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("Startup session" in problem
                            for problem in result["problems"]))

    def test_startup_error_is_failed(self) -> None:
        root = self._run(startup_state="error")
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("startup failed" in problem.lower()
                            for problem in result["problems"]))

    def test_synthetic_transcript_is_malformed(self) -> None:
        root = self._run(synthetic_bad=True)
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["qualified"], 63)
        self.assertEqual(len(result["malformed"]), 1)
        self.assertTrue(any("Malformed transcript" in problem
                            for problem in result["problems"]))

    def test_incomplete_metadata_is_failed(self) -> None:
        root = self._run(metadata_status="incomplete", metadata_error="crashed")
        result = live.report(root)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("reports an error" in problem
                            for problem in result["problems"]))

    def test_zero_capture_seal_is_insufficient(self) -> None:
        root = self._run(file_count=0, admitted=0)
        result = live.report(root)
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["qualified"], 0)


if __name__ == "__main__":
    unittest.main()
