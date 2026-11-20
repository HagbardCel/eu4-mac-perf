"""Publish, relocate and resolve a verified portable case locator."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    case_locator,
    evidence_index,
    gfx_replay_case,
)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from case_locator_fixtures import make_case, make_sequence_case  # noqa: E402


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# Generated test data only: no game, shader or texture payload is checked in.
SYNTHETIC_BLOB = "blobs/synthetic-checker.bgra"
SYNTHETIC_PAYLOAD = bytes(range(256)) * 16


def _tree_stats(root: Path) -> tuple[int, int]:
    """Regular-file bytes and count, matching the publication inventory."""
    total = 0
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
            count += 1
    return total, count


def _valid_digest(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value.lower()))


def _restore_report(case_dir: Path) -> dict:
    """Check a restored tree against its own declared manifest.

    A restore is byte-identical only when every declared member exists with the
    declared size and SHA-256.  Missing bytes, a missing digest and a changed
    size/digest are distinct failures so a caller cannot report a silent
    "available" or treat an absent digest as zero.
    """
    manifest = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    report = {
        "ok": True,
        "files": 0,
        "bytes": 0,
        "missing": [],
        "missing_digest": [],
        "size_mismatch": [],
        "digest_mismatch": [],
    }
    for item in manifest.get("files") or []:
        relative = item.get("path")
        target = case_dir / relative if isinstance(relative, str) else None
        expected_size = item.get("bytes")
        expected_digest = item.get("sha256")
        if target is None or not target.is_file() or target.is_symlink():
            report["missing"].append({
                "path": relative,
                "bytes": expected_size,
                "sha256": expected_digest,
            })
            continue
        size = target.stat().st_size
        report["files"] += 1
        report["bytes"] += size
        if size != expected_size:
            report["size_mismatch"].append({
                "path": relative,
                "expected": expected_size,
                "actual": size,
            })
        if not _valid_digest(expected_digest):
            report["missing_digest"].append({
                "path": relative,
                "sha256": expected_digest,
            })
            continue
        if _sha(target.read_bytes()) != expected_digest:
            report["digest_mismatch"].append({
                "path": relative,
                "expected": expected_digest,
            })
    report["ok"] = not any(report[key] for key in
                           ("missing", "missing_digest", "size_mismatch",
                            "digest_mismatch"))
    return report


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

    def test_restore_after_producer_removal_is_byte_identical(self) -> None:
        case = make_case(self.root / "producer" / "case",
                         extra_files={SYNTHETIC_BLOB: SYNTHETIC_PAYLOAD})
        manifest = json.loads((case / "case.json").read_text(encoding="utf-8"))
        expected_bytes = sum(int(item["bytes"]) for item in manifest["files"])
        published = case_locator.publish(case, self.evidence)
        payload = (Path(published["locator_path"]).parents[1]
                   / published["relative_path"])

        # The producer worktree disappears; only the retained publication
        # remains for the restore.
        shutil.rmtree(self.root / "producer")
        restored = self.root / "restore" / "case"
        shutil.copytree(payload, restored)

        report = _restore_report(restored)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["files"], len(manifest["files"]))
        self.assertEqual(report["bytes"], expected_bytes)
        self.assertEqual(_tree_stats(restored),
                         (published["size_bytes"], published["files"]))
        self.assertEqual(
            case_locator.case_identity(restored)["manifest_sha256"],
            published["identity"]["manifest_sha256"])
        verification = gfx_replay_case.verify(restored)
        self.assertTrue(verification["ok"], verification["problems"])
        self.assertEqual((restored / SYNTHETIC_BLOB).read_bytes(),
                         SYNTHETIC_PAYLOAD)

    def test_restore_rejects_missing_member_digest_and_size_drift(self) -> None:
        case = make_case(self.root / "producer" / "case",
                         extra_files={SYNTHETIC_BLOB: SYNTHETIC_PAYLOAD})
        published = case_locator.publish(case, self.evidence)
        payload = (Path(published["locator_path"]).parents[1]
                   / published["relative_path"])
        expected_digest = _sha(SYNTHETIC_PAYLOAD)

        def fresh(name: str) -> Path:
            target = self.root / "restore" / name / "case"
            shutil.copytree(payload, target)
            return target

        # A declared member whose bytes are absent is not a successful restore,
        # even though the rest of the tree is intact.
        missing = fresh("missing")
        (missing / SYNTHETIC_BLOB).unlink()
        report = _restore_report(missing)
        self.assertFalse(report["ok"])
        self.assertEqual([row["path"] for row in report["missing"]],
                         [SYNTHETIC_BLOB])
        self.assertEqual(report["missing"][0]["sha256"], expected_digest)
        self.assertIn(f"missing {SYNTHETIC_BLOB}",
                      gfx_replay_case.verify(missing)["problems"])

        # A declared member without a digest is unknown, never byte-identical.
        missing_digest = fresh("missing-digest")
        manifest_path = missing_digest / "case.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest["files"]:
            if item["path"] == SYNTHETIC_BLOB:
                item.pop("sha256", None)
        manifest_path.write_text(json.dumps(manifest))
        report = _restore_report(missing_digest)
        self.assertFalse(report["ok"])
        self.assertEqual([row["path"] for row in report["missing_digest"]],
                         [SYNTHETIC_BLOB])
        self.assertIn(f"digest drift {SYNTHETIC_BLOB}",
                      gfx_replay_case.verify(missing_digest)["problems"])

        # A present member with a changed size is size/digest drift, not a
        # silent digest match.
        size_drift = fresh("size")
        blob = size_drift / SYNTHETIC_BLOB
        blob.write_bytes(blob.read_bytes()[:-1])
        report = _restore_report(size_drift)
        self.assertFalse(report["ok"])
        self.assertEqual([row["path"] for row in report["size_mismatch"]],
                         [SYNTHETIC_BLOB])
        self.assertIn(f"size drift {SYNTHETIC_BLOB}",
                      gfx_replay_case.verify(size_drift)["problems"])

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
        self.assertFalse(drift["matches"][0]["verified"])
        self.assertFalse(drift["matches"][0]["verification_ok"])

    def test_duplicate_publish_reuses_one_verified_copy(self) -> None:
        case = make_case(self.root / "producer" / "case")
        first = case_locator.publish(case, self.evidence,
                                     originating_run="run-1")
        second = case_locator.publish(case, self.evidence)
        self.assertTrue(second["reused"])
        self.assertEqual(first["key"], second["key"])
        self.assertEqual(second["size_bytes"], first["size_bytes"])
        self.assertGreater(second["size_bytes"], 0)
        self.assertEqual(second["files"], first["files"])
        self.assertEqual(second["originating_run"], "run-1")
        self.assertEqual(second["first_published_utc"],
                         first["first_published_utc"])
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

    def publish_concurrently(self, cases):
        """Run publishers through a deterministic rename barrier."""
        results: list[dict] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(len(cases))
        original = os.rename

        def rename(source, destination):
            if str(destination).endswith("/case"):
                barrier.wait(timeout=15)
            return original(source, destination)

        def run(case):
            try:
                results.append(case_locator.publish(case, self.evidence))
            except Exception as exc:  # noqa: BLE001 - captured for the test
                errors.append(exc)

        with mock.patch("automation.case_locator.os.rename",
                        side_effect=rename):
            threads = [threading.Thread(target=run, args=(case,))
                       for case in cases]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        return results, errors

    def test_concurrent_identical_publications_converge(self) -> None:
        case = make_case(self.root / "producer" / "case")
        results, errors = self.publish_concurrently([case, case])
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual({row["key"] for row in results},
                         {results[0]["key"]})
        resolved = case_locator.locate(
            self.evidence, session_id="session-1")
        self.assertEqual(resolved["state"], "available", resolved)
        payloads = [path for path in (self.evidence / "cases").iterdir()
                    if path.is_dir() and not path.name.startswith(".")]
        self.assertEqual(len(payloads), 1)
        self.assertEqual(
            [path for path in (self.evidence / "locators").iterdir()
             if path.suffix == ".tmp"], [])

    def test_concurrent_different_manifests_under_one_seal_conflict(self) -> None:
        first = make_case(self.root / "one" / "case",
                          report_status="first")
        second = make_case(self.root / "two" / "case",
                           report_status="second")
        results, errors = self.publish_concurrently([first, second])
        self.assertEqual(len(results), 1, results)
        self.assertEqual(len(errors), 1, errors)
        self.assertIsInstance(errors[0], case_locator.CaseLocatorError)
        self.assertEqual(errors[0].kind, "locator_conflict")
        # The published locator must describe the bytes it points at.
        resolved = case_locator.locate(
            self.evidence, session_id="session-1")
        self.assertEqual(resolved["state"], "available", resolved)
        match = resolved["matches"][0]
        self.assertEqual(match["identity"]["manifest_sha256"],
                         results[0]["identity"]["manifest_sha256"])
        self.assertEqual(
            case_locator.case_identity(match["resolved_path"])[
                "manifest_sha256"],
            match["identity"]["manifest_sha256"])

    def test_corrupted_existing_destination_is_not_adopted(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = Path(published["locator_path"]).parents[1] / published[
            "relative_path"]
        (target / "report.json").write_text(json.dumps({"status": "corrupt"}))
        original = case_locator.case_identity(case)["manifest_sha256"]
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "published_copy_invalid")
        self.assertEqual(
            case_locator.case_identity(case)["manifest_sha256"], original)

    def test_interrupted_copy_leaves_no_payload_or_locator(self) -> None:
        case = make_case(self.root / "producer" / "case")
        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=OSError("injected copy failure")):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "copy_failed")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_interrupted_staged_verification_does_not_publish(self) -> None:
        case = make_case(self.root / "producer" / "case")
        ok = gfx_replay_case.verify(case)
        with mock.patch("automation.case_locator._verify",
                        side_effect=[ok, {"ok": False,
                                          "problems": ["injected"]}]):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "copied_case_invalid")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_interrupted_locator_write_removes_only_its_temp(self) -> None:
        case = make_case(self.root / "producer" / "case")
        with mock.patch("automation.case_locator.os.replace",
                        side_effect=OSError("injected locator failure")):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "locator_write")
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))
        self.assertEqual(
            [path for path in (self.evidence / "locators").iterdir()
             if path.suffix == ".tmp"], [])
        # The verified payload may remain; it is not advertised without a
        # locator and a later publication reuses it only after verification.
        resolved = case_locator.locate(self.evidence, session_id="session-1")
        self.assertEqual(resolved["state"], "unknown", resolved)

    def test_sequence_case_uses_the_target_frame_seal(self) -> None:
        case = make_sequence_case(self.root / "producer" / "sequence")
        identity = case_locator.case_identity(case)
        self.assertEqual(identity["seal_file"],
                         "frames/000002/frame-seal.json")
        self.assertEqual(identity["session_id"], "sequence-session")
        self.assertEqual(identity["capture_frame_id"], 2)
        self.assertIsNotNone(identity["seal_sha256"])
        published = case_locator.publish(case, self.evidence)
        self.assertFalse(any("no frame seal" in item
                             for item in published["limitations"]))
        # Relocate away from the producer and resolve by capture identity.
        shutil.rmtree(self.root / "producer")
        resolved = case_locator.locate(
            self.evidence, session_id="sequence-session",
            capture_frame_id=2,
            seal_sha256=identity["seal_sha256"])
        self.assertEqual(resolved["state"], "available", resolved)
        self.assertTrue(resolved["matches"][0]["verified"])
        inventory = case_locator.inventory(self.evidence, verify=True)
        self.assertEqual(inventory["counts"], {"available": 1})

    def test_inventory_and_discovery_are_metadata_only(self) -> None:
        case = make_case(self.root / "producer" / "case")
        case_locator.publish(case, self.evidence)
        with mock.patch("automation.case_locator._verify",
                        side_effect=AssertionError("must not hash")), \
                mock.patch("automation.gfx_replay_case.verify",
                           side_effect=AssertionError("must not verify")):
            inventory = case_locator.inventory(self.evidence)
            summaries = case_locator.summaries(self.evidence)
            discovered = evidence_index.discover(
                configured_roots=str(self.evidence))
        self.assertEqual(inventory["counts"], {"available": 1})
        self.assertFalse(inventory["verified"])
        self.assertFalse(inventory["locators"][0]["verified"])
        self.assertEqual(len(summaries), 1)
        self.assertEqual(discovered["locator_counts"]["available"], 1)
        verified = case_locator.inventory(self.evidence, verify=True)
        self.assertTrue(verified["verified"])
        self.assertTrue(verified["locators"][0]["verified"])

    def test_locate_requires_a_bounded_query(self) -> None:
        case = make_case(self.root / "producer" / "case")
        case_locator.publish(case, self.evidence)
        refused = case_locator.locate(self.evidence)
        self.assertEqual(refused["state"], "refused_unbounded", refused)
        self.assertEqual(refused["matches"], [])

    def test_locate_limit_and_locator_cap_are_explicit(self) -> None:
        first = make_case(self.root / "one" / "case", capture_frame_id=1)
        second = make_case(self.root / "two" / "case", capture_frame_id=2)
        case_locator.publish(first, self.evidence)
        case_locator.publish(second, self.evidence)
        limited = case_locator.locate(
            self.evidence, session_id="session-1", limit=1)
        self.assertEqual(limited["returned"], 1)
        self.assertTrue(limited["truncated"])
        with mock.patch("automation.case_locator.MAX_LOCATORS", 1):
            truncated = case_locator.locate(
                self.evidence, session_id="absent")
            inventory = case_locator.inventory(self.evidence)
        self.assertEqual(truncated["state"], "unknown_truncated", truncated)
        self.assertTrue(truncated["scan_truncated"])
        self.assertTrue(inventory["truncated"])
        self.assertEqual(inventory["scan_limit"], 1)


    def test_verified_lookup_with_an_unreturned_match_is_not_reusable(self):
        first = make_case(self.root / "one" / "case", capture_frame_id=1)
        second = make_case(self.root / "two" / "case", capture_frame_id=2)
        case_locator.publish(first, self.evidence)
        published = case_locator.publish(second, self.evidence)
        target = (Path(published["locator_path"]).parents[1]
                  / published["relative_path"])
        (target / "report.json").write_bytes(b"corrupt")
        located = case_locator.locate(
            self.evidence, session_id="session-1", limit=1, verify=True)
        self.assertEqual(located["state"], "truncated_unverified", located)
        self.assertFalse(located["safe_to_reuse"])
        self.assertFalse(located["verified_all"])
        self.assertEqual(located["state_scope"], "returned_matches_only")
        self.assertEqual(located["verification_scope"],
                         "verified_returned_only")
        self.assertEqual(located["verified_matches"], 1)

    def test_fully_verified_single_match_is_reusable(self):
        case = make_case(self.root / "producer" / "case")
        case_locator.publish(case, self.evidence)
        located = case_locator.locate(
            self.evidence, session_id="session-1", limit=10, verify=True)
        self.assertEqual(located["state"], "available", located)
        self.assertTrue(located["verified_all"])
        self.assertTrue(located["safe_to_reuse"])
        self.assertEqual(located["verified_matches"], 1)
        self.assertEqual(located["verification_scope"],
                         "verified_all_matched")
        self.assertEqual(located["state_scope"], "all_matched")
        self.assertTrue(located["matches"][0]["verified"])

    def test_metadata_only_lookup_is_explicitly_unverified(self):
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = (Path(published["locator_path"]).parents[1]
                  / published["relative_path"])
        (target / "report.json").write_bytes(b"corrupt")
        located = case_locator.locate(
            self.evidence, session_id="session-1", limit=10, verify=False)
        # Metadata can locate the package even when its bytes are corrupt...
        self.assertEqual(located["state"], "available", located)
        # ...but it must never present that as verified or reusable.
        self.assertFalse(located["verified_all"])
        self.assertFalse(located["safe_to_reuse"])
        self.assertEqual(located["verified_matches"], 0)
        self.assertEqual(located["verification_scope"], "metadata_only")
        self.assertEqual(located["state_scope"], "metadata_only")
        self.assertFalse(located["matches"][0]["verified"])
        self.assertEqual(located["matches"][0]["current_verification"],
                         "not_run")

    def test_corrupt_evidence_is_located_unverified_but_rejected_when_verified(self):
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = (Path(published["locator_path"]).parents[1]
                  / published["relative_path"])
        (target / "report.json").write_bytes(b"corrupt")
        verified = case_locator.locate(
            self.evidence, session_id="session-1", limit=10, verify=True)
        self.assertEqual(verified["state"], "digest_mismatch", verified)
        self.assertFalse(verified["verified_all"])
        self.assertFalse(verified["safe_to_reuse"])
        self.assertEqual(verified["verification_scope"],
                         "verified_returned_only")
        self.assertFalse(verified["matches"][0]["verified"])
        self.assertEqual(verified["matches"][0]["current_verification"],
                         "failed")

    def test_truncated_and_offset_verified_lookup_cannot_hide_corruption(self):
        for index in range(3):
            case = make_case(self.root / f"case-{index}" / "case",
                             capture_frame_id=index + 1)
            published = case_locator.publish(case, self.evidence)
            if index == 1:
                target = (Path(published["locator_path"]).parents[1]
                          / published["relative_path"])
                (target / "report.json").write_bytes(b"corrupt")
        first = case_locator.locate(
            self.evidence, session_id="session-1", limit=1, offset=0,
            verify=True)
        self.assertEqual(first["state"], "truncated_unverified", first)
        self.assertFalse(first["verified_all"])
        self.assertFalse(first["safe_to_reuse"])
        self.assertEqual(first["verification_scope"],
                         "verified_returned_only")
        # A page that starts after the corrupt match must not be presented as
        # a safe reuse of the whole matches either.
        last = case_locator.locate(
            self.evidence, session_id="session-1", limit=1, offset=2,
            verify=True)
        self.assertEqual(last["state"], "truncated_unverified", last)
        self.assertFalse(last["verified_all"])
        self.assertFalse(last["safe_to_reuse"])
        # Returning the corrupt match itself must expose the failure.
        middle = case_locator.locate(
            self.evidence, session_id="session-1", limit=1, offset=1,
            verify=True)
        self.assertEqual(middle["state"], "digest_mismatch", middle)
        self.assertFalse(middle["safe_to_reuse"])
        all_matches = case_locator.locate(
            self.evidence, session_id="session-1", limit=10, verify=True)
        self.assertEqual(all_matches["state"], "digest_mismatch", all_matches)
        self.assertFalse(all_matches["verified_all"])
        self.assertFalse(all_matches["safe_to_reuse"])
        # Metadata-only pagination also stays explicitly unverified.
        metadata = case_locator.locate(
            self.evidence, session_id="session-1", limit=1, offset=2,
            verify=False)
        self.assertFalse(metadata["verified_all"])
        self.assertFalse(metadata["safe_to_reuse"])
        self.assertEqual(metadata["verification_scope"], "metadata_only")

    def test_truncated_verified_lookup_exits_non_zero(self):
        for index in range(2):
            case = make_case(self.root / f"case-{index}" / "case",
                             capture_frame_id=index + 1)
            case_locator.publish(case, self.evidence)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = case_locator.main([
                "locate", "--root", str(self.evidence),
                "--session-id", "session-1"])
        self.assertEqual(status, 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value["state"], "truncated_unverified")
        self.assertFalse(value["safe_to_reuse"])

    def test_invalid_locator_prevents_claiming_safe_reuse(self):
        case = make_case(self.root / "producer" / "case")
        case_locator.publish(case, self.evidence)
        (self.evidence / "locators" / "broken.json").write_text(
            "{not json", encoding="utf-8")
        located = case_locator.locate(
            self.evidence, session_id="session-1", limit=10, verify=True)
        self.assertEqual(located["matches"][0]["state"], "available")
        self.assertEqual(located["invalid_locator_count"], 1)
        self.assertFalse(located["verified_all"])
        self.assertFalse(located["safe_to_reuse"])

    def test_inventory_pages_with_offset_and_limit(self):
        for index in range(3):
            case = make_case(self.root / f"case-{index}" / "case",
                             capture_frame_id=index + 1)
            case_locator.publish(case, self.evidence)
        page = case_locator.inventory(self.evidence, offset=1, limit=1)
        self.assertEqual(page["total"], 3)
        self.assertEqual(page["returned"], 1)
        self.assertEqual(page["offset"], 1)
        self.assertTrue(page["truncated"])
        self.assertIsNotNone(page["next_offset"])
        self.assertFalse(page["verified_all"])
        self.assertEqual(page["verification_scope"], "metadata_only")
        # Inventory is a summary, not a selected reusable lookup; it must not
        # expose an implicit safe flag.
        self.assertNotIn("safe_to_reuse", page)

    def test_inventory_pagination_never_upgrades_verification_scope(self):
        for index in range(3):
            case = make_case(self.root / f"case-{index}" / "case",
                             capture_frame_id=index + 1)
            case_locator.publish(case, self.evidence)
        page = case_locator.inventory(
            self.evidence, verify=True, offset=0, limit=1)
        self.assertTrue(page["verified"])
        self.assertFalse(page["verified_all"])
        self.assertEqual(page["verification_scope"],
                         "verified_returned_only")
        self.assertEqual(page["verified_matches"], 1)
        # A positive offset leaves earlier locators unverified even when the
        # page extends to the end of the scan.
        offset = case_locator.inventory(self.evidence, verify=True, offset=1)
        self.assertFalse(offset["verified_all"])
        self.assertTrue(offset["page_truncated"])
        self.assertEqual(offset["verification_scope"],
                         "verified_returned_only")
        full = case_locator.inventory(self.evidence, verify=True)
        self.assertTrue(full["verified_all"])
        self.assertEqual(full["verification_scope"],
                         "verified_all_matched")
        self.assertNotIn("safe_to_reuse", full)

    def test_byte_change_between_inventory_and_copy_is_refused(self):
        case = make_case(self.root / "producer" / "case")
        real = shutil.copytree
        changed = {"done": False}

        def copy_after_source_change(source, destination, *args, **kwargs):
            if not changed["done"] and Path(source).resolve() == case.resolve():
                changed["done"] = True
                victim = Path(source) / "report.json"
                victim.write_bytes(victim.read_bytes() + b"foreign")
            return real(source, destination, *args, **kwargs)

        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=copy_after_source_change):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "copy_changed")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_file_injection_between_inventory_and_copy_is_refused(self):
        case = make_case(self.root / "producer" / "case")
        real = shutil.copytree

        def copy_with_extra_source_file(source, destination, *args, **kwargs):
            if Path(source).resolve() == case.resolve():
                (Path(source) / "injected.bin").write_bytes(b"foreign")
            return real(source, destination, *args, **kwargs)

        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=copy_with_extra_source_file):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "copy_changed")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_symlink_injection_between_inventory_and_copy_is_refused(self):
        case = make_case(self.root / "producer" / "case")
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside bytes")
        real = shutil.copytree

        def copy_with_swapped_source(source, destination, *args, **kwargs):
            if Path(source).resolve() == case.resolve():
                victim = Path(source) / "report.json"
                victim.unlink()
                os.symlink(outside, victim)
            return real(source, destination, *args, **kwargs)

        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=copy_with_swapped_source):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "symlink_source")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_copy_window_directory_symlink_is_refused(self):
        case = make_case(self.root / "producer" / "case")
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "payload.bin").write_bytes(b"foreign")
        real = shutil.copytree

        def copy_then_swap(source, destination, *args, **kwargs):
            result = real(source, destination, *args, **kwargs)
            if Path(destination).parent.name.startswith(".publish-"):
                shaders = Path(destination) / "shaders"
                shutil.rmtree(shaders)
                os.symlink(foreign, shaders)
            return result

        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=copy_then_swap):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "symlink_source")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))

    def test_republish_rejects_a_symlinked_published_payload(self):
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = (Path(published["locator_path"]).parents[1]
                  / published["relative_path"])
        victim = target / "report.json"
        victim.unlink()
        os.symlink(case / "report.json", victim)
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "symlink_source")

    def test_size_budget_and_free_space_are_preflighted(self) -> None:
        case = make_case(self.root / "producer" / "case")
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence, size_budget=1)
        self.assertEqual(raised.exception.kind, "size_budget")
        self.assertFalse((self.evidence / "cases").exists())
        with mock.patch("automation.case_locator.shutil.disk_usage",
                        return_value=(0, 0, 0)):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "insufficient_space")
        self.assertTrue((case / "case.json").is_file())

    def test_evidence_root_cannot_be_the_case_or_inside_it(self) -> None:
        case = make_case(self.root / "producer" / "case")
        before = case_locator.case_identity(case)["manifest_sha256"]
        for root in (case, case / "archive"):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, root)
            self.assertEqual(raised.exception.kind,
                             "evidence_root_inside_case")
            self.assertFalse((case / "cases").exists())
            self.assertEqual(
                case_locator.case_identity(case)["manifest_sha256"], before)
        external = self.root / "durable-evidence"
        published = case_locator.publish(case, external)
        self.assertTrue(Path(published["locator_path"]).is_file())

    def test_metadata_never_claims_current_verification(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        target = Path(published["locator_path"]).parents[1] / published[
            "relative_path"]
        (target / "report.json").write_text(json.dumps({"status": "tampered"}))
        inventory = case_locator.inventory(self.evidence)
        row = inventory["locators"][0]
        self.assertEqual(row["state"], "available")
        self.assertFalse(row["verified"])
        self.assertFalse(row["verification_ok"])
        self.assertEqual(row["current_verification"], "not_run")
        self.assertTrue(row["recorded_verification"]["ok"])
        self.assertNotIn("verification", row)
        located = case_locator.locate(self.evidence, session_id="session-1")
        self.assertEqual(located["state"], "digest_mismatch", located)
        self.assertEqual(located["matches"][0]["current_verification"],
                         "failed")
        self.assertFalse(located["matches"][0]["verification_ok"])

    def test_republish_preserves_original_provenance(self) -> None:
        case = make_case(self.root / "producer" / "case")
        first = case_locator.publish(case, self.evidence,
                                     originating_run="run-1")
        second = case_locator.publish(case, self.evidence,
                                      originating_run="run-2")
        self.assertEqual(second["originating_run"], "run-1")
        self.assertEqual(second["first_published_utc"],
                         first["first_published_utc"])
        self.assertEqual(second["last_published_run"], "run-2")
        self.assertEqual(second["publication_count"], 2)
        third = case_locator.publish(case, self.evidence,
                                     originating_run="run-3")
        self.assertEqual(third["originating_run"], "run-1")
        self.assertEqual(third["last_published_run"], "run-3")
        self.assertEqual(third["publication_count"], 3)

    def test_absent_origin_is_not_backfilled_by_a_later_run(self) -> None:
        case = make_case(self.root / "producer" / "case")
        first = case_locator.publish(case, self.evidence)
        self.assertIsNone(first["originating_run"])
        second = case_locator.publish(case, self.evidence,
                                      originating_run="run-2")
        self.assertIsNone(second["originating_run"])
        self.assertEqual(second["last_published_run"], "run-2")
        self.assertEqual(second["publication_count"], 2)

    def test_corrupt_publication_count_is_a_locator_error(self) -> None:
        case = make_case(self.root / "producer" / "case")
        published = case_locator.publish(case, self.evidence)
        path = Path(published["locator_path"])
        value = json.loads(path.read_text())
        value["publication_count"] = "not-a-number"
        path.write_text(json.dumps(value))
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "locator_corrupt")

    def test_missing_worktree_parent_does_not_create_false_containment(self) -> None:
        self.assertFalse(case_locator._inside(
            Path("/tmp/durable-evidence"), Path("/tmp/missing-worktree")))

    def test_prunable_linked_worktree_does_not_block_external_roots(self) -> None:
        from types import SimpleNamespace
        output = (
            "worktree /repo\n\n"
            "worktree /repo/linked\n"
            "HEAD deadbeef\n"
            "prunable gitdir file points to non-existent location\n\n"
        )
        with mock.patch("automation.case_locator.subprocess.run",
                        return_value=SimpleNamespace(
                            returncode=0, stdout=output)):
            problem = case_locator._evidence_root_problem(
                Path("/repo"), Path("/external/evidence"))
            inside = case_locator._evidence_root_problem(
                Path("/case"), Path("/repo/linked/evidence"))
        self.assertIsNone(problem)
        self.assertIn("disposable linked worktree", inside)

    def test_symlink_in_source_is_refused_before_copying(self) -> None:
        case = make_case(self.root / "producer" / "case")
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside bytes")
        (case / "leak.bin").symlink_to(outside)
        with self.assertRaises(case_locator.CaseLocatorError) as raised:
            case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "symlink_source")
        self.assertFalse((self.evidence / "cases").exists())
        self.assertFalse((self.evidence / "locators").exists())

    def test_inventory_hash_does_not_follow_a_swapped_symlink(self) -> None:
        case = make_case(self.root / "producer" / "case")
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside bytes")
        real = case_locator._sha_nofollow

        def swap_then_hash(path, **kwargs):
            if Path(path).name == "report.json":
                Path(path).unlink()
                os.symlink(outside, path)
            return real(path, **kwargs)

        with mock.patch("automation.case_locator._sha_nofollow",
                        side_effect=swap_then_hash):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator._tree_inventory(case)
        self.assertEqual(raised.exception.kind, "symlink_source")

    def test_symlink_created_during_copy_is_refused(self) -> None:
        case = make_case(self.root / "producer" / "case")
        original = shutil.copytree

        def copy_with_late_symlink(source, destination, *args, **kwargs):
            result = original(source, destination, *args, **kwargs)
            (Path(destination) / "late.bin").symlink_to(
                self.root / "outside.bin")
            return result

        with mock.patch("automation.case_locator.shutil.copytree",
                        side_effect=copy_with_late_symlink):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "symlink_source")
        self.assertFalse(any((self.evidence / "cases").glob("*/case")))
        self.assertFalse(list((self.evidence / "locators").glob("*.json")))

    def test_malformed_schema_valid_locators_are_invalid(self) -> None:
        mutations = {
            "identity-string": lambda value: value.update(identity="not-a-dict"),
            "identity-list": lambda value: value.update(identity=[]),
            "identity-null": lambda value: value.update(identity=None),
            "path-integer": lambda value: value.update(relative_path=7),
            "path-nul": lambda value: value.update(relative_path="a\x00b"),
            "path-surrogate": lambda value: value.update(
                relative_path="\ud800"),
            "verification-string": lambda value: value.update(
                verification="ok"),
            "key-integer": lambda value: value.update(key=9),
            "session-integer": lambda value: value["identity"].update(
                session_id=5),
            "seal-short": lambda value: value["identity"].update(
                seal_sha256="abc"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                evidence = self.root / f"malformed-{name}"
                case = make_case(self.root / f"producer-{name}" / "case")
                published = case_locator.publish(case, evidence)
                path = Path(published["locator_path"])
                value = json.loads(path.read_text())
                mutate(value)
                path.write_text(json.dumps(value))
                inventory = case_locator.inventory(evidence)
                self.assertEqual(inventory["state"], "degraded")
                self.assertEqual(inventory["locators"], [])
                self.assertEqual(inventory["invalid_locator_count"], 1)
                self.assertEqual(case_locator.summaries(evidence), [])
                located = case_locator.locate(evidence, session_id="session-1")
                self.assertEqual(located["state"], "invalid_locator", located)
                discovered = evidence_index.discover(
                    configured_roots=str(evidence))
                self.assertEqual(
                    discovered["locator_counts"]["invalid_locators"], 1)
                self.assertEqual(discovered["case_locators"], [])

    def test_publish_rejects_a_locator_its_scanner_would_reject(self) -> None:
        case = make_case(self.root / "producer" / "malformed-case")
        identity = {
            "case_id": "malformed",
            "session_id": 5,
            "capture_frame_id": 7,
            "event_stream_sha256": "a" * 64,
            "seal_sha256": "b" * 64,
            "seal_file": None,
            "manifest_sha256": "c" * 64,
            "schema": "case/v1",
        }
        with mock.patch("automation.case_locator.case_identity",
                        return_value=identity):
            with self.assertRaises(case_locator.CaseLocatorError) as raised:
                case_locator.publish(case, self.evidence)
        self.assertEqual(raised.exception.kind, "locator_malformed")
        self.assertFalse(
            list((self.evidence / "locators").glob("*.json")))

    def test_invalid_locator_count_is_untruncated(self) -> None:
        self.evidence.mkdir(parents=True)
        directory = self.evidence / "locators"
        directory.mkdir()
        for index in range(25):
            (directory / f"bad-{index:02d}.json").write_text(
                "{not json", encoding="utf-8")
        inventory = case_locator.inventory(self.evidence)
        self.assertEqual(inventory["state"], "degraded")
        self.assertEqual(len(inventory["invalid_locators"]), 20)
        self.assertEqual(inventory["invalid_locator_count"], 25)
        discovered = evidence_index.discover(
            configured_roots=str(self.evidence))
        self.assertEqual(
            discovered["locator_counts"]["invalid_locators"], 25)

    def test_broken_locator_is_reported_not_silently_absent(self) -> None:
        case = make_case(self.root / "producer" / "case")
        case_locator.publish(case, self.evidence)
        broken = self.evidence / "locators" / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        inventory = case_locator.inventory(self.evidence)
        self.assertEqual(inventory["state"], "degraded")
        self.assertEqual(len(inventory["invalid_locators"]), 1)
        located = case_locator.locate(self.evidence, session_id="absent")
        self.assertEqual(located["state"], "invalid_locator", located)
        self.assertEqual(len(located["invalid_locators"]), 1)

    def test_locate_reports_the_worst_selected_state(self) -> None:
        first = make_case(self.root / "one" / "case", capture_frame_id=1)
        second = make_case(self.root / "two" / "case", capture_frame_id=2)
        one = case_locator.publish(first, self.evidence)
        case_locator.publish(second, self.evidence)
        target = Path(one["locator_path"]).parents[1] / one["relative_path"]
        (target / "report.json").write_text(json.dumps({"status": "corrupt"}))
        located = case_locator.locate(self.evidence, session_id="session-1",
                                      limit=10, verify=True)
        self.assertEqual(located["state"], "digest_mismatch", located)
        self.assertEqual(located["matched"], 2)
        self.assertEqual({row["state"] for row in located["matches"]},
                         {"available", "digest_mismatch"})

    def test_verified_locate_is_bounded_by_default(self) -> None:
        first = make_case(self.root / "one" / "case", capture_frame_id=1)
        second = make_case(self.root / "two" / "case", capture_frame_id=2)
        case_locator.publish(first, self.evidence)
        case_locator.publish(second, self.evidence)
        calls = {"n": 0}
        original = case_locator._verify

        def counted(path):
            calls["n"] += 1
            return original(path)

        with mock.patch("automation.case_locator._verify", side_effect=counted):
            located = case_locator.locate(self.evidence,
                                          session_id="session-1")
        self.assertEqual(located["returned"], 1)
        self.assertEqual(located["matched"], 2)
        self.assertTrue(located["truncated"])
        self.assertEqual(calls["n"], 1)

    def test_sequence_prefers_the_target_seal_over_a_capture_seal(self) -> None:
        case = make_sequence_case(self.root / "producer" / "sequence")
        (case / "capture").mkdir()
        (case / "capture" / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True,
            "session_id": "wrong-capture", "capture_frame_id": 99,
        }))
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
        manifest = json.loads((case / "case.json").read_text())
        manifest["files"] = files
        (case / "case.json").write_text(json.dumps(manifest))
        identity = case_locator.case_identity(case)
        self.assertEqual(identity["session_id"], "sequence-session")
        self.assertEqual(identity["capture_frame_id"], 2)
        self.assertEqual(identity["seal_file"],
                         "frames/000002/frame-seal.json")

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
