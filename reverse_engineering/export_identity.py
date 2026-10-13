"""Export integrity, inventory bundle identity, and qualification gates."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Literal

from reverse_engineering.manifest import (
    decompilation_success_stats,
    export_completeness,
    inventory_bundle_digest,
    inventory_digest,
    load_batch_manifest,
    validate_batch_file,
)
from reverse_engineering.paths import ReError

Mode = Literal[
    "inventory",
    "inventory_regen",
    "manifest",
    "decompile",
    "reconcile",
    "index",
    "qualify",
    "census_write",
]


def _warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


def read_ghidra_version(ghidra_root: Path) -> str:
    props = ghidra_root / "Ghidra" / "application.properties"
    if props.is_file():
        for line in props.read_text().splitlines():
            if line.startswith("application.version="):
                return line.split("=", 1)[1].strip()
    return "unknown"


def inventory_staging_dir(exp: Path) -> Path:
    return exp / "inventory.staging"


def inventory_dir_paths(inv: Path) -> tuple[Path, Path, Path]:
    return inv / "functions.jsonl", inv / "call_edges.jsonl", inv / "coverage.json"


def inventory_complete(inv: Path) -> bool:
    f, e, c = inventory_dir_paths(inv)
    return f.is_file() and e.is_file() and c.is_file()


def list_validated_batch_ids(decomp_dir: Path, manifest: dict[str, Any] | None = None) -> list[str]:
    if manifest is None:
        manifest_path = decomp_dir / "batch_manifest.json"
        if not manifest_path.is_file():
            return []
        manifest = load_batch_manifest(manifest_path)
    validated: list[str] = []
    for batch_id, expected in manifest.get("batches", {}).items():
        if not re.fullmatch(r"batch-\d{5}", batch_id):
            continue
        path = decomp_dir / f"{batch_id}.jsonl"
        if not validate_batch_file(path, expected):
            validated.append(batch_id)
    return validated


def smoke_qualified_from_payload(payload: dict[str, Any], expected_needles: tuple[str, ...]) -> bool:
    anchors = payload.get("anchors", [])
    if len(anchors) != len(expected_needles):
        return False
    by_needle: dict[str, dict[str, Any]] = {}
    for entry in anchors:
        needle = entry.get("needle")
        if needle is None or needle in by_needle:
            return False
        by_needle[needle] = entry
    for needle in expected_needles:
        entry = by_needle.get(needle)
        if entry is None:
            return False
        if not entry.get("success"):
            return False
        if entry.get("matched") is None:
            return False
    return True


def batch_set_sha256(manifest: dict[str, Any], decomp_dir: Path) -> str:
    """SHA-256 over manifest batch files in sorted batch-id order (raw file bytes)."""
    digest = hashlib.sha256()
    for batch_id in sorted(manifest.get("batches", {})):
        if not re.fullmatch(r"batch-\d{5}", batch_id):
            continue
        path = decomp_dir / f"{batch_id}.jsonl"
        if not path.is_file():
            raise ReError(f"missing batch file for digest: {batch_id}")
        digest.update(batch_id.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def publish_inventory_from_staging(staging: Path, inv: Path) -> None:
    if not inventory_complete(staging):
        raise ReError("inventory staging incomplete")
    backup = inv.parent / "inventory.backup"
    if backup.exists():
        raise ReError("existing inventory backup; inspect or recover it first")
    if inv.exists():
        if not inventory_complete(inv):
            raise ReError("inventory exists but is incomplete; repair before publishing staging")
        if inventory_bundle_digest(inv) == inventory_bundle_digest(staging):
            shutil.rmtree(staging)
            return
        inv.rename(backup)
    inv.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(staging, inv)
    except Exception:
        if backup.exists():
            if inv.exists():
                shutil.rmtree(inv)
            backup.rename(inv)
        raise


def remove_inventory_backup(exp: Path) -> None:
    backup = exp / "inventory.backup"
    if backup.exists():
        shutil.rmtree(backup)


def validate_export_identity(
    exp: Path,
    provenance: dict[str, Any],
    mode: Mode,
    *,
    exporter_script_hash: str | None = None,
) -> None:
    inv = exp / "inventory"
    if mode in ("inventory",):
        if not provenance.get("baseline_revision"):
            raise ReError("missing baseline; run import first")
        return
    if mode == "inventory_regen":
        return
    if not inventory_complete(inv):
        raise ReError("inventory bundle incomplete; run inventory first")
    bundle = inventory_bundle_digest(inv)
    if provenance.get("inventory_bundle_digest") and provenance["inventory_bundle_digest"] != bundle:
        _warn("inventory bundle digest drift vs provenance")
    if mode == "reconcile":
        return
    if mode == "manifest":
        return
    manifest_path = exp / "decompiled" / "batch_manifest.json"
    if mode in ("decompile", "index", "qualify", "census_write"):
        if not manifest_path.is_file():
            raise ReError("missing batch manifest; run manifest first")
        manifest = load_batch_manifest(manifest_path)
        if manifest.get("inventory_bundle_digest") and manifest["inventory_bundle_digest"] != bundle:
            _warn("manifest inventory_bundle_digest stale vs current inventory")
        if exporter_script_hash and manifest.get("exporter_script_hash") != exporter_script_hash:
            _warn("manifest exporter script hash mismatch vs current ExportDecompilation.java")
        if manifest.get("baseline_revision") != provenance.get("baseline_revision"):
            _warn("manifest baseline_revision mismatch vs provenance")
        if mode == "qualify":
            fn_digest = inventory_digest(inv / "functions.jsonl")
            if manifest.get("inventory_digest") != fn_digest:
                raise ReError(
                    "batch manifest inventory_digest stale vs functions.jsonl; run corpus manifest"
                )
    if mode in ("index", "qualify"):
        return
    if mode == "census_write":
        if not provenance.get("qualification", {}).get("passed"):
            raise ReError("export not qualified; run corpus qualify")


def assert_no_stray_batches(decomp_dir: Path, manifest: dict[str, Any]) -> list[str]:
    allowed = {f"{bid}.jsonl" for bid in manifest.get("batches", {})}
    errors: list[str] = []
    for path in decomp_dir.glob("batch-*.jsonl"):
        if path.name not in allowed:
            errors.append(f"stray batch file: {path.name}")
    return errors


def invalidate_qualification(provenance: dict[str, Any], reason: str) -> None:
    provenance["qualification"] = {"passed": False, "invalidated_reason": reason}


def compute_qualification(
    exp: Path,
    provenance: dict[str, Any],
    min_success_bytes: float,
    mandatory_anchor_check: Callable[[], int],
    *,
    smoke_needles: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
    decomp_dir = exp / "decompiled"
    functions_path = exp / "inventory" / "functions.jsonl"
    c_export, export_errors = export_completeness(manifest, decomp_dir)
    stats = decompilation_success_stats(manifest, decomp_dir, functions_path)
    stray = assert_no_stray_batches(decomp_dir, manifest)
    anchors_ok = mandatory_anchor_check() == 0
    smoke_payload = provenance.get("smoke_decompile") or {}
    if smoke_needles:
        smoke_ok = smoke_qualified_from_payload(smoke_payload, smoke_needles)
    else:
        smoke_ok = bool(provenance.get("smoke_qualified"))
    passed = (
        c_export >= 1.0
        and not stray
        and anchors_ok
        and smoke_ok
        and stats["c_success_bytes"] >= min_success_bytes
    )
    qual: dict[str, Any] = {
        "passed": passed,
        "c_export": c_export,
        "export_errors": export_errors + stray,
        "decompilation_stats": stats,
        "min_success_bytes_threshold": min_success_bytes,
        "mandatory_anchors_ok": anchors_ok,
        "smoke_qualified": smoke_ok,
    }
    if passed:
        qual["batch_set_sha256"] = batch_set_sha256(manifest, decomp_dir)
        qual["inventory_bundle_digest"] = inventory_bundle_digest(exp / "inventory")
    return qual


def assert_qualification_current(
    exp: Path,
    provenance: dict[str, Any],
    mandatory_anchor_check: Callable[[], int],
    *,
    smoke_needles: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    validate_export_identity(exp, provenance, "census_write")
    stored = provenance.get("qualification") or {}
    if not stored.get("passed"):
        raise ReError("export not qualified; run corpus qualify")
    if "min_success_bytes_threshold" not in stored:
        raise ReError("qualification record missing min_success_bytes_threshold; rerun corpus qualify")
    threshold = float(stored["min_success_bytes_threshold"])
    live = compute_qualification(
        exp,
        provenance,
        threshold,
        mandatory_anchor_check,
        smoke_needles=smoke_needles,
    )
    if not live["passed"]:
        raise ReError(
            "qualification no longer valid: "
            f"c_export={live['c_export']}, smoke={live['smoke_qualified']}, "
            f"anchors={live['mandatory_anchors_ok']}, "
            f"c_success_bytes={live['decompilation_stats'].get('c_success_bytes')}"
        )
    if stored.get("batch_set_sha256") != live.get("batch_set_sha256"):
        raise ReError("batch_set_sha256 mismatch; batches changed since qualification")
    if stored.get("inventory_bundle_digest") != live.get("inventory_bundle_digest"):
        raise ReError("inventory_bundle_digest mismatch since qualification")
    return live


def publish_batch_staging(staging: Path, final: Path, expected_rvas: list[str]) -> None:
    errors = validate_batch_file(staging, expected_rvas)
    if errors:
        raise ReError("batch staging validation failed: " + "; ".join(errors))
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(final)
