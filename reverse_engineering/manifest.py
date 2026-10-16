"""Deterministic decompilation batch manifests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from reverse_engineering.intervals import format_rva, parse_rva

TERMINAL_STATUSES = frozenset({"success", "failed", "timeout", "skipped"})
UNSUCCESSFUL_STATUSES = frozenset({"failed", "timeout", "skipped"})
BATCH_NAME_RE = re.compile(r"^batch-(\d{5})\.jsonl$")
CANONICAL_RVA_RE = re.compile(r"^0x[0-9a-f]{16}$")
BLANK_ERROR_PREFIX = "decompilation_failed_without_message"


def inventory_bundle_digest(inv_dir: Path) -> str:
    """SHA-256 over canonical functions, call_edges, and coverage JSON."""
    parts: list[str] = []
    functions = inv_dir / "functions.jsonl"
    edges = inv_dir / "call_edges.jsonl"
    coverage = inv_dir / "coverage.json"
    if functions.is_file():
        lines = [line for line in functions.read_text().splitlines() if line.strip()]
        lines.sort(key=lambda raw: parse_rva(json.loads(raw)["entry_rva"]))
        parts.append("functions.jsonl\n" + "\n".join(lines))
    if edges.is_file():
        lines = [line for line in edges.read_text().splitlines() if line.strip()]
        lines.sort()
        parts.append("call_edges.jsonl\n" + "\n".join(lines))
    if coverage.is_file():
        cov = json.loads(coverage.read_text())
        parts.append("coverage.json\n" + json.dumps(cov, sort_keys=True))
    return hashlib.sha256("\n---\n".join(parts).encode()).hexdigest()


def inventory_digest(functions_jsonl: Path) -> str:
    """SHA-256 of canonical functions.jsonl (sorted lines by entry_rva)."""
    lines: list[str] = []
    for line in functions_jsonl.read_text().splitlines():
        if line.strip():
            lines.append(line)
    lines.sort(key=lambda raw: parse_rva(json.loads(raw)["entry_rva"]))
    payload = "\n".join(lines) + ("\n" if lines else "")
    return hashlib.sha256(payload.encode()).hexdigest()


def eligible_internal_rvas(functions_jsonl: Path) -> list[int]:
    rvas: list[int] = []
    for line in functions_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") == "internal":
            rvas.append(parse_rva(row["entry_rva"]))
    rvas.sort()
    return rvas


def build_batch_manifest(
    *,
    functions_jsonl: Path,
    baseline_revision: str,
    batch_size: int,
    exporter_script_hash: str,
    inventory_digest_value: str,
    inventory_bundle_digest_value: str | None = None,
) -> dict[str, Any]:
    rvas = eligible_internal_rvas(functions_jsonl)
    batches: dict[str, list[str]] = {}
    for index, start in enumerate(range(0, len(rvas), batch_size), start=1):
        chunk = rvas[start : start + batch_size]
        key = f"batch-{index:05d}"
        batches[key] = [format_rva(rva) for rva in chunk]
    return {
        "baseline_revision": baseline_revision,
        "inventory_digest": inventory_digest_value,
        "inventory_bundle_digest": inventory_bundle_digest_value or inventory_digest_value,
        "batch_size": batch_size,
        "exporter_script_hash": exporter_script_hash,
        "eligible_function_count": len(rvas),
        "batches": batches,
    }


def write_batch_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def load_batch_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Deterministic encoding for exact manifest comparison."""
    batches = manifest.get("batches") or {}
    ordered: dict[str, Any] = {
        "baseline_revision": manifest.get("baseline_revision"),
        "inventory_digest": manifest.get("inventory_digest"),
        "inventory_bundle_digest": manifest.get("inventory_bundle_digest"),
        "batch_size": manifest.get("batch_size"),
        "exporter_script_hash": manifest.get("exporter_script_hash"),
        "eligible_function_count": manifest.get("eligible_function_count"),
        "batches": {batch_id: list(batches[batch_id]) for batch_id in sorted(batches)},
    }
    return (json.dumps(ordered, sort_keys=True) + "\n").encode()


def manifests_equivalent(saved: dict[str, Any], expected: dict[str, Any]) -> bool:
    return canonical_manifest_bytes(saved) == canonical_manifest_bytes(expected)


def manifest_partition_digest(manifest: dict[str, Any]) -> str:
    parts: list[str] = []
    for batch_id in sorted(manifest.get("batches", {})):
        rvas = manifest["batches"][batch_id]
        parts.append(f"{batch_id}\n" + "\n".join(rvas))
    return hashlib.sha256("\n---\n".join(parts).encode()).hexdigest()


def batches_archive_required(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    if old is None:
        return False
    if old.get("inventory_digest") != new.get("inventory_digest"):
        return True
    if manifest_partition_digest(old) != manifest_partition_digest(new):
        return True
    return False


def call_edges_only_inventory_drift(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    if old is None or batches_archive_required(old, new):
        return False
    return old.get("inventory_bundle_digest") != new.get("inventory_bundle_digest")


def archive_decompiled_batches(decomp_dir: Path, label: str) -> Path | None:
    """Move batch-NNNNN.jsonl files to decompiled/.archive/<label>/ (not batch_manifest.json)."""
    batch_files = [p for p in decomp_dir.glob("batch-*.jsonl") if BATCH_NAME_RE.match(p.name)]
    if not batch_files:
        return None
    archive_dir = decomp_dir / ".archive" / label
    archive_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(batch_files):
        shutil.move(str(path), str(archive_dir / path.name))
    return archive_dir


def unique_archive_dir(decomp_dir: Path, label: str) -> Path:
    base = decomp_dir / ".archive" / label
    candidate = base
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = decomp_dir / ".archive" / f"{label}-{suffix:02d}"
    return candidate


def list_manifest_batch_files(decomp_dir: Path) -> list[Path]:
    return sorted(p for p in decomp_dir.glob("batch-*.jsonl") if BATCH_NAME_RE.match(p.name))


def archive_batch_files(decomp_dir: Path, archive_dir: Path) -> list[tuple[Path, Path]]:
    """Move batch JSONL files into archive_dir. Returns (archived_path, original_path) pairs."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    moves: list[tuple[Path, Path]] = []
    for path in list_manifest_batch_files(decomp_dir):
        dest = archive_dir / path.name
        shutil.move(str(path), str(dest))
        moves.append((dest, path))
    return moves


def rollback_archived_files(moves: list[tuple[Path, Path]]) -> None:
    for archived, original in reversed(moves):
        if archived.is_file():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(archived), str(original))


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalize_staging_batch_errors(staging: Path) -> None:
    """Fill blank error fields on unsuccessful terminal records before validation."""
    if not staging.is_file():
        return
    lines_out: list[str] = []
    for line in staging.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            lines_out.append(line)
            continue
        status = row.get("status")
        if status in UNSUCCESSFUL_STATUSES and not _nonempty_text(row.get("error")):
            rva = row.get("entry_rva", "unknown")
            row["error"] = f"{BLANK_ERROR_PREFIX}: {rva}"
        lines_out.append(json.dumps(row, sort_keys=True))
    staging.write_text("\n".join(lines_out) + ("\n" if lines_out else ""))


def validate_batch_file(path: Path, expected_rvas: list[str]) -> list[str]:
    """Return list of errors; empty if valid."""
    errors: list[str] = []
    if not path.is_file():
        return [f"missing batch file: {path.name}"]
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    if len(lines) != len(expected_rvas):
        errors.append(f"{path.name}: expected {len(expected_rvas)} records, got {len(lines)}")
    seen: dict[str, dict] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid json: {exc}")
            continue
        if not isinstance(row, dict):
            errors.append(f"{path.name}: record must be a JSON object")
            continue
        rva = row.get("entry_rva")
        status = row.get("status")
        if not isinstance(rva, str) or not CANONICAL_RVA_RE.fullmatch(rva):
            errors.append(f"{path.name}: invalid entry_rva: {rva!r}")
            continue
        if not isinstance(status, str):
            errors.append(f"{path.name}: invalid status for {rva}: {status!r}")
            continue
        if rva in seen:
            errors.append(f"{path.name}: duplicate entry_rva {rva}")
        seen[rva] = row
        if status not in TERMINAL_STATUSES:
            errors.append(f"{path.name}: non-terminal status for {rva}: {status}")
            continue
        if status == "success":
            if not _nonempty_text(row.get("pseudocode")):
                errors.append(f"{path.name}: success without pseudocode for {rva}")
        elif status in UNSUCCESSFUL_STATUSES:
            if not _nonempty_text(row.get("error")):
                errors.append(f"{path.name}: {status} without error for {rva}")
    expected_set = set(expected_rvas)
    if set(seen) != expected_set:
        missing = expected_set - set(seen)
        extra = set(seen) - expected_set
        if missing:
            errors.append(f"{path.name}: missing RVAs: {sorted(missing)[:5]}...")
        if extra:
            errors.append(f"{path.name}: unexpected RVAs: {sorted(extra)[:5]}...")
    return errors


def export_completeness(manifest: dict[str, Any], decompiled_dir: Path) -> tuple[float, list[str]]:
    """Return (C_export fraction, errors)."""
    batches = manifest.get("batches", {})
    if not batches:
        return 0.0, ["empty batch manifest"]
    errors: list[str] = []
    all_rvas: set[str] = set()
    for batch_id, expected in batches.items():
        if len(set(expected)) != len(expected):
            errors.append(f"duplicate RVAs inside {batch_id}")
        overlap = all_rvas.intersection(expected)
        if overlap:
            errors.append(f"duplicate RVAs across batches near {batch_id}")
        all_rvas.update(expected)
        batch_path = decompiled_dir / f"{batch_id}.jsonl"
        errors.extend(validate_batch_file(batch_path, expected))
    eligible = manifest.get("eligible_function_count", len(all_rvas))
    if eligible != len(all_rvas):
        errors.append("eligible_function_count does not match manifest partition size")
    complete = len(errors) == 0
    return (1.0 if complete else 0.0, errors)


def pilot_sample_rvas(functions_jsonl: Path, n: int, seed: int = 0) -> list[int]:
    """Deterministic tertile-stratified sample by decoded_instruction_bytes."""
    import random

    rows: list[tuple[int, int]] = []
    for line in functions_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") != "internal":
            continue
        rows.append((parse_rva(row["entry_rva"]), int(row.get("decoded_instruction_bytes", 0))))
    if not rows or n <= 0:
        return []
    by_size = sorted(rows, key=lambda item: item[1])
    third = max(1, len(by_size) // 3)
    buckets = [by_size[:third], by_size[third : 2 * third], by_size[2 * third :]]
    rng = random.Random(seed)
    per = max(1, n // 3)
    sample: list[int] = []
    for bucket in buckets:
        pool = [rva for rva, _ in bucket]
        rng.shuffle(pool)
        sample.extend(pool[:per])
    return sorted(set(sample))[:n]


def _bytes_by_rva(functions_jsonl: Path, rvas: set[str] | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in functions_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") != "internal":
            continue
        rva = row["entry_rva"]
        if rvas is not None and rva not in rvas:
            continue
        out[rva] = int(row.get("decoded_instruction_bytes", 0))
    return out


def decompilation_success_stats(
    manifest: dict[str, Any],
    decompiled_dir: Path,
    functions_jsonl: Path,
    *,
    denominator_rvas: list[str] | None = None,
) -> dict[str, float | int]:
    if denominator_rvas is not None:
        denom_set = set(denominator_rvas)
        bytes_by_rva = _bytes_by_rva(functions_jsonl, denom_set)
        total_inventory_bytes = sum(bytes_by_rva.values())
    else:
        bytes_by_rva = _bytes_by_rva(functions_jsonl)
        total_inventory_bytes = sum(bytes_by_rva.values())

    success_count = 0
    failed_count = 0
    timeout_count = 0
    skipped_count = 0
    attempted_count = 0
    success_bytes = 0
    attempted_bytes = 0
    completed_batches = 0

    for batch_id, expected in manifest.get("batches", {}).items():
        batch_path = decompiled_dir / f"{batch_id}.jsonl"
        if not batch_path.is_file():
            continue
        if validate_batch_file(batch_path, expected):
            continue
        completed_batches += 1
        for line in batch_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            rva = row.get("entry_rva")
            if not isinstance(rva, str):
                continue
            status = row.get("status")
            attempted_count += 1
            size = bytes_by_rva.get(rva, 0)
            attempted_bytes += size
            if status == "success":
                success_count += 1
                success_bytes += size
            elif status == "failed":
                failed_count += 1
            elif status == "timeout":
                timeout_count += 1
            elif status == "skipped":
                skipped_count += 1

    c_attempted = success_bytes / attempted_bytes if attempted_bytes else 0.0
    c_total = success_bytes / total_inventory_bytes if total_inventory_bytes else 0.0
    return {
        "c_success_count": success_count / attempted_count if attempted_count else 0.0,
        "c_success_bytes": c_attempted,
        "c_success_bytes_attempted": c_attempted,
        "c_success_bytes_total": c_total,
        "success_count": success_count,
        "failed_count": failed_count,
        "timeout_count": timeout_count,
        "skipped_count": skipped_count,
        "total_count": attempted_count,
        "success_bytes": success_bytes,
        "total_bytes": attempted_bytes,
        "total_inventory_bytes": total_inventory_bytes,
        "completed_batches": completed_batches,
    }
