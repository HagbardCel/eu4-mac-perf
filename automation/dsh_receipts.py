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

from automation import gfx_ed25519
from automation.state import atomic_write
from automation.verification_manifest import (
    canonical_json,
    digest_bytes,
    digest_text,
    file_sha256 as digest_file,
)

SCHEMA = "eu4-dsh-qualification/v1"
REVISION_SCHEMA = "eu4-revision-qualification/v1"


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


def write_receipt(run_root: Path, receipt: dict, *, seed: bytes | None = None) -> tuple[Path, dict]:
    """Seal and store the one qualification record."""
    body = dict(receipt)
    body.pop("receipt_digest", None)
    body.pop("signature", None)
    if seed is not None:
        body["signature"] = gfx_ed25519.sign(seed, canonical_json(body).encode()).hex()
    body["receipt_digest"] = digest_text(canonical_json(body))
    target = paths(run_root).qualification
    atomic_write(target, canonical_json(body))
    return target, body


def worker_outputs(directory: Path, allowed, *, required=(), base: Path | None = None) -> list[dict]:
    """Inventory a terminated worker's closed output set without following links."""
    parents = {parent.as_posix() for name in allowed for parent in Path(name).parents}
    records = {}
    for path in sorted(directory.rglob("*")):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink():
            raise ValueError(f"worker output is a symlink: {name}")
        if path.is_dir() and name in parents:
            continue
        info = path.stat()
        if name not in allowed or not path.is_file() or info.st_nlink != 1:
            raise ValueError(f"unexpected worker output: {name}")
        records[name] = {"file": path.relative_to(base or directory).as_posix(),
                         "bytes": info.st_size, "sha256": digest_file(path)}
    if missing := set(required) - records.keys():
        raise ValueError(f"required worker outputs are absent: {sorted(missing)}")
    return list(records.values())


def authentic_receipt(receipt: dict, public_key: bytes) -> bool:
    if verify_receipt_digest(receipt):
        return False
    try:
        signature = bytes.fromhex(receipt["signature"])
    except (KeyError, TypeError, ValueError):
        return False
    body = {key: value for key, value in receipt.items()
            if key not in ("receipt_digest", "signature")}
    return gfx_ed25519.verify(public_key, canonical_json(body).encode(), signature)


def _artifact(root: Path, name: str) -> dict:
    relative = Path(name)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"invalid artifact path: {name}")
    path = root / relative
    if (any((root / part).is_symlink() for part in (relative, *relative.parents))
            or not path.is_file() or path.stat().st_nlink != 1):
        raise ValueError(f"artifact is not a private regular file: {name}")
    return {"file": name, "bytes": path.stat().st_size, "sha256": digest_file(path)}


def artifact_records(root: Path, names) -> list[dict]:
    return [_artifact(root, name) for name in sorted(set(names))]


def _verified_receipt(path: Path, public_key: bytes) -> dict:
    _artifact(path.parent, path.name)
    receipt = read_json(path)
    if (not isinstance(receipt, dict) or receipt.get("schema") != REVISION_SCHEMA
            or not authentic_receipt(receipt, public_key)):
        raise ValueError(f"invalid signed qualification receipt: {path}")
    return receipt


def applicable_execution(receipt_path: Path, binding: dict, public_key: bytes) -> dict | None:
    """Verify a protected receipt and its outputs without relabeling it."""
    try:
        previous = _verified_receipt(receipt_path, public_key)
        if (previous.get("execution") == "completed"
                and previous.get("outputs")
                and all(previous.get(key) == value for key, value in binding.items())
                and all(item == _artifact(receipt_path.parent, item["file"])
                        for item in previous["outputs"])):
            return previous
    except (KeyError, TypeError, ValueError, OSError):
        pass
    return None


def verify_revision(path: Path, public_key: bytes, *, revision: str, policy_sha256: str,
                    policy_contract_sha256: str, required_cases: list[str], run_root: Path) -> dict:
    """Verify a mandatory decision, every case binding, and the exact artifact bytes."""
    path = Path(path).absolute()
    run_root = Path(run_root).resolve(strict=True)
    _artifact(run_root, path.relative_to(run_root).as_posix())
    decision = _verified_receipt(path, public_key)
    if any(decision.get(key) != value for key, value in {
        "mode": "mandatory", "revision": revision, "policy_sha256": policy_sha256,
        "policy_contract_sha256": policy_contract_sha256, "required_cases": required_cases,
    }.items()) or [row["case_id"] for row in decision["cases"]] != required_cases:
        raise ValueError("mandatory decision does not bind the requested policy and revision")
    for row in decision["cases"]:
        receipt_path = Path(row["receipt"]).absolute()
        _artifact(run_root, receipt_path.relative_to(run_root).as_posix())
        receipt = _verified_receipt(receipt_path, public_key)
        if receipt["receipt_digest"] != row["receipt_digest"] or receipt.get("case_id") != row["case_id"]:
            raise ValueError(f"substituted case receipt: {row['case_id']}")
        if row.get("execution_reused"):
            binding = {key: row[key] for key in ("candidate", "recipe", "environment", "reference",
                                                 "validation_contract", "execution_revision")}
            if applicable_execution(receipt_path, binding, public_key) is None:
                raise ValueError(f"inapplicable case receipt: {row['case_id']}")
        elif (not receipt_path.is_relative_to(path.parent)
              or receipt.get("nonce") != path.parent.name
              or receipt.get("tree") != decision["tree"]
              or receipt.get("execution_revision") != revision
              or receipt.get("policy_sha256") != policy_sha256):
            raise ValueError(f"case receipt does not belong to this decision: {row['case_id']}")
        if any(item != _artifact(receipt_path.parent, item["file"])
               for item in receipt.get("outputs", [])):
            raise ValueError(f"case output changed: {row['case_id']}")
    return decision


def load_current_receipt(run_root: Path) -> dict | None:
    return read_json(paths(run_root).qualification)


def verify_receipt_digest(receipt: dict) -> list[str]:
    claimed = receipt.get("receipt_digest")
    body = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    actual = digest_text(canonical_json(body))
    if claimed != actual:
        return ["receipt_digest mismatch: claimed %s, computed %s" % (claimed, actual)]
    return []
