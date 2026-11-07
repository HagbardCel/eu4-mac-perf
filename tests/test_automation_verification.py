"""A0.1 verification manifest, evidence promotion, and unavailable handling."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.evidence import extract_primitive_table, lookup_from_table
from automation.runner import Runner, run_accept
from automation.state import file_sha256, load_campaign, save_state
from automation.verification_manifest import (
    EU4_EXECUTABLE_SHA256,
    REPO_BASELINE_COMMIT,
    build_manifest,
    digest_manifest,
    member_from_file,
    verify_manifest,
)
from automation_fake import FakeAgent  # noqa: E402 — tests/ on path via unittest discover

REPO = Path(__file__).resolve().parents[1]
EXE = Path("/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4")
A01_ACCEPT = REPO / "campaigns" / "eu4-a01-trusted-validation" / "accept.py"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@unittest.skipUnless(EXE.is_file(), "pinned EU4 executable not installed")
class PrimitiveEvidenceTests(unittest.TestCase):
    def test_extract_table_enum_4_is_gl_5(self) -> None:
        record = extract_primitive_table(EXE)
        self.assertEqual(record["provenance"], "instruction_static")
        self.assertEqual(record["executable_sha256"], EU4_EXECUTABLE_SHA256)
        self.assertEqual(record["output"]["engine_enum_4_gl_mode"], 5)
        self.assertEqual(lookup_from_table(record["output"]["table"], 4), 5)
        self.assertEqual(lookup_from_table(record["output"]["table"], 99), 4)

    def test_wrong_executable_hash_rejected(self) -> None:
        with self.assertRaises(ValueError):
            extract_primitive_table(EXE, expected_sha256="0" * 64)


class ManifestTests(unittest.TestCase):
    def test_identities_remain_distinct(self) -> None:
        self.assertNotEqual(REPO_BASELINE_COMMIT, EU4_EXECUTABLE_SHA256)

    def test_verify_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "helper.py"
            helper.write_text("x = 1\n", encoding="utf-8")
            manifest = build_manifest(
                members=[member_from_file(root, "helper.py", "trusted_helper")],
            )
            self.assertEqual(verify_manifest(root, manifest), [])
            helper.write_text("x = 2\n", encoding="utf-8")
            errors = verify_manifest(root, manifest)
            self.assertTrue(any("hash mismatch" in err for err in errors))


class UnavailableRunnerTests(unittest.TestCase):
    def test_unavailable_pauses_without_remediation_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed"
            seed.mkdir()
            (seed / "progress.md").write_text("# Progress\n", encoding="utf-8")
            (root / "objective.md").write_text("noop\n", encoding="utf-8")
            checker = root / "accept.py"
            checker.write_text(
                "import json\n"
                "print(json.dumps({'verdict':'unavailable','diagnostics':'no fixture'}))\n",
                encoding="utf-8",
            )
            path = root / "campaign.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "unavailable-test",
                        "objective": "objective.md",
                        "runtime": "opencode",
                        "model": "fake/model",
                        "opencode_version": "2.2.5",
                        "workspace": "seed",
                        "accept": ["python3", str(checker)],
                        "max_rounds": 4,
                        "max_seconds": 100,
                        "accept_timeout_seconds": 5,
                    }
                ),
                encoding="utf-8",
            )
            campaign = load_campaign(path)
            agent = FakeAgent()
            clock = Clock()
            runner = Runner(
                campaign, agent, root / "run", clock=clock.monotonic, sleep=clock.sleep
            )
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "validation_unavailable")
            self.assertEqual(state["last_verdict"], "unavailable")
            self.assertEqual(state["rounds"], 1)
            self.assertEqual(len(agent.submits), 1)

    def test_run_accept_recognizes_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "objective.md").write_text("x\n", encoding="utf-8")
            seed = root / "seed"
            seed.mkdir()
            checker = root / "accept.py"
            checker.write_text(
                "import json\n"
                "print(json.dumps({'verdict':'unavailable','diagnostics':'gap'}))\n",
                encoding="utf-8",
            )
            path = root / "campaign.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "u",
                        "objective": "objective.md",
                        "runtime": "opencode",
                        "model": "fake/model",
                        "opencode_version": "2.2.5",
                        "workspace": "seed",
                        "accept": ["python3", str(checker)],
                        "max_rounds": 1,
                        "max_seconds": 10,
                        "accept_timeout_seconds": 5,
                    }
                ),
                encoding="utf-8",
            )
            campaign = load_campaign(path)
            outcome = run_accept(campaign, seed)
            self.assertTrue(outcome["ok"])
            self.assertEqual(outcome["verdict"], "unavailable")


@unittest.skipUnless(
    EXE.is_file()
    and (REPO / "analysis/qualification/v0/verification-manifest.json").is_file(),
    "oracle not promoted",
)
class A01CheckerSmokeTests(unittest.TestCase):
    def test_pass_on_correct_candidate(self) -> None:
        import subprocess

        completed = subprocess.run(
            ["python3", str(A01_ACCEPT), str(REPO)],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "pass")


if __name__ == "__main__":
    unittest.main()
