"""Content-addressed receipts for Oracle P3 runs.

A receipt is *sealed* when it is content-addressed and can be verified against a
trusted reference: the digest is computed over the canonical payload, not merely
stored alongside it. All referenced artifacts are identified by hash.

Receipt verification is reproducible. The model trajectory is not: rerunning an
LLM at identical settings is not expected to reproduce the same edits or tool
calls, so the receipt records what happened, not a promise that it can be
replayed. Bulky logs stay outside the committed documentation.
"""
from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.core import canonical_bytes

RECEIPT_SCHEMA_VERSION = "eu4-oracle-receipt/v1"


class ReceiptError(RuntimeError):
    """A receipt is missing, malformed or does not verify."""


def file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_digest(payload: dict[str, Any]) -> str:
    return sha256(canonical_bytes(payload)).hexdigest()


def seal(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a payload into a verifiable, content-addressed receipt."""
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "sha256": receipt_digest(payload),
        "payload": payload,
    }


def verify(receipt: dict[str, Any], *, expected_sha256: str | None = None) -> None:
    """Raise ``ReceiptError`` unless the receipt is well-formed and intact."""
    if not isinstance(receipt, dict):
        raise ReceiptError("receipt is not an object")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise ReceiptError(f"unexpected receipt schema: {receipt.get('schema_version')!r}")
    payload = receipt.get("payload")
    if not isinstance(payload, dict):
        raise ReceiptError("receipt has no payload object")
    digest = receipt.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ReceiptError("receipt has no content digest")
    recomputed = receipt_digest(payload)
    if recomputed != digest:
        raise ReceiptError(f"receipt digest mismatch: {digest} != {recomputed}")
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ReceiptError(f"receipt does not match the trusted reference {expected_sha256}")


def write(path: Path, receipt: dict[str, Any]) -> Path:
    """Atomically write a sealed receipt."""
    verify(receipt)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=path.name, suffix=".tmp", delete=False
    )
    try:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, path)
    return path


def load(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptError(f"cannot read receipt {path}: {exc}") from exc
    verify(receipt)
    return receipt
