"""The trusted qualification record, and the candidate identities beside it.

One record per campaign, overwritten by each validation. It is not authority --
finalization re-runs the checker -- it exists so an operator can see which
candidate, checker and evidence a verdict was produced against.

Attempt history, counters and a journal are deliberately absent: DSH keeps the
session history, and the directive for this integration is explicit that the
project layer preserves only the qualification artifact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from automation.state import atomic_write
from automation.verification_manifest import (
    canonical_json,
    digest_bytes,
    digest_text,
    file_sha256 as digest_file,
)

SCHEMA = "eu4-dsh-qualification/v1"


def tree_digest(root: Path) -> tuple[str, list[dict]]:
    """Deterministic digest over a workspace tree, for seed-mode candidates.

    Symlinks are recorded by target rather than followed, so the digest cannot
    change meaning depending on what the link currently points at.
    """
    root = Path(root)
    entries: list[dict] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(files):
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "kind": "symlink",
                                "sha256": digest_text(os.readlink(path))})
            else:
                entries.append({"path": rel, "kind": "file",
                                "sha256": digest_file(path)})
    entries.sort(key=lambda item: item["path"])
    return digest_text(canonical_json(entries)), entries


def identity_digest(identities: dict) -> str:
    return digest_text(canonical_json(identities))


@dataclass(frozen=True)
class RunPaths:
    run_root: Path

    @property
    def identity(self) -> Path:
        return self.run_root / "identity.json"

    @property
    def qualification(self) -> Path:
        return self.run_root / "qualification.json"

    @property
    def lock(self) -> Path:
        return self.run_root / "dsh.lock"

    def ensure(self) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)


def paths(run_root: Path) -> RunPaths:
    return RunPaths(Path(run_root))


def read_json(path: Path) -> dict | None:
    if not Path(path).is_file():
        return None
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_identity(run_root: Path, value: dict) -> Path:
    path = paths(run_root).identity
    atomic_write(path, canonical_json(value))
    return path


def write_receipt(run_root: Path, receipt: dict) -> tuple[Path, dict]:
    """Seal and store the one qualification record."""
    body = dict(receipt)
    body.pop("receipt_digest", None)
    body["receipt_digest"] = digest_text(canonical_json(body))
    target = paths(run_root).qualification
    atomic_write(target, canonical_json(body))
    return target, body


def load_current_receipt(run_root: Path) -> dict | None:
    return read_json(paths(run_root).qualification)


def verify_receipt_digest(receipt: dict) -> list[str]:
    claimed = receipt.get("receipt_digest")
    body = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    actual = digest_text(canonical_json(body))
    if claimed != actual:
        return ["receipt_digest mismatch: claimed %s, computed %s" % (claimed, actual)]
    return []
