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

from automation.handoff import (
    HandoffController,
    ensure_reviewer_worktree,
    quarantine_report,
    recover_completed_reviewer_receipt,
    reviewer_invocation_path_for,
    reviewer_receipt_path_for,
    reviewer_worktree_path,
    run_root_for,
    save_workflow,
    verify_reviewer_receipt,
    write_reviewer_invocation,
    write_reviewer_receipt,
    write_synthetic_rejection,
    load_reviewer_receipt,
)
from automation.opencode import OBSERVED_VERSION, OpenCodeAgent
from automation.runner import Runner
from automation.state import load_campaign, repo_root
from automation.verification_manifest import file_sha256


def _export_candidate_into(workspace: Path, repo: Path, candidate_commit: str) -> None:
    """Materialize editable candidate + progress from a complete snapshot commit."""
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
        # Only editable surface — never overlay protected evidence from snapshot
        name = Path(rel).name
        if rel != "progress.md" and name != "primitive-lookup.candidate.json":
            continue
        blob = subprocess.run(
            ["git", "-C", str(repo), "show", f"{candidate_commit}:{rel}"],
            capture_output=True,
            check=True,
        ).stdout
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)


def resolve_oracle_manifest_pin(repo: Path, state: dict) -> Path | None:
    """Absolute pinned oracle manifest path for campaign JSON + accept env."""
    raw = state.get("oracle_manifest_path")
    if not raw:
        return None
    pin = Path(str(raw))
    if not pin.is_absolute():
        pin = (repo / pin).resolve()
    else:
        pin = pin.resolve()
    if not pin.is_file():
        raise RuntimeError(f"oracle manifest pin missing: {pin}")
    expected = state.get("oracle_manifest_sha256")
    if expected and file_sha256(pin) != expected:
        raise RuntimeError(
            f"oracle manifest pin digest mismatch: {pin} != {expected}"
        )
    return pin


def build_remediation_campaign(
    repo: Path,
    campaign_path: Path,
    state: dict,
) -> Path:
    """Materialize a remediation campaign from rejected snapshot + finding brief.

    Pure helper (no OpenCode) so unit tests can exercise the live failure path.
    """
    campaign_id = state["remediation_campaign_id"]
    # Keep generated remediation campaigns out of tracked campaigns/
    rem_dir = (
        repo
        / ".local"
        / "qualification"
        / "campaigns"
        / state["workflow_id"]
        / campaign_id
    )
    rem_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(campaign_path.read_text(encoding="utf-8"))
    data["id"] = campaign_id
    seed_ws = rem_dir / "seed"
    if seed_ws.exists():
        shutil.rmtree(seed_ws)
    seed_ws.mkdir(parents=True)
    _export_candidate_into(seed_ws, repo, state["rejected_candidate_commit"])
    finding = ((state.get("last_review") or {}).get("findings") or [{}])[0]
    _write_remediation_brief(rem_dir / "objective.md", state=state, finding=finding)
    data["objective"] = "objective.md"
    data["workspace"] = "seed"
    pin = resolve_oracle_manifest_pin(repo, state)
    if pin is not None:
        data["verification_manifest"] = str(pin)
    # Preserve accept checker next to the remediation campaign root
    accept = data.get("accept") or []
    for part in accept:
        src = campaign_path.parent / str(part)
        if src.is_file() and src.suffix == ".py":
            dest = rem_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
    out = rem_dir / "campaign.json"
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def pin_qualification_env(repo: Path, state: dict, oracle_dir: Path) -> None:
    """Set EU4_QUALIFICATION_* so Runner accept children see the absolute pin."""
    os.environ["EU4_QUALIFICATION_ORACLE"] = str(oracle_dir)
    pin = resolve_oracle_manifest_pin(repo, state)
    if pin is not None:
        os.environ["EU4_QUALIFICATION_MANIFEST"] = str(pin)


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
            "verification_manifest_sha256": state.get("oracle_manifest_sha256"),
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


def reviewer_report_path(reports: Path, workflow_id: str, round_id: int) -> Path:
    return reports / f"{workflow_id}-review-r{round_id}.json"


def materialize_reviewer_campaign(
    repo: Path,
    state: dict,
    *,
    campaign_path: Path,
    worktree: Path,
    report_path: Path,
    pin: Path,
) -> Path:
    """Write the reviewer campaign pinned to the workflow's immutable oracle."""
    review_campaign_dir = (
        repo
        / ".local"
        / "qualification"
        / "campaigns"
        / f"eu4-a02-reviewer-live-{state['workflow_id']}"
    )
    review_campaign_dir.mkdir(parents=True, exist_ok=True)
    obj = review_campaign_dir / "objective.md"
    shutil.copy2(
        repo / "campaigns/eu4-a02-reviewer/objective.md",
        obj,
    )
    obj.write_text(
        obj.read_text(encoding="utf-8")
        + f"\n\nCandidate commit: `{state['candidate_commit']}`\n"
        f"Write report to: `{report_path}`\n",
        encoding="utf-8",
    )
    accept_src = repo / "campaigns/eu4-a02-reviewer/accept.py"
    shutil.copy2(accept_src, review_campaign_dir / "accept.py")
    camp = {
        "id": state["reviewer_campaign_id"],
        "objective": "objective.md",
        "runtime": "opencode",
        "model": load_campaign(campaign_path).model,
        "opencode_version": OBSERVED_VERSION,
        "workspace": {
            "mode": "worktree",
            "path": str(worktree.relative_to(repo))
            if worktree.is_relative_to(repo)
            else str(worktree),
            "commit": state["candidate_commit"],
        },
        "accept": ["python3", "accept.py"],
        "max_rounds": 6,
        "max_seconds": 3600,
        "accept_timeout_seconds": 180,
        "verification_manifest": str(pin),
    }
    camp_path = review_campaign_dir / "campaign.json"
    camp_path.write_text(json.dumps(camp, indent=2) + "\n", encoding="utf-8")
    return camp_path


def legacy_workflow_layout(repo: Path, state: dict, reports: Path) -> Path | None:
    """Detect pre-namespacing artifacts for an unfinished workflow.

    Resuming one would silently start against ambiguous state, so callers block.
    """
    if state.get("state") in ("awaiting_merge", "blocked"):
        return None
    campaign_id = {
        "reviewing": state.get("reviewer_campaign_id"),
        "qualified_by_checker": state.get("reviewer_campaign_id"),
        "remediating": state.get("remediation_campaign_id"),
    }.get(state["state"]) or state.get("implementer_campaign_id")
    if campaign_id:
        legacy_root = repo / ".local" / "automation" / campaign_id
        scoped = run_root_for(repo, state["workflow_id"], campaign_id)
        if legacy_root.exists() and not scoped.exists():
            return legacy_root
    round_id = state.get("round", 0)
    legacy_report = reports / f"review-r{round_id}.json"
    scoped_report = reports / f"{state['workflow_id']}-review-r{round_id}.json"
    if legacy_report.exists() and not scoped_report.exists():
        return legacy_report
    return None


def run_opencode(workflow_path: Path, campaign_path: Path) -> int:
    """Drive live OpenCode implementer + reviewer (no synthetic review fallback)."""
    repo = repo_root(campaign_path)
    oracle = repo / "analysis" / "qualification" / "v0"
    reports = workflow_path.parent / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    def run_implementer(campaign_id: str, state: dict) -> dict:
        path = campaign_path
        run_root = run_root_for(repo, state["workflow_id"], campaign_id)
        if state.get("state") == "remediating" and state.get("rejected_candidate_commit"):
            path = build_remediation_campaign(repo, campaign_path, state)
        pin_qualification_env(repo, state, oracle)
        campaign = load_campaign(path)
        agent = OpenCodeAgent(
            os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
            model=campaign.model,
        )
        runner = Runner(campaign, agent, run_root)
        result = runner.execute(resume_only=run_root.joinpath("state.json").is_file())
        return result

    def run_reviewer(state: dict) -> dict:
        out = reviewer_report_path(
            reports, state["workflow_id"], state["round"]
        ).resolve()
        receipt_path = reviewer_receipt_path_for(out)
        invocation_path = reviewer_invocation_path_for(out)
        campaign_id = state["reviewer_campaign_id"]
        worktree = reviewer_worktree_path(repo, state["workflow_id"], state["round"])
        pin = resolve_oracle_manifest_pin(repo, state)
        if pin is None:
            raise RuntimeError("reviewer requires an immutable oracle pin")
        # Resume only with a completed receipt bound to this round's identity.
        if out.is_file() and receipt_path.is_file():
            try:
                receipt = load_reviewer_receipt(receipt_path)
                errs = verify_reviewer_receipt(
                    receipt,
                    campaign_id=campaign_id,
                    candidate_commit=state["candidate_commit"],
                    oracle_digest=state["oracle_manifest_sha256"],
                    report_sha256=file_sha256(out),
                    require_live=True,
                )
            except (OSError, json.JSONDecodeError, ValueError):
                errs = ["unreadable"]
            if not errs:
                # Worktree may have been cleared by a controller restart; the
                # canonical path is reconstructed and verified before acceptance.
                return {
                    "path": out,
                    "session_id": receipt["reviewer_session_id"],
                    "receipt_path": str(receipt_path),
                    "worktree": state.get("reviewer_worktree") or str(worktree),
                    "qualification_mode": "live",
                }
        run_root = run_root_for(repo, state["workflow_id"], campaign_id)
        runner_state = run_root / "state.json"
        saved = None
        if runner_state.is_file():
            try:
                saved = json.loads(runner_state.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                saved = None
        if saved and saved.get("status") == "complete":
            # A completed session can only be reused through receipt recovery;
            # leave its report in place so the operator can inspect the failure.
            recovered = recover_completed_reviewer_receipt(
                repo,
                run_root=run_root,
                report_path=out,
                invocation_path=invocation_path,
                campaign_id=campaign_id,
                candidate_commit=state["candidate_commit"],
                oracle_digest=state["oracle_manifest_sha256"],
                worktree=worktree,
            )
            if recovered:
                return recovered
            raise RuntimeError(
                "completed reviewer session cannot be recovered: "
                f"no valid receipt/invocation for {out}"
            )
        # Unverified pre-existing report must not become valid later.
        if out.is_file():
            quarantine_report(out)
        if out.exists():
            raise RuntimeError(f"reviewer report path not cleared before launch: {out}")
        active = bool(saved) and bool(saved.get("session_id"))

        wt = ensure_reviewer_worktree(
            repo,
            workflow_id=state["workflow_id"],
            round_id=state["round"],
            candidate_commit=state["candidate_commit"],
            active_session=active,
            existing_path=state.get("reviewer_worktree"),
        )
        camp_path = materialize_reviewer_campaign(
            repo,
            state,
            campaign_path=campaign_path,
            worktree=wt,
            report_path=out,
            pin=pin,
        )
        campaign = load_campaign(camp_path)
        agent = OpenCodeAgent(
            os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
            model=campaign.model,
        )
        os.environ["EU4_REVIEW_REPORT_PATH"] = str(out)
        os.environ["EU4_REVIEW_EXPECTED_CANDIDATE"] = state["candidate_commit"]
        os.environ["EU4_REVIEW_EXPECTED_ORACLE"] = state["oracle_manifest_sha256"]
        write_reviewer_invocation(
            invocation_path,
            campaign_id=campaign_id,
            candidate_commit=state["candidate_commit"],
            oracle_digest=state["oracle_manifest_sha256"],
            report_path=out,
            worktree=wt,
        )
        runner = Runner(campaign, agent, run_root)
        result = runner.execute(resume_only=runner_state.is_file())
        if result.get("status") != "complete" or result.get("last_verdict") != "pass":
            raise RuntimeError(
                f"reviewer campaign did not complete: {result.get('pause_reason')}"
            )
        if result.get("verification_manifest_sha256") != state["oracle_manifest_sha256"]:
            raise RuntimeError("reviewer oracle pin mismatch")
        if not out.is_file():
            raise RuntimeError("reviewer completed without writing out-of-tree report")
        session_id = result.get("session_id")
        if not session_id:
            raise RuntimeError("reviewer completed without session_id")
        write_reviewer_receipt(
            receipt_path,
            campaign_id=campaign_id,
            candidate_commit=state["candidate_commit"],
            oracle_digest=state["oracle_manifest_sha256"],
            reviewer_session_id=session_id,
            runner_status="complete",
            report_sha256=file_sha256(out),
            qualification_mode="live",
        )
        return {
            "path": out,
            "session_id": session_id,
            "worktree": str(wt),
            "receipt_path": str(receipt_path),
            "qualification_mode": "live",
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
    resumed = workflow_path.is_file()
    state = controller.load_or_init(load_campaign(campaign_path).id, pin)
    state["require_live_sessions"] = True
    legacy = legacy_workflow_layout(repo, state, reports) if resumed else None
    if legacy is not None:
        state["state"] = "blocked"
        state["block_reason"] = (
            f"legacy_workflow_layout: reusable state at {legacy}; "
            "start a new workflow or migrate it explicitly"
        )
        save_workflow(workflow_path, state)
        print(json.dumps(state, indent=2, default=str)[:8000])
        return 1
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
