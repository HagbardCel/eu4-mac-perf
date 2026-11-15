"""The DSH runtime pin, provisioner, preflight and overlay must agree.

Four tracked files describe the same profile:

- ``dsh/runtime.json`` names every required plugin and pins package identity;
- ``dsh/scripts/provision.sh`` links the two local plugins into the profile;
- ``dsh/scripts/preflight.sh`` checks the composed profile and the pins;
- ``dsh/profile/eu4-web/cordis.patch.yml`` is the authoritative registration.

These checks are deliberately offline. They make drift fail here instead of in
a live preflight or a session where a tool is silently missing.
"""

import hashlib
import json
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "dsh" / "runtime.json"
PREFLIGHT = REPO / "dsh" / "scripts" / "preflight.sh"
PROVISION = REPO / "dsh" / "scripts" / "provision.sh"
OVERLAY = REPO / "dsh" / "profile" / "eu4-web" / "cordis.patch.yml"
WORKFLOW = REPO / ".github" / "workflows" / "profiler.yml"


def _runtime() -> dict:
    return json.loads(RUNTIME.read_text(encoding="utf-8"))


class RuntimePinTests(unittest.TestCase):
    def test_both_local_plugins_are_required(self) -> None:
        required = _runtime()["required_plugins"]
        self.assertEqual(required["eu4-goal"]["name"], "@eu4/dsh-goal")
        self.assertEqual(required["eu4-tools"]["name"], "@eu4/dsh-tools")

    def test_every_local_package_pin_matches_the_tracked_manifest(self) -> None:
        for plugin_id, spec in _runtime()["required_plugins"].items():
            relative = spec.get("package_json")
            if relative is None:
                continue
            path = REPO / relative
            self.assertTrue(path.is_file(), f"{plugin_id}: {path} is missing")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, spec.get("package_json_sha256"),
                             f"{plugin_id}: package.json no longer matches runtime.json")

    def test_preflight_reads_the_required_ids_from_runtime(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn('required = list(runtime["required_plugins"])', text)
        self.assertIn('spec.get("package_json")', text)
        self.assertIn("package_json_sha256", text)
        self.assertIn('runtime["dsh"].get("package_json_sha256")', text)
        self.assertIn("dsh_package", text)

    def test_preflight_resolves_both_local_plugins(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        for package in ("@eu4/dsh-goal", "@eu4/dsh-tools"):
            self.assertIn(f'"{package}"', text)


class ProvisionAndRegistrationTests(unittest.TestCase):
    def test_provision_links_the_same_two_local_plugins(self) -> None:
        text = PROVISION.read_text(encoding="utf-8")
        self.assertIn("PLUGIN=$INFRA/dsh/eu4-goal-plugin", text)
        self.assertIn("TOOLS_PLUGIN=$INFRA/dsh/eu4-tools-plugin", text)
        self.assertIn('add "link:$PLUGIN"', text)
        self.assertIn('add "link:$TOOLS_PLUGIN"', text)

    def test_overlay_registers_both_local_plugins(self) -> None:
        text = OVERLAY.read_text(encoding="utf-8")
        for plugin_id, package in (("eu4-goal", "@eu4/dsh-goal"),
                                   ("eu4-tools", "@eu4/dsh-tools")):
            self.assertIn(f"- id: {plugin_id}", text)
            self.assertIn(f"name: '{package}'", text)

    def test_ci_runs_both_dsh_plugin_suites(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm test --prefix dsh/eu4-goal-plugin", text)
        self.assertIn("npm test --prefix dsh/eu4-tools-plugin", text)


if __name__ == "__main__":
    unittest.main()
