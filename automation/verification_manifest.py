"""Frozen verification-manifest helpers for autonomous qualification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "eu4-verification-manifest/v1"
REPO_BASELINE_COMMIT = "173321b6d3258576060e7941e9bd91f608e8f543"
EU4_EXECUTABLE_SHA256 = (
    "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_text(text: str) -> str:
    return digest_bytes(text.encode("utf-8"))


def digest_manifest(manifest: dict) -> str:
    return digest_text(canonical_json(manifest))


def build_manifest(
    *,
    members: list[dict],
    repo_baseline_commit: str = REPO_BASELINE_COMMIT,
    eu4_executable_sha256: str = EU4_EXECUTABLE_SHA256,
    extra: dict | None = None,
) -> dict:
    """Build a verification manifest.

    Each member is ``{"path": relpath, "sha256": hex, "role": str}``.
    ``path`` is relative to the repository root unless absolute.
    """
    body = {
        "schema": SCHEMA,
        "repo_baseline_commit": repo_baseline_commit,
        "eu4_executable_sha256": eu4_executable_sha256,
        "members": sorted(members, key=lambda item: (item["role"], item["path"])),
    }
    if extra:
        body.update(extra)
    return body


def member_from_file(repo: Path, relpath: str, role: str) -> dict:
    path = Path(relpath)
    if not path.is_absolute():
        path = repo / relpath
    return {
        "path": relpath if not Path(relpath).is_absolute() else str(path),
        "sha256": file_sha256(path),
        "role": role,
    }


def verify_manifest(repo: Path, manifest: dict) -> list[str]:
    """Return human-readable errors; empty means OK."""
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if "repo_baseline_commit" not in manifest:
        errors.append("missing repo_baseline_commit")
    if "eu4_executable_sha256" not in manifest:
        errors.append("missing eu4_executable_sha256")
    if manifest.get("repo_baseline_commit") == manifest.get("eu4_executable_sha256"):
        errors.append("repo_baseline_commit must not equal eu4_executable_sha256")
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        errors.append("members must be a non-empty list")
        return errors
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            errors.append(f"members[{index}] must be an object")
            continue
        for key in ("path", "sha256", "role"):
            if key not in member:
                errors.append(f"members[{index}] missing {key}")
        path_value = member.get("path")
        expected = member.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected, str):
            continue
        path = Path(path_value)
        if not path.is_absolute():
            path = repo / path_value
        if not path.is_file():
            errors.append(f"missing member file: {path_value}")
            continue
        actual = file_sha256(path)
        if actual != expected:
            errors.append(
                f"hash mismatch for {path_value}: expected {expected}, got {actual}"
            )
    return errors


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(path: Path, manifest: dict) -> str:
    text = canonical_json(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return digest_text(text)
