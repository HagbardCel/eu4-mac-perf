#!/usr/bin/env python3.14
"""Offline cache-profile measurements for one sealed captured frame.

Read-only against the sealed capture.  Every write goes under this evidence
directory (``work/``); the sealed frame and the existing shadow artifacts are
never touched.

Usage:
    python3.14 measure.py --label present-904 \
        --frame .local/m7/goal-round1/capture-c/frame
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

EVID = Path(__file__).resolve().parent
ROOT = EVID.parents[3]
WORK = EVID / "work"
HOST = ROOT / "benchmark/.build/gfx_frame_replay"
HOST_STAMP = ROOT / "benchmark/.build/gfx_frame_replay.deps.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_shader_translate, gfx_frame  # noqa: E402


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def system_probe(label: str) -> dict:
    """Load average plus the current CPU consumers, for contention context."""
    try:
        load = [round(value, 2) for value in os.getloadavg()]
    except OSError:
        load = []
    rows = []
    try:
        text = subprocess.run(
            ["ps", "-A", "-o", "pcpu=,comm="],
            capture_output=True, text=True, check=False).stdout
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            percent, _, command = line.partition(" ")
            try:
                value = float(percent)
            except ValueError:
                continue
            rows.append({"cpu_percent": value, "command": command.strip()})
    except OSError:
        pass
    rows.sort(key=lambda item: item["cpu_percent"], reverse=True)
    return {"probe": label, "at": now_iso(), "loadavg": load,
            "top_cpu": rows[:6]}


def stats(samples: list[float]) -> dict:
    return {
        "samples_s": [round(value, 6) for value in samples],
        "median_s": round(statistics.median(samples), 6),
        "min_s": round(min(samples), 6),
        "max_s": round(max(samples), 6),
        "reps": len(samples),
    }


def read_json(path: Path):
    return json.loads(path.read_text())


def cache_stats(cache_root: Path) -> dict:
    files = [path for path in cache_root.rglob("*") if path.is_file()]
    return {"files": len(files),
            "bytes": sum(path.stat().st_size for path in files)}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def core_artifact_digest(root: Path) -> str:
    """Digest of the translated shader payload, excluding cache metadata."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("ready", "shader-receipt.json",
                         "translation-cache.json"):
            continue
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(sha256_file(path).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def run_translate(out_dir: Path, cache_dir: Path | None,
                  probe: list[dict], *, warm: bool = False) -> dict:
    """One ``gfx_frame translate`` invocation, timed by wall clock."""
    if out_dir.exists():
        raise RuntimeError(f"translate out dir already exists: {out_dir}")
    command = [sys.executable, "-m", "automation.gfx_frame", "translate",
               "--frame", str(FRAME), "--out", str(out_dir)]
    if cache_dir is not None:
        if warm and not cache_dir.exists():
            raise RuntimeError(f"warm cache dir missing: {cache_dir}")
        command += ["--cache-dir", str(cache_dir)]
    probe.append(system_probe(f"before {out_dir.name}"))
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                               text=True, check=False)
    wall = time.perf_counter() - started
    probe.append(system_probe(f"after {out_dir.name}"))
    summary = json.loads(completed.stdout)
    record = {
        "out": str(out_dir),
        "cache": str(cache_dir) if cache_dir else None,
        "returncode": completed.returncode,
        "wall_s": round(wall, 6),
        "summary": summary,
    }
    if completed.returncode != 0 or summary.get("status") != "ok":
        record["stderr_tail"] = completed.stderr[-2000:]
    return record


def run_host(label: str, out_dir: Path, shaders: Path, extra: list[str],
             probe: list[dict]) -> dict:
    command = [str(HOST), "--frame", str(FRAME), "--shaders", str(shaders),
               "--out", str(out_dir)] + extra
    probe.append(system_probe(f"before {label}"))
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                               text=True, check=False, timeout=900)
    wall = time.perf_counter() - started
    probe.append(system_probe(f"after {label}"))
    report = {}
    report_path = out_dir / "replay.json"
    if report_path.is_file():
        report = read_json(report_path)
    return {
        "label": label,
        "argv": command[1:],
        "returncode": completed.returncode,
        "wall_s": round(wall, 6),
        "status": report.get("status"),
        "events": report.get("events"),
        "encoded": report.get("encoded"),
        "failed": report.get("failed"),
        "command_status": report.get("command_status"),
        "readback": report.get("readback"),
        "partial": report.get("partial"),
        "has_duration_ns": "duration_ns" in report,
        "report_keys": sorted(report.keys()),
        "stderr_tail": completed.stderr[-1000:],
    }


def timing_keys(value, prefix: str = "") -> list[str]:
    """Every key in the replay report that smells like a timing field."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = key.lower()
            if lowered == "timed_out":
                continue
            if "duration" in lowered or "gpu" in lowered or lowered.endswith("_ns") \
                    or lowered.endswith("_time") or lowered.endswith("_times"):
                found.append(prefix + key)
            found.extend(timing_keys(item, prefix + key + "."))
    elif isinstance(value, list):
        for index, item in enumerate(value[:2]):
            found.extend(timing_keys(item, prefix + f"[{index}]."))
    return found


def bench(fn, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return stats(samples)


def subprocess_baseline(command: list[str], cwd: Path, iterations: int) -> dict:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        subprocess.run(command, cwd=str(cwd), capture_output=True, check=False)
        samples.append(time.perf_counter() - started)
    return stats(samples)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--frame", required=True)
    parser.add_argument("--reps", type=int, default=3)
    args = parser.parse_args()

    global FRAME, WORK
    FRAME = (ROOT / args.frame).resolve() if not Path(args.frame).is_absolute() \
        else Path(args.frame)
    WORK = EVID / "work" / args.label

    if WORK.exists():
        resolved = WORK.resolve()
        expected_parent = (EVID / "work").resolve()
        if resolved.parent != expected_parent:
            raise SystemExit(f"refusing to remove unexpected path: {resolved}")
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    trace = gfx_frame.load_trace(FRAME)
    events = trace.get("_events") or []
    draws = [event for event in events if event.get("kind") == "draw"]
    identity = gfx_frame._shader_source_identity(trace)

    source_records: dict[tuple[str, str], str] = {}
    for event in draws:
        program = event.get("program") or {}
        for shader in program.get("shaders") or []:
            stage = str(shader.get("stage") or "")
            digest = str(shader.get("sha256") or "")
            name = shader.get("file")
            if stage and digest and name:
                source_records.setdefault((stage, digest), name)
    source_blobs = [(FRAME / "blobs" / name) for name in source_records.values()]

    report = {
        "schema": "gfx-cache-profile-measurements/v1",
        "label": args.label,
        "captured_at": now_iso(),
        "host": {
            "machine": os.uname().machine,
            "node": os.uname().nodename,
            "python": sys.version,
            "executable": sys.executable,
        },
        "frame": {
            "dir": str(FRAME),
            "frame_trace_sha256": sha256_file(FRAME / "frame-trace.json"),
            "frame_seal_sha256": sha256_file(FRAME / "frame-seal.json"),
            "events": len(events),
            "draws": len(draws),
            "seal": read_json(FRAME / "frame-seal.json"),
            "trace": read_json(FRAME / "frame-trace.json"),
            "unique_source_blobs": len(source_blobs),
            "source_bytes": sum(path.stat().st_size for path in source_blobs),
        },
        "cache_identity": {
            "sha256": identity["sha256"],
            "programs": len(identity["programs"]),
            "translator": identity["translator"],
        },
        "session_probe": [system_probe("start")],
        "translation_runs": [],
        "hashing": {},
        "host_runs": [],
        "supported_replay_run": None,
        "gpu_service_time": {
            "status": "unknown",
            "reason": (
                "benchmark/eu4_gfx_frame_replay.mm records no GPU service "
                "time: the success-path replay.json has no timing field and "
                "the host never reads MTLCommandBuffer.GPUStartTime/GPUEndTime. "
                "External wall time only bounds it."),
            "instrumentation": (
                "Add addCompletedHandler: on each committed MTLCommandBuffer, "
                "sum completed.GPUEndTime - completed.GPUStartTime, and publish "
                "the sum in replay.json; or record with Metal System Trace / an "
                "MTLCounterSampleBuffer. benchmark/eu4_gfx_perf.mm:72-86 "
                "already implements the addCompletedHandler variant for the "
                "live perf harness, but is not linked into this host."),
        },
        "native_cache": {
            "persistent": False,
            "in_process_function_cache": (
                "benchmark/eu4_gfx_frame_replay.mm:2162-2170 keys an "
                "NSMutableDictionary by MSL-bytes sha256 + entry + stage + "
                "compile options; :2182 newLibraryWithSource per unique key; "
                ":2202 stores the function."),
            "in_process_pipeline_cache": (
                "benchmark/eu4_gfx_frame_replay.mm:2690 "
                "newRenderPipelineStateWithDescriptor; :2701 stores in "
                "g_pipelines keyed by state_key."),
            "per_session_state": (
                "benchmark/eu4_gfx_frame_replay.mm:5526/5535-5536 create empty "
                "g_pipelines/g_functions dictionaries for each session."),
            "persistent_archive_search": (
                "no MTLBinaryArchive / metallib / newLibraryWithFile / "
                "newLibraryWithData reference in benchmark/eu4_gfx_frame_replay.mm"),
        },
    }

    # ------------------------------------------------------------------
    # Translation series: cold (empty cache root), warm (valid cache), and
    # plain (no cache dir) interleaved so ordering effects cancel.
    # ------------------------------------------------------------------
    probe = report["session_probe"]
    cold_runs: list[dict] = []
    warm_runs: list[dict] = []
    plain_runs: list[dict] = []
    last_cache: Path | None = None

    def do_cold(rep: int) -> None:
        nonlocal last_cache
        base = WORK / f"cold-{rep}"
        out_dir = base / "out"
        cache_dir = base / "cache"
        record = run_translate(out_dir, cache_dir, probe)
        record["kind"] = "cold"
        record["rep"] = rep
        record["cache_dir"] = str(cache_dir)
        record["cache_valid_after"] = gfx_frame._translation_cache_valid(
            cache_dir / identity["sha256"], identity)
        record["cache_stats"] = cache_stats(cache_dir / identity["sha256"])
        record["cache_key_matches_computed"] = (
            record["summary"]["translation_cache"]["key"] == identity["sha256"])
        cold_runs.append(record)
        last_cache = cache_dir

    def do_warm(rep: int) -> None:
        if last_cache is None:
            raise RuntimeError("warm requested before any cold cache exists")
        out_dir = WORK / f"warm-{rep}" / "out"
        record = run_translate(out_dir, last_cache, probe, warm=True)
        record["kind"] = "warm"
        record["rep"] = rep
        cache_root = last_cache / identity["sha256"]
        record["cache_valid_after"] = gfx_frame._translation_cache_valid(
            cache_root, identity)
        record["summary_hit"] = record["summary"]["translation_cache"]["hit"]
        record["summary_publish"] = record["summary"]["translation_cache"]["publish"]
        record["cache_key_matches_computed"] = (
            record["summary"]["translation_cache"]["key"] == identity["sha256"])
        artifact_digest, artifact_count = gfx_frame.artifact_digest(out_dir)
        record["artifact_digest_excluding_metadata"] = artifact_digest
        record["artifact_count"] = artifact_count
        record["shaders_json_sha256"] = sha256_file(out_dir / "shaders.json")
        record["msl_sha256"] = {
            path.name: sha256_file(path)
            for path in sorted(out_dir.glob("*.metal"))}
        warm_runs.append(record)

    def do_plain(rep: int) -> None:
        out_dir = WORK / f"plain-{rep}" / "out"
        record = run_translate(out_dir, None, probe)
        record["kind"] = "plain-no-cache-dir"
        record["rep"] = rep
        plain_runs.append(record)

    for rep in range(1, args.reps + 1):
        if rep % 2 == 1:
            do_cold(rep)
            do_warm(rep)
        else:
            do_warm(rep)
            do_cold(rep)
        do_plain(rep)

    report["translation_runs"] = cold_runs + warm_runs + plain_runs

    # Warm output must be byte-identical to the cold output for the shader
    # artifacts (translation-cache.json legitimately differs by its `hit`).
    cold_out = Path(cold_runs[0]["out"])
    warm_out = Path(warm_runs[0]["out"])
    report["warm_equals_cold"] = {
        "shaders_json": (sha256_file(cold_out / "shaders.json")
                         == sha256_file(warm_out / "shaders.json")),
        "artifacts": core_artifact_digest(cold_out) == core_artifact_digest(warm_out),
        "msl_files": ([path.name for path in sorted(cold_out.glob("*.metal"))]
                      == [path.name for path in sorted(warm_out.glob("*.metal"))]),
    }

    # ------------------------------------------------------------------
    # Hashing cost on authentic inputs.
    # ------------------------------------------------------------------
    cache_root = (Path(cold_runs[0]["cache_dir"]) / identity["sha256"])
    shaders_dir = cold_out
    shader_report = read_json(shaders_dir / "shaders.json")
    report["translation_result"] = {
        "programs": len(shader_report["programs"]),
        "stage_entries": sum(len(program.get("stages") or {})
                             for program in shader_report["programs"]),
        "distinct_compiler_inputs": len(
            {stage.get("compiler_sha256")
             for program in shader_report["programs"]
             for stage in (program.get("stages") or {}).values()}),
        "glsl_files": len(list(shaders_dir.glob("*.glsl"))),
        "spv_files": len(list(shaders_dir.glob("*.spv"))),
        "msl_files": len(list(shaders_dir.glob("*.metal"))),
        "draws": len(draws),
    }
    msl_files = sorted(shaders_dir.glob("*.metal"))
    artifact_files = sorted(
        [path for path in shaders_dir.iterdir()
         if path.is_file() and path.suffix in (".glsl", ".spv", ".metal")])
    translator_bytes = (
        identity["translator"]["bytes"]
        + sum(int(tool.get("bytes") or 0)
              for tool in identity["translator"]["tools"].values()))
    canonical = json.dumps(identity, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    stage_artifact_bytes = sum(path.stat().st_size for path in artifact_files)
    msl_bytes = sum(path.stat().st_size for path in msl_files)
    source_payloads = [path.read_bytes() for path in source_blobs]
    report_hashed_files = [path for path in shaders_dir.rglob("*")
                           if path.is_file()
                           and path.name not in ("ready", "shader-receipt.json")]
    report_hashed_bytes = sum(path.stat().st_size
                              for path in report_hashed_files)

    copy_destination = WORK / "copy-bench"
    if copy_destination.exists():
        shutil.rmtree(copy_destination)

    def bench_copy() -> None:
        if copy_destination.exists():
            shutil.rmtree(copy_destination)
        gfx_frame._copy_translation_cache(cache_root, copy_destination)

    report["hashing"] = {
        "identity_full": {
            **bench(lambda: gfx_frame._shader_source_identity(trace), 7),
            "bytes_hashed": translator_bytes + len(canonical),
            "breakdown": {
                "translator_and_tool_binaries": translator_bytes,
                "program_identity_canonical_json": len(canonical),
            },
        },
        "translator_identity_only": {
            **bench(gfx_frame._translator_identity, 7),
            "bytes_hashed": translator_bytes,
        },
        "warm_cache_validation": {
            **bench(lambda: gfx_frame._translation_cache_valid(
                cache_root, identity), 7),
            "bytes_hashed": stage_artifact_bytes,
            "files_hashed": len(artifact_files),
        },
        "receipt_artifact_digest": {
            **bench(lambda: gfx_frame.artifact_digest(shaders_dir), 7),
            "bytes_hashed": report_hashed_bytes,
            "files_hashed": len(report_hashed_files),
        },
        "shader_source_sha256_read_and_hash": {
            **bench(lambda: [sha256_file(path) for path in source_blobs], 7),
            "bytes_hashed": sum(len(payload) for payload in source_payloads),
            "files_hashed": len(source_blobs),
            "kind": "read+sha256, as translate_trace verifies each blob",
        },
        "shader_source_sha256_hash_only": {
            **bench(lambda: [hashlib.sha256(payload).hexdigest()
                             for payload in source_payloads], 7),
            "bytes_hashed": sum(len(payload) for payload in source_payloads),
            "files_hashed": len(source_payloads),
        },
        "msl_sha256": {
            **bench(lambda: [sha256_file(path) for path in msl_files], 7),
            "bytes_hashed": msl_bytes,
            "files_hashed": len(msl_files),
        },
        "cache_copy": {
            **bench(bench_copy, 5),
            "bytes_copied": cold_runs[0]["cache_stats"]["bytes"],
            "files_copied": cold_runs[0]["cache_stats"]["files"],
        },
        "interpreter_and_import_baseline": {
            **subprocess_baseline(
                [sys.executable, "-c", "from automation import gfx_frame"],
                ROOT, 5),
            "note": "empty Python 3.14 process that imports the module only",
        },
        "trace_load": {
            **bench(lambda: gfx_frame.load_trace(FRAME), 5),
            "bytes_read": (FRAME / "frame-events.jsonl").stat().st_size,
            "files_read": 2,
            "note": "frame-trace.json + frame-events.jsonl parse",
        },
    }

    # ------------------------------------------------------------------
    # Native replay host, one process per run (the host holds no cache
    # across processes).  Full replay, a --stop-after 0 run (process start
    # through frame load, no encode) and a --stop-after 2 run (through the
    # first draw encode) bound the un-instrumented startup portion.
    # ------------------------------------------------------------------
    shaders = warm_out
    host_probe = report["session_probe"]
    for rep in range(1, args.reps + 1):
        if rep % 2 == 1:
            order = [("full", []),
                     ("startup-first-draw", ["--stop-after", "2"]),
                     ("startup-no-encode", ["--stop-after", "0"])]
        else:
            order = [("startup-no-encode", ["--stop-after", "0"]),
                     ("startup-first-draw", ["--stop-after", "2"]),
                     ("full", [])]
        for kind, extra in order:
            run = run_host(f"{kind}-{rep}", WORK / f"host-{kind}-{rep}",
                           shaders, extra, host_probe)
            run["kind"] = kind
            run["rep"] = rep
            report["host_runs"].append(run)

    # Replay report timing fields: prove the absence/presence directly.
    sample_report = read_json(WORK / "host-full-1" / "replay.json")
    report["replay_report_timing_keys"] = timing_keys(sample_report)

    # The supported CLI path (build check + host) for one run.
    supported_out = WORK / "supported-cli"
    command = [sys.executable, "-m", "automation.gfx_frame", "replay",
               "--frame", str(FRAME), "--shaders", str(shaders),
               "--out", str(supported_out)]
    host_probe.append(system_probe("before supported-cli"))
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                               text=True, check=False, timeout=900)
    wall = time.perf_counter() - started
    host_probe.append(system_probe("after supported-cli"))
    report["supported_replay_run"] = {
        "argv": command[1:],
        "returncode": completed.returncode,
        "wall_s": round(wall, 6),
        "stdout": completed.stdout[-2000:],
        "host_build_stamp": read_json(HOST_STAMP) if HOST_STAMP.is_file() else {},
        "host_binary_sha256": sha256_file(HOST),
    }

    env = {key: value for key, value in os.environ.items()
           if key.startswith("EU4_FRAME_REPLAY")}
    report["session_probe"].append(system_probe("end"))
    report["diagnostic_environment"] = env

    out_path = EVID / f"measurements-{args.label}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "label": args.label,
        "measurements": str(out_path),
        "identity": identity["sha256"],
        "cold_wall_s": [run["wall_s"] for run in cold_runs],
        "warm_wall_s": [run["wall_s"] for run in warm_runs],
        "warm_equals_cold": report["warm_equals_cold"],
        "timing_keys_in_replay_json": report["replay_report_timing_keys"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
