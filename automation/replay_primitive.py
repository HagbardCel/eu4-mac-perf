"""Allowlisted primitive-lookup replay adapter (trusted; not reviewer-supplied)."""

from __future__ import annotations

import json
from pathlib import Path

from automation.verification_manifest import load_manifest

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
    if obs not in {"out_of_range_default", "engine_enum_4_gl_mode", "table"}:
        errors.append(f"unsupported observation: {obs}")
    ref = binding.get("evidence_ref")
    if not isinstance(ref, dict) or ref.get("path") is None:
        errors.append("binding.evidence_ref.path required")
    else:
        from automation.verification_manifest import file_sha256

        expected_hash = ref.get("sha256")
        actual = file_sha256(evidence_path)
        if expected_hash != actual:
            errors.append("evidence_ref.sha256 does not match frozen evidence bytes")
    expected = binding.get("expected")
    output = evidence.get("output") or {}
    if obs == "out_of_range_default":
        truth = (output.get("mapping") or {}).get("default_out_of_range")
        if expected != truth:
            errors.append(
                "binding.expected does not match evidence mapping.default_out_of_range"
            )
    elif obs == "engine_enum_4_gl_mode":
        if expected != output.get("engine_enum_4_gl_mode"):
            errors.append(
                "binding.expected does not match evidence engine_enum_4_gl_mode"
            )
    elif obs == "table":
        if expected != output.get("table"):
            errors.append("binding.expected does not match evidence table")
    return errors


def resolve_regression_root(oracle_dir: Path, manifest: dict | None = None) -> Path | None:
    """Regression directory from verified manifest — never from CURRENT alone."""
    if manifest is None:
        tip = oracle_dir / "verification-manifest.json"
        if not tip.is_file():
            return None
        manifest = load_manifest(tip)
    version = manifest.get("oracle_version_name")
    if not isinstance(version, str) or not version:
        return None
    # Prefer explicit dir from manifest
    explicit = manifest.get("oracle_version_dir")
    if isinstance(explicit, str) and explicit:
        root = Path(explicit)
        if not root.is_absolute():
            # relative to oracle_dir parent chain: treat as repo-relative via oracle parent
            candidate = oracle_dir.parent.parent.parent / explicit if False else None
            # Prefer oracle_dir / relative if under oracle_versions
            under = oracle_dir / "oracle_versions" / version
            if under.is_dir():
                reg = under / "regressions"
                return reg if reg.is_dir() else None
            root = oracle_dir / explicit
        reg = root / "regressions" if root.name != "regressions" else root
        if reg.is_dir():
            return reg
    reg_root = oracle_dir / "oracle_versions" / version / "regressions"
    if reg_root.is_dir():
        return reg_root
    return None


def evaluate_promoted_regressions(
    repo: Path,
    oracle_dir: Path,
    candidate: dict,
    evidence: dict,
    *,
    manifest: dict | None = None,
) -> str | None:
    """Run allowlisted promoted regressions against the workspace candidate."""
    if manifest is None:
        tip = oracle_dir / "verification-manifest.json"
        if tip.is_file():
            manifest = load_manifest(tip)
    reg_root = resolve_regression_root(oracle_dir, manifest)
    if reg_root is None:
        # v0 with no regressions is fine
        if manifest and manifest.get("oracle_version_name") in (None, "v0"):
            return None
        if manifest and any(
            m.get("role") == "regression" for m in (manifest.get("members") or [])
        ):
            return "unavailable: manifest lists regressions but version directory missing"
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
