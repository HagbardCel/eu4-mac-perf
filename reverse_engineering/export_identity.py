"""Export integrity, inventory bundle identity, and qualification gates."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

from reverse_engineering.manifest import (
    decompilation_success_stats,
    export_completeness,
    inventory_bundle_digest,
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


def read_ghidra_version(ghidra_root: Path) -> str:
    props = ghidra_root / "Ghidra" / "application.properties"
    if props.is_file():
        for line in props.read_text().splitlines():
            if line.startswith("application.version="):
                return line.split("=", 1)[1].strip()
    return "unknown"


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


def has_validated_batches(exp: Path) -> bool:
    decomp = exp / "decompiled"
    return bool(list_validated_batch_ids(decomp))


def manifest_locked(exp: Path) -> bool:
    return has_validated_batches(exp)


def publish_inventory_from_staging(staging: Path, inv: Path) -> None:
    if inv.exists():
        shutil.rmtree(inv)
    shutil.copytree(staging, inv)


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
        if manifest_locked(exp):
            raise ReError("cannot regenerate inventory: validated decompilation batches exist")
        return
    if not inventory_complete(inv):
        raise ReError("inventory bundle incomplete; run inventory first")
    bundle = inventory_bundle_digest(inv)
    if provenance.get("inventory_bundle_digest") and provenance["inventory_bundle_digest"] != bundle:
        raise ReError("inventory bundle digest mismatch; create new baseline_revision")
    if mode == "reconcile":
        return
    if mode == "manifest":
        if manifest_locked(exp):
            raise ReError("cannot overwrite manifest: validated batches exist")
        return
    manifest_path = exp / "decompiled" / "batch_manifest.json"
    if mode in ("decompile", "index", "qualify", "census_write"):
        if not manifest_path.is_file():
            raise ReError("missing batch manifest; run manifest first")
        manifest = load_batch_manifest(manifest_path)
        if manifest.get("inventory_bundle_digest") and manifest["inventory_bundle_digest"] != bundle:
            raise ReError("manifest inventory_bundle_digest stale")
        if exporter_script_hash and manifest.get("exporter_script_hash") != exporter_script_hash:
            raise ReError("manifest exporter script hash mismatch")
        if manifest.get("baseline_revision") != provenance.get("baseline_revision"):
            raise ReError("manifest baseline_revision mismatch")
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


def compute_qualification(
    exp: Path,
    provenance: dict[str, Any],
    min_success_bytes: float,
    mandatory_anchor_check,
) -> dict[str, Any]:
    manifest = load_batch_manifest(exp / "decompiled" / "batch_manifest.json")
    decomp_dir = exp / "decompiled"
    functions_path = exp / "inventory" / "functions.jsonl"
    c_export, export_errors = export_completeness(manifest, decomp_dir)
    stats = decompilation_success_stats(manifest, decomp_dir, functions_path)
    stray = assert_no_stray_batches(decomp_dir, manifest)
    anchors_ok = mandatory_anchor_check() == 0
    smoke_ok = provenance.get("smoke_qualified", False)
    passed = (
        c_export >= 1.0
        and not stray
        and anchors_ok
        and smoke_ok
        and stats["c_success_bytes"] >= min_success_bytes
    )
    return {
        "passed": passed,
        "c_export": c_export,
        "export_errors": export_errors + stray,
        "decompilation_stats": stats,
        "min_success_bytes_threshold": min_success_bytes,
        "mandatory_anchors_ok": anchors_ok,
        "smoke_qualified": smoke_ok,
    }


def publish_batch_staging(staging: Path, final: Path, expected_rvas: list[str]) -> None:
    errors = validate_batch_file(staging, expected_rvas)
    if errors:
        raise ReError("batch staging validation failed: " + "; ".join(errors))
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(final)
