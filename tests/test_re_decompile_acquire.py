import argparse
import json
import os
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from reverse_engineering.corpus import (
    DEFAULT_PROCESS_TIMEOUT,
    PILOT_BATCH_ID,
    _commit_manifest_with_archive,
    cmd_decompile,
    cmd_manifest,
    terminate_process_group,
)
from reverse_engineering.export_identity import (
    publish_batch_staging,
    validate_acquisition_preflight,
)
from reverse_engineering.manifest import (
    build_batch_manifest,
    decompilation_success_stats,
    inventory_bundle_digest,
    inventory_digest,
    load_batch_manifest,
    normalize_staging_batch_errors,
    validate_batch_file,
)
from reverse_engineering.paths import ReError

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"
FUNCTIONS = FIXTURE / "inventory" / "functions.jsonl"


def _copy_export_mini(dest: Path) -> Path:
    shutil.copytree(FIXTURE, dest)
    inv = dest / "inventory"
    manifest_path = dest / "decompiled" / "batch_manifest.json"
    manifest = load_batch_manifest(manifest_path)
    manifest["inventory_digest"] = inventory_digest(FUNCTIONS)
    manifest["inventory_bundle_digest"] = inventory_bundle_digest(inv)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    provenance = {
        "baseline_revision": manifest["baseline_revision"],
        "inventory_bundle_digest": inventory_bundle_digest(inv),
        "inventory_digest": inventory_digest(FUNCTIONS),
        "ghidra_version": "10.0",
        "ghidra_meta": {
            "project_dir": "/proj",
            "project_name": "EU4_test",
            "program_name": "eu4",
        },
    }
    (dest / "provenance.json").write_text(json.dumps(provenance) + "\n")
    return dest


class BatchValidationTests(unittest.TestCase):
    def test_json_array_record_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch-00001.jsonl"
            path.write_text('["not","a","function"]\n')
            errors = validate_batch_file(path, ["0x0000000000001000"])
            self.assertTrue(any("JSON object" in err for err in errors))

    def test_success_without_pseudocode_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch-00001.jsonl"
            path.write_text(
                '{"entry_rva":"0x0000000000001000","status":"success","decompilation_seconds":1.0}\n'
            )
            errors = validate_batch_file(path, ["0x0000000000001000"])
            self.assertTrue(any("without pseudocode" in err for err in errors))

    def test_failed_without_error_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch-00001.jsonl"
            path.write_text(
                '{"entry_rva":"0x0000000000001000","status":"failed","decompilation_seconds":1.0}\n'
            )
            errors = validate_batch_file(path, ["0x0000000000001000"])
            self.assertTrue(any("without error" in err for err in errors))

    def test_normalize_fills_blank_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch.jsonl.staging"
            path.write_text(
                '{"entry_rva":"0x0000000000001000","status":"failed","decompilation_seconds":1.0}\n'
            )
            normalize_staging_batch_errors(path)
            row = json.loads(path.read_text().strip())
            self.assertIn("decompilation_failed_without_message", row["error"])
            errors = validate_batch_file(path, ["0x0000000000001000"])
            self.assertEqual(errors, [])


class AcquisitionIdentityTests(unittest.TestCase):
    def test_swapped_rva_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = _copy_export_mini(Path(tmp) / "exp")
            provenance = json.loads((exp / "provenance.json").read_text())
            manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
            rvas = list(manifest["batches"]["batch-00001"])
            rvas[0] = "0x0000000000009999"
            manifest["batches"]["batch-00001"] = rvas
            with self.assertRaises(ReError):
                validate_acquisition_preflight(
                    exp,
                    provenance,
                    manifest,
                    exporter_script_hash=manifest["exporter_script_hash"],
                    ghidra_version="10.0",
                    ghidra_meta=provenance["ghidra_meta"],
                )

    def test_wrong_exporter_hash_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = _copy_export_mini(Path(tmp) / "exp")
            provenance = json.loads((exp / "provenance.json").read_text())
            manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
            with self.assertRaises(ReError) as ctx:
                validate_acquisition_preflight(
                    exp,
                    provenance,
                    manifest,
                    exporter_script_hash="deadbeef",
                    ghidra_version="10.0",
                    ghidra_meta=provenance["ghidra_meta"],
                )
            self.assertIn("exporter", str(ctx.exception).lower())


class CoverageStatsTests(unittest.TestCase):
    def test_attempted_vs_total_differ_for_partial_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = _copy_export_mini(Path(tmp) / "exp")
            functions_path = exp / "inventory" / "functions.jsonl"
            extra = (
                '{"entry_rva":"0x0000000000005000","kind":"internal",'
                '"decoded_instruction_bytes":100}\n'
            )
            functions_path.write_text(functions_path.read_text() + extra)
            manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
            stats = decompilation_success_stats(
                manifest,
                exp / "decompiled",
                functions_path,
            )
            self.assertGreater(stats["c_success_bytes_attempted"], 0.0)
            self.assertLess(stats["c_success_bytes_total"], stats["c_success_bytes_attempted"])


class DecompileOrchestrationTests(unittest.TestCase):
    def test_valid_batch_skips_ghidra(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
            with unittest.mock.patch("reverse_engineering.corpus.binary_sha256", return_value="sha"):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.local_binary_root", return_value=binary_root
                ):
                    with unittest.mock.patch("reverse_engineering.corpus.verify_binary"):
                        with unittest.mock.patch(
                            "reverse_engineering.corpus.load_ghidra_meta",
                            return_value={
                                "project_dir": "/proj",
                                "project_name": "EU4_test",
                                "program_name": "eu4",
                            },
                        ):
                            with unittest.mock.patch(
                                "reverse_engineering.corpus._acquisition_preflight_context",
                                return_value=("10.0", {"project_dir": "/proj", "project_name": "EU4_test", "program_name": "eu4"}),
                            ):
                                with unittest.mock.patch(
                                    "reverse_engineering.corpus.script_hash",
                                    return_value=load_batch_manifest(
                                        exp / "decompiled" / "batch_manifest.json"
                                    )["exporter_script_hash"],
                                ):
                                    with unittest.mock.patch(
                                        "reverse_engineering.corpus.analyze_headless"
                                    ) as mock_headless:
                                        rc = cmd_decompile(
                                            argparse.Namespace(
                                                baseline_revision="fixture-mini",
                                                batch_size=500,
                                                timeout_seconds=120,
                                                process_timeout=DEFAULT_PROCESS_TIMEOUT,
                                                min_success_bytes=0.0,
                                                pilot=0,
                                                batch_id="batch-00001",
                                                require_complete=False,
                                                force=False,
                                            )
                                        )
            self.assertEqual(rc, 0)
            mock_headless.assert_not_called()

    def _pilot_mocks(self, exp: Path, binary_root: Path):
        exporter = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")[
            "exporter_script_hash"
        ]
        return unittest.mock.patch.multiple(
            "reverse_engineering.corpus",
            binary_sha256=unittest.mock.Mock(return_value="sha"),
            local_binary_root=unittest.mock.Mock(return_value=binary_root),
            verify_binary=unittest.mock.Mock(),
            load_ghidra_meta=unittest.mock.Mock(
                return_value={
                    "project_dir": "/proj",
                    "project_name": "EU4_test",
                    "program_name": "eu4",
                }
            ),
            _acquisition_preflight_context=unittest.mock.Mock(
                return_value=(
                    "10.0",
                    {"project_dir": "/proj", "project_name": "EU4_test", "program_name": "eu4"},
                )
            ),
            script_hash=unittest.mock.Mock(return_value=exporter),
        )

    def _fake_headless_write_success(self, project_dir, project_name, *extra, process_timeout=None):
        rva_file = Path(extra[6])
        staging = Path(extra[7])
        rvas = [line.strip() for line in rva_file.read_text().splitlines() if line.strip()]
        lines = [
            json.dumps(
                {
                    "entry_rva": rva,
                    "status": "success",
                    "decompilation_seconds": 0.1,
                    "pseudocode": "void pilot_fn() {}",
                }
            )
            for rva in rvas
        ]
        staging.write_text("\n".join(lines) + "\n")

    def test_pilot_writes_report_with_nonzero_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
            with self._pilot_mocks(exp, binary_root):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.analyze_headless",
                    side_effect=self._fake_headless_write_success,
                ):
                    rc = cmd_decompile(
                        argparse.Namespace(
                            baseline_revision="fixture-mini",
                            batch_size=500,
                            timeout_seconds=120,
                            process_timeout=DEFAULT_PROCESS_TIMEOUT,
                            min_success_bytes=0.0,
                            pilot=2,
                            batch_id=None,
                            require_complete=False,
                            force=True,
                        )
                    )
            self.assertEqual(rc, 0)
            pilot_dir = exp / "logs" / "pilot"
            report = json.loads((pilot_dir / "report.json").read_text())
            self.assertEqual(report["subprocess_outcome"], "success")
            self.assertGreater(report["success_count"], 0)
            self.assertGreater(report["c_success_bytes_attempted"], 0.0)
            self.assertTrue((pilot_dir / f"{PILOT_BATCH_ID}.jsonl").is_file())

    def test_pilot_failure_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
            with self._pilot_mocks(exp, binary_root):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.analyze_headless",
                    side_effect=RuntimeError("boom"),
                ):
                    with self.assertRaises(RuntimeError):
                        cmd_decompile(
                            argparse.Namespace(
                                baseline_revision="fixture-mini",
                                batch_size=500,
                                timeout_seconds=120,
                                process_timeout=DEFAULT_PROCESS_TIMEOUT,
                                min_success_bytes=0.0,
                                pilot=2,
                                batch_id=None,
                                require_complete=False,
                                force=True,
                            )
                        )
            report = json.loads((exp / "logs" / "pilot" / "report.json").read_text())
            self.assertEqual(report["subprocess_outcome"], "failed")
            self.assertIn("boom", report["error"])
            self.assertFalse((exp / "logs" / "pilot" / f"{PILOT_BATCH_ID}.jsonl.staging").is_file())

    def test_pilot_keyboard_interrupt_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
            with self._pilot_mocks(exp, binary_root):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.analyze_headless",
                    side_effect=KeyboardInterrupt,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        cmd_decompile(
                            argparse.Namespace(
                                baseline_revision="fixture-mini",
                                batch_size=500,
                                timeout_seconds=120,
                                process_timeout=DEFAULT_PROCESS_TIMEOUT,
                                min_success_bytes=0.0,
                                pilot=2,
                                batch_id=None,
                                require_complete=False,
                                force=True,
                            )
                        )
            report = json.loads((exp / "logs" / "pilot" / "report.json").read_text())
            self.assertEqual(report["subprocess_outcome"], "failed")
            self.assertFalse((exp / "logs" / "pilot" / f"{PILOT_BATCH_ID}.jsonl.staging").is_file())

    def test_keyboard_interrupt_terminates_process_group(self) -> None:
        proc = unittest.mock.MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        with unittest.mock.patch("reverse_engineering.corpus.os.killpg") as killpg:
            terminate_process_group(proc)
            killpg.assert_called()


class ManifestFreshTests(unittest.TestCase):
    def test_fresh_archives_batches_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
            (exp / "index.sqlite").write_bytes(b"sqlite")
            with unittest.mock.patch("reverse_engineering.corpus.binary_sha256", return_value="sha"):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.local_binary_root", return_value=binary_root
                ):
                    with unittest.mock.patch("reverse_engineering.corpus.verify_binary"):
                        with unittest.mock.patch(
                            "reverse_engineering.corpus._acquisition_preflight_context",
                            return_value=("10.0", json.loads((exp / "provenance.json").read_text())["ghidra_meta"]),
                        ):
                            with unittest.mock.patch(
                                "reverse_engineering.corpus.script_hash",
                                return_value=load_batch_manifest(
                                    exp / "decompiled" / "batch_manifest.json"
                                )["exporter_script_hash"],
                            ):
                                rc = cmd_manifest(
                                    argparse.Namespace(
                                        baseline_revision="fixture-mini",
                                        batch_size=500,
                                        fresh=True,
                                    )
                                )
            self.assertEqual(rc, 0)
            self.assertFalse((exp / "decompiled" / "batch-00001.jsonl").is_file())
            self.assertFalse((exp / "index.sqlite").is_file())
            archives = list((exp / "decompiled" / ".archive").iterdir())
            self.assertEqual(len(archives), 1)
            archived = archives[0]
            self.assertTrue((archived / "batch-00001.jsonl").is_file())
            self.assertTrue((archived / "index.sqlite").is_file())
            self.assertTrue((archived / "batch_manifest.json").is_file())
            qual = json.loads((exp / "provenance.json").read_text())["qualification"]
            self.assertFalse(qual.get("passed", True))


class ManifestArchiveRollbackTests(unittest.TestCase):
    def _two_batch_export(self, binary_root: Path) -> Path:
        exp = _copy_export_mini(binary_root / "exports" / "fixture-mini")
        decomp = exp / "decompiled"
        shutil.copy(decomp / "batch-00001.jsonl", decomp / "batch-00002.jsonl")
        (exp / "index.sqlite").write_bytes(b"sqlite-index")
        return exp

    def _manifest_mocks(self, binary_root: Path, exp: Path):
        exporter = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")[
            "exporter_script_hash"
        ]
        return unittest.mock.patch.multiple(
            "reverse_engineering.corpus",
            binary_sha256=unittest.mock.Mock(return_value="sha"),
            local_binary_root=unittest.mock.Mock(return_value=binary_root),
            verify_binary=unittest.mock.Mock(),
            _acquisition_preflight_context=unittest.mock.Mock(
                return_value=(
                    "10.0",
                    json.loads((exp / "provenance.json").read_text())["ghidra_meta"],
                )
            ),
            script_hash=unittest.mock.Mock(return_value=exporter),
        )

    def _snapshot_acquisition(self, exp: Path) -> dict[str, bytes]:
        decomp = exp / "decompiled"
        snap = {
            "manifest": (decomp / "batch_manifest.json").read_bytes(),
            "batch-00001": (decomp / "batch-00001.jsonl").read_bytes(),
            "batch-00002": (decomp / "batch-00002.jsonl").read_bytes(),
            "index": (exp / "index.sqlite").read_bytes(),
        }
        return snap

    def _assert_snapshot_unchanged(self, exp: Path, snap: dict[str, bytes]) -> None:
        decomp = exp / "decompiled"
        self.assertEqual((decomp / "batch_manifest.json").read_bytes(), snap["manifest"])
        self.assertEqual((decomp / "batch-00001.jsonl").read_bytes(), snap["batch-00001"])
        self.assertEqual((decomp / "batch-00002.jsonl").read_bytes(), snap["batch-00002"])
        self.assertEqual((exp / "index.sqlite").read_bytes(), snap["index"])

    def test_archive_rollback_on_second_batch_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = self._two_batch_export(binary_root)
            snap = self._snapshot_acquisition(exp)
            manifest = build_batch_manifest(
                functions_jsonl=exp / "inventory" / "functions.jsonl",
                baseline_revision="fixture-mini",
                batch_size=500,
                exporter_script_hash=load_batch_manifest(
                    exp / "decompiled" / "batch_manifest.json"
                )["exporter_script_hash"],
                inventory_digest_value=inventory_digest(exp / "inventory" / "functions.jsonl"),
                inventory_bundle_digest_value=inventory_bundle_digest(exp / "inventory"),
            )
            real_move = shutil.move
            state = {"n": 0}

            def move_inject(src, dst):
                state["n"] += 1
                if state["n"] == 2:
                    raise OSError("injected batch move failure")
                return real_move(src, dst)

            with self._manifest_mocks(binary_root, exp):
                with unittest.mock.patch("shutil.move", side_effect=move_inject):
                    with self.assertRaises(OSError):
                        _commit_manifest_with_archive(
                            exp, exp / "decompiled", manifest, "rollback-test"
                        )
            self._assert_snapshot_unchanged(exp, snap)
            self.assertFalse((exp / "decompiled" / "batch_manifest.json.staging").is_file())

    def test_archive_rollback_on_index_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = self._two_batch_export(binary_root)
            snap = self._snapshot_acquisition(exp)
            manifest = build_batch_manifest(
                functions_jsonl=exp / "inventory" / "functions.jsonl",
                baseline_revision="fixture-mini",
                batch_size=500,
                exporter_script_hash=load_batch_manifest(
                    exp / "decompiled" / "batch_manifest.json"
                )["exporter_script_hash"],
                inventory_digest_value=inventory_digest(exp / "inventory" / "functions.jsonl"),
                inventory_bundle_digest_value=inventory_bundle_digest(exp / "inventory"),
            )
            real_move = shutil.move

            def move_inject(src, dst):
                if str(dst).endswith("index.sqlite") and ".archive" in str(dst):
                    raise OSError("injected index move failure")
                return real_move(src, dst)

            with self._manifest_mocks(binary_root, exp):
                with unittest.mock.patch("shutil.move", side_effect=move_inject):
                    with self.assertRaises(OSError):
                        _commit_manifest_with_archive(
                            exp, exp / "decompiled", manifest, "rollback-test"
                        )
            self._assert_snapshot_unchanged(exp, snap)

    def test_archive_rollback_on_manifest_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = self._two_batch_export(binary_root)
            snap = self._snapshot_acquisition(exp)
            manifest = build_batch_manifest(
                functions_jsonl=exp / "inventory" / "functions.jsonl",
                baseline_revision="fixture-mini",
                batch_size=500,
                exporter_script_hash=load_batch_manifest(
                    exp / "decompiled" / "batch_manifest.json"
                )["exporter_script_hash"],
                inventory_digest_value=inventory_digest(exp / "inventory" / "functions.jsonl"),
                inventory_bundle_digest_value=inventory_bundle_digest(exp / "inventory"),
            )
            real_replace = os.replace

            def replace_inject(src, dst):
                if str(dst).endswith("batch_manifest.json"):
                    raise OSError("injected replace failure")
                return real_replace(src, dst)

            with self._manifest_mocks(binary_root, exp):
                with unittest.mock.patch("os.replace", side_effect=replace_inject):
                    with self.assertRaises(OSError):
                        _commit_manifest_with_archive(
                            exp, exp / "decompiled", manifest, "rollback-test"
                        )
            self._assert_snapshot_unchanged(exp, snap)


if __name__ == "__main__":
    unittest.main()
