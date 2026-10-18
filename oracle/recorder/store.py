"""Immutable, content-addressed evidence store for the P4 recorder.

This is the store the recorder publishes into and the offline analysis reads from.
It is deliberately small and independent: it does **not** reuse the P3 run-receipt
directory as an event CAS. It adapts the create-only atomic-publication discipline
already proven in ``reverse_engineering/renderer_extract.py`` - write a private
staging file, then ``os.link`` it into place, which cannot overwrite a name that
already exists - and pairs it with the record schema's canonical hashing.

A manifest references every blob by digest. Loading a store validates the manifest,
and ``verify`` is the load-time integrity gate: every referenced blob must exist,
match its digest, and (for records) re-validate against the schema. Nothing here
opens a socket, a game process, or the EU IV binary.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.recorder.schema import (
    CANONICALIZATION_VERSION,
    SchemaError,
    canonical_bytes,
    record_digest,
    validate_payload,
)

EVIDENCE_MANIFEST_VERSION = "eu4-oracle-evidence-manifest/v1"

MANIFEST_NAME = "manifest.json"
BLOBS_DIR = "blobs"

_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")


class StoreError(RuntimeError):
    """The evidence store is malformed, corrupt, or internally inconsistent."""


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def write_create_only(path: Path, payload: bytes) -> str:
    """Publish ``payload`` at ``path`` atomically, refusing to overwrite a name.

    Returns ``"created"`` or ``"unchanged"`` (an identical file already exists). A
    *different* file at the destination is a corruption, not a conflict to resolve
    silently, so it raises.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == payload:
            return "unchanged"
        raise StoreError(f"destination occupied with different content: {path}")
    fd, staging = tempfile.mkstemp(dir=str(path.parent), prefix=".blob.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(staging, path)
            return "created"
        except FileExistsError:
            if path.is_file() and path.read_bytes() == payload:
                return "unchanged"
            raise StoreError(f"concurrent publication conflict: {path}") from None
    finally:
        Path(staging).unlink(missing_ok=True)


class EvidenceStore:
    """A directory of immutable content-addressed blobs plus a sealed manifest."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.blobs_dir = self.root / BLOBS_DIR
        self.manifest_path = self.root / MANIFEST_NAME
        self._blobs: dict[str, int] = {}
        self._records: list[str] = []
        if self.manifest_path.exists():
            self._load_manifest()

    # --- blobs -----------------------------------------------------------------

    def blob_path(self, digest: str) -> Path:
        return self.blobs_dir / digest

    def put_blob(self, data: bytes) -> str:
        digest = _sha256_bytes(data)
        self._put_blob_with_digest(digest, data)
        return digest

    def _put_blob_with_digest(self, digest: str, data: bytes) -> None:
        write_create_only(self.blob_path(digest), data)
        self._blobs[digest] = len(data)

    def get_blob(self, digest: str) -> bytes:
        path = self.blob_path(digest)
        if not path.is_file():
            raise StoreError(f"blob not found: {digest}")
        data = path.read_bytes()
        if _sha256_bytes(data) != digest:
            raise StoreError(f"blob digest mismatch: {digest}")
        return data

    # --- records ---------------------------------------------------------------

    def add_record(self, payload: dict[str, Any]) -> str:
        """Validate, content-address and store an observation; return its digest."""
        validate_payload(payload)
        digest = record_digest(payload)
        self._put_blob_with_digest(digest, canonical_bytes(payload))
        if digest not in self._records:
            self._records.append(digest)
        return digest

    @property
    def records(self) -> list[str]:
        return list(self._records)

    def get_record(self, digest: str) -> dict[str, Any]:
        data = self.get_blob(digest)
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError(f"record blob is not canonical JSON: {digest}") from exc
        try:
            validate_payload(payload)
            if record_digest(payload) != digest:
                raise StoreError(f"record digest mismatch: {digest}")
        except SchemaError as exc:
            raise StoreError(f"record blob fails schema validation: {digest}: {exc}") from exc
        return payload

    # --- manifest --------------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        payload = {
            "canonicalization_version": CANONICALIZATION_VERSION,
            "blobs": {digest: {"size": size} for digest, size in sorted(self._blobs.items())},
            "records": list(self._records),
        }
        return {
            "schema_version": EVIDENCE_MANIFEST_VERSION,
            "manifest_sha256": _sha256_bytes(canonical_bytes(payload)),
            "payload": payload,
        }

    def write_manifest(self) -> Path:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_bytes(canonical_bytes(self.manifest()) + b"\n")
        return self.manifest_path

    def _load_manifest(self) -> None:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError(f"cannot read manifest {self.manifest_path}: {exc}") from exc
        self._validate_manifest(manifest)
        payload = manifest["payload"]
        self._blobs = {digest: entry["size"] for digest, entry in payload["blobs"].items()}
        self._records = list(payload["records"])

    @staticmethod
    def _validate_manifest(manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise StoreError("manifest is not an object")
        if manifest.get("schema_version") != EVIDENCE_MANIFEST_VERSION:
            raise StoreError(f"unsupported manifest schema: {manifest.get('schema_version')!r}")
        payload = manifest.get("payload")
        if not isinstance(payload, dict):
            raise StoreError("manifest has no payload object")
        digest = manifest.get("manifest_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise StoreError("manifest has no content digest")
        if _sha256_bytes(canonical_bytes(payload)) != digest:
            raise StoreError("manifest digest mismatch")
        blobs = payload.get("blobs")
        if not isinstance(blobs, dict):
            raise StoreError("manifest blobs must be an object")
        for blob_digest, entry in blobs.items():
            if not _DIGEST.match(blob_digest):
                raise StoreError(f"manifest blob key is not a sha256: {blob_digest!r}")
            if not isinstance(entry, dict):
                raise StoreError(f"manifest blob entry is not an object: {blob_digest}")
            size = entry.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise StoreError(f"manifest blob size is malformed: {blob_digest}")
        records = payload.get("records")
        if not isinstance(records, list) or not all(isinstance(r, str) for r in records):
            raise StoreError("manifest records must be a list of digests")

    def verify(self) -> None:
        """Load-time integrity: every referenced blob exists, matches, and re-validates."""
        for digest, size in self._blobs.items():
            path = self.blob_path(digest)
            if not path.is_file():
                raise StoreError(f"missing blob: {digest}")
            data = path.read_bytes()
            if _sha256_bytes(data) != digest:
                raise StoreError(f"blob digest mismatch: {digest}")
            if len(data) != size:
                raise StoreError(f"blob size mismatch: {digest}")
        for digest in self._records:
            if digest not in self._blobs:
                raise StoreError(f"record {digest} has no blob entry")
            self.get_record(digest)
