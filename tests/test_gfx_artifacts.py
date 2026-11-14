"""PR 1 acceptance: bounded, deduplicated diagnostic artifact storage."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_artifacts  # noqa: E402


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name) / "store"

    def tearDown(self):
        self._temporary.cleanup()

    def store(self, limit: int = 1024) -> gfx_artifacts.ArtifactStore:
        return gfx_artifacts.ArtifactStore(self.root, max_bytes=limit)

    def test_content_addressing_and_dedup(self):
        store = self.store()
        first = store.put_bytes(b"A" * 100, kind="frame", logical_name="first")
        second = store.put_bytes(b"A" * 100, kind="frame", logical_name="second")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(store.summary()["objects"], 1)
        self.assertEqual(store.summary()["deduplicated_bytes_saved"], 100)
        self.assertEqual(store.read(first.sha256), b"A" * 100)

    def test_identical_payloads_have_one_object_file(self):
        store = self.store()
        item = store.put_bytes(b"same", kind="frame")
        object_path = store.get(item.sha256)
        store.put_bytes(b"same", kind="frame")
        self.assertEqual(len(list((self.root / "objects").rglob("*"))), 2)  # dir + file
        self.assertEqual(object_path.read_bytes(), b"same")

    def test_oldest_non_preserved_payload_is_evicted(self):
        store = self.store(limit=100)
        first = store.put_bytes(b"A" * 60, kind="frame")
        second = store.put_bytes(b"B" * 30, kind="frame")
        third = store.put_bytes(b"C" * 60, kind="frame")
        self.assertIsNotNone(third)
        with self.assertRaises(gfx_artifacts.ArtifactError):
            store.get(first.sha256)
        self.assertEqual(store.read(second.sha256), b"B" * 30)
        self.assertEqual(store.read(third.sha256), b"C" * 60)
        self.assertLessEqual(store.summary()["bytes"], 100)

    def test_preserved_failure_evidence_is_never_evicted(self):
        store = self.store(limit=50)
        preserved = store.put_bytes(b"P" * 80, kind="frame", preserved=True)
        self.assertIsNotNone(preserved)
        self.assertTrue(store.summary()["over_limit"])
        dropped = store.put_bytes(b"N" * 10, kind="frame")
        self.assertIsNone(dropped)
        self.assertEqual(store.read(preserved.sha256), b"P" * 80)
        self.assertEqual(store.summary()["preserved_bytes"], 80)
        self.assertEqual(store.summary()["dropped"], 1)
        self.assertEqual(store.dropped[0]["reason"][:20], "storage budget 50 by")

    def test_preserving_an_existing_deduplicated_payload_upgrades_it(self):
        store = self.store(limit=200)
        item = store.put_bytes(b"D" * 10, kind="frame")
        upgraded = store.put_bytes(b"D" * 10, kind="frame", preserved=True)
        self.assertTrue(upgraded.preserved)
        self.assertEqual(store.summary()["preserved_objects"], 1)

    def test_disabled_store_accounts_drops(self):
        store = gfx_artifacts.ArtifactStore(self.root, enabled=False)
        self.assertIsNone(store.put_bytes(b"x", kind="frame", source="live"))
        self.assertEqual(store.summary()["dropped"], 1)
        self.assertEqual(store.dropped[0]["reason"], "storage disabled")

    def test_manifest_records_retained_and_dropped(self):
        store = self.store(limit=20)
        keep = store.put_bytes(b"K" * 10, kind="frame", preserved=True, logical_name="post")
        store.put_bytes(b"X" * 30, kind="frame")
        path = store.write_manifest()
        value = json.loads(path.read_text())
        self.assertEqual(value["schema"], gfx_artifacts.SCHEMA)
        self.assertEqual(len(value["artifacts"]), 1)
        self.assertEqual(value["artifacts"][0]["sha256"], keep.sha256)
        self.assertEqual(value["artifacts"][0]["logical_name"], "post")
        self.assertEqual(len(value["dropped"]), 1)

    def test_index_survives_reopen(self):
        store = self.store()
        item = store.put_bytes(b"persist", kind="frame")
        reopened = gfx_artifacts.ArtifactStore(self.root, max_bytes=1024)
        self.assertEqual(reopened.read(item.sha256), b"persist")
        self.assertEqual(reopened.summary()["objects"], 1)

    def test_invalid_limit_is_rejected(self):
        with self.assertRaises(gfx_artifacts.ArtifactError):
            gfx_artifacts.ArtifactStore(self.root, max_bytes=0)


if __name__ == "__main__":
    unittest.main()
