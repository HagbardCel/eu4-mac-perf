#!/usr/bin/env python3
"""Publish and resolve a verified sealed case across worktree disposal.

A capture identity is only useful if it can be found after the producing
worktree is gone.  This module keeps a small operator-owned locator document
per published case under an explicit evidence root:

* the case bytes are copied once to ``<root>/cases/<key>/case``;
* the copy is verified before it is published by atomic rename;
* ``<root>/locators/<key>.json`` records the capture identity, manifest digest,
  relative path, verification state and limitations;
* lookup is bounded and read-only and reports ``unknown`` for an absent locator
  rather than claiming that evidence was deleted.

It deliberately does not become a general archive service: callers name the
root, publication is idempotent per key, and no historical artifact is moved.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

from automation import gfx_replay_case

SCHEMA = "gfx-case-locator/v1"
LOCATOR_DIR = "locators"
CASES_DIR = "cases"
MAX_LOCATORS = 5000
_KEY = re.compile(r"^[A-Za-z0-9._-]{1,180}$")


class CaseLocatorError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha(path: Path) -> str | None:
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def case_identity(case_dir: Path | str) -> dict:
    """Capture and manifest identity of one packaged case.

    ``seal_sha256`` is the digest of the frame seal file, which is the stable
    publication key.  A legacy package without a seal keeps its manifest
    identity and an explicit limitation.
    """
    case_dir = Path(case_dir)
    manifest = _json(case_dir / "case.json")
    seal_path = case_dir / "capture" / "frame-seal.json"
    seal = _json(seal_path) if seal_path.is_file() else {}
    return {
        "case_id": manifest.get("case_id"),
        "session_id": seal.get("session_id"),
        "capture_frame_id": seal.get("capture_frame_id"),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "seal_sha256": _sha(seal_path),
        "manifest_sha256": _sha(case_dir / "case.json"),
        "schema": manifest.get("schema"),
    }


def locator_key(identity: dict) -> str:
    """Stable, filename-safe key: seal digest when present, else manifest."""
    seal = identity.get("seal_sha256")
    if isinstance(seal, str) and len(seal) == 64:
        session = re.sub(r"[^A-Za-z0-9._-]+", "-",
                         str(identity.get("session_id") or "session"))[:80]
        frame = re.sub(r"[^A-Za-z0-9._-]+", "-",
                       str(identity.get("capture_frame_id") or "frame"))[:40]
        return f"{session}-{frame}-{seal[:16]}"
    manifest = identity.get("manifest_sha256")
    if not isinstance(manifest, str) or len(manifest) != 64:
        raise CaseLocatorError("case_identity", "case has no usable identity")
    return f"manifest-{manifest[:24]}"


def _limitations(identity: dict, verification: dict) -> list[str]:
    limitations: list[str] = []
    if not identity.get("seal_sha256"):
        limitations.append(
            "legacy case has no frame seal; the locator is keyed by the case "
            "manifest digest only")
    producer = (verification.get("producer_identity") or {}).get("status")
    if producer == "legacy_missing":
        limitations.append(
            "legacy_missing: no producer receipt; do not graft a producer "
            "identity onto these bytes")
    elif producer and producer not in ("verified", "ok"):
        limitations.append(f"producer identity is {producer}")
    if not verification.get("independently_reproducible"):
        limitations.append("portable verification does not claim independent "
                           "reproduction")
    return limitations


def _verification_summary(verification: dict) -> dict:
    return {
        "ok": verification.get("ok"),
        "files": verification.get("files"),
        "graph_status": verification.get("graph_status"),
        "reproduction_status": verification.get("reproduction_status"),
        "independently_reproducible": verification.get(
            "independently_reproducible"),
        "producer_identity": (verification.get("producer_identity") or {}).get(
            "status"),
        "problems": (verification.get("problems") or [])[:8],
    }


def _verify(case_dir: Path) -> dict:
    try:
        return gfx_replay_case.verify(case_dir)
    except Exception as exc:  # noqa: BLE001 - every verifier failure is closed
        return {"ok": False,
                "problems": [f"{type(exc).__name__}: {exc}"]}


def _write_locator(root: Path, key: str, value: dict) -> Path:
    directory = root / LOCATOR_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    os.replace(partial, path)
    return path


def publish(case_dir: Path | str, evidence_root: Path | str, *,
            originating_run: str | None = None) -> dict:
    """Copy and verify one case, then atomically publish its locator.

    Re-publishing the same key verifies the existing copy and reuses it.  A
    different manifest under the same seal is a conflict, never an overwrite.
    An interrupted copy leaves the source usable and no locator behind.
    """
    case_dir = Path(case_dir).expanduser().resolve()
    root = Path(evidence_root).expanduser().resolve()
    if not case_dir.is_dir():
        raise CaseLocatorError("case_missing", str(case_dir))
    verification = _verify(case_dir)
    if verification.get("ok") is not True:
        raise CaseLocatorError(
            "case_not_verified",
            "; ".join(str(item) for item in
                      (verification.get("problems") or ["unknown"])[:8]))
    identity = case_identity(case_dir)
    key = locator_key(identity)
    cases = root / CASES_DIR
    cases.mkdir(parents=True, exist_ok=True)
    target = cases / key / "case"
    reused = target.is_dir()
    if reused:
        existing = _verify(target)
        if existing.get("ok") is not True:
            raise CaseLocatorError(
                "published_copy_invalid",
                "; ".join(str(item) for item in
                          (existing.get("problems") or ["unknown"])[:8]))
        published_identity = case_identity(target)
        if (published_identity.get("manifest_sha256")
                != identity.get("manifest_sha256")):
            raise CaseLocatorError(
                "locator_conflict",
                f"{key} already names different case bytes")
    else:
        staging_parent = cases / f".publish-{key}-{os.getpid()}-{uuid.uuid4().hex}"
        staging = staging_parent / "case"
        try:
            shutil.copytree(case_dir, staging)
            copied = _verify(staging)
            if copied.get("ok") is not True:
                raise CaseLocatorError(
                    "copied_case_invalid",
                    "; ".join(str(item) for item in
                              (copied.get("problems") or ["unknown"])[:8]))
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(staging, target)
            except OSError as exc:
                # A concurrent publisher may have won the same key.  Verify
                # the winner instead of reporting a spurious failure.
                if not target.is_dir():
                    raise CaseLocatorError("publish_race", str(exc)) from exc
                reused = True
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
    verification_now = _verify(target)
    if verification_now.get("ok") is not True:
        raise CaseLocatorError(
            "published_copy_invalid",
            "; ".join(str(item) for item in
                      (verification_now.get("problems") or ["unknown"])[:8]))
    locator = {
        "schema": SCHEMA,
        "key": key,
        "identity": identity,
        "case_id": identity.get("case_id"),
        "relative_path": str(target.relative_to(root)),
        "verification": _verification_summary(verification_now),
        "limitations": _limitations(identity, verification_now),
        "originating_run": originating_run,
        "published_utc": _now(),
        "reused": reused,
    }
    path = _write_locator(root, key, locator)
    locator["locator_path"] = str(path)
    return locator


def _locators(root: Path) -> list[dict]:
    directory = root / LOCATOR_DIR
    if not directory.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(directory.glob("*.json"))[:MAX_LOCATORS]:
        value = _json(path)
        if value.get("schema") != SCHEMA:
            continue
        value["locator_path"] = str(path)
        found.append(value)
    return found


def _resolve_match(root: Path, locator: dict) -> dict:
    relative = str(locator.get("relative_path") or "")
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return dict(locator, state="invalid_path",
                    detail="locator path escapes the evidence root")
    if not target.is_dir():
        return dict(locator, state="missing_payload", resolved_path=str(target))
    verification = _verify(target)
    identity = case_identity(target)
    expected = locator.get("identity") or {}
    if verification.get("ok") is not True:
        return dict(locator, state="digest_mismatch", resolved_path=str(target),
                    verification=_verification_summary(verification),
                    detail="; ".join(str(item) for item in
                                     (verification.get("problems") or [])[:4]))
    if (expected.get("manifest_sha256")
            and identity.get("manifest_sha256")
            != expected.get("manifest_sha256")):
        return dict(locator, state="digest_mismatch", resolved_path=str(target),
                    detail="case manifest no longer matches the locator")
    if (expected.get("seal_sha256")
            and identity.get("seal_sha256") != expected.get("seal_sha256")):
        return dict(locator, state="digest_mismatch", resolved_path=str(target),
                    detail="frame seal no longer matches the locator")
    return dict(locator, state="available", resolved_path=str(target),
                verification=_verification_summary(verification))


def locate(evidence_root: Path | str, *, session_id: str | None = None,
           capture_frame_id: int | None = None, seal_sha256: str | None = None,
           case_id: str | None = None) -> dict:
    """Resolve one published case without searching the filesystem.

    States are ``available``, ``missing_payload`` (locator but no bytes),
    ``digest_mismatch`` (bytes changed), ``root_unavailable`` and ``unknown``
    (no matching locator; never equated with deletion).
    """
    root = Path(evidence_root).expanduser().resolve()
    query = {
        "session_id": session_id,
        "capture_frame_id": capture_frame_id,
        "seal_sha256": seal_sha256,
        "case_id": case_id,
    }
    if not root.is_dir():
        return {"schema": SCHEMA, "root": str(root), "state": "root_unavailable",
                "query": query, "matches": []}
    matches = []
    for locator in _locators(root):
        identity = locator.get("identity") or {}
        if (session_id is not None
                and str(identity.get("session_id")) != str(session_id)):
            continue
        if (capture_frame_id is not None
                and str(identity.get("capture_frame_id"))
                != str(capture_frame_id)):
            continue
        if seal_sha256 is not None and identity.get("seal_sha256") != seal_sha256:
            continue
        if case_id is not None and str(locator.get("case_id")) != str(case_id):
            continue
        matches.append(_resolve_match(root, locator))
    state = "unknown"
    if matches:
        for candidate in ("available", "missing_payload", "digest_mismatch",
                          "invalid_path"):
            if any(item["state"] == candidate for item in matches):
                state = candidate
                break
    return {
        "schema": SCHEMA,
        "root": str(root),
        "state": state,
        "query": query,
        "matches": matches,
        "read_only": True,
    }


def summaries(evidence_root: Path | str) -> list[dict]:
    """Locator metadata for discovery; never hashes the full case payload.

    A discovery pass must stay bounded, so it reports the state recorded at
    publication plus whether the payload directory still exists.  ``locate``
    re-verifies bytes when a caller is about to use the case.
    """
    root = Path(evidence_root).expanduser().resolve()
    if not root.is_dir():
        return []
    rows: list[dict] = []
    for locator in _locators(root):
        relative = str(locator.get("relative_path") or "")
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
            inside = True
        except ValueError:
            inside = False
        state = ("available" if inside and target.is_dir()
                 else "invalid_path" if not inside else "missing_payload")
        identity = locator.get("identity") or {}
        rows.append({
            "kind": "case_locator",
            "key": locator.get("key"),
            "case_id": locator.get("case_id"),
            "session_id": identity.get("session_id"),
            "capture_frame_id": identity.get("capture_frame_id"),
            "event_stream_sha256": identity.get("event_stream_sha256"),
            "seal_sha256": identity.get("seal_sha256"),
            "manifest_sha256": identity.get("manifest_sha256"),
            "relative_path": relative,
            "resolved_path": str(target),
            "state": state,
            "verification": locator.get("verification"),
            "limitations": locator.get("limitations") or [],
            "originating_run": locator.get("originating_run"),
            "published_utc": locator.get("published_utc"),
            "locator_path": locator.get("locator_path"),
            "read_only": True,
        })
    return rows


def inventory(evidence_root: Path | str) -> dict:
    """Bounded summary of published locators under one root."""
    root = Path(evidence_root).expanduser().resolve()
    if not root.is_dir():
        return {"schema": SCHEMA, "root": str(root),
                "state": "root_unavailable", "locators": []}
    rows = [_resolve_match(root, locator) for locator in _locators(root)]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "schema": SCHEMA,
        "root": str(root),
        "state": "available" if rows else "empty",
        "counts": counts,
        "locators": rows,
        "read_only": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--case", required=True, type=Path)
    publish_parser.add_argument("--root", required=True, type=Path)
    publish_parser.add_argument("--originating-run", default=None)
    locate_parser = sub.add_parser("locate")
    locate_parser.add_argument("--root", required=True, type=Path)
    locate_parser.add_argument("--session-id", default=None)
    locate_parser.add_argument("--capture-frame-id", type=int, default=None)
    locate_parser.add_argument("--seal-sha256", default=None)
    locate_parser.add_argument("--case-id", default=None)
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "publish":
            value = publish(args.case, args.root,
                            originating_run=args.originating_run)
            status = 0
        elif args.operation == "locate":
            value = locate(
                args.root, session_id=args.session_id,
                capture_frame_id=args.capture_frame_id,
                seal_sha256=args.seal_sha256, case_id=args.case_id)
            status = 0 if value["state"] == "available" else 2
        else:
            value = inventory(args.root)
            status = 0
    except CaseLocatorError as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "kind": exc.kind, "detail": exc.detail}))
        return 1
    print(json.dumps(value, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
