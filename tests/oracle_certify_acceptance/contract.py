"""E1 certificate promotion rules and schema-valid stand-ins for negative controls."""
from __future__ import annotations

from typing import Any, Mapping

CERTIFICATE_SCHEMA = "eu4-oracle-tier1-certificate/v1"

# E1 must compile the RE1 tree (or consume an equivalent trusted build record),
# copy the executable into an owned path, and bind evaluation to that copy.
TRUSTED_BUILD_KEYS = frozenset({
    "inputs_sha256",
    "recipe_sha256",
    "owned_executable",
    "owned_executable_sha256",
})


def assert_certificate_shape(cert: Mapping[str, Any]) -> None:
    if cert.get("schema") != CERTIFICATE_SCHEMA:
        raise AssertionError(f"schema is not {CERTIFICATE_SCHEMA!r}")
    for block in ("evidence", "reference_admission", "candidate", "evaluation",
                  "coverage", "promotion"):
        if block not in cert:
            raise AssertionError(f"missing block: {block}")
    candidate = cert["candidate"]
    for key in ("source_sha256", "executable_sha256", "build_identity",
                "build_flags", "executed_matches_build", "trusted_build"):
        if key not in candidate:
            raise AssertionError(f"candidate missing {key!r}")


def assert_trusted_build_required(candidate: Mapping[str, Any]) -> None:
    """Self-consistent digests alone are not trusted source-to-executable binding."""
    trusted = candidate.get("trusted_build")
    if not isinstance(trusted, dict):
        raise AssertionError("candidate.trusted_build must be a dict from E1 build")
    unknown = set(trusted) - TRUSTED_BUILD_KEYS
    if unknown:
        raise AssertionError(f"trusted_build has unknown keys: {sorted(unknown)}")
    missing = TRUSTED_BUILD_KEYS - set(trusted)
    if missing:
        raise AssertionError(f"trusted_build missing keys: {sorted(missing)}")
    if candidate["executable_sha256"] != trusted["owned_executable_sha256"]:
        raise AssertionError(
            "executable_sha256 must match the owned executable digest")
    if not candidate.get("executed_matches_build"):
        raise AssertionError("executed_matches_build must be true for evaluation")


def assert_promotion(cert: Mapping[str, Any], expected: str) -> None:
    assert_certificate_shape(cert)
    status = cert["promotion"]["status"]
    if status != expected:
        raise AssertionError(
            f"promotion.status expected {expected!r}, got {status!r}: "
            f"{cert['promotion'].get('reasons')}")


def assert_evidence_not_live_grounded(cert: Mapping[str, Any]) -> None:
    """Explicitly synthetic sessions cannot receive live-grounded certification."""
    tier = cert.get("evidence_tier") or cert.get("evidence", {}).get("tier")
    if tier == "tier1-live-sealed-session":
        raise AssertionError("synthetic evidence cannot claim tier1-live-sealed-session")
    if cert["promotion"]["status"] == "certified" and cert["evidence"].get(
            "explicitly_synthetic"):
        raise AssertionError("explicitly synthetic evidence cannot be certified")


def assert_rejects_false_certification(
        cert: Mapping[str, Any],
        *,
        expect_not_certified: bool = True) -> None:
    """Used by negative-control tests: a stand-in must not satisfy promotion rules."""
    assert_certificate_shape(cert)
    if expect_not_certified and cert["promotion"]["status"] == "certified":
        raise AssertionError(
            "stand-in incorrectly certified: "
            f"{cert['promotion'].get('reasons')}")


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
            "trusted_build": {
                "inputs_sha256": {"gfx_set_constant_buffer.cpp": "f" * 64},
                "recipe_sha256": "0" * 64,
                "owned_executable": "/owned/harness",
                "owned_executable_sha256": "e" * 64,
            },
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
    """Schema-valid certificate that certifies despite one eligible mismatch."""
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


def self_declared_build_stand_in(**_kwargs: Any) -> dict[str, Any]:
    """Hashes agree but trusted_build is absent — must not count as bound."""
    cert = _base_certificate(promotion={"status": "certified", "reasons": []})
    candidate = dict(cert["candidate"])
    candidate.pop("trusted_build", None)
    cert["candidate"] = candidate
    return cert
