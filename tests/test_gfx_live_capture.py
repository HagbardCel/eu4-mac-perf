#!/usr/bin/env python3
"""Native contract checks for the bounded PR D live capture backend."""

from __future__ import annotations

import hashlib
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
from oracle.certify.policy import behavior  # noqa: E402
from oracle.function import fixture  # noqa: E402

_DIAG_COUNTERS = (
    "intercepted", "invalid_refused", "pending_contended",
    "verification_contended", "screened_repeat", "selected", "busy", "slot",
    "pointer", "address", "thread_limit", "closed", "read_failed",
    "signature_read_contended", "key_insert_contended", "table_capacity_refused",
    "capture_buffer_refused", "capacity_refused_selection", "cas_exhausted",
    "first_selected", "verification_selected", "novel_retained",
    "producer_matched_repeat", "distinct_not_retained",
    "ineligible_not_established", "candidate_incomplete", "storage_failed",
    "undrained", "selected_invariant_failure", "producer_matched_discarded",
    "audit_persisted", "admission_invariant_lost", "unanchored_reclaimed",
    "p_cov", "p_div", "r", "l",
)


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
             timeout: int = 90
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
            capture_output=True, text=True, check=False, timeout=timeout,
        )
        return root, result

    def _summary(self, root: Path) -> dict[str, str]:
        return live._parse_table(root / "summary.native",
                                 live.SUMMARY_HEADER)

    def test_admission_predicates_account_exactly(self) -> None:
        root, result = self._run("accept")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        self.assertNotIn("ACQ_DIAG", result.stdout)
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

    def test_extent_crossing_protected_page_is_incomplete(self) -> None:
        root, result = self._run("extent-cross")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["admitted"], "1")
        self.assertEqual(summary["incomplete"], "1")
        self.assertEqual(summary["completed"], "0")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

    def test_unreadable_required_buffer_is_incomplete(self) -> None:
        root, result = self._run("buffer-guard")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["admitted"], "1")
        self.assertEqual(summary["incomplete"], "1")
        self.assertEqual(summary["completed"], "0")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

    def test_unreadable_exit_state_is_incomplete(self) -> None:
        root, result = self._run("exit-guard")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["admitted"], "1")
        self.assertEqual(summary["incomplete"], "1")
        self.assertEqual(summary["completed"], "0")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

    def test_thread_table_exhaustion_refuses_once(self) -> None:
        root, result = self._run("thread-full")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["threads"], "64")
        self.assertEqual(summary["not_admitted_slot"], "63")
        self.assertEqual(summary["not_admitted_thread_limit"], "1")
        self.assertEqual(summary["not_admitted"], "64")
        self.assertEqual(summary["admitted"], "0")
        self.assertEqual(summary["written"], "0")
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

    def test_quota_overflow_refuses_the_65th_invocation(self) -> None:
        root, result = self._run("quota-overflow")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["quota"], "64")
        self.assertEqual(summary["admitted"], "64")
        self.assertEqual(summary["written"], "64")
        self.assertEqual(summary["not_admitted_quota"], "1")
        self.assertEqual(summary["intercepted"], "65")
        self.assertEqual(summary["reserved"], "0")
        self.assertEqual(summary["ready"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 64)

    def test_late_publication_loses_to_terminalization(self) -> None:
        root, result = self._run("late-publish")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary(root)
        self.assertEqual(summary["state"], "partial")
        self.assertEqual(summary["quiescence"], "timeout")
        self.assertEqual(summary["admitted"], "1")
        self.assertEqual(summary["incomplete"], "1")
        self.assertEqual(summary["completed"], "0")
        self.assertEqual(summary["written"], "0")
        self.assertEqual(len(list((root / "captures").glob("*.capture"))), 0)

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


    def _summary_v2(self, root: Path) -> dict[str, str]:
        text = (root / "summary.native").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("EU4-GFX-LIVE-SUMMARY v2\n"), msg=text)
        rows: dict[str, str] = {}
        for line in text.splitlines()[1:]:
            if line == "END":
                break
            key, value = line.split("=", 1)
            rows[key] = value
        return rows

    def _diags(self, stdout: str) -> list[dict[str, str]]:
        found = []
        for line in stdout.splitlines():
            if not line.startswith("ACQ_DIAG "):
                continue
            fields = {}
            keys = []
            for part in line.split()[1:]:
                key, value = part.split("=", 1)
                fields[key] = value
                keys.append(key)
            expected_keys = ["scenario"]
            if "variant" in fields:
                expected_keys.append("variant")
            if "outcome" in fields:
                expected_keys.append("outcome")
            expected_keys.extend(_DIAG_COUNTERS)
            self.assertEqual(keys, expected_keys, msg=line)
            found.append(fields)
        return found

    def _identities(self, rows: dict[str, str]) -> None:
        def count(name: str) -> int:
            return int(rows[name])

        invalid = sum(count(name) for name in (
            "busy", "slot", "pointer", "address", "thread_limit", "closed",
            "read_failed", "signature_read_contended", "key_insert_contended",
            "table_capacity_refused", "capture_buffer_refused",
            "capacity_refused_selection", "cas_exhausted"))
        self.assertEqual(count("invalid_refused"), invalid)
        self.assertEqual(
            count("intercepted"),
            invalid + count("pending_contended") +
            count("verification_contended") + count("screened_repeat") +
            count("selected"))
        self.assertEqual(
            count("selected"),
            count("first_selected") + count("verification_selected"))
        self.assertEqual(
            count("selected"),
            count("novel_retained") + count("producer_matched_repeat") +
            count("distinct_not_retained") +
            count("ineligible_not_established") +
            count("candidate_incomplete") + count("storage_failed") +
            count("undrained") + count("selected_invariant_failure"))
        self.assertEqual(
            count("producer_matched_repeat"),
            count("producer_matched_discarded") + count("audit_persisted"))
        self.assertLessEqual(count("audit_persisted"), 8)
        self.assertLessEqual(count("novel_retained"), 64)
        self.assertLessEqual(int(rows.get("ineligible_persisted", "0")), 11)

    def _expect_diag(self, line: dict[str, str],
                     expected: dict[str, object]) -> None:
        for name in ("scenario", "variant", "outcome"):
            if name in expected:
                self.assertEqual(line[name], str(expected[name]))
            else:
                self.assertNotIn(name, line)
        for name in _DIAG_COUNTERS:
            self.assertEqual(line[name], str(expected.get(name, 0)), msg=name)
        self._identities(line)

    def _v2(self, mode: str, *, timeout: int = 90
            ) -> tuple[Path, subprocess.CompletedProcess[str], dict[str, str],
                       list[dict[str, str]]]:
        root, result = self._run(mode, timeout=timeout)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary_v2(root)
        self._identities(summary)
        return root, result, summary, self._diags(result.stdout)

    def _files(self, root: Path, marker: str) -> list[Path]:
        captures = root / "captures"
        if not captures.exists():
            return []
        chosen = []
        for path in captures.glob("*.capture"):
            audit = ".audit." in path.name
            ineligible = ".ineligible." in path.name
            if marker == "audit" and audit:
                chosen.append(path)
            elif marker == "ineligible" and ineligible:
                chosen.append(path)
            elif marker == "retained" and not audit and not ineligible:
                chosen.append(path)
        return sorted(chosen, key=lambda path: int(path.name.split(".")[1]))

    def _qualified(self, path: Path) -> dict[str, object]:
        descriptor = fixture.load_descriptor()
        materialized = fixture.materialize_capture(
            path.read_text(encoding="utf-8"), descriptor)
        record = materialized["record"]
        assessment = fixture.assess(record, descriptor)
        self.assertEqual(materialized["manifest"]["verdict"], "qualified",
                         msg=path.name)
        self.assertEqual(assessment["missing"], [])
        self.assertEqual(record["completeness"]["losses"], 0)
        return {"record": record, "descriptor": descriptor,
                "behavior": " ".join(behavior(record, descriptor).key)}

    def _manifest_hashes(self, root: Path) -> None:
        manifest = json.loads((root / "manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["schema"], "eu4-gfx-live-manifest/v2")
        self.assertEqual(manifest["acquisition_policy"], "acq-diversity/v1")
        self.assertTrue(any(item["role"] == "audit-associations"
                            for item in manifest["files"]))
        for item in manifest["files"]:
            digest = hashlib.sha256(
                (root / item["path"]).read_bytes()).hexdigest()
            self.assertEqual(item["sha256"], digest, msg=item["path"])

    def test_identical_calls_keep_one_anchor(self) -> None:
        root, _result, summary, diags = self._v2("acq-identical")
        self.assertEqual(len(diags), 1)
        self._expect_diag(diags[0], {
            "scenario": "identical_calls_100",
            "intercepted": 100, "screened_repeat": 92, "selected": 8,
            "first_selected": 1, "verification_selected": 7,
            "novel_retained": 1, "producer_matched_repeat": 7,
            "audit_persisted": 7, "r": 1, "l": 5,
        })
        self.assertEqual(summary["state"], "sealed")
        self.assertEqual(summary["structural"], "ok")
        retained = self._files(root, "retained")
        self.assertEqual(len(retained), 1)
        self._qualified(retained[0])
        association = json.loads((root / "audit-associations.json").read_text(
            encoding="utf-8"))
        self.assertEqual(association["schema"],
                         "eu4-gfx-live-audit-associations/v1")
        self.assertEqual(len(association["entries"]), 7)
        self._manifest_hashes(root)

    def test_same_slot_different_dword_is_two_keys(self) -> None:
        root, _result, _summary, diags = self._v2("acq-dword")
        self._expect_diag(diags[0], {
            "scenario": "same_slot_different_dword",
            "intercepted": 2, "selected": 2, "first_selected": 2,
            "novel_retained": 2, "r": 2, "l": 5,
        })
        retained = self._files(root, "retained")
        self.assertEqual(len(retained), 2)
        for path in retained:
            self._qualified(path)

    def test_scheduled_sample_cas_keeps_the_encounter(self) -> None:
        _root, _result, _summary, diags = self._v2("acq-sched-cas")
        self._expect_diag(diags[0], {
            "scenario": "scheduled_sample_phase_b_refused",
            "variant": "cas_exhausted", "outcome": "cas_exhausted",
            "intercepted": 2, "invalid_refused": 1, "selected": 1,
            "cas_exhausted": 1, "first_selected": 1, "novel_retained": 1,
            "r": 1, "l": 5,
        })

    def test_unanchored_reprobe_reserves_then_establishes(self) -> None:
        root, _result, summary, diags = self._v2("acq-unanchored-pcov")
        self._expect_diag(diags[0], {
            "scenario": "unanchored_missing_cell_pcov",
            "intercepted": 2, "selected": 2, "first_selected": 1,
            "verification_selected": 1, "novel_retained": 1,
            "ineligible_not_established": 1, "r": 1, "l": 5,
        })
        self.assertEqual(summary["established"], "1168")
        ineligible = self._files(root, "ineligible")
        self.assertEqual(len(ineligible), 1)
        descriptor = fixture.load_descriptor()
        materialized = fixture.materialize_capture(
            ineligible[0].read_text(encoding="utf-8"), descriptor)
        assessment = fixture.assess(materialized["record"], descriptor)
        self.assertEqual(assessment["missing"], ["post:dirty"])
        self.assertEqual(materialized["manifest"]["verdict"], "unqualified")
        self._qualified(self._files(root, "retained")[0])

    def test_two_threads_share_one_pending_owner(self) -> None:
        cases = (
            ("acq-threads-holds", "holds_gate", "cas_exhausted",
             {"cas_exhausted": 1, "invalid_refused": 1}),
            ("acq-threads-pending", "sees_pending", "pending_contended",
             {"pending_contended": 1}),
            ("acq-threads-insert", "key_insert_contended",
             "key_insert_contended",
             {"invalid_refused": 1, "key_insert_contended": 1}),
        )
        for mode, variant, outcome, extra in cases:
            with self.subTest(mode=mode):
                _root, _result, _summary, diags = self._v2(mode)
                expected = {
                    "scenario": "two_threads_same_unseen_key",
                    "variant": variant, "outcome": outcome,
                    "intercepted": 2, "selected": 1, "first_selected": 1,
                    "novel_retained": 1, "r": 1, "l": 5,
                }
                expected.update(extra)
                self._expect_diag(diags[0], expected)

    def test_anchor_survives_nonretained_verifications(self) -> None:
        root, _result, summary, diags = self._v2("acq-anchor")
        self.assertEqual(len(diags), 3)
        self._expect_diag(diags[0], {
            "scenario": "anchor_survives_nonretained",
            "variant": "encounter_2", "outcome": "distinct_not_retained",
            "intercepted": 2, "selected": 2, "first_selected": 1,
            "verification_selected": 1, "novel_retained": 1,
            "distinct_not_retained": 1, "r": 1, "l": 5,
        })
        self._expect_diag(diags[1], {
            "scenario": "anchor_survives_nonretained",
            "variant": "encounter_17", "outcome": "ineligible_not_established",
            "intercepted": 17, "screened_repeat": 14, "selected": 3,
            "first_selected": 1, "verification_selected": 2,
            "novel_retained": 1, "distinct_not_retained": 1,
            "ineligible_not_established": 1, "r": 1, "l": 5,
        })
        self._expect_diag(diags[2], {
            "scenario": "anchor_survives_nonretained",
            "variant": "encounter_33", "outcome": "storage_failed",
            "intercepted": 33, "screened_repeat": 29, "selected": 4,
            "first_selected": 1, "verification_selected": 3,
            "novel_retained": 1, "distinct_not_retained": 1,
            "ineligible_not_established": 1, "storage_failed": 1,
            "r": 1, "l": 5,
        })
        self.assertEqual(summary["admission_invariant_lost"], "0")
        self.assertEqual(len(self._files(root, "retained")), 1)
        self._qualified(self._files(root, "retained")[0])

    def test_rollback_failure_is_fail_closed(self) -> None:
        _root, _result, summary, diags = self._v2("acq-rollback")
        self._expect_diag(diags[0], {
            "scenario": "rollback_fail_after_bind_fail",
            "outcome": "cas_exhausted",
            "intercepted": 1, "invalid_refused": 1, "cas_exhausted": 1,
            "admission_invariant_lost": 1, "p_cov": 1, "l": 5,
        })
        self.assertEqual(summary["structural"], "failed")
        self.assertEqual(len(self._files(_root, "retained")), 0)

    def test_native_predicate_matches_frozen_python(self) -> None:
        root, result, _summary, _diags = self._v2("acq-predicate")
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith("PREDICATE ")]
        files = self._files(root, "retained") + self._files(root, "ineligible")
        self.assertEqual(len(lines), len(files))
        descriptor = fixture.load_descriptor()
        for line, path in zip(lines, files):
            rest = line[len("PREDICATE "):]
            _label, rest = rest.split(" missing=", 1)
            missing, behavior_text = rest.split(" behavior=", 1)
            materialized = fixture.materialize_capture(
                path.read_text(encoding="utf-8"), descriptor)
            record = materialized["record"]
            assessment = fixture.assess(record, descriptor)
            self.assertEqual(missing, ",".join(assessment["missing"]),
                             msg=path.name)
            self.assertEqual(record["completeness"]["losses"], 0)
            if assessment["missing"]:
                self.assertEqual(behavior_text, "")
                self.assertEqual(materialized["manifest"]["verdict"],
                                 "unqualified")
            else:
                self.assertEqual(materialized["manifest"]["verdict"],
                                 "qualified")
                self.assertEqual(
                    behavior_text,
                    " ".join(behavior(record, descriptor).key))

    def test_package_accounting_matrix(self) -> None:
        cases = {
            "acq-pre": {"intercepted": "3", "screened_repeat": "1",
                        "novel_retained": "1", "verification_selected": "1",
                        "distinct_not_retained": "1", "r": "1"},
            "acq-post": {"intercepted": "17", "screened_repeat": "14",
                         "novel_retained": "2", "verification_selected": "2",
                         "producer_matched_repeat": "1"},
            "acq-zero": {"intercepted": "33", "screened_repeat": "29",
                         "first_selected": "2", "verification_selected": "2",
                         "novel_retained": "2", "producer_matched_repeat": "2"},
            "acq-capacity": {"intercepted": "65", "selected": "64",
                             "novel_retained": "64",
                             "capacity_refused_selection": "1", "r": "64",
                             "l": "0"},
            "acq-reclaim": {"intercepted": "129", "novel_retained": "1",
                            "ineligible_not_established": "128",
                            "unanchored_reclaimed": "1"},
            "acq-false-coverage": {"r": "0", "established": "0",
                                   "ineligible_not_established": "1"},
            "acq-deadline": {"state": "partial", "closed_reason": "deadline",
                             "coverage_complete": "0", "structural": "ok"},
            "acq-mismatch": {"novel_retained": "1", "r": "1",
                             "selected_invariant_failure": "1",
                             "admission_invariant_lost": "1",
                             "structural": "failed"},
            "acq-drop-reservation": {"novel_retained": "0", "r": "0",
                                     "selected_invariant_failure": "1",
                                     "admission_invariant_lost": "1",
                                     "structural": "failed"},
        }
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                _root, result, summary, _diags = self._v2(mode)
                self.assertIn("ACQ_CHECK", result.stdout)
                for key, value in expected.items():
                    self.assertEqual(summary[key], value, msg=key)

    def test_failure_injections_and_evidence(self) -> None:
        _root, result, summary, diags = self._v2("acq-sig-read")
        self._expect_diag(diags[0], {
            "scenario": "signature_read_contended",
            "outcome": "signature_read_contended",
            "intercepted": 1, "invalid_refused": 1,
            "signature_read_contended": 1, "l": 6,
        })
        _root, result, _summary, diags = self._v2("acq-scan")
        self._expect_diag(diags[0], {
            "scenario": "uniqueness_scan_unreadable",
            "outcome": "cas_exhausted",
            "intercepted": 1, "invalid_refused": 1, "cas_exhausted": 1, "l": 6,
        })
        root, result, summary, diags = self._v2("acq-table-synthetic")
        self.assertIn("SYNTHETIC_STATE_INJECTION", result.stdout)
        self._expect_diag(diags[0], {
            "scenario": "table_capacity_refused", "variant": "synthetic",
            "outcome": "table_capacity_refused",
            "intercepted": 1, "invalid_refused": 1,
            "table_capacity_refused": 1, "l": 6,
        })
        for mode in ("acq-pending-retry", "acq-reader", "acq-tight-screen",
                     "acq-victim-cov", "acq-victim-div", "acq-reprobe-held"):
            with self.subTest(mode=mode):
                _root, result, summary, _diags = self._v2(mode)
                self.assertIn("ACQ_CHECK", result.stdout)
                self.assertEqual(summary["structural"], "ok")
                self.assertEqual(
                    int(summary["r"]) + int(summary["p_cov"]) +
                    int(summary["p_div"]) + int(summary["l"]) <= 64, True)
        root, _result, summary, _diags = self._v2("acq-audit-empty")
        association = json.loads((root / "audit-associations.json").read_text(
            encoding="utf-8"))
        self.assertEqual(association["entries"], [])
        self._manifest_hashes(root)
        self.assertEqual(summary["structural"], "ok")
        root, _result, summary, _diags = self._v2("acq-bad-hash")
        self.assertEqual(summary["structural"], "failed")
        association = json.loads((root / "audit-associations.json").read_text(
            encoding="utf-8"))
        entry = association["entries"][0]
        actual = hashlib.sha256(
            (root / entry["audit_path"]).read_bytes()).hexdigest()
        self.assertNotEqual(entry["audit_sha256"], actual)
        self._manifest_hashes(root)
        root, result = self._run("acq-skew")
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        summary = self._summary_v2(root)
        self.assertEqual(summary["state"], "error")
        self.assertEqual(summary["structural"], "failed")
        self.assertNotEqual(
            int(summary["intercepted"]),
            int(summary["invalid_refused"]) +
            int(summary["pending_contended"]) +
            int(summary["verification_contended"]) +
            int(summary["screened_repeat"]) + int(summary["selected"]))
        done = (root / "session.done").read_text(encoding="utf-8")
        self.assertIn("state=error", done)

    def test_screen_and_capture_overhead(self) -> None:
        _root, result = self._run("acq-overhead", timeout=180)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        line = next(item for item in result.stdout.splitlines()
                    if item.startswith("ACQ_OVERHEAD "))
        fields = dict(part.split("=", 1) for part in line.split()[1:])
        mach_p50 = int(fields["mach_p50"])
        mach_p99 = int(fields["mach_p99"])
        screen_p50 = int(fields["screen_p50"])
        screen_p99 = int(fields["screen_p99"])
        v1_full = int(fields["v1_full_p50"])
        v2_full = int(fields["v2_full_p50"])
        self.assertGreaterEqual(int(fields["screen_count"]), 10000)
        self.assertGreaterEqual(int(fields["v2_count"]), 10000)
        self.assertLessEqual(mach_p50, 1000)
        self.assertLessEqual(mach_p99, 2500)
        self.assertLessEqual(screen_p50, 1000)
        self.assertLessEqual(screen_p99, 2500)
        self.assertLessEqual(v2_full, (v1_full * 5) // 4)


if __name__ == "__main__":
    unittest.main()
