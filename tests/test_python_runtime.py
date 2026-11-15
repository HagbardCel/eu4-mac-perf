"""Phase 1: one absolute, environment-correct, dependency-checked interpreter."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import agent_tools, python_runtime  # noqa: E402


def imports(name: str) -> bool:
    try:
        importlib.import_module(name)
    except Exception:  # noqa: BLE001
        return False
    return True


def make_venv(root: Path) -> tuple[Path, Path]:
    """A real symlink-based venv and the site-packages it owns."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)],
                   check=True, capture_output=True, text=True)
    python = root / "bin" / "python"
    completed = subprocess.run(
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True, capture_output=True, text=True)
    return python, Path(completed.stdout.strip())


class InterpreterEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.venv = cls.root / "venv"
        cls.venv_python, site_packages = make_venv(cls.venv)
        # A dependency that exists only inside this virtualenv.
        (site_packages / "only_in_venv.py").write_text(
            "PRESENT = True\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def test_a_bare_name_is_absolute_but_keeps_its_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            link = directory / "python-under-test"
            os.symlink(sys.executable, link)
            value = python_runtime.resolve(
                "python-under-test", ("numpy",),
                environ={"PATH": f"{directory}{os.pathsep}{os.environ.get('PATH', '')}"})
            real_target = os.path.realpath(link)
        self.assertEqual(value["executable"], str(link))
        self.assertNotEqual(value["executable"], real_target)
        self.assertTrue(os.path.isabs(value["executable"]))

    def test_a_symlinked_virtualenv_is_probed_as_its_own_environment(self):
        self.assertTrue(os.path.islink(self.venv_python))
        value = python_runtime.resolve(str(self.venv_python), ("only_in_venv",))
        self.assertTrue(value["ok"], value)
        # The selected executable is exactly the one whose dependencies were
        # tested; the venv entry point is not dereferenced to the base binary.
        self.assertEqual(value["executable"], str(self.venv_python))
        self.assertNotEqual(value["executable"], os.path.realpath(self.venv_python))
        self.assertTrue(value["virtualenv"])
        self.assertEqual(Path(value["environment"]).resolve(),
                         self.venv.resolve())
        dependency = value["dependencies"]["only_in_venv"]
        self.assertTrue(dependency["ok"])
        self.assertIn(str(self.venv), dependency["origin"])

    def test_the_same_dependency_is_absent_from_the_system_interpreter(self):
        value = python_runtime.probe(sys.executable, ("only_in_venv",))
        self.assertFalse(value["ok"])
        self.assertEqual(value["missing"], ["only_in_venv"])
        self.assertIsNone(value["dependencies"]["only_in_venv"]["origin"])

    def test_an_explicitly_configured_virtualenv_retains_its_path(self):
        value = python_runtime.resolve(
            None, ("only_in_venv",),
            environ={"EU4_DSH_PYTHON": str(self.venv_python)})
        self.assertTrue(value["ok"], value)
        self.assertEqual(value["source"], "EU4_DSH_PYTHON")
        self.assertEqual(value["executable"], str(self.venv_python))
        self.assertEqual(value["configured"], str(self.venv_python))

    def test_a_broken_import_is_not_a_present_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            broken = directory / "broken"
            broken.mkdir()
            (broken / "numpy.py").write_text(
                "raise ImportError('numpy is broken in this environment')\n",
                encoding="utf-8")
            wrapper = directory / "python-broken-numpy"
            wrapper.write_text(
                "#!/bin/sh\n"
                f'PYTHONPATH="{broken}" exec "{sys.executable}" "$@"\n',
                encoding="utf-8")
            wrapper.chmod(0o755)
            # The specification is discoverable...
            found = subprocess.run(
                [str(wrapper), "-c",
                 "import importlib.util,sys;"
                 "print(bool(importlib.util.find_spec('numpy')))"],
                capture_output=True, text=True, check=True)
            self.assertEqual(found.stdout.strip(), "True")
            # ...but importing it fails, so the dependency is unusable.
            value = python_runtime.probe(str(wrapper), ("numpy",))
        self.assertFalse(value["ok"])
        self.assertEqual(value["missing"], ["numpy"])
        self.assertIn("ImportError", value["dependency_errors"]["numpy"])
        self.assertIn("cannot import numpy", value["reason"])

    def test_a_nonexistent_interpreter_fails_closed(self):
        value = python_runtime.resolve("/no/such/python", ("numpy",))
        self.assertFalse(value["ok"])
        self.assertIn("cannot resolve the configured interpreter", value["reason"])
        self.assertIn("does not exist", value["reason"])

    @unittest.skipUnless(imports("numpy"), "numpy is not installed here")
    def test_a_valid_interpreter_with_working_numpy_reports_imported_version(self):
        value = python_runtime.probe(sys.executable, ("numpy",))
        self.assertTrue(value["ok"], value)
        self.assertTrue(value["dependencies"]["numpy"]["ok"])
        self.assertIsNotNone(value["dependencies"]["numpy"]["version"])
        self.assertEqual(value["executable"], python_runtime.absolute(sys.executable))

    def test_resolution_of_the_current_interpreter_preserves_its_path(self):
        value = python_runtime.resolve(environ={})
        self.assertTrue(value["absolute"])
        self.assertEqual(value["source"], "current")
        self.assertEqual(value["executable"],
                         python_runtime.absolute(sys.executable))
        self.assertIn("numpy", value["dependencies"])
        self.assertEqual(value["ok"], imports("numpy"))

    def test_runtime_block_distinguishes_virtualenv_environments(self):
        with mock.patch.dict(
                __import__("os").environ,
                {"EU4_DSH_PYTHON": str(self.venv_python)}):
            block = agent_tools._runtime_block()
        self.assertEqual(block["configured_executable"],
                         str(self.venv_python))
        self.assertEqual(Path(block["configured_environment"]).resolve(),
                         self.venv.resolve())
        self.assertTrue(block["mismatch"])
        with mock.patch.dict(
                __import__("os").environ,
                {"EU4_DSH_PYTHON": python_runtime.absolute(sys.executable)}):
            same = agent_tools._runtime_block()
        self.assertFalse(same["mismatch"])

    def test_cli_print_path_preserves_a_virtualenv(self):
        completed = subprocess.run(
            [str(self.venv_python), "-m", "automation.python_runtime",
             "resolve", "--requested", str(self.venv_python),
             "--require", "only_in_venv", "--print-path"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), str(self.venv_python))

    def test_cli_reports_a_nonzero_exit_for_a_bad_interpreter(self):
        completed = subprocess.run(
            [sys.executable, "-m", "automation.python_runtime", "resolve",
             "--requested", "/no/such/python", "--json"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertNotEqual(completed.returncode, 0)
        value = json.loads(completed.stdout)
        self.assertFalse(value["ok"])


if __name__ == "__main__":
    unittest.main()
