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
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from automation import dsh_profile, python_runtime

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
        self.assertIn(': "${EU4_DSH_INFRA_ROOT:=$INFRA}"', text)
        self.assertIn("export EU4_DSH_INFRA_ROOT", text)
        # EU4_DSH_WORKSPACE belongs to the bridge's candidate assertion.
        self.assertNotIn("export EU4_DSH_WORKSPACE", text)

    def test_overlay_uses_the_infra_root_not_the_workspace_assertion(self) -> None:
        text = OVERLAY.read_text(encoding="utf-8")
        self.assertIn("process.env.EU4_DSH_INFRA_ROOT", text)
        self.assertNotIn("process.env.EU4_DSH_WORKSPACE", text)

    def test_preflight_emits_one_json_object_on_success(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        self.assertNotIn('print(json.dumps({"ok": True}))', text)
        self.assertIn('printf \'{"ok":true', text)

    def test_campaign_overlay_mounts_the_tools_plugin(self) -> None:
        text = RUN_CAMPAIGN.read_text(encoding="utf-8")
        self.assertIn("automation.python_runtime", text)
        self.assertIn("--print-path", text)
        self.assertIn("TOOLS_PLUGIN=$INFRA/dsh/eu4-tools-plugin/lib/index.js", text)
        self.assertIn("- id: eu4-tools", text)
        self.assertIn("infra: '$INFRA'", text)

    def test_ci_runs_both_dsh_plugin_suites(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("npm test --prefix dsh/eu4-goal-plugin", text)
        self.assertIn("npm test --prefix dsh/eu4-tools-plugin", text)


@unittest.skipUnless(
    importlib.util.find_spec("numpy") is not None,
    "the launcher requires the diagnostic NumPy interpreter")
class LauncherEnvironmentTests(unittest.TestCase):
    """The launchers must not reuse the bridge's candidate-workspace variable.

    These use a fake ``dsh`` so no Web server, model request or EU4 launch is
    made. The campaign case still runs the real bridge prepare, including its
    candidate-workspace guard.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "dsh-home"
        (self.home / "profiles" / "eu4-web").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.out = self.root / "fake-dsh.json"
        fake = self.bin / "dsh"
        fake.write_text(
            "#!/bin/sh\n"
            "python3 - \"$@\" <<'PY'\n"
            "import json, os, sys\n"
            "json.dump({\n"
            "    'cwd': os.getcwd(),\n"
            "    'infra': os.environ.get('EU4_DSH_INFRA_ROOT'),\n"
            "    'workspace': os.environ.get('EU4_DSH_WORKSPACE'),\n"
            "    'skill': os.environ.get('EU4_DSH_SKILL_ROOT'),\n"
            "    'python': os.environ.get('EU4_DSH_PYTHON'),\n"
            "    'args': sys.argv[1:],\n"
            "}, open(os.environ['FAKE_DSH_OUT'], 'w'))\n"
            "PY\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)

    def _env(self, extra: dict | None = None) -> dict:
        env = os.environ.copy()
        for key in ("EU4_DSH_WORKSPACE", "EU4_DSH_INFRA_ROOT", "EU4_DSH_SKILL_ROOT"):
            env.pop(key, None)
        env.update({
            "EU4_DSH_HOME": str(self.home),
            "EU4_DSH_PYTHON": sys.executable,
            "PATH": f"{self.bin}{os.pathsep}{env['PATH']}",
            "FAKE_DSH_OUT": str(self.out),
        })
        env.update(extra or {})
        return env

    def _run(self, args: list[str], cwd: Path, extra_env: dict | None = None):
        return subprocess.run(
            [str(RUN), *args], cwd=cwd, env=self._env(extra_env),
            capture_output=True, text=True, check=False, timeout=180,
        )

    def _editable_worktree(self) -> Path:
        workspace = self.root / "editable-worktree"
        skill = workspace / ".opencode" / "skills" / "opengl-metal-reconstruction"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        return workspace

    def _campaign_sidecar(self) -> tuple[Path, Path]:
        token = uuid.uuid4().hex[:10]
        root = REPO / ".local" / "pre-m7" / f"launcher-{token}"
        workspace = root / "workspace"
        run_root = root / "run"
        sidecar = json.loads(
            (REPO / "dsh" / "campaigns" / "offline-smoke.json").read_text(
                encoding="utf-8"))
        sidecar["candidate"]["workspace"] = str(workspace.relative_to(REPO))
        sidecar["run_root"] = str(run_root.relative_to(REPO))
        path = self.root / "campaign-sidecar.json"
        path.write_text(json.dumps(sidecar), encoding="utf-8")
        self.addCleanup(shutil.rmtree, root, True)
        return path, workspace

    def test_workspace_from_unrelated_cwd_pins_trusted_infra(self) -> None:
        workspace = self._editable_worktree()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        ran = self._run(["--workspace", str(workspace)], unrelated)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        recorded = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(Path(recorded["cwd"]).resolve(), unrelated.resolve())
        self.assertEqual(Path(recorded["infra"]).resolve(), REPO.resolve())
        self.assertIsNone(recorded["workspace"])
        self.assertEqual(Path(recorded["skill"]).resolve(),
                         (workspace / ".opencode" / "skills").resolve())

    def test_campaign_prepare_rejects_a_declared_wrong_workspace(self) -> None:
        sidecar, _ = self._campaign_sidecar()
        ran = self._run(
            ["--campaign", str(sidecar)], self.root,
            {"EU4_DSH_WORKSPACE": str(REPO)},
        )
        self.assertNotEqual(ran.returncode, 0, ran.stdout)
        self.assertIn("declared DSH workspace", ran.stderr + ran.stdout)
        self.assertFalse(self.out.exists(), "prepare refusal must precede dsh")

    def test_campaign_prepare_passes_without_the_workspace_variable(self) -> None:
        sidecar, workspace = self._campaign_sidecar()
        ran = self._run(["--campaign", str(sidecar)], self.root)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        self.assertTrue(workspace.is_dir(), "prepare did not seed the candidate")
        recorded = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(Path(recorded["infra"]).resolve(), REPO.resolve())
        self.assertIsNone(recorded["workspace"])

    @unittest.skipUnless(shutil.which("node"), "the tools bridge needs Node")
    def test_tools_plugin_python_core_resolves_from_the_infra_cwd(self) -> None:
        unrelated = self.root / "unrelated-node"
        unrelated.mkdir()
        bridge = (REPO / "dsh" / "eu4-tools-plugin" / "lib" / "bridge.js").as_uri()
        script = unrelated / "bridge-smoke.mjs"
        script.write_text(
            "import { runTool } from " + json.dumps(bridge) + ";\n"
            "const value = await runTool('eu4_session', "
            "{ op: 'list' }, "
            "{ python: " + json.dumps(sys.executable) +
            ", cwd: " + json.dumps(str(REPO)) +
            ", env: { EU4_DSH_PYTHON: " + json.dumps(sys.executable) + " } });\n"
            "console.log(JSON.stringify({ ok: value.ok, "
            "sessions: value.result?.sessions?.length ?? 0, "
            "runtime: value.runtime, outcome: value.outcome, "
            "schema: value.schema }));\n",
            encoding="utf-8",
        )
        ran = subprocess.run(
            ["node", str(script)], cwd=unrelated,
            capture_output=True, text=True, check=False, timeout=120,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        recorded = json.loads(ran.stdout.strip().splitlines()[-1])
        self.assertTrue(recorded["ok"], ran.stdout)
        self.assertIsInstance(recorded["sessions"], int)
        # The actual bridge spawned the configured absolute interpreter, and
        # the dependency state it reports matches that interpreter.
        self.assertEqual(recorded["schema"], "eu4-agent-tool/v2")
        self.assertEqual(recorded["runtime"]["executable"],
                         python_runtime.absolute(sys.executable))
        numpy_expected = importlib.util.find_spec("numpy") is not None
        self.assertEqual(recorded["runtime"]["numpy"]["available"], numpy_expected)
        self.assertEqual(recorded["runtime"]["configured"],
                         sys.executable)
        self.assertFalse(recorded["runtime"]["mismatch"])
        self.assertEqual(recorded["outcome"]["execution"], "ok")
        self.assertEqual(recorded["outcome"]["diagnostic"], "complete")


class InterpreterConsistencyTests(unittest.TestCase):
    """Phase 1.1: launcher, preflight and adapter agree on one interpreter."""

    def test_profile_has_no_bare_python_default(self) -> None:
        text = OVERLAY.read_text(encoding="utf-8")
        self.assertIn("python: !!js process.env.EU4_DSH_PYTHON ?? ''", text)
        self.assertNotIn("python3.14", text)

    def test_preflight_resolves_and_checks_the_same_interpreter(self) -> None:
        text = PREFLIGHT.read_text(encoding="utf-8")
        self.assertIn("automation.python_runtime", text)
        self.assertIn("--print-path", text)
        self.assertIn("EU4_DSH_PYTHON", text)
        self.assertIn("eu4-tools python does not resolve", text)
        self.assertIn('"python":"%s"', text)

    def test_run_resolves_the_interpreter_before_invoking_the_launcher(self) -> None:
        text = RUN.read_text(encoding="utf-8")
        self.assertIn("automation.python_runtime", text)
        self.assertIn("--print-path", text)
        self.assertIn('echo "run: Python interpreter:', text)


@unittest.skipUnless(shutil.which("node"), "the tools bridge needs Node")
class LauncherInterpreterTests(unittest.TestCase):
    """run.sh must export an absolute interpreter that the profile receives."""

    def setUp(self) -> None:
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("numpy is required to resolve the diagnostic interpreter")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "dsh-home"
        (self.home / "profiles" / "eu4-web").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.out = self.root / "fake-dsh.json"
        fake = self.bin / "dsh"
        fake.write_text(
            "#!/bin/sh\n"
            "python3 - \"$@\" <<'PY'\n"
            "import json, os, sys\n"
            "json.dump({\n"
            "    'python': os.environ.get('EU4_DSH_PYTHON'),\n"
            "    'args': sys.argv[1:],\n"
            "}, open(os.environ['FAKE_DSH_OUT'], 'w'))\n"
            "PY\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        self.python_link = self.bin / "python-eu4"
        os.symlink(sys.executable, self.python_link)

    def _env(self, extra: dict | None = None) -> dict:
        env = os.environ.copy()
        for key in ("EU4_DSH_WORKSPACE", "EU4_DSH_INFRA_ROOT", "EU4_DSH_SKILL_ROOT"):
            env.pop(key, None)
        env.update({
            "EU4_DSH_HOME": str(self.home),
            "PATH": f"{self.bin}{os.pathsep}{env['PATH']}",
            "FAKE_DSH_OUT": str(self.out),
        })
        env.update(extra or {})
        return env

    def _run(self, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(RUN)], cwd=self.root, env=self._env(extra_env),
            capture_output=True, text=True, check=False, timeout=180,
        )

    def test_a_bare_name_is_exported_as_the_resolved_absolute_path(self) -> None:
        ran = self._run({"EU4_DSH_PYTHON": "python-eu4"})
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        recorded = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(recorded["python"], str(self.python_link))
        self.assertTrue(os.path.isabs(recorded["python"]))

    def test_an_explicit_virtualenv_is_exported_unchanged(self) -> None:
        venv = self.root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip",
             "--system-site-packages", str(venv)],
            check=True, capture_output=True, text=True)
        python = venv / "bin" / "python3"
        self.assertTrue(python.exists())
        ran = self._run({"EU4_DSH_PYTHON": str(python)})
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        recorded = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(recorded["python"], str(python))
        self.assertNotEqual(recorded["python"], os.path.realpath(python))

    def test_an_unusable_interpreter_fails_before_the_launcher(self) -> None:
        ran = self._run({"EU4_DSH_PYTHON": "/no/such/python"})
        self.assertNotEqual(ran.returncode, 0)
        self.assertIn("NumPy", ran.stderr + ran.stdout)
        self.assertFalse(self.out.exists(), "dsh must not boot with a bad interpreter")


if __name__ == "__main__":
    unittest.main()
