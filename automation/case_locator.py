#!/usr/bin/env python3
"""Publish and resolve a verified sealed case across worktree disposal.

A capture identity is only useful if it can be found after the producing
worktree is gone.  This module keeps a small operator-owned locator document
per published case under an explicit evidence root:

* the case bytes are copied once to ``<root>/cases/<key>/case``;
* the copy is verified before publication by atomic rename;
* ``<root>/locators/<key>.json`` records the capture identity, manifest digest,
  relative path, recorded verification, limitations and size;
* lookup is bounded and read-only and reports ``unknown`` for an absent locator
  rather than claiming that evidence was deleted.

Sequence packages resolve the diagnosed target frame through
``sequence.json`` (``frames/<index>/frame-seal.json``), not the single-frame
``capture/frame-seal.json`` layout.

Inventory and discovery are metadata-only: they read locator documents and
check payload existence, but do not hash the full package.  Full verification
is opt-in for a selected case.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from automation import gfx_replay_case

SCHEMA = "gfx-case-locator/v1"
LOCATOR_DIR = "locators"
CASES_DIR = "cases"
MAX_LOCATORS = 5000
DEFAULT_SIZE_BUDGET = 4 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE = 64 * 1024 * 1024
DEFAULT_LIMIT = 20


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


def _tree_stats(root: Path) -> tuple[int, int]:
    """Return (regular-file bytes, file count) without following escapes."""
    total = 0
    count = 0
    root = Path(root)
    if not root.is_dir():
        return 0, 0
    for directory, _subdirs, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
    return total, count


def _symlink_problem(root: Path) -> str | None:
    """Reject symlinks so counting and copying have identical semantics."""
    root = Path(root)
    for directory, subdirs, names in os.walk(root):
        for name in list(subdirs) + list(names):
            path = Path(directory) / name
            if path.is_symlink():
                try:
                    relative = path.relative_to(root).as_posix()
                except ValueError:
                    relative = str(path)
                return (f"case contains a symlink at {relative}; publish a "
                        "self-contained copy instead")
    return None


def _tree_inventory(root: Path) -> dict[str, tuple[int, str]]:
    """Regular files under ``root`` with relative path, size and digest.

    Symlinks are rejected rather than followed.  The inventory is taken before
    copying and re-computed on the staged copy; a path that was swapped for a
    symlink or changed bytes during the copy window therefore cannot be
    published as a regular file.
    """
    root = Path(root)
    inventory: dict[str, tuple[int, str]] = {}

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = f"{prefix}{entry.name}"
                if entry.is_symlink():
                    raise CaseLocatorError(
                        "symlink_source",
                        f"case contains a symlink at {relative}; publish a "
                        "self-contained copy instead")
                if entry.is_dir(follow_symlinks=False):
                    walk(Path(entry.path), relative + "/")
                elif entry.is_file(follow_symlinks=False):
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                        digest = _sha(Path(entry.path))
                    except OSError as exc:
                        raise CaseLocatorError(
                            "copy_unreadable",
                            f"cannot inventory {relative}: {exc}") from exc
                    inventory[relative] = (size, digest or "")

    if root.is_dir():
        walk(root, "")
    return inventory


def _existing_ancestor(path: Path) -> Path:
    current = Path(path)
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _identity_conflict(expected: dict, actual: dict) -> str | None:
    """Compare the complete publication identity, not just verification."""
    for key in ("case_id", "schema", "session_id", "capture_frame_id",
                "event_stream_sha256", "seal_sha256", "manifest_sha256"):
        if str(expected.get(key)) != str(actual.get(key)):
            return (f"{key} differs: requested {expected.get(key)!r}, "
                    f"published {actual.get(key)!r}")
    return None


def case_identity(case_dir: Path | str) -> dict:
    """Capture and manifest identity of one packaged case.

    ``seal_sha256`` is the digest of the authoritative seal file, which is the
    stable publication key.  A sequence case resolves its target frame seal
    through the shared ``gfx_replay_case`` helper.  A legacy package without a
    seal keeps its manifest identity and an explicit limitation.
    """
    case_dir = Path(case_dir).resolve()
    manifest = _json(case_dir / "case.json")
    seal_path = gfx_replay_case.case_capture_seal_path(case_dir, manifest)
    seal = _json(seal_path) if seal_path is not None else {}
    seal_file = None
    if seal_path is not None:
        try:
            seal_file = seal_path.relative_to(case_dir).as_posix()
        except ValueError:
            seal_file = None
    return {
        "case_id": manifest.get("case_id"),
        "session_id": seal.get("session_id"),
        "capture_frame_id": seal.get("capture_frame_id"),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "seal_sha256": _sha(seal_path) if seal_path is not None else None,
        "seal_file": seal_file,
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
    elif identity.get("seal_file") not in (None, "capture/frame-seal.json"):
        limitations.append(
            f"sequence case seal resolved from {identity.get('seal_file')}")
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


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _locator_lock(root: Path, key: str):
    """Serialize the read-modify-write of one locator document."""
    directory = root / LOCATOR_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".{key}.lock"
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _write_locator(root: Path, key: str, value: dict) -> Path:
    """Write one locator through an exclusively created unique temp file."""
    directory = root / LOCATOR_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    temporary = directory / f".{key}.{uuid.uuid4().hex}.tmp"
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, path)
            _fsync_directory(directory)
        except OSError as exc:
            raise CaseLocatorError(
                "locator_write",
                f"could not publish locator {path}: {exc}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return path


def _preflight_space(root: Path, source_bytes: int, *,
                     size_budget: int, min_free: int) -> None:
    if source_bytes > size_budget:
        raise CaseLocatorError(
            "size_budget",
            f"case is {source_bytes} bytes but the publication budget is "
            f"{size_budget} bytes; publish the smaller diagnostic package or "
            "raise --size-budget explicitly")
    usage = shutil.disk_usage(_existing_ancestor(root))
    free = usage.free if hasattr(usage, "free") else int(usage[2])
    if free < source_bytes + max(0, min_free):
        raise CaseLocatorError(
            "insufficient_space",
            f"case needs {source_bytes} bytes plus {max(0, min_free)} free "
            f"bytes but {_existing_ancestor(root)} has {free} bytes free; "
            "free space or choose another --root; the source is unchanged")


def _adopt_existing(target: Path, identity: dict, key: str) -> dict:
    """Verify and identity-check an already published copy."""
    symlink = _symlink_problem(target)
    if symlink is not None:
        raise CaseLocatorError("symlink_source", symlink)
    existing = _verify(target)
    if existing.get("ok") is not True:
        raise CaseLocatorError(
            "published_copy_invalid",
            "; ".join(str(item) for item in
                      (existing.get("problems") or ["unknown"])[:8]))
    conflict = _identity_conflict(identity, case_identity(target))
    if conflict is not None:
        raise CaseLocatorError("locator_conflict", f"{key}: {conflict}")
    return existing


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left.resolve() == right.resolve()


def _inside(path: Path, parent: Path) -> bool:
    """Containment by textual resolve and by inode, defeating case aliases."""
    path = Path(path)
    parent = Path(parent)
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        pass
    if not parent.exists():
        # A deleted/prunable worktree must not make its nearest existing
        # ancestor look like the worktree itself.
        return False
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    target = parent
    probe = current
    while True:
        try:
            if os.path.samefile(probe, target):
                return True
        except OSError:
            pass
        if probe == probe.parent:
            break
        probe = probe.parent
    return False


def _evidence_root_problem(case_dir: Path, root: Path) -> str | None:
    """Reject a root that cannot preserve the case after worktree disposal."""
    if _same_path(root, case_dir):
        return ("evidence root is the case directory; choose a root outside "
                "the producer worktree")
    if _inside(root, case_dir):
        return (f"evidence root {root} is inside the case directory "
                f"{case_dir}; choose a root outside the producer worktree")
    # Prefer refusing linked/disposable worktrees.  The main worktree is a
    # durable operator root (for example <repo>/.local); linked worktrees are
    # disposed with the experiment.
    try:
        completed = subprocess.run(
            ["git", "-C", str(case_dir), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10)
        if completed.returncode == 0:
            entries: list[dict] = []
            current: dict = {}
            for line in completed.stdout.splitlines():
                if line.startswith("worktree "):
                    if current:
                        entries.append(current)
                    current = {
                        "path": Path(line[len("worktree "):]).resolve(),
                        "prunable": False,
                    }
                elif line.startswith("prunable") and current:
                    current["prunable"] = True
            if current:
                entries.append(current)
            for index, entry in enumerate(entries):
                if index == 0:
                    continue  # main worktree is durable
                tree = entry["path"]
                # A prunable/missing linked worktree path is still unsafe: its
                # directory can be recreated by a publish and later cleaned.
                if _same_path(root, tree) or _inside(root, tree):
                    return (f"evidence root {root} is inside a disposable "
                            f"linked worktree ({tree}); use a durable "
                            "operator-owned root such as the main checkout's "
                            ".local")
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def publish(case_dir: Path | str, evidence_root: Path | str, *,
            originating_run: str | None = None,
            size_budget: int = DEFAULT_SIZE_BUDGET,
            min_free_bytes: int = DEFAULT_MIN_FREE) -> dict:
    """Copy and verify one case, then atomically publish its locator.

    Re-publishing the same key verifies the existing copy and reuses it.  A
    different identity under the same key is a conflict, never an overwrite.
    An interrupted copy leaves the source usable and no locator behind.
    """
    case_dir = Path(case_dir).expanduser().resolve()
    root = Path(evidence_root).expanduser().resolve()
    if not case_dir.is_dir():
        raise CaseLocatorError("case_missing", str(case_dir))
    root_problem = _evidence_root_problem(case_dir, root)
    if root_problem is not None:
        raise CaseLocatorError("evidence_root_inside_case", root_problem)
    verification = _verify(case_dir)
    if verification.get("ok") is not True:
        raise CaseLocatorError(
            "case_not_verified",
            "; ".join(str(item) for item in
                      (verification.get("problems") or ["unknown"])[:8]))
    identity = case_identity(case_dir)
    key = locator_key(identity)
    cases = root / CASES_DIR
    target = cases / key / "case"
    reused = target.is_dir()
    source_bytes = 0
    source_files = 0
    if reused:
        verification_now = _adopt_existing(target, identity, key)
    else:
        symlink = _symlink_problem(case_dir)
        if symlink is not None:
            raise CaseLocatorError("symlink_source", symlink)
        inventory = _tree_inventory(case_dir)
        source_bytes = sum(size for size, _digest in inventory.values())
        source_files = len(inventory)
        _preflight_space(root, source_bytes,
                         size_budget=size_budget, min_free=min_free_bytes)
        cases.mkdir(parents=True, exist_ok=True)
        staging_parent = (
            cases / f".publish-{key}-{os.getpid()}-{uuid.uuid4().hex}")
        staging = staging_parent / "case"
        try:
            try:
                shutil.copytree(case_dir, staging, symlinks=True)
            except OSError as exc:
                raise CaseLocatorError(
                    "copy_failed",
                    f"could not copy {case_dir} to staging: {exc}; the source "
                    "is unchanged") from exc
            staged_symlink = _symlink_problem(staging)
            if staged_symlink is not None:
                raise CaseLocatorError("symlink_source", staged_symlink)
            # Reject any staged path that differs from the pre-copy inventory.
            # This is the copy-window guard: a file or directory swapped for a
            # symlink after the scan is either rejected above or fails this
            # size/digest/path comparison.
            staged_inventory = _tree_inventory(staging)
            if staged_inventory != inventory:
                changed = sorted(
                    set(inventory) ^ set(staged_inventory)
                    | {name for name in inventory.keys()
                       & staged_inventory.keys()
                       if inventory[name] != staged_inventory[name]})
                raise CaseLocatorError(
                    "copy_changed",
                    f"staged case changed during copy at "
                    f"{', '.join(changed[:8])}; refusing to publish")
            copied = _verify(staging)
            if copied.get("ok") is not True:
                raise CaseLocatorError(
                    "copied_case_invalid",
                    "; ".join(str(item) for item in
                              (copied.get("problems") or ["unknown"])[:8]))
            staging_identity = case_identity(staging)
            conflict = _identity_conflict(identity, staging_identity)
            if conflict is not None:
                raise CaseLocatorError(
                    "publish_identity_changed",
                    f"copied case identity changed: {conflict}")
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.rename(staging, target)
            except OSError as exc:
                # A concurrent publisher may have won the same key.  Verify
                # both its bytes and its complete identity before adopting it.
                if not target.is_dir():
                    raise CaseLocatorError("publish_race", str(exc)) from exc
                reused = True
            verification_now = _adopt_existing(target, identity, key)
        finally:
            shutil.rmtree(staging_parent, ignore_errors=True)
    source_bytes, source_files = _tree_stats(target)
    with _locator_lock(root, key):
        previous = _json(root / LOCATOR_DIR / f"{key}.json")
        if previous:
            # The first recorded origin is immutable, even when it is absent;
            # do not silently attribute the case to a later run.
            original_run = previous.get("originating_run")
            try:
                prior_count = int(previous.get("publication_count") or 0)
            except (TypeError, ValueError) as exc:
                raise CaseLocatorError(
                    "locator_corrupt",
                    f"locator publication_count is invalid: {exc}") from exc
            publication_count = prior_count + 1
            first_published = previous.get("first_published_utc") or _now()
            last_published_run = (originating_run
                                  or previous.get("last_published_run")
                                  or original_run)
        else:
            original_run = originating_run
            publication_count = 1
            first_published = _now()
            last_published_run = originating_run
        last_published_utc = _now()
        locator = {
            "schema": SCHEMA,
            "key": key,
            "identity": identity,
            "case_id": identity.get("case_id"),
            "relative_path": str(target.relative_to(root)),
            "verification": _verification_summary(verification_now),
            "limitations": _limitations(identity, verification_now),
            "originating_run": original_run,
            "first_published_utc": first_published,
            "last_published_run": last_published_run,
            "last_published_utc": last_published_utc,
            "publication_count": publication_count,
            "published_utc": last_published_utc,
            "reused": reused,
            "size_bytes": source_bytes,
            "files": source_files,
        }
        problem = _locator_problem(locator)
        if problem is not None:
            raise CaseLocatorError("locator_malformed", problem)
        path = _write_locator(root, key, locator)
        locator["locator_path"] = str(path)
        return locator


_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")


def _relative_text(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts


def _locator_problem(value: dict) -> str | None:
    """Reject schema-valid locators whose field types would crash readers."""
    key = value.get("key")
    if not isinstance(key, str) or not key:
        return "locator key is not a nonempty string"
    relative = value.get("relative_path")
    if not _relative_text(relative):
        return "locator relative_path is not a safe relative string"
    identity = value.get("identity")
    if not isinstance(identity, dict):
        return "locator identity is not an object"
    if identity.get("session_id") is not None and not isinstance(
            identity.get("session_id"), str):
        return "identity.session_id is not a string"
    frame = identity.get("capture_frame_id")
    if frame is not None and (isinstance(frame, bool)
                              or not isinstance(frame, (int, str))):
        return "identity.capture_frame_id is not an integer or string"
    for field in ("event_stream_sha256", "manifest_sha256", "seal_sha256"):
        digest = identity.get(field)
        if digest is not None and not (
                isinstance(digest, str) and _HEX64.match(digest)):
            return f"identity.{field} is not a 64-character hex digest"
    seal_file = identity.get("seal_file")
    if seal_file is not None and not _relative_text(seal_file):
        return "identity.seal_file is not a safe relative string"
    for field in ("schema", "case_id"):
        if identity.get(field) is not None and not isinstance(
                identity.get(field), str):
            return f"identity.{field} is not a string"
    for field in ("case_id", "originating_run", "last_published_run",
                  "first_published_utc", "last_published_utc",
                  "published_utc"):
        if value.get(field) is not None and not isinstance(
                value.get(field), str):
            return f"locator {field} is not a string"
    verification = value.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            return "locator verification is not an object"
        if "ok" in verification and not isinstance(verification["ok"], bool):
            return "locator verification.ok is not a boolean"
    for field in ("limitations",):
        items = value.get(field)
        if items is not None and (
                not isinstance(items, list)
                or any(not isinstance(item, str) for item in items)):
            return f"locator {field} is not a list of strings"
    for field in ("publication_count", "size_bytes", "files"):
        number = value.get(field)
        if number is not None and (isinstance(number, bool)
                                   or not isinstance(number, int)):
            return f"locator {field} is not an integer"
    if "reused" in value and not isinstance(value["reused"], bool):
        return "locator reused is not a boolean"
    return None


def _locator_scan(root: Path) -> tuple[list[dict], bool, list[dict]]:
    """Read locator documents, reporting scan exhaustion and broken files."""
    directory = root / LOCATOR_DIR
    if not directory.is_dir():
        return [], False, []
    paths = sorted(directory.glob("*.json"))
    truncated = len(paths) > MAX_LOCATORS
    found: list[dict] = []
    invalid: list[dict] = []
    for path in paths[:MAX_LOCATORS]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            invalid.append({"locator_path": str(path),
                            "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(value, dict) or value.get("schema") != SCHEMA:
            invalid.append({"locator_path": str(path),
                            "error": "locator schema is invalid"})
            continue
        problem = _locator_problem(value)
        if problem is not None:
            invalid.append({"locator_path": str(path), "error": problem})
            continue
        value["locator_path"] = str(path)
        found.append(value)
    return found, truncated, invalid


def _target_path(root: Path, locator: dict) -> tuple[Path | None, str | None]:
    relative = str(locator.get("relative_path") or "")
    if not relative:
        return None, "locator has no relative_path"
    try:
        target = (root / relative).resolve()
    except (OSError, ValueError, UnicodeError) as exc:
        return None, f"locator path cannot be resolved: {exc}"
    try:
        target.relative_to(root)
    except ValueError:
        return None, "locator path escapes the evidence root"
    return target, None


def _identity_view(locator: dict, row: dict) -> dict:
    """Flatten the capture identity for discovery consumers."""
    identity = locator.get("identity") or {}
    for key in ("session_id", "capture_frame_id", "seal_sha256",
                "event_stream_sha256", "manifest_sha256", "seal_file"):
        row.setdefault(key, identity.get(key))
    row.setdefault("read_only", True)
    return row


def _without_verification(locator: dict, **values) -> dict:
    row = {key: value for key, value in locator.items()
           if key != "verification"}
    row.update(values)
    return row


def _locator_metadata(root: Path, locator: dict) -> dict:
    """Metadata-only state: containment and payload existence, no hashing."""
    target, problem = _target_path(root, locator)
    if problem is not None or target is None:
        row = _without_verification(
            locator, state="invalid_path", detail=problem,
            resolved_path=None, verified=False, verification_ok=False,
            current_verification="not_run",
            recorded_verification=locator.get("verification"))
    else:
        state = "available" if target.is_dir() else "missing_payload"
        row = _without_verification(
            locator, state=state, resolved_path=str(target),
            verified=False, verification_ok=False,
            current_verification=("not_run" if state == "available"
                                  else "missing_payload"),
            recorded_verification=locator.get("verification"))
    return _identity_view(locator, row)


def _resolve_match(root: Path, locator: dict) -> dict:
    """Full verification of one selected case."""
    target, problem = _target_path(root, locator)
    if problem is not None or target is None:
        return _identity_view(locator, _without_verification(
            locator, state="invalid_path", detail=problem,
            resolved_path=None, verified=False, verification_ok=False,
            current_verification="not_run",
            recorded_verification=locator.get("verification")))
    if not target.is_dir():
        return _identity_view(locator, _without_verification(
            locator, state="missing_payload", resolved_path=str(target),
            verified=False, verification_ok=False,
            current_verification="missing_payload",
            recorded_verification=locator.get("verification")))
    verification = _verify(target)
    identity = case_identity(target)
    expected = locator.get("identity") or {}
    if verification.get("ok") is not True:
        return _identity_view(locator, dict(
            locator, state="digest_mismatch", resolved_path=str(target),
            verified=False, verification_ok=False,
            current_verification="failed",
            recorded_verification=locator.get("verification"),
            verification=_verification_summary(verification),
            detail="; ".join(str(item) for item in
                             (verification.get("problems") or [])[:4])))
    conflict = _identity_conflict(expected, identity)
    if conflict is not None:
        return _identity_view(locator, dict(
            locator, state="digest_mismatch", resolved_path=str(target),
            verified=False, verification_ok=False,
            current_verification="failed",
            recorded_verification=locator.get("verification"),
            verification=_verification_summary(verification),
            detail=conflict))
    return _identity_view(locator, dict(
        locator, state="available", resolved_path=str(target),
        verified=True, verification_ok=True,
        current_verification="passed",
        recorded_verification=locator.get("verification"),
        verification=_verification_summary(verification)))


def _matches(locator: dict, *, session_id: str | None,
             capture_frame_id: int | None, seal_sha256: str | None,
             case_id: str | None, key: str | None) -> bool:
    identity = locator.get("identity") or {}
    if key is not None and str(locator.get("key")) != str(key):
        return False
    if session_id is not None and str(identity.get("session_id")) != str(
            session_id):
        return False
    if capture_frame_id is not None and str(
            identity.get("capture_frame_id")) != str(capture_frame_id):
        return False
    if seal_sha256 is not None and identity.get("seal_sha256") != seal_sha256:
        return False
    if case_id is not None and str(locator.get("case_id")) != str(case_id):
        return False
    return True


def locate(evidence_root: Path | str, *, session_id: str | None = None,
           capture_frame_id: int | None = None, seal_sha256: str | None = None,
           case_id: str | None = None, key: str | None = None,
           limit: int | None = None, verify: bool = True) -> dict:
    """Resolve a bounded, selected set of published cases.

    At least one identity filter is required so a lookup cannot walk and
    re-verify an entire large archive.  Full verification is bounded to one
    match by default; pass a larger ``limit`` or ``verify=False`` explicitly.
    States are ``available``, ``missing_payload`` (locator but no bytes),
    ``digest_mismatch`` (bytes or identity changed), ``invalid_path``,
    ``root_unavailable``, ``refused_unbounded`` and
    ``unknown``/``unknown_truncated`` (no matching locator; never equated with
    deletion).
    """
    root = Path(evidence_root).expanduser().resolve()
    query = {
        "session_id": session_id,
        "capture_frame_id": capture_frame_id,
        "seal_sha256": seal_sha256,
        "case_id": case_id,
        "key": key,
    }
    if all(value is None for value in query.values()):
        return {"schema": SCHEMA, "root": str(root),
                "state": "refused_unbounded", "query": query, "matches": [],
                "detail": "provide at least one identity filter "
                          "(session/frame/seal/case/key)",
                "read_only": True}
    if limit is None:
        limit = 1 if verify else DEFAULT_LIMIT
    if limit < 1:
        raise CaseLocatorError("limit", "limit must be >= 1")
    if not root.is_dir():
        return {"schema": SCHEMA, "root": str(root), "state": "root_unavailable",
                "query": query, "matches": [], "read_only": True}
    locators, scan_truncated, invalid_locators = _locator_scan(root)
    matches: list[dict] = []
    matched_metadata_states: dict[str, int] = {}
    matched_total = 0
    for locator in locators:
        if not _matches(locator, session_id=session_id,
                        capture_frame_id=capture_frame_id,
                        seal_sha256=seal_sha256, case_id=case_id, key=key):
            continue
        matched_total += 1
        metadata = _locator_metadata(root, locator)
        matched_metadata_states[metadata["state"]] = (
            matched_metadata_states.get(metadata["state"], 0) + 1)
        if len(matches) >= limit:
            continue
        matches.append(_resolve_match(root, locator) if verify else metadata)
    state = "unknown"
    if matches:
        for candidate in ("invalid_path", "digest_mismatch", "missing_payload",
                          "available"):
            if any(item["state"] == candidate for item in matches):
                state = candidate
                break
    elif invalid_locators:
        state = "invalid_locator"
    elif scan_truncated:
        state = "unknown_truncated"
    truncated = scan_truncated or matched_total > len(matches)
    # Metadata for every matched locator was computed even when only ``limit``
    # were fully verified.  A damaged unreturned match must not leave the
    # lookup looking like a success.
    metadata_worst = "available"
    for candidate in ("invalid_path", "missing_payload"):
        if matched_metadata_states.get(candidate):
            metadata_worst = candidate
            break
    verified_all = not truncated
    if verify and truncated and matched_total > len(matches):
        # An unreturned match could be corrupt; a page-1-only result is not a
        # reusable success.  The distinct state is non-zero at the CLI.
        state = "truncated_unverified"
        metadata_worst = "unknown"
    elif state == "available" and metadata_worst != "available":
        state = metadata_worst
    return {
        "schema": SCHEMA,
        "root": str(root),
        "state": state,
        "query": query,
        "matches": matches,
        "returned": len(matches),
        "matched": matched_total,
        "limit": limit,
        "scan_truncated": scan_truncated,
        "truncated": truncated,
        "verified_all": verified_all,
        "safe_to_reuse": state == "available" and verified_all,
        "invalid_locators": invalid_locators[:20],
        "invalid_locator_count": len(invalid_locators),
        "matched_metadata_states": matched_metadata_states,
        "state_scope": ("all_matched" if verified_all
                        else "returned_matches_only"),
        "read_only": True,
    }


def summaries(evidence_root: Path | str) -> list[dict]:
    """Locator metadata for discovery; never hashes the full case payload."""
    root = Path(evidence_root).expanduser().resolve()
    if not root.is_dir():
        return []
    locators, _truncated, _invalid = _locator_scan(root)
    return [_locator_metadata(root, locator) for locator in locators]


def inventory(evidence_root: Path | str, *, verify: bool = False,
              offset: int = 0, limit: int | None = None) -> dict:
    """Bounded summary of published locators under one root.

    Metadata-only by default.  ``verify=True`` hashes and re-verifies each
    selected package and is intended for an explicit operator check, not a
    discovery loop.  ``offset``/``limit`` page the stable locator order; the
    counts describe the full selected set and the returned page separately.
    """
    if offset < 0:
        raise CaseLocatorError("offset", "offset must be >= 0")
    if limit is not None and limit < 1:
        raise CaseLocatorError("limit", "limit must be >= 1")
    root = Path(evidence_root).expanduser().resolve()
    if not root.is_dir():
        return {"schema": SCHEMA, "root": str(root),
                "state": "root_unavailable", "locators": [],
                "counts": {}, "truncated": False, "read_only": True}
    locators, truncated, invalid_locators = _locator_scan(root)
    selected = locators[offset:]
    if limit is not None:
        selected = selected[:limit]
    rows = [(_resolve_match(root, locator) if verify
             else _locator_metadata(root, locator))
            for locator in selected]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    invalid_count = len(invalid_locators)
    if invalid_locators:
        state = "degraded"
    elif not rows:
        state = "empty"
    elif counts.get("available") == len(rows):
        state = "available"
    else:
        state = "degraded"
    page_truncated = offset + len(rows) < len(locators)
    return {
        "schema": SCHEMA,
        "root": str(root),
        "state": state,
        "counts": counts,
        "locators": rows,
        "total": len(locators),
        "offset": offset,
        "limit": limit,
        "returned": len(rows),
        "next_offset": (offset + len(rows)) if page_truncated else None,
        "invalid_locators": invalid_locators[:20],
        "invalid_locator_count": invalid_count,
        "verified": verify,
        "truncated": truncated or page_truncated,
        "scan_truncated": truncated,
        "scan_limit": MAX_LOCATORS,
        "read_only": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    publish_parser = sub.add_parser("publish")
    publish_parser.add_argument("--case", required=True, type=Path)
    publish_parser.add_argument("--root", required=True, type=Path)
    publish_parser.add_argument("--originating-run", default=None)
    publish_parser.add_argument("--size-budget", type=int,
                                default=DEFAULT_SIZE_BUDGET)
    locate_parser = sub.add_parser("locate")
    locate_parser.add_argument("--root", required=True, type=Path)
    locate_parser.add_argument("--session-id", default=None)
    locate_parser.add_argument("--capture-frame-id", type=int, default=None)
    locate_parser.add_argument("--seal-sha256", default=None)
    locate_parser.add_argument("--case-id", default=None)
    locate_parser.add_argument("--key", default=None)
    locate_parser.add_argument("--limit", type=int, default=None)
    locate_parser.add_argument("--no-verify", action="store_true",
                               help="metadata-only selected lookup")
    inventory_parser = sub.add_parser("inventory")
    inventory_parser.add_argument("--root", required=True, type=Path)
    inventory_parser.add_argument("--verify", action="store_true",
                                  help="hash and fully verify every locator")
    inventory_parser.add_argument("--offset", type=int, default=0)
    inventory_parser.add_argument("--limit", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "publish":
            value = publish(args.case, args.root,
                            originating_run=args.originating_run,
                            size_budget=args.size_budget)
            status = 0
        elif args.operation == "locate":
            value = locate(
                args.root, session_id=args.session_id,
                capture_frame_id=args.capture_frame_id,
                seal_sha256=args.seal_sha256, case_id=args.case_id,
                key=args.key, limit=args.limit,
                verify=not args.no_verify)
            status = 0 if value["state"] == "available" else 2
        else:
            value = inventory(args.root, verify=args.verify,
                             offset=args.offset, limit=args.limit)
            status = 0
    except CaseLocatorError as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "kind": exc.kind, "detail": exc.detail}))
        return 1
    print(json.dumps(value, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
