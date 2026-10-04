#!/usr/bin/env python3
"""Integrity sealing and compressed raw evidence for frame-model runs."""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
SCENE_REFERENCE = ROOT / "fixtures/venice_scene.png"
SCENE_REFERENCE_MANIFEST = ROOT / "fixtures/venice_scene.json"

SEAL_ARTIFACTS = (
    ("telemetry.csv", "telemetry.csv.gz"),
    ("power.samples.json", "power.samples.json.gz"),
    ("powermetrics.pliststream", "powermetrics.pliststream.gz"),
)

CAPSULE_INVENTORY_FILES = (
    "manifest.json",
    "events.jsonl",
    "auto-probe.csv",
    "control.bin",
    "telemetry.csv.gz",
    "power.samples.json.gz",
    "powermetrics.pliststream.gz",
    "powermetrics.stderr",
    "ready-scene.png",
    "game-ready.log",
    "game-ready.reconstructed.log",
    "report.json",
    "report.md",
    "salvage-report.json",
    "salvage-report.md",
)

LOCAL_SIDECAR_FILES = (
    "game.stdout",
    "game.stderr",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(readable) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = readable.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    return total, digest.hexdigest()


def _logical_hash_gzip(path: Path) -> tuple[int, str]:
    with gzip.open(path, "rb") as handle:
        return _sha256_stream(handle)


def _gzip_compress(source: Path, destination: Path) -> None:
    with source.open("rb") as raw, destination.open("wb") as out, gzip.GzipFile(
        fileobj=out, mode="wb", compresslevel=9, mtime=0
    ) as compressed:
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
    identity = {
        "status": "reconstructed_incomplete",
        "policy": "historical_reconstructed",
        "candidate_commit": commit,
        "candidate_commit_short": commit[:7] if commit else "b0d49b44",
        "basis": basis,
        "missing": [
            "persisted capture-time git HEAD",
            "persisted capture-time controller SHA-256",
        ],
    }
    if manifest and (manifest.get("identity_at_capture") or {}).get("identity_start"):
        identity["policy"] = "recorded_direct"
        identity["status"] = "recorded_at_capture"
        identity.pop("missing", None)
    return identity


def seal_artifact_pair(run_dir: Path, plain_name: str, gzip_name: str) -> dict[str, Any] | None:
    plain = run_dir / plain_name
    gzip_path = run_dir / gzip_name
    if plain.is_file():
        _gzip_compress(plain, gzip_path)
        logical_bytes = plain.stat().st_size
        logical_sha256 = _sha256_file(plain)
    elif gzip_path.is_file():
        logical_bytes, logical_sha256 = _logical_hash_gzip(gzip_path)
    else:
        return None
    return {
        "plain_path": plain_name,
        "gzip_path": gzip_name,
        "uncompressed_bytes": logical_bytes,
        "uncompressed_sha256": logical_sha256,
        "gzip_bytes": gzip_path.stat().st_size,
        "gzip_sha256": _sha256_file(gzip_path),
        "compression": "gzip-9",
    }


def _sanitize_path(path: Path) -> str:
    text = str(path)
    home = str(Path.home())
    if text.startswith(home):
        return "$HOME/" + text[len(home) + 1 :]
    return text


def _phase_c_wall_window(manifest: dict, run_dir: Path) -> tuple[dt.datetime | None, dt.datetime | None]:
    anchor = manifest.get("clock_anchor") or {}
    wall_ns = anchor.get("wall_ns")
    if wall_ns is None:
        events_path = run_dir / "events.jsonl"
        if events_path.is_file():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "clock_anchor":
                    wall_ns = event.get("wall_ns")
                    break
    if wall_ns is None:
        return None, None
    start = dt.datetime.fromtimestamp(wall_ns / 1_000_000_000, tz=dt.timezone.utc)
    end = start + dt.timedelta(hours=2)
    return start, end


_LOG_TIMESTAMP_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"
)
_EU4_SESSION_TIME_RE = re.compile(r"^Time:(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", re.MULTILINE)


def _parse_log_line_timestamp(line: str) -> dt.datetime | None:
    match = _LOG_TIMESTAMP_RE.match(line)
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None


def _parse_eu4_session_start(text: str) -> dt.datetime | None:
    match = _EU4_SESSION_TIME_RE.search(text)
    if not match:
        return None
    try:
        return dt.datetime.strptime(match.group(1), "%Y-%m-%d %H:%M").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        return None


def _readiness_markers() -> tuple[str, ...]:
    return (
        "Human Player set as primary local",
        "Venice",
        "Launching SINGLEPLAYER-game",
        "Start-date: 1444.11.11",
        "End RestoreDeviceObjects",
    )


def _block_satisfies_markers(block: str) -> bool:
    from autonomous_runner import game_log_ready

    return game_log_ready(block)


def _block_anchored_to_window(block: str, start: dt.datetime, end: dt.datetime) -> bool:
    session_start = _parse_eu4_session_start(block)
    if session_start is not None:
        return start <= session_start <= end
    timestamps = [_parse_log_line_timestamp(line) for line in block.splitlines()]
    timestamps = [value for value in timestamps if value is not None]
    if not timestamps:
        return False
    return any(start <= value <= end for value in timestamps)


def _save_marker_in_block(block: str, manifest: dict) -> bool:
    working = manifest.get("working_save") or ""
    name = Path(working.replace("$HOME/", str(Path.home()))).name
    if not name:
        return False
    return name in block


def persist_capture_time_readiness_log(run_dir: Path, game_log: Path, old_log: bytes) -> dict[str, Any]:
    from autonomous_runner import fresh_game_log

    run_dir = Path(run_dir)
    text = fresh_game_log(game_log, old_log)
    filename = None
    if text:
        filename = "game-ready.log"
        (run_dir / filename).write_text(text, encoding="utf-8")
    return {
        "origin": "captured_at_readiness",
        "status": "captured_at_readiness",
        "old_log_bytes": len(old_log),
        "old_log_sha256": hashlib.sha256(old_log).hexdigest(),
        "committed_filename": filename,
        "bytes": len(text.encode("utf-8")) if text else 0,
    }


def extract_game_ready_log(
    run_dir: Path,
    game_log_path: Path | None = None,
    *,
    manifest: dict | None = None,
) -> dict[str, Any]:
    """Build a readiness log excerpt with explicit provenance metadata."""
    run_dir = Path(run_dir)
    if manifest is None:
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    if game_log_path is None:
        game_log_path = base.USER_DATA / "logs/game.log"
    window_start, window_end = _phase_c_wall_window(manifest, run_dir)
    provenance: dict[str, Any] = {
        "source_path": _sanitize_path(game_log_path),
        "extraction_method": "heuristic_marker_block",
        "markers": list(_readiness_markers()),
        "phase_c_wall_window_utc": (
            {
                "start": window_start.isoformat() if window_start else None,
                "end": window_end.isoformat() if window_end else None,
            }
            if window_start and window_end
            else None
        ),
    }
    if not game_log_path.is_file():
        provenance["status"] = "unavailable"
        provenance["reason"] = "game.log not present locally"
        return provenance

    text = game_log_path.read_text(encoding="utf-8", errors="replace")
    if _block_satisfies_markers(text):
        session_start = _parse_eu4_session_start(text)
        anchored = False
        if window_start and window_end and session_start is not None:
            anchored = window_start <= session_start <= window_end
        if not anchored:
            anchored = _save_marker_in_block(text, manifest)
        candidates = [(0, text, anchored)]
    else:
        candidates = []
    if not candidates:
        launch_positions = [
            match.start()
            for match in re.finditer(r"Launching\s+SINGLEPLAYER-game", text, flags=re.IGNORECASE)
        ]
        for position in launch_positions:
            end_marker = text.find("End RestoreDeviceObjects", position)
            if end_marker < 0:
                continue
            end_line = text.find("\n", end_marker)
            block = text[position : end_line if end_line >= 0 else len(text)]
            if not _block_satisfies_markers(block):
                continue
            anchored = False
            if window_start and window_end:
                anchored = _block_anchored_to_window(block, window_start, window_end)
            if not anchored:
                anchored = _save_marker_in_block(block, manifest)
            candidates.append((position, block, anchored))

    if not candidates:
        provenance["status"] = "unavailable"
        provenance["reason"] = "no readiness marker block found in game.log"
        return provenance

    position, block, anchored = max(candidates, key=lambda item: item[0])
    provenance["status"] = (
        "reconstructed_posthoc_anchored" if anchored else "reconstructed_posthoc_unanchored"
    )
    filename = "game-ready.log" if anchored else "game-ready.reconstructed.log"
    out_path = run_dir / filename
    out_path.write_text(block.rstrip() + "\n", encoding="utf-8")
    provenance["committed_filename"] = filename
    provenance["bytes"] = out_path.stat().st_size
    provenance["sha256"] = _sha256_file(out_path)
    provenance["log_offset"] = position
    return provenance


def _scene_capture_block(manifest: dict, run_dir: Path) -> dict[str, Any] | None:
    scene_path = run_dir / "ready-scene.png"
    if not scene_path.is_file():
        return None
    block: dict[str, Any] = {
        "capture_sha256": _sha256_file(scene_path),
    }
    try:
        from PIL import Image

        with Image.open(scene_path) as image:
            block["width"], block["height"] = image.size
    except OSError:
        pass
    if SCENE_REFERENCE_MANIFEST.is_file():
        ref_manifest = json.loads(SCENE_REFERENCE_MANIFEST.read_text(encoding="utf-8"))
        block["reference_manifest_sha256"] = base.sha256(SCENE_REFERENCE_MANIFEST)
        block["reference_image_sha256"] = ref_manifest.get("sha256")
        source_run = ref_manifest.get("source_run")
        if source_run:
            block["reference_source_run"] = _sanitize_path(Path(source_run))
        if SCENE_REFERENCE.is_file() and ref_manifest.get("sha256"):
            if base.sha256(SCENE_REFERENCE) != ref_manifest["sha256"]:
                block["reference_image_sha256_note"] = "fixture file on disk differs from manifest sha256"
    repo_ref = ROOT / "benchmark/submission_scene_reference.json"
    if repo_ref.is_file():
        repo_manifest = json.loads(repo_ref.read_text(encoding="utf-8"))
        repo_image = ROOT / repo_manifest["reference_image"]
        block["repository_validation_reference"] = {
            "manifest": str(repo_ref.relative_to(ROOT)),
            "image": repo_manifest.get("reference_image"),
            "sha256": repo_manifest.get("sha256"),
            "image_present": repo_image.is_file(),
        }
        if repo_image.is_file() and repo_manifest.get("sha256"):
            block["repository_validation_reference"]["image_sha256"] = base.sha256(repo_image)
    alignment = manifest.get("scene_alignment")
    if alignment:
        block["capture_time_comparison"] = {
            "mean_rgb_delta": alignment.get("mean_rgb_delta"),
            "shift_x_px": alignment.get("shift_x_px"),
            "shift_y_px": alignment.get("shift_y_px"),
        }
    return block


def _capture_inventory(run_dir: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    for name in CAPSULE_INVENTORY_FILES:
        path = run_dir / name
        if path.is_file():
            inventory[name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
    return inventory


def _sidecar_metadata(run_dir: Path) -> dict[str, Any]:
    sidecars: dict[str, Any] = {}
    for name in LOCAL_SIDECAR_FILES:
        path = run_dir / name
        if not path.is_file():
            sidecars[name] = {"present": False}
            continue
        size = path.stat().st_size
        sidecars[name] = {
            "present": True,
            "bytes": size,
            "empty": size == 0,
            "sha256": _sha256_file(path) if size else None,
        }
    return sidecars


def infer_capsule_profile(run_dir: Path, manifest: dict | None = None) -> str:
    run_dir = Path(run_dir)
    if manifest is None:
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    derived_status = manifest.get("derived_analysis_status")
    if derived_status == "failed":
        return "intrusive_capture_only_v1"
    has_report = (run_dir / "report.json").is_file() and (run_dir / "salvage-report.json").is_file()
    has_ready = (run_dir / "game-ready.log").is_file() or (run_dir / "game-ready.reconstructed.log").is_file()
    if manifest.get("report_kind") == "intrusive_diagnostic" and has_report and has_ready:
        return "intrusive_complete_v1"
    if manifest.get("report_kind") == "intrusive_diagnostic":
        return "intrusive_capture_only_v1"
    return "intrusive_capture_only_v1"


def seal_run_evidence(
    run_dir: Path,
    *,
    identity_at_capture: dict | None = None,
    identity_snapshot_fn=None,
    write_game_log: bool = True,
    capsule_profile: str | None = None,
    preserve_readiness_origin: bool = True,
) -> dict:
    """Write raw_evidence.json and optional gzip companions."""
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}

    if identity_snapshot_fn is None:
        from eu4_frame_model import _offline_git_identity_snapshot

        identity_snapshot_fn = _offline_git_identity_snapshot

    readiness_log = None
    if write_game_log:
        for name in ("game-ready.log", "game-ready.reconstructed.log"):
            path = run_dir / name
            if path.is_file():
                readiness_log = {
                    "status": "present_on_disk",
                    "committed_filename": name,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
                break
        if readiness_log is None:
            readiness_log = extract_game_ready_log(run_dir, manifest=manifest)

    identity_at_seal = identity_snapshot_fn()
    artifacts: dict[str, Any] = {}
    for plain_name, gzip_name in SEAL_ARTIFACTS:
        entry = seal_artifact_pair(run_dir, plain_name, gzip_name)
        if entry:
            artifacts[plain_name] = entry

    dropped = (manifest.get("probe_integrity") or {}).get("dropped_records")
    profile = capsule_profile or infer_capsule_profile(run_dir, manifest)
    prior_path = run_dir / "raw_evidence.json"
    prior_readiness = None
    if preserve_readiness_origin and prior_path.is_file():
        try:
            prior_readiness = json.loads(prior_path.read_text(encoding="utf-8")).get("readiness_log")
        except (OSError, json.JSONDecodeError):
            prior_readiness = None
    if prior_readiness and prior_readiness.get("origin"):
        merged = dict(prior_readiness)
        if readiness_log:
            merged.update(readiness_log)
        merged["origin"] = prior_readiness["origin"]
        readiness_log = merged
    stderr_path = run_dir / "powermetrics.stderr"
    powermetrics_stderr = None
    if stderr_path.is_file():
        size = stderr_path.stat().st_size
        powermetrics_stderr = {
            "bytes": size,
            "empty": size == 0,
            "sha256": _sha256_file(stderr_path) if size else None,
        }
    evidence = {
        "sealed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "capsule_profile": profile,
        "run_dir": str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir),
        "capture_identity": reconstructed_capture_identity(manifest),
        "identity_at_capture": identity_at_capture,
        "identity_at_seal": identity_at_seal,
        "artifacts": artifacts,
        "capture_inventory": _capture_inventory(run_dir),
        "capture_inventory_note": "All committed capsule files except raw_evidence.json",
        "scene_capture": _scene_capture_block(manifest, run_dir),
        "readiness_log": readiness_log,
        "local_sidecars": _sidecar_metadata(run_dir),
        "probe_integrity": manifest.get("probe_integrity"),
        "dropped_records": dropped,
        "powermetrics_stderr": powermetrics_stderr,
    }
    out = run_dir / "raw_evidence.json"
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


PROFILE_REQUIRED_FILES = {
    "intrusive_complete_v1": (
        "manifest.json",
        "telemetry.csv.gz",
        "power.samples.json.gz",
        "powermetrics.pliststream.gz",
        "ready-scene.png",
        "game-ready.log",
        "report.json",
        "salvage-report.json",
    ),
    "intrusive_capture_only_v1": (
        "manifest.json",
        "telemetry.csv.gz",
        "power.samples.json.gz",
        "powermetrics.pliststream.gz",
        "ready-scene.png",
    ),
}


def verify_run_evidence(
    run_dir: Path,
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Hermetic capsule verification using committed run-dir bytes only."""
    run_dir = Path(run_dir)
    raw_path = run_dir / "raw_evidence.json"
    if not raw_path.is_file():
        raise ValueError("raw_evidence.json is missing")
    sealed = json.loads(raw_path.read_text(encoding="utf-8"))
    expected_profile = profile or sealed.get("capsule_profile")
    if not expected_profile:
        raise ValueError("capsule_profile missing from raw_evidence.json")
    if profile and sealed.get("capsule_profile") != profile:
        raise ValueError(
            f"sealed profile {sealed.get('capsule_profile')} != asserted profile {profile}"
        )
    inventory = sealed.get("capture_inventory") or {}
    missing = []
    for name in PROFILE_REQUIRED_FILES.get(expected_profile, ()):
        if name not in inventory:
            missing.append(name)
        elif not (run_dir / name).is_file():
            missing.append(name)
        else:
            on_disk = _sha256_file(run_dir / name)
            if inventory[name].get("sha256") != on_disk:
                raise ValueError(f"inventory hash mismatch for {name}")
    power_entry = (sealed.get("artifacts") or {}).get("power.samples.json")
    plist_entry = (sealed.get("artifacts") or {}).get("powermetrics.pliststream")
    semantic = {"status": "skipped"}
    if power_entry and plist_entry:
        power_gz = run_dir / "power.samples.json.gz"
        plist_gz = run_dir / "powermetrics.pliststream.gz"
        if power_gz.is_file() and plist_gz.is_file():
            power_payload = json.loads(gzip.open(power_gz, "rt", encoding="utf-8").read())
            plist_count = 0
            with gzip.open(plist_gz, "rb") as handle:
                partial = b""
                import plistlib

                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    parts = (partial + chunk).split(b"\0")
                    partial = parts.pop()
                    for part in parts:
                        if part.strip():
                            plistlib.loads(part.strip())
                            plist_count += 1
            semantic = {
                "status": "passed" if len(power_payload) == plist_count else "failed",
                "power_sample_count": len(power_payload),
                "plist_record_count": plist_count,
            }
            if semantic["status"] != "passed":
                raise ValueError("semantic plist↔power sample count mismatch")
    if missing:
        raise ValueError(f"missing required capsule files: {', '.join(missing)}")
    return {
        "status": "passed",
        "capsule_profile": expected_profile,
        "missing_files": missing,
        "semantic_plist_power": semantic,
        "inventory_files": len(inventory),
    }
