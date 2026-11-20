"""Read-only discovery of recorded diagnostic campaigns across worktrees.

The M7.0 canary produced its evidence on an infrastructure branch, but the
diagnostic session ran from another worktree.  ``.local/`` is per worktree, so
the agent had to inspect sibling checkouts by hand and search the filesystem.

This module scans one explicitly configured evidence root list instead:

* ``EU4_EVIDENCE_ROOT`` -- OS-path-separated absolute roots; when unset the
  repository's ``.local`` directory is the only default;
* a bounded depth (root, root/campaign, root/campaign/session-*), never a
  filesystem-wide search;
* read-only: metadata and digests are read, nothing is copied, moved or
  mutated;
* every row keeps the original artifact paths and SHA-256 digests so later
  inspection can prove it read the same bytes.

No database is introduced: session directories and their ``result.json`` /
``case.json`` metadata already answer the required queries.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from automation import case_locator

SCHEMA = "gfx-evidence-index/v1"
ROOT_ENV = "EU4_EVIDENCE_ROOT"
MAX_DIRECTORIES = 20000
MAX_RESULT_BYTES = 32 * 1024 * 1024
CLASSIFICATIONS = (
    "rendering_difference",
    "incomplete",
    "alignment_failure",
    "gpu_execution_failure",
    "process_failure",
    "infrastructure_failure",
    "match",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def roots(configured: str | None = None,
          environ: dict | None = None) -> list[Path]:
    """Resolve the evidence roots from configuration or the default.

    A relative configured root is resolved from the caller's working directory;
    an unset root is the repository's own ``.local`` directory.  Roots are
    de-duplicated by resolved path so a worktree that appears twice is scanned
    once.
    """
    environment = os.environ if environ is None else environ
    value = configured if configured is not None else environment.get(ROOT_ENV)
    candidates: list[Path] = []
    if value:
        for item in str(value).split(os.pathsep):
            if item.strip():
                candidates.append(Path(item).expanduser())
    else:
        candidates.append(_repo_root() / ".local")
    resolved: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            path = candidate.resolve()
        except OSError:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    return resolved


def _read_json(path: Path) -> dict:
    try:
        if path.stat().st_size > MAX_RESULT_BYTES:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _timestamp(value) -> str | None:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(
            float(value), tz=dt.timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _campaign_root(session_dir: Path, root: Path) -> Path:
    try:
        relative = session_dir.relative_to(root)
    except ValueError:
        return session_dir.parent
    return root / relative.parts[0] if len(relative.parts) > 1 else root


def _record(session_dir: Path, root: Path) -> dict:
    result_path = session_dir / "result.json"
    result = _read_json(result_path)
    case_dir = session_dir / "case"
    case_path = case_dir / "case.json"
    case_manifest = _read_json(case_path) if case_path.is_file() else {}
    qualification = result.get("qualification") or {}
    comparison = (result.get("frames") or {}).get("comparison") or {}
    health = result.get("health") or {}
    scenario = result.get("scenario") or {}
    finished = _timestamp(result.get("finished_utc")
                          or result.get("finished_unix")
                          or (result.get("terminal") or {}).get("updated_utc"))
    created = _timestamp(result.get("started_utc")) or finished
    return {
        "session_id": result.get("session_id") or session_dir.name,
        "session_dir": str(session_dir),
        "campaign_root": str(_campaign_root(session_dir, root)),
        "evidence_root": str(root),
        "recorded_classification": result.get("classification")
        or qualification.get("status"),
        # Discovery is an index, not a verdict. The recorded classification is
        # reported with its provenance; eu4_diff_inspect re-derives the current
        # classification from the same result and shows both.
        "classification": result.get("classification")
        or qualification.get("status"),
        "classification_source": "recorded_result",
        "qualified": qualification.get("qualified"),
        "stop": (result.get("stop") or {}).get("reason"),
        "health": health.get("verdict"),
        "scenario": {
            "name": scenario.get("name"),
            "digest": scenario.get("digest"),
        },
        "comparison": {
            "status": comparison.get("status"),
            "compared": comparison.get("compared"),
            "failed": comparison.get("failed"),
        },
        "created_utc": created,
        "finished_utc": finished,
        "has_case": case_path.is_file(),
        "case_schema": case_manifest.get("schema"),
        "case_sequence": case_manifest.get("sequence"),
        "provenance": {
            "result_path": str(result_path),
            "result_sha256": _sha256(result_path),
            "case_path": str(case_path) if case_path.is_file() else None,
            "case_sha256": _sha256(case_path) if case_path.is_file() else None,
            "read_only": True,
        },
    }


def _session_dirs(root: Path) -> list[Path]:
    """Bounded discovery: root/session-*, root/*/session-*, root/*/*/session-*."""
    found: list[Path] = []
    scanned = 0
    if not root.is_dir():
        return found

    def maybe(directory: Path) -> None:
        nonlocal scanned
        if scanned >= MAX_DIRECTORIES:
            return
        scanned += 1
        if directory.is_dir() and (directory / "result.json").is_file():
            found.append(directory)

    for first in sorted(root.iterdir()) if root.is_dir() else []:
        if not first.is_dir():
            continue
        maybe(first)
        if first.name.startswith("session-"):
            continue
        try:
            children = sorted(first.iterdir())
        except OSError:
            continue
        for second in children:
            if not second.is_dir():
                continue
            maybe(second)
            if second.name.startswith("session-"):
                continue
            try:
                grandchildren = sorted(second.iterdir())
            except OSError:
                continue
            for third in grandchildren:
                if third.is_dir() and third.name.startswith("session-"):
                    maybe(third)
    return found


def discover(
    *,
    configured_roots: str | None = None,
    query: str | None = None,
    classification: str | None = None,
    since: str | None = None,
    limit: int = 20,
    offset: int = 0,
    locator_limit: int | None = None,
    locator_offset: int = 0,
    environ: dict | None = None,
) -> dict:
    """Bounded, filtered, read-only campaign discovery across evidence roots."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if locator_offset < 0:
        raise ValueError("locator_offset must be >= 0")
    if locator_limit is None:
        locator_limit = limit
    if locator_limit < 1:
        raise ValueError("locator_limit must be >= 1")
    root_list = roots(configured_roots, environ)
    rows: list[dict] = []
    skipped_roots: list[str] = []
    for root in root_list:
        if not root.is_dir():
            skipped_roots.append(str(root))
            continue
        seen: set[str] = set()
        for directory in _session_dirs(root):
            key = str(directory)
            if key in seen:
                continue
            seen.add(key)
            rows.append(_record(directory, root))
    locator_rows: list[dict] = []
    locator_truncated = False
    locator_invalid = 0
    for root in root_list:
        if not root.is_dir():
            continue
        inventory = case_locator.inventory(root, verify=False)
        locator_truncated = (locator_truncated
                             or bool(inventory.get("truncated")))
        locator_invalid += int(inventory.get("invalid_locator_count") or 0)
        for row in inventory.get("locators") or []:
            row["evidence_root"] = str(root)
            locator_rows.append(row)
    locator_total = len(locator_rows)
    discovered_total = len(rows)
    discovered_counts = {name: sum(1 for row in rows if row["classification"] == name)
                         for name in CLASSIFICATIONS}
    discovered_counts["other"] = sum(
        1 for row in rows if row["classification"] not in CLASSIFICATIONS)
    needle = (query or "").strip().lower()
    if needle:
        rows = [
            row for row in rows
            if needle in json.dumps(row, sort_keys=True).lower()
        ]
    if classification:
        rows = [row for row in rows if row["classification"] == classification]
    if since:
        rows = [
            row for row in rows
            if str(row.get("finished_utc") or row.get("created_utc") or "")
            >= str(since)
        ]
    if needle:
        locator_rows = [
            row for row in locator_rows
            if needle in json.dumps(row, sort_keys=True).lower()
        ]
    if classification:
        # A locator carries a recorded verification, not a campaign
        # classification; it is only visible in an unfiltered listing.
        locator_rows = []
    if since:
        locator_rows = [
            row for row in locator_rows
            if str(row.get("published_utc") or "") >= str(since)
        ]
    rows.sort(
        key=lambda row: str(row.get("finished_utc") or row.get("created_utc") or ""),
        reverse=True,
    )
    page = rows[offset:offset + limit]
    locator_filtered = len(locator_rows)
    locator_page = locator_rows[
        locator_offset:locator_offset + locator_limit]
    locator_available = sum(1 for row in locator_page
                            if row.get("state") == "available")
    locator_missing = sum(1 for row in locator_page
                          if row.get("state") == "missing_payload")
    counts = {name: sum(1 for row in rows if row["classification"] == name)
              for name in CLASSIFICATIONS}
    counts["other"] = sum(1 for row in rows if row["classification"] not in CLASSIFICATIONS)
    counts["total"] = len(rows)
    return {
        "schema": SCHEMA,
        "roots": [str(root) for root in root_list],
        "skipped_roots": skipped_roots,
        "sessions": page,
        "counts": counts,
        "discovered": {
            "total": discovered_total,
            "by_classification": discovered_counts,
        },
        "filtered": len(rows),
        "returned": len(page),
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(page) < len(rows),
        "next_offset": (offset + len(page)) if offset + len(page) < len(rows) else None,
        "case_locators": locator_page,
        "locator_counts": {
            "total": locator_total,
            "scanned": locator_total,
            "filtered": locator_filtered,
            "returned": len(locator_page),
            "offset": locator_offset,
            "limit": locator_limit,
            "available": locator_available,
            "missing_payload": locator_missing,
            "filter_truncated": (locator_offset + len(locator_page)
                                  < locator_filtered),
            "scan_truncated": locator_truncated,
            "scan_limit": case_locator.MAX_LOCATORS,
            "invalid_locators": locator_invalid,
            "scope": "returned_page",
        },
        "read_only": True,
        "query": {
            "text": query or None,
            "classification": classification,
            "since": since,
        },
    }
