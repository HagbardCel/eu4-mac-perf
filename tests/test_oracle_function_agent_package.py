"""G3a: package description, catalog boundary, held-out isolation and P3 immutability.

All checks are offline. The end-to-end isolation test uses the deterministic P3
stub (a synthetic agent), not a model.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from oracle.function_agent import binding, catalog, packages, seam
from oracle.function_agent.adapter import FunctionAgentAdapter
from oracle.slice import corpus
from oracle.slice.adapters import StubAdapter
from oracle.slice.opencode import OpenCodeAdapter

ROOT = Path(__file__).resolve().parents[1]
SLICE_DIR = ROOT / "oracle" / "slice"
MANIFEST = ROOT / "oracle" / "function_agent" / "frozen_slice_manifest.json"


class _FakeTools:
    """Records what an inner agent tried to call."""

    def __init__(self):
        self.seen: list[str] = []
        self.calls = 0

    def call(self, method, args=None):
        self.seen.append(method)
        self.calls += 1
        return {"tool": method}


class CatalogBoundaryTests(unittest.TestCase):
    def test_agent_catalog_is_exactly_the_five_tools(self):
        report = catalog.catalog_report()

        self.assertEqual(report["tools"], ["inspect", "fixtures", "coverage", "test", "diff"])
        self.assertEqual(report["count"], 5)
        self.assertFalse(report["certify_discoverable"])
        self.assertEqual(set(catalog.tool_names()),
                         {"inspect", "fixtures", "coverage", "test", "diff"})

    def test_certify_is_absent_from_definitions_and_schemas(self):
        blob = json.dumps(catalog.tool_definitions()).lower()
        self.assertNotIn("certify", blob)
        for name in catalog.CONTROLLER_ONLY_TOOL_NAMES:
            self.assertNotIn(name, catalog.tool_names())

    def test_catalog_wrapper_refuses_certify_before_the_bridge_sees_it(self):
        fake = _FakeTools()
        bound = catalog.CatalogBoundTools(fake, allowed=catalog.AGENT_VISIBLE_TOOLS)

        self.assertEqual(bound.call("inspect", {"function_id": "synth:apply"}),
                         {"tool": "inspect"})
        with self.assertRaises(catalog.CatalogError):
            bound.call("certify", {})
        self.assertEqual(fake.seen, ["inspect"])

    def test_a_widened_package_vocabulary_is_rejected(self):
        fake = _FakeTools()
        with self.assertRaises(catalog.CatalogError):
            catalog.CatalogBoundTools(fake, allowed=catalog.AGENT_VISIBLE_TOOLS + ("certify",))

        package = packages.describe_package("synth:apply")
        widened = replace(package, tool_names=package.tool_names + ("certify",))
        with self.assertRaises(catalog.CatalogError):
            FunctionAgentAdapter(widened, StubAdapter("repair"))

    def test_the_adapter_refuses_a_package_that_claims_arbitrary_cpp_support(self):
        package = packages.describe_package("synth:apply")
        with self.assertRaises(ValueError):
            FunctionAgentAdapter(replace(package, arbitrary_cpp_supported=True),
                                 StubAdapter("repair"))


class PackageDescriptorTests(unittest.TestCase):
    def test_synth_apply_descriptor_names_only_trusted_material(self):
        package = packages.describe_package("synth:apply")
        described = package.describe()

        self.assertEqual(described["function_id"], "synth:apply")
        self.assertEqual(described["candidate"], "target.c")
        self.assertEqual(described["header"], "synth.h")
        self.assertEqual(described["tool_names"],
                         ["inspect", "fixtures", "coverage", "test", "diff"])
        self.assertEqual(described["workspace_kind"], "synthetic-candidate-only")
        self.assertFalse(described["arbitrary_cpp_supported"])
        self.assertFalse(described["generic_workspace"])
        self.assertEqual(packages.supported_packages(), ("synth:apply",))
        # The descriptor carries no expectations and no controller secret.
        blob = json.dumps(described).lower()
        for forbidden in ("expected", "held_out", "controller.token", "post_memory"):
            self.assertNotIn(forbidden, blob)

    def test_unknown_package_is_refused(self):
        with self.assertRaises(packages.PackageRefused) as caught:
            packages.describe_package("re1:border_loop")
        self.assertEqual(caught.exception.status, "refused: no-package")

    def test_incomplete_package_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(packages.PackageRefused) as caught:
                packages.describe_package("synth:apply", fixtures_root=Path(tmp))
            self.assertEqual(caught.exception.status, "refused: incomplete-package")


class HeldOutIsolationTests(unittest.TestCase):
    """Held-out material is unreachable from the materialized agent workspace."""

    def test_materialized_workspace_contains_no_held_out_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = seam.run_function_package(
                "synth:apply", run_id="run-g3a", base_dir=Path(tmp) / "runs",
                inner=StubAdapter("repair"), downstream="synthetic-stub", synthetic=True,
                budget_calls=60, max_attempts=4, wall_seconds=60.0)
            self.assertEqual(result.status, "certified")

            workspace = result.run_dir / "workspace"
            report = catalog.assert_isolated(workspace)

            self.assertTrue(report["ok"])
            self.assertGreater(report["held_out_fingerprints"], 0)
            self.assertNotIn("held_out", report["files"])
            self.assertFalse(any("hid-" in name for name in report["files"]))
            self.assertNotIn("controller.token", report["files"])
            self.assertNotIn("budget.sqlite3", report["files"])
            self.assertFalse(any(name.endswith(".py") for name in report["files"]))
            self.assertEqual(result.isolation["ok"], True)

            # The controller token lives in the run directory, outside the workspace.
            self.assertTrue((result.run_dir / "controller.token").is_file())
            self.assertFalse((workspace / "controller.token").exists())

    def test_a_planted_held_out_fixture_is_detected(self):
        held = corpus.corpus_dir(corpus.HELD_OUT) / "hid-000.json"
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            shutil.copyfile(held, workspace / "borrowed.json")

            report = catalog.held_out_report(workspace)
            self.assertFalse(report["ok"])
            self.assertTrue(any("fingerprint" in problem for problem in report["problems"]))
            with self.assertRaises(catalog.HeldOutLeak):
                catalog.assert_isolated(workspace)

    def test_a_directory_symlink_into_held_out_is_rejected(self):
        held_root = corpus.corpus_dir(corpus.HELD_OUT)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "candidate.c").write_text("int main() { return 0; }\n",
                                                    encoding="utf-8")
            (workspace / "shortcuts").symlink_to(held_root, target_is_directory=True)

            report = catalog.held_out_report(workspace)
            self.assertFalse(report["ok"])
            self.assertTrue(any("symlinks are not allowed" in problem
                                for problem in report["problems"]))
            with self.assertRaises(catalog.HeldOutLeak):
                catalog.assert_isolated(workspace)

    def test_an_oversized_text_file_is_not_treated_as_clean(self):
        cap = catalog._MAX_SCAN_BYTES
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            (workspace / "large.txt").write_bytes(b"x" * (cap + 1))

            report = catalog.held_out_report(workspace)
            self.assertFalse(report["ok"])
            self.assertTrue(any("scan incomplete" in problem
                                for problem in report["problems"]))
            with self.assertRaises(catalog.HeldOutLeak):
                catalog.assert_isolated(workspace)

    def test_missing_workspace_is_not_treated_as_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"

            report = catalog.held_out_report(workspace)
            self.assertFalse(report["ok"])
            self.assertTrue(any("does not exist" in problem
                                for problem in report["problems"]))
            with self.assertRaises(catalog.HeldOutLeak):
                catalog.assert_isolated(workspace)

    def test_workspace_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            (real / "candidate.c").write_text("int main() { return 0; }\n", encoding="utf-8")
            workspace = Path(tmp) / "workspace"
            workspace.symlink_to(real, target_is_directory=True)

            report = catalog.held_out_report(workspace)
            self.assertFalse(report["ok"])
            self.assertTrue(any("must not be a symlink" in problem
                                for problem in report["problems"]))
            with self.assertRaises(catalog.HeldOutLeak):
                catalog.assert_isolated(workspace)


class FrozenSliceTests(unittest.TestCase):
    """G3a reuses certified P3 by import; it does not edit it."""

    def _current_slice(self) -> dict[str, str]:
        digests: dict[str, str] = {}
        for path in sorted(SLICE_DIR.rglob("*")):
            if not path.is_file() or path.suffix == ".pyc":
                continue
            relative = path.relative_to(ROOT).as_posix()
            if "__pycache__" in path.parts or relative.startswith("oracle/slice/runs/"):
                continue
            digests[relative] = sha256(path.read_bytes()).hexdigest()
        return digests

    def test_certified_p3_sources_match_the_recorded_manifest(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "eu4-oracle-frozen-slice-manifest/v1")
        self.assertEqual(manifest["source_commit"],
                         "8632d66b8c9c977ec6f03eac76767a4800eb7ebc")
        current = self._current_slice()

        self.assertEqual(sorted(current), sorted(manifest["files"]))
        mismatched = [name for name, digest in manifest["files"].items()
                      if current.get(name) != digest]
        self.assertEqual(mismatched, [], "certified P3 files changed")

        for required in ("oracle/slice/controller.py", "oracle/slice/bridge.py",
                         "oracle/slice/adapters.py", "oracle/slice/budget.py",
                         "oracle/slice/workspace.py", "oracle/slice/receipts.py",
                         "oracle/slice/opencode.py"):
            self.assertIn(required, manifest["files"])


class PinnedOpenCodeBindingTests(unittest.TestCase):
    """The seam builds the existing pinned binding; it never launches it."""

    def test_binding_reuses_the_pinned_template_prompt_and_model(self):
        package = packages.describe_package("synth:apply")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = binding.opencode_run(
                package, run_id="run-g3a", run_dir=root / "run",
                workspace=root / "workspace", repo_root=ROOT,
                python=Path(sys.executable), binary=root / "opencode")

            self.assertEqual(run.template_dir, SLICE_DIR / "opencode_config")
            self.assertEqual(run.prompt_path, SLICE_DIR / "opencode_config" / "prompt.txt")
            self.assertEqual(run.model, "opencode/deepseek-v4.1-flash-free#high")
            self.assertEqual(run.mode, "repair")

            adapter = binding.opencode_adapter(
                package, run_id="run-g3a", run_dir=root / "run",
                workspace=root / "workspace", repo_root=ROOT,
                python=Path(sys.executable), binary=root / "opencode")
            self.assertIsInstance(adapter, OpenCodeAdapter)
            self.assertEqual(adapter.name, "opencode")

            wrapped = FunctionAgentAdapter(package, adapter)
            self.assertEqual(wrapped.name, "function-agent:opencode")
            self.assertEqual(wrapped.tool_catalog()["count"], 5)

    def test_binding_descriptor_names_the_pin(self):
        described = binding.binding_descriptor()
        self.assertEqual(described["provider"], "opencode")
        self.assertEqual(described["model"], "deepseek-v4.1-flash-free")
        self.assertEqual(described["variant"], "high")
        self.assertIn("opencode.json.template", described["template"])


if __name__ == "__main__":
    unittest.main()
