"""P3h: the driver's execution-time pre-registration contract, offline.

The driver is the only entry point that launches the pinned binary. These tests
exercise its contract against a *genuine* temporary Git repository - no model, no
network, no OpenCode - and prove that an invalid declaration is refused before the
adapter (and therefore the model) is ever constructed.

They cover the three prospective defects the P3h review found: a baseline that had
to equal HEAD (which forced the declaration to stay uncommitted), a declaration
that merely had to be *readable* rather than committed, and an acceptance run that
could select an undeclared prompt.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oracle.slice import opencode_run

_GIT = ("git", "-c", "user.name=driver-test", "-c", "user.email=driver-test@example.invalid",
        "-c", "commit.gpgsign=false")


def _git(root: Path, *arguments: str) -> str:
    probe = subprocess.run([*_GIT, "-C", str(root), *arguments],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {probe.stderr}")
    return probe.stdout.strip()


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def _patched(root: Path):
    """Point the driver's repository globals at the temporary root."""
    with mock.patch.object(opencode_run, "REPO_ROOT", root.resolve()), \
            mock.patch.object(opencode_run, "SOURCE_PATHS", {"x_sha256": "src/x.py"}):
        yield


def _setup(root: Path) -> dict:
    """A genuine repository: pinned source A, then the committed declaration B."""
    (root / "src").mkdir(parents=True)
    (root / "src" / "x.py").write_text("PINNED = 1\n", encoding="utf-8")
    (root / "cand.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (root / "cand2.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (root / "prompt.txt").write_text("repair the candidate\n", encoding="utf-8")
    (root / "cand_link.c").symlink_to("cand.c")
    _git(root, "init", "--quiet")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "pin the sources")
    baseline = _git(root, "rev-parse", "HEAD")

    declared = {
        "schema": opencode_run.PREREGISTRATION_SCHEMA,
        "run_id": "run-driver",
        "mode": "acceptance",
        "staging_root": str(root / "staging"),
        "python": str(Path(sys.executable)),
        "model": {"provider": "opencode", "id": "m", "variant": None},
        "prompt_path": "prompt.txt",
        "prompt_sha256": _sha(root / "prompt.txt"),
        "binary": {"path": "/bin/true", "sha256": "a" * 64, "version": "stub"},
        "source": {
            "candidate_path": "cand.c",
            "defective_candidate_sha256": _sha(root / "cand.c"),
            "baseline_commit": baseline,
        },
        "source_hashes": {"x_sha256": _sha(root / "src" / "x.py")},
        "run": {"mode": "acceptance", "attempts": 1, "budget_calls": 40,
                "wall_seconds": 3600.0, "finalization_seconds": 300.0},
    }
    prereg_path = root / "docs" / "prereg.json"
    prereg_path.parent.mkdir(parents=True)
    prereg_path.write_text(json.dumps(declared, indent=2), encoding="utf-8")
    _git(root, "add", "docs/prereg.json")
    _git(root, "commit", "--quiet", "-m", "publish the pre-registration")
    return {"root": root, "declared": declared, "prereg_path": prereg_path,
            "baseline": baseline, "prompt_path": root / "prompt.txt"}


def _args(root: Path, **overrides) -> argparse.Namespace:
    params = dict(candidate=str(root / "cand.c"), python=str(sys.executable),
                  mode="acceptance", budget_calls=None, wall_seconds=None,
                  finalization_seconds=300.0)
    params.update(overrides)
    return argparse.Namespace(**params)


def _unrelated_commit(root: Path) -> str:
    """A root commit that is not an ancestor of the pinned source revision."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _git(root, "checkout", "--quiet", "--orphan", "unrelated")
    _git(root, "commit", "--quiet", "-m", "an unrelated root")
    sha = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "--quiet", branch)
    return sha


class ContractTests(unittest.TestCase):
    def test_a_committed_declaration_with_default_bounds_is_accepted(self):
        """The whole point: commit-and-push, then run from a clean checkout.

        A is an ancestor of HEAD (the commit that published the declaration), the
        pinned sources are unchanged, and the run's omitted optional flags resolve
        to the declared defaults.
        """
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertEqual(problems, [])

    def test_an_edited_declaration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            path = data["prereg_path"]
            path.write_text(path.read_text() + "\n", encoding="utf-8")
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=path)
            self.assertTrue(any("committed blob" in problem for problem in problems), problems)

    def test_an_uncommitted_declaration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            uncommitted = data["root"] / "docs" / "draft.json"
            uncommitted.write_text(json.dumps(data["declared"], indent=2), encoding="utf-8")
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=uncommitted)
            self.assertTrue(
                any("is not committed at HEAD" in problem for problem in problems), problems)

    def test_a_baseline_that_is_not_an_ancestor_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            declared = json.loads(json.dumps(data["declared"]))
            declared["source"]["baseline_commit"] = _unrelated_commit(data["root"])
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    declared, _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertTrue(any("not an ancestor" in problem for problem in problems), problems)

    def test_an_undeclared_candidate_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"], candidate=str(data["root"] / "cand2.c")),
                    prereg_path=data["prereg_path"])
            self.assertTrue(any("candidate source path" in problem for problem in problems), problems)

    def test_a_symlinked_candidate_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            link = data["root"] / "cand_link.c"
            self.assertTrue(link.is_symlink())
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"], candidate=str(link)),
                    prereg_path=data["prereg_path"])
            self.assertTrue(any("symlink" in problem for problem in problems), problems)

    def test_a_changed_pinned_source_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            (data["root"] / "src" / "x.py").write_text("TAMPERED\n", encoding="utf-8")
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertTrue(any("working tree is not clean" in problem for problem in problems),
                            problems)
            self.assertTrue(any("differs from the declaration" in problem for problem in problems),
                            problems)


class PromptIdentityTests(unittest.TestCase):
    def _enforce(self, data, *, prompt_path, prereg_path=None):
        return opencode_run._enforce_preregistration(
            prereg_path or data["prereg_path"], run_id="run-driver",
            staging_root=Path(data["declared"]["staging_root"]),
            model=data["declared"]["model"], args=_args(data["root"]), strict=True,
            prompt_path=prompt_path)

    def test_a_declared_prompt_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            with _patched(data["root"]):
                declared = self._enforce(data, prompt_path=data["prompt_path"])
            self.assertEqual(declared["run_id"], "run-driver")

    def test_an_undeclared_prompt_path_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            wrong = data["root"] / "docs" / "other_prompt.txt"
            wrong.write_text("something else\n", encoding="utf-8")
            with _patched(data["root"]):
                with self.assertRaises(SystemExit) as ctx:
                    self._enforce(data, prompt_path=wrong)
            self.assertIn("is not the declared", str(ctx.exception))

    def test_a_prompt_whose_content_is_not_declared_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            tampered = json.loads(json.dumps(data["declared"]))
            tampered["prompt_sha256"] = "0" * 64
            path = data["root"] / "docs" / "tampered.json"
            path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
            with _patched(data["root"]):
                with self.assertRaises(SystemExit) as ctx:
                    self._enforce(data, prompt_path=data["prompt_path"], prereg_path=path)
            self.assertIn("prompt content differs", str(ctx.exception))

    def test_acceptance_refuses_the_pilot_prompt_before_the_adapter_is_built(self):
        """The one-shot acceptance workflow has no prompt flexibility."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(opencode_run.OpenCodeAdapter, "__init__",
                                   side_effect=AssertionError("adapter was built")) as built:
                with self.assertRaises(SystemExit) as ctx:
                    opencode_run.main(["--mode", "acceptance", "--prompt", "pilot",
                                       "--run-id", "r", "--staging-root", tmp])
            self.assertIn("repair prompt", str(ctx.exception))
            built.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
