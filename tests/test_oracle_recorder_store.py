"""P4.1: the immutable, content-addressed evidence store.

Offline and adversarial: these tests prove content addressing, create-only
publication, and that a tampered, truncated or falsely-referenced blob is caught
at load time rather than trusted.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from oracle.recorder import schema
from oracle.recorder import store as store_mod
from oracle.recorder.store import EvidenceStore, StoreError


def _payload() -> dict:
    """A minimal well-formed, fully qualified observation."""
    return {
        "record_type": "observation",
        "provenance": {
            "module_sha256": "a" * 64,
            "function_id": "gfx:0x15ec36a",
            "callsite": "rva:0x15ec36a+0x2a",
            "capture_policy_digest": "b" * 64,
            "scenario": "venice-paused",
            "epoch": "epoch-a",
            "thread": 0,
        },
        "invocation": {"hook_id": "hook:gfx-setcb", "call_id": "call:1",
                       "parent_span": None, "sequence": 0},
        "abi": {
            "calling_convention": "sysv-amd64",
            "carriers": [{"name": "rdi", "kind": "int", "bits": 64,
                          "value_hex": "0000000000000001", "confidence": "observed"}],
            "return_carrier": None,
            "confidence": "observed",
        },
        "state": {
            "pre": [{"space": "heap:ctx", "offset": 296, "value_hex": "01",
                     "valid_mask": "ff", "confidence": "observed"}],
            "post": [{"space": "heap:ctx", "offset": 296, "value_hex": "00",
                      "valid_mask": "ff", "confidence": "observed"}],
            "unknown_dependencies": [],
        },
        "resources": [{"resource_id": "buf:1", "generation": 0, "version": 3,
                       "contents_sha256": "c" * 64, "aliases": []}],
        "effects": {"ordered": [{"op": "glBindBuffer", "args": ["8892", "01"]}],
                    "complete": True},
        "completeness": {"state": "captured", "losses": 0, "unavailable_dependencies": []},
        "fidelity": {"mode": "faithful", "policy_version": "p4-fid-v1"},
        "raw": {"blob_sha256": None},
    }


def _write_manifest_file(root: Path, blobs: dict[str, int], records: list[str], *,
                         schema_version: str = store_mod.EVIDENCE_MANIFEST_VERSION) -> None:
    """Write a manifest directly, for corrupt-input tests."""
    payload = {
        "canonicalization_version": schema.CANONICALIZATION_VERSION,
        "blobs": {digest: {"size": size} for digest, size in blobs.items()},
        "records": list(records),
    }
    manifest = {
        "schema_version": schema_version,
        "manifest_sha256": hashlib.sha256(schema.canonical_bytes(payload)).hexdigest(),
        "payload": payload,
    }
    (root / store_mod.MANIFEST_NAME).write_bytes(schema.canonical_bytes(manifest) + b"\n")


class BlobTests(unittest.TestCase):
    def test_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            first = store.put_blob(b"hello")
            second = store.put_blob(b"hello")
            self.assertEqual(first, second)
            self.assertEqual(first, hashlib.sha256(b"hello").hexdigest())
            self.assertEqual(store.get_blob(first), b"hello")

    def test_get_missing_blob_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            with self.assertRaises(StoreError):
                store.get_blob("0" * 64)

    def test_create_only_refuses_a_conflicting_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            path = store.blob_path(hashlib.sha256(b"hello").hexdigest())
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"different")
            with self.assertRaises(StoreError):
                store.put_blob(b"hello")

    def test_no_staging_files_are_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            store.put_blob(b"hello")
            leftovers = [p.name for p in store.blobs_dir.iterdir() if p.name.startswith(".blob.")]
            self.assertEqual(leftovers, [])


class RecordTests(unittest.TestCase):
    def test_add_record_round_trips_through_a_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            digest = store.add_record(_payload())
            self.assertEqual(store.records, [digest])
            store.write_manifest()
            reopened = EvidenceStore(Path(tmp))
            reopened.verify()
            self.assertEqual(reopened.records, [digest])
            self.assertEqual(reopened.get_record(digest), _payload())

    def test_add_record_rejects_a_malformed_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            payload = _payload()
            payload["fidelity"]["mode"] = "nope"
            with self.assertRaises(schema.SchemaError):
                store.add_record(payload)

    def test_add_record_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            first = store.add_record(_payload())
            second = store.add_record(_payload())
            self.assertEqual(first, second)
            self.assertEqual(store.records, [first])

    def test_record_digest_equals_its_blob_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EvidenceStore(Path(tmp))
            digest = store.add_record(_payload())
            self.assertEqual(digest, schema.record_digest(_payload()))


class IntegrityTests(unittest.TestCase):
    def _store_with_one_record(self, root: Path) -> str:
        store = EvidenceStore(root)
        digest = store.add_record(_payload())
        store.write_manifest()
        return digest

    def test_tampered_blob_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = self._store_with_one_record(root)
            EvidenceStore(root).blob_path(digest).write_bytes(b"corrupted")
            with self.assertRaises(StoreError):
                EvidenceStore(root).verify()

    def test_missing_blob_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            digest = self._store_with_one_record(root)
            EvidenceStore(root).blob_path(digest).unlink()
            with self.assertRaises(StoreError):
                EvidenceStore(root).verify()

    def test_manifest_digest_tamper_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._store_with_one_record(root)
            manifest_path = root / store_mod.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["payload"]["records"] = []  # no longer matches manifest_sha256
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(StoreError):
                EvidenceStore(root)

    def test_unsupported_manifest_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_manifest_file(root, {}, [], schema_version="eu4-oracle-evidence-manifest/zzz")
            with self.assertRaises(StoreError):
                EvidenceStore(root)

    def test_manifest_referencing_a_non_record_blob_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blobs = root / store_mod.BLOBS_DIR
            blobs.mkdir(parents=True)
            data = b'{"not":"a record"}'
            digest = hashlib.sha256(data).hexdigest()
            (blobs / digest).write_bytes(data)
            _write_manifest_file(root, {digest: len(data)}, [digest])
            with self.assertRaises(StoreError):
                EvidenceStore(root).verify()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
