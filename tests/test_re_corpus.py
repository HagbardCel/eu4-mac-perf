import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from reverse_engineering import index_db
from reverse_engineering.intervals import merge_intervals, parse_rva, union_size
from reverse_engineering.corpus import build_analyze_headless_argv, cmd_index
from reverse_engineering.manifest import (
    archive_decompiled_batches,
    batches_archive_required,
    build_batch_manifest,
    decompilation_success_stats,
    export_completeness,
    inventory_bundle_digest,
    inventory_digest,
    validate_batch_file,
)

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"
FUNCTIONS = FIXTURE / "inventory" / "functions.jsonl"


class CmdIndexStaleManifestTests(unittest.TestCase):
    def test_stale_manifest_builds_inventory_only_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary_root = Path(tmp) / "eu4"
            exp = binary_root / "exports" / "fixture-mini"
            shutil.copytree(FIXTURE, exp)
            manifest_path = exp / "decompiled" / "batch_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["inventory_digest"] = "stale-functions-digest"
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            (exp / "provenance.json").write_text(
                json.dumps({"baseline_revision": "fixture-mini"}) + "\n"
            )
            stderr = io.StringIO()
            with unittest.mock.patch("reverse_engineering.corpus.binary_sha256", return_value="sha"):
                with unittest.mock.patch(
                    "reverse_engineering.corpus.local_binary_root", return_value=binary_root
                ):
                    with unittest.mock.patch.object(sys, "stderr", stderr):
                        rc = cmd_index(argparse.Namespace(baseline_revision="fixture-mini"))
            self.assertEqual(rc, 0)
            self.assertIn("inventory_digest stale", stderr.getvalue())
            conn = index_db.connect(exp / "index.sqlite")
            self.assertEqual(len(index_db.search_text(conn, "glUseProgram")), 0)
            self.assertGreaterEqual(len(index_db.search_symbol(conn, "SShader")), 1)
            self.assertTrue(list(index_db.callees(conn, "0x0000000000002000")))


class IntervalTests(unittest.TestCase):
    def test_union_size_overlapping_instructions(self) -> None:
        intervals = [(0x1000, 0x1005), (0x1005, 0x1008), (0x1008, 0x100F)]
        self.assertEqual(union_size(intervals), 15)

    def test_merge_adjacent(self) -> None:
        merged = merge_intervals([(0, 5), (5, 8)])
        self.assertEqual(merged, [(0, 8)])


class ManifestTests(unittest.TestCase):
    def test_numeric_rva_sort_order(self) -> None:
        manifest = build_batch_manifest(
            functions_jsonl=FUNCTIONS,
            baseline_revision="t",
            batch_size=2,
            exporter_script_hash="x",
            inventory_digest_value="d",
        )
        batch = manifest["batches"]["batch-00001"]
        ints = [parse_rva(r) for r in batch]
        self.assertEqual(ints, sorted(ints))
        self.assertLess(ints[0], ints[1])

    def test_export_completeness_fixture(self) -> None:
        manifest = json.loads((FIXTURE / "decompiled" / "batch_manifest.json").read_text())
        manifest["inventory_digest"] = inventory_digest(FUNCTIONS)
        c_export, errors = export_completeness(manifest, FIXTURE / "decompiled")
        self.assertEqual(c_export, 1.0)
        self.assertEqual(errors, [])

    def test_decompilation_success_bytes(self) -> None:
        manifest = json.loads((FIXTURE / "decompiled" / "batch_manifest.json").read_text())
        stats = decompilation_success_stats(manifest, FIXTURE / "decompiled", FUNCTIONS)
        self.assertEqual(stats["total_bytes"], 17)
        self.assertEqual(stats["success_bytes"], 13)

    def test_validate_batch_rejects_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch-00001.jsonl"
            path.write_text('{"entry_rva":"0x1","status":"success"}\n')
            errors = validate_batch_file(path, ["0x0000000000000001", "0x0000000000000002"])
            self.assertTrue(errors)


class HeadlessArgvTests(unittest.TestCase):
    def test_script_path_is_separate_argv(self) -> None:
        with unittest.mock.patch.dict("os.environ", {"GHIDRA_INSTALL": "/tmp/ghidra"}):
            with unittest.mock.patch("reverse_engineering.corpus.ghidra_install") as mock_install:
                mock_install.return_value = Path("/tmp/ghidra")
                argv = build_analyze_headless_argv(Path("/proj"), "EU4_test", "-import", "/eu4")
        self.assertIn("-scriptPath", argv)
        idx = argv.index("-scriptPath")
        self.assertNotEqual(argv[idx + 1], "")
        self.assertFalse(argv[idx].startswith("-scriptPath="))


class ManifestArchiveTests(unittest.TestCase):
    def test_inventory_digest_change_requires_archive(self) -> None:
        old = {"inventory_digest": "a", "batches": {"batch-00001": ["0x1"]}}
        new = {"inventory_digest": "b", "batches": {"batch-00001": ["0x1"]}}
        self.assertTrue(batches_archive_required(old, new))
        self.assertFalse(batches_archive_required(None, new))

    def test_partition_change_requires_archive(self) -> None:
        old = {"inventory_digest": "a", "batches": {"batch-00001": ["0x1", "0x2"]}}
        new = {"inventory_digest": "a", "batches": {"batch-00001": ["0x1"], "batch-00002": ["0x2"]}}
        self.assertTrue(batches_archive_required(old, new))

    def test_archive_moves_batch_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decomp = Path(tmp)
            batch = decomp / "batch-00001.jsonl"
            batch.write_text('{"entry_rva":"0x1","status":"success"}\n')
            dest = archive_decompiled_batches(decomp, "20260101T000000Z")
            self.assertIsNotNone(dest)
            self.assertFalse(batch.is_file())
            self.assertTrue((dest / "batch-00001.jsonl").is_file())


class BundleDigestTests(unittest.TestCase):
    def test_inventory_bundle_digest_stable(self) -> None:
        inv = FIXTURE / "inventory"
        d1 = inventory_bundle_digest(inv)
        d2 = inventory_bundle_digest(inv)
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)


class TargetPinTests(unittest.TestCase):
    def test_target_sha_matches_benchmark_inventory(self) -> None:
        target = json.loads(
            (Path(__file__).resolve().parents[1] / "reverse_engineering" / "target.json").read_text()
        )
        engine = Path(__file__).resolve().parents[1] / "benchmark" / "eu4_engine_inventory.py"
        text = engine.read_text()
        self.assertIn(target["sha256"], text)


if __name__ == "__main__":
    unittest.main()
