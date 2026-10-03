#!/usr/bin/env python3
"""Integrity sealing and compressed raw evidence for frame-model runs."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]

SEAL_ARTIFACTS = (
    ("telemetry.csv", "telemetry.csv.gz"),
    ("power.samples.json", "power.samples.json.gz"),
)

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gzip_compress(source: Path, destination: Path) -> None:
    with source.open("rb") as raw, gzip.open(destination, "wb", compresslevel=9) as compressed:
        while True:
            chunk = raw.read(1 << 20)
            if not chunk:
                break
            compressed.write(chunk)


def resolve_telemetry_path(run_dir: Path) -> Path | None:
    plain = run_dir / "telemetry.csv"
    if plain.is_file():
        return plain
    gz = run_dir / "telemetry.csv.gz"
    if gz.is_file():
        return gz
    return None


def resolve_power_samples_path(run_dir: Path) -> Path | None:
    plain = run_dir / "power.samples.json"
    if plain.is_file():
        return plain
    gz = run_dir / "power.samples.json.gz"
    if gz.is_file():
        return gz
    return None


def _candidate_capture_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "b0d49b44^{commit}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        pass
    return None


def reconstructed_capture_identity(manifest: dict | None = None) -> dict:
    commit = _candidate_capture_commit()
    basis = [
        "clean-tree preflight enforced before intrusive build (contract preflight)",
        "repository history and run/event timestamps near 2026-10-03T21:02Z UTC",
        "recorded C source and dylib hashes in manifest evidence.build",
    ]
    if manifest:
        build = (manifest.get("evidence") or {}).get("build") or manifest.get("build") or {}
        if build.get("library_sha256") or build.get("executable_sha256"):
            basis.append("manifest executable and profiler dylib SHA-256")
    return {
        "status": "reconstructed_incomplete",
        "candidate_commit": commit,
        "candidate_commit_short": commit[:7] if commit else "b0d49b44",
        "basis": basis,
        "missing": [
            "persisted capture-time git HEAD",
            "persisted capture-time controller SHA-256",
        ],
    }


def seal_artifact_pair(run_dir: Path, plain_name: str, gzip_name: str) -> dict[str, Any] | None:
    plain = run_dir / plain_name
    if not plain.is_file():
        gz_only = run_dir / gzip_name
        if not gz_only.is_file():
            return None
        return {
            "plain_path": plain_name,
            "gzip_path": gzip_name,
            "uncompressed_bytes": None,
            "uncompressed_sha256": None,
            "gzip_bytes": gz_only.stat().st_size,
            "gzip_sha256": _sha256_file(gz_only),
            "compression": "gzip-9",
            "note": "gzip only on disk; uncompressed not present locally",
        }
    gzip_path = run_dir / gzip_name
    _gzip_compress(plain, gzip_path)
    return {
        "plain_path": plain_name,
        "gzip_path": gzip_name,
        "uncompressed_bytes": plain.stat().st_size,
        "uncompressed_sha256": _sha256_file(plain),
        "gzip_bytes": gzip_path.stat().st_size,
        "gzip_sha256": _sha256_file(gzip_path),
        "compression": "gzip-9",
    }


def seal_run_evidence(
    run_dir: Path,
    *,
    identity_at_capture: dict | None = None,
    identity_snapshot_fn=None,
) -> dict:
    """Write raw_evidence.json and optional gzip companions."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    if identity_snapshot_fn is None:
        from eu4_frame_model import _offline_git_identity_snapshot

        identity_snapshot_fn = _offline_git_identity_snapshot

    identity_at_seal = identity_snapshot_fn()
    artifacts: dict[str, Any] = {}
    for plain_name, gzip_name in SEAL_ARTIFACTS:
        entry = seal_artifact_pair(run_dir, plain_name, gzip_name)
        if entry:
            artifacts[plain_name] = entry

    dropped = (manifest.get("probe_integrity") or {}).get("dropped_records")
    evidence = {
        "sealed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_dir": str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir),
        "capture_identity": reconstructed_capture_identity(manifest),
        "identity_at_capture": identity_at_capture,
        "identity_at_seal": identity_at_seal,
        "artifacts": artifacts,
        "probe_integrity": manifest.get("probe_integrity"),
        "dropped_records": dropped,
    }
    out = run_dir / "raw_evidence.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence
