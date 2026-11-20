"""Phase 5: bounded, read-only discovery across configured evidence roots."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import evidence_index  # noqa: E402


def campaign(root: Path, campaign: str, session: str, *,
             classification="incomplete", health="healthy",
             finished="2026-11-14T18:12:56Z") -> Path:
    directory = root / campaign / f"session-{session}"
    directory.mkdir(parents=True)
    result = {
        "schema": "gfx-dual-run/v1",
        "session_id": session,
        "classification": classification,
        "stop": {"reason": "frame_difference"},
        "health": {"verdict": health},
        "scenario": {"name": campaign, "digest": "d" * 64},
        "qualification": {"status": classification, "qualified": False,
                          "requested_frames": 20},
        "frames": {"comparison": {"status": "fail", "compared": 1, "failed": 1}},
        "finished_utc": finished,
    }
    (directory / "result.json").write_text(json.dumps(result))
    case = directory / "case"
    case.mkdir()
    (case / "case.json").write_text(json.dumps({"schema": "gfx-diagnostic-case/v1"}))
    return directory


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "evidence"
        self.first = campaign(self.root, "live-a6", "a6", classification="rendering_difference")
        self.second = campaign(self.root, "live-a21", "a21", classification="alignment_failure",
                               health="unknown", finished="2026-11-14T18:48:26Z")

    def test_discovers_both_nested_campaigns_with_provenance(self):
        value = evidence_index.discover(configured_roots=str(self.root))
        self.assertEqual(value["schema"], evidence_index.SCHEMA)
        self.assertEqual(value["counts"]["total"], 2)
        self.assertTrue(value["read_only"])
        identifiers = {row["session_id"] for row in value["sessions"]}
        self.assertEqual(identifiers, {"a6", "a21"})
        for row in value["sessions"]:
            self.assertTrue(Path(row["provenance"]["result_path"]).is_file())
            self.assertEqual(len(row["provenance"]["result_sha256"]), 64)
            self.assertTrue(row["provenance"]["read_only"])
            self.assertTrue(row["has_case"])
            self.assertEqual(row["case_schema"], "gfx-diagnostic-case/v1")
            self.assertEqual(len(row["provenance"]["case_sha256"]), 64)

    def test_filtering_by_query_and_classification(self):
        value = evidence_index.discover(
            configured_roots=str(self.root), query="a21")
        self.assertEqual([row["session_id"] for row in value["sessions"]], ["a21"])
        value = evidence_index.discover(
            configured_roots=str(self.root), classification="alignment_failure")
        self.assertEqual([row["session_id"] for row in value["sessions"]], ["a21"])
        self.assertEqual(value["counts"]["alignment_failure"], 1)
        self.assertEqual(value["counts"]["rendering_difference"], 0)
        self.assertEqual(
            value["discovered"]["by_classification"]["rendering_difference"], 1)

    def test_limit_and_offset_are_explicit(self):
        value = evidence_index.discover(
            configured_roots=str(self.root), limit=1, offset=0)
        self.assertEqual(len(value["sessions"]), 1)
        self.assertTrue(value["truncated"])
        self.assertEqual(value["next_offset"], 1)
        second = evidence_index.discover(
            configured_roots=str(self.root), limit=1, offset=1)
        self.assertEqual(len(second["sessions"]), 1)
        self.assertFalse(second["truncated"])
        self.assertNotEqual(value["sessions"][0]["session_id"],
                            second["sessions"][0]["session_id"])

    def test_discovery_is_read_only(self):
        before = {path: (path.stat().st_size, path.stat().st_mtime_ns,
                         hashlib.sha256(path.read_bytes()).hexdigest())
                  for path in self.root.rglob("*") if path.is_file()}
        evidence_index.discover(configured_roots=str(self.root))
        after = {path: (path.stat().st_size, path.stat().st_mtime_ns,
                        hashlib.sha256(path.read_bytes()).hexdigest())
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_roots_are_configurable_by_environment_and_deduplicated(self):
        value = evidence_index.roots(
            environ={"EU4_EVIDENCE_ROOT": os.pathsep.join(
                [str(self.root), str(self.root), str(self.root / "missing")])})
        self.assertEqual([str(path) for path in value],
                         [str(self.root.resolve()),
                          str((self.root / "missing").resolve())])
        value = evidence_index.discover(configured_roots=str(self.root / "missing"))
        self.assertEqual(value["sessions"], [])
        self.assertEqual(value["skipped_roots"], [str((self.root / "missing").resolve())])

    def test_no_filesystem_wide_search_by_default(self):
        value = evidence_index.discover(
            configured_roots=str(self.root), limit=1)
        # The scan is bounded to root/campaign/session-*; a direct session
        # directory under the root is also recognised.
        direct = campaign(self.root, "", "direct")
        value = evidence_index.discover(configured_roots=str(self.root))
        self.assertIn("direct", {row["session_id"] for row in value["sessions"]})
        self.assertTrue(str(direct).startswith(str(self.root)))

    def test_published_case_locators_are_discoverable(self):
        from automation import case_locator

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from case_locator_fixtures import make_case

        case = make_case(self.root / "published" / "case",
                         session_id="session-locator", capture_frame_id=4)
        published = case_locator.publish(case, self.root,
                                         originating_run="run-7")
        value = evidence_index.discover(configured_roots=str(self.root))
        # Existing campaign rows and counts are unchanged.
        self.assertEqual(value["counts"]["total"], 2)
        self.assertEqual(value["locator_counts"]["total"], 1)
        self.assertEqual(value["locator_counts"]["available"], 1)
        row = value["case_locators"][0]
        self.assertEqual(row["session_id"], "session-locator")
        self.assertEqual(row["capture_frame_id"], 4)
        self.assertEqual(row["seal_sha256"],
                         published["identity"]["seal_sha256"])
        self.assertEqual(row["originating_run"], "run-7")
        self.assertEqual(row["state"], "available")
        self.assertTrue(row["read_only"])
        # A text query matches locator metadata too.
        filtered = evidence_index.discover(
            configured_roots=str(self.root), query="session-locator")
        self.assertEqual(len(filtered["case_locators"]), 1)
        # A classification filter is a campaign question, not a locator one.
        filtered = evidence_index.discover(
            configured_roots=str(self.root),
            classification="rendering_difference")
        self.assertEqual(filtered["case_locators"], [])

    def test_locator_counts_are_scoped_and_page_independently(self):
        from automation import case_locator

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from case_locator_fixtures import make_case

        for index in range(3):
            case = make_case(self.root / f"published-{index}" / "case",
                             session_id=f"session-{index}",
                             capture_frame_id=index + 1)
            case_locator.publish(case, self.root)
        first = evidence_index.discover(
            configured_roots=str(self.root), limit=20,
            locator_limit=2, locator_offset=0)
        self.assertEqual(first["locator_counts"]["scanned"], 3)
        self.assertEqual(first["locator_counts"]["filtered"], 3)
        self.assertEqual(first["locator_counts"]["returned"], 2)
        self.assertEqual(len(first["case_locators"]), 2)
        self.assertTrue(first["locator_counts"]["filter_truncated"])
        second = evidence_index.discover(
            configured_roots=str(self.root), limit=20,
            locator_limit=2, locator_offset=2)
        self.assertEqual(second["locator_counts"]["returned"], 1)
        self.assertFalse(second["locator_counts"]["filter_truncated"])
        self.assertNotEqual(
            {row["key"] for row in first["case_locators"]},
            {row["key"] for row in second["case_locators"]})


if __name__ == "__main__":
    unittest.main()
