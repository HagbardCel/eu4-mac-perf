"""A8: real Runner/coordinator/checker synthetic lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import launch_ledger  # noqa: E402
from automation.gfx_draw_accept_v2 import build_candidate  # noqa: E402
from automation.gfx_l32b_complete import Coordinator  # noqa: E402
from automation.gfx_l32b_hooks import L32bHooks  # noqa: E402
from tests.automation_fake import FakeAgent  # noqa: E402
from tests.test_gfx_contract_v1_integration import (  # noqa: E402
    PACKET_DELTA,
    V1_MODULE,
    _capture,
    _sha,
)
from tests.test_gfx_draw_packet import (  # noqa: E402
    CALLER,
    DRAW_RVA,
    _draw_row,
    _master,
    _resource_row,
    _write_capture,
)

REQUIRED = (
    "draw_invocation",
    "primitive_semantics",
    "shader_identity",
    "viewport",
    "render_target",
)
V1_EVIDENCE = "analysis/gfx-draw/v0/l32b/contracts/v1/evidence.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _review(path: Path, candidate: str, oracle: str, outcome: str = "qualified") -> None:
    path.write_text(json.dumps({
        "schema": "eu4-review/v1",
        "candidate_commit": candidate,
        "oracle_manifest_sha256": oracle,
        "outcome": outcome,
        "findings": [],
        "summary": "A8 synthetic review",
    }) + "\n", encoding="utf-8")


def _cases(path: Path, candidate: str, contract_version: str, checkpoint: Path,
           field: str = "shader_identity") -> None:
    path.write_text(json.dumps({
        "schema": "eu4-gfx-adversarial-cases/v1",
        "candidate_commit": candidate,
        "contract_version": contract_version,
        "checkpoint_sha256": _sha(checkpoint),
        "fields": [{
            "field": field,
            "mutation_class": "wrong_selected_object",
            "structural_validity": "valid",
            "forged_value": {"resource_id": "wrong", "shader_pointer": "0x9999"},
            "forged_evidence": [{"type": "draw_record", "call_id": 11}],
            "why_semantically_wrong": "not the selected shader",
            "expected_error_prefix": field,
        }],
    }) + "\n", encoding="utf-8")


def _impl_delta(packet: Path) -> None:
    text = packet.read_text(encoding="utf-8")
    if "shader_record = next(" in text:
        return
    marker = '    primitive_enum = target.meta.get("primitive_enum")\n'
    text = text.replace(marker, PACKET_DELTA + marker, 1)
    start = text.index("REQUIRED_INPUTS = (")
    end = text.index(")\n", start) + 2
    text = text[:start] + (
        "REQUIRED_INPUTS = (\n"
        '    "draw_invocation",\n'
        '    "primitive_semantics",\n'
        '    "shader_identity",\n'
        '    "viewport",\n'
        '    "render_target",\n'
        ")\n"
    ) + text[end:]
    packet.write_text(text, encoding="utf-8")


def _setup_capture(run_root: Path, workspace: Path, tmp: Path) -> None:
    capture = run_root / "captures" / "s1"
    if capture.is_dir():
        return
    source = _capture(tmp)
    shutil.copytree(source, capture)
    candidate_sha, failure = build_candidate(workspace)
    if failure:
        raise AssertionError(failure)
    (capture / "capture-request.json").write_text(
        json.dumps({"library_sha256": candidate_sha}) + "\n", encoding="utf-8"
    )
    plan = run_root / "capture-plans" / "s1.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({
        "question": "which shader is bound at the selected draw",
        "expected_observation": "one _GfxSetShader before the selected _GfxDraw",
    }) + "\n", encoding="utf-8")
    pointer = launch_ledger.read_pointer(run_root)
    if pointer is None:
        raise AssertionError("campaign ledger pointer is missing")
    ledger = Path(pointer["ledger"])
    token = launch_ledger.claim(
        ledger,
        campaign_id=run_root.name,
        run_root=run_root,
        output=capture,
        targets=["_GfxDraw"],
        requested_timeout=120,
    )
    launch_ledger.finish(ledger, token, pid=1, status="completed")


def _write_contract(workspace: Path, checkpoint: Path) -> None:
    evidence = workspace / V1_EVIDENCE
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"shader": "shader-1"}) + "\n", encoding="utf-8")
    module = workspace / "automation" / "gfx_contracts" / "v1.py"
    module.write_text(V1_MODULE, encoding="utf-8")
    spec = workspace / "automation" / "gfx_contracts" / "v1.spec.json"
    spec.write_text(json.dumps({
        "schema": "eu4-gfx-contract-spec/v1",
        "version": "v1",
        "fields": [{
            "name": "shader_identity",
            "validator": "validate_shader_identity",
            "evidence_refs": [{
                "path": V1_EVIDENCE,
                "sha256": _sha(evidence),
            }],
        }],
    }, indent=2) + "\n", encoding="utf-8")
    test = workspace / "tests" / "test_gfx_contract_v1.py"
    test.write_text(
        "import unittest\n\n"
        "class V1Tests(unittest.TestCase):\n"
        "    def test_placeholder(self):\n"
        "        self.assertTrue(True)\n",
        encoding="utf-8",
    )


def _state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scripted_agent(tmp: Path, state_path: Path) -> FakeAgent:
    agent = FakeAgent()

    def on_submit(workspace: Path) -> None:
        run_root = workspace.parent
        campaign = run_root.name
        if "contract" in campaign:
            checkpoint = _state(state_path).get("checkpoint")
            if not checkpoint:
                raise AssertionError("contract campaign has no checkpoint")
            _write_contract(workspace, Path(checkpoint))
            return
        if "review" in campaign:
            state = _state(state_path)
            campaign_doc = json.loads(
                (run_root / "campaign.json").read_text(encoding="utf-8")
            ) if (run_root / "campaign.json").is_file() else {}
            target_commit = (campaign_doc.get("workspace") or {}).get("commit")
            target = state.get("final_candidate") or state.get("contract") \
                if target_commit == (state.get("contract") or {}).get("candidate_commit") \
                else state.get("candidate")
            oracle = (target or {}).get("oracle_manifest_sha256") \
                or state.get("oracle_manifest_sha256")
            report = run_root / "review.json"
            _review(report, str(target_commit), str(oracle))
            if target_commit == (state.get("contract") or {}).get("candidate_commit"):
                checkpoint = Path(str(state.get("contract_checkpoint")))
                _cases(report.with_name("review-adversarial.json"),
                       str(target_commit), "v1", checkpoint)
            return
        _impl_delta(workspace / "benchmark" / "eu4_gfx_draw_packet.py")
        _setup_capture(run_root, workspace, tmp)

    agent.on_submit = on_submit
    return agent


@unittest.skipUnless(sys.platform == "darwin", "A8 requires macOS native build")
class E2ETests(unittest.TestCase):
    def test_full_synthetic_lifecycle(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            head = _git(ROOT, "rev-parse", "HEAD")
            branch = f"a8-e2e-{uuid.uuid4().hex[:8]}"
            _git(ROOT, "branch", branch, head)
            templates = tmp / "campaigns"
            shutil.copytree(ROOT / "campaigns", templates)
            complete = templates / "eu4-l32b-complete"
            l32b = json.loads((complete / "l32b.json").read_text())
            l32b["required_inputs"] = list(REQUIRED)
            l32b["test_modules"] = ["tests.test_gfx_draw_packet"]
            (complete / "l32b.json").write_text(json.dumps(l32b, indent=2) + "\n")
            workflow = json.loads((complete / "workflow.json").read_text())
            workflow["workflow_id"] = f"eu4-l32b-complete-a8-{uuid.uuid4().hex[:8]}"
            workflow["required_inputs"] = list(REQUIRED)
            workflow["baseline_commit"] = head
            workflow["integration_branch"] = branch
            workflow["trusted_contract"] = {
                "commit": head,
                "contract_version": "v0",
                "contract_sha256": _sha(ROOT / "automation" / "gfx_contracts" / "v0.py"),
                "manifest": "campaigns/eu4-l32b-complete/l32b-manifest.json",
            }
            workflow["checker_freeze_commit"] = head
            manifest = subprocess.run(
                ["git", "-C", str(ROOT), "show",
                 f"{head}:campaigns/eu4-l32b-complete/l32b-manifest.json"],
                capture_output=True, check=True,
            ).stdout
            workflow["checker_freeze_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
            workflow["oracle_manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
            run_root = ROOT / ".local" / "automation" / f"a8-e2e-{uuid.uuid4().hex[:8]}"
            workflow["launch_ledger"] = {
                "path": str(run_root / "live-launch-ledger.json"),
                "limit": 6,
                "deadline_seconds": 120,
            }
            (complete / "workflow.json").write_text(json.dumps(workflow, indent=2) + "\n")
            state_path = tmp / "state.json"
            agent = _scripted_agent(tmp, state_path)
            hooks = L32bHooks(
                repo=ROOT,
                workflow=workflow,
                run_root=run_root,
                agent_factory=lambda model: agent,
                model="fake/model",
                templates=templates,
            )
            coordinator = Coordinator(
                repo=ROOT, workflow=workflow, state_path=state_path,
                hooks=hooks.hooks(),
            )
            try:
                state = coordinator.run()
            finally:
                subprocess.run(
                    ["git", "-C", str(ROOT), "branch", "-D", branch],
                    check=False, capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(ROOT), "worktree", "prune"],
                    check=False, capture_output=True,
                )
                shutil.rmtree(run_root, ignore_errors=True)
                for leftover in (ROOT / ".local" / "automation").glob(
                        f"*{workflow['workflow_id']}*"):
                    shutil.rmtree(leftover, ignore_errors=True)
            self.assertEqual(state["state"], "awaiting_merge", state)
            self.assertEqual(state["final_candidate"]["candidate_commit"],
                             _git(ROOT, "rev-parse", branch))
            self.assertTrue(state["final_review"]["qualification_receipt_sha256"])


if __name__ == "__main__":
    unittest.main()
