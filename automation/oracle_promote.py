"""Transactional, evidence-bound, append-only promotion of reviewer counterexamples."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from automation.replay_primitive import (
    ALLOWLISTED_REPLAYS,
    binding_matches_primitive_evidence,
    mutant_fails_out_of_range,
)
from automation.review_result import validate_review
from automation.verification_manifest import (
    build_manifest,
    canonical_json,
    digest_manifest,
    digest_text,
    file_sha256,
    load_manifest,
    member_from_file,
    save_manifest,
    verify_manifest,
)


class PromotionDecision:
    PROMOTE = "promote"
    REJECT_ONLY = "reject_only"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


def finding_content_digest(finding: dict, *, review_digest: str) -> str:
    """Stable digest of a qualified finding bound to the source review."""
    binding = finding.get("binding") or {}
    payload = {
        "id": finding.get("id"),
        "kind": finding.get("kind"),
        "expected_provenance": finding.get("expected_provenance"),
        "reproducer": finding.get("reproducer"),
        "binding": {
            "replay_id": binding.get("replay_id"),
            "observation": binding.get("observation"),
            "expected": binding.get("expected"),
            "function_rva": binding.get("function_rva"),
            "executable_sha256": binding.get("executable_sha256"),
            "evidence_ref": binding.get("evidence_ref"),
            "sequence": binding.get("sequence"),
        },
        "review_digest": review_digest,
    }
    return digest_text(canonical_json(payload))


def classify_finding(finding: dict, evidence: dict, evidence_path: Path) -> str:
    if finding.get("kind") != "behavioral_counterexample":
        return PromotionDecision.REJECT_ONLY
    if finding.get("expected_provenance") == "unestablished":
        return PromotionDecision.REJECT_ONLY
    binding = finding.get("binding")
    if not isinstance(binding, dict):
        return PromotionDecision.REJECT_ONLY
    repro = finding.get("reproducer")
    if not repro:
        return PromotionDecision.UNRESOLVED
    errors = binding_matches_primitive_evidence(binding, evidence, evidence_path)
    if errors:
        if any("does not match evidence" in e and "expected" in e for e in errors):
            return PromotionDecision.CONFLICT
        return PromotionDecision.REJECT_ONLY
    if binding.get("replay_id") not in ALLOWLISTED_REPLAYS:
        return PromotionDecision.REJECT_ONLY
    return PromotionDecision.PROMOTE


def _active_manifest(oracle_dir: Path) -> dict | None:
    path = oracle_dir / "verification-manifest.json"
    if not path.is_file():
        return None
    return load_manifest(path)


def _version_dir(oracle_dir: Path, version_name: str) -> Path:
    return oracle_dir / "oracle_versions" / version_name


def _load_regression_index(oracle_dir: Path, manifest: dict | None) -> dict[str, dict]:
    """Map finding id -> regression payload; also index by finding_content_digest."""
    by_id: dict[str, dict] = {}
    if not manifest:
        return by_id
    version = manifest.get("oracle_version_name")
    if not isinstance(version, str) or not version:
        return by_id
    reg_root = _version_dir(oracle_dir, version) / "regressions"
    if not reg_root.is_dir():
        return by_id
    for path in sorted(reg_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        fid = payload.get("id")
        if isinstance(fid, str):
            by_id[fid] = payload
    return by_id


def _ancestry_digests(oracle_dir: Path, manifest: dict | None) -> dict[str, str]:
    """finding_content_digest -> version that carries it (active + walk parent)."""
    found: dict[str, str] = {}
    current = manifest
    seen: set[str] = set()
    while current:
        version = current.get("oracle_version_name")
        if not isinstance(version, str) or version in seen:
            break
        seen.add(version)
        reg_root = _version_dir(oracle_dir, version) / "regressions"
        if reg_root.is_dir():
            for path in reg_root.glob("*.json"):
                payload = json.loads(path.read_text(encoding="utf-8"))
                digest = payload.get("finding_content_digest")
                if isinstance(digest, str):
                    found.setdefault(digest, version)
                fid = payload.get("id")
                # Also key collisions by id for content mismatch detection
                if isinstance(fid, str):
                    found.setdefault(f"id:{fid}", version)
        parent = current.get("parent_manifest_sha256")
        # Walk only via parent version name stored on disk if present
        parent_name = current.get("parent_oracle_version_name")
        if isinstance(parent_name, str) and parent_name:
            parent_manifest_path = (
                _version_dir(oracle_dir, parent_name) / "verification-manifest.json"
            )
            if parent_manifest_path.is_file():
                current = load_manifest(parent_manifest_path)
                continue
        break
    return found


def _repair_current(oracle_dir: Path, version_name: str) -> None:
    current = oracle_dir / "CURRENT"
    tmp = oracle_dir / "CURRENT.tmp"
    tmp.write_text(version_name + "\n", encoding="utf-8")
    os.replace(tmp, current)


def _ensure_version_local_manifest(
    repo: Path, oracle_dir: Path, tip_manifest: dict
) -> None:
    """Recreate a missing version-local pin from a validated active tip only."""
    version = tip_manifest.get("oracle_version_name")
    if not isinstance(version, str) or not version:
        return
    published = _version_dir(oracle_dir, version)
    pin = published / "verification-manifest.json"
    if pin.is_file():
        return
    active = _active_manifest(oracle_dir)
    if not active or active.get("oracle_version_name") != version:
        raise RuntimeError(
            f"cannot recover version-local manifest for {version}: active tip mismatch"
        )
    if digest_manifest(active) != digest_manifest(tip_manifest):
        raise RuntimeError(
            f"cannot recover version-local manifest for {version}: tip digest drift"
        )
    errs = verify_manifest(repo, active)
    if errs:
        raise RuntimeError(
            f"cannot recover version-local manifest for {version}: "
            + "; ".join(errs)
        )
    tip_path = oracle_dir / "verification-manifest.json"
    tip_text = tip_path.read_text(encoding="utf-8")
    published.mkdir(parents=True, exist_ok=True)
    tmp = published / "verification-manifest.json.tmp"
    tmp.write_text(tip_text, encoding="utf-8")
    os.replace(tmp, pin)


def _version_id(parent_manifest_sha256: str, new_payloads: list[dict]) -> str:
    blob = canonical_json(
        {
            "parent": parent_manifest_sha256,
            "findings": sorted(new_payloads, key=lambda p: p["id"]),
        }
    )
    return "v_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def promote_grounded_findings(
    *,
    repo: Path,
    review: dict,
    oracle_dir: Path,
    trusted_helpers: list[str],
    checker_relpath: str,
    review_path: Path | None = None,
) -> dict:
    """Append-only promote. Publication commit point = active manifest replace."""
    errors = validate_review(review)
    if errors:
        raise ValueError("review invalid: " + "; ".join(errors))
    if review.get("outcome") != "rejected":
        return {"promoted": False, "reason": "outcome_not_rejected"}

    evidence_path = oracle_dir / "primitive-table.evidence.json"
    if not evidence_path.is_file():
        raise FileNotFoundError("frozen primitive evidence missing")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    review_digest = (
        file_sha256(review_path)
        if review_path and review_path.is_file()
        else digest_text(canonical_json(review))
    )

    decisions = []
    to_promote = []
    for finding in review.get("findings") or []:
        decision = classify_finding(finding, evidence, evidence_path)
        decisions.append({"id": finding.get("id"), "decision": decision})
        if decision == PromotionDecision.PROMOTE:
            to_promote.append(finding)

    if not to_promote:
        return {
            "promoted": False,
            "decisions": decisions,
            "reason": "no_promotable_findings",
        }

    if not mutant_fails_out_of_range(evidence):
        raise RuntimeError("allowlisted replay failed mutant detection gate")

    parent_manifest = _active_manifest(oracle_dir)
    parent_sha = digest_manifest(parent_manifest) if parent_manifest else "none"
    parent_version = (
        parent_manifest.get("oracle_version_name") if parent_manifest else None
    )
    ancestry = _ancestry_digests(oracle_dir, parent_manifest)
    active_regs = _load_regression_index(oracle_dir, parent_manifest)

    new_payloads: list[dict] = []
    already: list[str] = []
    for finding in to_promote:
        digest = finding_content_digest(finding, review_digest=review_digest)
        fid = finding["id"]
        if digest in ancestry:
            already.append(digest)
            continue
        if f"id:{fid}" in ancestry or fid in active_regs:
            existing = active_regs.get(fid) or {}
            prior = existing.get("finding_content_digest")
            if prior and prior != digest:
                raise ValueError(
                    f"finding id collision for {fid}: content differs from active oracle"
                )
            already.append(digest)
            continue
        binding = finding["binding"]
        payload = {
            "schema": "eu4-promoted-regression/v1",
            "id": fid,
            "finding_content_digest": digest,
            "review_digest": review_digest,
            "replay_id": binding["replay_id"],
            "observation": binding["observation"],
            "expected": binding["expected"],
            "evidence_ref": binding["evidence_ref"],
            "function_rva": binding["function_rva"],
            "executable_sha256": binding["executable_sha256"],
            "sequence": binding.get("sequence") or ["load_candidate", "compare_obs"],
            "promoted_from_review_candidate": review.get("candidate_commit"),
        }
        new_payloads.append(payload)

    if not new_payloads:
        # All findings already applied — reuse active version
        version = parent_version or "v0"
        if parent_manifest:
            _repair_current(oracle_dir, version)
            _ensure_version_local_manifest(repo, oracle_dir, parent_manifest)
        return {
            "promoted": True,
            "oracle_version": version,
            "decisions": decisions,
            "reused": True,
            "manifest_sha256": parent_sha if parent_manifest else "",
            "already_applied": already,
            "version_manifest_path": (
                parent_manifest.get("version_manifest_path")
                if parent_manifest
                else None
            ),
        }

    versions = oracle_dir / "oracle_versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_name = _version_id(parent_sha, new_payloads)
    published = versions / version_name
    staging = versions / f".staging-{version_name}"

    # Recovery: unpublished complete version already on disk
    if published.is_dir() and (published / "regressions").is_dir():
        tip = _active_manifest(oracle_dir)
        if tip and tip.get("oracle_version_name") == version_name:
            _repair_current(oracle_dir, version_name)
            _ensure_version_local_manifest(repo, oracle_dir, tip)
            return {
                "promoted": True,
                "oracle_version": version_name,
                "decisions": decisions,
                "reused": True,
                "manifest_sha256": digest_manifest(tip),
                "version_manifest_path": tip.get("version_manifest_path"),
            }

    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    reg_dir = staging / "regressions"
    reg_dir.mkdir()

    # Copy prior regressions (append-only ancestry materialization)
    if parent_version:
        prior_root = _version_dir(oracle_dir, parent_version) / "regressions"
        if prior_root.is_dir():
            for path in prior_root.glob("*.json"):
                shutil.copy2(path, reg_dir / path.name)

    for payload in new_payloads:
        (reg_dir / f"{payload['id']}.json").write_text(
            canonical_json(payload), encoding="utf-8"
        )

    shutil.copy2(evidence_path, staging / "primitive-table.evidence.json")

    # 1→2: rename staging to final content-addressed path
    if published.exists():
        shutil.rmtree(published)
    staging.rename(published)

    # 3: build manifest against final published paths only
    members = []
    for rel in trusted_helpers:
        members.append(member_from_file(repo, rel, "trusted_helper"))
    members.append(member_from_file(repo, checker_relpath, "checker"))
    members.append(
        member_from_file(
            repo,
            str((oracle_dir / "primitive-table.evidence.json").relative_to(repo))
            if (oracle_dir / "primitive-table.evidence.json").is_relative_to(repo)
            else str(oracle_dir / "primitive-table.evidence.json"),
            "frozen_oracle",
        )
    )
    if "automation/replay_primitive.py" not in trusted_helpers:
        members.append(
            member_from_file(repo, "automation/replay_primitive.py", "trusted_helper")
        )
    for path in sorted((published / "regressions").glob("*.json")):
        rel = (
            str(path.relative_to(repo))
            if path.is_relative_to(repo)
            else str(path)
        )
        members.append(member_from_file(repo, rel, "regression"))

    version_rel = (
        str(published.relative_to(repo))
        if published.is_relative_to(repo)
        else str(published)
    )
    version_manifest_rel = f"{version_rel}/verification-manifest.json"
    manifest = build_manifest(
        members=members,
        extra={
            "oracle_version_name": version_name,
            "oracle_version_dir": version_rel,
            "version_manifest_path": version_manifest_rel,
            "promoted_from_review_candidate": review.get("candidate_commit"),
            "parent_manifest_sha256": parent_sha if parent_manifest else None,
            "parent_oracle_version_name": parent_version,
        },
    )
    errs = verify_manifest(repo, manifest)
    if errs:
        raise RuntimeError("promoted manifest invalid: " + "; ".join(errs))

    # 4: immutable per-version pin BEFORE tip activation
    text = canonical_json(manifest)
    version_manifest_path = published / "verification-manifest.json"
    version_tmp = published / "verification-manifest.json.tmp"
    version_tmp.write_text(text, encoding="utf-8")
    os.replace(version_tmp, version_manifest_path)

    # 5: atomic tip publish (activation / commit point)
    tip = oracle_dir / "verification-manifest.json"
    tmp = oracle_dir / "verification-manifest.json.tmp"
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, tip)

    # 6: convenience CURRENT mirror
    _repair_current(oracle_dir, version_name)

    return {
        "promoted": True,
        "oracle_version": version_name,
        "decisions": decisions,
        "reused": False,
        "manifest_sha256": digest_text(text),
        "version_manifest_path": version_manifest_rel,
    }
