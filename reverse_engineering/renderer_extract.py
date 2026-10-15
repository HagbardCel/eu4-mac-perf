#!/usr/bin/env python3
"""Restartable renderer snapshot extraction (static evidence + decompilation)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path
from collections.abc import Callable
from typing import Any

from reverse_engineering.corpus import (
    build_analyze_headless_argv,
    ghidra_install,
    load_ghidra_meta,
    script_hash,
    sha256_file,
    verify_binary,
)
from reverse_engineering.export_identity import read_ghidra_version
from reverse_engineering.intervals import format_rva, intervals_from_ranges, merge_intervals, parse_rva
from reverse_engineering.paths import (
    ReError,
    binary_sha256,
    load_target,
    local_binary_root,
    resolve_export_dir,
)
from reverse_engineering.renderer_scope import (
    DEFAULT_SCOPE_DIR,
    FROZEN_RENDERER_BATCH_SIZE,
    PRIORITY_ENTRIES,
    build_batch_plan,
    canonical_batch_plan_bytes,
    is_gfx_helper,
    load_scope_hashes,
    resolved_baseline_revision,
    validate_scope,
)

ROOT = Path(__file__).resolve().parents[1]
BATCH_ID_PREFIX = "gfx-batch-"
ZIP_STATIC = "static.jsonl"
ZIP_DECOMP = "decompiled.jsonl"
ZIP_RECEIPT = "receipt.json"
EXPECTED_INSTRUCTIONS = 281654
DECOMP_STATUSES = frozenset({"success", "failed", "timeout", "skipped"})
STATIC_STATUSES = frozenset({"success", "failed"})
CENSUS_FIELD_KEYS = (
    "pcode_kind",
    "flow_type_is_call",
    "pcode_is_call",
    "flow_instruction_class",
)
EXPECTED_DECODED_BYTES = 1145487
DEFAULT_DECOMP_TIMEOUT = 120
DEFAULT_PROCESS_TIMEOUT = 3600


def snapshot_root(exp: Path) -> Path:
    return exp / "renderer_snapshot" / "v1"


def batches_dir(exp: Path) -> Path:
    return snapshot_root(exp) / "batches"


def batch_zip_path(exp: Path, batch_id: str) -> Path:
    return batches_dir(exp) / f"{batch_id}.zip"


def load_batch_plan(exp: Path) -> dict[str, Any]:
    path = snapshot_root(exp) / "manifest" / "batch_plan.json"
    if not path.is_file():
        raise ReError(f"missing batch plan: {path}; run renderer_scope prepare first")
    return json.loads(path.read_text())


def assert_frozen_batch_plan(
    saved: dict[str, Any],
    summary: dict[str, Any],
    bundle_digest: str,
    ordered_rvas: list[str],
) -> None:
    expected = build_batch_plan(
        summary=summary,
        bundle_digest=bundle_digest,
        ordered_rvas=ordered_rvas,
        batch_size=FROZEN_RENDERER_BATCH_SIZE,
    )
    if canonical_batch_plan_bytes(saved) != canonical_batch_plan_bytes(expected):
        raise ReError(
            f"batch_plan.json does not match frozen scope partition (batch_size={FROZEN_RENDERER_BATCH_SIZE})"
        )


def load_frozen_context(
    scope_dir: Path,
    baseline_revision: str | None,
) -> tuple[Path, dict[str, Any], dict[str, Any], str, list[str], str]:
    revision = resolved_baseline_revision(scope_dir, baseline_revision)
    binary_root = local_binary_root(binary_sha256())
    exp = resolve_export_dir(binary_root, revision)
    summary, bundle_digest, ordered_rvas = validate_scope(
        scope_dir=scope_dir, exp=exp, baseline_revision=revision
    )
    plan = load_batch_plan(exp)
    assert_frozen_batch_plan(plan, summary, bundle_digest, ordered_rvas)
    return exp, summary, plan, bundle_digest, ordered_rvas, revision


def batch_rva_digest(rvas: list[str]) -> str:
    payload = "\n".join(rvas) + ("\n" if rvas else "")
    return hashlib.sha256(payload.encode()).hexdigest()


def exporter_hashes() -> dict[str, str]:
    return {
        "ExportRendererStatic.java": script_hash("ExportRendererStatic.java"),
        "ExportDecompilation.java": script_hash("ExportDecompilation.java"),
    }


def load_jsonl_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ReError(f"jsonl line {line_no}: {exc}") from exc
    return rows


def load_inventory_index(exp: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    path = exp / "inventory" / "functions.jsonl"
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        index[row["entry_rva"]] = row
    return index


def load_callsites_by_caller(exp: Path) -> dict[str, list[dict[str, Any]]]:
    path = exp / "audits" / "callsites-v1.jsonl"
    if not path.is_file():
        raise ReError(f"missing callsite census: {path}")
    by_caller: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        caller = row.get("caller_rva")
        if caller:
            by_caller[caller].append(row)
    return by_caller


def normalized_ranges(ranges: list[dict[str, Any]]) -> list[tuple[int, int]]:
    return merge_intervals(intervals_from_ranges(ranges))


def static_census_call_rvas(static_row: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for ins in static_row.get("instructions") or []:
        if ins.get("census_call"):
            out.add(ins["instruction_rva"])
    return out


def census_call_rvas_for_function(callsites: list[dict[str, Any]], entry_rva: str) -> set[str]:
    return {row["callsite_rva"] for row in callsites if row.get("caller_rva") == entry_rva}


def intervals_contained(inner: list[tuple[int, int]], outer: list[tuple[int, int]]) -> bool:
    if not inner:
        return True
    if not outer:
        return False
    for i_start, i_end in inner:
        covered = False
        for o_start, o_end in outer:
            if i_start >= o_start and i_end <= o_end:
                covered = True
                break
        if not covered:
            return False
    return True


def ranges_to_tuples(ranges: list[dict[str, Any]]) -> list[tuple[int, int]]:
    return [(parse_rva(item["start_rva"]), parse_rva(item["end_rva"])) for item in ranges]


def reconstruct_instruction_evidence(
    instructions: list[dict[str, Any]],
) -> tuple[int, int, list[tuple[int, int]]]:
    if not instructions:
        raise ReError("instructions array is empty")
    intervals: list[tuple[int, int]] = []
    total_bytes = 0
    for ins in instructions:
        rva_str = ins.get("instruction_rva")
        length = ins.get("length")
        bytes_hex = ins.get("bytes_hex")
        if rva_str is None or length is None or bytes_hex is None:
            raise ReError("instruction missing instruction_rva, length, or bytes_hex")
        length = int(length)
        if length <= 0:
            raise ReError(f"invalid instruction length at {rva_str}")
        if len(bytes_hex) != 2 * length:
            raise ReError(f"bytes_hex length mismatch at {rva_str}")
        try:
            int(bytes_hex, 16)
        except ValueError as exc:
            raise ReError(f"invalid bytes_hex at {rva_str}: {exc}") from exc
        start = parse_rva(rva_str)
        end = start + length
        intervals.append((start, end))
        total_bytes += length
    sorted_iv = sorted(intervals)
    for index in range(1, len(sorted_iv)):
        prev_end = sorted_iv[index - 1][1]
        cur_start = sorted_iv[index][0]
        if cur_start < prev_end:
            raise ReError("overlapping or duplicate instruction intervals")
    merged = merge_intervals(intervals)
    return len(instructions), total_bytes, merged


def validate_callsite_classification(
    static_row: dict[str, Any],
    callsites: list[dict[str, Any]],
) -> None:
    entry = static_row["entry_rva"]
    census_by_site = {row["callsite_rva"]: row for row in callsites if row.get("caller_rva") == entry}
    static_calls = static_census_call_rvas(static_row)
    census_calls = set(census_by_site)
    if static_calls != census_calls:
        raise ReError(
            f"{entry}: callsite RVA set mismatch (static={len(static_calls)} census={len(census_calls)})"
        )
    for ins in static_row.get("instructions") or []:
        if not ins.get("census_call"):
            continue
        site = ins["instruction_rva"]
        census = census_by_site.get(site)
        if census is None:
            raise ReError(f"{entry}: census_call at {site} missing from census")
        for key in CENSUS_FIELD_KEYS:
            static_val = ins.get(key)
            census_val = census.get(key)
            if static_val != census_val:
                raise ReError(f"{entry}: callsite {site} field {key} mismatch (static={static_val!r} census={census_val!r})")


def validate_static_failed_row(static_row: dict[str, Any], expected_rva: str) -> None:
    entry = format_rva(parse_rva(expected_rva))
    if static_row.get("entry_rva") != entry:
        raise ReError(f"failed static entry_rva mismatch: expected {entry}")
    if static_row.get("status") != "failed":
        raise ReError(f"{entry}: expected failed static status")
    error = static_row.get("error")
    if not error or not str(error).strip():
        raise ReError(f"{entry}: failed static row requires nonempty error")
    instructions = static_row.get("instructions") or []
    if instructions:
        raise ReError(f"{entry}: failed static row must not include instructions")


def validate_static_success_row(
    static_row: dict[str, Any],
    inv_row: dict[str, Any],
    callsites: list[dict[str, Any]],
) -> tuple[int, int]:
    entry = static_row["entry_rva"]
    instructions = static_row.get("instructions") or []
    count, nbytes, decoded_ranges = reconstruct_instruction_evidence(instructions)
    if int(static_row.get("instruction_count", -1)) != count:
        raise ReError(f"{entry}: instruction_count does not match instructions array")
    if int(static_row.get("decoded_instruction_bytes", -1)) != nbytes:
        raise ReError(f"{entry}: decoded_instruction_bytes does not match instructions array")
    header_ranges = normalized_ranges(static_row.get("decoded_instruction_ranges") or [])
    if header_ranges != decoded_ranges:
        raise ReError(f"{entry}: decoded_instruction_ranges do not match instructions array")
    inv_ranges = normalized_ranges(inv_row.get("instruction_ranges") or [])
    if decoded_ranges != inv_ranges:
        raise ReError(f"{entry}: decoded instruction ranges mismatch vs inventory")
    if int(inv_row["instruction_count"]) != count or int(inv_row["decoded_instruction_bytes"]) != nbytes:
        raise ReError(f"{entry}: reconstructed evidence mismatch vs inventory")
    body_ranges = normalized_ranges(static_row.get("body_ranges") or [])
    if not intervals_contained(decoded_ranges, body_ranges):
        raise ReError(f"{entry}: decoded instructions not contained in body_ranges")
    validate_callsite_classification(static_row, callsites)
    return count, nbytes


def validate_decomp_rows(rows: list[dict[str, Any]], expected_rvas: list[str]) -> None:
    if len(rows) != len(expected_rvas):
        raise ReError(f"decompile row count {len(rows)} != expected {len(expected_rvas)}")
    by_rva = {row["entry_rva"]: row for row in rows}
    if len(by_rva) != len(rows):
        raise ReError("duplicate entry_rva in decompiled.jsonl")
    for rva in expected_rvas:
        key = format_rva(parse_rva(rva))
        row = by_rva.get(key)
        if row is None:
            raise ReError(f"missing decompile row for {rva}")
        status = row.get("status")
        if status not in DECOMP_STATUSES:
            raise ReError(f"{key}: invalid decompile status {status!r}")
        if status == "success":
            pseudo = row.get("pseudocode")
            if not pseudo or not str(pseudo).strip():
                raise ReError(f"{key}: success decompile requires nonempty pseudocode")
        elif status == "skipped":
            continue


def build_receipt(
    *,
    static_bytes: bytes,
    decomp_bytes: bytes,
    batch_id: str,
    rvas: list[str],
    plan: dict[str, Any],
    summary: dict[str, Any],
    meta: dict[str, Any],
    ghidra_version: str,
    binary_sha: str,
) -> dict[str, Any]:
    hashes = exporter_hashes()
    return {
        "schema": 1,
        "batch_id": batch_id,
        "baseline_revision": plan["baseline_revision"],
        "selection_manifest_sha256": summary["selection_manifest_sha256"],
        "inventory_bundle_digest": plan["inventory_bundle_digest"],
        "batch_rva_digest": batch_rva_digest(rvas),
        "batch_size": len(rvas),
        "static_sha256": hashlib.sha256(static_bytes).hexdigest(),
        "decompiled_sha256": hashlib.sha256(decomp_bytes).hexdigest(),
        "exporter_script_hashes": hashes,
        "binary_sha256": binary_sha,
        "ghidra_version": ghidra_version,
        "program_name": meta.get("program_name"),
        "image_base": meta.get("image_base"),
        "executable_sha256": meta.get("executable_sha256"),
    }


def expected_receipt_identity(
    *,
    batch_id: str,
    rvas: list[str],
    plan: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    pinned = load_target()
    sha = pinned["sha256"]
    ghidra_meta = load_ghidra_meta(local_binary_root(sha))
    return {
        "batch_id": batch_id,
        "baseline_revision": plan["baseline_revision"],
        "selection_manifest_sha256": summary["selection_manifest_sha256"],
        "inventory_bundle_digest": plan["inventory_bundle_digest"],
        "batch_rva_digest": batch_rva_digest(rvas),
        "batch_size": len(rvas),
        "exporter_script_hashes": exporter_hashes(),
        "binary_sha256": sha,
        "ghidra_version": summary["ghidra_version"],
        "program_name": ghidra_meta.get("program_name"),
        "image_base": ghidra_meta.get("image_base"),
        "executable_sha256": sha.lower(),
    }


def validate_receipt(
    receipt: dict[str, Any],
    static_bytes: bytes,
    decomp_bytes: bytes,
    expected: dict[str, Any],
) -> None:
    if receipt.get("static_sha256") != hashlib.sha256(static_bytes).hexdigest():
        raise ReError("receipt static_sha256 mismatch")
    if receipt.get("decompiled_sha256") != hashlib.sha256(decomp_bytes).hexdigest():
        raise ReError("receipt decompiled_sha256 mismatch")
    identity_keys = (
        "batch_id",
        "baseline_revision",
        "selection_manifest_sha256",
        "inventory_bundle_digest",
        "batch_rva_digest",
        "batch_size",
        "binary_sha256",
        "ghidra_version",
        "program_name",
        "image_base",
    )
    for key in identity_keys:
        if receipt.get(key) != expected.get(key):
            raise ReError(f"receipt {key} mismatch")
    exe_receipt = (receipt.get("executable_sha256") or "").lower()
    exe_expected = (expected.get("executable_sha256") or "").lower()
    if exe_receipt != exe_expected:
        raise ReError("receipt executable_sha256 mismatch")
    if receipt.get("exporter_script_hashes") != expected.get("exporter_script_hashes"):
        raise ReError("receipt exporter_script_hashes mismatch")


def validate_batch_package(
    *,
    batch_id: str,
    static_text: str,
    decomp_text: str,
    receipt: dict[str, Any],
    rvas: list[str],
    inventory: dict[str, dict[str, Any]],
    callsites_by_caller: dict[str, list[dict[str, Any]]],
    plan: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    static_rows = load_jsonl_text(static_text)
    decomp_rows = load_jsonl_text(decomp_text)
    if [row["entry_rva"] for row in static_rows] != [format_rva(parse_rva(r)) for r in rvas]:
        raise ReError("static.jsonl RVAs do not match batch plan order")
    validate_decomp_rows(decomp_rows, rvas)
    expected = expected_receipt_identity(batch_id=batch_id, rvas=rvas, plan=plan, summary=summary)
    validate_receipt(receipt, static_text.encode(), decomp_text.encode(), expected)
    for rva in rvas:
        key = format_rva(parse_rva(rva))
        inv = inventory.get(key)
        if inv is None:
            raise ReError(f"inventory missing {key}")
        static_row = next(row for row in static_rows if row["entry_rva"] == key)
        status = static_row.get("status")
        if status not in STATIC_STATUSES:
            raise ReError(f"{key}: invalid static status {status!r}")
        callsites = callsites_by_caller.get(key, [])
        if status == "failed":
            validate_static_failed_row(static_row, key)
        else:
            validate_static_success_row(static_row, inv, callsites)


def read_zip_package(path: Path) -> tuple[str, str, dict[str, Any]]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = set(zf.namelist())
            required = {ZIP_STATIC, ZIP_DECOMP, ZIP_RECEIPT}
            if names != required:
                raise ReError(f"{path.name}: expected members {required}, got {names}")
            static_text = zf.read(ZIP_STATIC).decode()
            decomp_text = zf.read(ZIP_DECOMP).decode()
            receipt = json.loads(zf.read(ZIP_RECEIPT).decode())
    except zipfile.BadZipFile as exc:
        raise ReError(f"{path.name}: corrupt zip: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ReError(f"{path.name}: zip member decode error: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReError(f"{path.name}: receipt JSON error: {exc}") from exc
    return static_text, decomp_text, receipt


def write_batch_zip(
    static_text: str,
    decomp_text: str,
    receipt: dict[str, Any],
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(ZIP_STATIC, static_text)
        zf.writestr(ZIP_DECOMP, decomp_text)
        zf.writestr(ZIP_RECEIPT, json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    return buf.getvalue()


def _link_create_only(dest: Path, payload: bytes, *, allow_unchanged: bool) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        existing = dest.read_bytes()
        if allow_unchanged and existing == payload:
            return "unchanged"
        if allow_unchanged:
            raise ReError(f"existing package at {dest} differs; use --repair to replace")
        raise ReError(f"restoration conflict: destination occupied ({dest})")
    fd, staging = tempfile.mkstemp(dir=dest.parent, prefix=".batch_pkg.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        try:
            os.link(staging, dest)
            return "created"
        except FileExistsError:
            if allow_unchanged and dest.is_file() and dest.read_bytes() == payload:
                return "unchanged"
            if allow_unchanged:
                raise ReError("concurrent batch-package publication conflict")
            raise ReError(f"restoration conflict: destination occupied ({dest})")
    finally:
        Path(staging).unlink(missing_ok=True)


def publish_create_only(dest: Path, payload: bytes) -> str:
    return _link_create_only(dest, payload, allow_unchanged=True)


def restore_create_only(dest: Path, payload: bytes) -> str:
    if dest.is_file():
        raise ReError(f"restoration conflict: destination occupied ({dest})")
    return _link_create_only(dest, payload, allow_unchanged=False)


def write_repair_archive(archive_dir: Path, stem: str, archive_bytes: bytes) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(dir=archive_dir, prefix=f"{stem}-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(archive_bytes)
        fd2, archive_path = tempfile.mkstemp(dir=archive_dir, prefix=f"{stem}-", suffix=".zip")
        os.close(fd2)
        Path(archive_path).unlink(missing_ok=True)
        os.link(staging, archive_path)
        return Path(archive_path)
    except (OSError, ReError) as exc:
        Path(staging).unlink(missing_ok=True)
        raise ReError(f"failed to write repair archive: {exc}") from exc
    finally:
        Path(staging).unlink(missing_ok=True)


def assert_acquisition_preflight(summary: dict[str, Any]) -> str:
    verify_binary()
    ghidra_version = read_ghidra_version(ghidra_install())
    if ghidra_version == "unknown":
        raise ReError("could not read Ghidra version")
    if ghidra_version != summary.get("ghidra_version"):
        raise ReError("live Ghidra version mismatch vs frozen summary")
    return ghidra_version


def assert_export_meta(meta: dict[str, Any], summary: dict[str, Any]) -> None:
    pinned = load_target()
    ghidra_meta = load_ghidra_meta(local_binary_root(pinned["sha256"]))
    if meta.get("program_name") != ghidra_meta.get("program_name"):
        raise ReError("Ghidra program_name mismatch vs ghidra_project.json")
    if int(meta.get("image_base", 0)) != int(ghidra_meta.get("image_base", 0)):
        raise ReError("image_base mismatch vs ghidra_project.json")
    exe_sha = (meta.get("executable_sha256") or "").lower()
    if exe_sha != pinned["sha256"].lower():
        raise ReError("exporter executable_sha256 mismatch vs pinned binary")


def run_ghidra_batch(
    *,
    binary_root: Path,
    rvas: list[str],
    staging: Path,
    decomp_timeout: int,
    process_timeout: int,
) -> dict[str, Any]:
    meta = load_ghidra_meta(binary_root)
    project_dir = Path(meta["project_dir"])
    project_name = meta["project_name"]
    program_name = meta["program_name"]
    rva_file = staging / "rvas.txt"
    rva_file.write_text("\n".join(format_rva(parse_rva(r)) for r in rvas) + "\n")
    static_out = staging / "static.jsonl"
    decomp_out = staging / "decompiled.jsonl"
    meta_out = staging / "scan-meta.json"
    ghidra = ghidra_install()
    args = build_analyze_headless_argv(
        project_dir,
        project_name,
        "-process",
        program_name,
        "-noanalysis",
        "-readOnly",
        "-postScript",
        "ExportRendererStatic.java",
        str(rva_file),
        str(static_out),
        str(meta_out),
        "-postScript",
        "ExportDecompilation.java",
        str(rva_file),
        str(decomp_out),
        str(decomp_timeout),
    )
    result = subprocess.run(args, capture_output=True, text=True, timeout=process_timeout)
    if result.returncode != 0:
        tail = (result.stdout or "")[-4000:] + (result.stderr or "")[-2000:]
        raise ReError(f"analyzeHeadless failed (exit {result.returncode}):\n{tail}")
    if not static_out.is_file() or not decomp_out.is_file() or not meta_out.is_file():
        raise ReError("Ghidra export outputs missing")
    return json.loads(meta_out.read_text())


def publish_boundary_thunks(scope_dir: Path, exp: Path) -> None:
    expected = load_scope_hashes(scope_dir)
    src_name = "graphics-boundary-thunks.jsonl"
    src = scope_dir / src_name
    actual = sha256_file(src)
    if actual != expected[src_name]:
        raise ReError("graphics-boundary-thunks.jsonl hash mismatch vs scope-v1.sha256")
    dest = snapshot_root(exp) / "boundary" / "opengl-thunks.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.read_bytes() == src.read_bytes():
        return
    if dest.is_file():
        raise ReError(f"boundary file exists and differs: {dest}")
    dest.write_bytes(src.read_bytes())


def repair_publish(dest: Path, payload: bytes, archive_bytes: bytes | None) -> str:
    if archive_bytes is None:
        return publish_create_only(dest, payload)
    archive_path = write_repair_archive(dest.parent / ".archive", dest.stem, archive_bytes)
    if dest.is_file():
        dest.unlink()
    try:
        return publish_create_only(dest, payload)
    except (ReError, OSError) as primary:
        try:
            restore_create_only(dest, archive_bytes)
        except (ReError, OSError) as restore_exc:
            raise ReError(
                f"Replacement publication failed: {primary}. "
                f"Restoration also failed: {restore_exc}. "
                f"Original package preserved at: {archive_path}. "
                f"Destination was not overwritten by restoration."
            ) from restore_exc
        raise ReError(
            f"Replacement publication failed: {primary}. "
            f"Original restored at {dest}. "
            f"Archive retained at {archive_path}."
        ) from primary


def acquire_batch(
    *,
    exp: Path,
    scope_dir: Path,
    plan: dict[str, Any],
    summary: dict[str, Any],
    batch_id: str,
    inventory: dict[str, dict[str, Any]],
    callsites_by_caller: dict[str, list[dict[str, Any]]],
    decomp_timeout: int,
    process_timeout: int,
    repair: bool,
    ensure_preflight: Callable[[], str],
) -> str:
    batches = plan.get("batches") or {}
    if batch_id not in batches:
        raise ReError(f"unknown batch id: {batch_id}")
    rvas = batches[batch_id]
    dest = batch_zip_path(exp, batch_id)
    archive_bytes: bytes | None = None
    if dest.is_file() and not repair:
        try:
            static_text, decomp_text, receipt = read_zip_package(dest)
            validate_batch_package(
                batch_id=batch_id,
                static_text=static_text,
                decomp_text=decomp_text,
                receipt=receipt,
                rvas=rvas,
                inventory=inventory,
                callsites_by_caller=callsites_by_caller,
                plan=plan,
                summary=summary,
            )
            return "unchanged"
        except ReError:
            raise ReError(f"existing package {batch_id} is invalid; use --repair {batch_id}")
    if dest.is_file() and repair:
        archive_bytes = dest.read_bytes()
    ghidra_version = ensure_preflight()
    binary_root = local_binary_root(binary_sha256())
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        meta = run_ghidra_batch(
            binary_root=binary_root,
            rvas=rvas,
            staging=staging,
            decomp_timeout=decomp_timeout,
            process_timeout=process_timeout,
        )
        assert_export_meta(meta, summary)
        static_text = staging.joinpath("static.jsonl").read_text()
        decomp_text = staging.joinpath("decompiled.jsonl").read_text()
        receipt = build_receipt(
            static_bytes=static_text.encode(),
            decomp_bytes=decomp_text.encode(),
            batch_id=batch_id,
            rvas=rvas,
            plan=plan,
            summary=summary,
            meta=meta,
            ghidra_version=ghidra_version,
            binary_sha=binary_sha256(),
        )
        validate_batch_package(
            batch_id=batch_id,
            static_text=static_text,
            decomp_text=decomp_text,
            receipt=receipt,
            rvas=rvas,
            inventory=inventory,
            callsites_by_caller=callsites_by_caller,
            plan=plan,
            summary=summary,
        )
        payload = write_batch_zip(static_text, decomp_text, receipt)
    if repair and archive_bytes is not None:
        return repair_publish(dest, payload, archive_bytes)
    return publish_create_only(dest, payload)


def cmd_run(args: argparse.Namespace) -> int:
    if args.repair and not args.batch_id:
        raise ReError("--repair requires --batch-id")
    scope_dir = Path(args.scope_dir).expanduser()
    exp, summary, plan, _, _, revision = load_frozen_context(scope_dir, args.baseline_revision)
    inventory = load_inventory_index(exp)
    callsites_by_caller = load_callsites_by_caller(exp)
    publish_boundary_thunks(scope_dir, exp)
    batch_ids = sorted(plan.get("batches", {}))
    if args.batch_id:
        batch_ids = [args.batch_id]
    preflight_done = False
    cached_ghidra_version = ""

    def ensure_preflight() -> str:
        nonlocal preflight_done, cached_ghidra_version
        if not preflight_done:
            cached_ghidra_version = assert_acquisition_preflight(summary)
            preflight_done = True
        return cached_ghidra_version

    results: dict[str, str] = {}
    for batch_id in batch_ids:
        action = acquire_batch(
            exp=exp,
            scope_dir=scope_dir,
            plan=plan,
            summary=summary,
            batch_id=batch_id,
            inventory=inventory,
            callsites_by_caller=callsites_by_caller,
            decomp_timeout=args.decomp_timeout,
            process_timeout=args.process_timeout,
            repair=args.repair and args.batch_id == batch_id,
            ensure_preflight=ensure_preflight,
        )
        results[batch_id] = action
        print(f"{batch_id}: {action}", file=sys.stderr)
    print(json.dumps({"status": "ok", "batches": results}, indent=2))
    return 0


def classify_batches(
    exp: Path,
    plan: dict[str, Any],
    summary: dict[str, Any],
    inventory: dict[str, Any],
    callsites_by_caller: dict[str, list[dict[str, Any]]],
) -> tuple[list[str], list[str], dict[str, str]]:
    valid: list[str] = []
    invalid: dict[str, str] = {}
    all_batches = sorted(plan.get("batches", {}))
    for batch_id in all_batches:
        path = batch_zip_path(exp, batch_id)
        if not path.is_file():
            continue
        try:
            static_text, decomp_text, receipt = read_zip_package(path)
            validate_batch_package(
                batch_id=batch_id,
                static_text=static_text,
                decomp_text=decomp_text,
                receipt=receipt,
                rvas=plan["batches"][batch_id],
                inventory=inventory,
                callsites_by_caller=callsites_by_caller,
                plan=plan,
                summary=summary,
            )
            valid.append(batch_id)
        except ReError as exc:
            invalid[batch_id] = str(exc)
    missing = [bid for bid in all_batches if bid not in valid and bid not in invalid]
    return valid, missing, invalid


def summarize_package_coverage(
    exp: Path,
    valid: list[str],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    static_success = 0
    static_failed = 0
    decomp_counts: dict[str, int] = defaultdict(int)
    verified_ins = 0
    verified_bytes = 0
    static_success_rvas: set[str] = set()
    for batch_id in valid:
        static_text, decomp_text, _ = read_zip_package(batch_zip_path(exp, batch_id))
        for row in load_jsonl_text(static_text):
            status = row.get("status")
            if status == "success":
                static_success += 1
                static_success_rvas.add(row["entry_rva"])
                try:
                    count, nbytes, _ = reconstruct_instruction_evidence(row.get("instructions") or [])
                    verified_ins += count
                    verified_bytes += nbytes
                except ReError:
                    pass
            elif status == "failed":
                static_failed += 1
        for row in load_jsonl_text(decomp_text):
            decomp_counts[row.get("status", "unknown")] += 1
    gfx_helpers = [rva for rva, row in inventory.items() if is_gfx_helper(row)]
    gfx_missing = [rva for rva in gfx_helpers if rva not in static_success_rvas]
    priority_missing = [
        label
        for rva, label in PRIORITY_ENTRIES
        if format_rva(parse_rva(rva)) not in static_success_rvas
    ]
    return {
        "static_success_functions": static_success,
        "static_failed_functions": static_failed,
        "decompile_status_counts": dict(decomp_counts),
        "verified_decoded_instructions": verified_ins,
        "verified_decoded_bytes": verified_bytes,
        "priority_entries_static_success": len(PRIORITY_ENTRIES) - len(priority_missing),
        "priority_entries_missing_static_success": priority_missing,
        "gfx_helpers_static_success": len(gfx_helpers) - len(gfx_missing),
        "gfx_helpers_expected": len(gfx_helpers),
        "gfx_helpers_missing_static_success": gfx_missing[:10],
    }


def cmd_report(args: argparse.Namespace) -> int:
    scope_dir = Path(args.scope_dir).expanduser()
    exp, summary, plan, _, _, revision = load_frozen_context(scope_dir, args.baseline_revision)
    inventory = load_inventory_index(exp)
    callsites_by_caller = load_callsites_by_caller(exp)
    all_batches = sorted(plan.get("batches", {}))
    valid, missing, invalid = classify_batches(exp, plan, summary, inventory, callsites_by_caller)
    complete = len(valid) == len(all_batches) and len(all_batches) == 45
    observed_functions = sum(len(plan["batches"][bid]) for bid in valid)
    report: dict[str, Any] = {
        "status": "complete" if complete else "partial",
        "baseline_revision": revision,
        "planned_batches": len(all_batches),
        "valid_batches": len(valid),
        "missing_batch_ids": missing,
        "invalid_batch_ids": sorted(invalid),
        "invalid_batch_errors": invalid,
        "planned_functions": plan.get("selected_function_count"),
        "observed_functions": observed_functions,
    }
    report.update(summarize_package_coverage(exp, valid, inventory))
    if complete:
        report.update(
            {
                "expected_decoded_instructions": EXPECTED_INSTRUCTIONS,
                "expected_decoded_bytes": EXPECTED_DECODED_BYTES,
                "instructions_match_expected": report["verified_decoded_instructions"]
                == EXPECTED_INSTRUCTIONS,
                "bytes_match_expected": report["verified_decoded_bytes"] == EXPECTED_DECODED_BYTES,
            }
        )
    logs = snapshot_root(exp) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "coverage.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Renderer snapshot extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--scope-dir", default=str(DEFAULT_SCOPE_DIR))
        p.add_argument("--baseline-revision", default=None)
        p.set_defaults(func=cmd_run if name == "run" else cmd_report)
    run = sub.choices["run"]
    run.add_argument("--batch-id", default=None)
    run.add_argument("--repair", action="store_true")
    run.add_argument("--decomp-timeout", type=int, default=DEFAULT_DECOMP_TIMEOUT)
    run.add_argument("--process-timeout", type=int, default=DEFAULT_PROCESS_TIMEOUT)
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
