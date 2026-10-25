"""Production-artifact gate helpers (E2-owned; no import from E1 test modules)."""
from __future__ import annotations

import json
import platform
import shutil
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

from tests.oracle_certify_acceptance.acceptance_candidate import BUILD_FLAGS
from tests.oracle_certify_acceptance.re1_harness import BUILD_FLAGS as HARNESS_BUILD_FLAGS

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
APPROVED_HARNESS = FIXTURES / "re1-approved-harness"
APPROVED_META = FIXTURES / "re1-approved-harness.meta.json"
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


def production_binding_compatible() -> tuple[bool, str]:
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
    record = production_record()
    if record is not None:
        expected = record.get("executable_sha256")
        if isinstance(expected, str) and len(expected) == 64:
            actual = sha256(APPROVED_HARNESS.read_bytes()).hexdigest()
            if actual != expected:
                return (
                    False,
                    "fixture executable_sha256 does not match the production build record "
                    f"({actual} != {expected})",
                )
    elif meta.get("executable_sha256"):
        actual = sha256(APPROVED_HARNESS.read_bytes()).hexdigest()
        if actual != meta["executable_sha256"]:
            return False, "fixture bytes do not match fixtures/re1-approved-harness.meta.json"
    if HARNESS_BUILD_FLAGS != BUILD_FLAGS:
        return False, "harness build flags drifted from acceptance BUILD_FLAGS"
    return True, "compatible"


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
