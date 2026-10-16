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
