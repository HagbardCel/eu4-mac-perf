"""E1 certificate promotion rules and schema-valid stand-ins for negative controls."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from hashlib import sha256

CERTIFICATE_SCHEMA = "eu4-oracle-tier1-certificate/v1"
FROZEN_CANDIDATE_KEYS = frozenset({
    "source_sha256",
    "executable_sha256",
    "build_identity",
    "build_flags",
    "executed_matches_build",
})


def assert_certificate_shape(cert: Mapping[str, Any]) -> None:
    if cert.get("schema") != CERTIFICATE_SCHEMA:
        raise AssertionError(f"schema is not {CERTIFICATE_SCHEMA!r}")
    for block in ("evidence", "reference_admission", "candidate", "evaluation",
                  "coverage", "promotion"):
        if block not in cert:
            raise AssertionError(f"missing block: {block}")
    candidate = cert["candidate"]
    missing = FROZEN_CANDIDATE_KEYS - set(candidate)
    if missing:
        raise AssertionError(f"candidate missing frozen keys: {sorted(missing)}")


def assert_build_binding_honest(
        cert: Mapping[str, Any],
        executable_path: Path,
        *,
        expected_source_sha256: str | None = None) -> None:
    """Certificate candidate identities must match supplied artifact bytes (consistency only)."""
    assert_certificate_shape(cert)
    candidate = cert["candidate"]
    if not executable_path.is_file():
        raise AssertionError("executable path does not exist")
    on_disk = sha256(executable_path.read_bytes()).hexdigest()
    if on_disk != candidate["executable_sha256"]:
        raise AssertionError(
            "executable_sha256 does not match the evaluated executable bytes")
    if expected_source_sha256 is not None:
        if candidate["source_sha256"] != expected_source_sha256:
            raise AssertionError(
                "source_sha256 does not match the canonical RE1 source digest")


def assert_promotion(cert: Mapping[str, Any], expected: str) -> None:
    assert_certificate_shape(cert)
    status = cert["promotion"]["status"]
    if status != expected:
        raise AssertionError(
            f"promotion.status expected {expected!r}, got {status!r}: "
            f"{cert['promotion'].get('reasons')}")


def assert_evidence_not_live_grounded(cert: Mapping[str, Any]) -> None:
    tier = cert.get("evidence_tier") or cert.get("evidence", {}).get("tier")
    if tier == "tier1-live-sealed-session":
        raise AssertionError("synthetic evidence cannot claim tier1-live-sealed-session")
    if cert["promotion"]["status"] == "certified" and cert["evidence"].get(
            "explicitly_synthetic"):
        raise AssertionError("explicitly synthetic evidence cannot be certified")


def assert_rejects_false_certification(cert: Mapping[str, Any]) -> None:
    assert_certificate_shape(cert)
    if cert["promotion"]["status"] == "certified":
        raise AssertionError(
            "stand-in incorrectly certified: "
            f"{cert['promotion'].get('reasons')}")


def _diversity_floor_met(cert: Mapping[str, Any], min_distinct_behaviors: int) -> bool:
    return int(cert["coverage"]["distinct_behaviors"]) >= min_distinct_behaviors


# Acceptance convention for binding-failure integration tests (not a schema extension).
_BINDING_FAILURE_PHRASES = (
    "executable_sha256",
    "executable digest",
    "source_sha256",
    "executed_matches_build",
    "build binding",
    "candidate binding",
    "identity mismatch",
)
_BUILD_IDENTITY_PHRASES = (
    "build_identity",
    "build identity",
    "trusted build",
    "production build",
)


def _failure_diagnostics(cert: Mapping[str, Any]) -> str:
    parts = list(cert["promotion"].get("reasons", []))
    parts.extend(str(item) for item in cert["evaluation"].get("failures", []))
    return " ".join(parts).lower()


def _has_binding_diagnostic(diagnostics: str) -> bool:
    return any(phrase in diagnostics for phrase in _BINDING_FAILURE_PHRASES)


def _assert_reference_admission_eligible(cert: Mapping[str, Any]) -> None:
    admission = cert["reference_admission"]
    if admission["session"]["verdict"] != "eligible":
        raise AssertionError(
            f"session admission must be eligible, got {admission['session']}")
    fixtures = admission.get("fixtures", {})
    if fixtures.get("verdict") != "eligible":
        raise AssertionError(
            f"fixture admission must be eligible, got {fixtures}")


def assert_promotion_certified(
        cert: Mapping[str, Any], *, min_distinct_behaviors: int) -> None:
    assert_certificate_shape(cert)
    _assert_reference_admission_eligible(cert)
    assert_promotion(cert, "certified")
    assert_evaluation_counts(cert, match=50, mismatch=0)
    coverage = cert["coverage"]
    if coverage.get("missing_required"):
        raise AssertionError(
            f"certified promotion requires no missing required cells: "
            f"{coverage['missing_required']}")
    if not _diversity_floor_met(cert, min_distinct_behaviors):
        raise AssertionError(
            "certified promotion requires distinct_behaviors >= "
            f"{min_distinct_behaviors}, got {coverage['distinct_behaviors']}")


def assert_promotion_scoped_for_diversity(
        cert: Mapping[str, Any], *, min_distinct_behaviors: int) -> None:
    assert_certificate_shape(cert)
    _assert_reference_admission_eligible(cert)
    assert_promotion(cert, "scoped")
    assert_evaluation_counts(cert, match=50, mismatch=0)
    coverage = cert["coverage"]
    if coverage.get("missing_required"):
        raise AssertionError(
            "scoped diversity test requires required cells satisfied, got "
            f"missing {coverage['missing_required']}")
    if _diversity_floor_met(cert, min_distinct_behaviors):
        raise AssertionError(
            "scoped promotion expected diversity floor not met")


def assert_build_identity_binding_failure(
        cert: Mapping[str, Any],
        *,
        submitted_forged_identity: str) -> None:
    """E1 must fail with a build-identity diagnostic (not the forged-digest helper)."""
    if not submitted_forged_identity:
        raise AssertionError("forged build_identity must be non-empty")
    assert_certificate_shape(cert)
    _assert_reference_admission_eligible(cert)
    assert_promotion(cert, "failed")
    diagnostics = _failure_diagnostics(cert)
    if not any(phrase in diagnostics for phrase in _BUILD_IDENTITY_PHRASES):
        raise AssertionError(
            "promotion failure lacks a build-identity diagnostic in reasons/failures")
    recorded = cert["candidate"]["build_identity"]
    if recorded == submitted_forged_identity:
        raise AssertionError(
            "certificate must not echo the forged build_identity as trusted")


def assert_candidate_binding_failure(
        cert: Mapping[str, Any],
        *,
        submitted_forged_digest: str,
        executable_path: Path) -> None:
    """E1 must fail with a binding diagnostic when the submitted digest is forged."""
    if not executable_path.is_file():
        raise AssertionError("executable path does not exist")
    actual_digest = sha256(executable_path.read_bytes()).hexdigest()
    if submitted_forged_digest == actual_digest:
        raise AssertionError(
            "binding negative fixture must submit a digest that differs from the executable")
    assert_certificate_shape(cert)
    _assert_reference_admission_eligible(cert)
    assert_promotion(cert, "failed")
    diagnostics = _failure_diagnostics(cert)
    if not _has_binding_diagnostic(diagnostics):
        raise AssertionError(
            "promotion failure lacks a binding-specific diagnostic in reasons/failures")
    recorded = cert["candidate"]["executable_sha256"]
    if recorded == submitted_forged_digest and cert["candidate"]["executed_matches_build"]:
        raise AssertionError(
            "certificate must not claim executed_matches_build for a forged digest")


def assert_evaluation_counts(cert: Mapping[str, Any], *,
                             match: int, mismatch: int,
                             infrastructure_refusal: int = 0,
                             evidence_refusal: int = 0) -> None:
    ev = cert["evaluation"]
    if ev["match"] != match:
        raise AssertionError(f"match count expected {match}, got {ev['match']}")
    if ev["mismatch"] != mismatch:
        raise AssertionError(f"mismatch count expected {mismatch}, got {ev['mismatch']}")
    if ev["infrastructure_refusal"] != infrastructure_refusal:
        raise AssertionError("unexpected infrastructure_refusal count")
    if ev["evidence_refusal"] != evidence_refusal:
        raise AssertionError("unexpected evidence_refusal count")


def _base_certificate(**overrides: Any) -> dict[str, Any]:
    cert: dict[str, Any] = {
        "schema": CERTIFICATE_SCHEMA,
        "function_id": "gfx:set-constant-buffer",
        "evidence_tier": "mocked-structural",
        "evidence": {
            "session_id": "gfx-e1-acceptance",
            "manifest_sha256": "a" * 64,
            "descriptor_sha256": "b" * 64,
            "policy_sha256": "c" * 64,
            "transcript_count": 50,
            "eligible": 50,
            "ineligible": 0,
            "malformed": 0,
            "loss": {},
            "mocked": True,
            "explicitly_synthetic": False,
        },
        "reference_admission": {
            "session": {"verdict": "eligible", "reasons": []},
            "fixtures": {"verdict": "eligible", "reasons": []},
        },
        "candidate": {
            "source_sha256": "d" * 64,
            "executable_sha256": "e" * 64,
            "build_identity": "acceptance-test",
            "build_flags": ["-std=c++20"],
            "executed_matches_build": True,
        },
        "evaluation": {
            "match": 50,
            "mismatch": 0,
            "infrastructure_refusal": 0,
            "evidence_refusal": 0,
            "failures": [],
        },
        "coverage": {
            "policy_sha256": "c" * 64,
            "required_cells": [],
            "observed_required": [],
            "missing_required": [],
            "distinct_behaviors": 10,
            "diversity_floor": 2,
            "diversity_met": True,
        },
        "promotion": {"status": "certified", "reasons": []},
        "limitations": ["mocked structural session; no live gate"],
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in cert and isinstance(cert[key], dict):
            cert[key] = {**cert[key], **value}
        else:
            cert[key] = value
    return cert


def false_certifier_stand_in(**_kwargs: Any) -> dict[str, Any]:
    return _base_certificate(
        evaluation={
            "match": 49,
            "mismatch": 1,
            "infrastructure_refusal": 0,
            "evidence_refusal": 0,
            "failures": ["eligible mismatch on fixture 7"],
        },
        promotion={"status": "certified", "reasons": []},
    )


def forged_executable_binding_stand_in(**_kwargs: Any) -> dict[str, Any]:
    """executed_matches_build true but executable_sha256 does not match disk."""
    cert = _base_certificate(promotion={"status": "certified", "reasons": []})
    cert["candidate"]["executable_sha256"] = "0" * 64
    return cert


def unrelated_failure_stand_in(**_kwargs: Any) -> dict[str, Any]:
    """Eligible admission with a failed promotion unrelated to candidate binding."""
    return _base_certificate(
        promotion={
            "status": "failed",
            "reasons": ["required coverage cells missing"],
        },
    )
