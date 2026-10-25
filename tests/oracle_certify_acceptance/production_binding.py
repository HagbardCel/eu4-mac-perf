"""Production-artifact gate helpers (E2-owned; no import from E1 test modules)."""
from __future__ import annotations

import json
import platform
import re
import shutil
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

BindingAction = Literal["skip", "ok", "fail"]

from tests.oracle_certify_acceptance.acceptance_candidate import BUILD_FLAGS
from tests.oracle_certify_acceptance.re1_harness import BUILD_FLAGS as HARNESS_BUILD_FLAGS

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
APPROVED_HARNESS = FIXTURES / "re1-approved-harness"
APPROVED_META = FIXTURES / "re1-approved-harness.meta.json"
# Must match fixtures/re1-approved-harness.meta.json platform (approved pin).
APPROVED_PLATFORM_SYSTEM = "darwin"
APPROVED_PLATFORM_MACHINE = "arm64"
PRODUCTION_RECORD = (
    ROOT / "docs/oracle/policies/re1-gfx-set-constant-buffer-trusted-build.json")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def approved_harness_meta() -> dict[str, Any] | None:
    if not APPROVED_META.is_file():
        return None
    return _load_json(APPROVED_META)


def production_record() -> dict[str, Any] | None:
    if not PRODUCTION_RECORD.is_file():
        return None
    return _load_json(PRODUCTION_RECORD)


def test_local_build_record(candidate: dict[str, Any]) -> dict[str, Any]:
    """Record injected by portable tests via patch on certify.load_production_build_record."""
    return {
        "schema": "eu4-oracle-trusted-build/v1",
        "build_identity": candidate["build_identity"],
        "source_sha256": candidate["source_sha256"],
        "executable_sha256": candidate["executable_sha256"],
        "build_flags": list(candidate["build_flags"]),
    }


def _sha256_hex(value: Any) -> str | None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        return None
    return value.lower()


def approved_harness_digest() -> str:
    if not APPROVED_HARNESS.is_file():
        raise FileNotFoundError("approved harness fixture is missing")
    return sha256(APPROVED_HARNESS.read_bytes()).hexdigest()


def host_compatible() -> tuple[bool, str]:
    """True when this host can execute the pinned approved harness bytes."""
    if not APPROVED_HARNESS.is_file():
        return False, "approved harness fixture is missing"
    meta = approved_harness_meta()
    if meta is None:
        return False, "approved harness metadata is missing"
    plat = meta.get("platform") or {}
    system = str(plat.get("system", "")).lower()
    machine = str(plat.get("machine", "")).lower()
    host_system = platform.system().lower()
    host_machine = platform.machine().lower()
    if system and system != host_system:
        return (
            False,
            f"approved harness targets {system}/{machine}; host is {host_system}/{host_machine}",
        )
    if machine and machine != host_machine:
        return (
            False,
            f"approved harness targets {system}/{machine}; host is {host_system}/{host_machine}",
        )
    return True, "compatible host"


def assert_fixture_integrity() -> tuple[bool, str]:
    """True when fixture bytes and flags match committed metadata and production record."""
    if not APPROVED_HARNESS.is_file():
        return False, "approved harness fixture is missing"
    meta = approved_harness_meta()
    if meta is None:
        return False, "approved harness metadata is missing"
    actual = approved_harness_digest()
    meta_executable = _sha256_hex(meta.get("executable_sha256"))
    if meta_executable is None:
        return (
            False,
            "fixtures/re1-approved-harness.meta.json executable_sha256 is missing or invalid",
        )
    if actual != meta_executable:
        return (
            False,
            "fixture executable_sha256 does not match "
            f"fixtures/re1-approved-harness.meta.json ({actual} != {meta_executable})",
        )
    record = production_record()
    if record is not None:
        record_executable = _sha256_hex(record.get("executable_sha256"))
        if record_executable is None:
            return (
                False,
                "production build record executable_sha256 is missing or invalid",
            )
        if actual != record_executable:
            return (
                False,
                "fixture executable_sha256 does not match the production build record "
                f"({actual} != {record_executable})",
            )
        if meta_executable != record_executable:
            return (
                False,
                "fixtures/re1-approved-harness.meta.json executable_sha256 does not "
                f"match the production build record ({meta_executable} != {record_executable})",
            )
        meta_source = _sha256_hex(meta.get("source_sha256"))
        record_source = _sha256_hex(record.get("source_sha256"))
        if meta_source is None or record_source is None:
            return (
                False,
                "fixture or production build record source_sha256 is missing or invalid",
            )
        if meta_source != record_source:
            return (
                False,
                "fixtures/re1-approved-harness.meta.json source_sha256 does not "
                f"match the production build record ({meta_source} != {record_source})",
            )
        meta_identity = meta.get("build_identity")
        record_identity = record.get("build_identity")
        if not isinstance(meta_identity, str) or not meta_identity:
            return (
                False,
                "fixtures/re1-approved-harness.meta.json build_identity is missing or invalid",
            )
        if not isinstance(record_identity, str) or not record_identity:
            return (
                False,
                "production build record build_identity is missing or invalid",
            )
        if meta_identity != record_identity:
            return (
                False,
                "fixtures/re1-approved-harness.meta.json build_identity does not "
                f"match the production build record ({meta_identity} != {record_identity})",
            )
        meta_flags = meta.get("build_flags")
        record_flags = record.get("build_flags")
        if not isinstance(meta_flags, list) or not all(
            isinstance(flag, str) for flag in meta_flags
        ):
            return (
                False,
                "fixtures/re1-approved-harness.meta.json build_flags is missing or invalid",
            )
        if not isinstance(record_flags, list) or not all(
            isinstance(flag, str) for flag in record_flags
        ):
            return (
                False,
                "production build record build_flags is missing or invalid",
            )
        if meta_flags != record_flags:
            return (
                False,
                "fixtures/re1-approved-harness.meta.json build_flags do not "
                "match the production build record",
            )
    if HARNESS_BUILD_FLAGS != BUILD_FLAGS:
        return False, "harness build flags drifted from acceptance BUILD_FLAGS"
    plat = meta.get("platform")
    if not isinstance(plat, dict):
        return (
            False,
            "fixtures/re1-approved-harness.meta.json platform is missing or invalid",
        )
    system = str(plat.get("system", "")).lower()
    machine = str(plat.get("machine", "")).lower()
    if system != APPROVED_PLATFORM_SYSTEM or machine != APPROVED_PLATFORM_MACHINE:
        return (
            False,
            "fixtures/re1-approved-harness.meta.json platform does not match "
            f"the approved pin ({system}/{machine} != "
            f"{APPROVED_PLATFORM_SYSTEM}/{APPROVED_PLATFORM_MACHINE})",
        )
    return True, "fixture integrity ok"


def production_binding_compatible() -> tuple[BindingAction, str]:
    """Classify host readiness vs artifact integrity for production binding gates."""
    host_ok, host_reason = host_compatible()
    if not host_ok:
        return "skip", host_reason
    integrity_ok, integrity_reason = assert_fixture_integrity()
    if not integrity_ok:
        return "fail", integrity_reason
    return "ok", "compatible"


def copy_approved_harness(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(APPROVED_HARNESS, dest)
    dest.chmod(0o755)
    return dest


def production_candidate(work_dir: Path) -> dict[str, Any]:
    record = production_record()
    meta = approved_harness_meta()
    if record is None and meta is None:
        raise RuntimeError("production build record and fixture metadata are both absent")
    source = record or meta
    owned = work_dir / "re1-approved-harness"
    copy_approved_harness(owned)
    digest = sha256(owned.read_bytes()).hexdigest()
    expected = source.get("executable_sha256")
    if isinstance(expected, str) and digest != expected:
        raise RuntimeError(
            f"approved harness digest {digest} != record {expected}")
    flags = source.get("build_flags")
    if not isinstance(flags, list):
        flags = list(BUILD_FLAGS)
    identity = source.get("build_identity")
    if not isinstance(identity, str) or not identity:
        raise RuntimeError("production build_identity is missing")
    return {
        "source_sha256": source["source_sha256"],
        "executable_sha256": digest,
        "build_identity": identity,
        "build_flags": list(flags),
        "executed_matches_build": True,
        "harness_path": str(owned),
    }
