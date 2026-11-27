"""Operator-owned policy and receipts for offline candidate qualification."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from automation import (
    case_locator, dsh_receipts, gfx_ed25519, gfx_frame_compare,
    gfx_gl_control_qualification, gfx_prefix_check, m5_replay, state,
)
from automation.state import outside_workspace as _outside
from automation.gfx_artifacts import sha256_bytes
from reverse_engineering import reconstruction_diff

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = dsh_receipts.REVISION_SCHEMA
POLICY = "EU4_QUALIFICATION_POLICY"
POLICY_DIGEST = "EU4_QUALIFICATION_POLICY_SHA256"
POLICY_SCHEMA = "eu4-operator-qualification/v1"
POLICY_CONTRACT = Path(__file__).with_name("qualification-policy-v1.json")


class QualificationError(RuntimeError):
    pass


def _policy(workspace: Path, commit: str) -> tuple[dict, str]:
    if not re.fullmatch("[0-9a-f]{40}", commit):
        raise QualificationError("a full candidate commit is required")
    if not (path := os.environ.get(POLICY)):
        raise QualificationError("operator qualification policy is unconfigured")
    raw = _outside(Path(path), workspace).read_bytes()
    digest = sha256_bytes(raw)
    if digest != os.environ.get(POLICY_DIGEST):
        raise QualificationError("operator qualification policy digest differs")
    policy = json.loads(raw)
    if not isinstance(policy, dict) or policy.get("schema") != POLICY_SCHEMA:
        raise QualificationError("unsupported qualification policy")
    _outside(ROOT, workspace)
    trusted = state.trusted_git(ROOT, "rev-parse", "HEAD").strip()
    if trusted != policy.get("trusted_revision") or state.trusted_git(
            ROOT, "status", "--porcelain", "--untracked-files=all"):
        raise QualificationError("trusted validator checkout differs from its approved revision")
    state.require_worktree(workspace, ROOT, commit)
    return policy, digest


def _passes(case: dict, row: dict) -> bool:
    required_coverage = set(case.get("minimum_coverage", []))
    if not required_coverage.issubset(row.get("coverage", [])):
        return False
    if row["verdict"] == "exact":
        return row.get("execution") == "completed" and row.get("independently_verified") is True
    if case["category"] != "diagnostic":
        return False
    if row["verdict"] == "different":
        return bool(required_coverage and row.get("execution") == "completed"
                    and row.get("independently_verified") is True)
    return (row["verdict"] in ("missing_original_observation", "inapplicable_reference")
            and row.get("original_evidence_verified") is True
            and bool(row.get("problems"))
            and set(row["problems"]).issubset(case.get("original_limitations", [])))


def qualify(workspace: Path | str, commit: str, case_id: str | None = None) -> dict:
    workspace = Path(workspace).expanduser().resolve(strict=True)
    policy, policy_digest = _policy(workspace, commit)
    if not (policy.get("receipt_signing_key") and policy.get("receipt_public_key")):
        raise QualificationError("operator policy does not name the receipt signing identity")
    seed_path = _outside(Path(policy["receipt_signing_key"]), workspace)
    info = seed_path.stat()
    if not seed_path.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise QualificationError("receipt signing key must be a private operator file")
    seed = bytes.fromhex(seed_path.read_text().strip())
    public_key = bytes.fromhex(policy["receipt_public_key"])
    if gfx_ed25519.derive_public_key(seed) != public_key:
        raise QualificationError("receipt signing key does not match the approved public key")
    contract = json.loads(POLICY_CONTRACT.read_text())
    if contract["schema"] != POLICY_SCHEMA:
        raise QualificationError("operator policy contract version differs")
    required = list(contract["cases"])
    authentic = contract["authentic_case"]
    evidence = policy.get("evidence", {})
    if set(evidence) - set(required) or {"cases", "authentic_case"}.intersection(policy):
        raise QualificationError("deployment cannot change protected case definitions")
    cases = {}
    for name, rule in contract["cases"].items():
        deployment = evidence.get(name, {})
        committed = rule.get("reference", {})
        if any(key in deployment and deployment[key] != value for key, value in committed.items()):
            raise QualificationError(f"committed reference identity differs for {name}")
        allowed = {"path", "package", "expected", "architecture", "gl", "reuse_receipt"}
        if name == authentic:
            allowed |= {"key", "manifest_sha256", "seal_sha256"}
        if set(deployment) - allowed - committed.keys():
            raise QualificationError(f"unexpected deployment settings for {name}")
        cases[name] = {**rule, "reference": {**committed, **deployment}}
    if case_id is not None and case_id not in cases:
        raise QualificationError("case id is absent from the protected policy")
    selected = [case_id] if case_id is not None else required
    run_root = _outside(Path(policy["run_root"]), workspace)
    run_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = Path(tempfile.mkdtemp(prefix="qualification-", dir=run_root))
    tree = state.trusted_git(ROOT, "rev-parse", f"{commit}^{{tree}}").strip()
    results = []
    for index, key in enumerate(selected):
        case = cases[key]
        job = directory / f"case-{index}"
        job.mkdir()
        try:
            if case["kind"] == "constant-buffer":
                previous = None
                if case["reference"].get("reuse_receipt"):
                    previous = run_root / case["reference"]["reuse_receipt"]
                    if not previous.resolve().is_relative_to(run_root):
                        raise QualificationError("execution receipt escapes the protected run root")
                row = reconstruction_diff.qualify_candidate(
                    ROOT, commit, job, case["reference"], previous=previous, public_key=public_key)
            elif case["kind"] == "synthetic-gl":
                reference = case["reference"]
                row = gfx_gl_control_qualification.qualify_candidate(ROOT, commit, job, _outside(Path(reference["package"]), workspace),
                                   _outside(Path(reference["expected"]), workspace), reference)
            elif case["kind"] in ("gl", "metal-prefix"):
                source, row = case_locator.resolve_sealed_reference(
                    policy.get("evidence_roots"), case["reference"], workspace)
                if source is not None and case["kind"] == "gl":
                    row = gfx_gl_control_qualification.qualify_published_candidate(
                        ROOT, commit, job, source, case)
                elif source is not None:
                    frame, _, _, trusted = source
                    row = gfx_prefix_check.qualify_candidate(
                        ROOT, policy["trusted_revision"], commit, job, frame, trusted, case)
            elif case["kind"] in ("m5-sprite", "m6-frame", "dual-run-reference"):
                reference = case["reference"]
                original = case_locator.resolve_legacy_reference(
                    policy.get("evidence_roots"), reference, workspace)
                if case["kind"] == "m5-sprite":
                    row = m5_replay.qualify_candidate(commit, job, original, reference)
                elif case["kind"] == "m6-frame":
                    row = gfx_prefix_check.qualify_full_frame(
                        ROOT, policy["trusted_revision"], commit, job, original, reference)
                else:
                    scenario = ROOT / "analysis/infra/scenarios/opengl-save-consistency.json"
                    row = gfx_frame_compare.verify_dual_run_reference(
                        original, reference, json.loads(scenario.read_text())["comparison"])
            else:
                row = {"verdict": "unsupported", "problems": ["case executor is unavailable"]}
        except case_locator.CaseLocatorError as exc:
            row = {"verdict": exc.kind, "problems": [str(exc)]}
        except (KeyError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            row = {"verdict": "execution_failure", "problems": [str(exc)]}
        receipt = row.pop("reused_execution_receipt", None)
        if receipt is None:
            receipt, issued = dsh_receipts.write_receipt(job, {
                "schema": SCHEMA, "nonce": directory.name, "case_id": key,
                "execution_revision": commit, "tree": tree, "policy_sha256": policy_digest,
                "validator_revision": policy["trusted_revision"], **row,
            }, seed=seed)
            receipt_digest = issued["receipt_digest"]
        else:
            row["execution_reused"] = True
            receipt_digest = row.pop("reused_receipt_digest")
        results.append({"case_id": key, "category": case["category"],
                        "receipt": str(receipt), "receipt_digest": receipt_digest,
                        "requirement_satisfied": _passes(case, row), **row})
    authentic_pass = any(
        row["case_id"] == authentic and row["category"] == "exact"
        and cases[authentic]["kind"] == "gl"
        and row.get("evidence_tier") == "original-eu4" and row["verdict"] == "exact"
        and row["requirement_satisfied"] and not row.get("execution_reused")
        for row in results)
    qualified = case_id is None and authentic_pass and all(
        row["requirement_satisfied"] for row in results)
    result = {
        "schema": SCHEMA, "revision": commit, "tree": tree, "policy_sha256": policy_digest,
        "mode": "focused" if case_id is not None else "mandatory",
        "required_cases": required, "executed_cases": [row["case_id"] for row in results
                           if row.get("execution") == "completed" and not row.get("execution_reused")],
        "cases": results, "qualified": qualified,
        "policy_contract_sha256": sha256_bytes(POLICY_CONTRACT.read_bytes()),
    }
    path, _ = dsh_receipts.write_receipt(directory, result, seed=seed)
    if case_id is None:
        dsh_receipts.verify_revision(path, public_key, revision=commit, policy_sha256=policy_digest,
                                     policy_contract_sha256=result["policy_contract_sha256"],
                                     required_cases=required, run_root=run_root)
    return {**result, "receipt": str(path)}
