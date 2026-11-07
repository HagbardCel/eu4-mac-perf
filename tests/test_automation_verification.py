"""A0.1 verification: workspace candidate, manifest integrity, unavailable."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.evidence import extract_primitive_table
from automation.runner import Runner
from automation.state import load_campaign
from automation.verification_manifest import (
    EU4_EXECUTABLE_SHA256,
    REPO_BASELINE_COMMIT,
    build_manifest,
    member_from_file,
    verify_manifest,
)
from automation_fake import FakeAgent

REPO = Path(__file__).resolve().parents[1]
EXE = Path("/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4")
A01_ACCEPT = REPO / "campaigns" / "eu4-a01-trusted-validation" / "accept.py"
SEED = REPO / "campaigns" / "eu4-a01-trusted-validation" / "seed"
ORACLE = REPO / "analysis" / "qualification" / "v0"


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


class ManifestTests(unittest.TestCase):
    def test_identities_remain_distinct(self) -> None:
        self.assertNotEqual(REPO_BASELINE_COMMIT, EU4_EXECUTABLE_SHA256)

    def test_verify_detects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "helper.py"
            helper.write_text("x = 1\n", encoding="utf-8")
            manifest = build_manifest(
                members=[member_from_file(root, "helper.py", "trusted_helper")]
            )
            self.assertEqual(verify_manifest(root, manifest), [])
            helper.write_text("x = 2\n", encoding="utf-8")
            self.assertTrue(any("hash mismatch" in e for e in verify_manifest(root, manifest)))


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

    def test_campaign_without_verification_manifest_still_runs(self) -> None:
        """Runner compat: absent verification_manifest keeps prior behavior."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = root / "seed"
            seed.mkdir()
            (seed / "marker.txt").write_text("todo\n", encoding="utf-8")
            (seed / "progress.md").write_text(
                "# Progress\n\n## Next\nwait\n", encoding="utf-8"
            )
            (root / "objective.md").write_text("ready\n", encoding="utf-8")
            smoke = REPO / "campaigns" / "automation-smoke" / "accept.py"
            path = root / "campaign.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "compat",
                        "objective": "objective.md",
                        "runtime": "opencode",
                        "model": "fake/model",
                        "opencode_version": "2.2.5",
                        "workspace": "seed",
                        "accept": ["python3", str(smoke)],
                        "max_rounds": 4,
                        "max_seconds": 100,
                        "accept_timeout_seconds": 5,
                    }
                ),
                encoding="utf-8",
            )
            campaign = load_campaign(path)
            self.assertIsNone(campaign.verification_manifest)
            agent = FakeAgent()

            def ready(workspace: Path) -> None:
                (workspace / "marker.txt").write_text("ready\n", encoding="utf-8")

            agent.on_submit = ready
            clock = Clock()
            runner = Runner(
                campaign, agent, root / "run", clock=clock.monotonic, sleep=clock.sleep
            )
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "complete")


@unittest.skipUnless(
    EXE.is_file() and (ORACLE / "verification-manifest.json").is_file(),
    "oracle not promoted",
)
class WorkspaceCandidateTests(unittest.TestCase):
    def test_seed_fails_then_workspace_fix_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws"
            shutil.copytree(SEED, workspace)
            fail = subprocess.run(
                ["python3", str(A01_ACCEPT), str(workspace)],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(fail.stdout)["verdict"], "fail", fail.stdout)

            evidence = json.loads(
                (ORACLE / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            cand = workspace / "analysis/qualification/v0/primitive-lookup.candidate.json"
            # Repair defect A only; leave B
            cand.write_text(
                json.dumps(
                    {
                        "schema": "eu4-primitive-lookup/v1",
                        "table": evidence["output"]["table"],
                        "engine_enum_4_gl_mode": evidence["output"]["engine_enum_4_gl_mode"],
                        "out_of_range_default": 0,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            ok = subprocess.run(
                ["python3", str(A01_ACCEPT), str(workspace)],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(ok.stdout)["verdict"], "pass", ok.stdout)

    def test_oracle_tamper_is_unavailable_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "ws"
            shutil.copytree(SEED, workspace)
            evidence = ORACLE / "primitive-table.evidence.json"
            original = evidence.read_text(encoding="utf-8")
            try:
                # Tamper frozen oracle bytes without touching workspace candidate
                evidence.write_text(original.replace('"instruction_static"', '"tampered"'), encoding="utf-8")
                payload = json.loads(
                    subprocess.run(
                        ["python3", str(A01_ACCEPT), str(workspace)],
                        cwd=REPO,
                        capture_output=True,
                        text=True,
                    ).stdout
                )
                self.assertEqual(payload["verdict"], "unavailable")
                self.assertIn("integrity", payload["diagnostics"].lower())
            finally:
                evidence.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
