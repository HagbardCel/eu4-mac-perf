"""The driver's execution-time pre-registration contract, exercised offline.

The driver is the only entry point that launches the pinned binary. These tests
exercise its contract against a *genuine* temporary Git repository - no model, no
network, no OpenCode - and prove that an invalid declaration is refused before the
adapter (and therefore the model) is ever constructed.

They cover every prospective defect the reviews found:

* a baseline that had to equal HEAD (which forced the declaration to stay
  uncommitted), and a declaration that merely had to be *readable* rather than
  committed;
* an acceptance run that could select an undeclared prompt;
* a clean commit C, *after* the declaration commit, that changes a held-out
  fixture or the OpenCode configuration while leaving the pinned Python sources
  untouched;
* explicit, declared or non-finite execution bounds; and
* a staging root, or a run directory, inside the repository - including an
  absolute/traversing/over-long run id and a pre-existing ``workspace`` symlink.
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

from oracle.slice import corpus, opencode_run

_GIT = ("git", "-c", "user.name=driver-test", "-c", "user.email=driver-test@example.invalid",
        "-c", "commit.gpgsign=false")

_TEMPLATE = """{
  "model": "@MODEL@",
  "python": "@PYTHON@",
  "repo": "@REPO_ROOT@",
  "run_dir": "@RUN_DIR@",
  "workspace": "@WORKSPACE@"
}
"""


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
            mock.patch.object(opencode_run, "SOURCE_PATHS", {"x_sha256": "src/x.py"}), \
            mock.patch.object(opencode_run, "CONFIG_DIR",
                              root.resolve() / "oracle" / "slice" / "opencode_config"):
        yield


def _fixture(set_name: str, fixture_id: str) -> str:
    return json.dumps({
        "schema_version": "eu4-oracle-fixture/v1",
        "set": set_name,
        "fixture_id": fixture_id,
        "function_id": "synth:apply",
        "input": {"mode": 0, "arg": 0, "dirty": 0, "value": 0, "counter": 0},
        "expected": {"capture": {"complete": True, "lost_events": 0,
                                 "unknown_dependencies": []}},
    }, indent=2) + "\n"


def _setup(root: Path) -> dict:
    """A genuine repository: pinned source A, then the committed declaration B."""
    staging = (root.parent / (root.name + "-staging")).resolve()
    staging.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(parents=True)
    (root / "src" / "x.py").write_text("PINNED = 1\n", encoding="utf-8")
    (root / "cand.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (root / "cand2.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    (root / "prompt.txt").write_text("repair the candidate\n", encoding="utf-8")
    (root / "cand_link.c").symlink_to("cand.c")

    config_dir = root / "oracle" / "slice" / "opencode_config"
    config_dir.mkdir(parents=True)
    (config_dir / "opencode.json.template").write_text(_TEMPLATE, encoding="utf-8")
    for set_name, fixture_id in ((corpus.VISIBLE, "vis-000"), (corpus.HELD_OUT, "hid-000")):
        directory = root / "oracle" / "slice" / "fixtures" / set_name
        directory.mkdir(parents=True)
        (directory / f"{fixture_id}.json").write_text(_fixture(set_name, fixture_id),
                                                      encoding="utf-8")

    _git(root, "init", "--quiet")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "pin the sources")
    baseline = _git(root, "rev-parse", "HEAD")

    with _patched(root):
        config, substitutions = opencode_run._config_identity(
            run_id="run-driver", staging_root=staging, python=Path(sys.executable),
            provider="opencode", model_id="m", variant=None)
        corpus_identity = opencode_run._corpus_identity()

    declared = {
        "schema": opencode_run.PREREGISTRATION_SCHEMA,
        "run_id": "run-driver",
        "mode": "acceptance",
        "staging_root": str(staging),
        "python": str(Path(sys.executable)),
        "model": {"provider": "opencode", "id": "m", "variant": None},
        "prompt_path": "prompt.txt",
        "prompt_sha256": _sha(root / "prompt.txt"),
        "binary": {"path": "/bin/true", "sha256": "a" * 64, "version": "stub"},
        "config": config,
        "substitutions": substitutions,
        "corpus": corpus_identity,
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
            "baseline": baseline, "prompt_path": root / "prompt.txt", "staging": staging}


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
        pinned sources are unchanged, the corpus and configuration still match, and
        the run's omitted optional flags resolve to the declared defaults.
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

    def test_a_held_out_fixture_changed_in_a_later_clean_commit_is_refused(self):
        """The precise scenario the ancestor policy would otherwise admit.

        A is an ancestor, the tree is clean and every pinned Python source matches -
        yet a commit *after* the declaration swapped a held-out fixture, so the run
        would spend the holdout against a corpus nobody declared.
        """
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            fixture = data["root"] / "oracle" / "slice" / "fixtures" / "held_out" / "hid-000.json"
            fixture.write_text(_fixture(corpus.HELD_OUT, "hid-000").replace(
                '"complete": true', '"complete": false'), encoding="utf-8")
            _git(data["root"], "commit", "--quiet", "-am", "swap a held-out fixture")
            self.assertEqual(_git(data["root"], "status", "--porcelain"), "")
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertTrue(
                any("held_out corpus differs" in problem for problem in problems), problems)

    def test_a_configuration_template_changed_in_a_later_clean_commit_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            template = (data["root"] / "oracle" / "slice" / "opencode_config"
                        / "opencode.json.template")
            template.write_text(_TEMPLATE.replace('"model"', '"agent_model"'), encoding="utf-8")
            _git(data["root"], "commit", "--quiet", "-am", "edit the permissions template")
            self.assertEqual(_git(data["root"], "status", "--porcelain"), "")
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    data["declared"], _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertTrue(
                any("configuration template_sha256 differs" in problem for problem in problems),
                problems)

    def test_a_declared_zero_budget_is_refused_as_invalid(self):
        """Rejected because zero is invalid - not because it differs from a default."""
        with tempfile.TemporaryDirectory() as tmp:
            data = _setup(Path(tmp))
            declared = json.loads(json.dumps(data["declared"]))
            declared["run"]["budget_calls"] = 0
            with _patched(data["root"]):
                problems = opencode_run._contract_problems(
                    declared, _args(data["root"]), prereg_path=data["prereg_path"])
            self.assertIn("declared run.budget_calls=0 must be a positive integer", problems)
            self.assertFalse(
                any("run.budget_calls" in problem and "differs" in problem for problem in problems),
                problems)


class LimitTests(unittest.TestCase):
    def _problems(self, **limits):
        return opencode_run._limit_problems(limits, label="effective")

    def test_positive_bounds_are_valid(self):
        self.assertEqual(self._problems(budget_calls=1, wall_seconds=0.5,
                                        finalization_seconds=0.5), [])

    def test_zero_and_negative_bounds_are_refused(self):
        for bad in (0, -1, -0.0):
            self.assertTrue(self._problems(budget_calls=bad, wall_seconds=1.0,
                                           finalization_seconds=1.0))
        for bad in (0.0, -0.5):
            self.assertTrue(self._problems(budget_calls=1, wall_seconds=bad,
                                           finalization_seconds=1.0))

    def test_non_finite_and_boolean_bounds_are_refused(self):
        self.assertTrue(self._problems(budget_calls=True, wall_seconds=1.0,
                                       finalization_seconds=1.0))
        self.assertTrue(self._problems(budget_calls=1, wall_seconds=float("nan"),
                                       finalization_seconds=1.0))
        self.assertTrue(self._problems(budget_calls=1, wall_seconds=float("inf"),
                                       finalization_seconds=1.0))
        self.assertTrue(self._problems(budget_calls=1, wall_seconds=1.0,
                                       finalization_seconds=float("nan")))


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
        outside = str(Path(tempfile.gettempdir()) / "p3h4-driver-staging")
        with mock.patch.object(opencode_run.OpenCodeAdapter, "__init__",
                               side_effect=AssertionError("adapter was built")) as built:
            with self.assertRaises(SystemExit) as ctx:
                opencode_run.main(["--mode", "acceptance", "--prompt", "pilot",
                                   "--run-id", "r", "--staging-root", outside])
        self.assertIn("repair prompt", str(ctx.exception))
        built.assert_not_called()


class StagingRootTests(unittest.TestCase):
    def test_a_staging_root_inside_the_repository_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched(root):
                self.assertTrue(opencode_run._staging_problems(root / "staging"))
                self.assertTrue(opencode_run._staging_problems(root))
                self.assertTrue(opencode_run._staging_problems(root.parent))

    def test_a_sibling_staging_root_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _setup(root)
            with _patched(root):
                self.assertEqual(opencode_run._staging_problems(data["staging"]), [])

    def test_main_refuses_an_in_repo_staging_root_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _patched(root):
                with mock.patch.object(opencode_run.OpenCodeAdapter, "__init__",
                                       side_effect=AssertionError("adapter was built")):
                    with self.assertRaises(SystemExit) as ctx:
                        opencode_run.main(["--mode", "acceptance", "--run-id", "r",
                                           "--staging-root", str(root / "staging")])
            self.assertIn("must lie outside the repository", str(ctx.exception))


class RunIdentityTests(unittest.TestCase):
    """The run id names one path component under the staging root.

    Validating the staging root alone is not enough: ``/`` discards it, ``..``
    escapes it, and a pre-existing symlink - the run directory itself or a child
    such as ``workspace`` - routes the agent's project directory back into the
    repository. Each case must be refused before the adapter is ever constructed.
    """

    def test_valid_run_ids_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            for run_id in ("run-driver", "p3b-acceptance-20261017", "a.b_c-1"):
                self.assertEqual(opencode_run._run_dir_problems(staging, run_id), [])
            self.assertEqual(opencode_run._run_dir_problems(
                staging, opencode_run._default_run_id("acceptance")), [])

    def test_malformed_run_ids_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            staging.mkdir()
            for run_id in ("", " ", ".", "..", "a/b", ".hidden", "/tmp/evil", "../escape",
                           "a" * 97):
                self.assertTrue(opencode_run._run_dir_problems(staging, run_id), run_id)

    def test_main_refuses_a_traversal_run_id_before_the_adapter_is_built(self):
        outside = str(Path(tempfile.gettempdir()) / "p3h5-driver-staging")
        with mock.patch.object(opencode_run.OpenCodeAdapter, "__init__",
                               side_effect=AssertionError("adapter was built")) as built:
            with self.assertRaises(SystemExit) as ctx:
                opencode_run.main(["--mode", "acceptance", "--run-id", "../escape",
                                   "--staging-root", outside])
        self.assertIn("single path component", str(ctx.exception))
        built.assert_not_called()

    def test_an_overlong_run_id_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp) / "staging"
            staging.mkdir()
            problems = opencode_run._run_dir_problems(staging, "r" * 97)
        self.assertTrue(any("longer than 96" in problem for problem in problems), problems)

    def test_a_symlinked_run_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            staging.mkdir()
            (root / "target").mkdir()
            (staging / "run-x").symlink_to(root / "target")
            problems = opencode_run._run_dir_problems(staging, "run-x")
        self.assertTrue(any("must not be a symlink" in problem for problem in problems), problems)

    def test_a_workspace_symlink_is_refused_before_materialization(self):
        """A real run directory whose ``workspace`` escapes into the repository.

        ``materialize_workspace`` would follow the link, write ``candidate.c`` into
        the repository, and hand the agent a project directory inside it - the class
        of isolation failure the pilot exposed. It must be refused first.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = _setup(root)
            staging = data["staging"]
            target = root / "empty-target"
            target.mkdir()
            run_dir = staging / "run-x"
            run_dir.mkdir()
            (run_dir / "workspace").symlink_to(target)

            with _patched(root):
                self.assertTrue(opencode_run._run_dir_problems(staging, "run-x"))
                with mock.patch.object(opencode_run.OpenCodeAdapter, "__init__",
                                       side_effect=AssertionError("adapter was built")) as built:
                    with self.assertRaises(SystemExit) as ctx:
                        opencode_run.main(["--mode", "acceptance", "--run-id", "run-x",
                                           "--staging-root", str(staging)])

            self.assertIn("must not be a symlink", str(ctx.exception))
            built.assert_not_called()
            self.assertFalse((target / "candidate.c").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
