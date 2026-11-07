"""Orchestration entry for A0 implementer → review → remediate loops.

Usage:
  python3 -m automation.handoff_cli run-scripted <workflow.json>
  python3 -m automation.handoff_cli run-opencode <workflow.json> <implementer_campaign.json>

``run-scripted`` is a deterministic non-agent proof (not independent review).
``run-opencode`` launches real OpenCode implementer and reviewer sessions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from automation.handoff import HandoffController, write_synthetic_rejection
from automation.opencode import VERIFIED_VERSION, OpenCodeAgent
from automation.runner import Runner
from automation.state import load_campaign, repo_root
from automation.verification_manifest import file_sha256


def _export_candidate_into(workspace: Path, repo: Path, candidate_commit: str) -> None:
    """Materialize allowlisted candidate files from a complete snapshot commit."""
    rels = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-tree",
            "-r",
            "--name-only",
            candidate_commit,
            "--",
            "analysis/qualification",
            "progress.md",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for rel in rels:
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{candidate_commit}:{rel}"],
            capture_output=True,
            check=True,
        ).stdout
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)


def _write_remediation_brief(
    path: Path,
    *,
    state: dict,
    finding: dict,
) -> None:
    binding = finding.get("binding") or {}
    path.write_text(
        "# Remediation brief\n\n"
        f"Repair the validated finding `{finding.get('id')}`.\n\n"
        f"- observation: `{binding.get('observation')}`\n"
        f"- expected: `{binding.get('expected')}`\n"
        f"- candidate commit: `{state.get('rejected_candidate_commit')}`\n"
        f"- oracle version: `{state.get('oracle_version')}`\n"
        f"- oracle manifest: `{state.get('oracle_manifest_path')}`\n"
        f"- oracle digest: `{state.get('oracle_manifest_sha256')}`\n\n"
        "Start from the rejected candidate snapshot already in this workspace. "
        "Do not edit trusted oracle files.\n",
        encoding="utf-8",
    )


def _scripted_controller(
    repo: Path, workflow_path: Path, oracle_dir: Path
) -> HandoffController:
    workspace = repo / ".local" / "qualification" / "scripted-workspace"
    evidence = json.loads(
        (oracle_dir / "primitive-table.evidence.json").read_text(encoding="utf-8")
    )
    reports = workflow_path.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    def run_implementer(campaign_id: str, state: dict) -> dict:
        workspace.mkdir(parents=True, exist_ok=True)
        # Remediation: seed from rejected snapshot first
        if state.get("rejected_candidate_commit") and state.get("state") == "remediating":
            _export_candidate_into(
                workspace, repo, state["rejected_candidate_commit"]
            )
            finding = (state.get("last_review") or {}).get("findings") or [{}]
            _write_remediation_brief(
                workspace / "objective-remediation.md",
                state=state,
                finding=finding[0] if finding else {},
            )
        cand_dir = workspace / "analysis" / "qualification" / "v0"
        cand_dir.mkdir(parents=True, exist_ok=True)
        table = evidence["output"]["table"]
        mode = evidence["output"]["engine_enum_4_gl_mode"]
        # Base campaign repairs A only; remediation repairs B from brief/snapshot
        if state.get("state") == "remediating" or state.get("round", 0) >= 1:
            oor = evidence["output"]["mapping"]["default_out_of_range"]
        else:
            oor = 0
        (cand_dir / "primitive-lookup.candidate.json").write_text(
            json.dumps(
                {
                    "schema": "eu4-primitive-lookup/v1",
                    "table": table,
                    "engine_enum_4_gl_mode": mode,
                    "out_of_range_default": oor,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (workspace / "progress.md").write_text("# ok\n", encoding="utf-8")
        env = os.environ.copy()
        env["EU4_A01_FIXTURE_MODE"] = "1"
        env["EU4_QUALIFICATION_ORACLE"] = str(oracle_dir)
        if state.get("oracle_manifest_path"):
            pin = Path(state["oracle_manifest_path"])
            if not pin.is_absolute():
                pin = repo / pin
            if pin.is_file():
                env["EU4_QUALIFICATION_MANIFEST"] = str(pin)
        ran = subprocess.run(
            [
                sys.executable,
                str(repo / "campaigns/eu4-a01-trusted-validation/accept.py"),
                str(workspace),
            ],
            cwd=str(repo),
            capture_output=True,
            text=True,
            env=env,
        )
        payload = json.loads(ran.stdout or "{}")
        verdict = payload.get("verdict")
        return {
            "status": "complete" if verdict == "pass" else "paused",
            "last_verdict": verdict,
            "workspace": str(workspace),
            "pause_reason": None if verdict == "pass" else payload.get("diagnostics"),
            "session_id": f"scripted-impl-{campaign_id}",
        }

    def run_reviewer(state: dict) -> Path:
        # Scripted path only — not independent agent review.
        path = reports / f"review-r{state['round']}.json"
        cand = json.loads(
            (
                workspace
                / "analysis/qualification/v0/primitive-lookup.candidate.json"
            ).read_text(encoding="utf-8")
        )
        expected = evidence["output"]["mapping"]["default_out_of_range"]
        if cand.get("out_of_range_default") == expected:
            from automation.review_result import write_review

            write_review(
                path,
                {
                    "schema": "eu4-review/v1",
                    "candidate_commit": state["candidate_commit"],
                    "oracle_manifest_sha256": state["oracle_manifest_sha256"],
                    "outcome": "qualified",
                    "findings": [],
                },
            )
            return path
        binding = {
            "replay_id": "primitive_lookup_v1",
            "observation": "out_of_range_default",
            "expected": expected,
            "function_rva": evidence["function_rva"],
            "executable_sha256": evidence["executable_sha256"],
            "evidence_ref": {
                "path": "analysis/qualification/v0/primitive-table.evidence.json",
                "sha256": file_sha256(oracle_dir / "primitive-table.evidence.json"),
            },
            "sequence": ["read_candidate", "compare_out_of_range_default"],
        }
        write_synthetic_rejection(
            path,
            candidate_commit=state["candidate_commit"],
            oracle_manifest_sha256=state["oracle_manifest_sha256"],
            binding=binding,
        )
        return path

    return HandoffController(
        repo=repo,
        workflow_path=workflow_path,
        run_implementer=run_implementer,
        run_reviewer=run_reviewer,
        oracle_dir=oracle_dir,
        checker_path=repo / "campaigns/eu4-a01-trusted-validation/accept.py",
    )


def _seed_temp_oracle(repo: Path, oracle_dir: Path) -> None:
    """Install synthetic fixture oracle for portable scripted runs."""
    fixture = repo / "tests" / "fixtures" / "qualification"
    oracle_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        fixture / "primitive-table.evidence.json",
        oracle_dir / "primitive-table.evidence.json",
    )
    shutil.copy2(
        fixture / "primitive-lookup.golden.json",
        oracle_dir / "primitive-lookup.golden.json",
    )
    from automation.verification_manifest import (
        REPO_BASELINE_COMMIT,
        EU4_EXECUTABLE_SHA256,
        build_manifest,
        member_from_file,
        save_manifest,
    )

    # Members hashed against the real repo helpers + fixture evidence via abs path
    evidence_rel = str((oracle_dir / "primitive-table.evidence.json"))
    members = [
        member_from_file(repo, "automation/evidence.py", "trusted_helper"),
        member_from_file(repo, "automation/macho_map.py", "trusted_helper"),
        member_from_file(repo, "automation/verification_manifest.py", "trusted_helper"),
        member_from_file(repo, "automation/replay_primitive.py", "trusted_helper"),
        member_from_file(
            repo, "campaigns/eu4-a01-trusted-validation/accept.py", "checker"
        ),
        {
            "path": evidence_rel,
            "sha256": file_sha256(oracle_dir / "primitive-table.evidence.json"),
            "role": "frozen_oracle",
        },
    ]
    manifest = build_manifest(
        members=members,
        repo_baseline_commit=REPO_BASELINE_COMMIT,
        eu4_executable_sha256=EU4_EXECUTABLE_SHA256,
        extra={"oracle_version_name": "v0"},
    )
    save_manifest(oracle_dir / "verification-manifest.json", manifest)
    (oracle_dir / "CURRENT").write_text("v0\n", encoding="utf-8")


def run_scripted(workflow_path: Path) -> int:
    repo = repo_root(workflow_path)
    # Portable: use temp oracle under workflow dir, never mutate tracked tip
    oracle = workflow_path.parent / "oracle"
    if oracle.exists():
        shutil.rmtree(oracle)
    _seed_temp_oracle(repo, oracle)
    workspace = repo / ".local" / "qualification" / "scripted-workspace"
    if workspace.exists():
        shutil.rmtree(workspace)
    seed = repo / "campaigns/eu4-a01-trusted-validation/seed"
    shutil.copytree(seed, workspace)
    controller = _scripted_controller(repo, workflow_path, oracle)
    pin = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    state = controller.load_or_init("eu4-a01-trusted-validation", pin)
    for _ in range(12):
        state = controller.step(state)
        if state["state"] in ("awaiting_merge", "blocked"):
            break
        if state["state"] == "reviewing" and state.get("review_path"):
            state = controller.step(state)
    print(
        json.dumps(
            {
                "state": state["state"],
                "receipt": state.get("qualification_receipt"),
                "block": state.get("block_reason"),
            },
            indent=2,
        )
    )
    return 0 if state["state"] == "awaiting_merge" else 1


def run_opencode(workflow_path: Path, campaign_path: Path) -> int:
    """Drive live OpenCode implementer + reviewer (no synthetic review fallback)."""
    repo = repo_root(campaign_path)
    oracle = repo / "analysis" / "qualification" / "v0"
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    if not password:
        print("OPENCODE_SERVER_PASSWORD required", file=sys.stderr)
        return 2
    reports = workflow_path.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    def run_implementer(campaign_id: str, state: dict) -> dict:
        path = campaign_path
        run_root = repo / ".local" / "automation" / campaign_id
        if state.get("state") == "remediating" and state.get("rejected_candidate_commit"):
            rem_dir = campaign_path.parent / campaign_id
            rem_dir.mkdir(parents=True, exist_ok=True)
            data = json.loads(campaign_path.read_text(encoding="utf-8"))
            data["id"] = campaign_id
            seed_ws = rem_dir / "seed"
            if seed_ws.exists():
                shutil.rmtree(seed_ws)
            seed_ws.mkdir(parents=True)
            _export_candidate_into(
                seed_ws, repo, state["rejected_candidate_commit"]
            )
            finding = ((state.get("last_review") or {}).get("findings") or [{}])[0]
            _write_remediation_brief(
                rem_dir / "objective.md", state=state, finding=finding
            )
            data["objective"] = "objective.md"
            data["workspace"] = "seed"
            if state.get("oracle_manifest_path"):
                data["verification_manifest"] = state["oracle_manifest_path"]
            path = rem_dir / "campaign.json"
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            # copy brief next to campaign
            shutil.copy2(rem_dir / "objective.md", rem_dir / "objective.md")
        campaign = load_campaign(path)
        if campaign.opencode_version != VERIFIED_VERSION:
            raise RuntimeError("unsupported opencode version")
        agent = OpenCodeAgent(
            os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
            username=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
            password=password,
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
            model=campaign.model,
        )
        runner = Runner(
            campaign, agent, run_root, secrets=[password] if password else []
        )
        result = runner.execute(resume_only=run_root.joinpath("state.json").is_file())
        result["session_id"] = result.get("session_id")
        return result

    def run_reviewer(state: dict) -> dict:
        out = reports / f"review-r{state['round']}.json"
        if out.is_file():
            return {"path": out, "session_id": state.get("reviewer_session_id")}
        # Detached worktree at candidate commit
        wt = repo / ".local" / "qualification" / f"review-wt-r{state['round']}"
        if wt.exists():
            shutil.rmtree(wt)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "worktree",
                "add",
                "--detach",
                str(wt),
                state["candidate_commit"],
            ],
            check=True,
            capture_output=True,
        )
        review_campaign_dir = campaign_path.parent / "eu4-a02-reviewer-live"
        review_campaign_dir.mkdir(parents=True, exist_ok=True)
        obj = review_campaign_dir / "objective.md"
        shutil.copy2(
            repo / "campaigns/eu4-a02-reviewer/objective.md",
            obj,
        )
        obj.write_text(
            obj.read_text(encoding="utf-8")
            + f"\n\nCandidate commit: `{state['candidate_commit']}`\n"
            f"Write report to: `{out}`\n",
            encoding="utf-8",
        )
        accept_src = repo / "campaigns/eu4-a02-reviewer/accept.py"
        shutil.copy2(accept_src, review_campaign_dir / "accept.py")
        camp = {
            "id": state["reviewer_campaign_id"],
            "objective": "objective.md",
            "runtime": "opencode",
            "model": load_campaign(campaign_path).model,
            "opencode_version": VERIFIED_VERSION,
            "workspace": {
                "mode": "worktree",
                "path": str(wt.relative_to(repo))
                if wt.is_relative_to(repo)
                else str(wt),
                "commit": state["candidate_commit"],
            },
            "accept": ["python3", "accept.py"],
            "max_rounds": 6,
            "max_seconds": 3600,
            "accept_timeout_seconds": 180,
        }
        camp_path = review_campaign_dir / "campaign.json"
        camp_path.write_text(json.dumps(camp, indent=2) + "\n", encoding="utf-8")
        env = os.environ.copy()
        env["EU4_REVIEW_REPORT_PATH"] = str(out)
        env["EU4_REVIEW_EXPECTED_CANDIDATE"] = state["candidate_commit"]
        env["EU4_REVIEW_EXPECTED_ORACLE"] = state["oracle_manifest_sha256"]
        campaign = load_campaign(camp_path)
        agent = OpenCodeAgent(
            os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
            username=os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
            password=password,
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
            model=campaign.model,
        )
        run_root = repo / ".local" / "automation" / campaign.id
        # Inject env for accept child processes via Runner — accept reads os.environ
        os.environ["EU4_REVIEW_REPORT_PATH"] = str(out)
        os.environ["EU4_REVIEW_EXPECTED_CANDIDATE"] = state["candidate_commit"]
        os.environ["EU4_REVIEW_EXPECTED_ORACLE"] = state["oracle_manifest_sha256"]
        runner = Runner(
            campaign, agent, run_root, secrets=[password] if password else []
        )
        result = runner.execute(resume_only=run_root.joinpath("state.json").is_file())
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            raise RuntimeError(
                f"reviewer campaign did not complete: {result.get('pause_reason')}"
            )
        if not out.is_file():
            raise RuntimeError("reviewer completed without writing out-of-tree report")
        return {
            "path": out,
            "session_id": result.get("session_id"),
            "worktree": str(wt),
        }

    controller = HandoffController(
        repo=repo,
        workflow_path=workflow_path,
        run_implementer=run_implementer,
        run_reviewer=run_reviewer,
        oracle_dir=oracle,
        checker_path=repo / "campaigns/eu4-a01-trusted-validation/accept.py",
    )
    pin = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    state = controller.load_or_init(load_campaign(campaign_path).id, pin)
    state["require_live_sessions"] = True
    for _ in range(20):
        state = controller.step(state)
        if state["state"] in ("awaiting_merge", "blocked"):
            break
        if state["state"] == "reviewing" and state.get("review_path"):
            state = controller.step(state)
    print(json.dumps(state, indent=2, default=str)[:8000])
    return 0 if state["state"] == "awaiting_merge" else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("run-scripted", "run-opencode"):
        print(
            "usage: python3 -m automation.handoff_cli run-scripted <workflow.json>\n"
            "       python3 -m automation.handoff_cli run-opencode <workflow.json> <campaign.json>",
            file=sys.stderr,
        )
        return 2
    if args[0] == "run-scripted":
        return run_scripted(Path(args[1]))
    return run_opencode(Path(args[1]), Path(args[2]))


if __name__ == "__main__":
    raise SystemExit(main())
