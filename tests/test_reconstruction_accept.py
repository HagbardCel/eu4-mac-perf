"""Invariants for the shared reconstruction checker."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import automation.reconstruction_accept as accept
from automation.runner import Runner
from automation_fake import FakeAgent
from test_automation_runner import write_campaign

REPO = Path(__file__).resolve().parents[1]
SHA = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
SYMBOL = "_GfxExample"
NOTE = "analysis/note.md"
REGISTRY = "renderer/integration/eu4_gfx_entries.def"
RESEARCH = f"{SYMBOL} at 0x1000\n"
PREFIX = bytes.fromhex("554889e54157")
NORMAL = [
    {"rva": "0x1000", "length": 6, "bytes": "554889e54157"},
    {"rva": "0x1006", "length": 1, "bytes": "c3"},
]


def gfx(name: str, start: int, end: int, fn: str, prefix: bytes = PREFIX) -> str:
    encoded = "".join(f"\\x{byte:02x}" for byte in prefix)
    return f'GFX_ENTRY({name}, {start:#x}, {end:#x}, {fn}, "{encoded}")\n'


BASELINE = gfx("settings", 0x100, 0x180, "eu4_gfx_settings")
ADDED = BASELINE + gfx(SYMBOL, 0x1000, 0x1007, "eu4_gfx_example")


def configuration(**updates) -> dict:
    config = {
        "symbol": SYMBOL,
        "note": NOTE,
        "allowed_paths": [NOTE, "progress.md", REGISTRY],
        "test_modules": ["tests.toy"],
    }
    config.update(updates)
    return config


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.repo, self.workspace = root / "infra", root / "candidate"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        self.write(self.repo, "automation/reconstruction_accept.py", accept._MODULE.read_text())
        self.write(self.repo, "reverse_engineering/target.json", json.dumps({"sha256": SHA}))
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "baseline without registry")
        self.missing_registry_pin = git(self.repo, "rev-parse", "HEAD")
        self.write(self.repo, REGISTRY, BASELINE)
        git(self.repo, "add", REGISTRY)
        git(self.repo, "commit", "-qm", "post-R2a baseline")
        self.pin = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "worktree", "add", "--detach", str(self.workspace), self.pin)
        self.note = self.write(self.workspace, NOTE, RESEARCH)
        self.registry = self.workspace / REGISTRY
        self.checkpoint = accept.checkpoint_path(self.workspace)
        parser = accept.import_parser(self.workspace)
        self.enterContext(patch.object(accept, "trusted_root", return_value=self.repo))
        self.enterContext(patch.object(accept, "import_parser", return_value=parser))
        self.enterContext(patch.object(accept, "_executable_sha", return_value=(SHA, None)))
        self.enterContext(patch.object(accept, "load_span", return_value=(0x1000, 0x1007, NORMAL, None)))
        self.toolchain = self.enterContext(patch.object(accept, "toolchain_problem", return_value=None))
        self.build = self.enterContext(patch.object(accept, "build_candidate", return_value=None))
        self.enterContext(patch.object(accept, "run_modules", return_value=None))

    @staticmethod
    def write(root: Path, relative: str, content: str) -> Path:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def check(self, verdict: str, detail: str, *, pin: str | None = None, **config) -> None:
        contract = {"id": "eu4-example", "workspace": {"commit": pin or self.pin}}
        result = accept.check(self.workspace, contract, configuration(**config))
        self.assertEqual(result["verdict"], verdict, result)
        self.assertIn(detail, result["diagnostics"])

    def test_pinned_worktree_and_checker_are_authoritative(self) -> None:
        self.check("unavailable", "worktree HEAD is not the pinned commit", pin=self.missing_registry_pin)
        git(self.workspace, "switch", "-c", "candidate-branch")
        self.check("unavailable", "worktree is not detached")
        self.write(self.repo, "automation/reconstruction_accept.py", "# different checker bytes")
        git(self.repo, "commit", "-qam", "checker drift")
        drifted = git(self.repo, "rev-parse", "HEAD")
        git(self.workspace, "switch", "--detach", drifted)
        self.check("unavailable", "shared checker does not match the pinned commit", pin=drifted)
        self.assertFalse(self.checkpoint.exists())

    def test_missing_pinned_registry_refuses_research_checkpoint(self) -> None:
        git(self.workspace, "switch", "--detach", self.missing_registry_pin)
        self.check("unavailable", "pinned source unavailable", pin=self.missing_registry_pin)
        self.assertFalse(self.checkpoint.exists())

    def test_checkpoint_lifecycle(self) -> None:
        # Candidate metadata must not displace the pin's executable identity.
        target = "reverse_engineering/target.json"
        self.write(self.repo, target, '{"sha256":"fff"}')
        self.check("inconclusive", "research checkpoint captured")
        self.check("inconclusive", "research checkpoint preserved")
        saved = self.checkpoint.read_bytes()
        self.assertEqual(accept.checkpoint_sha256(self.checkpoint), hashlib.sha256(saved).hexdigest())
        self.note.write_text("rewritten\nBLOCKER: encoder required\n")
        self.check("fail", "research checkpoint was modified")
        self.assertEqual(self.checkpoint.read_bytes(), saved)
        self.note.write_text(RESEARCH)
        self.check("unavailable", "reconstruction configuration changed", test_modules=["tests.other"])
        body = json.loads(saved)
        body["function"] = "corrupted"
        self.checkpoint.write_text(json.dumps(body))
        self.check("unavailable", "research checkpoint identity changed")

    def test_relocated_registry_across_both_stages(self) -> None:
        self.note.write_text("BLOCKER: encoder required\n")
        self.registry.write_text(ADDED)
        self.check("fail", "before the research checkpoint")
        self.assertFalse(self.checkpoint.exists())
        self.registry.write_text(BASELINE)
        self.note.write_text(RESEARCH)
        self.check("inconclusive", "research checkpoint captured")

        self.registry.unlink()
        self.check("fail", "candidate registry is missing")
        self.registry.write_text(ADDED)
        self.write(self.workspace, "renderer/integration/unauthorized.mm", "// outside the allowlist")
        self.check("fail", "source scope violated")
        (self.workspace / "renderer/integration/unauthorized.mm").unlink()
        self.registry.write_text(BASELINE + gfx(SYMBOL, 0x1100, 0x1107, "eu4_gfx_example"))
        self.check("fail", "entry rva does not match research")
        self.build.assert_not_called()

        self.registry.write_text(ADDED)
        self.toolchain.return_value = "no Metal SDK"
        self.check("unavailable", "no Metal SDK")
        self.toolchain.return_value = None
        self.build.return_value = "candidate library failed to build: undefined"
        self.check("fail", "failed to build")
        self.build.return_value = None
        self.check("pass", "automated offline validation passed")

    def test_entry_authenticity(self) -> None:
        parse = accept.import_parser(Path(tempfile.gettempdir())).parse_gfx_entries
        self.assertIsNone(accept.registration_problem(
            ADDED, BASELINE, SYMBOL, 0x1000, 0x1007, NORMAL, parse))
        cases = {
            "entry rva does not match research": BASELINE + gfx(SYMBOL, 0x1100, 0x1107, "eu4_gfx_example"),
            "entry end does not match the instruction span from research": BASELINE + gfx(SYMBOL, 0x1000, 0x1010, "eu4_gfx_example"),
            "prefix does not match the instruction bytes from research": BASELINE + gfx(
                SYMBOL, 0x1000, 0x1007, "eu4_gfx_example", bytes.fromhex("554889e541")),
            "pre-existing gfx entry changed": gfx("settings", 0x100, 0x190, "eu4_gfx_settings") + gfx(
                SYMBOL, 0x1000, 0x1007, "eu4_gfx_example"),
        }
        for diagnostic, text in cases.items():
            with self.subTest(diagnostic=diagnostic):
                problem = accept.registration_problem(
                    text, BASELINE, SYMBOL, 0x1000, 0x1007, NORMAL, parse)
                self.assertEqual(problem, diagnostic)

class ExecutionTests(unittest.TestCase):
    def _module(self, root: Path, body: str) -> None:
        tests = root / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "__init__.py").write_text("", encoding="utf-8")
        (tests / "toy.py").write_text(body, encoding="utf-8")

    def test_result_counts(self) -> None:
        passing = "import unittest\nclass T(unittest.TestCase):\n def test_a(self):\n  pass\n"
        failing = "import unittest\nclass T(unittest.TestCase):\n def test_a(self):\n  self.fail('nope')\n"
        skipped = "import unittest\nclass T(unittest.TestCase):\n @unittest.skip('x')\n def test_a(self):\n  pass\n"
        unexpected = "import unittest\nclass T(unittest.TestCase):\n @unittest.expectedFailure\n def test_a(self):\n  pass\n"
        expected = "import unittest\nclass T(unittest.TestCase):\n @unittest.expectedFailure\n def test_a(self):\n  self.fail('expected')\n"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._module(root, passing)
            self.assertIsNone(accept.run_modules(root, ["tests.toy"]))
            self._module(root, failing)
            failed = accept.run_modules(root, ["tests.toy"])
            self.assertTrue(failed.startswith("candidate tests failed"))
            self._module(root, skipped)
            quiet = accept.run_modules(root, ["tests.toy"])
            self.assertTrue(quiet.startswith("unavailable:"))
            self._module(root, unexpected)
            surprise = accept.run_modules(root, ["tests.toy"])
            self.assertTrue(surprise.startswith("candidate tests failed"))
            self.assertIn("unexpected success", surprise)
            self._module(root, expected)
            self.assertIsNone(accept.run_modules(root, ["tests.toy"]))

    def test_metal_validation_environment(self) -> None:
        body = (
            "import os, unittest\n"
            "class T(unittest.TestCase):\n"
            " def test_env(self):\n"
            "  self.assertEqual(os.environ.get('MTL_DEBUG_LAYER'), '1')\n"
            "  self.assertEqual(os.environ.get('MTL_DEBUG_LAYER_ERROR_MODE'), 'assert')\n"
        )
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._module(root, body)
            self.assertIsNone(accept.run_modules(root, ["tests.toy"]))


class ThinImportTests(unittest.TestCase):
    def test_imports_without_pythonpath(self) -> None:
        script = REPO / "campaigns/eu4-set-scissor-rectangle/accept.py"
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        ran = subprocess.run(
            [sys.executable, str(script), str(script.parent / "missing")],
            cwd=script.parent, env=env, capture_output=True, text=True,
        )
        self.assertNotIn("ModuleNotFoundError", ran.stderr)
        self.assertIn(json.loads(ran.stdout)["verdict"], {"fail", "unavailable"})


class RunnerBlockerTests(unittest.TestCase):
    def test_blocked_does_not_resume(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'blocked','diagnostics':'BLOCKER: encoder required'}))\n",
                encoding="utf-8",
            )
            agent = FakeAgent()
            runner = Runner(
                __import__("automation.state", fromlist=["load_campaign"]).load_campaign(
                    write_campaign(root, checker=checker, max_rounds=4)),
                agent, root / "run",
            )
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "semantic_blocker")
            self.assertEqual(state["last_verdict"], "blocked")
            self.assertIn("BLOCKER:", state["last_diagnostics"])
            self.assertEqual(len(agent.submits), 1)
            resumed = runner.execute(resume_only=True)
            self.assertEqual(resumed["pause_reason"], "semantic_blocker")
            self.assertEqual(len(agent.submits), 1)


class IntegrationTests(unittest.TestCase):
    def test_real_worktree_captures_research(self) -> None:
        status = subprocess.run(
            [sys.executable, "-m", "reverse_engineering.research", "status"],
            cwd=REPO, capture_output=True, text=True,
        )
        if status.returncode != 0:
            self.skipTest("research unavailable")
        try:
            document = json.loads(status.stdout)
        except json.JSONDecodeError:
            self.skipTest("research unavailable")
        if document.get("status") != "ok":
            self.skipTest("research unavailable")
        try:
            blob = subprocess.check_output(
                ["git", "-C", str(REPO), "show", "HEAD:automation/reconstruction_accept.py"],
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            self.skipTest("shared checker is not in HEAD")
        if blob != accept._MODULE.read_bytes():
            self.skipTest("shared checker is not in HEAD")
        resolved = subprocess.run(
            [sys.executable, "-m", "reverse_engineering.research", "resolve", "_GfxSetIndexBuffer", "--limit", "5"],
            cwd=REPO, capture_output=True, text=True, check=False,
        )
        if resolved.returncode != 0:
            self.skipTest("research unavailable")
        identity = json.loads(resolved.stdout)["identity"]
        symbol = identity["name_mangled"]
        entry = identity["entry_rva"]
        pin = git(REPO, "rev-parse", "HEAD")
        with tempfile.TemporaryDirectory() as raw:
            worktree = Path(raw) / "workspace"
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "add", "--detach", str(worktree), pin],
                check=True, capture_output=True, text=True,
            )
            try:
                note = worktree / NOTE
                note.parent.mkdir(parents=True, exist_ok=True)
                note.write_text(f"{symbol} {entry}\n", encoding="utf-8")
                result = accept.check(worktree, {
                    "id": "eu4-integration",
                    "workspace": {"commit": pin},
                }, configuration(symbol=symbol))
                self.assertEqual(result["verdict"], "inconclusive", result["diagnostics"])
                self.assertTrue(accept.checkpoint_path(worktree).is_file())
            finally:
                subprocess.run(
                    ["git", "-C", str(REPO), "worktree", "remove", "--force", str(worktree)],
                    check=False, capture_output=True, text=True,
                )
