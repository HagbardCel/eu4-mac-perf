#!/usr/bin/env python3
"""Build, control, and parse logs for the R0 frame-evolution probe."""

from __future__ import annotations

import ctypes
import json
import mmap
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import eu4_benchmark as base

import eu4_r0_control as r0_control
import r0_capture_provenance as capture_provenance

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = Path(__file__).resolve().parent
SOURCE = BENCHMARK / "eu4_r0_probe.c"
READBACK_SOURCE = BENCHMARK / "eu4_r0_readback.c"
CONTROL_SOURCE = BENCHMARK / "eu4_r0_control.c"
LIBRARY = BENCHMARK / ".build/libeu4_r0_probe.dylib"
HOST_CONTROL_LIBRARY = BENCHMARK / ".build/libeu4_r0_control_host.dylib"
HOST_CONTROL_LIBRARY_X86 = BENCHMARK / ".build/libeu4_r0_control_host_x86_64.dylib"
HOST_ARCH = platform.machine()
CLOCK_ALIGNMENT_TOLERANCE_NS = 50_000_000
CLOCK_ALIGNMENT_MAX_RTT_NS = 5_000_000_000

READBACK_OK = 0
READBACK_UNSUPPORTED_DRAWABLE = 1
READBACK_FRAMEBUFFER_MISMATCH = 2
READBACK_DIMENSION_MISMATCH = 3
READBACK_ALLOCATION_FAILED = 4
READBACK_GL_ERROR = 5
READBACK_UNKNOWN_FAILURE = 6
READBACK_DRAWABLE_STATE_MISMATCH = 7

FAILURE_CODE_NAMES = {
    READBACK_OK: "ok",
    READBACK_UNSUPPORTED_DRAWABLE: "unsupported_drawable",
    READBACK_FRAMEBUFFER_MISMATCH: "framebuffer_mismatch",
    READBACK_DIMENSION_MISMATCH: "dimension_mismatch",
    READBACK_ALLOCATION_FAILED: "allocation_failed",
    READBACK_GL_ERROR: "gl_error",
    READBACK_UNKNOWN_FAILURE: "unknown_failure",
    READBACK_DRAWABLE_STATE_MISMATCH: "drawable_state_mismatch",
}
HARNESS = ROOT / "tests/r0_probe_harness.c"
HARNESS_EXE = BENCHMARK / ".build/r0_probe_harness"
READBACK_HARNESS = BENCHMARK / ".build/r0_readback_harness"
GOG_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

_PROBE_SOURCES = [SOURCE, READBACK_SOURCE, CONTROL_SOURCE]


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            "clang",
            "-arch",
            HOST_ARCH,
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-dynamiclib",
            "-o",
            str(HOST_CONTROL_LIBRARY),
            str(CONTROL_SOURCE),
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-dynamiclib",
            "-o",
            str(HOST_CONTROL_LIBRARY_X86),
            str(CONTROL_SOURCE),
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-dynamiclib",
            "-framework",
            "OpenGL",
            "-o",
            str(LIBRARY),
            *[str(path) for path in _PROBE_SOURCES],
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(BENCHMARK),
            "-framework",
            "OpenGL",
            "-o",
            str(READBACK_HARNESS),
            str(ROOT / "tests/r0_readback_harness.c"),
            str(READBACK_SOURCE),
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-framework",
            "OpenGL",
            "-o",
            str(HARNESS_EXE),
            str(HARNESS),
        ],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"R0 probe build failed: {result.stderr.strip()}")


def _publish_native() -> ctypes._CFuncPtr:
    if not HOST_CONTROL_LIBRARY.is_file():
        build()
    lib = ctypes.CDLL(str(HOST_CONTROL_LIBRARY))
    publish = lib.eu4_r0_control_publish
    publish.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    publish.restype = None
    return publish


def write_control(path: Path, *, generation: int, mode: int, scenario_id: int) -> None:
    if not LIBRARY.is_file():
        build()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)
    publish = _publish_native()
    with path.open("r+b") as handle:
        with mmap.mmap(
            handle.fileno(), r0_control.EU4_R0_CONTROL_PAGE_SIZE, access=mmap.ACCESS_WRITE
        ) as mapping:
            page = (ctypes.c_uint8 * r0_control.EU4_R0_CONTROL_PAGE_SIZE).from_buffer(mapping)
            publish(ctypes.c_void_p(ctypes.addressof(page)), generation, mode, scenario_id)
            del page
            mapping.flush()


def probe_env(control_path: Path, log_path: Path, *, capture_dir: Path | None = None) -> dict[str, str]:
    env = {
        "EU4_R0_CONTROL": str(control_path),
        "EU4_R0_LOG": str(log_path),
        "DYLD_INSERT_LIBRARIES": str(LIBRARY),
    }
    if capture_dir is not None:
        capture_dir.mkdir(parents=True, exist_ok=True)
        env["EU4_R0_CAPTURE_DIR"] = str(capture_dir)
    return env


def event_uptime_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)


def wait_for_ack(log_path: Path, generation: int, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        parsed = parse_log(log_path)
        for ack in parsed["acks"]:
            if ack["generation"] == generation:
                return ack
        time.sleep(0.1)
    raise base.BenchmarkError(f"Timed out waiting for ACK generation={generation}")


def paired_clock_alignment(
    control_path: Path,
    log_path: Path,
    generation: int,
    *,
    timeout_s: float = 10.0,
    tolerance_ns: int = CLOCK_ALIGNMENT_TOLERANCE_NS,
    max_rtt_ns: int = CLOCK_ALIGNMENT_MAX_RTT_NS,
) -> dict[str, Any]:
    t_before = event_uptime_ns()
    write_control(
        control_path,
        generation=generation,
        mode=r0_control.EU4_R0_MODE_PASSIVE,
        scenario_id=0,
    )
    ack = wait_for_ack(log_path, generation, timeout_s=timeout_s)
    t_after = event_uptime_ns()
    t_ack = int(ack["uptime_ns"])
    round_trip_ns = t_after - t_before
    if round_trip_ns > max_rtt_ns:
        return {
            "status": "inconclusive",
            "reason": "excessive_round_trip",
            "round_trip_ns": round_trip_ns,
            "t_before": t_before,
            "t_after": t_after,
            "t_ack": t_ack,
        }
    contained = (t_before - tolerance_ns) <= t_ack <= (t_after + tolerance_ns)
    return {
        "status": "passed" if contained else "failed",
        "round_trip_ns": round_trip_ns,
        "t_before": t_before,
        "t_after": t_after,
        "t_ack": t_ack,
        "tolerance_ns": tolerance_ns,
    }


def load_capture_sidecar(capture_dir: Path, generation: int) -> dict[str, Any] | None:
    return capture_provenance.load_capture_sidecar(capture_dir, generation)


def verify_capture_artifact(
    capture_dir: Path,
    generation: int,
    log_path: Path,
    *,
    events: list[dict[str, Any]] | None = None,
    window: str | None = None,
) -> dict[str, Any]:
    frames = parse_log(log_path)["frames"]
    return capture_provenance.verify_capture_provenance(
        capture_dir, generation, frames, events=events, window=window
    )


def find_frame_for_generation_and_index(
    log_path: Path, generation: int, frame_index: int
) -> dict[str, Any] | None:
    return capture_provenance.find_frame_for_generation_and_index(
        parse_log(log_path)["frames"], generation, frame_index
    )


def find_frame_for_generation(log_path: Path, generation: int) -> dict[str, Any] | None:
    for frame in reversed(parse_log(log_path)["frames"]):
        if frame.get("control_generation") == generation and frame.get("census_attempted"):
            return frame
    return None


def _frame_segment_accepts(frame: dict[str, Any]) -> bool:
    return bool(
        frame.get("census_attempted")
        and frame.get("census_ok")
        and frame.get("pause_verified")
        and int(frame.get("width", 0)) > 0
        and frame.get("flush_ok")
        and int(frame.get("gl_error_observed", 0)) == 0
    )


def _longest_contiguous_segment(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not frames:
        return []
    ordered = sorted(frames, key=lambda item: int(item["frame_index"]))
    best: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    prev_index: int | None = None
    for frame in ordered:
        index = int(frame["frame_index"])
        if prev_index is not None and index != prev_index + 1:
            if len(current) > len(best):
                best = current
            current = []
        current.append(frame)
        prev_index = index
    if len(current) > len(best):
        best = current
    return best


def wait_until_qualified_window(
    log_path: Path,
    generation: int,
    *,
    min_frames: int,
    min_seconds: float,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        accepted = [
            frame
            for frame in parse_log(log_path)["frames"]
            if frame.get("control_generation") == generation and _frame_segment_accepts(frame)
        ]
        segment = _longest_contiguous_segment(accepted)
        if len(segment) >= min_frames and segment[0].get("uptime_ns") and len(segment) >= 2:
            duration = (int(segment[-1]["uptime_ns"]) - int(segment[0]["uptime_ns"])) / 1e9
            if duration >= min_seconds:
                return {
                    "status": "qualified",
                    "generation": generation,
                    "frame_count": len(segment),
                    "duration_seconds": duration,
                    "frame_index_start": int(segment[0]["frame_index"]),
                    "frame_index_end": int(segment[-1]["frame_index"]),
                }
        time.sleep(0.25)
    return {
        "status": "incomplete",
        "reason": "qualified_window_timeout",
        "generation": generation,
        "min_frames": min_frames,
        "min_seconds": min_seconds,
    }


def capture_failure_artifact(capture_dir: Path, generation: int) -> dict[str, Any] | None:
    path = capture_dir / f"capture_{generation}.failed.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"write_ok": False, "artifact_kind": "capture_failure"}


def wait_for_capture_verified(
    capture_dir: Path, log_path: Path, generation: int, *, timeout_s: float = 30.0
) -> dict[str, Any]:
    failure = capture_failure_artifact(capture_dir, generation)
    if failure is not None:
        return {
            "status": "failed",
            "reason": "capture_write_failed",
            "generation": generation,
            "failure": failure,
        }
    sidecar = load_capture_sidecar(capture_dir, generation)
    if sidecar is not None and sidecar.get("write_ok") is False:
        return {
            "status": "failed",
            "reason": "capture_write_failed",
            "generation": generation,
            "failure": sidecar,
        }
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        failure = capture_failure_artifact(capture_dir, generation)
        if failure is not None:
            return {
                "status": "failed",
                "reason": "capture_write_failed",
                "generation": generation,
                "failure": failure,
            }
        result = verify_capture_artifact(capture_dir, generation, log_path)
        if result.get("status") == "verified":
            return result
        time.sleep(0.25)
    return {"status": "failed", "reason": "capture_verify_timeout", "generation": generation}


def probe_production_drawable(
    control_path: Path, log_path: Path, generation: int, *, timeout_s: float = 15.0
) -> dict[str, Any]:
    """One census generation against the real drawable (no EU4_R0_TEST_CENSUS)."""
    write_control(
        control_path,
        generation=generation,
        mode=r0_control.EU4_R0_MODE_CENSUS,
        scenario_id=0,
    )
    wait_for_ack(log_path, generation, timeout_s=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        frame = find_frame_for_generation(log_path, generation)
        if frame:
            code = int(frame.get("readback_failure_code", READBACK_UNKNOWN_FAILURE))
            name = FAILURE_CODE_NAMES.get(code, "unknown_failure")
            return {
                "supported": code == READBACK_OK and bool(frame.get("census_ok")),
                "failure_code": code,
                "failure_class": name,
                "frame": frame,
                "generation": generation,
            }
        time.sleep(0.2)
    return {
        "supported": False,
        "failure_class": "timeout",
        "reason": "no census frame observed",
        "generation": generation,
    }


def flush_single_owner_passes(parsed: dict[str, Any]) -> bool:
    violations = parsed.get("thread_violations") or []
    if violations:
        return False
    frames = parsed.get("frames") or []
    if not frames:
        return False
    thread_ids: list[int] = []
    for frame in frames:
        tid = int(frame.get("pthread_tid") or 0)
        if tid == 0:
            return False
        thread_ids.append(tid)
    return len(set(thread_ids)) == 1


def infer_main_thread_id(frames: list[dict[str, Any]]) -> int | None:
    votes: dict[int, int] = {}
    for frame in frames:
        if frame.get("flush_is_main_thread") and frame.get("pthread_tid"):
            tid = int(frame["pthread_tid"])
            votes[tid] = votes.get(tid, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


def parse_log(path: Path) -> dict[str, Any]:
    acks: list[dict[str, int]] = []
    frames: list[dict[str, Any]] = []
    swaps: list[dict[str, Any]] = []
    thread_violations: list[dict[str, int]] = []
    if not path.is_file():
        return {
            "acks": acks,
            "frames": frames,
            "swaps": swaps,
            "thread_violations": thread_violations,
        }
    for raw in path.read_text(errors="replace").splitlines():
        parts = raw.split(",")
        if not parts:
            continue
        kind = parts[0]
        if kind == "ACK" and len(parts) >= 6:
            acks.append(
                {
                    "generation": int(parts[1]),
                    "frame_index": int(parts[2]),
                    "uptime_ns": int(parts[3]),
                    "mode": int(parts[4]),
                    "scenario_id": int(parts[5]),
                }
            )
        elif kind == "ACK" and len(parts) >= 4:
            acks.append(
                {
                    "generation": int(parts[1]),
                    "frame_index": int(parts[2]),
                    "uptime_ns": int(parts[3]),
                    "mode": 0,
                    "scenario_id": 0,
                }
            )
        elif kind == "F" and len(parts) >= 20:
            frames.append(
                {
                    "frame_index": int(parts[1]),
                    "mode": int(parts[2]),
                    "scenario_id": int(parts[3]),
                    "pause_verified": int(parts[4]),
                    "census_ok": int(parts[5]),
                    "context_id": int(parts[6]),
                    "width": int(parts[7]),
                    "height": int(parts[8]),
                    "memcmp_equal_prev": int(parts[9]),
                    "census_attempted": int(parts[10]),
                    "crc64": int(parts[11]),
                    "flush_is_main_thread": int(parts[12]),
                    "uptime_ns": int(parts[13]),
                    "gl_error_observed": int(parts[14]),
                    "flush_ok": int(parts[15]),
                    "flush_cgl_error": int(parts[16]),
                    "pthread_tid": int(parts[17]),
                    "control_generation": int(parts[18]),
                    "readback_failure_code": int(parts[19]),
                }
            )
        elif kind == "F" and len(parts) >= 18:
            frames.append(
                {
                    "frame_index": int(parts[1]),
                    "mode": int(parts[2]),
                    "scenario_id": int(parts[3]),
                    "pause_verified": int(parts[4]),
                    "census_ok": int(parts[5]),
                    "context_id": int(parts[6]),
                    "width": int(parts[7]),
                    "height": int(parts[8]),
                    "memcmp_equal_prev": int(parts[9]),
                    "census_attempted": int(parts[10]),
                    "crc64": int(parts[11]),
                    "flush_is_main_thread": int(parts[12]),
                    "uptime_ns": int(parts[13]),
                    "gl_error_observed": int(parts[14]),
                    "flush_ok": int(parts[15]),
                    "flush_cgl_error": int(parts[16]),
                    "pthread_tid": int(parts[17]),
                    "control_generation": 0,
                    "readback_failure_code": READBACK_UNKNOWN_FAILURE,
                }
            )
        elif kind == "F" and len(parts) >= 13:
            frames.append(
                {
                    "frame_index": int(parts[1]),
                    "mode": int(parts[2]),
                    "scenario_id": int(parts[3]),
                    "pause_verified": int(parts[4]),
                    "census_ok": int(parts[5]),
                    "context_id": int(parts[6]),
                    "width": int(parts[7]),
                    "height": int(parts[8]),
                    "memcmp_equal_prev": int(parts[9]),
                    "census_attempted": int(parts[10]),
                    "crc64": int(parts[11]),
                    "flush_is_main_thread": int(parts[12]),
                    "uptime_ns": 0,
                    "gl_error_observed": 0,
                    "flush_ok": 1,
                    "flush_cgl_error": 0,
                    "pthread_tid": 0,
                }
            )
        elif kind == "T" and len(parts) >= 4:
            thread_violations.append(
                {
                    "uptime_ns": int(parts[1]),
                    "owner_tid": int(parts[2]),
                    "offender_tid": int(parts[3]),
                }
            )
        elif kind == "S" and len(parts) >= 7:
            swaps.append(
                {
                    "uptime_ns": int(parts[1]),
                    "wall_ns": int(parts[2]),
                    "mode": int(parts[3]),
                    "interval_ns": int(parts[4]),
                    "swaps": int(parts[5]),
                    "paused_swaps": int(parts[6]),
                }
            )
    return {
        "acks": acks,
        "frames": frames,
        "swaps": swaps,
        "thread_violations": thread_violations,
    }


def preflight() -> dict[str, Any]:
    identity = base.gog_identity()
    if identity["executable_sha256"] != GOG_SHA256:
        raise base.BenchmarkError("GOG executable changed; review version-pinned pause offsets")
    build()
    with tempfile.TemporaryDirectory(prefix="eu4-r0-preflight-") as temporary:
        root = Path(temporary)
        control = root / "control.bin"
        log = root / "probe.log"
        write_control(control, generation=1, mode=r0_control.EU4_R0_MODE_CENSUS, scenario_id=0)
        env = os.environ.copy()
        env.update(probe_env(control, log))
        env["EU4_R0_TEST_CENSUS"] = "1"
        result = subprocess.run([str(HARNESS_EXE)], env=env, capture_output=True, text=True, check=False, timeout=30)
        if result.returncode:
            raise base.BenchmarkError(
                f"R0 probe harness failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        parsed = parse_log(log)
        if not parsed["acks"] or not parsed["frames"]:
            raise base.BenchmarkError("R0 preflight log missing ACK or frame records")
        census_ok_frames = [f for f in parsed["frames"] if f.get("census_ok")]
        if not census_ok_frames:
            raise base.BenchmarkError("R0 preflight did not produce census_ok readback")
        if not flush_single_owner_passes(parsed):
            raise base.BenchmarkError("R0 preflight failed flush_single_owner gate")
        return {"status": "ready", "acks": len(parsed["acks"]), "frames": len(parsed["frames"])}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "preflight"])
    args = parser.parse_args()
    if args.command == "build":
        build()
        print(LIBRARY)
    else:
        print(json.dumps(preflight(), indent=2))
