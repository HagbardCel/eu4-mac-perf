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
import os
import tempfile
import unittest
from pathlib import Path

from automation import dsh_profile

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "dsh" / "runtime.json"
PREFLIGHT = REPO / "dsh" / "scripts" / "preflight.sh"
PROVISION = REPO / "dsh" / "scripts" / "provision.sh"
RUN = REPO / "dsh" / "scripts" / "run.sh"
RUN_CAMPAIGN = REPO / "dsh" / "scripts" / "run-campaign.sh"
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
        self.assertIn("dsh_profile.verify_local_plugin", text)
        self.assertIn("profile_dir", text)

    def test_preflight_resolves_both_local_plugins(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        for package in ("@eu4/dsh-goal", "@eu4/dsh-tools"):
            self.assertIn(f'"{package}"', text)


def _write_plugin(checkout: Path, package: str) -> Path:
    root = checkout / "dsh" / ("eu4-goal-plugin" if package == "@eu4/dsh-goal"
                               else "eu4-tools-plugin")
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text(
        json.dumps({"name": package, "version": "0.0.0"}), encoding="utf-8")
    return root


class InstalledProfileLinkTests(unittest.TestCase):
    """Preflight must validate the package resolved from the profile.

    The tracked source package can be correct while the profile links a
    different checkout. A temporary DSH home proves that case is refused and
    that the intended checkout is accepted.
    """

    def _spec(self, package: str, expected: Path, infra: Path) -> dict:
        manifest = expected / "package.json"
        return {
            "name": package,
            "package_json": str(manifest.relative_to(infra)),
            "package_json_sha256": hashlib.sha256(
                manifest.read_bytes()).hexdigest(),
        }

    def _profile_link(self, home: Path, package: str, target: Path) -> None:
        link = home / "profiles" / "eu4-web" / "node_modules" / package
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, link, target_is_directory=True)

    def test_a_link_to_another_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "dsh-home"
            infra = root / "intended-checkout"
            other = root / "other-checkout"
            expected = _write_plugin(infra, "@eu4/dsh-goal")
            actual = _write_plugin(other, "@eu4/dsh-goal")
            # Identical manifest bytes: only the canonical destination differs.
            (actual / "package.json").write_bytes(
                (expected / "package.json").read_bytes())
            self._profile_link(home, "@eu4/dsh-goal", actual)

            problems = dsh_profile.verify_local_plugin(
                "eu4-goal", self._spec("@eu4/dsh-goal", expected, infra),
                home / "profiles" / "eu4-web", infra)

            self.assertTrue(problems, "a foreign checkout must not pass")
            self.assertTrue(any("but this checkout provides" in item
                                for item in problems), problems)

    def test_the_intended_checkout_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "dsh-home"
            infra = root / "intended-checkout"
            expected = _write_plugin(infra, "@eu4/dsh-tools")
            self._profile_link(home, "@eu4/dsh-tools", expected)

            problems = dsh_profile.verify_local_plugin(
                "eu4-tools", self._spec("@eu4/dsh-tools", expected, infra),
                home / "profiles" / "eu4-web", infra)

            self.assertEqual(problems, [])

    def test_a_missing_install_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            infra = root / "intended-checkout"
            expected = _write_plugin(infra, "@eu4/dsh-goal")
            problems = dsh_profile.verify_local_plugin(
                "eu4-goal", self._spec("@eu4/dsh-goal", expected, infra),
                root / "dsh-home" / "profiles" / "eu4-web", infra)
            self.assertTrue(any("provision.sh --init" in item
                                for item in problems), problems)


class ProvisionAndRegistrationTests(unittest.TestCase):
    def test_provision_links_the_same_two_local_plugins(self) -> None:
        text = PROVISION.read_text(encoding="utf-8")
        self.assertIn("PLUGIN=$INFRA/dsh/eu4-goal-plugin", text)
        self.assertIn("TOOLS_PLUGIN=$INFRA/dsh/eu4-tools-plugin", text)
        self.assertIn('install_local_plugin @eu4/dsh-goal "$PLUGIN"', text)
        self.assertIn('install_local_plugin @eu4/dsh-tools "$TOOLS_PLUGIN"', text)
        self.assertIn("link_is_expected", text)
        self.assertIn('add "link:$package"', text)

    def test_overlay_registers_both_local_plugins(self) -> None:
        text = OVERLAY.read_text(encoding="utf-8")
        for plugin_id, package in (("eu4-goal", "@eu4/dsh-goal"),
                                   ("eu4-tools", "@eu4/dsh-tools")):
            self.assertIn(f"- id: {plugin_id}", text)
            self.assertIn(f"name: '{package}'", text)

    def test_run_passes_the_infrastructure_root_to_the_tools_plugin(self) -> None:
        text = RUN.read_text(encoding="utf-8")
        self.assertIn(': "${EU4_DSH_WORKSPACE:=$INFRA}"', text)
        self.assertIn("export EU4_DSH_WORKSPACE", text)

    def test_campaign_overlay_mounts_the_tools_plugin(self) -> None:
        text = RUN_CAMPAIGN.read_text(encoding="utf-8")
        self.assertIn("TOOLS_PLUGIN=$INFRA/dsh/eu4-tools-plugin/lib/index.js", text)
        self.assertIn("- id: eu4-tools", text)
        self.assertIn("infra: '$INFRA'", text)

    def test_ci_runs_both_dsh_plugin_suites(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm test --prefix dsh/eu4-goal-plugin", text)
        self.assertIn("npm test --prefix dsh/eu4-tools-plugin", text)


if __name__ == "__main__":
    unittest.main()
