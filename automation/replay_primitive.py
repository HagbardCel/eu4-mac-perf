"""Allowlisted primitive-lookup replay adapter (trusted; not reviewer-supplied)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REL_CANDIDATE = "analysis/qualification/v0/primitive-lookup.candidate.json"
ALLOWLISTED_REPLAYS = frozenset({"primitive_lookup_v1"})


def evaluate_candidate_against_evidence(
    candidate: dict,
    evidence: dict,
    *,
    check_out_of_range_field: bool,
) -> str | None:
    """Return error string or None if candidate matches evidence for controlled checks."""
    expected_table = (evidence.get("output") or {}).get("table")
    expected_mode = (evidence.get("output") or {}).get("engine_enum_4_gl_mode")
    table = candidate.get("table")
    if not isinstance(table, list) or table != expected_table:
        return (
            "counterexample: candidate.table != frozen instruction_static table; "
            f"expected {expected_table}, got {table}"
        )
    if candidate.get("engine_enum_4_gl_mode") != expected_mode:
        return (
            "counterexample: candidate.engine_enum_4_gl_mode mismatch; "
            f"expected {expected_mode}, got {candidate.get('engine_enum_4_gl_mode')}"
        )
    if check_out_of_range_field:
        expected_default = (
            (evidence.get("output") or {}).get("mapping") or {}
        ).get("default_out_of_range")
        actual = candidate.get("out_of_range_default")
        if actual != expected_default:
            return (
                "counterexample: candidate.out_of_range_default mismatch; "
                f"expected {expected_default}, got {actual}"
            )
    return None


def binding_matches_primitive_evidence(
    binding: dict, evidence: dict, evidence_path: Path
) -> list[str]:
    """Verify reproducer binding refers to the same primitive-table evidence."""
    errors: list[str] = []
    if binding.get("replay_id") not in ALLOWLISTED_REPLAYS:
        errors.append("replay_id must be allowlisted primitive_lookup_v1")
    if binding.get("function_rva") != evidence.get("function_rva"):
        errors.append("binding.function_rva does not match evidence")
    if binding.get("executable_sha256") != evidence.get("executable_sha256"):
        errors.append("binding.executable_sha256 does not match evidence")
    obs = binding.get("observation")
    if obs != "out_of_range_default" and obs != "engine_enum_4_gl_mode" and obs != "table":
        errors.append(f"unsupported observation: {obs}")
    # Evidence file identity
    ref = binding.get("evidence_ref")
    if not isinstance(ref, dict) or ref.get("path") is None:
        errors.append("binding.evidence_ref.path required")
    else:
        from automation.verification_manifest import file_sha256

        expected_hash = ref.get("sha256")
        actual = file_sha256(evidence_path)
        if expected_hash != actual:
            errors.append("evidence_ref.sha256 does not match frozen evidence bytes")
    # Expected Obs must equal the evidence field for the claimed observation
    expected = binding.get("expected")
    output = evidence.get("output") or {}
    if obs == "out_of_range_default":
        truth = (output.get("mapping") or {}).get("default_out_of_range")
        if expected != truth:
            errors.append("binding.expected does not match evidence mapping.default_out_of_range")
    elif obs == "engine_enum_4_gl_mode":
        if expected != output.get("engine_enum_4_gl_mode"):
            errors.append("binding.expected does not match evidence engine_enum_4_gl_mode")
    elif obs == "table":
        if expected != output.get("table"):
            errors.append("binding.expected does not match evidence table")
    return errors


def evaluate_promoted_regressions(
    repo: Path, oracle_dir: Path, candidate: dict, evidence: dict
) -> str | None:
    """Run allowlisted promoted regressions against the workspace candidate."""
    reg_dir = oracle_dir / "oracle_versions"
    # Active version pointer
    current = oracle_dir / "CURRENT"
    if current.is_file():
        version = current.read_text(encoding="utf-8").strip()
        reg_root = oracle_dir / "oracle_versions" / version / "regressions"
    else:
        reg_root = oracle_dir / "regressions"
    if not reg_root.is_dir():
        return None
    for path in sorted(reg_root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        replay = payload.get("replay_id")
        if replay not in ALLOWLISTED_REPLAYS:
            return f"regression {path.name} uses non-allowlisted replay_id"
        obs = payload.get("observation")
        if obs == "out_of_range_default":
            err = evaluate_candidate_against_evidence(
                candidate, evidence, check_out_of_range_field=True
            )
            if err:
                return f"promoted regression {path.name}: {err}"
        elif obs in ("table", "engine_enum_4_gl_mode"):
            err = evaluate_candidate_against_evidence(
                candidate, evidence, check_out_of_range_field=False
            )
            if err:
                return f"promoted regression {path.name}: {err}"
        else:
            return f"regression {path.name} unsupported observation"
    return None


def mutant_fails_out_of_range(evidence: dict) -> bool:
    """Known mutant: out_of_range_default wrong — used in promotion tests."""
    bad = {
        "table": evidence["output"]["table"],
        "engine_enum_4_gl_mode": evidence["output"]["engine_enum_4_gl_mode"],
        "out_of_range_default": 0,
    }
    return (
        evaluate_candidate_against_evidence(
            bad, evidence, check_out_of_range_field=True
        )
        is not None
    )
