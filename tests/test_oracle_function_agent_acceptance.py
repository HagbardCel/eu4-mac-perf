"""Independent G3a acceptance (black-box). Implementation agents must not edit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oracle.function_agent import catalog, seam
from oracle.slice import controller, receipts
from oracle.slice.adapters import StubAdapter

FORBIDDEN_WORKSPACE_NAMES = frozenset(catalog.CONTROLLER_ONLY_FILES)
FORBIDDEN_PATH_MARKERS = ("held_out", "hid-", "controller.token")


def _workspace_relative_files(workspace: Path) -> list[str]:
    if not workspace.is_dir():
        return []
    return sorted(
        p.relative_to(workspace).as_posix()
        for p in workspace.rglob("*")
        if p.is_file())


def assert_workspace_independent_isolation(workspace: Path) -> None:
    """Direct artifact inspection — must not rely on catalog helpers alone."""
    for rel in _workspace_relative_files(workspace):
        name = Path(rel).name
        if name in FORBIDDEN_WORKSPACE_NAMES:
            raise AssertionError(f"controller-only file in workspace: {rel}")
        lowered = rel.lower()
        for marker in FORBIDDEN_PATH_MARKERS:
            if marker in lowered:
                raise AssertionError(f"forbidden marker {marker!r} in workspace path: {rel}")


def _run_package(root: Path, inner, **overrides) -> seam.SeamResult:
    params = dict(
        function_id="synth:apply",
        run_id="g3a-accept",
        base_dir=root / "runs",
        inner=inner,
        downstream="acceptance-stub",
        synthetic=True,
        budget_calls=60,
        max_attempts=4,
        wall_seconds=60.0,
    )
    params.update(overrides)
    return seam.run_function_package(**params)


class G3aAcceptanceTests(unittest.TestCase):
    def test_catalog_is_exactly_five_tools(self):
        catalog.validate_catalog(catalog.tool_definitions())
        names = catalog.tool_names()
        self.assertEqual(names, catalog.AGENT_VISIBLE_TOOLS)
        self.assertNotIn("certify", names)

    def test_synth_apply_end_to_end_sealed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _run_package(root, StubAdapter("repair"))
            self.assertEqual(result.status, controller.STATUS_CERTIFIED)
            self.assertIsNotNone(result.receipt)
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            workspace = result.run_dir / "workspace"
            assert_workspace_independent_isolation(workspace)
            catalog.assert_isolated(workspace, fixtures_root=result.package.fixtures_root)

    def test_certify_tool_request_is_refused(self):
        fake_tools = type("T", (), {"call": lambda self, m, a=None: {"tool": m}})()
        bound = catalog.CatalogBoundTools(
            fake_tools, allowed=catalog.AGENT_VISIBLE_TOOLS)
        with self.assertRaises(catalog.CatalogError):
            bound.call("certify", {})

    def test_claim_denies_arbitrary_cpp(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_package(Path(tmp), StubAdapter("repair"))
            self.assertFalse(result.claim["arbitrary_cpp_supported"])
            self.assertFalse(result.claim["generic_workspace"])

    def test_missing_package_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = seam.run_function_package(
                "synth:not-installed",
                run_id="g3a-accept",
                base_dir=root / "runs",
                inner=StubAdapter("repair"),
                downstream="acceptance-stub",
                synthetic=True,
            )
            self.assertEqual(result.status, "refused: no-package")
            self.assertIsNone(result.run)
            self.assertIsNone(result.receipt)

    def test_budget_abort_via_seam_params(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run_package(Path(tmp), StubAdapter("repair"), budget_calls=3)
            self.assertEqual(result.status, controller.STATUS_ABORTED)
            self.assertEqual(result.abort_reason, "budget")
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])


class DefectiveSeamStandIn:
    """Schema-valid success without a sealed receipt."""

    def run(self, **_kwargs):
        return seam.SeamResult(
            status=controller.STATUS_CERTIFIED,
            claim={
                "function_id": "synth:apply",
                "synthetic": True,
                "arbitrary_cpp_supported": False,
                "generic_workspace": False,
            },
            run=None,
        )


class G3aNegativeControlTests(unittest.TestCase):
    def test_defective_stand_in_fails_receipt_requirement(self):
        result = DefectiveSeamStandIn().run()
        self.assertIsNone(result.receipt)
        with self.assertRaises(receipts.ReceiptError):
            receipts.verify(result.receipt)


if __name__ == "__main__":
    unittest.main()
