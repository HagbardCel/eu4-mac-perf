"""Deterministic decompilation batch manifests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from reverse_engineering.intervals import format_rva, parse_rva

TERMINAL_STATUSES = frozenset({"success", "failed", "timeout", "skipped"})
BATCH_NAME_RE = re.compile(r"^batch-(\d{5})\.jsonl$")


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
        rva = row.get("entry_rva")
        status = row.get("status")
        if rva in seen:
            errors.append(f"{path.name}: duplicate entry_rva {rva}")
        seen[rva] = row
        if status not in TERMINAL_STATUSES:
            errors.append(f"{path.name}: non-terminal status for {rva}: {status}")
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


def decompilation_success_stats(
    manifest: dict[str, Any],
    decompiled_dir: Path,
    functions_jsonl: Path,
) -> dict[str, float]:
    bytes_by_rva: dict[str, int] = {}
    for line in functions_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") != "internal":
            continue
        bytes_by_rva[row["entry_rva"]] = int(row.get("decoded_instruction_bytes", 0))

    success_count = 0
    total_count = 0
    success_bytes = 0
    total_bytes = 0
    for batch_id, expected in manifest.get("batches", {}).items():
        batch_path = decompiled_dir / f"{batch_id}.jsonl"
        if not batch_path.is_file():
            continue
        for line in batch_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rva = row["entry_rva"]
            total_count += 1
            size = bytes_by_rva.get(rva, 0)
            total_bytes += size
            if row.get("status") == "success":
                success_count += 1
                success_bytes += size
    return {
        "c_success_count": success_count / total_count if total_count else 0.0,
        "c_success_bytes": success_bytes / total_bytes if total_bytes else 0.0,
        "success_count": success_count,
        "total_count": total_count,
        "success_bytes": success_bytes,
        "total_bytes": total_bytes,
    }
