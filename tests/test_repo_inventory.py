#!/usr/bin/env python3
"""Tests for the repository audit tooling (inventory, graph, lifecycle)."""
import csv
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tooling.common import repo_audit, repo_graph, repo_inventory, repo_lifecycle  # noqa: E402
from tooling.common import repo_snapshot as snapshot_mod  # noqa: E402

GIT = shutil.which("git")


def _git(root, *args):
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


class _Repo:
    """A throwaway Git repository for snapshot-sensitive tests."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "audit@example.invalid")
        _git(self.root, "config", "user.name", "audit")
        _git(self.root, "config", "commit.gpgsign", "false")

    def write(self, rel, content, *, binary=False):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def commit(self, message="commit"):
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", message)
        return _git(self.root, "rev-parse", "HEAD").strip()


@unittest.skipIf(GIT is None, "git not available")
class SnapshotTests(unittest.TestCase):
    def test_unicode_filename_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("notes—dash.md", "# dash\n")
            repo.write("mod.py", "x = 1\n")
            repo.commit()
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            self.assertIn("notes—dash.md", snap.paths())
            self.assertEqual(repo_inventory.kind_of("notes—dash.md"), "markdown")
            rows = repo_inventory.inventory_rows(snap)
            self.assertEqual(sum(1 for r in rows if r["kind"] == "markdown"), 1)
            self.assertEqual(sum(1 for _, n in repo_inventory._counts(rows, "kind")), len(rows))

    def test_ref_ignores_worktree_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.py", "import b\n")
            repo.write("b.py", "y = 1\n")
            ref = repo.commit("A")
            before = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, ref)).edges
            # mutate the worktree without committing
            repo.write("a.py", "import c\n")
            repo.write("c.py", "z = 1\n")
            after = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, ref)).edges
            self.assertEqual(before, after)

    def test_ref_pinning_differs_from_later_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a.py", "x = 1\n")
            ref_a = repo.commit("A")
            repo.write("b.py", "y = 1\n")
            ref_b = repo.commit("B")
            self.assertEqual(len(snapshot_mod.RepoSnapshot.from_ref(repo.root, ref_a).paths()), 1)
            self.assertEqual(len(snapshot_mod.RepoSnapshot.from_ref(repo.root, ref_b).paths()), 2)

    def test_binary_and_unknown_extensions_are_visible_but_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("blob.bin", b"\x00\x01\x02", binary=True)
            repo.write("thing.weird", "text\n")
            repo.commit()
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            graph = repo_graph.build(snap)
            self.assertEqual(graph.analysis["blob.bin"][0], "skipped_not_text")
            self.assertEqual(graph.analysis["thing.weird"][0], "skipped_not_text")
            # still present in the inventory and lifecycle outputs
            self.assertIn("blob.bin", snap.paths())

    def test_exact_blob_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            small = b"x" * 42
            large = b"y" * 1_500_003
            repo.write("small.txt", small, binary=True)
            repo.write("big.txt", large, binary=True)
            repo.commit()
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            self.assertEqual(snap.entry("small.txt").size, len(small))
            self.assertEqual(snap.entry("big.txt").size, len(large))
            # index mode resolves the same sizes explicitly
            idx = snapshot_mod.RepoSnapshot.from_index(repo.root)
            self.assertEqual(idx.entry("small.txt").size, len(small))
            self.assertEqual(idx.entry("big.txt").size, len(large))

    def test_above_size_limit_is_reported_oversized(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            content = "a = 1\n#" + "x" * (repo_graph.MAX_PARSE_BYTES + 10)
            repo.write("huge.py", content)
            repo.commit()
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            self.assertEqual(snap.entry("huge.py").size, len(content.encode("utf-8")))
            graph = repo_graph.build(snap)
            self.assertEqual(graph.analysis["huge.py"][0], "skipped_oversized")

    def test_unmerged_index_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("f.txt", "base\n")
            repo.commit()
            main = _git(repo.root, "rev-parse", "--abbrev-ref", "HEAD").strip()
            _git(repo.root, "checkout", "-q", "-b", "other")
            repo.write("f.txt", "other\n")
            repo.commit()
            _git(repo.root, "checkout", "-q", main)
            repo.write("f.txt", "main\n")
            repo.commit()
            # merge conflict leaves unmerged stages in the index
            subprocess.run(["git", "-C", str(repo.root), "merge", "other"], capture_output=True, text=True)
            with self.assertRaises(snapshot_mod.SnapshotError):
                snapshot_mod.RepoSnapshot.from_index(repo.root)

    def _hash_blob(self, repo, content):
        proc = subprocess.run(
            ["git", "-C", str(repo.root), "hash-object", "-w", "--stdin"],
            input=content,
            capture_output=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.decode().strip()

    def test_lone_nonzero_stage_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            repo.commit()
            blob = self._hash_blob(repo, b"conflicted\n")
            # a single stage-2 entry with no stage-1/stage-3 counterpart
            subprocess.run(
                ["git", "-C", str(repo.root), "update-index", "--index-info"],
                input=f"100644 {blob} 2\tconflicted.txt\n".encode(),
                capture_output=True,
            )
            self.assertEqual(
                [l for l in _git(repo.root, "ls-files", "-s").splitlines() if "conflicted" in l][0].split()[2],
                "2",
            )
            with self.assertRaises(snapshot_mod.SnapshotError):
                snapshot_mod.RepoSnapshot.from_index(repo.root)

    def test_missing_index_blob_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            repo.commit()
            absent = "1234567890abcdef1234567890abcdef12345678"
            subprocess.run(
                ["git", "-C", str(repo.root), "update-index", "--add", "--cacheinfo", f"100644,{absent},missing.txt"],
                capture_output=True,
            )
            self.assertIn("missing.txt", _git(repo.root, "ls-files", "-s"))
            with self.assertRaises(snapshot_mod.SnapshotError):
                snapshot_mod.RepoSnapshot.from_index(repo.root)

    def test_gitlink_without_local_commit_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            repo.commit()
            absent_commit = "1" * 40
            subprocess.run(
                ["git", "-C", str(repo.root), "update-index", "--add", "--cacheinfo", f"160000,{absent_commit},subdir"],
                capture_output=True,
            )
            snap = snapshot_mod.RepoSnapshot.from_index(repo.root)
            entry = snap.entry("subdir")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.obj_type, "commit")
            self.assertEqual(entry.size, -1)
            # a gitlink is never read as text
            self.assertIsNone(snap.read_text("subdir"))

    def test_symlink_index_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            (repo.root / "linkfile").symlink_to("target")
            _git(repo.root, "add", "linkfile")
            snap = snapshot_mod.RepoSnapshot.from_index(repo.root)
            entry = snap.entry("linkfile")
            self.assertEqual(entry.obj_type, "blob")
            self.assertEqual(entry.size, len("target"))
            self.assertEqual(snap.read_text("linkfile"), "target")

    def test_intent_to_add_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            repo.commit()
            repo.write("pending.txt", "nonempty pending content")
            _git(repo.root, "add", "-N", "pending.txt")
            with self.assertRaises(snapshot_mod.SnapshotError):
                snapshot_mod.RepoSnapshot.from_index(repo.root)

    def test_staged_empty_file_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("base.txt", "base\n")
            repo.commit()
            repo.write("empty.txt", "")
            _git(repo.root, "add", "empty.txt")
            snap = snapshot_mod.RepoSnapshot.from_index(repo.root)
            entry = snap.entry("empty.txt")
            self.assertEqual(entry.obj_type, "blob")
            self.assertEqual(entry.size, 0)


class InvariantTests(unittest.TestCase):
    """The archival invariants are enforcement, not decoration: a violation flips ok."""

    def _row(self, **over):
        row = {"path": "x", "archive_eligible": False, "archive_candidate": False, "dependency_conflict": False}
        row.update(over)
        return row

    def test_eligible_with_a_consumer_reference_fails(self):
        stats = repo_lifecycle.invariants([self._row(archive_eligible=True)], 1, {"x"}, {"x"})
        self.assertEqual(stats["n_archive_eligible_with_consumer"], 1)
        self.assertFalse(stats["ok"])

    def test_eligible_without_approval_fails(self):
        stats = repo_lifecycle.invariants([self._row(archive_eligible=True)], 1, set(), {"y"})
        self.assertEqual(stats["n_archive_eligible_but_unapproved"], 1)
        self.assertFalse(stats["ok"])

    def test_unclassified_path_fails(self):
        stats = repo_lifecycle.invariants([self._row()], 2, set(), set())
        self.assertFalse(stats["all_classified"])
        self.assertFalse(stats["ok"])


class GlobMatchTests(unittest.TestCase):
    def test_star_stays_within_one_segment(self):
        self.assertTrue(repo_lifecycle.match("analysis/*.md", "analysis/x.md"))
        self.assertFalse(repo_lifecycle.match("analysis/*.md", "analysis/sub/x.md"))

    def test_question_mark_stays_within_one_segment(self):
        self.assertTrue(repo_lifecycle.match("a?c.md", "abc.md"))
        self.assertFalse(repo_lifecycle.match("a?c.md", "a/c.md"))

    def test_double_star_spans_segments(self):
        self.assertTrue(repo_lifecycle.match("analysis/**/*.md", "analysis/sub/x.md"))
        self.assertTrue(repo_lifecycle.match("analysis/**/*.md", "analysis/a/b/x.md"))
        self.assertTrue(repo_lifecycle.match("results/**", "results/a/b.bin"))
        self.assertTrue(repo_lifecycle.match("results/**", "results"))

    def test_non_glob_pattern_is_exact(self):
        self.assertTrue(repo_lifecycle.match("plan.md", "plan.md"))
        self.assertFalse(repo_lifecycle.match("plan.md", "docs/plan.md"))


@unittest.skipIf(GIT is None, "git not available")
class ImportResolutionTests(unittest.TestCase):
    def _import_targets(self, repo_root, source):
        graph = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo_root, "HEAD"))
        return {e.target for e in graph.edges if e.source == source and e.relationship == "import"}

    def test_relative_import_is_package_scoped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("package_a/consumer.py", "from .helper import process\n")
            # a same-named module in a different package must NOT satisfy the relative import
            repo.write("package_b/helper.py", "def process():\n    return 1\n")
            repo.commit()
            self.assertEqual(self._import_targets(repo.root, "package_a/consumer.py"), set())
            # adding the real sibling resolves exactly, high confidence
            repo.write("package_a/helper.py", "def process():\n    return 2\n")
            repo.commit()
            graph = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD"))
            edges = [e for e in graph.edges if e.source == "package_a/consumer.py" and e.relationship == "import"]
            self.assertEqual({e.target for e in edges}, {"package_a/helper.py"})
            self.assertTrue(all(e.confidence == "high" for e in edges))

    def test_absolute_import_ambiguity_is_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("a/mod.py", "x = 1\n")
            repo.write("b/mod.py", "y = 2\n")
            repo.write("c/user.py", "import mod\n")
            repo.commit()
            graph = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD"))
            edges = [e for e in graph.edges if e.source == "c/user.py" and e.relationship == "import"]
            self.assertEqual({e.target for e in edges}, {"a/mod.py", "b/mod.py"})
            self.assertTrue(all(e.confidence == "low" for e in edges))

    def test_exact_absolute_import_prefers_package_qualified_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("pkg/mod.py", "x = 1\n")
            # a decoy that only matches by suffix
            repo.write("other/pkg/mod.py", "y = 2\n")
            repo.write("user.py", "import pkg.mod\n")
            repo.commit()
            graph = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD"))
            edges = [e for e in graph.edges if e.source == "user.py" and e.relationship == "import"]
            self.assertEqual({e.target for e in edges}, {"pkg/mod.py"})
            self.assertTrue(all(e.confidence == "high" for e in edges))

    def test_unique_suffix_only_import_is_low_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("app/consumer.py", "import config\n")
            # no config.py at the repo root: the only match is a suffix fallback
            repo.write("unused/config.py", "x = 1\n")
            repo.commit()
            graph = repo_graph.build(snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD"))
            edges = [e for e in graph.edges if e.source == "app/consumer.py" and e.relationship == "import"]
            self.assertEqual({e.target for e in edges}, {"unused/config.py"})
            # uniqueness alone is not confidence
            self.assertTrue(all(e.confidence == "low" for e in edges))
            # ...but it is still a positive consumer reference
            self.assertIn("unused/config.py", graph.consumer_targets())


@unittest.skipIf(GIT is None, "git not available")
class LifecycleTests(unittest.TestCase):
    def _resolve(self, repo_root, policy):
        snap = snapshot_mod.RepoSnapshot.from_ref(repo_root, "HEAD")
        graph = repo_graph.build(snap)
        return {row["path"]: row for row in repo_lifecycle.resolve(snap, graph, policy)}

    def test_retired_without_consumers_is_candidate_not_eligible(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("bench/old_experiment.py", "x = 1\n")
            repo.commit()
            policy = {"rules": [{"match": "bench/**", "lifecycle": "RETIRED_EXPERIMENT", "reason": "test"}]}
            row = self._resolve(repo.root, policy)["bench/old_experiment.py"]
            self.assertEqual(row["lifecycle"], "RETIRED_EXPERIMENT")
            self.assertFalse(row["dependency_conflict"])
            self.assertTrue(row["archive_candidate"])
            self.assertFalse(row["archive_eligible"])

    def test_retired_with_active_consumer_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("bench/old.py", "x = 1\n")
            repo.write("app/main.py", "from bench.old import x\n")
            repo.commit()
            policy = {"rules": [{"match": "bench/**", "lifecycle": "RETIRED_EXPERIMENT", "reason": "test"}]}
            row = self._resolve(repo.root, policy)["bench/old.py"]
            self.assertTrue(row["dependency_conflict"])
            self.assertTrue(row["review_required"])
            self.assertFalse(row["archive_candidate"])
            self.assertFalse(row["archive_eligible"])

    def test_generated_derived_with_active_consumer_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("gen/data.py", "x = 1\n")
            repo.write("app/use.py", "from gen.data import x\n")
            repo.commit()
            policy = {
                "rules": [{"match": "gen/**", "lifecycle": "GENERATED_DERIVED", "reason": "test"}],
                "approved_for_archive": ["gen/data.py"],
            }
            row = self._resolve(repo.root, policy)["gen/data.py"]
            self.assertTrue(row["dependency_conflict"])
            self.assertFalse(row["archive_candidate"])
            self.assertFalse(row["archive_eligible"])
            self.assertTrue(row["review_required"])

    def test_low_confidence_reference_still_blocks_archival(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("gen/data.py", "x = 1\n")
            repo.write("app/consumer.py", "import data\n")  # suffix-only -> low confidence
            repo.commit()
            policy = {
                "rules": [{"match": "gen/**", "lifecycle": "GENERATED_DERIVED", "reason": "test"}],
                "approved_for_archive": ["gen/data.py"],
            }
            row = self._resolve(repo.root, policy)["gen/data.py"]
            # confidence does not gate protection: even a low-confidence consumer ref blocks
            self.assertTrue(row["dependency_conflict"])
            self.assertFalse(row["archive_eligible"])

    def test_generated_derived_with_ci_reference_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("gen/data.py", "x = 1\n")
            repo.write(".github/workflows/w.yml", "run: gen/data.py\n")
            repo.commit()
            policy = {
                "rules": [{"match": "gen/**", "lifecycle": "GENERATED_DERIVED", "reason": "test"}],
                "approved_for_archive": ["gen/data.py"],
            }
            row = self._resolve(repo.root, policy)["gen/data.py"]
            self.assertTrue(row["dependency_conflict"])
            self.assertFalse(row["archive_eligible"])

    def test_archival_requires_explicit_approval_and_no_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("bench/old.py", "x = 1\n")
            repo.commit()
            policy = {
                "rules": [{"match": "bench/**", "lifecycle": "RETIRED_EXPERIMENT", "reason": "test"}],
                "approved_for_archive": ["bench/old.py"],
            }
            row = self._resolve(repo.root, policy)["bench/old.py"]
            self.assertTrue(row["archive_eligible"])

    def test_nested_analysis_docs_are_unknown_not_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("analysis/README.md", "# index\n")
            repo.write("analysis/legacy-writeup.md", "# writeup\n")
            repo.write("analysis/notes/foo.md", "# nested notes\n")
            repo.write("analysis/output/table.json", "{}\n")
            repo.commit()
            rows = self._resolve(repo.root, repo_lifecycle.load_policy())
            self.assertEqual(rows["analysis/README.md"]["lifecycle"], "ACTIVE")
            self.assertEqual(rows["analysis/legacy-writeup.md"]["lifecycle"], "RETIRED_EXPERIMENT")
            # nested docs in an unrecognized subtree are uncertain, not assumed generated
            self.assertEqual(rows["analysis/notes/foo.md"]["lifecycle"], "UNKNOWN")
            self.assertFalse(rows["analysis/notes/foo.md"]["archive_candidate"])
            # known generated families remain generated
            self.assertEqual(rows["analysis/output/table.json"]["lifecycle"], "GENERATED_DERIVED")
            self.assertTrue(rows["analysis/output/table.json"]["archive_candidate"])

    def test_evidence_qualification_from_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("results/run/raw_evidence.json", "{}\n")
            repo.write("results/run/report.json", "{}\n")
            repo.write("results/other/note.md", "# note\n")
            repo.write("app.py", "x = 1\n")
            repo.commit()
            policy = {
                "rules": [{"match": "results/**", "lifecycle": "FROZEN_VERIFIER", "reason": "test"}],
                "evidence_capsules": [
                    {
                        "descriptor": "results/run/raw_evidence.json",
                        "profile": "intrusive_complete_v1",
                        "verified_inventory": ["results/run/report.json"],
                    }
                ],
            }
            rows = self._resolve(repo.root, policy)
            self.assertEqual(rows["results/run/raw_evidence.json"]["evidence_qualification"], "seal_descriptor")
            self.assertEqual(rows["results/run/raw_evidence.json"]["verification_profile"], "intrusive_complete_v1")
            self.assertEqual(rows["results/run/report.json"]["evidence_qualification"], "verified_sealed_evidence")
            self.assertEqual(rows["results/run/report.json"]["verification_profile"], "intrusive_complete_v1")
            self.assertEqual(rows["results/other/note.md"]["evidence_qualification"], "preserved_historical_evidence")
            self.assertEqual(rows["results/other/note.md"]["verification_profile"], "")
            # non-evidence paths carry no qualification
            self.assertEqual(rows["app.py"]["evidence_qualification"], "")

    def test_policy_change_does_not_change_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("bench/old.py", "x = 1\n")
            repo.commit()
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            graph = repo_graph.build(snap)
            inv_a = repo_inventory.render_csv(repo_inventory.inventory_rows(snap))
            rows_a = repo_lifecycle.resolve(snap, graph, {"rules": [{"match": "bench/**", "lifecycle": "RETIRED_EXPERIMENT"}]})
            rows_b = repo_lifecycle.resolve(snap, graph, {"rules": [{"match": "bench/**", "lifecycle": "GENERATED_DERIVED"}]})
            self.assertEqual(inv_a, repo_inventory.render_csv(repo_inventory.inventory_rows(snap)))
            self.assertNotEqual(rows_a[0]["lifecycle"], rows_b[0]["lifecycle"])

    def test_invariants_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _Repo(tmp)
            repo.write("bench/old.py", "x = 1\n")
            repo.commit()
            policy = {"rules": [{"match": "bench/**", "lifecycle": "RETIRED_EXPERIMENT"}]}
            snap = snapshot_mod.RepoSnapshot.from_ref(repo.root, "HEAD")
            graph = repo_graph.build(snap)
            rows = repo_lifecycle.resolve(snap, graph, policy)
            stats = repo_lifecycle.invariants(rows, len(snap.paths()), policy.get("approved_for_archive", []))
            self.assertTrue(stats["all_classified"])
            self.assertEqual(stats["n_archive_eligible_but_unapproved"], 0)


class RealRepoAuditTests(unittest.TestCase):
    def test_kind_area_totals_reconcile(self):
        snap = snapshot_mod.RepoSnapshot.from_ref(ROOT, "HEAD")
        rows = repo_inventory.inventory_rows(snap)
        total = len(rows)
        self.assertEqual(sum(n for _, n in repo_inventory._counts(rows, "kind")), total)
        self.assertEqual(sum(n for _, n in repo_inventory._counts(rows, "area")), total)

    def test_every_path_classified_and_nothing_archive_eligible_by_default(self):
        snap = snapshot_mod.RepoSnapshot.from_ref(ROOT, "HEAD")
        graph = repo_graph.build(snap)
        policy = repo_lifecycle.load_policy()
        rows = repo_lifecycle.resolve(snap, graph, policy)
        stats = repo_lifecycle.invariants(
            rows, len(snap.paths()), policy.get("approved_for_archive", []), graph.consumer_targets()
        )
        self.assertTrue(stats["all_classified"])
        self.assertEqual(stats["n_archive_eligible"], 0)
        self.assertEqual(stats["n_archive_eligible_but_unapproved"], 0)
        self.assertEqual(stats["n_archive_eligible_with_consumer"], 0)
        self.assertTrue(stats["ok"])

    def test_phase_c_capsule_members_are_qualified(self):
        snap = snapshot_mod.RepoSnapshot.from_ref(ROOT, "HEAD")
        graph = repo_graph.build(snap)
        rows = {r["path"]: r for r in repo_lifecycle.resolve(snap, graph, repo_lifecycle.load_policy())}
        prefix = "results/20261003T210202Z-intrusive-diagnostic/"
        self.assertEqual(rows[prefix + "raw_evidence.json"]["evidence_qualification"], "seal_descriptor")
        self.assertEqual(rows[prefix + "raw_evidence.json"]["verification_profile"], "intrusive_complete_v1")
        self.assertEqual(rows[prefix + "manifest.json"]["evidence_qualification"], "verified_sealed_evidence")
        self.assertEqual(rows[prefix + "report.md"]["evidence_qualification"], "verified_sealed_evidence")
        # a results/ path outside the capsule is preserved but not sealed
        others = [
            r for r in rows.values()
            if r["path"].startswith("results/") and r["evidence_qualification"] == "preserved_historical_evidence"
        ]
        self.assertTrue(others, "expected unqualified preserved evidence under results/")

    def test_committed_artifacts_reproduce(self):
        context_path = ROOT / "docs" / "repo-audit-context.json"
        if not context_path.exists():
            self.skipTest("no committed audit artifacts")
        context = json.loads(context_path.read_text(encoding="utf-8"))
        ref = context["source"]["ref"]
        outputs, _stats = repo_audit.generate(ROOT, ref, False, repo_lifecycle.DEFAULT_POLICY_PATH)
        block = outputs.pop("__map_block__")
        for relpath, content in outputs.items():
            self.assertEqual((ROOT / relpath).read_text(encoding="utf-8"), content, relpath)
        map_text = (ROOT / "docs" / "repo-map.md").read_text(encoding="utf-8")
        start = map_text.index(repo_audit.BEGIN_MARKER) + len(repo_audit.BEGIN_MARKER)
        end = map_text.index(repo_audit.END_MARKER)
        self.assertEqual(map_text[start:end].strip(), block.strip())


if __name__ == "__main__":
    unittest.main()
