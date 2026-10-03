#!/usr/bin/env python3
"""Summarize WP1 diagnostic matrix evidence and optionally register the manifest entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "analysis/evidence"
MANIFEST = ROOT / "analysis/profiler-overhead-diagnosis-manifest.json"
TRAINING_RECIPES = ("mesh", "borders", "text_ui")
EXPECTED_PURPOSE = "profiler_overhead_diagnosis_v1"
EXPECTED_STATUS = "diagnostic_complete"
EXPECTED_SCOPE = "training_only"
EXPECTED_DIAGNOSTIC_POLICY = "diag_matrix_diag_counters_baseline_v3"
INVALID_DIAGNOSTIC_POLICY_V2 = "diag_matrix_counters_baseline_v2"


@dataclass(frozen=True)
class Wp1Discovery:
    role: str  # VALID | HISTORICAL | REQUALIFICATION
    path: Path
    evidence_id: str
    captured_at_utc: str
    detail: str
    entry: dict | None = None


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


def _diagnostic_policy_version(archive: dict) -> str | None:
    policies = archive.get("policy_versions") or {}
    version = policies.get("diagnostic_matrix")
    if version:
        return version
    for entry in _recipe_entries(archive):
        dm = entry.get("diagnostic_matrix") or {}
        if dm.get("policy_version"):
            return dm["policy_version"]
    return None


def _evidence_id_from_archive(archive: dict) -> str | None:
    payload = _payload(archive)
    immutable = payload.get("immutable_evidence") or archive.get("immutable_evidence") or {}
    return immutable.get("evidence_id") or archive.get("evidence_id")


def _captured_at_utc(archive: dict, evidence_id: str) -> str:
    return archive.get("recorded_at_utc") or evidence_id


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

    if archive.get("git_provenance_verified") is not True:
        raise ValueError("git_provenance_verified must be true")
    if archive.get("git_tree_clean") is not True:
        raise ValueError("git_tree_clean must be true")
    identity_start = archive.get("identity_start") or {}
    identity_end = archive.get("identity_end") or {}
    if identity_start.get("git_tree_clean") is not True or identity_end.get("git_tree_clean") is not True:
        raise ValueError("identity_start/end git_tree_clean must be true")
    start_commit = identity_start.get("git_commit")
    end_commit = identity_end.get("git_commit")
    if not start_commit or start_commit != end_commit:
        raise ValueError("identity_start and identity_end git_commit must match")
    artifact_start = archive.get("executed_artifact_start") or {}
    artifact_end = archive.get("executed_artifact_end") or {}
    if artifact_start != artifact_end:
        raise ValueError("executed_artifact_start and executed_artifact_end must match")

    policy = _diagnostic_policy_version(archive)
    if policy == INVALID_DIAGNOSTIC_POLICY_V2:
        raise ValueError(
            "diagnostic_matrix policy v2 has invalid vs_counters (Tier-1 counters log path collision); "
            "remap to diagnostic_archives_historical or recapture with v3",
        )
    if policy != EXPECTED_DIAGNOSTIC_POLICY:
        raise ValueError(f"expected diagnostic_matrix policy {EXPECTED_DIAGNOSTIC_POLICY}, got {policy!r}")

    recipe_names = {
        entry.get("recipe", {}).get("name")
        for entry in _recipe_entries(archive)
        if entry.get("recipe", {}).get("role") != "held_out"
    }
    missing = set(TRAINING_RECIPES) - recipe_names
    if missing:
        raise ValueError(f"missing training recipes: {sorted(missing)}")

    rel_path = archive_path.relative_to(ROOT).as_posix()
    body = archive_path.read_bytes()
    archive_sha256_committed = hashlib.sha256(body).hexdigest()

    immutable = payload.get("immutable_evidence") or archive.get("immutable_evidence") or {}
    evidence_id = immutable.get("evidence_id") or archive.get("evidence_id")
    if not evidence_id:
        raise ValueError("archive missing evidence_id (top-level metadata)")
    if immutable.get("archive_sha256") and immutable["archive_sha256"] != archive_sha256_committed:
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
    captured_at = _captured_at_utc(archive, evidence_id)
    return {
        "evidence_id": evidence_id,
        "archive_path": rel_path,
        "archive_sha256_committed": archive_sha256_committed,
        "git_commit": git_commit,
        "captured_at_utc": captured_at,
    }


def _load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _manifest_evidence_roles(manifest: dict) -> dict[str, str]:
    """Map evidence_id to discovery role from explicit manifest membership."""
    roles: dict[str, str] = {}
    for item in manifest.get("requalification_archives") or []:
        evidence_id = item.get("evidence_id")
        if evidence_id:
            roles[evidence_id] = "REQUALIFICATION"
    for item in manifest.get("diagnostic_archives_historical") or []:
        evidence_id = item.get("evidence_id")
        if evidence_id:
            roles[evidence_id] = "HISTORICAL"
    for item in manifest.get("diagnostic_archives") or []:
        evidence_id = item.get("evidence_id")
        if evidence_id and evidence_id not in roles:
            roles[evidence_id] = "VALID"
    return roles


def discover_wp1_archives() -> list[Wp1Discovery]:
    if not EVIDENCE_DIR.is_dir():
        return []
    manifest_roles = _manifest_evidence_roles(_load_manifest())
    discoveries: list[Wp1Discovery] = []
    for path in sorted(EVIDENCE_DIR.glob("frame-model-offline-*.json")):
        try:
            archive = _load_archive(path)
        except (OSError, json.JSONDecodeError):
            continue
        if _payload(archive).get("purpose") != EXPECTED_PURPOSE:
            continue
        evidence_id = _evidence_id_from_archive(archive) or path.name
        captured_at = _captured_at_utc(archive, evidence_id)
        manifest_role = manifest_roles.get(evidence_id)
        try:
            entry = validate_wp1_archive(archive, path)
            if manifest_role == "REQUALIFICATION":
                discoveries.append(
                    Wp1Discovery(
                        role="REQUALIFICATION",
                        path=path,
                        evidence_id=entry["evidence_id"],
                        captured_at_utc=entry["captured_at_utc"],
                        detail="manifest requalification_archives",
                        entry=entry,
                    ),
                )
                continue
            if manifest_role == "HISTORICAL":
                discoveries.append(
                    Wp1Discovery(
                        role="HISTORICAL",
                        path=path,
                        evidence_id=entry["evidence_id"],
                        captured_at_utc=entry["captured_at_utc"],
                        detail="manifest diagnostic_archives_historical",
                        entry=entry,
                    ),
                )
                continue
            discoveries.append(
                Wp1Discovery(
                    role=manifest_role or "VALID",
                    path=path,
                    evidence_id=entry["evidence_id"],
                    captured_at_utc=entry["captured_at_utc"],
                    detail=entry["archive_sha256_committed"][:16] + "…",
                    entry=entry,
                ),
            )
        except ValueError as error:
            policy = _diagnostic_policy_version(archive)
            if policy == INVALID_DIAGNOSTIC_POLICY_V2:
                discoveries.append(
                    Wp1Discovery(
                        role="HISTORICAL",
                        path=path,
                        evidence_id=evidence_id,
                        captured_at_utc=captured_at,
                        detail=f"invalid vs_counters ({policy}): {error}",
                    ),
                )
    discoveries.sort(key=lambda item: item.captured_at_utc, reverse=True)
    return discoveries


def _newest_valid_discovery(discoveries: list[Wp1Discovery]) -> Wp1Discovery | None:
    valid = [item for item in discoveries if item.role == "VALID"]
    if not valid:
        return None
    return max(valid, key=lambda item: item.captured_at_utc)


def register_manifest(archive_path: Path, *, source_archive_sha256_mac_capture: str | None = None) -> dict:
    archive = _load_archive(archive_path)
    entry = validate_wp1_archive(archive, archive_path)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if _manifest_evidence_roles(manifest).get(entry["evidence_id"]) == "REQUALIFICATION":
        raise ValueError(
            f"evidence_id {entry['evidence_id']} is registered as requalification; "
            "do not append to diagnostic_archives via --register",
        )
    for item in manifest.get("requalification_archives") or []:
        if item.get("archive_path") == entry["archive_path"]:
            raise ValueError(
                f"archive_path {entry['archive_path']} is listed under requalification_archives",
            )
    if source_archive_sha256_mac_capture:
        entry["source_archive_sha256_mac_capture"] = source_archive_sha256_mac_capture
    archives = manifest.setdefault("diagnostic_archives", [])
    committed = entry["archive_sha256_committed"]
    for item in archives:
        if item.get("evidence_id") == entry["evidence_id"]:
            existing = item.get("archive_sha256_committed") or item.get("archive_sha256")
            if existing and existing != committed:
                raise ValueError(
                    f"evidence_id {entry['evidence_id']} already registered with a different "
                    f"archive_sha256_committed",
                )
            return item
    archives.append(entry)
    manifest["diagnostic_archives"] = sorted(
        archives,
        key=lambda item: item.get("captured_at_utc") or "",
        reverse=True,
    )
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?", help="WP1 evidence JSON under analysis/evidence/")
    parser.add_argument(
        "--find",
        action="store_true",
        help="list VALID, HISTORICAL, and REQUALIFICATION archives (newest capture first); no path required",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="append validated entry to profiler-overhead-diagnosis-manifest.json",
    )
    parser.add_argument(
        "--register-latest",
        action="store_true",
        help="register the newest VALID WP1 archive by recorded_at_utc / evidence_id",
    )
    parser.add_argument(
        "--source-sha256",
        metavar="SHA256",
        help="Mac-emitted archive SHA before path sanitization (stored alongside committed SHA)",
    )
    parser.add_argument("--markdown", action="store_true", help="print recipe tables to stdout")
    args = parser.parse_args()

    if args.find or args.register_latest:
        discoveries = discover_wp1_archives()
        if not discoveries:
            print("No profiler_overhead_diagnosis_v1 archives under analysis/evidence/", file=sys.stderr)
            return 1
        if args.find:
            for item in discoveries:
                rel = item.path.relative_to(ROOT)
                print(f"{item.role}\t{rel}\t{item.evidence_id}\t{item.captured_at_utc}\t{item.detail}")
        if args.register_latest:
            newest = _newest_valid_discovery(discoveries)
            if newest is None or newest.entry is None:
                print("No VALID v3 WP1 archive found to register", file=sys.stderr)
                return 1
            entry = register_manifest(newest.path, source_archive_sha256_mac_capture=args.source_sha256)
            print(json.dumps(entry, indent=2))
        return 0

    if not args.archive:
        parser.error("archive path is required unless --find or --register-latest is used")
    path = args.archive.expanduser().resolve()
    if not path.is_file():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1
    if args.register:
        entry = register_manifest(path, source_archive_sha256_mac_capture=args.source_sha256)
        print(json.dumps(entry, indent=2))
    if args.markdown or not args.register:
        print(summarize_markdown(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
