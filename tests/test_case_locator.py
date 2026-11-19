"""Publish, relocate and resolve a verified portable case locator."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import case_locator, gfx_replay_case  # noqa: E402


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_case(root: Path, *, case_id: str = "synthetic",
              session_id: str = "session-1",
              capture_frame_id: int = 7,
              report_status: str = "partial") -> Path:
    root.mkdir(parents=True)
    (root / "capture").mkdir()
    (root / "shaders").mkdir()
    (root / "capture" / "frame-trace.json").write_text(
        json.dumps({"schema": "gfx-frame-trace/v1", "complete": True}))
    (root / "capture" / "frame-events.jsonl").write_text("")
    (root / "capture" / "frame-seal.json").write_text(json.dumps({
        "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
        "session_id": session_id,
        "capture_frame_id": capture_frame_id,
        "event_stream_sha256": _sha(b"events"),
        "reference_sha256": _sha(b"reference"),
    }))
    (root / "shaders" / "shaders.json").write_text("{}")
    (root / "report.json").write_text(json.dumps({"status": report_status}))
    (root / "graph.json").write_text(json.dumps({"status": "partial"}))
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        files.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(payload),
            "sha256": _sha(payload),
        })
    (root / "case.json").write_text(json.dumps({
        "schema": gfx_replay_case.SCHEMA,
        "case_id": case_id,
        "files": files,
        "replay_status": "partial",
    }))
    return root


class CaseLocatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.evidence = self.root / "evidence"

    def test_publish_then_locate_reports_the_verified_copy(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(
            case, self.evidence, originating_run="run-1")
        self.assertTrue(published["reused"] is False)
        self.assertEqual(published["case_id"], "synthetic")
        self.assertEqual(published["identity"]["session_id"], "session-1")
        self.assertEqual(published["identity"]["capture_frame_id"], 7)
        self.assertTrue(Path(published["locator_path"]).is_file())
        resolved = case_locator.locate(
            self.evidence, session_id="session-1", capture_frame_id=7,
            seal_sha256=published["identity"]["seal_sha256"])
        self.assertEqual(resolved["state"], "available", resolved)
        match = resolved["matches"][0]
        self.assertEqual(match["originating_run"], "run-1")
        self.assertTrue(Path(match["resolved_path"], "case.json").is_file())
        self.assertEqual(
            case_locator.case_identity(match["resolved_path"])[
                "manifest_sha256"],
            published["identity"]["manifest_sha256"])

    def test_lookup_survives_removal_of_the_producer_worktree(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        shutil.rmtree(self.root / "producer")
        resolved = case_locator.locate(
            self.evidence, session_id="session-1",
            seal_sha256=published["identity"]["seal_sha256"])
        self.assertEqual(resolved["state"], "available", resolved)

    def test_two_cases_with_one_name_resolve_separately(self) -> None:
        first = make_case(self.root / "one" / "case", case_id="same-name",
                          session_id="session-a", capture_frame_id=1)
        second = make_case(self.root / "two" / "case", case_id="same-name",
                           session_id="session-b", capture_frame_id=2)
        one = case_locator.publish(first, self.evidence)
        two = case_locator.publish(second, self.evidence)
        self.assertNotEqual(one["key"], two["key"])
        resolved = case_locator.locate(
            self.evidence, case_id="same-name", session_id="session-b")
        self.assertEqual(resolved["state"], "available", resolved)
        self.assertEqual(
            resolved["matches"][0]["identity"]["seal_sha256"],
            two["identity"]["seal_sha256"])

    def test_missing_payload_and_digest_drift_are_distinct(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = Path(published["locator_path"]).parents[1] / published[
            "relative_path"]
        shutil.rmtree(target)
        missing = case_locator.locate(self.evidence, session_id="session-1")
        self.assertEqual(missing["state"], "missing_payload", missing)

        # Re-publish, then change a copied payload without touching case.json.
        case_locator.publish(case, self.evidence)
        (target / "report.json").write_text(json.dumps({"status": "tampered"}))
        drift = case_locator.locate(self.evidence, session_id="session-1")
        self.assertEqual(drift["state"], "digest_mismatch", drift)

    def test_duplicate_publish_reuses_one_verified_copy(self) -> None:
        case = make_case(self.root / "producer" / "case")
        first = case_locator.publish(case, self.evidence)
        second = case_locator.publish(case, self.evidence)
        self.assertTrue(second["reused"])
        self.assertEqual(first["key"], second["key"])
        case_dirs = [path for path in (self.evidence / "cases").iterdir()
                     if path.is_dir() and not path.name.startswith(".")]
        self.assertEqual(len(case_dirs), 1)

    def test_conflicting_bytes_under_one_seal_are_refused(self) -> None:
        first = make_case(self.root / "one" / "case")
        second = make_case(self.root / "two" / "case",
                           report_status="different")
        case_locator.publish(first, self.evidence)
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(second, self.evidence)
        self.assertEqual(raised.exception.kind, "locator_conflict")

    def test_unverified_source_is_not_published(self) -> None:
        case = make_case(self.root / "producer" / "case")
        (case / "report.json").write_text(json.dumps({"status": "changed"}))
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "case_not_verified")
        self.assertFalse((self.evidence / "locators").exists())

    def test_unknown_and_unavailable_roots_are_explicit(self) -> None:
        unknown = case_locator.locate(self.evidence, session_id="none")
        self.assertEqual(unknown["state"], "root_unavailable")
        self.evidence.mkdir()
        self.assertEqual(
            case_locator.locate(self.evidence, session_id="none")["state"],
            "unknown")
        self.assertEqual(
            case_locator.inventory(self.root / "missing")["state"],
            "root_unavailable")

    def test_inventory_counts_states(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        inventory = case_locator.inventory(self.evidence)
        self.assertEqual(inventory["state"], "available")
        self.assertEqual(inventory["counts"], {"available": 1})
        target = Path(inventory["locators"][0]["resolved_path"])
        shutil.rmtree(target)
        inventory = case_locator.inventory(self.evidence)
        self.assertEqual(inventory["counts"], {"missing_payload": 1})
        self.assertEqual(inventory["locators"][0]["key"], published["key"])

    def test_legacy_case_without_a_seal_uses_the_manifest_key(self) -> None:
        case = make_case(self.root / "producer" / "case")
        (case / "capture" / "frame-seal.json").unlink()
        # The manifest still declares the deleted seal, so rebuild it.
        files = []
        for path in sorted(case.rglob("*")):
            if not path.is_file() or path.name == "case.json":
                continue
            payload = path.read_bytes()
            files.append({
                "path": path.relative_to(case).as_posix(),
                "bytes": len(payload),
                "sha256": _sha(payload),
            })
        (case / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SCHEMA,
            "case_id": "legacy",
            "files": files,
            "replay_status": "partial",
        }))
        published = case_locator.publish(case, self.evidence)
        self.assertTrue(published["key"].startswith("manifest-"))
        self.assertIn("legacy case has no frame seal",
                      " ".join(published["limitations"]))


if __name__ == "__main__":
    unittest.main()
