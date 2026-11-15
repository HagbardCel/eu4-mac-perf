"""Phase 1: one absolute, dependency-checked Python interpreter."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import python_runtime  # noqa: E402


def fake_interpreter(directory: Path, *, numpy: bool = False,
                     version=(3, 14, 0)) -> Path:
    path = directory / "fake-python"
    dependency = json.dumps("/fake/site-packages/numpy/__init__.py"
                            if numpy else None)
    path.write_text(
        "#!/bin/sh\n"
        "cat <<'JSON'\n"
        + json.dumps({
            "executable": str(path),
            "version": list(version),
            "dependencies": {"numpy": None if not numpy else "/fake/numpy"},
        })
        + "\nJSON\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


class ResolutionTests(unittest.TestCase):
    def test_resolution_is_absolute_and_reports_the_current_interpreter(self):
        value = python_runtime.resolve(environ={})
        self.assertTrue(value["absolute"])
        self.assertTrue(os.path.isabs(value["executable"]))
        self.assertEqual(value["source"], "current")
        self.assertEqual(Path(value["executable"]).resolve(),
                         Path(sys.executable).resolve())
        self.assertIn("numpy", value["dependencies"])
        expected = importlib.util.find_spec("numpy") is not None
        self.assertEqual(value["ok"], expected, value)
        if not expected:
            self.assertIn("cannot import numpy", value["reason"])

    def test_explicit_path_is_authoritative_and_never_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = fake_interpreter(Path(temporary), numpy=False)
            value = python_runtime.resolve(str(fake), ("numpy",))
        self.assertFalse(value["ok"])
        self.assertEqual(value["source"], "requested")
        self.assertEqual(Path(value["executable"]).resolve(), fake.resolve())
        self.assertEqual(value["missing"], ["numpy"])
        self.assertIn("cannot import numpy", value["reason"])
        self.assertIn("EU4_DSH_PYTHON", value["reason"])

    def test_a_satisfied_explicit_path_is_used_as_given(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = fake_interpreter(Path(temporary), numpy=True)
            value = python_runtime.resolve(str(fake), ("numpy",))
        self.assertTrue(value["ok"], value)
        self.assertEqual(Path(value["executable"]).resolve(), fake.resolve())
        self.assertEqual(value["dependencies"]["numpy"], "/fake/numpy")

    def test_a_bare_name_is_resolved_on_path_to_an_absolute_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake = fake_interpreter(directory, numpy=True)
            renamed = directory / "python-under-test"
            fake.rename(renamed)
            value = python_runtime.resolve("python-under-test", ("numpy",),
                                           environ={"PATH": str(directory)})
        self.assertTrue(value["ok"], value)
        self.assertTrue(os.path.isabs(value["executable"]))
        self.assertEqual(Path(value["executable"]).resolve(), renamed.resolve())

    def test_a_missing_interpreter_is_an_actionable_failure(self):
        value = python_runtime.resolve("/no/such/python", ("numpy",))
        self.assertFalse(value["ok"])
        self.assertIn("does not exist", value["reason"])
        self.assertIn("cannot resolve the configured interpreter",
                      value["reason"])

    def test_environment_is_only_used_when_no_argument_is_given(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake = fake_interpreter(directory, numpy=True)
            value = python_runtime.resolve(None, ("numpy",),
                                           environ={"EU4_DSH_PYTHON": str(fake)})
        self.assertEqual(value["source"], "EU4_DSH_PYTHON")
        self.assertEqual(Path(value["executable"]).resolve(), fake.resolve())

    def test_probe_matches_the_running_environment(self):
        value = python_runtime.probe(sys.executable, ("numpy",))
        expected = importlib.util.find_spec("numpy") is not None
        self.assertEqual(value["dependencies"]["numpy"] is None, not expected)
        self.assertEqual(value["ok"], expected)
        self.assertIn("numpy", value["required"])

    def test_cli_print_path_is_absolute(self):
        completed = subprocess.run(
            [sys.executable, "-m", "automation.python_runtime", "resolve",
             "--print-path"],
            cwd=ROOT, capture_output=True, text=True, check=False)
        if importlib.util.find_spec("numpy") is None:
            # The resolver fails closed without the comparison dependency.
            self.assertEqual(completed.returncode, 1)
            self.assertIn("cannot import numpy", completed.stderr)
            return
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(os.path.isabs(completed.stdout.strip()))

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
