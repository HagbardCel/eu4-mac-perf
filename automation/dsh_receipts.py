"""Durable DSH qualification receipts, counters and run-root layout.

Receipts are records, not authority: ``automation.dsh_bridge`` re-verifies every
recomputable identity before it lets anything depend on a stored receipt. What
lives here is storage and hashing only, so it can be tested without a campaign.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from automation.state import atomic_write

SCHEMA = "eu4-dsh-receipt/v1"
CURRENT_SCHEMA = "eu4-dsh-current/v1"
COUNTERS_SCHEMA = "eu4-dsh-counters/v1"
DEFAULT_COUNTERS = {
    "schema": COUNTERS_SCHEMA,
    "validations": 0,
    "identical_retries": 0,
    "policy_stops": 0,
    "integrity_refusals": 0,
    "last_verdict": None,
    "last_diagnostics_digest": None,
    "last_candidate_digest": None,
    "elapsed_seconds": 0.0,
    "started_at": None,
}


def canonical_json(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


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
    def counters(self) -> Path:
        return self.run_root / "counters.json"

    @property
    def receipts(self) -> Path:
        return self.run_root / "receipts"

    @property
    def current(self) -> Path:
        return self.run_root / "current.json"

    @property
    def lock(self) -> Path:
        return self.run_root / "dsh.lock"

    @property
    def events(self) -> Path:
        return self.run_root / "events.jsonl"

    @property
    def validation(self) -> Path:
        return self.run_root / "validation"

    def ensure(self) -> None:
        for directory in (self.run_root, self.receipts, self.validation):
            directory.mkdir(parents=True, exist_ok=True)


def paths(run_root: Path) -> RunPaths:
    return RunPaths(Path(run_root))


def read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_identity(run_root: Path, value: dict) -> Path:
    path = paths(run_root).identity
    atomic_write(path, canonical_json(value))
    return path


def load_counters(run_root: Path) -> dict:
    stored = read_json(paths(run_root).counters) or {}
    counters = dict(DEFAULT_COUNTERS)
    counters.update({k: v for k, v in stored.items() if k in counters})
    return counters


def save_counters(run_root: Path, counters: dict) -> None:
    atomic_write(paths(run_root).counters, canonical_json(counters))


def append_event(run_root: Path, event: dict) -> None:
    path = paths(run_root).events
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def load_current(run_root: Path) -> dict | None:
    return read_json(paths(run_root).current)


def write_receipt(run_root: Path, receipt: dict) -> tuple[Path, dict]:
    """Seal and store one receipt, then repoint ``current.json``."""
    body = dict(receipt)
    body.pop("receipt_digest", None)
    body["receipt_digest"] = digest_text(canonical_json(body))
    identities = body["identities"]
    name = "%s-r%s.json" % (
        str(identities["candidate"]["digest"])[:8],
        body.get("validation_round", 0),
    )
    target = paths(run_root).receipts / name
    atomic_write(target, canonical_json(body))
    atomic_write(
        paths(run_root).current,
        canonical_json({
            "schema": CURRENT_SCHEMA,
            "receipt": target.name,
            "identity_digest": identity_digest(identities),
            "receipt_digest": body["receipt_digest"],
            "written_at": body.get("created_at"),
        }),
    )
    return target, body


def load_current_receipt(run_root: Path) -> dict | None:
    pointer = load_current(run_root)
    if not pointer:
        return None
    target = paths(run_root).receipts / str(pointer.get("receipt") or "")
    if not target.is_file():
        return None
    return read_json(target)


def verify_receipt_digest(receipt: dict) -> list[str]:
    claimed = receipt.get("receipt_digest")
    body = {k: v for k, v in receipt.items() if k != "receipt_digest"}
    actual = digest_text(canonical_json(body))
    if claimed != actual:
        return ["receipt_digest mismatch: claimed %s, computed %s" % (claimed, actual)]
    return []
