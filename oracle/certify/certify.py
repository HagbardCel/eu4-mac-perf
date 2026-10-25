"""Emit one deterministic Tier 1 certificate for a sealed session.

Contract baseline: 8632d66b8c9c977ec6f03eac76767a4800eb7ebc.

This module does not launch EU IV, attach to a process, rebuild the native
capture library, or call ``benchmark/eu4_gfx_live_capture.py:report``. It does
not create ``manifest.json``. Live grounding is never claimed: matching
recorded hashes do not prove that those bytes were loaded in the game.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.certify.build_record import (
    TRUSTED_BUILD_FLAGS,
    frozen_re1_source_sha256,
    load_production_build_record,
)
from oracle.certify.errors import CertifyError
from oracle.certify.evidence import Admission, admit_session
from oracle.certify.policy import coverage, load_policy
from oracle.function import fixture, replay

SCHEMA = "eu4-oracle-tier1-certificate/v1"
CERTIFICATE_NAME = "tier1-certificate.json"

_LIVE_TIER = "tier1-live-sealed-session"
_GROUNDING = (
    "live grounding is not established; the certifier checks recorded identity "
    "and hash consistency only"
)


def frozen_source_sha256() -> str:
    """Digest of the tracked RE1 sources, independent of a caller-supplied hash."""
    return frozen_re1_source_sha256()


def _descriptor_file_sha256(descriptor: dict[str, Any]) -> str:
    path = fixture.DESCRIPTOR_PATH
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
        on_disk = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CertifyError(f"cannot read the frozen descriptor: {error}") from None
    try:
        fixture.validate_descriptor(descriptor)
    except fixture.FixtureError as error:
        raise CertifyError(f"descriptor is not usable: {error}") from None
    if on_disk != descriptor:
        raise CertifyError("descriptor does not match the frozen descriptor file")
    return sha256(data).hexdigest()


def _file_sha256(path: Path) -> str | None:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _reject_certificate_inside_session(session_dir: Path, certificate_dir: Path) -> None:
    if certificate_dir == session_dir or session_dir in certificate_dir.parents:
        raise CertifyError(
            "the certificate directory must be outside the sealed evidence directory")


@dataclass(frozen=True)
class _Binding:
    ok: bool
    failures: tuple[str, ...]
    source_sha256: str
    executable_sha256: str | None
    submitted_executable_sha256: str | None
    build_identity: str
    build_flags: tuple[str, ...]
    harness_path: Path | None


def _bind_candidate(candidate: dict[str, Any]) -> _Binding:
    if not isinstance(candidate, dict):
        raise CertifyError("candidate must be an object")
    required = ("source_sha256", "executable_sha256", "build_identity",
                "build_flags", "harness_path")
    missing = [key for key in required if key not in candidate]
    if missing:
        raise CertifyError(f"candidate is missing {missing}")

    failures: list[str] = []
    source = frozen_source_sha256()
    submitted_source = candidate.get("source_sha256")
    if submitted_source != source:
        failures.append(
            "candidate binding: source_sha256 does not match the frozen "
            "reconstruction sources")

    flags = candidate.get("build_flags")
    if type(flags) is not list or any(type(item) is not str for item in flags):
        flag_tuple: tuple[str, ...] = ()
        failures.append(
            "candidate binding: build flags are not the trusted reconstruction flags")
    else:
        flag_tuple = tuple(flags)
        if flag_tuple != TRUSTED_BUILD_FLAGS:
            failures.append(
                "candidate binding: build flags are not the trusted reconstruction flags")

    identity = candidate.get("build_identity")
    if not isinstance(identity, str) or not identity:
        identity = ""
        failures.append("candidate binding: build identity is missing")

    harness_value = candidate.get("harness_path")
    harness: Path | None = None
    on_disk: str | None = None
    submitted_executable = candidate.get("executable_sha256")
    if not isinstance(submitted_executable, str):
        submitted_executable = None
    if not isinstance(harness_value, str) or not harness_value:
        failures.append(
            "candidate binding: executable digest could not be read; "
            "executed_matches_build is false")
    else:
        harness = Path(harness_value).expanduser()
        on_disk = _file_sha256(harness)
        if on_disk is None:
            failures.append(
                "candidate binding: executable digest could not be read; "
                "executed_matches_build is false")
        elif on_disk != submitted_executable:
            failures.append(
                "candidate binding: executable digest "
                f"{on_disk} does not match the recorded build artifact "
                f"{submitted_executable}; executed_matches_build is false")

    try:
        record = load_production_build_record()
    except CertifyError as error:
        failures.append(f"candidate binding: {error}")
        record = None

    if record is not None:
        record_source = record["source_sha256"]
        if source != record_source:
            failures.append(
                "candidate binding: frozen source digest disagrees with the "
                "production build record")
        if submitted_source != record_source:
            failures.append(
                "candidate binding: source_sha256 does not match the production "
                "build record")
        record_identity = record["build_identity"]
        if identity != record_identity:
            failures.append(
                "candidate binding: build_identity does not match the production "
                "build record")
        record_flags = tuple(record["build_flags"])
        if flag_tuple != record_flags:
            failures.append(
                "candidate binding: build flags do not match the production "
                "build record")
        record_executable = record["executable_sha256"]
        if submitted_executable is not None and submitted_executable != record_executable:
            failures.append(
                "candidate binding: executable_sha256 does not match the production "
                "build record")
        if on_disk is not None and on_disk != record_executable:
            failures.append(
                "candidate binding: executable digest does not match the production "
                "build record")

    if record is not None:
        identity = record["build_identity"]
        flag_tuple = tuple(record["build_flags"])

    return _Binding(
        ok=not failures,
        failures=tuple(failures),
        source_sha256=source,
        executable_sha256=on_disk,
        submitted_executable_sha256=submitted_executable,
        build_identity=identity,
        build_flags=flag_tuple,
        harness_path=harness,
    )


def _classify(result: dict[str, Any]) -> str:
    verdict = result.get("verdict")
    failures = [str(item) for item in result.get("failures") or []]
    if verdict == "match" and not failures:
        return "match"
    if verdict == "mismatch":
        return "mismatch"
    if verdict == "refused" and failures and all(
            item.startswith("infrastructure:") for item in failures):
        return "infrastructure_refusal"
    return "evidence_refusal"


def _explicitly_synthetic(admission: Admission) -> bool:
    text = " ".join((*admission.reasons, *admission.fixture_reasons)).lower()
    return "synthetic" in text


def _coverage_or_failure(records: list[dict[str, Any]], descriptor: dict[str, Any],
                         policy: Any) -> tuple[dict[str, Any], str | None]:
    try:
        body = coverage(records, descriptor, policy)
    except CertifyError as error:
        return {}, str(error)
    return body, None


def _promotion(admission: Admission, binding: _Binding, counts: dict[str, int],
               coverage_body: dict[str, Any], coverage_error: str | None,
               *, executed_matches_build: bool, replayed: bool) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not admission.eligible:
        reasons.append("session admission ineligible")
    if admission.fixtures != "eligible":
        reasons.append("fixture admission ineligible")
    reasons.extend(binding.failures)
    if counts["mismatch"]:
        reasons.append("eligible mismatch")
    if counts["infrastructure_refusal"]:
        reasons.append("eligible infrastructure refusal")
    if counts["evidence_refusal"]:
        reasons.append("eligible evidence refusal is an admission inconsistency")
    if coverage_error:
        reasons.append(f"coverage behavior key could not be derived: {coverage_error}")
    if replayed and not executed_matches_build:
        reasons.append(
            "candidate binding: executable digest changed during evaluation; "
            "executed_matches_build is false")

    if reasons:
        return "failed", reasons

    missing_required = list(coverage_body.get("missing_required") or [])
    diversity_met = bool(coverage_body.get("diversity_met"))
    if missing_required or not diversity_met:
        if missing_required:
            reasons.append("required coverage cells are missing")
        if not diversity_met:
            reasons.append("diversity floor is not met")
        return "scoped", reasons
    return "certified", []


def certify_session(session_dir: Path, descriptor: dict[str, Any], policy: Path,
                    candidate: dict[str, Any], *,
                    certificate_dir: Path) -> dict[str, Any]:
    """Certify one sealed session. The certificate is written under ``certificate_dir``."""
    session_dir = Path(session_dir).expanduser().resolve()
    certificate_dir = Path(certificate_dir).expanduser().resolve()
    policy_path = Path(policy).expanduser().resolve()
    _reject_certificate_inside_session(session_dir, certificate_dir)

    file_sha = _descriptor_file_sha256(descriptor)
    loaded = load_policy(policy_path, descriptor, file_sha)
    admission = admit_session(session_dir, descriptor, file_sha)
    binding = _bind_candidate(candidate)

    records = [item.record for item in admission.eligible_transcripts]
    coverage_body, coverage_error = _coverage_or_failure(records, descriptor, loaded)
    limitations = list(coverage_body.pop("_limitations", [])) if coverage_body else []

    counts = {"match": 0, "mismatch": 0, "infrastructure_refusal": 0,
              "evidence_refusal": 0}
    failures: list[str] = []
    replayed = False
    executed_matches_build = False
    if (admission.eligible and admission.fixtures == "eligible" and binding.ok
            and binding.harness_path is not None
            and binding.executable_sha256 is not None):
        replayed = True
        before = binding.executable_sha256
        for transcript in admission.eligible_transcripts:
            try:
                result = replay.replay_capture(transcript.text, binding.harness_path)
            except Exception as error:  # one fixture must not hide the rest
                result = {"verdict": "refused", "failures": [
                    f"reference:replay-error: {type(error).__name__}: {error}"]}
            kind = _classify(result)
            counts[kind] += 1
            if kind != "match":
                for failure in result.get("failures") or [kind]:
                    failures.append(f"{transcript.name}: {failure}")
        after = _file_sha256(binding.harness_path)
        executed_matches_build = after == before == binding.submitted_executable_sha256
    else:
        failures.extend(binding.failures)

    explicitly_synthetic = _explicitly_synthetic(admission)
    status, promotion_reasons = _promotion(
        admission, binding, counts, coverage_body, coverage_error,
        executed_matches_build=executed_matches_build, replayed=replayed)

    if not coverage_body:
        coverage_body = {
            "policy_sha256": loaded.sha256,
            "required_cells": list(loaded.required_cells),
            "observed_required": [],
            "missing_required": list(loaded.required_cells),
            "optional_cells": list(loaded.optional_cells),
            "observed_optional": [],
            "missing_optional": list(loaded.optional_cells),
            "distinct_behaviors": 0,
            "duplicate_count": 0,
            "diversity_floor": loaded.min_distinct_behaviors,
            "diversity_met": False,
        }
    limitations.append(_GROUNDING)
    if explicitly_synthetic:
        limitations.append(
            "evidence is explicitly synthetic and is not live-grounded")
    if admission.library_sha256 is None:
        limitations.append(
            "sealed manifest does not record a capture-library digest")
    evidence_tier = ("explicitly-synthetic" if explicitly_synthetic
                     else "sealed-session-unverified")
    if evidence_tier == _LIVE_TIER:
        raise CertifyError("refusing to claim a live-grounded evidence tier")

    certificate = {
        "schema": SCHEMA,
        "function_id": descriptor["function_id"],
        "evidence_tier": evidence_tier,
        "evidence": {
            "session_id": None,
            "manifest_sha256": admission.manifest_sha256,
            "descriptor_sha256": file_sha,
            "policy_sha256": loaded.sha256,
            "transcript_count": admission.transcript_count,
            "eligible": len(admission.eligible_transcripts),
            "ineligible": admission.ineligible,
            "malformed": admission.malformed,
            "loss": admission.loss,
            "explicitly_synthetic": explicitly_synthetic,
            "live_grounded": False,
            "evidence_label": "synthetic" if explicitly_synthetic else "unverified",
        },
        "reference_admission": {
            "session": {"verdict": "eligible" if admission.eligible else "ineligible",
                        "reasons": list(admission.reasons)},
            "fixtures": {"verdict": admission.fixtures,
                         "reasons": list(admission.fixture_reasons)},
        },
        "candidate": {
            "source_sha256": binding.source_sha256,
            "executable_sha256": binding.executable_sha256,
            "build_identity": binding.build_identity,
            "build_flags": list(binding.build_flags),
            "executed_matches_build": executed_matches_build,
        },
        "evaluation": {
            "match": counts["match"],
            "mismatch": counts["mismatch"],
            "infrastructure_refusal": counts["infrastructure_refusal"],
            "evidence_refusal": counts["evidence_refusal"],
            "failures": failures,
        },
        "coverage": {key: value for key, value in coverage_body.items()
                     if not key.startswith("_")},
        "promotion": {"status": status, "reasons": promotion_reasons},
        "limitations": limitations,
    }
    certificate["evidence"]["session_id"] = _session_id(session_dir)

    text = json.dumps(certificate, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    certificate_dir.mkdir(parents=True, exist_ok=True)
    target = certificate_dir / CERTIFICATE_NAME
    _reject_certificate_inside_session(session_dir, target.parent)
    _publish_certificate_atomically(certificate_dir, target, text)
    return json.loads(text)


def _publish_certificate_atomically(certificate_dir: Path, target: Path,
                                    text: str) -> None:
    """Write the certificate via a sibling temp file so symlinks are not followed."""
    fd, staging = tempfile.mkstemp(
        dir=str(certificate_dir),
        prefix=f".{CERTIFICATE_NAME}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        try:
            os.unlink(staging)
        except OSError:
            pass
        raise
    try:
        os.replace(staging, target)
    except OSError as error:
        try:
            os.unlink(staging)
        except OSError:
            pass
        raise CertifyError(
            f"could not publish the tier1 certificate: {error}") from None


def _session_id(session_dir: Path) -> str | None:
    """Return the session id named by the sealed manifest, or null.

    This is an identity field, not an admission verdict. A missing or unreadable
    manifest contributes no id.
    """
    path = session_dir / "manifest.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    session = raw.get("session")
    if not isinstance(session, str) or not session:
        return None
    return session
