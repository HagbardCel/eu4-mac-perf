"""Invariants for the shared reconstruction checker."""

from __future__ import annotations

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
PIN = "abc123"
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
        "allowed_paths": [NOTE, "progress.md", "benchmark/eu4_gfx_entries.def"],
        "test_modules": ["tests.toy"],
    }
    config.update(updates)
    return config


def campaign() -> dict:
    return {"id": "eu4-example", "workspace": {"commit": PIN}}


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.paths = [NOTE]
        self.show = {
            "benchmark/eu4_gfx_entries.def": BASELINE,
            "reverse_engineering/target.json": json.dumps({"sha256": SHA}),
        }
        self.patches = [
            patch.object(accept, "_worktree_problem", return_value=None),
            patch.object(accept, "checker_blob_problem", return_value=None),
            patch.object(accept, "changed_paths", side_effect=lambda _workspace: list(self.paths)),
            patch.object(accept, "pinned_show", side_effect=lambda _w, _c, rel: self.show[rel]),
            patch.object(accept, "_executable_sha", return_value=(SHA, None)),
            patch.object(accept, "load_span", return_value=(0x1000, 0x1007, NORMAL, None)),
            patch.object(accept, "toolchain_problem", return_value=None),
            patch.object(accept, "build_candidate", return_value=None),
            patch.object(accept, "run_modules", return_value=None),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def workspace(self, parent: Path, text: str) -> Path:
        workspace = parent / "workspace"
        note = workspace / NOTE
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(text, encoding="utf-8")
        return workspace

    def test_wrong_source_cannot_qualify(self) -> None:
        note = f"{SYMBOL} 0x1000\n"
        with tempfile.TemporaryDirectory() as raw:
            workspace = self.workspace(Path(raw), "BLOCKER: encoder required\n")
            with patch.object(
                accept, "_worktree_problem",
                return_value=("fail", "worktree HEAD is not the pinned commit"),
            ):
                wrong_head = accept.check(workspace, campaign(), configuration())
            self.assertEqual(wrong_head["verdict"], "fail")
            self.assertIn("pinned commit", wrong_head["diagnostics"])
            with patch.object(
                accept, "checker_blob_problem",
                return_value="shared checker does not match the pinned commit",
            ):
                wrong_blob = accept.check(workspace, campaign(), configuration())
            self.assertEqual(wrong_blob["verdict"], "fail")
            self.paths = ["benchmark/evil.c"]
            unauthorized = accept.check(workspace, campaign(), configuration())
            self.assertEqual(unauthorized["verdict"], "fail")
            self.assertIn("source scope violated", unauthorized["diagnostics"])
            self.paths = [NOTE, "benchmark/eu4_gfx_entries.def"]
            premature = accept.check(self.workspace(Path(raw), "BLOCKER: encoder required\n"), campaign(), configuration())
            self.assertEqual(premature["verdict"], "fail")
            self.assertIn("before the research checkpoint", premature["diagnostics"])
            self.assertNotIn("BLOCKER:", premature["diagnostics"])
            self.assertFalse(accept.checkpoint_path(workspace).exists())

    def test_checkpoint_lifecycle(self) -> None:
        text = f"{SYMBOL} at 0x1000\n"
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            workspace = self.workspace(parent, text)
            first = accept.check(workspace, campaign(), configuration())
            path = accept.checkpoint_path(workspace)
            self.assertEqual(first["verdict"], "inconclusive")
            self.assertIn("captured", first["diagnostics"])
            second = accept.check(workspace, campaign(), configuration())
            self.assertEqual(second["verdict"], "inconclusive")
            self.assertIn("preserved", second["diagnostics"])
            digest = subprocess.run(
                [sys.executable, "-c",
                 "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())",
                 str(path)],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            self.assertEqual(digest, accept.checkpoint_sha256(path))
            saved = path.read_bytes()
            (workspace / NOTE).write_text("rewritten\nBLOCKER: encoder required\n", encoding="utf-8")
            rewritten = accept.check(workspace, campaign(), configuration())
            self.assertEqual(rewritten["verdict"], "fail")
            self.assertIn("research checkpoint was modified", rewritten["diagnostics"])
            self.assertEqual(path.read_bytes(), saved)
            (workspace / NOTE).write_text(text, encoding="utf-8")
            changed = configuration(test_modules=["tests.other"])
            drifted = accept.check(workspace, campaign(), changed)
            self.assertEqual(drifted["verdict"], "fail")
            self.assertIn("reconstruction configuration changed", drifted["diagnostics"])

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

    def test_backend_build_and_pass(self) -> None:
        text = f"{SYMBOL} at 0x1000\n"
        with tempfile.TemporaryDirectory() as raw:
            workspace = self.workspace(Path(raw), text)
            self.assertEqual(accept.check(workspace, campaign(), configuration())["verdict"], "inconclusive")
            registry = workspace / "benchmark" / "eu4_gfx_entries.def"
            registry.parent.mkdir()
            registry.write_text(ADDED, encoding="utf-8")
            self.paths = [NOTE, "benchmark/eu4_gfx_entries.def"]
            with patch.object(accept, "build_candidate", return_value="candidate library failed to build: undefined"):
                failed = accept.check(workspace, campaign(), configuration())
            self.assertEqual(failed["verdict"], "fail")
            self.assertIn("failed to build", failed["diagnostics"])
            with patch.object(accept, "toolchain_problem", return_value="no Metal SDK"):
                missing = accept.check(workspace, campaign(), configuration())
            self.assertEqual(missing["verdict"], "unavailable")
            self.assertIn("no Metal SDK", missing["diagnostics"])
            passed = accept.check(workspace, campaign(), configuration())
            self.assertEqual(passed["verdict"], "pass")


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


class BlobTests(unittest.TestCase):
    def test_committed_checker_and_pinned_target(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo = Path(raw)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            module = repo / "automation" / "reconstruction_accept.py"
            module.parent.mkdir()
            module.write_bytes(accept._MODULE.read_bytes())
            target = repo / "reverse_engineering" / "target.json"
            target.parent.mkdir()
            target.write_text(json.dumps({"sha256": SHA}) + "\n", encoding="utf-8")
            git(repo, "add", "automation", "reverse_engineering")
            git(repo, "commit", "-q", "-m", "pin")
            pin = git(repo, "rev-parse", "HEAD")
            self.assertIsNone(accept.checker_blob_problem(repo, pin))
            self.assertIn(SHA, accept.pinned_show(repo, pin, "reverse_engineering/target.json"))
            target.write_text('{"sha256":"fff"}\n', encoding="utf-8")
            self.assertIn(SHA, accept.pinned_show(repo, pin, "reverse_engineering/target.json"))
            module.write_text("changed\n", encoding="utf-8")
            git(repo, "add", "automation")
            git(repo, "commit", "-q", "-m", "drift")
            drifted = git(repo, "rev-parse", "HEAD")
            self.assertEqual(
                accept.checker_blob_problem(repo, drifted),
                "shared checker does not match the pinned commit",
            )


class ThinImportTests(unittest.TestCase):
    def test_imports_without_pythonpath(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO) as raw:
            campaign_dir = Path(raw)
            (campaign_dir / "accept.py").write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "from pathlib import Path\n"
                "def trusted_root():\n"
                "    here = Path(__file__).resolve()\n"
                "    for parent in here.parents:\n"
                "        if (parent / 'automation' / 'reconstruction_accept.py').is_file() and (parent / '.git').exists():\n"
                "            return parent\n"
                "    raise SystemExit('trusted repository not found')\n"
                "sys.path.insert(0, str(trusted_root()))\n"
                "from automation.reconstruction_accept import check\n"
                "here = Path(__file__).resolve().parent\n"
                "campaign = json.loads((here / 'campaign.json').read_text())\n"
                "config = json.loads((here / 'reconstruction.json').read_text())\n"
                "result = check(Path(sys.argv[1]), campaign, config)\n"
                "print(json.dumps(result))\n"
                "raise SystemExit(0 if result['verdict'] == 'pass' else 1 if result['verdict'] == 'fail' else 2)\n",
                encoding="utf-8",
            )
            (campaign_dir / "campaign.json").write_text(json.dumps(campaign()), encoding="utf-8")
            (campaign_dir / "reconstruction.json").write_text(json.dumps(configuration()), encoding="utf-8")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            ran = subprocess.run(
                [sys.executable, "accept.py", str(campaign_dir / "missing")],
                cwd=campaign_dir, env=env, capture_output=True, text=True,
            )
            self.assertNotIn("ModuleNotFoundError", ran.stderr)
            payload = json.loads(ran.stdout)
            self.assertIn(payload["verdict"], {"fail", "unavailable"})


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
