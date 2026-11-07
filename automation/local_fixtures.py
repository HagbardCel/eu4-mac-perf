"""Local content-addressed fixture store for proprietary EU4 assets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from automation.verification_manifest import EU4_EXECUTABLE_SHA256, canonical_json

DEFAULT_STORE = Path(".local/fixtures")
PROVENANCE_NAME = "provenance.json"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store_root(repo: Path) -> Path:
    override = os.environ.get("EU4_FIXTURE_STORE")
    if override:
        return Path(override)
    return repo / DEFAULT_STORE


def fixture_path(repo: Path, digest: str) -> Path:
    return store_root(repo) / "sha256" / digest


def install_file(repo: Path, source: Path, *, kind: str, note: str = "") -> dict:
    digest = sha256_file(source)
    dest = fixture_path(repo, digest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        shutil.copy2(source, dest)
    elif sha256_file(dest) != digest:
        raise RuntimeError(f"fixture collision at {dest}")
    record = {
        "schema": "eu4-local-fixture/v1",
        "sha256": digest,
        "kind": kind,
        "source_path": str(source),
        "store_path": str(dest.relative_to(repo)) if dest.is_relative_to(repo) else str(dest),
        "eu4_executable_sha256": EU4_EXECUTABLE_SHA256,
        "note": note,
    }
    meta = dest.with_suffix(dest.suffix + ".json")
    meta.write_text(canonical_json(record), encoding="utf-8")
    return record


def require_fixture(repo: Path, digest: str) -> Path | None:
    path = fixture_path(repo, digest)
    if not path.is_file():
        return None
    if sha256_file(path) != digest:
        raise RuntimeError(f"corrupt fixture {digest}")
    return path


def install_simple_shader(repo: Path) -> dict | None:
    default = Path("/Applications/EuropaUniversalisIV/gfx/FX/simple.shader")
    if not default.is_file():
        return None
    return install_file(
        repo,
        default,
        kind="simple.shader",
        note="Authorized local install copy; do not redistribute in git",
    )
