"""The model-free receipt guard, proved against a synthetic published run.

The guard is what makes a published P3-B receipt *independently* checkable. This
test builds a complete, realistic publication - a receipt produced by the real
controller running the OpenCode adapter against a stub binary, plus a committed
artifact, a sanitized event summary and a pre-registration - then drives the
guard over it and over a set of tampered variants.

The publication is self-contained: the pinned sources are copied into the
temporary root and committed there, so the revision database describes exactly
the bytes the receipt pins. That is the property the guard checks a historical
claim against, and it is what makes the test independent of whether *this*
working tree happens to be clean.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oracle.core import canonical_bytes
from oracle.slice import controller, corpus, opencode, receipts
from oracle.slice.controller import RunConfig
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun

REPO_ROOT = Path(__file__).resolve().parents[1]
SLICE_DIR = REPO_ROOT / "oracle" / "slice"

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "tooling" / "common"))
import test_oracle_slice_opencode as stubs  # noqa: E402
import check_oracle_receipt as guard  # noqa: E402

_GIT = ("git", "-c", "user.name=guard-test", "-c", "user.email=guard-test@example.invalid",
        "-c", "commit.gpgsign=false")


def _git(root: Path, *arguments: str) -> str:
    probe = subprocess.run([*_GIT, "-C", str(root), *arguments], capture_output=True, text=True)
    if probe.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {probe.stderr}")
    return probe.stdout.strip()


def _publish(root: Path) -> dict:
    """Produce a certified receipt and publish it in its own pinned repository."""
    staging = root / "staging"
    shutil.copytree(REPO_ROOT / "oracle", root / "oracle",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _git(root, "init", "--quiet")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "pin the oracle sources")
    revision = _git(root, "rev-parse", "HEAD")

    stub = stubs._write_stub(root, stubs._completed_stream())
    adapter = OpenCodeAdapter(OpenCodeRun(
        run_id="run-e2e", run_dir=staging / "run-e2e",
        workspace=staging / "run-e2e" / "workspace", repo_root=REPO_ROOT,
        python=Path(sys.executable), binary=stub, provider="opencode",
        model_id="deepseek-v4.1-flash-free", variant="high"))
    result = controller.run(RunConfig(
        run_id="run-e2e", candidate_src=SLICE_DIR / "target.c", base_dir=staging,
        budget_calls=60, max_attempts=1, wall_seconds=120.0, finalization_seconds=300.0,
        baseline_commit=revision, require_repair_acceptance=True,
        model={"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"},
    ), adapter)
    assert result.status == controller.STATUS_CERTIFIED, result.receipt

    for name in ("receipts", "artifacts", "agents", "preregistration"):
        (root / "docs" / "oracle" / name).mkdir(parents=True, exist_ok=True)

    payload = result.receipt["payload"]
    binding = payload["toolchain"]["opencode"]
    (root / "docs/oracle/receipts/run-e2e.json").write_bytes(canonical_bytes(result.receipt))
    # The receipt records the snapshot relative to its base directory.
    snapshot = Path(payload["source"]["snapshot"])
    if not snapshot.is_absolute():
        snapshot = staging / snapshot
    (root / "docs/oracle/artifacts" / f"{payload['source']['final_candidate_sha256']}.c").write_bytes(
        snapshot.read_bytes())
    adapter.write_sanitized_summary(root / "docs/oracle/agents/run-e2e.events.json")
    # The sealed call journal travels with the publication.
    shutil.copyfile(result.run_dir / "tool-calls.json",
                    root / "docs/oracle/agents/run-e2e.tool-calls.json")

    prereg = {
        "schema": "eu4-oracle-preregistration/v1",
        "run_id": "run-e2e",
        "mode": "acceptance",
        "staging_root": str(staging),
        "python": str(Path(sys.executable)),
        "repo_root": str(REPO_ROOT),
        "binary": dict(binding["binary"]),
        "model": dict(binding["model"]),
        "prompt_sha256": binding["prompt_sha256"],
        "config": {
            "template_sha256": binding["config"]["template_sha256"],
            "materialized_sha256": binding["config"]["materialized_sha256"],
        },
        "substitutions": {
            "@MODEL@": adapter.run.model,
            "@PYTHON@": str(Path(sys.executable)),
            "@REPO_ROOT@": str(REPO_ROOT),
            "@RUN_DIR@": str(adapter.run.run_dir),
            "@WORKSPACE@": str(adapter.run.workspace),
        },
        "corpus": {"visible": payload["corpus"]["visible"],
                   "held_out": payload["corpus"]["held_out"]},
        "source": {
            "candidate_path": "oracle/slice/target.c",
            "defective_candidate_sha256": payload["source"]["defective_candidate_sha256"],
            "baseline_commit": revision,
        },
        "run": {
            "budget_calls": 60,
            "attempts": 1,
            "wall_seconds": 120.0,
            "finalization_seconds": 300.0,
        },
        "source_hashes": {
            "bridge_sha256": receipts.file_sha256(SLICE_DIR / "bridge.py"),
        },
    }
    (root / "docs/oracle/preregistration/run-e2e.json").write_text(
        json.dumps(prereg, indent=2), encoding="utf-8")
    return prereg


def _reseal(root: Path, mutate) -> None:
    """Tamper with a receipt *and* re-seal it, so only the payload check can fail."""
    path = root / "docs/oracle/receipts/run-e2e.json"
    receipt = json.loads(path.read_text())
    mutate(receipt["payload"])
    receipt["sha256"] = receipts.receipt_digest(receipt["payload"])
    path.write_bytes(canonical_bytes(receipt))


class ReceiptGuardTests(unittest.TestCase):
    def test_a_clean_publication_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            self.assertEqual(guard.main(["--root", str(root)]), 0)

    def test_no_publication_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(guard.main(["--root", tmp]), 0)

    def test_a_declaration_without_a_receipt_is_pending_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            (root / "docs/oracle/receipts/run-e2e.json").unlink()
            self.assertEqual(guard.main(["--root", str(root)]), 0)
            self.assertEqual(guard.main(["--root", str(root), "--require-receipts"]), 1)

    def test_a_tampered_receipt_fails_its_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            path = root / "docs/oracle/receipts/run-e2e.json"
            receipt = json.loads(path.read_text())
            receipt["payload"]["verdict"]["status"] = "certified_but_edited"
            path.write_text(json.dumps(receipt))
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_a_reordered_pre_registration_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            path = root / "docs/oracle/preregistration/run-e2e.json"
            prereg = json.loads(path.read_text())
            prereg["prompt_sha256"] = "0" * 64
            path.write_text(json.dumps(prereg))
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_a_changed_current_source_does_not_invalidate_a_pinned_run(self):
        """The guard must not reject a historical claim because *today's* code moved.

        This is the regression for the guard rejecting its own later remediation:
        the pinned identities are checked against the pinned revision, and the
        replay runs under that revision's own modules.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            for name in ("harness.py", "compare.py", "bridge.py", "controller.py", "driver.c"):
                target = root / "oracle" / "slice" / name
                target.write_bytes(target.read_bytes() + b"\n/* a later commit */\n")
            self.assertEqual(guard.main(["--root", str(root)]), 0)

    def test_a_pin_that_disagrees_with_its_revision_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            path = root / "docs/oracle/preregistration/run-e2e.json"
            prereg = json.loads(path.read_text())
            prereg["source_hashes"]["bridge_sha256"] = "0" * 64
            path.write_text(json.dumps(prereg), encoding="utf-8")
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_a_receipt_that_disagrees_with_its_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            _reseal(root, lambda payload: payload["toolchain"].__setitem__(
                "harness_sha256", "0" * 64))
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_a_published_receipt_without_a_declaration_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            (root / "docs/oracle/preregistration/run-e2e.json").unlink()
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_corpus_drift_in_the_current_tree_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            fixtures = sorted((root / "oracle/slice/fixtures/visible").glob("*.json"))
            self.assertTrue(fixtures)
            fixture = json.loads(fixtures[0].read_text())
            fixture["expected"] = {}
            fixtures[0].write_text(json.dumps(fixture), encoding="utf-8")
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_a_run_that_pins_no_revision_cannot_be_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            _reseal(root, lambda payload: payload["source"].__setitem__(
                "baseline_commit", None))
            self.assertEqual(guard.main(["--root", str(root)]), 1)

    def test_held_out_fingerprint_in_the_published_summary_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            path = root / "docs/oracle/agents/run-e2e.events.json"
            summary = json.loads(path.read_text())
            summary["tool_calls"].append({"tool": "hid-000", "status": "completed"})
            path.write_bytes(canonical_bytes(summary) + b"\n")
            self.assertEqual(guard.main(["--root", str(root)]), 1)


class AbsolutePathTests(unittest.TestCase):
    def test_stray_paths_are_flagged_and_allowlisted_ones_are_not(self):
        text = json.dumps({"snapshot": "/Users/fabian/staging/run-e2e/x.c",
                           "cc": "/usr/bin/cc"})
        stray = guard.check_absolute_paths(text, ("/opt/other",))
        self.assertIn("/Users/fabian/staging/run-e2e/x.c", stray)
        self.assertEqual(guard.check_absolute_paths(text, ("/Users/fabian/staging/", "/usr/")), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
