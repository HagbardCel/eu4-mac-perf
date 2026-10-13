#!/usr/bin/env python3
"""Ghidra headless orchestration, export validation, indexing, and reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from reverse_engineering import index_db
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.export_identity import (
    compute_qualification,
    inventory_complete,
    inventory_staging_dir,
    invalidate_qualification,
    publish_batch_staging,
    publish_inventory_from_staging,
    read_ghidra_version,
    smoke_qualified_from_payload,
    validate_export_identity,
)
from reverse_engineering.manifest import (
    build_batch_manifest,
    decompilation_success_stats,
    export_completeness,
    inventory_bundle_digest,
    inventory_digest,
    load_batch_manifest,
    pilot_sample_rvas,
    write_batch_manifest,
)
from reverse_engineering.reconcile_gfx import reconcile_draw_sites
from reverse_engineering.paths import (
    GHIDRA_SCRIPTS,
    RE_DIR,
    ReError,
    binary_sha256,
    default_baseline_revision,
    executable_path,
    export_root,
    ghidra_project_dir,
    ghidra_project_meta_path,
    load_target,
    local_binary_root,
    resolve_export_dir,
)

ROOT = RE_DIR.parent
DRAW_CALLERS = ROOT / "analysis" / "draw-callers.json"
FRAME_ENGINE = ROOT / "analysis" / "frame-model-engine.json"
RENDERBUCKETS_HOOK = ROOT / "analysis" / "renderbuckets-entry-hook-site.json"

DEFAULT_BATCH_SIZE = 500
DEFAULT_MIN_SUCCESS_BYTES = 0.85
SMOKE_ANCHORS = (
    "SShaderOpenGL6SetAll",
    "CPdxMeshObject13RenderBuckets",
    "CEU3GraphicalMap6Render",
)

MANDATORY_ANCHOR_SYMBOLS = (
    "__ZN13SShaderOpenGL6SetAllEv",
    "_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb",
    "__ZN16CEU3GraphicalMap6RenderEP21GfxDeferredContextGFXRK10CEU3Camerafb",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def script_hash(name: str) -> str:
    path = GHIDRA_SCRIPTS / name
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ghidra_install() -> Path:
    env = os.environ.get("GHIDRA_INSTALL")
    if not env:
        raise ReError("GHIDRA_INSTALL is not set (path to Ghidra root)")
    root = Path(env).expanduser()
    headless = root / "support" / "analyzeHeadless"
    if not headless.is_file():
        raise ReError(f"analyzeHeadless not found under {root}")
    return root


def build_analyze_headless_argv(project_dir: Path, project_name: str, *extra: str) -> list[str]:
    ghidra = ghidra_install()
    headless = ghidra / "support" / "analyzeHeadless"
    return [
        str(headless),
        str(project_dir),
        project_name,
        "-scriptPath",
        str(GHIDRA_SCRIPTS),
        *extra,
    ]


def analyze_headless(project_dir: Path, project_name: str, *extra: str) -> None:
    args = build_analyze_headless_argv(project_dir, project_name, *extra)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout)[-4000:]
        raise ReError(f"analyzeHeadless failed:\n{tail}")


def verify_binary() -> Path:
    target = load_target()
    exe = executable_path()
    if not exe.is_file():
        raise ReError(f"EU IV executable not found: {exe}")
    digest = sha256_file(exe)
    if digest != target["sha256"]:
        raise ReError(f"executable SHA-256 mismatch: got {digest}")
    return exe


def load_ghidra_meta(binary_root: Path) -> dict[str, Any]:
    path = ghidra_project_meta_path(binary_root)
    if not path.is_file():
        raise ReError(f"missing {path}; run corpus import first")
    return json.loads(path.read_text())


def load_provenance(exp: Path) -> dict[str, Any]:
    path = exp / "provenance.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def save_provenance(exp: Path, data: dict[str, Any]) -> None:
    path = exp / "provenance.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def project_name_for_sha(digest: str) -> str:
    return f"EU4_{digest[:8]}"


def mach_o_preflight(exe: Path) -> dict[str, Any]:
    file_out = subprocess.run(["file", str(exe)], capture_output=True, text=True, check=True).stdout.strip()
    otool = subprocess.run(["otool", "-l", str(exe)], capture_output=True, text=True, check=True).stdout
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in otool.splitlines():
        line = line.strip()
        if line.startswith("sectname"):
            current = {"sectname": line.split()[-1]}
        elif line.startswith("segname") and current:
            current["segname"] = line.split()[-1]
        elif line.startswith("size") and current:
            current["size"] = int(line.split()[-1], 16)
        elif line.startswith("flags") and current:
            current["flags"] = line.split()[-1]
            sections.append(current)
            current = {}
    return {"file": file_out, "sections": sections}


def cmd_preflight(_: argparse.Namespace) -> int:
    exe = verify_binary()
    info = mach_o_preflight(exe)
    print(json.dumps({"executable": str(exe), "sha256": sha256_file(exe), "mach_o": info}, indent=2))
    try:
        ghidra = ghidra_install()
        print(f"ghidra_install: {ghidra}")
    except ReError as exc:
        print(f"ghidra: {exc}", file=sys.stderr)
        return 1
    java = subprocess.run(["java", "-version"], capture_output=True, text=True)
    print((java.stderr or java.stdout).strip())
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    exe = verify_binary()
    digest = sha256_file(exe)
    binary_root = local_binary_root(digest)
    project_dir = ghidra_project_dir(binary_root)
    if project_dir.exists() and any(project_dir.iterdir()):
        raise ReError(f"ghidra project already exists at {project_dir}; remove manually to re-import")
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = project_name_for_sha(digest)
    baseline = args.baseline_revision or default_baseline_revision()
    exp = export_root(binary_root, baseline)
    exp.mkdir(parents=True, exist_ok=True)
    meta_tmp = exp / "logs" / "program_metadata.json"
    meta_tmp.parent.mkdir(parents=True, exist_ok=True)
    analyze_headless(
        project_dir,
        project_name,
        "-import",
        str(exe),
        "-processor",
        "x86:LE:64:default",
        "-postScript",
        "RecordProjectMetadata.java",
        str(meta_tmp),
    )
    program_meta = json.loads(meta_tmp.read_text())
    ghidra_meta = {
        "project_dir": str(project_dir),
        "project_name": project_name,
        "program_name": program_meta["program_name"],
        "program_path_in_project": program_meta.get("program_path_in_project"),
        "image_base": program_meta.get("image_base"),
    }
    ghidra_project_meta_path(binary_root).write_text(json.dumps(ghidra_meta, indent=2) + "\n")
    ghidra_root = ghidra_install()
    provenance = {
        "baseline_revision": baseline,
        "binary_sha256": digest,
        "ghidra_version": read_ghidra_version(ghidra_root),
        "ghidra_meta": ghidra_meta,
        "import_script_hashes": {
            "RecordProjectMetadata.java": script_hash("RecordProjectMetadata.java"),
            "ImportAndInventory.java": script_hash("ImportAndInventory.java"),
            "ExportDecompilation.java": script_hash("ExportDecompilation.java"),
        },
    }
    save_provenance(exp, provenance)
    print(f"import complete: baseline_revision={baseline}")
    if getattr(args, "run_smoke", False):
        return cmd_smoke(argparse.Namespace(baseline_revision=baseline))
    return 0


def mandatory_anchors_check(exp: Path) -> int:
    functions = {
        json.loads(line)["name_mangled"]: json.loads(line)["entry_rva"]
        for line in (exp / "inventory" / "functions.jsonl").read_text().splitlines()
        if line.strip()
    }
    decomp_dir = exp / "decompiled"
    status_by_rva: dict[str, str | None] = {}
    for path in decomp_dir.glob("batch-*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                status_by_rva[row["entry_rva"]] = row.get("status")
    for sym in MANDATORY_ANCHOR_SYMBOLS:
        rva = functions.get(sym)
        if not rva or status_by_rva.get(rva) != "success":
            return 1
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    verify_binary()
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    meta = load_ghidra_meta(binary_root)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    if not provenance:
        raise ReError("missing provenance; run import first")
    smoke_report = exp / "logs" / "smoke-decompile.json"
    smoke_report.parent.mkdir(parents=True, exist_ok=True)
    qualified = False
    try:
        analyze_headless(
            Path(meta["project_dir"]),
            meta["project_name"],
            "-process",
            meta["program_name"],
            "-noanalysis",
            "-readOnly",
            "-postScript",
            "SmokeDecompile.java",
            str(smoke_report),
            *SMOKE_ANCHORS,
        )
        smoke_payload = json.loads(smoke_report.read_text()) if smoke_report.is_file() else {}
        qualified = smoke_qualified_from_payload(smoke_payload, SMOKE_ANCHORS)
    except ReError:
        smoke_payload = json.loads(smoke_report.read_text()) if smoke_report.is_file() else {}
        qualified = False
    provenance["smoke_decompile"] = smoke_payload
    provenance["smoke_qualified"] = qualified
    save_provenance(exp, provenance)
    if not qualified:
        print("smoke decompile failed (project preserved; fix anchors and rerun corpus smoke)", file=sys.stderr)
        return 1
    print("smoke decompile passed")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    verify_binary()
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    meta = load_ghidra_meta(binary_root)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    if not provenance:
        raise ReError("missing provenance; run import first")
    inv_dir = exp / "inventory"
    mode = "inventory_regen" if inventory_complete(inv_dir) else "inventory"
    validate_export_identity(exp, provenance, mode)
    staging = inventory_staging_dir(exp)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    analyze_headless(
        Path(meta["project_dir"]),
        meta["project_name"],
        "-process",
        meta["program_name"],
        "-noanalysis",
        "-readOnly",
        "-postScript",
        "ImportAndInventory.java",
        str(staging),
    )
    if not inventory_complete(staging):
        raise ReError("inventory staging incomplete after Ghidra export")
    bundle = inventory_bundle_digest(staging)
    if provenance.get("inventory_bundle_digest") and provenance["inventory_bundle_digest"] != bundle:
        raise ReError("inventory bundle digest changed; create a new baseline_revision")
    publish_inventory_from_staging(staging, inv_dir)
    functions_path = inv_dir / "functions.jsonl"
    digest_value = inventory_digest(functions_path)
    provenance["inventory_digest"] = digest_value
    provenance["inventory_bundle_digest"] = bundle
    provenance["inventory_script_hash"] = script_hash("ImportAndInventory.java")
    provenance["coverage"] = json.loads((inv_dir / "coverage.json").read_text())
    save_provenance(exp, provenance)
    print(f"inventory exported to {inv_dir} (bundle={bundle[:12]}…)")
    return 0


def cmd_manifest(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    validate_export_identity(exp, provenance, "manifest")
    inv = exp / "inventory"
    functions_path = inv / "functions.jsonl"
    if not functions_path.is_file():
        raise ReError("run inventory before manifest")
    digest_value = inventory_digest(functions_path)
    bundle_value = inventory_bundle_digest(inv)
    if provenance.get("inventory_bundle_digest") and provenance["inventory_bundle_digest"] != bundle_value:
        raise ReError("inventory bundle digest mismatch")
    manifest = build_batch_manifest(
        functions_jsonl=functions_path,
        baseline_revision=provenance["baseline_revision"],
        batch_size=args.batch_size,
        exporter_script_hash=script_hash("ExportDecompilation.java"),
        inventory_digest_value=digest_value,
        inventory_bundle_digest_value=bundle_value,
    )
    write_batch_manifest(exp / "decompiled" / "batch_manifest.json", manifest)
    print(f"batch manifest: {manifest['eligible_function_count']} functions")
    return 0


def _write_rva_list(path: Path, rvas: list[str]) -> None:
    lines = [parse_rva(r) for r in rvas]
    path.write_text("\n".join(format_rva(r) for r in lines) + "\n")


def cmd_decompile(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    meta = load_ghidra_meta(binary_root)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    exporter_hash = script_hash("ExportDecompilation.java")
    manifest_path = exp / "decompiled" / "batch_manifest.json"
    if not manifest_path.is_file():
        cmd_manifest(argparse.Namespace(baseline_revision=exp.name, batch_size=args.batch_size))
    manifest = load_batch_manifest(manifest_path)
    validate_export_identity(exp, provenance, "decompile", exporter_script_hash=exporter_hash)
    functions_path = exp / "inventory" / "functions.jsonl"
    batches = manifest["batches"]
    pilot_dir: Path | None = None
    if args.pilot:
        sample = pilot_sample_rvas(functions_path, args.pilot)
        pilot_dir = exp / "logs" / "pilot"
        pilot_dir.mkdir(parents=True, exist_ok=True)
        batches = {"pilot-sample": [format_rva(r) for r in sample]}
    timeout = args.timeout_seconds
    decomp_dir = pilot_dir or (exp / "decompiled")
    decomp_dir.mkdir(parents=True, exist_ok=True)
    for batch_id, rvas in batches.items():
        if args.pilot:
            out_path = decomp_dir / "batch-pilot.jsonl"
            staging = decomp_dir / "batch-pilot.jsonl.staging"
        else:
            out_path = decomp_dir / f"{batch_id}.jsonl"
            staging = decomp_dir / f"{batch_id}.jsonl.staging"
        if out_path.is_file() and not args.force:
            errors = export_completeness({"batches": {batch_id: rvas}}, decomp_dir)[1]
            if not errors:
                continue
        if staging.is_file():
            staging.unlink()
        with tempfile.TemporaryDirectory() as tmp:
            rva_file = Path(tmp) / "rvas.txt"
            _write_rva_list(rva_file, rvas)
            analyze_headless(
                Path(meta["project_dir"]),
                meta["project_name"],
                "-process",
                meta["program_name"],
                "-noanalysis",
                "-readOnly",
                "-postScript",
                "ExportDecompilation.java",
                str(rva_file),
                str(staging),
                str(timeout),
            )
        publish_batch_staging(staging, out_path, rvas)
        invalidate_qualification(provenance, "batch_replaced")
        save_provenance(exp, provenance)
    if args.pilot:
        print(f"pilot decompile written under {pilot_dir}")
        return 0
    c_export, errors = export_completeness(manifest, decomp_dir)
    stats = decompilation_success_stats(manifest, decomp_dir, functions_path)
    qual = compute_qualification(
        exp,
        provenance,
        args.min_success_bytes,
        lambda: mandatory_anchors_check(exp),
        smoke_needles=SMOKE_ANCHORS,
    )
    qual["c_export"] = c_export
    qual["export_errors"] = errors
    provenance["qualification"] = qual
    save_provenance(exp, provenance)
    report = {
        "c_export": c_export,
        "export_errors": errors,
        "decompilation_stats": stats,
        "min_success_bytes_threshold": args.min_success_bytes,
        "qualification": qual,
    }
    (exp / "logs" / "decompile-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if c_export < 1.0:
        return 1
    if not qual["passed"]:
        print("decompile finished but export is not qualified", file=sys.stderr)
        return 1
    return 0


def cmd_qualify(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    validate_export_identity(exp, provenance, "qualify")
    qual = compute_qualification(
        exp,
        provenance,
        args.min_success_bytes,
        lambda: mandatory_anchors_check(exp),
        smoke_needles=SMOKE_ANCHORS,
    )
    provenance["qualification"] = qual
    save_provenance(exp, provenance)
    (exp / "logs" / "qualification.json").write_text(json.dumps(qual, indent=2) + "\n")
    print(json.dumps(qual, indent=2))
    return 0 if qual["passed"] else 1


def cmd_index(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    validate_export_identity(exp, provenance, "index")
    manifest_path = exp / "decompiled" / "batch_manifest.json"
    manifest = load_batch_manifest(manifest_path)
    decomp_dir = exp / "decompiled"
    if not args.allow_incomplete:
        c_export, errors = export_completeness(manifest, decomp_dir)
        if c_export < 1.0 and not args.partial:
            raise ReError(f"incomplete export (c_export={c_export}); use --partial or --allow-incomplete")
        if errors and not args.partial:
            raise ReError("export completeness errors: " + "; ".join(errors[:5]))
    db_path = exp / "index.sqlite"
    if args.partial or args.allow_incomplete:
        db_path = exp / ".local" / "index-partial.sqlite"
    prov_strings = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in provenance.items()}
    index_db.rebuild_index(
        db_path,
        exp / "inventory",
        decomp_dir,
        prov_strings,
        manifest=manifest,
    )
    print(f"index rebuilt: {db_path}")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    provenance = load_provenance(exp)
    validate_export_identity(exp, provenance, "reconcile")
    draw = json.loads(DRAW_CALLERS.read_text())
    report = reconcile_draw_sites(draw, exp / "inventory")
    out = exp / "logs" / "gfx-reconcile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


def cmd_check_anchors(args: argparse.Namespace) -> int:
    digest = binary_sha256()
    binary_root = local_binary_root(digest)
    exp = resolve_export_dir(binary_root, args.baseline_revision)
    functions = {
        json.loads(line)["name_mangled"]: json.loads(line)["entry_rva"]
        for line in (exp / "inventory" / "functions.jsonl").read_text().splitlines()
        if line.strip()
    }
    decomp_dir = exp / "decompiled"
    status_by_rva = {}
    for path in decomp_dir.glob("batch-*.jsonl"):
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                status_by_rva[row["entry_rva"]] = row.get("status")
    missing = []
    for sym in MANDATORY_ANCHOR_SYMBOLS:
        rva = functions.get(sym)
        if not rva:
            missing.append({"symbol": sym, "reason": "not in inventory"})
            continue
        if status_by_rva.get(rva) != "success":
            missing.append({"symbol": sym, "entry_rva": rva, "status": status_by_rva.get(rva)})
    print(json.dumps({"mandatory_anchors_ok": not missing, "issues": missing}, indent=2))
    return 1 if missing else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EU IV Ghidra corpus orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight", help="verify binary and toolchain").set_defaults(func=cmd_preflight)
    p_import = sub.add_parser("import", help="D1 Ghidra import (analyze only)")
    p_import.add_argument("--baseline-revision", default=None)
    p_import.add_argument("--run-smoke", action="store_true", dest="run_smoke")
    p_import.set_defaults(func=cmd_import)
    p_smoke = sub.add_parser("smoke", help="retryable smoke decompile validation")
    p_smoke.add_argument("--baseline-revision", default=None)
    p_smoke.set_defaults(func=cmd_smoke)
    for name in ("inventory", "manifest", "reconcile", "check-anchors"):
        p = sub.add_parser(name)
        p.add_argument("--baseline-revision", default=None)
        p.set_defaults(func=globals()[f"cmd_{name.replace('-', '_')}"])
    p_index = sub.add_parser("index")
    p_index.add_argument("--baseline-revision", default=None)
    p_index.add_argument("--partial", action="store_true")
    p_index.add_argument("--allow-incomplete", action="store_true")
    p_index.set_defaults(func=cmd_index)
    p_qualify = sub.add_parser("qualify", help="persist qualification.passed in provenance")
    p_qualify.add_argument("--baseline-revision", default=None)
    p_qualify.add_argument("--min-success-bytes", type=float, default=DEFAULT_MIN_SUCCESS_BYTES)
    p_qualify.set_defaults(func=cmd_qualify)
    p_manifest = sub.choices["manifest"]
    p_manifest.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_decomp = sub.add_parser("decompile")
    p_decomp.add_argument("--baseline-revision", default=None)
    p_decomp.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_decomp.add_argument("--timeout-seconds", type=int, default=120)
    p_decomp.add_argument("--min-success-bytes", type=float, default=DEFAULT_MIN_SUCCESS_BYTES)
    p_decomp.add_argument("--pilot", type=int, default=0)
    p_decomp.add_argument("--force", action="store_true")
    p_decomp.set_defaults(func=cmd_decompile)
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
