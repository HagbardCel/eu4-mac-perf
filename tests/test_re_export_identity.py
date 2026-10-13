import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.export_identity import (
    manifest_locked,
    publish_inventory_from_staging,
    smoke_qualified_from_payload,
    validate_export_identity,
)
from reverse_engineering.paths import ReError

FIXTURE = Path(__file__).resolve().parent / "re_fixtures"
SMOKE_OK = FIXTURE / "smoke_all_ok.json"
SMOKE_BAD = FIXTURE / "smoke_wrong_needles.json"


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

    def test_changed_digest_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "inventory.staging"
            inv = root / "inventory"
            _write_inventory(inv, "old")
            _write_inventory(staging, "new")
            with self.assertRaises(ReError):
                publish_inventory_from_staging(staging, inv)
            self.assertIn("old", (inv / "functions.jsonl").read_text())

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
        self.assertFalse(
            smoke_qualified_from_payload(
                payload,
                ("SShaderOpenGL6SetAll", "CPdxMeshObject13RenderBuckets", "CEU3GraphicalMap6Render"),
            )
        )


class QualificationAssertTests(unittest.TestCase):
    def test_missing_threshold_rejected(self) -> None:
        from reverse_engineering.export_identity import assert_qualification_current

        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            (exp / "inventory").mkdir()
            provenance = {"qualification": {"passed": True, "batch_set_sha256": "abc"}}
            with self.assertRaises(ReError):
                assert_qualification_current(exp, provenance, lambda: 0)


class ManifestLockTests(unittest.TestCase):
    def test_lock_with_invalid_final_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exp = Path(tmp)
            (exp / "decompiled").mkdir()
            (exp / "decompiled" / "batch-00001.jsonl").write_text("not valid jsonl export\n")
            self.assertTrue(manifest_locked(exp))
            prov = {"baseline_revision": "t"}
            with self.assertRaises(ReError):
                validate_export_identity(exp, prov, "inventory_regen")


if __name__ == "__main__":
    unittest.main()
