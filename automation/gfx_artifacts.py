"""Bounded, content-addressed diagnostic artifact storage.

Diagnostic runs produce many large framebuffers and few small structured
records.  The default policy is therefore:

* small structured records are always retained;
* raw payloads are content-addressed and deduplicated;
* a configurable byte budget evicts non-preserved payloads oldest-first;
* evidence attached to an established failure is marked ``preserved`` and is
  never evicted, even if that makes the store exceed its budget (which is
  reported, not hidden);
* a payload that cannot be stored is recorded in ``dropped`` with its digest
  and reason -- never silently omitted.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "gfx-artifact-store/v1"
INDEX_NAME = "artifact-index.json"
MANIFEST_NAME = "artifact-manifest.json"
DEFAULT_MAX_BYTES = 256 * 1024 * 1024


class ArtifactError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass
class StoredArtifact:
    sha256: str
    bytes: int
    kind: str
    preserved: bool
    first_source: str
    stored_at: str
    refs: int = 1
    logical_name: str = ""

    def as_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "bytes": self.bytes,
            "kind": self.kind,
            "preserved": self.preserved,
            "first_source": self.first_source,
            "stored_at": self.stored_at,
            "refs": self.refs,
            "logical_name": self.logical_name,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "StoredArtifact":
        return cls(
            sha256=str(value["sha256"]),
            bytes=int(value["bytes"]),
            kind=str(value.get("kind", "misc")),
            preserved=bool(value.get("preserved", False)),
            first_source=str(value.get("first_source", "")),
            stored_at=str(value.get("stored_at", "")),
            refs=int(value.get("refs", 1)),
            logical_name=str(value.get("logical_name", "")),
        )


class ArtifactStore:
    """One run's bounded content-addressed evidence store."""

    def __init__(self, root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES,
                 enabled: bool = True):
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ArtifactError("limit", "max_bytes must be a positive integer")
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.max_bytes = max_bytes
        self.enabled = enabled
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects.mkdir(parents=True, exist_ok=True)
        self.index: dict[str, StoredArtifact] = {}
        self.dropped: list[dict] = []
        self.over_limit = False
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        path = self.root / INDEX_NAME
        if not path.is_file():
            return
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            return
        for entry in value.get("artifacts", []):
            if isinstance(entry, dict):
                try:
                    item = StoredArtifact.from_dict(entry)
                except (KeyError, TypeError, ValueError):
                    continue
                self.index[item.sha256] = item
        dropped = value.get("dropped")
        if isinstance(dropped, list):
            self.dropped = [row for row in dropped if isinstance(row, dict)]

    def _save(self) -> None:
        _atomic_json(
            self.root / INDEX_NAME,
            {
                "schema": SCHEMA,
                "updated_utc": _now(),
                "max_bytes": self.max_bytes,
                "over_limit": self.over_limit,
                "artifacts": [self.index[key].as_dict() for key in sorted(self.index)],
                "dropped": self.dropped,
                "summary": self.summary(),
            },
        )

    # -- storage ----------------------------------------------------------
    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest

    def _total_bytes(self) -> int:
        return sum(item.bytes for item in self.index.values())

    def _evict_for(self, incoming: int) -> int:
        """Evict non-preserved, singly-referenced objects oldest-first.

        Returns the bytes freed.  Preserved evidence and shared/deduplicated
        objects are never evicted, so a failure package cannot lose inputs.
        """
        freed = 0
        candidates = sorted(
            (item for item in self.index.values() if not item.preserved and item.refs <= 1),
            key=lambda item: item.stored_at,
        )
        for item in candidates:
            if self._total_bytes() + incoming <= self.max_bytes:
                break
            path = self._object_path(item.sha256)
            try:
                path.unlink()
            except OSError:
                continue
            self.index.pop(item.sha256, None)
            freed += item.bytes
        return freed

    def _drop(self, digest: str, size: int, kind: str, source: str, reason: str) -> None:
        self.dropped.append(
            {
                "sha256": digest,
                "bytes": size,
                "kind": kind,
                "source": source,
                "reason": reason,
                "at": _now(),
            }
        )

    def put_bytes(self, data: bytes, *, kind: str = "misc", preserved: bool = False,
                  source: str = "", logical_name: str = "") -> StoredArtifact | None:
        if not isinstance(data, (bytes, bytearray)):
            raise ArtifactError("payload", "data must be bytes")
        data = bytes(data)
        digest = sha256_bytes(data)
        size = len(data)
        existing = self.index.get(digest)
        if existing is not None:
            existing.refs += 1
            if preserved:
                existing.preserved = True
            if logical_name and not existing.logical_name:
                existing.logical_name = logical_name
            self._save()
            return existing
        if not self.enabled:
            self._drop(digest, size, kind, source, "storage disabled")
            self._save()
            return None
        projected = self._total_bytes() + size
        if projected > self.max_bytes:
            self._evict_for(size)
            projected = self._total_bytes() + size
        if projected > self.max_bytes and not preserved:
            self._drop(digest, size, kind, source,
                       f"storage budget {self.max_bytes} bytes would be exceeded")
            self._save()
            return None
        object_path = self._object_path(digest)
        object_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = object_path.with_name(object_path.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, object_path)
        item = StoredArtifact(
            sha256=digest,
            bytes=size,
            kind=kind,
            preserved=preserved,
            first_source=source,
            stored_at=_now(),
            refs=1,
            logical_name=logical_name,
        )
        self.index[digest] = item
        if self._total_bytes() > self.max_bytes:
            self.over_limit = True
        self._save()
        return item

    def put_file(self, path: Path, *, kind: str = "misc", preserved: bool = False,
                 logical_name: str = "") -> StoredArtifact | None:
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactError("unreadable", f"{path}: {exc}") from exc
        return self.put_bytes(data, kind=kind, preserved=preserved,
                              source=str(path), logical_name=logical_name)

    def get(self, digest: str) -> Path:
        if digest not in self.index:
            raise ArtifactError("missing", digest)
        path = self._object_path(digest)
        if not path.is_file():
            raise ArtifactError("missing_object", digest)
        return path

    def read(self, digest: str) -> bytes:
        return self.get(digest).read_bytes()

    # -- reporting --------------------------------------------------------
    def summary(self) -> dict:
        total = self._total_bytes()
        dedup_saved = sum(
            item.bytes * (item.refs - 1) for item in self.index.values()
        )
        return {
            "objects": len(self.index),
            "bytes": total,
            "max_bytes": self.max_bytes,
            "preserved_objects": sum(1 for item in self.index.values() if item.preserved),
            "preserved_bytes": sum(item.bytes for item in self.index.values() if item.preserved),
            "deduplicated_bytes_saved": dedup_saved,
            "dropped": len(self.dropped),
            "over_limit": self.over_limit or total > self.max_bytes,
        }

    def write_manifest(self, names: dict[str, str] | None = None) -> Path:
        """Write a logical-name manifest for the retained artifacts."""
        mapping = names or {}
        rows = []
        for digest in sorted(self.index):
            item = self.index[digest]
            rows.append(item.as_dict() | {"logical_name": mapping.get(digest, item.logical_name)})
        value = {
            "schema": SCHEMA,
            "created_utc": _now(),
            "summary": self.summary(),
            "artifacts": rows,
            "dropped": list(self.dropped),
        }
        path = self.root / MANIFEST_NAME
        _atomic_json(path, value)
        return path
