"""The model-free receipt guard, proved against a synthetic published run.

The guard is what makes a published P3-B receipt *independently* checkable. This
test builds a complete, realistic publication - a receipt produced by the real
controller running the OpenCode adapter against a stub binary, plus a committed
artifact, a sanitized event summary and a pre-registration - then drives the
guard over it and over a set of tampered variants.
"""
from __future__ import annotations

import json
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


def _publish(root: Path) -> dict:
    """Produce a certified receipt and publish it the way PR-B will."""
    staging = root / "staging"
    stub = stubs._write_stub(root, stubs._completed_stream())
    adapter = OpenCodeAdapter(OpenCodeRun(
        run_id="run-e2e", run_dir=staging / "run-e2e",
        workspace=staging / "run-e2e" / "workspace", repo_root=REPO_ROOT,
        python=Path(sys.executable), binary=stub, provider="opencode",
        model_id="deepseek-v4.1-flash-free", variant="high"))
    result = controller.run(RunConfig(
        run_id="run-e2e", candidate_src=SLICE_DIR / "target.c", base_dir=staging,
        budget_calls=60, max_attempts=1, wall_seconds=120.0,
        model={"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"},
    ), adapter)
    assert result.status == controller.STATUS_CERTIFIED, result.receipt

    # Publish under a repo root that shares the real oracle sources.
    (root / "oracle").symlink_to(REPO_ROOT / "oracle", target_is_directory=True)
    for name in ("receipts", "artifacts", "agents", "preregistration"):
        (root / "docs" / "oracle" / name).mkdir(parents=True, exist_ok=True)

    payload = result.receipt["payload"]
    binding = payload["toolchain"]["opencode"]
    (root / "docs/oracle/receipts/run-e2e.json").write_bytes(canonical_bytes(result.receipt))
    snapshot = Path(payload["source"]["snapshot"])
    (root / "docs/oracle/artifacts" / f"{payload['source']['final_candidate_sha256']}.c").write_bytes(
        snapshot.read_bytes())
    adapter.write_sanitized_summary(root / "docs/oracle/agents/run-e2e.events.json")

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
        "source_hashes": {
            "bridge_sha256": receipts.file_sha256(SLICE_DIR / "bridge.py"),
        },
    }
    (root / "docs/oracle/preregistration/run-e2e.json").write_text(
        json.dumps(prereg, indent=2), encoding="utf-8")
    return prereg


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

    def test_a_changed_oracle_refuses_to_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _publish(root)
            (root / "oracle").unlink()
            (root / "oracle").mkdir()
            (root / "oracle/slice").mkdir()
            for name in ("harness.py", "compare.py", "driver.c", "synth.h", "bridge.py"):
                source = SLICE_DIR / name
                (root / "oracle/slice" / name).write_bytes(source.read_bytes() + b"\n")
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
