import argparse
import contextlib
import io
import json
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from reverse_engineering import renderer_extract, renderer_scope
from reverse_engineering.paths import ReError

FIXTURE_INV = Path(__file__).resolve().parent / "re_fixtures" / "export_mini" / "inventory"


def _summary() -> dict:
    return {"selection_manifest_sha256": "s", "ghidra_version": "12.1.4", "baseline_revision": "t"}


def _plan() -> dict:
    return {
        "baseline_revision": "t",
        "inventory_bundle_digest": "b",
        "batches": {"gfx-batch-00001": ["0x0000000000001000"]},
        "selected_function_count": 1,
    }


def _census_row(callsite: str, caller: str) -> dict:
    return {
        "callsite_rva": callsite,
        "caller_rva": caller,
        "pcode_kind": "CALL",
        "flow_type_is_call": True,
        "pcode_is_call": True,
        "flow_instruction_class": "direct",
    }


def _valid_static_row(entry: str = "0x0000000000001000") -> dict:
    inv = json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])
    ins0 = {
        "instruction_rva": entry,
        "length": 5,
        "bytes_hex": "0" * 10,
        "census_call": False,
        "pcode_kind": "none",
        "flow_type_is_call": False,
        "pcode_is_call": False,
    }
    ins1 = {
        "instruction_rva": "0x0000000000001005",
        "length": 3,
        "bytes_hex": "0" * 6,
        "census_call": True,
        "pcode_kind": "CALL",
        "flow_type_is_call": True,
        "pcode_is_call": True,
        "flow_instruction_class": "direct",
    }
    ranges = inv["instruction_ranges"]
    return {
        "entry_rva": entry,
        "status": "success",
        "instruction_count": inv["instruction_count"],
        "decoded_instruction_bytes": inv["decoded_instruction_bytes"],
        "body_ranges": ranges,
        "decoded_instruction_ranges": ranges,
        "instructions": [ins0, ins1],
    }


def _validate_package(**kwargs: object) -> None:
    defaults = {
        "batch_id": "gfx-batch-00001",
        "rvas": ["0x0000000000001000"],
        "plan": _plan(),
        "summary": _summary(),
    }
    defaults.update(kwargs)
    renderer_extract.validate_batch_package(**defaults)


class IdentityPatchMixin:
    def setUp(self) -> None:
        self._target = unittest.mock.patch(
            "reverse_engineering.renderer_extract.load_target",
            return_value={"sha256": "a"},
        )
        self._ghidra_meta = unittest.mock.patch(
            "reverse_engineering.renderer_extract.load_ghidra_meta",
            return_value={"program_name": "eu4", "image_base": 1},
        )
        self._target.start()
        self._ghidra_meta.start()

    def tearDown(self) -> None:
        self._ghidra_meta.stop()
        self._target.stop()


class ZipPackageTests(IdentityPatchMixin, unittest.TestCase):
    def _inventory(self) -> dict:
        row = json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])
        return {row["entry_rva"]: row}

    def test_missing_zip_member_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(renderer_extract.ZIP_STATIC, "{}\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_bytes(buf.getvalue())
            with self.assertRaises(ReError):
                renderer_extract.read_zip_package(path)

    def test_corrupt_zip_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_bytes(b"not-a-zip")
            with self.assertRaises(ReError):
                renderer_extract.read_zip_package(path)

    def test_receipt_hash_mismatch_rejected(self) -> None:
        static = json.dumps(_valid_static_row()) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        receipt["static_sha256"] = "0" * 64
        with self.assertRaises(ReError):
            _validate_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                inventory=self._inventory(),
                callsites_by_caller={
                    "0x0000000000001000": [_census_row("0x0000000000001005", "0x0000000000001000")]
                },
            )

    def test_receipt_batch_id_mismatch_rejected(self) -> None:
        static = json.dumps(_valid_static_row()) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        receipt["batch_id"] = "gfx-batch-99999"
        with self.assertRaises(ReError):
            _validate_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                inventory=self._inventory(),
                callsites_by_caller={
                    "0x0000000000001000": [_census_row("0x0000000000001005", "0x0000000000001000")]
                },
            )

    def test_callsite_field_mismatch_rejected(self) -> None:
        static_row = _valid_static_row()
        static_row["instructions"][1]["pcode_kind"] = "CALLIND"
        static = json.dumps(static_row) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        with self.assertRaises(ReError):
            _validate_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                inventory=self._inventory(),
                callsites_by_caller={
                    "0x0000000000001000": [_census_row("0x0000000000001005", "0x0000000000001000")]
                },
            )

    def test_empty_instructions_rejected(self) -> None:
        static_row = _valid_static_row()
        static_row["instructions"] = []
        with self.assertRaises(ReError):
            renderer_extract.validate_static_success_row(
                static_row,
                self._inventory()["0x0000000000001000"],
                [_census_row("0x0000000000001005", "0x0000000000001000")],
            )

    def test_static_failed_with_decompile_timeout_valid(self) -> None:
        entry = "0x0000000000001000"
        static = json.dumps({"entry_rva": entry, "status": "failed", "error": "walk failed"}) + "\n"
        decomp = json.dumps({"entry_rva": entry, "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=[entry],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        _validate_package(
            static_text=static,
            decomp_text=decomp,
            receipt=receipt,
            inventory=self._inventory(),
            callsites_by_caller={},
        )

    def test_static_success_decompile_timeout_valid_package(self) -> None:
        entry = "0x0000000000001000"
        static = json.dumps(_valid_static_row(entry)) + "\n"
        decomp = json.dumps({"entry_rva": entry, "status": "timeout", "error": "decompiler timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=[entry],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        _validate_package(
            static_text=static,
            decomp_text=decomp,
            receipt=receipt,
            inventory=self._inventory(),
            callsites_by_caller={entry: [_census_row("0x0000000000001005", entry)]},
        )

    def test_publish_identical_is_no_op(self) -> None:
        payload = b"zip-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            self.assertEqual(renderer_extract.publish_create_only(dest, payload), "created")
            self.assertEqual(renderer_extract.publish_create_only(dest, payload), "unchanged")

    def test_repair_restores_on_publish_failure(self) -> None:
        original = b"original-package"
        replacement = b"replacement"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(original)

            def fail_publish(_dest: Path, _payload: bytes) -> str:
                raise ReError("publish failed")

            with unittest.mock.patch(
                "reverse_engineering.renderer_extract.publish_create_only",
                fail_publish,
            ):
                with self.assertRaises(ReError) as ctx:
                    renderer_extract.repair_publish(dest, replacement, original)
            self.assertEqual(dest.read_bytes(), original)
            msg = str(ctx.exception)
            self.assertIn("Original restored", msg)
            self.assertIn("Archive retained", msg)
            self.assertNotIn("Restoration also failed", msg)

    def test_repair_restore_failure_reports_both_errors(self) -> None:
        original = b"original-package"
        replacement = b"replacement"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(original)

            def fail_publish(_dest: Path, _payload: bytes) -> str:
                raise ReError("publish failed")

            def fail_restore(_dest: Path, _payload: bytes) -> str:
                raise OSError("restore io failed")

            with unittest.mock.patch(
                "reverse_engineering.renderer_extract.publish_create_only",
                fail_publish,
            ), unittest.mock.patch(
                "reverse_engineering.renderer_extract.restore_create_only",
                fail_restore,
            ):
                with self.assertRaises(ReError) as ctx:
                    renderer_extract.repair_publish(dest, replacement, original)
            msg = str(ctx.exception)
            self.assertIn("publish failed", msg)
            self.assertIn("Restoration also failed", msg)
            self.assertIn("preserved at", msg.lower())

    def test_repair_conflict_does_not_overwrite_concurrent(self) -> None:
        original = b"original-package"
        replacement = b"replacement"
        concurrent = b"concurrent-other"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(original)

            def occupy_then_fail(_dest: Path, _payload: bytes) -> str:
                dest.write_bytes(concurrent)
                raise ReError("publish failed")

            with unittest.mock.patch(
                "reverse_engineering.renderer_extract.publish_create_only",
                occupy_then_fail,
            ):
                with self.assertRaises(ReError) as ctx:
                    renderer_extract.repair_publish(dest, replacement, original)
            self.assertEqual(dest.read_bytes(), concurrent)
            msg = str(ctx.exception).lower()
            self.assertIn("restoration also failed", msg)
            self.assertIn("preserved at", msg)

    def test_restore_create_only_rejects_occupied_dest_even_if_identical(self) -> None:
        payload = b"same-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(payload)
            with self.assertRaises(ReError) as ctx:
                renderer_extract.restore_create_only(dest, payload)
            self.assertIn("restoration conflict", str(ctx.exception).lower())

    def test_archive_failure_leaves_dest_untouched(self) -> None:
        original = b"original-package"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(original)
            with unittest.mock.patch(
                "reverse_engineering.renderer_extract.write_repair_archive",
                side_effect=ReError("archive failed"),
            ):
                with self.assertRaises(ReError):
                    renderer_extract.repair_publish(dest, b"new", original)
            self.assertEqual(dest.read_bytes(), original)


class PreflightTests(unittest.TestCase):
    def test_wrong_ghidra_version_rejected(self) -> None:
        with unittest.mock.patch(
            "reverse_engineering.renderer_extract.read_ghidra_version",
            return_value="11.0.0",
        ):
            with unittest.mock.patch("reverse_engineering.renderer_extract.verify_binary"):
                with self.assertRaises(ReError):
                    renderer_extract.assert_acquisition_preflight(_summary())

    def test_ensure_preflight_wrapper_calls_assert_once(self) -> None:
        summary = _summary()
        with unittest.mock.patch(
            "reverse_engineering.renderer_extract.assert_acquisition_preflight",
            return_value="12.1.4",
        ) as mock_assert:
            preflight_done = False

            def ensure_preflight() -> str:
                nonlocal preflight_done
                if not preflight_done:
                    version = renderer_extract.assert_acquisition_preflight(summary)
                    preflight_done = True
                    return version
                return "12.1.4"

            ensure_preflight()
            ensure_preflight()
            mock_assert.assert_called_once()


class UnchangedSkipsPreflightTests(IdentityPatchMixin, unittest.TestCase):
    def test_unchanged_batch_skips_preflight(self) -> None:
        preflight_calls = 0

        def ensure_preflight() -> str:
            nonlocal preflight_calls
            preflight_calls += 1
            return "12.1.4"

        static = json.dumps(_valid_static_row()) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        payload = renderer_extract.write_batch_zip(static, decomp, receipt)
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            batch_dir = exp / "renderer_snapshot" / "v1" / "batches"
            batch_dir.mkdir(parents=True)
            (batch_dir / "gfx-batch-00001.zip").write_bytes(payload)
            inv_path = exp / "inventory" / "functions.jsonl"
            inv_path.parent.mkdir(parents=True)
            inv_path.write_text((FIXTURE_INV / "functions.jsonl").read_text())
            audit_path = exp / "audits" / "callsites-v1.jsonl"
            audit_path.parent.mkdir(parents=True)
            audit_path.write_text(
                json.dumps(_census_row("0x0000000000001005", "0x0000000000001000")) + "\n"
            )
            inventory = renderer_extract.load_inventory_index(exp)
            callsites = renderer_extract.load_callsites_by_caller(exp)
            with unittest.mock.patch(
                "reverse_engineering.renderer_extract.run_ghidra_batch",
            ) as mock_ghidra:
                action = renderer_extract.acquire_batch(
                    exp=exp,
                    scope_dir=Path("/tmp"),
                    plan=_plan(),
                    summary=_summary(),
                    batch_id="gfx-batch-00001",
                    inventory=inventory,
                    callsites_by_caller=callsites,
                    decomp_timeout=120,
                    process_timeout=3600,
                    repair=False,
                    ensure_preflight=ensure_preflight,
                )
                mock_ghidra.assert_not_called()
        self.assertEqual(action, "unchanged")
        self.assertEqual(preflight_calls, 0)


class EvidenceAcceptanceTests(unittest.TestCase):
    def _acceptance_kwargs(self, **overrides: object) -> dict:
        base = {
            "packages_complete": True,
            "planned_functions": 4450,
            "observed_functions": 4450,
            "invalid_batch_ids": [],
            "static_failed_functions": 0,
            "verified_decoded_instructions": renderer_extract.EXPECTED_INSTRUCTIONS,
            "verified_decoded_bytes": renderer_extract.EXPECTED_DECODED_BYTES,
            "priority_entries_static_success": len(renderer_scope.PRIORITY_ENTRIES),
            "gfx_helpers_static_success": 92,
            "gfx_helpers_expected": 92,
            "decompile_status_counts": {"success": 4450},
        }
        base.update(overrides)
        return base

    def test_evidence_complete_when_all_required_pass(self) -> None:
        result = renderer_extract.evaluate_evidence_acceptance(**self._acceptance_kwargs())
        self.assertTrue(result["evidence_complete"])
        self.assertTrue(all(result["required_checks"].values()))

    def test_decompile_failures_advisory_only(self) -> None:
        result = renderer_extract.evaluate_evidence_acceptance(
            **self._acceptance_kwargs(decompile_status_counts={"success": 4449, "timeout": 1}),
        )
        self.assertTrue(result["evidence_complete"])
        self.assertFalse(result["advisory_checks"]["decompile_failures_zero"])

    def test_partial_packages_still_evaluates_all_required_checks(self) -> None:
        result = renderer_extract.evaluate_evidence_acceptance(
            **self._acceptance_kwargs(
                packages_complete=False,
                observed_functions=100,
                verified_decoded_instructions=17126,
                verified_decoded_bytes=79181,
                priority_entries_static_success=0,
                gfx_helpers_static_success=0,
                decompile_status_counts={"success": 100},
            ),
        )
        self.assertFalse(result["evidence_complete"])
        self.assertFalse(result["required_checks"]["packages_complete"])
        self.assertFalse(result["required_checks"]["instructions_match"])

    def test_status_three_state_transitions(self) -> None:
        partial_acc = renderer_extract.evaluate_evidence_acceptance(
            **self._acceptance_kwargs(packages_complete=False, observed_functions=1),
        )
        self.assertEqual(
            renderer_extract.report_status_from_acceptance(False, partial_acc),
            "partial",
        )
        pkg_acc = renderer_extract.evaluate_evidence_acceptance(
            **self._acceptance_kwargs(priority_entries_static_success=0),
        )
        self.assertEqual(
            renderer_extract.report_status_from_acceptance(True, pkg_acc),
            "packages_complete",
        )
        full_acc = renderer_extract.evaluate_evidence_acceptance(**self._acceptance_kwargs())
        self.assertEqual(
            renderer_extract.report_status_from_acceptance(True, full_acc),
            "evidence_complete",
        )


class BinaryByteVerificationUnitTests(unittest.TestCase):
    def test_parse_ghidra_image_base_rejects_invalid(self) -> None:
        with self.assertRaises(ReError) as ctx:
            renderer_extract.parse_ghidra_image_base({"image_base": "not-a-number"})
        self.assertIn("image_base_mismatch", str(ctx.exception))

    def test_no_verifiable_instructions_when_all_static_failed(self) -> None:
        entry = "0x0000000000001000"
        static = json.dumps({"entry_rva": entry, "status": "failed", "error": "walk failed"}) + "\n"
        decomp = json.dumps({"entry_rva": entry, "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=[entry],
            plan=_plan(),
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        payload = renderer_extract.write_batch_zip(static, decomp, receipt)
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            batch_dir = exp / "renderer_snapshot" / "v1" / "batches"
            batch_dir.mkdir(parents=True)
            (batch_dir / "gfx-batch-00001.zip").write_bytes(payload)
            mapper = unittest.mock.MagicMock()
            result = renderer_extract.verify_binary_bytes_for_valid_batches(
                exp,
                ["gfx-batch-00001"],
                0x100000000,
                mapper,
            )
        self.assertEqual(result["status"], "no_verifiable_instructions")
        self.assertEqual(result["verified_batches"], 1)
        mapper.read_bytes_at_va.assert_not_called()


class ReportCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.exp = Path(self._tmp.name) / "exp"
        self.exp.mkdir()
        batches = {
            f"gfx-batch-{index:05d}": [f"0x{index:016x}"] * 100 for index in range(1, 45)
        }
        batches["gfx-batch-00045"] = [f"0x{45:016x}"] * 50
        self.plan = {"batches": batches, "selected_function_count": 4450}
        self.all_batch_ids = sorted(self.plan["batches"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _coverage_summary(self, **overrides: object) -> dict:
        base = {
            "static_success_functions": 4450,
            "static_failed_functions": 0,
            "decompile_status_counts": {"success": 4450},
            "verified_decoded_instructions": renderer_extract.EXPECTED_INSTRUCTIONS,
            "verified_decoded_bytes": renderer_extract.EXPECTED_DECODED_BYTES,
            "priority_entries_static_success": len(renderer_scope.PRIORITY_ENTRIES),
            "priority_entries_missing_static_success": [],
            "gfx_helpers_static_success": 92,
            "gfx_helpers_expected": 92,
            "gfx_helpers_missing_static_success": [],
        }
        base.update(overrides)
        return base

    def _run_cmd_report(
        self,
        *,
        valid: list[str],
        missing: list[str],
        invalid: dict[str, str] | None = None,
        coverage: dict | None = None,
        require_evidence: bool = False,
        verify_binary_bytes: bool = False,
        byte_verification: object | None = None,
        byte_verification_error: Exception | None = None,
    ) -> tuple[int, dict]:
        invalid = invalid or {}
        coverage = coverage if coverage is not None else self._coverage_summary()
        args = argparse.Namespace(
            scope_dir="/tmp",
            baseline_revision="auto-v1-20261013",
            require_evidence=require_evidence,
            verify_binary_bytes=verify_binary_bytes,
        )
        patches = [
            unittest.mock.patch(
                "reverse_engineering.renderer_extract.load_frozen_context",
                return_value=(self.exp, {}, self.plan, "", [], "auto-v1-20261013"),
            ),
            unittest.mock.patch(
                "reverse_engineering.renderer_extract.load_inventory_index",
                return_value={},
            ),
            unittest.mock.patch(
                "reverse_engineering.renderer_extract.load_callsites_by_caller",
                return_value={},
            ),
            unittest.mock.patch(
                "reverse_engineering.renderer_extract.classify_batches",
                return_value=(valid, missing, invalid),
            ),
            unittest.mock.patch(
                "reverse_engineering.renderer_extract.summarize_package_coverage",
                return_value=coverage,
            ),
        ]
        if verify_binary_bytes:
            if byte_verification_error is not None:
                patches.append(
                    unittest.mock.patch(
                        "reverse_engineering.renderer_extract.run_binary_byte_verification",
                        side_effect=byte_verification_error,
                    )
                )
            elif byte_verification is not None:
                patches.append(
                    unittest.mock.patch(
                        "reverse_engineering.renderer_extract.run_binary_byte_verification",
                        return_value=byte_verification,
                    )
                )
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            code = renderer_extract.cmd_report(args)
        log_path = self.exp / "renderer_snapshot" / "v1" / "logs" / "coverage.json"
        self.assertTrue(log_path.is_file())
        report = json.loads(log_path.read_text())
        return code, report

    def test_partial_corpus_require_evidence_exits_one(self) -> None:
        code, report = self._run_cmd_report(
            valid=["gfx-batch-00001"],
            missing=self.all_batch_ids[1:],
            coverage=self._coverage_summary(
                static_success_functions=100,
                verified_decoded_instructions=17126,
                verified_decoded_bytes=79181,
                priority_entries_static_success=0,
                gfx_helpers_static_success=0,
                decompile_status_counts={"success": 100},
            ),
            require_evidence=True,
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(code, 1)

    def test_packages_complete_static_failure_exits_one(self) -> None:
        code, report = self._run_cmd_report(
            valid=self.all_batch_ids,
            missing=[],
            coverage=self._coverage_summary(static_failed_functions=1),
            require_evidence=True,
        )
        self.assertEqual(report["status"], "packages_complete")
        self.assertFalse(report["evidence_acceptance"]["evidence_complete"])
        self.assertEqual(code, 1)

    def test_evidence_complete_require_evidence_exits_zero(self) -> None:
        code, report = self._run_cmd_report(
            valid=self.all_batch_ids,
            missing=[],
            require_evidence=True,
        )
        self.assertEqual(report["status"], "evidence_complete")
        self.assertEqual(code, 0)

    def test_byte_mismatch_fails_verify_gate_independently(self) -> None:
        code, report = self._run_cmd_report(
            valid=self.all_batch_ids,
            missing=[],
            coverage=self._coverage_summary(),
            verify_binary_bytes=True,
            byte_verification={
                "status": "failed",
                "scope": "all_valid_batches",
                "verified_batches": 1,
                "verified_instructions": 0,
                "verified_bytes": 0,
                "first_mismatch": {
                    "entry_rva": "0x1",
                    "instruction_rva": "0x2",
                    "reason": "byte_mismatch",
                },
            },
        )
        self.assertEqual(report["status"], "evidence_complete")
        self.assertEqual(code, 1)

    def test_malformed_image_base_writes_coverage_and_classifies(self) -> None:
        with unittest.mock.patch(
            "reverse_engineering.renderer_extract.run_binary_byte_verification",
            side_effect=ReError("image_base_mismatch: invalid image_base value 'nope'"),
        ):
            code, report = self._run_cmd_report(
                valid=self.all_batch_ids,
                missing=[],
                verify_binary_bytes=True,
            )
        self.assertEqual(
            report["binary_byte_verification"]["first_mismatch"]["reason"],
            "image_base_mismatch",
        )
        self.assertEqual(code, 1)

    def test_binary_io_error_recorded_on_verification_failure(self) -> None:
        code, report = self._run_cmd_report(
            valid=self.all_batch_ids,
            missing=[],
            verify_binary_bytes=True,
            byte_verification_error=ReError("binary_io_error: permission denied"),
        )
        block = report["binary_byte_verification"]
        self.assertEqual(block["status"], "failed")
        self.assertEqual(block["first_mismatch"]["reason"], "binary_io_error")
        self.assertEqual(code, 1)


class BatchPlanTests(unittest.TestCase):
    def test_permuted_partition_rejected(self) -> None:
        summary = {
            "baseline_revision": "t",
            "selection_manifest_sha256": "s",
            "ghidra_version": "12.1.4",
        }
        rvas = ["0x0000000000001000", "0x0000000000002000"]
        expected = renderer_scope.build_batch_plan(
            summary=summary,
            bundle_digest="b",
            ordered_rvas=rvas,
            batch_size=renderer_scope.FROZEN_RENDERER_BATCH_SIZE,
        )
        saved = json.loads(renderer_extract.canonical_batch_plan_bytes(expected).decode())
        saved["batches"]["gfx-batch-00001"] = list(reversed(saved["batches"]["gfx-batch-00001"]))
        with self.assertRaises(ReError):
            renderer_extract.assert_frozen_batch_plan(saved, summary, "b", rvas)


if __name__ == "__main__":
    unittest.main()
