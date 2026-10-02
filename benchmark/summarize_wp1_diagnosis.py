#!/usr/bin/env python3
"""Summarize WP1 diagnostic matrix evidence and optionally register the manifest entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "analysis/evidence"
MANIFEST = ROOT / "analysis/profiler-overhead-diagnosis-manifest.json"
TRAINING_RECIPES = ("mesh", "borders", "text_ui")
EXPECTED_PURPOSE = "profiler_overhead_diagnosis_v1"
EXPECTED_STATUS = "diagnostic_complete"
EXPECTED_SCOPE = "training_only"


def _load_archive(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _payload(archive: dict) -> dict:
    """On-disk immutable archives store capture under `preflight`; CLI stdout is flat."""
    if archive.get("purpose") == EXPECTED_PURPOSE:
        return archive
    preflight = archive.get("preflight")
    if isinstance(preflight, dict) and preflight.get("purpose") == EXPECTED_PURPOSE:
        return preflight
    return archive


def _recipe_entries(archive: dict) -> list[dict]:
    payload = _payload(archive)
    rw = payload.get("representative_workloads")
    if isinstance(rw, dict) and rw.get("recipes"):
        return rw["recipes"]
    preflight = archive.get("preflight") or {}
    rw = preflight.get("representative_workloads") or {}
    return rw.get("recipes") or []


def _format_gate_row(recipe: str, gate_key: str, gate: dict) -> str:
    mf = gate.get("median_fraction")
    ci = gate.get("confidence_interval_95")
    status = gate.get("status", "")
    return f"| {recipe} | `{gate_key}` | {mf:+.1%} | {ci} | {status} |"


def _format_comparison_row(recipe: str, stage: str, comp: dict) -> str:
    el = comp["elapsed_ns"]
    cpu = comp["cpu_ns"]
    return (
        f"| {recipe} | `{stage}` | {el['median_fraction']:+.1%} | {el.get('confidence_interval_95')} "
        f"| {cpu['median_fraction']:+.1%} | {cpu.get('confidence_interval_95')} |"
    )


def summarize_markdown(archive_path: Path) -> str:
    archive = _load_archive(archive_path)
    payload = _payload(archive)
    lines = [
        f"# WP1 matrix summary for `{archive_path.relative_to(ROOT)}`",
        "",
        f"- `purpose`: {payload.get('purpose')}",
        f"- `status`: {payload.get('status')}",
        f"- `validation_scope`: {payload.get('validation_scope')}",
        "",
        "## Causal gates (elapsed_ns)",
        "",
        "| Recipe | Gate | median_fraction | CI95 | status |",
        "|--------|------|-----------------|------|--------|",
    ]
    for entry in _recipe_entries(archive):
        name = entry.get("recipe", {}).get("name")
        if name not in TRAINING_RECIPES:
            continue
        for key, gate in sorted(entry.get("gates", {}).items()):
            if key.endswith("_elapsed_ns"):
                lines.append(_format_gate_row(name, key, gate))
    lines.extend(
        [
            "",
            "## Diagnostic matrix vs counters (elapsed_ns / cpu_ns)",
            "",
            "| Recipe | Stage | elapsed median_fraction | elapsed CI95 | cpu median_fraction | cpu CI95 |",
            "|--------|-------|-------------------------|--------------|---------------------|----------|",
        ],
    )
    has_vs_counters = False
    for entry in _recipe_entries(archive):
        name = entry.get("recipe", {}).get("name")
        if name not in TRAINING_RECIPES:
            continue
        dm = entry.get("diagnostic_matrix") or {}
        vs_counters = dm.get("vs_counters") or {}
        for stage in sorted(vs_counters):
            has_vs_counters = True
            lines.append(_format_comparison_row(name, stage, vs_counters[stage]))
    if not has_vs_counters:
        lines.append("| *(none)* | | | | | |")
    lines.extend(
        [
            "",
            "## Diagnostic matrix vs diag_A (elapsed_ns / cpu_ns)",
            "",
            "| Recipe | Stage | elapsed median_fraction | elapsed CI95 | cpu median_fraction | cpu CI95 |",
            "|--------|-------|-------------------------|--------------|---------------------|----------|",
        ],
    )
    for entry in _recipe_entries(archive):
        name = entry.get("recipe", {}).get("name")
        if name not in TRAINING_RECIPES:
            continue
        dm = entry.get("diagnostic_matrix") or {}
        vs_a = dm.get("vs_diag_A") or dm.get("comparisons") or {}
        for stage in sorted(vs_a):
            lines.append(_format_comparison_row(name, stage, vs_a[stage]))
    return "\n".join(lines) + "\n"


def validate_wp1_archive(archive: dict, archive_path: Path) -> dict:
    payload = _payload(archive)
    purpose = payload.get("purpose")
    if purpose != EXPECTED_PURPOSE:
        raise ValueError(f"expected purpose {EXPECTED_PURPOSE}, got {purpose!r}")
    if payload.get("status") != EXPECTED_STATUS:
        raise ValueError(f"expected status {EXPECTED_STATUS}, got {payload.get('status')!r}")
    scope = payload.get("validation_scope")
    if scope != EXPECTED_SCOPE:
        raise ValueError(f"expected validation_scope {EXPECTED_SCOPE}, got {scope!r}")

    rel_path = archive_path.relative_to(ROOT).as_posix()
    body = archive_path.read_bytes()
    archive_sha256 = hashlib.sha256(body).hexdigest()

    immutable = payload.get("immutable_evidence") or archive.get("immutable_evidence") or {}
    evidence_id = immutable.get("evidence_id") or archive.get("evidence_id")
    if not evidence_id:
        raise ValueError("archive missing evidence_id (top-level metadata)")
    if immutable.get("archive_sha256") and immutable["archive_sha256"] != archive_sha256:
        raise ValueError("immutable_evidence.archive_sha256 does not match file on disk")
    if immutable.get("archive_path"):
        expected = (ROOT / immutable["archive_path"]).resolve()
        if expected != archive_path.resolve():
            raise ValueError(
                f"immutable_evidence.archive_path {immutable['archive_path']} does not match file {archive_path}",
            )

    git_commit = (
        archive.get("git_commit")
        or (archive.get("identity_end") or {}).get("git_commit")
        or (archive.get("identity_start") or {}).get("git_commit")
    )
    captured_at = archive.get("recorded_at_utc") or evidence_id
    return {
        "evidence_id": evidence_id,
        "archive_path": rel_path,
        "archive_sha256": archive_sha256,
        "git_commit": git_commit,
        "captured_at_utc": captured_at,
    }


def find_wp1_archives() -> list[Path]:
    if not EVIDENCE_DIR.is_dir():
        return []
    found: list[Path] = []
    for path in sorted(EVIDENCE_DIR.glob("frame-model-offline-*.json")):
        try:
            archive = _load_archive(path)
        except (OSError, json.JSONDecodeError):
            continue
        if _payload(archive).get("purpose") == EXPECTED_PURPOSE:
            found.append(path)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def register_manifest(archive_path: Path) -> dict:
    archive = _load_archive(archive_path)
    entry = validate_wp1_archive(archive, archive_path)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    archives = manifest.setdefault("diagnostic_archives", [])
    if any(item.get("evidence_id") == entry["evidence_id"] for item in archives):
        return entry
    archives.append(entry)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?", help="WP1 evidence JSON under analysis/evidence/")
    parser.add_argument(
        "--find",
        action="store_true",
        help="list WP1 archives under analysis/evidence/ (newest first); no path required",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="append validated entry to profiler-overhead-diagnosis-manifest.json",
    )
    parser.add_argument(
        "--register-latest",
        action="store_true",
        help="register the newest WP1 archive from --find",
    )
    parser.add_argument("--markdown", action="store_true", help="print recipe tables to stdout")
    args = parser.parse_args()

    if args.find or args.register_latest:
        matches = find_wp1_archives()
        if not matches:
            print("No profiler_overhead_diagnosis_v1 archives under analysis/evidence/", file=sys.stderr)
            return 1
        if args.find:
            for path in matches:
                entry = validate_wp1_archive(_load_archive(path), path)
                print(f"{path.relative_to(ROOT)}\t{entry['evidence_id']}\t{entry['archive_sha256'][:16]}…")
        if args.register_latest:
            entry = register_manifest(matches[0])
            print(json.dumps(entry, indent=2))
        return 0

    if not args.archive:
        parser.error("archive path is required unless --find or --register-latest is used")
    path = args.archive.expanduser().resolve()
    if not path.is_file():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1
    if args.register:
        entry = register_manifest(path)
        print(json.dumps(entry, indent=2))
    if args.markdown or not args.register:
        print(summarize_markdown(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
