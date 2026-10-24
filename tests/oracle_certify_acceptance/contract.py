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


def assert_build_binding_honest(cert: Mapping[str, Any],
                                executable_path: Path) -> None:
    """Frozen candidate fields must match the executable E1 actually evaluated."""
    assert_certificate_shape(cert)
    candidate = cert["candidate"]
    if not executable_path.is_file():
        raise AssertionError("executable path does not exist")
    on_disk = sha256(executable_path.read_bytes()).hexdigest()
    if on_disk != candidate["executable_sha256"]:
        raise AssertionError(
            "executable_sha256 does not match the evaluated executable bytes")
    if candidate["executed_matches_build"] and on_disk != candidate[
            "executable_sha256"]:
        raise AssertionError("executed_matches_build is inconsistent with bytes")


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
