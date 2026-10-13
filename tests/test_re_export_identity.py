import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.export_identity import (
    assert_qualification_current,
    batch_set_sha256,
    compute_qualification,
    publish_inventory_from_staging,
    smoke_qualified_from_payload,
    validate_export_identity,
)
from reverse_engineering.manifest import inventory_bundle_digest, load_batch_manifest, validate_batch_file
from reverse_engineering.paths import ReError

FIXTURE = Path(__file__).resolve().parent / "re_fixtures"
EXPORT_MINI = FIXTURE / "export_mini"
SMOKE_OK = FIXTURE / "smoke_all_ok.json"
SMOKE_BAD = FIXTURE / "smoke_wrong_needles.json"
SMOKE_NEEDLES = (
    "SShaderOpenGL6SetAll",
    "CPdxMeshObject13RenderBuckets",
    "CEU3GraphicalMap6Render",
)


def _write_inventory(inv: Path, marker: str) -> None:
    inv.mkdir(parents=True, exist_ok=True)
    (inv / "functions.jsonl").write_text(
        json.dumps({"entry_rva": "0x0000000000000001", "kind": "internal", "tag": marker}) + "\n"
    )
    (inv / "call_edges.jsonl").write_text(
        json.dumps({"callsite_rva": "0x2", "caller_rva": "0x1", "resolution": "direct_internal"}) + "\n"
    )
    (inv / "coverage.json").write_text(
        json.dumps({"D_decoded_instruction_bytes": 10, "tag": marker}, sort_keys=True) + "\n"
    )


def _copy_export_mini(dest: Path) -> Path:
    shutil.copytree(EXPORT_MINI, dest)
    return dest


def _qualified_provenance(exp: Path, *, include_threshold: bool = True) -> dict:
    inv = exp / "inventory"
    bundle = inventory_bundle_digest(inv)
    manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
    batch_digest = batch_set_sha256(manifest, exp / "decompiled")
    smoke = json.loads(SMOKE_OK.read_text())
    qual: dict = {
        "passed": True,
        "batch_set_sha256": batch_digest,
        "inventory_bundle_digest": bundle,
    }
    if include_threshold:
        qual["min_success_bytes_threshold"] = 0.0
    return {
        "baseline_revision": manifest["baseline_revision"],
        "inventory_bundle_digest": bundle,
        "ghidra_version": "10.0",
        "import_script_hashes": {
            "ImportAndInventory.java": "a",
            "ExportDecompilation.java": "e",
        },
        "smoke_decompile": smoke,
        "smoke_qualified": True,
        "qualification": qual,
    }


class InventoryPublishTests(unittest.TestCase):
    def test_first_publication_replaces_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "inventory.staging"
            inv = root / "inventory"
            _write_inventory(staging, "a")
            publish_inventory_from_staging(staging, inv)
            self.assertFalse(staging.exists())
            self.assertTrue((inv / "functions.jsonl").is_file())
            self.assertIn("a", (inv / "coverage.json").read_text())

    def test_identical_digest_discards_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "inventory.staging"
            inv = root / "inventory"
            _write_inventory(inv, "same")
            _write_inventory(staging, "same")
            publish_inventory_from_staging(staging, inv)
            self.assertFalse(staging.exists())
            self.assertIn("same", (inv / "functions.jsonl").read_text())

    def test_changed_digest_replaces_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "inventory.staging"
            inv = root / "inventory"
            _write_inventory(inv, "old")
            _write_inventory(staging, "new")
            publish_inventory_from_staging(staging, inv)
            self.assertIn("new", (inv / "functions.jsonl").read_text())
            self.assertTrue((root / "inventory.backup").is_dir())
            self.assertIn("old", (root / "inventory.backup" / "functions.jsonl").read_text())

    def test_incomplete_staging_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "inventory.staging"
            inv = root / "inventory"
            _write_inventory(inv, "keep")
            staging.mkdir()
            (staging / "functions.jsonl").write_text("{}\n")
            with self.assertRaises(ReError):
                publish_inventory_from_staging(staging, inv)


class SmokePayloadTests(unittest.TestCase):
    def test_all_ok(self) -> None:
        payload = json.loads(SMOKE_OK.read_text())
        needles = tuple(a["needle"] for a in payload["anchors"])
        self.assertTrue(smoke_qualified_from_payload(payload, needles))

    def test_wrong_needles(self) -> None:
        payload = json.loads(SMOKE_BAD.read_text())
        self.assertFalse(smoke_qualified_from_payload(payload, SMOKE_NEEDLES))


class QualificationAssertTests(unittest.TestCase):
    def test_missing_threshold_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = _copy_export_mini(Path(tmp) / "exp")
            provenance = _qualified_provenance(exp, include_threshold=False)
            with self.assertRaises(ReError) as ctx:
                assert_qualification_current(
                    exp, provenance, lambda: 0, smoke_needles=SMOKE_NEEDLES
                )
            self.assertIn("min_success_bytes_threshold", str(ctx.exception))

    def test_batch_digest_mismatch_with_valid_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = _copy_export_mini(Path(tmp) / "exp")
            provenance = _qualified_provenance(exp)
            manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
            batch_path = exp / "decompiled" / "batch-00001.jsonl"
            expected = manifest["batches"]["batch-00001"]
            text = batch_path.read_text()
            lines = text.splitlines()
            lines[0] = lines[0] + " "
            batch_path.write_text("\n".join(lines) + "\n")
            self.assertEqual(validate_batch_file(batch_path, expected), [])
            live = compute_qualification(
                exp, provenance, 0.0, lambda: 0, smoke_needles=SMOKE_NEEDLES
            )
            self.assertTrue(live["passed"])
            with self.assertRaises(ReError) as ctx:
                assert_qualification_current(
                    exp, provenance, lambda: 0, smoke_needles=SMOKE_NEEDLES
                )
            self.assertIn("batch_set_sha256 mismatch", str(ctx.exception))


class IdentityWarnTests(unittest.TestCase):
    def test_inventory_regen_allowed_with_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            inv = exp / "inventory"
            _write_inventory(inv, "x")
            (exp / "decompiled").mkdir()
            (exp / "decompiled" / "batch-00001.jsonl").write_text("{}\n")
            bundle = inventory_bundle_digest(inv)
            prov = {"baseline_revision": "t", "inventory_bundle_digest": bundle}
            validate_export_identity(exp, prov, "inventory_regen")


if __name__ == "__main__":
    unittest.main()
