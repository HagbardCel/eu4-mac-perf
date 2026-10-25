"""Frozen RE1 production build record (read-only trust root for candidate binding)."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.certify.errors import CertifyError

SCHEMA = "eu4-oracle-trusted-build/v1"
_PRODUCTION_BUILD_RECORD_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "oracle" / "policies"
    / "re1-gfx-set-constant-buffer-trusted-build.json")
RECON_DIR = (Path(__file__).resolve().parents[2] / "analysis" / "reconstruction"
             / "_GfxSetConstantBuffer" / "reconstruction")
RE1_SOURCE_FILES = (
    "arith_literals.cpp",
    "gfx_set_constant_buffer.cpp",
    "gfx_set_constant_buffer.hpp",
    "harness.cpp",
)
TRUSTED_BUILD_FLAGS = (
    "-std=c++20",
    "-O0",
    "-g",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-fsanitize=address,undefined",
    "-fno-sanitize-recover=all",
)
_SHA256_RE = __import__("re").compile(r"\A[0-9a-f]{64}\Z")


def frozen_re1_source_sha256() -> str:
    """Digest of tracked RE1 sources (four behavior-defining files)."""
    digest = sha256()
    for name in sorted(RE1_SOURCE_FILES):
        path = RECON_DIR / name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CertifyError(
                f"frozen reconstruction source is unreadable: {name}: {error}") from None
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(data)
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CertifyError("production build record is not a JSON object")
    if raw.get("schema") != SCHEMA:
        raise CertifyError("production build record schema is not trusted-build/v1")
    identity = raw.get("build_identity")
    if not isinstance(identity, str) or not identity:
        raise CertifyError("production build record build_identity is missing")
    source = raw.get("source_sha256")
    if not isinstance(source, str) or not _SHA256_RE.match(source):
        raise CertifyError("production build record source_sha256 is missing or invalid")
    executable = raw.get("executable_sha256")
    if not isinstance(executable, str) or not _SHA256_RE.match(executable):
        raise CertifyError(
            "production build record executable_sha256 is missing or invalid")
    flags = raw.get("build_flags")
    if type(flags) is not list or any(type(item) is not str for item in flags):
        raise CertifyError("production build record build_flags are invalid")
    if tuple(flags) != TRUSTED_BUILD_FLAGS:
        raise CertifyError(
            "production build record build_flags are not the trusted reconstruction flags")
    expected_source = frozen_re1_source_sha256()
    if source != expected_source:
        raise CertifyError(
            "production build record source_sha256 does not match the frozen "
            "reconstruction sources")
    return raw


def load_production_build_record() -> dict[str, Any]:
    """Load the fixed production build record from the policy tree."""
    try:
        text = _PRODUCTION_BUILD_RECORD_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise CertifyError(
            f"production build record is unreadable: {error}") from None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise CertifyError("production build record is not valid JSON") from None
    return _validate_record(parsed)
