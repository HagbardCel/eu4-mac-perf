#!/usr/bin/env python3
"""Frozen renderer function scope: validation and batch-plan generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.manifest import inventory_bundle_digest, inventory_digest
from reverse_engineering.paths import (
    RE_DIR,
    ReError,
    binary_sha256,
    load_target,
    local_binary_root,
    resolve_export_dir,
)

ROOT = RE_DIR.parent
DEFAULT_SCOPE_DIR = ROOT / "analysis" / "renderer-corpus" / "scope" / "v1"
ARTIFACTS_EVIDENCE_ZIP = ROOT / "artifacts" / "eu4-callsite-evidence.zip"

BATCH_PLAN_SCHEMA = 1
BATCH_ID_PREFIX = "gfx-batch-"
FROZEN_RENDERER_BATCH_SIZE = 100

SCOPE_HASH_FILES = (
    "README.md",
    "IMPLEMENTATION_PLAN.md",
    "manifest-summary.json",
    "renderer-functions.jsonl",
    "renderer-entry-rvas.txt",
    "renderer-functions.tsv",
    "stage2-deep-targets.jsonl",
    "stage2-deep-entry-rvas.txt",
    "graphics-boundary-thunks.jsonl",
)

SOURCE_INPUT_PATHS = (
    "inventory/functions.jsonl",
    "inventory/call_edges.jsonl",
    "audits/callsites-v1.jsonl",
)

PRIORITY_ENTRIES: tuple[tuple[str, str], ...] = (
    ("0x0000000000ed4ac8", "CEU3GraphicalMap::Render"),
    ("0x00000000010cbb00", "CPdxMapBorderLayer::DrawBorders"),
    ("0x00000000014c7da4", "CPdxMeshObject::RenderBuckets"),
    ("0x00000000015ea6df", "_GfxBeginScene"),
    ("0x00000000015ea789", "_GfxDraw"),
    ("0x00000000015eaa77", "_GfxDrawIndexed"),
    ("0x00000000015ebbb0", "_GfxSetShader"),
    ("0x00000000015ebc40", "SShaderOpenGL::SetAll"),
    ("0x00000000015ebf41", "_GfxSetVertexBuffers"),
)

IDENTITY_FIELDS = (
    "kind",
    "name_mangled",
    "name_demangled",
    "namespace",
    "instruction_count",
    "decoded_instruction_bytes",
    "instruction_ranges",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_summary(scope_dir: Path) -> dict[str, Any]:
    path = scope_dir / "manifest-summary.json"
    if not path.is_file():
        raise ReError(f"missing scope summary: {path}")
    return json.loads(path.read_text())


def load_scope_hashes(scope_dir: Path) -> dict[str, str]:
    path = scope_dir / "scope-v1.sha256"
    if not path.is_file():
        raise ReError(f"missing scope hash sidecar: {path}")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise ReError(f"invalid scope-v1.sha256 line: {line}")
        digest, name = parts
        out[name] = digest
    return out


def canonical_batch_plan_bytes(plan: dict[str, Any]) -> bytes:
    return (json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n").encode()


def normalize_ranges(ranges: list[dict[str, Any]]) -> list[tuple[str, str]]:
    normalized: list[tuple[str, str]] = []
    for item in ranges:
        normalized.append((item["start_rva"], item["end_rva"]))
    normalized.sort()
    return normalized


def inventory_identity_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in IDENTITY_FIELDS}


def is_gfx_helper(row: dict[str, Any]) -> bool:
    demangled = row.get("name_demangled") or ""
    mangled = row.get("name_mangled") or ""
    return demangled.startswith("_Gfx") or mangled.startswith("_Gfx")


def load_provenance(exp: Path) -> dict[str, Any]:
    path = exp / "provenance.json"
    if not path.is_file():
        raise ReError(f"missing provenance: {path}")
    return json.loads(path.read_text())


def read_rva_lines(path: Path) -> list[str]:
    rvas: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rvas.append(format_rva(parse_rva(line)))
    return rvas


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ReError(f"{path.name} line {line_no}: invalid json: {exc}") from exc
    return rows


def verify_scope_file_hashes(scope_dir: Path, summary: dict[str, Any]) -> None:
    expected_hashes = load_scope_hashes(scope_dir)
    for name in SCOPE_HASH_FILES:
        if name not in expected_hashes:
            raise ReError(f"scope-v1.sha256 missing entry for {name}")
        path = scope_dir / name
        if not path.is_file():
            raise ReError(f"missing scope file: {name}")
        actual = sha256_file(path)
        if actual != expected_hashes[name]:
            raise ReError(f"scope file hash mismatch for {name}")
    if sha256_file(scope_dir / "renderer-functions.jsonl") != summary["selection_manifest_sha256"]:
        raise ReError("selection_manifest_sha256 mismatch vs renderer-functions.jsonl")
    if sha256_file(scope_dir / "renderer-entry-rvas.txt") != summary["rva_list_sha256"]:
        raise ReError("rva_list_sha256 mismatch vs renderer-entry-rvas.txt")
    deep_path = scope_dir / "stage2-deep-entry-rvas.txt"
    if sha256_file(deep_path) != summary["deep_rva_list_sha256"]:
        raise ReError("deep_rva_list_sha256 mismatch vs stage2-deep-entry-rvas.txt")


def resolved_baseline_revision(scope_dir: Path, cli_revision: str | None) -> str:
    summary = load_summary(scope_dir)
    return verify_summary_identity(summary, cli_revision)


def verify_summary_identity(summary: dict[str, Any], baseline_revision: str | None) -> str:
    target = load_target()
    revision = summary.get("baseline_revision")
    if not revision:
        raise ReError("manifest-summary missing baseline_revision")
    if baseline_revision is not None and baseline_revision != revision:
        raise ReError(
            f"baseline_revision {baseline_revision!r} does not match frozen summary {revision!r}"
        )
    if summary.get("binary_sha256") != target["sha256"]:
        raise ReError("manifest-summary binary_sha256 mismatch vs target.json")
    return revision


def verify_provenance_identity(exp: Path, summary: dict[str, Any], revision: str) -> str:
    provenance = load_provenance(exp)
    if provenance.get("baseline_revision") != revision:
        raise ReError("provenance baseline_revision mismatch vs frozen summary")
    if provenance.get("binary_sha256") != summary.get("binary_sha256"):
        raise ReError("provenance binary_sha256 mismatch vs frozen summary")
    if provenance.get("ghidra_version") != summary.get("ghidra_version"):
        raise ReError("provenance ghidra_version mismatch vs frozen summary")
    inv = exp / "inventory"
    if not (inv / "functions.jsonl").is_file():
        raise ReError("inventory/functions.jsonl missing")
    fn_digest = inventory_digest(inv / "functions.jsonl")
    bundle_digest = inventory_bundle_digest(inv)
    if summary.get("inventory_digest") != fn_digest:
        raise ReError("inventory_digest mismatch vs local inventory/functions.jsonl")
    if summary.get("inventory_bundle_digest") != bundle_digest:
        raise ReError("inventory_bundle_digest mismatch vs local inventory bundle")
    if provenance.get("inventory_digest") != fn_digest:
        raise ReError("provenance inventory_digest missing or mismatched")
    if provenance.get("inventory_bundle_digest") != bundle_digest:
        raise ReError("provenance inventory_bundle_digest missing or mismatched")
    return bundle_digest


def verify_source_inputs(exp: Path, summary: dict[str, Any]) -> None:
    expected = summary.get("source_inputs_sha256") or {}
    for rel in SOURCE_INPUT_PATHS:
        path = exp / rel
        if not path.is_file():
            raise ReError(f"missing source input for scope validation: {rel}")
        actual = sha256_file(path)
        want = expected.get(rel)
        if not want:
            raise ReError(f"manifest-summary missing source_inputs_sha256 for {rel}")
        if actual != want:
            raise ReError(f"source input hash mismatch for {rel}")
    zip_path = ARTIFACTS_EVIDENCE_ZIP
    if zip_path.is_file():
        actual_zip = sha256_file(zip_path)
        want_zip = summary.get("source_zip_sha256")
        if want_zip and actual_zip != want_zip:
            raise ReError("artifacts/eu4-callsite-evidence.zip SHA-256 mismatch vs summary")


def build_inventory_index(functions_path: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for line in functions_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        index[row["entry_rva"]] = row
    return index


def verify_selection_semantics(scope_dir: Path, summary: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> list[str]:
    """Return ordered canonical RVAs for batch planning."""
    manifest_path = scope_dir / "renderer-functions.jsonl"
    rva_path = scope_dir / "renderer-entry-rvas.txt"
    deep_manifest_path = scope_dir / "stage2-deep-targets.jsonl"
    deep_rva_path = scope_dir / "stage2-deep-entry-rvas.txt"
    thunk_path = scope_dir / "graphics-boundary-thunks.jsonl"

    selected_rows = load_jsonl(manifest_path)
    rva_list = read_rva_lines(rva_path)
    deep_rows = load_jsonl(deep_manifest_path)
    deep_rva_list = read_rva_lines(deep_rva_path)
    thunk_rows = load_jsonl(thunk_path)

    if len(selected_rows) != summary["selected_internal_functions"]:
        raise ReError("selected function count mismatch vs manifest-summary")
    if len(rva_list) != len(selected_rows):
        raise ReError("renderer-entry-rvas.txt length mismatch vs manifest")
    if len(deep_rows) != summary["deep_shortlist_functions"]:
        raise ReError("deep target count mismatch vs manifest-summary")
    if len(thunk_rows) != summary["graphics_boundary_thunks"]:
        raise ReError("thunk count mismatch vs manifest-summary")

    seen: set[str] = set()
    ordered_rvas: list[str] = []
    deep_from_main: dict[str, dict[str, Any]] = {}
    tiers: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    total_instructions = 0
    total_bytes = 0

    for index, row in enumerate(selected_rows):
        rva = row["entry_rva"]
        if rva in seen:
            raise ReError(f"duplicate selected entry_rva: {rva}")
        seen.add(rva)
        ordered_rvas.append(rva)
        if rva != rva_list[index]:
            raise ReError(f"RVA list order mismatch at index {index}: {rva} vs {rva_list[index]}")
        if row.get("kind") != "internal":
            raise ReError(f"selected function {rva} is not internal")
        inv_row = inventory.get(rva)
        if inv_row is None:
            raise ReError(f"selected RVA {rva} missing from inventory")
        if inventory_identity_row(inv_row) != inventory_identity_row(row):
            raise ReError(f"inventory identity mismatch for {rva}")
        if row.get("stage2_deep_candidate"):
            deep_from_main[rva] = row
        tiers[row.get("tier") or ""] += 1
        domains[row.get("domain") or ""] += 1
        total_instructions += int(row.get("instruction_count", 0))
        total_bytes += int(row.get("decoded_instruction_bytes", 0))

    if ordered_rvas != sorted(ordered_rvas, key=parse_rva):
        raise ReError("renderer-functions.jsonl is not sorted by entry RVA")

    deep_rvas = [row["entry_rva"] for row in deep_rows]
    if deep_rvas != deep_rva_list:
        raise ReError("stage2-deep-entry-rvas.txt does not match deep manifest order")
    if set(deep_rvas) != set(deep_from_main):
        raise ReError("deep targets are not exactly the stage2_deep_candidate subset")
    for rva, main_row in deep_from_main.items():
        deep_row = next(row for row in deep_rows if row["entry_rva"] == rva)
        if deep_row != main_row:
            raise ReError(f"deep manifest row differs from main manifest for {rva}")

    thunk_rvas = {row["entry_rva"] for row in thunk_rows}
    if len(thunk_rvas) != len(thunk_rows):
        raise ReError("duplicate thunk entry_rva in graphics-boundary-thunks.jsonl")
    overlap = thunk_rvas.intersection(seen)
    if overlap:
        raise ReError(f"thunk RVAs overlap decompilation list: {sorted(overlap)[:3]}")
    for row in thunk_rows:
        rva = row["entry_rva"]
        inv_row = inventory.get(rva)
        if inv_row is None or inv_row.get("kind") != "thunk":
            raise ReError(f"thunk {rva} is not a thunk in inventory")
        if row.get("status") != "boundary_only":
            raise ReError(f"thunk {rva} missing boundary_only status")

    gfx_inventory = {rva for rva, row in inventory.items() if is_gfx_helper(row)}
    missing_gfx = gfx_inventory - seen
    if missing_gfx:
        raise ReError(f"missing _Gfx* helpers from selection: {sorted(missing_gfx)[:5]}")
    if summary.get("all_92_gfx_helpers_present"):
        if len(gfx_inventory) != 92:
            raise ReError(f"expected 92 inventory _Gfx* helpers, found {len(gfx_inventory)}")

    for rva, label in PRIORITY_ENTRIES:
        inv_row = inventory.get(rva)
        if inv_row is None:
            raise ReError(f"priority function missing from inventory: {label} {rva}")
        if rva not in seen:
            raise ReError(f"priority function missing from selection: {label} {rva}")
        sel_row = next(row for row in selected_rows if row["entry_rva"] == rva)
        demangled = sel_row.get("name_demangled") or ""
        if demangled != label:
            raise ReError(f"priority label mismatch for {rva}: {demangled!r} vs {label!r}")

    if tiers != Counter(summary.get("tiers", {})):
        raise ReError(f"tier counts mismatch: {dict(tiers)} vs summary")
    if domains != Counter(summary.get("subsystem_counts", {})):
        raise ReError(f"subsystem counts mismatch: {dict(domains)} vs summary")
    if total_instructions != summary.get("decoded_instructions_selected"):
        raise ReError("decoded instruction count mismatch vs summary")
    if total_bytes != summary.get("decoded_bytes_selected"):
        raise ReError("decoded byte count mismatch vs summary")

    return ordered_rvas


def validate_scope(
    *,
    scope_dir: Path,
    exp: Path,
    baseline_revision: str | None,
) -> tuple[dict[str, Any], str, list[str]]:
    summary = load_summary(scope_dir)
    verify_scope_file_hashes(scope_dir, summary)
    revision = verify_summary_identity(summary, baseline_revision)
    bundle_digest = verify_provenance_identity(exp, summary, revision)
    verify_source_inputs(exp, summary)
    inventory = build_inventory_index(exp / "inventory" / "functions.jsonl")
    ordered_rvas = verify_selection_semantics(scope_dir, summary, inventory)
    return summary, bundle_digest, ordered_rvas


def build_batch_plan(
    *,
    summary: dict[str, Any],
    bundle_digest: str,
    ordered_rvas: list[str],
    batch_size: int,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ReError("batch_size must be a positive integer")
    batches: dict[str, list[str]] = {}
    for index, start in enumerate(range(0, len(ordered_rvas), batch_size), start=1):
        chunk = ordered_rvas[start : start + batch_size]
        batches[f"{BATCH_ID_PREFIX}{index:05d}"] = chunk
    return {
        "schema": BATCH_PLAN_SCHEMA,
        "baseline_revision": summary["baseline_revision"],
        "selection_manifest_sha256": summary["selection_manifest_sha256"],
        "inventory_bundle_digest": bundle_digest,
        "batch_size": batch_size,
        "selected_function_count": len(ordered_rvas),
        "batches": batches,
    }


def parse_batch_plan(path: Path) -> dict[str, Any]:
    try:
        plan = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ReError(f"batch plan is not valid JSON: {exc}") from exc
    required = (
        "schema",
        "baseline_revision",
        "selection_manifest_sha256",
        "inventory_bundle_digest",
        "batch_size",
        "selected_function_count",
        "batches",
    )
    for key in required:
        if key not in plan:
            raise ReError(f"batch plan missing required field: {key}")
    if plan["schema"] != BATCH_PLAN_SCHEMA:
        raise ReError(f"unsupported batch plan schema: {plan['schema']}")
    if not isinstance(plan["batches"], dict) or not plan["batches"]:
        raise ReError("batch plan batches must be a non-empty object")
    total = 0
    for batch_id, rvas in plan["batches"].items():
        if not batch_id.startswith(BATCH_ID_PREFIX):
            raise ReError(f"unexpected batch id: {batch_id}")
        if not isinstance(rvas, list) or not rvas:
            raise ReError(f"batch {batch_id} must be a non-empty RVA list")
        total += len(rvas)
    if total != plan["selected_function_count"]:
        raise ReError("batch plan selected_function_count does not match batch partition")
    return plan


def _link_batch_plan_bytes(path: Path, new_bytes: bytes) -> str:
    """Create-only publication via hard link; handles concurrent FileExistsError."""
    fd, staging = tempfile.mkstemp(dir=path.parent, prefix=".batch_plan.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(new_bytes)
        try:
            os.link(staging, path)
            return "created"
        except FileExistsError:
            if path.is_file() and path.read_bytes() == new_bytes:
                return "unchanged"
            raise ReError("concurrent batch-plan publication conflict")
    finally:
        Path(staging).unlink(missing_ok=True)


def publish_batch_plan(path: Path, plan: dict[str, Any]) -> str:
    """Return action: created or unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_bytes = canonical_batch_plan_bytes(plan)
    if path.is_file():
        existing = path.read_bytes()
        if existing == new_bytes:
            return "unchanged"
        try:
            parse_batch_plan(path)
        except ReError:
            raise ReError(f"existing batch plan at {path} is malformed; remove it before replacing")
        raise ReError(f"existing batch plan at {path} differs; remove it before replacing")
    return _link_batch_plan_bytes(path, new_bytes)


def snapshot_manifest_dir(exp: Path) -> Path:
    return exp / "renderer_snapshot" / "v1" / "manifest"


def cmd_validate(args: argparse.Namespace) -> int:
    scope_dir = Path(args.scope_dir).expanduser()
    revision = resolved_baseline_revision(scope_dir, args.baseline_revision)
    binary_root = local_binary_root(binary_sha256())
    exp = resolve_export_dir(binary_root, revision)
    summary, _, _ = validate_scope(scope_dir=scope_dir, exp=exp, baseline_revision=revision)
    print(
        json.dumps(
            {
                "status": "ok",
                "baseline_revision": revision,
                "selected_internal_functions": summary["selected_internal_functions"],
                "export_dir": str(exp),
            },
            indent=2,
        )
    )
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    scope_dir = Path(args.scope_dir).expanduser()
    if args.batch_size <= 0:
        raise ReError("batch_size must be a positive integer")
    revision = resolved_baseline_revision(scope_dir, args.baseline_revision)
    binary_root = local_binary_root(binary_sha256())
    exp = resolve_export_dir(binary_root, revision)
    summary, bundle_digest, ordered_rvas = validate_scope(
        scope_dir=scope_dir,
        exp=exp,
        baseline_revision=revision,
    )
    plan = build_batch_plan(
        summary=summary,
        bundle_digest=bundle_digest,
        ordered_rvas=ordered_rvas,
        batch_size=args.batch_size,
    )
    plan_path = snapshot_manifest_dir(exp) / "batch_plan.json"
    action = publish_batch_plan(plan_path, plan)
    print(
        json.dumps(
            {
                "status": "ok",
                "batch_plan_action": action,
                "batch_plan_path": str(plan_path),
                "batch_count": len(plan["batches"]),
                "batch_size": plan["batch_size"],
                "selected_function_count": plan["selected_function_count"],
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Frozen renderer scope validation and batch planning")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("validate", cmd_validate), ("prepare", cmd_prepare)):
        p = sub.add_parser(name)
        p.add_argument(
            "--scope-dir",
            default=str(DEFAULT_SCOPE_DIR),
            help="path to analysis/renderer-corpus/scope/v1",
        )
        p.add_argument(
            "--baseline-revision",
            default=None,
            help="must match manifest-summary baseline_revision when set",
        )
        p.set_defaults(func=func)
    prepare = sub.choices["prepare"]
    prepare.add_argument("--batch-size", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
