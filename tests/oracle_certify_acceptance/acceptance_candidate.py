"""Prepare a known-good RE1 candidate mapping for E2 integration tests only.

E1 must independently verify identities and establish source-to-build trust.
This module does not supply authoritative trusted_build metadata.
"""
from __future__ import annotations

import shutil
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.certify.build_record import RE1_SOURCE_FILES
from tests.oracle_certify_acceptance.re1_harness import (
    BUILD_FLAGS,
    build_re1_harness,
)

BUILD_IDENTITY = "re1-gfx-set-constant-buffer-acceptance"


def re1_canonical_source_sha256() -> str:
    digest = sha256()
    for path in sorted(RE1_SOURCE_FILES, key=lambda path: path.name):
        name = path.name
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def build_acceptance_candidate(work_dir: Path, harness: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    owned = work_dir / "re1-harness-bin"
    shutil.copy2(harness, owned)
    owned.chmod(0o755)
    return {
        "source_sha256": re1_canonical_source_sha256(),
        "executable_sha256": sha256(owned.read_bytes()).hexdigest(),
        "build_identity": BUILD_IDENTITY,
        "build_flags": list(BUILD_FLAGS),
        "executed_matches_build": True,
        "harness_path": str(owned),
    }


def prepare_acceptance_candidate(work_dir: Path) -> dict[str, Any]:
    harness = build_re1_harness()
    if harness is None:
        raise RuntimeError("RE1 harness not built")
    return build_acceptance_candidate(work_dir, harness)
