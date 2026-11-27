#!/usr/bin/env python3.14
"""Validation and fresh-process baselines for the cache-profile evidence.

Checks that the measured workload is the sealed present and that the current
translator reproduces the sealed shadow artifacts byte-for-byte, and records
first-call baselines that a within-process microbenchmark cannot show.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

EVID = Path(__file__).resolve().parent
ROOT = EVID.parents[3]
CAPTURES = ROOT / ".local/m7/goal-round1"

sys.path.insert(0, str(ROOT))
from automation import gfx_frame  # noqa: E402

CASES = {
    "present-904": {
        "frame": CAPTURES / "capture-c/frame",
        "shadow": CAPTURES / "capture-c/shadow",
    },
    "present-916": {
        "frame": CAPTURES / "capture-b/frames/frame-000001",
        "shadow": CAPTURES / "capture-b/shadow/frame-000001",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def content_hashes(directory: Path, suffix: str) -> dict[str, str]:
    """hash -> filename for every payload with this suffix."""
    result = {}
    for path in directory.glob(f"*{suffix}"):
        result[sha256(path)] = path.name
    return result


def fresh_process_trace_load(frame: Path, reps: int = 3) -> dict:
    script = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from automation import gfx_frame\n"
        "started = time.perf_counter()\n"
        "trace = gfx_frame.load_trace(%r)\n"
        "loaded = time.perf_counter()\n"
        "identity = gfx_frame._shader_source_identity(trace)\n"
        "done = time.perf_counter()\n"
        "seen = {}\n"
        "for event in trace['_events']:\n"
        "    members = list(gfx_frame._iter_blob_members(event))\n"
        "    for attribute in event.get('attributes') or []:\n"
        "        if isinstance(attribute, dict) and attribute.get('data'):\n"
        "            members.append({'file': attribute['data']})\n"
        "    for member in members:\n"
        "        name = member.get('file')\n"
        "        if name and name not in seen:\n"
        "            seen[name] = (%r + '/blobs/' + name)\n"
        "verified = trace.get('_blob_count_verified')\n"
        "import os\n"
        "bytes_verified = sum(os.path.getsize(path) for path in seen.values())\n"
        "print(loaded - started, done - loaded, verified, bytes_verified)\n"
    ) % (str(ROOT), str(frame), str(frame))
    samples = []
    for _ in range(reps):
        completed = subprocess.run([sys.executable, "-c", script],
                                   cwd=str(ROOT), capture_output=True,
                                   text=True, check=True)
        loaded, identity, verified, bytes_verified = completed.stdout.split()
        samples.append({
            "trace_load_s": float(loaded),
            "identity_s": float(identity),
            "blobs_verified": int(verified),
            "blob_bytes": int(bytes_verified),
        })
    return {
        "reps": reps,
        "trace_load_s": [round(item["trace_load_s"], 6) for item in samples],
        "identity_s": [round(item["identity_s"], 6) for item in samples],
        "trace_load_median_s": round(statistics.median(
            item["trace_load_s"] for item in samples), 6),
        "blobs_verified": samples[0]["blobs_verified"],
        "blob_bytes": samples[0]["blob_bytes"],
        "reference_bytes": (frame / "frame-reference.bgra").stat().st_size,
    }


def command_baseline(reps: int = 5) -> dict:
    samples = []
    for _ in range(reps):
        started = time.perf_counter()
        subprocess.run([sys.executable, "-m", "automation.gfx_frame"],
                       cwd=str(ROOT), capture_output=True, check=False)
        samples.append(time.perf_counter() - started)
    return {"reps": reps,
            "samples_s": [round(value, 6) for value in samples],
            "median_s": round(statistics.median(samples), 6)}


def main() -> int:
    report = {"schema": "gfx-cache-profile-validation/v1",
              "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "cases": {}, "baselines": {}}
    for label, paths in CASES.items():
        frame, shadow = paths["frame"], paths["shadow"]
        measurement = json.loads(
            (EVID / f"measurements-{label}.json").read_text())
        seal = json.loads((frame / "frame-seal.json").read_text())
        trace = gfx_frame.load_trace(frame)
        identity = gfx_frame._shader_source_identity(trace)
        entry = {
            "frame_dir": str(frame),
            "sealed": seal.get("sealed"),
            "complete": seal.get("complete"),
            "session_id": seal.get("session_id"),
            "present_index": seal.get("present_index"),
            "events": seal.get("operation_count"),
            "draws": len([event for event in trace["_events"]
                          if event.get("kind") == "draw"]),
            "frame_seal_sha256": sha256(frame / "frame-seal.json"),
            "frame_trace_sha256": sha256(frame / "frame-trace.json"),
            "event_stream_sha256": seal.get("event_stream_sha256"),
            "reference_sha256": seal.get("reference_sha256"),
            "identity_sha256": identity["sha256"],
            "identity_matches_measurement": (
                identity["sha256"]
                == measurement["cache_identity"]["sha256"]),
            "translation_result": measurement["translation_result"],
        }
        # Sealed shadow artifacts, produced before this measurement series.
        shadow_report = shadow / "shadow-report.json"
        if shadow_report.is_file():
            sealed = json.loads(shadow_report.read_text())
            metal = sealed.get("metal") or {}
            entry["sealed_shadow_report"] = {
                "status": sealed.get("status"),
                "events": metal.get("events"),
                "encoded": metal.get("encoded"),
                "failed": metal.get("failed"),
                "command_status": metal.get("command_status"),
                "duration_ns": metal.get("duration_ns"),
                "total_duration_ns": sealed.get("total_duration_ns"),
                "checkpoint_count": sealed.get("checkpoint_count"),
            }
        sealed_cache = shadow / "shaders/translation-cache.json"
        if sealed_cache.is_file():
            old = json.loads(sealed_cache.read_text())["identity"]
            entry["sealed_translation_identity"] = {
                "schema": old.get("schema"),
                "sha256": old.get("sha256"),
                "translator": old.get("translator"),
            }
            old_programs = old.get("programs") or {}
            same = all(
                identity["programs"][program]["stages"] == stages
                for program, stages in old_programs.items())
            entry["sealed_stage_digests_match_current"] = same
            entry["sealed_program_count"] = len(old_programs)
        # Our cold translation vs the sealed shadow shader payload.
        cold_out = Path(measurement["translation_runs"][0]["out"])
        sealed_shaders = shadow / "shaders"
        if sealed_shaders.is_dir():
            sealed_msl = content_hashes(sealed_shaders, ".metal")
            cold_msl = content_hashes(cold_out, ".metal")
            sealed_glsl = content_hashes(sealed_shaders, ".glsl")
            cold_glsl = content_hashes(cold_out, ".glsl")
            entry["sealed_shader_payload"] = {
                "msl_content_sets_equal": set(sealed_msl) == set(cold_msl),
                "msl_unique_sealed": len(sealed_msl),
                "msl_unique_current": len(cold_msl),
                "msl_filenames_equal": (
                    set(sealed_msl.values()) == set(cold_msl.values())),
                "glsl_content_sets_equal": set(sealed_glsl) == set(cold_glsl),
                "glsl_unique_sealed": len(sealed_glsl),
                "glsl_unique_current": len(cold_glsl),
            }
        # Replayed Metal frame vs the sealed Metal frame.
        replayed = (EVID / "work" / label / "host-full-1" / "metal-frame.bgra")
        sealed_frame = shadow / "metal-frame.bgra"
        if replayed.is_file() and sealed_frame.is_file():
            entry["metal_frame"] = {
                "replayed_sha256": sha256(replayed),
                "sealed_sha256": sha256(sealed_frame),
                "identical": sha256(replayed) == sha256(sealed_frame),
            }
        report["cases"][label] = entry

    report["baselines"] = {
        "fresh_process_present904": fresh_process_trace_load(
            CASES["present-904"]["frame"], 3),
        "module_start_baseline": command_baseline(5),
    }
    report["identity_equal_across_presents"] = (
        report["cases"]["present-904"]["identity_sha256"]
        == report["cases"]["present-916"]["identity_sha256"])
    out = EVID / "validation.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "validation": str(out),
        "identity_equal_across_presents": report[
            "identity_equal_across_presents"],
        "present904_metal_frame_identical": report["cases"]["present-904"]
            .get("metal_frame", {}).get("identical"),
        "present916_metal_frame_identical": report["cases"]["present-916"]
            .get("metal_frame", {}).get("identical"),
        "baselines": report["baselines"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
