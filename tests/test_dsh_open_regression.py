"""The DSH path must not depend on, or disturb, the OpenCode path.

The plan requires the infrastructure PR to be reviewed for accidental OpenCode
dependency, duplicated validation logic and unnecessary privilege. These are the
mechanical parts of that review, encoded so they cannot regress silently.

Reusing `automation.runner.run_accept` is deliberate: the verdict vocabulary and
timeout handling have one source of truth. Reusing the OpenCode *adapter* or its
campaign loop is not.
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DSH_MODULES = sorted((REPO / "automation").glob("dsh_*.py"))
PLUGIN_SOURCES = sorted((REPO / "dsh" / "eu4-goal-plugin").glob("lib/*.js"))
FORBIDDEN_IMPORTS = {"automation.opencode", "automation.cli"}


def imports_of(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))]


class OpenCodeIndependenceTests(unittest.TestCase):
    def test_the_dsh_modules_exist(self) -> None:
        names = {path.name for path in DSH_MODULES}
        self.assertTrue({"dsh_bridge.py", "dsh_campaign.py", "dsh_receipts.py"} <= names,
                        f"DSH modules missing: {names}")

    def test_no_dsh_module_imports_the_opencode_adapter_or_cli(self) -> None:
        for path in DSH_MODULES:
            for node in imports_of(path):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module, FORBIDDEN_IMPORTS, path.name)
                    for alias in node.names:
                        combined = f"{node.module}.{alias.name}"
                        self.assertNotIn(combined, FORBIDDEN_IMPORTS, path.name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, FORBIDDEN_IMPORTS, path.name)

    def test_the_only_runner_dependency_is_the_verdict_helper(self) -> None:
        """The bridge reuses run_accept; it must not reach for the Runner loop."""
        found = []
        for path in DSH_MODULES:
            for node in imports_of(path):
                if isinstance(node, ast.ImportFrom) and node.module == "automation.runner":
                    found.append((path.name, sorted(a.name for a in node.names)))
        self.assertTrue(found, "the bridge is expected to reuse run_accept")
        for name, aliases in found:
            self.assertEqual(aliases, ["run_accept"],
                             f"{name} imports more of the OpenCode runner than run_accept")

    def test_no_dsh_module_spawns_a_shell(self) -> None:
        """Every subprocess is argv-based: no shell interpolation of model text."""
        for path in DSH_MODULES:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("shell=True", source, path.name)
            self.assertNotIn("os.system(", source, path.name)
            self.assertNotIn("os.popen(", source, path.name)

    def test_no_dsh_module_references_opencode_state(self) -> None:
        """.local/automation belongs to the OpenCode runner; DSH must not write it."""
        for path in DSH_MODULES:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ".local/automation" in line:
                    self.assertIn(
                        "must not live under", line,
                        f"{path.name}:{number} touches OpenCode state: {line.strip()}",
                    )

    def test_the_plugin_has_no_opencode_reference(self) -> None:
        for path in PLUGIN_SOURCES:
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("opencode", source.lower(), path.name)


class ExistingPolicyPreservedTests(unittest.TestCase):
    def test_load_campaign_still_rejects_a_non_opencode_runtime(self) -> None:
        """The historical loader keeps its gate; DSH has its own reader."""
        from automation.state import load_campaign

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "objective.md").write_text("objective\n", encoding="utf-8")
            (root / "accept.py").write_text("print('{}')\n", encoding="utf-8")
            (root / "workspace").mkdir()
            (root / "campaign.json").write_text(json.dumps({
                "id": "probe",
                "objective": "objective.md",
                "runtime": "dsh",
                "model": "provider/model",
                "opencode_version": "2.2.5",
                "workspace": "workspace",
                "accept": ["python3", "accept.py"],
                "max_rounds": 4,
                "max_seconds": 60,
                "accept_timeout_seconds": 10,
            }), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                load_campaign(root / "campaign.json")
        self.assertIn("opencode", str(caught.exception))

    def test_the_dsh_reader_accepts_the_same_directory(self) -> None:
        """The DSH reader reads the shared contract without the runtime gate."""
        sidecar = json.loads(
            (REPO / "dsh" / "campaigns" / "offline-smoke.json").read_text(encoding="utf-8")
        )
        campaign = json.loads(
            (REPO / "campaigns" / "automation-smoke" / "campaign.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sidecar["source_campaign"],
                         "campaigns/automation-smoke/campaign.json")
        self.assertEqual(campaign["runtime"], "opencode")


if __name__ == "__main__":
    unittest.main()
