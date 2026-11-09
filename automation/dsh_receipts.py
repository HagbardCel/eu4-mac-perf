"""Durable DSH qualification receipts, counters and run-root layout.

Receipts are records, not authority: ``automation.dsh_bridge`` re-verifies every
recomputable identity before it lets anything depend on a stored receipt. What
lives here is storage and hashing only, so it can be tested without a campaign.
"""

from __future__ import annotations

import json
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

SCHEMA = "eu4-dsh-receipt/v1"
CURRENT_SCHEMA = "eu4-dsh-current/v1"
COUNTERS_SCHEMA = "eu4-dsh-counters/v1"
JOURNAL_SCHEMA = "eu4-dsh-journal/v1"
DEFAULT_COUNTERS = {
    "schema": COUNTERS_SCHEMA,
    "validations": 0,
    "identical_retries": 0,
    "policy_stops": 0,
    "integrity_refusals": 0,
    "last_verdict": None,
    "last_diagnostics_digest": None,
    "last_candidate_digest": None,
    "finalizations": 0,
    "last_finalize_verdict": None,
    "elapsed_seconds": 0.0,
    "started_at": None,
}


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
    def journal(self) -> Path:
        return self.run_root / "journal.jsonl"

    @property
    def events(self) -> Path:
        return self.run_root / "events.jsonl"

    @property
    def validation(self) -> Path:
        return self.run_root / "validation"

    @property
    def quarantine(self) -> Path:
        return self.run_root / "validation" / "quarantine"

    def ensure(self) -> None:
        for directory in (self.run_root, self.receipts, self.validation,
                          self.quarantine):
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
    append_journal(run_root, {
        "kind": "receipt",
        "name": target.name,
        "receipt_digest": body["receipt_digest"],
        "identity_digest": identity_digest(identities),
        "verdict": (body.get("outcome") or {}).get("checker_verdict"),
        "classification": (body.get("outcome") or {}).get("bridge_classification"),
    })
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


def load_journal(run_root: Path) -> list[dict]:
    path = paths(run_root).journal
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"corrupt": line})
    return rows


def append_journal(run_root: Path, entry: dict) -> dict:
    """Append one linked record. Any party able to append can also forge, so this
    is tamper *evidence* for the ordinary case, not a security boundary."""
    rows = load_journal(run_root)
    previous = rows[-1] if rows else None
    body = dict(entry)
    body["schema"] = JOURNAL_SCHEMA
    body["seq"] = (previous or {}).get("seq", 0) + 1
    body["prev"] = (previous or {}).get("link")
    body["link"] = digest_text(canonical_json(body))
    path = paths(run_root).journal
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(body, sort_keys=True) + "\n")
    return body


def verify_journal(run_root: Path) -> list[str]:
    """Recompute the chain; report the first structural inconsistency."""
    problems: list[str] = []
    previous_link = None
    expected_seq = 1
    for index, row in enumerate(load_journal(run_root)):
        if "corrupt" in row:
            problems.append(f"journal line {index + 1} is not JSON")
            continue
        claimed = row.get("link")
        body = {k: v for k, v in row.items() if k != "link"}
        if digest_text(canonical_json(body)) != claimed:
            problems.append(f"journal line {index + 1} does not match its link")
        if row.get("prev") != previous_link:
            problems.append(f"journal line {index + 1} breaks the chain")
        if row.get("seq") != expected_seq:
            problems.append(f"journal line {index + 1} has sequence {row.get('seq')}")
        previous_link = claimed
        expected_seq += 1
    return problems


def journal_problem(run_root: Path, receipt: dict) -> list[str]:
    """The current receipt must be the one the journal recorded."""
    problems = verify_journal(run_root)
    if problems:
        return problems
    pointer = load_current(run_root) or {}
    name = str(pointer.get("receipt") or "")
    rows = [row for row in load_journal(run_root)
            if row.get("kind") == "receipt" and row.get("name") == name]
    if not rows:
        return [f"receipt {name} is not recorded in the append-only journal"]
    if rows[-1].get("receipt_digest") != receipt.get("receipt_digest"):
        return [f"receipt {name} does not match its journal record"]
    return []
