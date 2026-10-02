#!/usr/bin/env python3
"""One-launch causal frame accounting for the pinned GOG EU IV build."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import mmap
import os
import platform
import re
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import autonomous_runner as auto
import eu4_benchmark as base
import eu4_diagnostic as diagnostic
import eu4_engine_inventory as engine
import frame_model_workload as workload
import frame_model_draw_api as draw_api
import frame_model_tier1_policy as tier1
from frame_model_gates import GateEvidence, required_gates_for_report_kind, run_budget, RUN_DEADLINE_SECONDS
from fixture_manager import FixtureManager

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_suffix(".c")
LIBRARY = ROOT / "benchmark/.build/libeu4_frame_model.dylib"
HARNESS_SOURCE = ROOT / "tests/frame_model_harness.c"
HARNESS = ROOT / "benchmark/.build/frame_model_harness"
ARB_HARNESS_SOURCE = ROOT / "tests/frame_model_arb_harness.c"
ARB_HARNESS = ROOT / "benchmark/.build/frame_model_arb_harness"
DETOUR_HARNESS_SOURCE = ROOT / "tests/frame_model_detour_harness.c"
DETOUR_HARNESS = ROOT / "benchmark/.build/frame_model_detour_harness"
RENDER_GATE_HARNESS_SOURCE = ROOT / "tests/frame_model_render_gate_harness.c"
RENDER_GATE_HARNESS = ROOT / "benchmark/.build/frame_model_render_gate_harness"
SCOPE_HARNESS = ROOT / "benchmark/.build/frame_model_scope_harness"
GPU_HARNESS = ROOT / "benchmark/.build/frame_model_gpu_harness"
QUEUE_HARNESS = ROOT / "benchmark/.build/frame_model_queue_harness"
SHADOW_HARNESS = ROOT / "benchmark/.build/frame_model_shadow_harness"
WRITER_HARNESS = ROOT / "benchmark/.build/frame_model_writer_harness"
TEST_LIBRARY=ROOT/"benchmark/.build/libeu4_frame_model_test.dylib"
WORKLOAD_HARNESS=ROOT/"benchmark/.build/frame_model_workload_harness"
WRITER_PRODUCTION_HARNESS=ROOT/"benchmark/.build/frame_model_writer_production_harness"
DRAW_ALIAS_HARNESS=ROOT/"benchmark/.build/frame_model_draw_alias_harness"
ALIAS_INTERPOSE_HARNESS=ROOT/"benchmark/.build/frame_model_alias_interpose_harness"
CONTROL_HARNESS=ROOT/"benchmark/.build/frame_model_control_harness"
POWER_HELPER_SOURCE = ROOT / "benchmark/power_mode_helper"
POWER_HELPER_INSTALLED = Path("/usr/local/libexec/eu4-power-mode")
CONTROL_SIZE = 4096
FORMAT_VERSION = 3
CONTROL = struct.Struct("<6I8Q")
CONTROL_COMMAND = struct.Struct("<6I4Q")
EXPECTED = engine.EXPECTED_SHA256
MODE = {"off": 0, "profile": 1, "skip_render": 2, "drop_draws": 3,
        "raster_suppress": 4, "cadence_30": 5, "cadence_15": 6,"reference":7}
PHASES = (("A0", "profile", 20, False), ("B", "skip_render", 20, False),
          ("A1", "profile", 20, False), ("C", "drop_draws", 20, False),
          ("A2", "profile", 20, False), ("D", "raster_suppress", 20, False),
          ("A3", "profile", 20, False), ("E30", "cadence_30", 30, False),
          ("A4", "profile", 20, False), ("E15", "cadence_15", 30, False),
          ("A5", "profile", 20, False))
FRAME_FIELDS = ("update_id", "render_id", "present_id", "phase", "thread_id",
                "wall_ns", "cpu_ns", "update_wall_ns", "update_cpu_ns", "draws",
                "indices", "triangles", "draw_wall_ns_est", "draw_cpu_ns_est", "draw_timed_samples",
                "buffer_calls", "buffer_bytes", "buffer_storage_bytes", "buffer_upload_bytes",
                "texture_calls", "texture_bytes", "state_calls", "texture_binds",
                "buffer_binds", "program_switches", "uniform_calls", "uniform_bytes",
                "bucket_calls", "append_calls", "idle_wall_ns", "idle_cpu_ns",
                "render_wall_ns", "render_cpu_ns", "map_wall_ns", "map_cpu_ns",
                "present_wall_ns", "present_cpu_ns", "wait_ns", "wait_cpu_ns", "sleep_ns", "flags",
                "forwarded_draws", "suppressed_draws", "render_attempts",
                "render_executed", "present_scene_calls", "present_calls", "measurement_epoch", "start_ns", "end_ns",
                "render_start_ns", "render_end_ns", "present_time_ns")
V2_FRAME_FIELDS = FRAME_FIELDS
FRAME_FIELDS += ("generation", "sample_window", "render_deadline_ns", "render_lateness_ns",
                 "render_missed_deadlines", "render_overruns", "raster_challenges")
LEGACY_FRAME_FIELDS = V2_FRAME_FIELDS[:-6]
HOOK_NAMES = ("UpdateOneFrame", "CInGameIdler::Idle", "CInGameIdler::Render",
              "CEU3GraphicalMap::Render", "CGraphics::PresentScene",
              "CPdxMeshObject::AddToBucket", "CArray<SFlushData>::Append",
              "GL draw", "GL buffer/texture upload", "GL uniform calls",
              "CGLFlushDrawable", "GL state call")
SCOPE_NAMES = ("UpdateOneFrame", "CInGameIdler::Idle", "CInGameIdler::Render",
               "CEU3GraphicalMap::Render", "CGraphics::PresentScene",
               "CPdxMeshObject::AddToBucket", "CArray<SFlushData>::Append",
               "CGLFlushDrawable", "HookedUpdateLoop", "CadenceSleep")
SCOPE_LOOP = SCOPE_NAMES.index("HookedUpdateLoop")


def build() -> dict:
    generated = subprocess.run([sys.executable, str(ROOT / "benchmark/eu4_engine_inventory.py"),
                                "generate"], capture_output=True, text=True)
    if generated.returncode:
        raise base.BenchmarkError(generated.stderr.strip() or generated.stdout.strip())
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = (
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-pthread","-o",str(WRITER_PRODUCTION_HARNESS),str(ROOT/"tests/frame_model_writer_production_harness.c")],
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-o",str(DRAW_ALIAS_HARNESS),str(ROOT/"tests/frame_model_draw_alias_harness.c")],
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-o",str(ALIAS_INTERPOSE_HARNESS),str(ROOT/"tests/frame_model_alias_interpose_harness.c")],
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-o",str(CONTROL_HARNESS),str(ROOT/"tests/frame_model_control_harness.c")],
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-DEU4_FRAME_MODEL_TEST",
         "-dynamiclib","-framework","OpenGL","-framework","CoreGraphics","-o",str(TEST_LIBRARY),str(SOURCE)],
        ["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror","-framework","OpenGL",
         "-o",str(WORKLOAD_HARNESS),str(ROOT/"tests/frame_model_workload_harness.c")],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-dynamiclib", "-framework", "OpenGL", "-framework", "CoreGraphics",
         "-o", str(LIBRARY), str(SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-framework", "OpenGL", "-o", str(HARNESS), str(HARNESS_SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(ARB_HARNESS), str(ARB_HARNESS_SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(DETOUR_HARNESS), str(DETOUR_HARNESS_SOURCE)],
        ["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(RENDER_GATE_HARNESS), str(RENDER_GATE_HARNESS_SOURCE)],
    )
    commands += tuple(["clang","-arch","x86_64","-O2","-Wall","-Wextra","-Werror",
        "-o",str(path),str(ROOT/"tests"/(path.name+".c"))] for path in (SCOPE_HARNESS,GPU_HARNESS,QUEUE_HARNESS,SHADOW_HARNESS,WRITER_HARNESS))
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Build failed: {result.stderr.strip()}")
    for path in (LIBRARY, HARNESS, ARB_HARNESS, DETOUR_HARNESS, RENDER_GATE_HARNESS,SCOPE_HARNESS,GPU_HARNESS):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")
    return {"executable_sha256": base.sha256(base.GOG_EXE),
            "engine_inventory_sha256": base.sha256(engine.OUTPUT),
            "backend_graph_sha256":base.sha256(engine.BACKEND_GRAPH),
            "engine_sites": len(json.loads(engine.OUTPUT.read_text())["hooks"]),
            "library_sha256": base.sha256(LIBRARY),
            "library_source_sha256": base.sha256(SOURCE),
            "native_source_hashes":{p.name:base.sha256(p) for p in (SOURCE,ROOT/"benchmark/eu4_scope_tree.h",ROOT/"benchmark/eu4_gpu_segments.h",ROOT/"benchmark/eu4_spsc.h",ROOT/"benchmark/eu4_raster_policy.h",ROOT/"benchmark/eu4_render_gate.h",ROOT/"benchmark/eu4_detour.h",ROOT/"benchmark/eu4_gl_shadow.h",ROOT/"benchmark/eu4_writer_buffer.h",draw_api.HEADER,ROOT/"benchmark/eu4_draw_api_ids.h",engine.HEADER)},
            "draw_api_manifest":json.loads(draw_api.OUTPUT.read_text()),
            "draw_manifest_sha256": base.sha256(draw_api.OUTPUT),
            "architecture": "x86_64"}


def offline_detour_harness() -> dict:
    run=subprocess.run([str(DETOUR_HARNESS)],capture_output=True,text=True,
                       timeout=10,check=False)
    if run.returncode:
        raise base.BenchmarkError(f"Offline engine detour harness failed ({run.returncode}): {run.stderr.strip()}")
    return {"status":"passed","output":run.stdout.strip(),
            "cases":["trampoline continuation","integer and mixed-register ABI forwarding",
                     "recursive/nested calls","instruction-boundary rejection",
                     "bad expected-byte rejection","restore"]}


def offline_render_gate_harness() -> dict:
    run=subprocess.run([str(RENDER_GATE_HARNESS)],capture_output=True,text=True,
                       timeout=10,check=False)
    if run.returncode:
        raise base.BenchmarkError(f"Offline render-gate harness failed ({run.returncode}): {run.stderr.strip()}")
    return {"status":"passed","output":run.stdout.strip()}


def offline_producer_harnesses() -> dict:
    evidence={}
    for path in (SCOPE_HARNESS,GPU_HARNESS,QUEUE_HARNESS,SHADOW_HARNESS,WRITER_HARNESS):
        run=subprocess.run([str(path)],capture_output=True,text=True,timeout=10,check=False)
        if run.returncode: raise base.BenchmarkError(f"{path.name} failed: {run.stderr}")
        evidence[path.name]={"status":"passed","output":run.stdout.strip()}
        if path==SCOPE_HARNESS:
            rows=list(csv.reader(run.stdout.splitlines()))
            tree=scope_tree_summary(rows,[{"name":"A0"}])["phases"]["1"]
            if tree["tree_reconciliation"]!="passed":
                raise base.BenchmarkError("Production scope serialization failed analyzer reconciliation")
    return evidence


def offline_control_harness():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);path=root/"control";log=root/"trace"
        path.write_bytes(CONTROL.pack(FORMAT_VERSION,2,0,0,0,0,0,0,1,0,0,0,0,0)+bytes(CONTROL_SIZE-CONTROL.size))
        run=subprocess.run([str(CONTROL_HARNESS),str(TEST_LIBRARY)],
            env={**os.environ,"EU4_FRAME_MODEL_CONTROL":str(path),"EU4_FRAME_MODEL_LOG":str(log)},
            capture_output=True,text=True,timeout=10,check=False)
        origins=[r for r in read_rows(log) if r[0]=="O"]
        if run.returncode or len(origins)!=2 or [int(r[1]) for r in origins]!=[7,8] or any(int(r[14]) for r in origins):
            raise base.BenchmarkError(f"Unowned control/origin harness failed: {run.stderr}, {origins}")
        return {"status":"passed","output":run.stdout.strip(),"unknown_origin_records":origins}


def offline_draw_alias_harness():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);path=root/"control";log=root/"trace"
        path.write_bytes(CONTROL.pack(FORMAT_VERSION,2,1,1,3,0,0,0,1,7,0,0,0,0)+bytes(CONTROL_SIZE-CONTROL.size))
        run=subprocess.run([str(DRAW_ALIAS_HARNESS),str(TEST_LIBRARY)],
            env={**os.environ,"EU4_FRAME_MODEL_CONTROL":str(path),"EU4_FRAME_MODEL_LOG":str(log)},
            capture_output=True,text=True,timeout=10,check=False)
        if run.returncode:
            raise base.BenchmarkError(f"Draw alias behavioral harness failed: {run.stderr.strip() or run.stdout.strip()}")
        return {"status":"passed","output":run.stdout.strip()}


def offline_alias_interpose_harness():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);path=root/"control";log=root/"trace"
        path.write_bytes(CONTROL.pack(FORMAT_VERSION,2,1,1,3,0,0,0,1,7,0,0,0,0)+bytes(CONTROL_SIZE-CONTROL.size))
        run=subprocess.run([str(ALIAS_INTERPOSE_HARNESS)],
            env={**os.environ,"DYLD_INSERT_LIBRARIES":str(TEST_LIBRARY),
                 "EU4_FRAME_MODEL_CONTROL":str(path),"EU4_FRAME_MODEL_LOG":str(log)},
            capture_output=True,text=True,timeout=10,check=False)
        if run.returncode:
            raise base.BenchmarkError(
                f"ARB interpose launch harness failed: {run.stderr.strip() or run.stdout.strip()}")
        return {"status":"passed","output":run.stdout.strip()}


def offline_writer_production_harness():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);path=root/"control";log=root/"trace"
        path.write_bytes(CONTROL.pack(FORMAT_VERSION,2,1,1,3,0,0,0,1,7,0,0,0,0)+bytes(CONTROL_SIZE-CONTROL.size))
        run=subprocess.run([str(WRITER_PRODUCTION_HARNESS),str(TEST_LIBRARY)],
            env={**os.environ,"EU4_FRAME_MODEL_CONTROL":str(path),"EU4_FRAME_MODEL_LOG":str(log)},
            capture_output=True,text=True,timeout=10,check=False)
        rows=read_rows(log);frames=decode_frames(rows)
        streams={}
        for r in rows:
            if r[0] not in {"J","F"}: continue
            thread=int(r[16] if r[0]=="J" else r[5])
            streams.setdefault(thread,[]).append((r[0],int(r[1])))
        ordered=all(len(stream)==64 and all(a==("J",b[1]) and b[0]=="F" for a,b in zip(stream[::2],stream[1::2])) for stream in streams.values())
        if run.returncode or len(frames)!=128 or len(streams)!=4 or not ordered or len([r for r in rows if r[0]=="O"])!=4:
            raise base.BenchmarkError(f"Production writer ordering/shutdown failed: {run.stderr}")
        if next((int(r[1]) for r in reversed(rows) if r[0]=="Z"),None)!=0:
            raise base.BenchmarkError("Production writer reported failure")
        return {"status":"passed","output":run.stdout.strip(),"frames":len(frames),"producers":len(streams)}


def offline_arb_harness() -> dict:
    run=subprocess.run([str(ARB_HARNESS),str(LIBRARY.resolve())],
                       capture_output=True,text=True,timeout=10,check=False)
    if run.returncode:
        raise base.BenchmarkError(f"GLhandleARB ABI forwarding harness failed: {run.stderr.strip()}")
    return {"status":"passed","output":run.stdout.strip()}


def offline_harness(library: Path | None = None, mode: int = 1, loops: int = 15000) -> dict:
    if (not LIBRARY.is_file() or not HARNESS.is_file() or not ARB_HARNESS.is_file() or
        not DETOUR_HARNESS.is_file() or not RENDER_GATE_HARNESS.is_file()):
        build()
    with tempfile.TemporaryDirectory(prefix="eu4-frame-model-") as directory:
        root = Path(directory)
        control_path, log_path = root / "control.bin", root / "trace.csv"
        fields = [FORMAT_VERSION,2,mode,1,3,0,0,0,1,1,0,0,0,0]
        control_path.write_bytes(CONTROL.pack(*fields) + bytes(CONTROL_SIZE-CONTROL.size))
        env = os.environ.copy()
        if library:
            env["DYLD_INSERT_LIBRARIES"] = str(library.resolve())
            env["EU4_FRAME_MODEL_CONTROL"] = str(control_path)
            env["EU4_FRAME_MODEL_LOG"] = str(log_path)
        else:
            env.pop("DYLD_INSERT_LIBRARIES", None)
            env.pop("EU4_FRAME_MODEL_CONTROL", None)
            env.pop("EU4_FRAME_MODEL_LOG", None)
        run = subprocess.run([str(HARNESS), str(loops)], env=env, capture_output=True,
                             text=True, timeout=60, check=False)
        if run.returncode:
            if run.returncode==2:
                raise base.BenchmarkError(
                    "Synthetic GL preflight cannot create an accelerated CGL pixel format "
                    "on this host; the ≤3% profiler-overhead gate is unmeasured")
            raise base.BenchmarkError(f"Offline GL harness failed ({run.returncode}): {run.stderr.strip()}")
        rows = read_rows(log_path)
        counters = {int(row[1]): int(row[2]) for row in rows if row[0] == "C"}
        draws = counters.get(7, 0)
        if library and draws == 0:
            raise base.BenchmarkError("GL draw interposition did not observe synthetic draws")
        measured=dict(part.split("=",1) for part in run.stdout.strip().split() if "=" in part)
        return {"elapsed_ns": int(measured["elapsed_ns"]),"cpu_ns":int(measured["cpu_ns"]),
                "draws_observed": draws, "trace_rows": len(rows),
                "hook_failures": next((int(row[1]) for row in rows if row[0] == "Z"), None)}


OFFLINE_EVIDENCE_DIR = ROOT / "analysis/evidence"
OFFLINE_EVIDENCE_POINTER = ROOT / "analysis/frame-model-offline-evidence.json"
OFFLINE_EVIDENCE_POINTER_REL = "analysis/frame-model-offline-evidence.json"
OFFLINE_EVIDENCE_GENERATED_PREFIX = "analysis/evidence/"
OFFLINE_EVIDENCE_SCHEMA_VERSION = "stage2-split-v2"
OFFLINE_EVIDENCE_SCHEMA_VERSION_V1 = "stage2-split-v1"
OFFLINE_SEVEN_PAIR_POLICY_VERSION = "reference_counters_3pct_sampled_5pct_v1"
OFFLINE_ABLATION_POLICY_VERSION = "harness_ablation_vs_sampled_5pct_v1"
OFFLINE_DIAGNOSTIC_POLICY_VERSION = "diag_matrix_diag_counters_baseline_v3"
OFFLINE_DIAGNOSTIC_POLICY_VERSION_INVALID_V2 = "diag_matrix_counters_baseline_v2"
TRAINING_ONLY_VALIDATION_SCOPE = "training_only"
FORENSIC_DETAIL_RECORD_KINDS = ("D", "S", "U", "V", "W", "B", "b", "T", "t")
PROFILER_OVERHEAD_DIAGNOSIS_PURPOSE = "profiler_overhead_diagnosis_v1"
DIAGNOSTIC_MATRIX_COUNTERS_STAGE = "diag_counters"
DIAGNOSTIC_MATRIX_BASELINE = "diag_A"
DIAGNOSTIC_MATRIX_STAGES = (
    "diag_A",
    "diag_B_gpu",
    "diag_C_detail",
    "diag_D_cached_detail",
    "diag_E_full",
    "diag_F_full_cached",
)
DIAGNOSTIC_MATRIX_FEATURES = {
    "diag_A": (0, 0, 0),
    "diag_B_gpu": (1, 0, 0),
    "diag_C_detail": (0, 1, 0),
    "diag_D_cached_detail": (0, 1, 1),
    "diag_E_full": (1, 1, 0),
    "diag_F_full_cached": (1, 1, 1),
}
DIAGNOSTIC_MATRIX_TRIAL_STAGES = (DIAGNOSTIC_MATRIX_COUNTERS_STAGE,) + DIAGNOSTIC_MATRIX_STAGES
CONTROLLER = Path(__file__)
KEY_OFFLINE_SOURCE_PATHS = (
    SOURCE,
    CONTROLLER,
    ROOT / "benchmark/eu4_gpu_segments.h",
    draw_api.HEADER,
    engine.HEADER,
)


def _sha256_optional(path: Path) -> str | None:
    try:
        return base.sha256(path)
    except OSError:
        return None


def _git_commit_metadata() -> dict:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": None, "git_commit_short": None}
    return {"git_commit": commit, "git_commit_short": commit[:7]}


def _git_tree_clean_violations_for_evidence() -> list[str]:
    """Paths that block canonical offline evidence (see ``_git_tree_clean_for_evidence``)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ["<git status unavailable>"]
    violations: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith(OFFLINE_EVIDENCE_GENERATED_PREFIX):
            if status == "??":
                continue
            violations.append(path)
            continue
        if path == OFFLINE_EVIDENCE_POINTER_REL:
            continue
        violations.append(path)
    return violations


def _git_tree_clean_for_evidence() -> bool:
    """Return whether the worktree is clean for canonical offline evidence.

    Untracked files under ``analysis/evidence/`` and updates to the rolling
    pointer ``analysis/frame-model-offline-evidence.json`` are generated outputs
    and are ignored. Modifications or deletions of tracked evidence archives
    under ``analysis/evidence/`` still fail closed.
    """
    return not _git_tree_clean_violations_for_evidence()


def _offline_git_identity_snapshot() -> dict:
    git_meta = _git_commit_metadata()
    return {
        **git_meta,
        "git_tree_clean": _git_tree_clean_for_evidence(),
        "controller_sha256": _sha256_optional(CONTROLLER),
        "key_source_hashes": {
            path.name: digest
            for path in KEY_OFFLINE_SOURCE_PATHS
            if (digest := _sha256_optional(path)) is not None
        },
    }


def _require_git_identity_stable(identity_start: dict, identity_end: dict) -> None:
    if identity_start.get("git_commit") != identity_end.get("git_commit"):
        raise base.BenchmarkError("HEAD moved during offline preflight; refusing immutable evidence")
    if not identity_start.get("git_tree_clean"):
        raise base.BenchmarkError(
            "Source tree dirty before offline preflight (excluding generated evidence outputs)",
        )
    if not identity_end.get("git_tree_clean"):
        raise base.BenchmarkError(
            "Source tree dirty after offline preflight (excluding generated evidence outputs)",
        )
    if identity_start.get("controller_sha256") != identity_end.get("controller_sha256"):
        raise base.BenchmarkError("Controller changed during offline preflight; refusing immutable evidence")
    start_sources = identity_start.get("key_source_hashes") or {}
    end_sources = identity_end.get("key_source_hashes") or {}
    for name, digest in start_sources.items():
        if end_sources.get(name) != digest:
            raise base.BenchmarkError(
                f"Source identity changed during offline preflight ({name}); refusing immutable evidence",
            )


def _offline_executed_artifact_snapshot() -> dict:
    """Hashes of on-disk compiled artifacts used during offline preflight."""
    return {
        "profiler_dylib_sha256": _sha256_optional(LIBRARY),
        "test_library_sha256": _sha256_optional(TEST_LIBRARY),
        "workload_harness_sha256": _sha256_optional(WORKLOAD_HARNESS),
        "draw_manifest_sha256": _sha256_optional(draw_api.OUTPUT),
    }


def _require_build_executed_artifacts_match(build_info: dict, artifact_snapshot: dict) -> None:
    """Ensure build metadata matches the compiled artifacts immediately after build()."""
    for key, build_key in (
        ("profiler_dylib_sha256", "library_sha256"),
        ("draw_manifest_sha256", "draw_manifest_sha256"),
    ):
        expected = build_info.get(build_key)
        actual = artifact_snapshot.get(key)
        if expected and actual != expected:
            raise base.BenchmarkError(
                f"Post-build {key} does not match build metadata; refusing immutable evidence",
            )


def _require_executed_artifacts_stable(artifact_start: dict, artifact_end: dict) -> None:
    for key, start_digest in artifact_start.items():
        end_digest = artifact_end.get(key)
        if start_digest != end_digest:
            raise base.BenchmarkError(
                f"Executed artifact changed during offline preflight ({key}); refusing immutable evidence",
            )


def _offline_git_provenance(identity_start: dict, identity_end: dict) -> dict:
    head_stable = bool(
        identity_start.get("git_commit")
        and identity_start.get("git_commit") == identity_end.get("git_commit"),
    )
    source_clean = bool(
        identity_start.get("git_tree_clean") and identity_end.get("git_tree_clean"),
    )
    verified = head_stable and source_clean
    provenance = {
        "identity_start": identity_start,
        "identity_end": identity_end,
        "git_tree_clean": source_clean,
        "git_provenance_verified": verified,
    }
    if verified:
        provenance["git_commit"] = identity_start["git_commit"]
        provenance["git_commit_short"] = identity_start.get("git_commit_short")
    else:
        provenance["git_commit"] = None
        provenance["git_commit_short"] = None
    return provenance


def _offline_evidence_id(now: dt.datetime | None = None) -> str:
    moment = now or dt.datetime.now(dt.timezone.utc)
    suffix = uuid.uuid4().hex[:8]
    return f"{moment.strftime('%Y%m%dT%H%M%S')}.{moment.microsecond:06d}Z-{suffix}"


def _offline_artifact_hashes(
    representative: dict,
    build_info: dict | None = None,
    *,
    identity_end: dict | None = None,
    executed_artifacts: dict | None = None,
) -> dict:
    build_info = build_info or {}
    native = build_info.get("native_source_hashes") or {}
    executed = executed_artifacts or {}
    artifacts = {
        "profiler_dylib_sha256": executed.get("profiler_dylib_sha256"),
        "test_library_sha256": executed.get("test_library_sha256"),
        "workload_harness_sha256": executed.get("workload_harness_sha256"),
        "harness_source_sha256": representative.get("harness_source_sha256"),
        "profiler_source_sha256": representative.get("profiler_source_sha256"),
        "gpu_segments_source_sha256": representative.get("gpu_segments_source_sha256"),
        "draw_observers_sha256": native.get(draw_api.HEADER.name) or _sha256_optional(draw_api.HEADER),
        "draw_manifest_sha256": executed.get("draw_manifest_sha256"),
        "site_header_sha256": native.get(engine.HEADER.name) or _sha256_optional(engine.HEADER),
        "controller_sha256": (identity_end or {}).get("controller_sha256") or _sha256_optional(CONTROLLER),
        "game_executable_sha256": build_info.get("executable_sha256"),
        "recipe_sha256": {
            entry["recipe"]["name"]: entry["recipe"]["sha256"]
            for entry in (representative.get("recipes") or [])
            if "recipe" in entry
        },
    }
    return artifacts


def _offline_gate_summary(representative: dict) -> list:
    summary = []
    for entry in representative.get("recipes") or []:
        recipe = entry.get("recipe") or {}
        summary.append({
            "recipe": recipe.get("name"),
            "gates": {name: gate.get("status") for name, gate in (entry.get("gates") or {}).items()},
            "ablations": {
                component: {axis: values.get("status") for axis, values in axes.items()}
                for component, axes in (entry.get("ablations") or {}).items()
            },
        })
    return summary


def _offline_admission_pointer_summary(admission: dict | None) -> dict:
    if not admission:
        return {"status": "unavailable"}
    training = {
        item.get("recipe") or "?": item.get("status")
        for item in admission.get("training_recipes") or []
    }
    held = admission.get("held_out_recipe") or {}
    return {
        "status": admission.get("status"),
        "policy_version": admission.get("policy_version"),
        "training": training,
        "held_out_status": held.get("status"),
        "held_out_recipe": held.get("recipe"),
        "held_out_qualification": held.get("qualification"),
        "held_out_reason": held.get("reason"),
    }


def _offline_forensic_pointer_summary(forensic: dict | None) -> dict:
    if not forensic:
        return {"status": "unavailable"}
    recipes = {
        item.get("recipe") or "?": item.get("status")
        for item in forensic.get("recipes") or []
    }
    return {
        "status": forensic.get("status"),
        "policy_version": forensic.get("policy_version"),
        "recipes": recipes,
    }


def _offline_environment_metadata() -> dict:
    meta: dict = {
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "arch": platform.machine(),
        "gog_executable_sha256": _sha256_optional(base.GOG_EXE),
    }
    if sys.platform == "darwin":
        try:
            meta["macos_version"] = platform.mac_ver()[0]
            meta["macos_build"] = subprocess.run(
                ["sw_vers", "-buildVersion"], capture_output=True, text=True, check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            meta.setdefault("macos_version", None)
            meta.setdefault("macos_build", None)
        try:
            meta["chip_model"] = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            meta["chip_model"] = None
    else:
        meta["macos_version"] = None
        meta["macos_build"] = None
        meta["chip_model"] = None
    try:
        meta["clang_version"] = subprocess.run(
            ["clang", "--version"], capture_output=True, text=True, check=True,
        ).stdout.splitlines()[0]
    except (OSError, subprocess.CalledProcessError, IndexError):
        meta["clang_version"] = None
    return meta


def _offline_policy_versions(representative: dict) -> dict:
    first = (representative.get("recipes") or [{}])[0]
    return {
        "seven_pair_gate": OFFLINE_SEVEN_PAIR_POLICY_VERSION,
        "tier1_causal_gate": tier1.TIER1_CAUSAL_POLICY_VERSION,
        "ablation_gate": OFFLINE_ABLATION_POLICY_VERSION,
        "diagnostic_matrix": OFFLINE_DIAGNOSTIC_POLICY_VERSION,
        "ablation_policy_text": first.get("ablation_policy"),
        "diagnostic_policy_text": (first.get("diagnostic_matrix") or {}).get("policy"),
    }


def build_offline_evidence_metadata(
    preflight_evidence: dict,
    evidence_id: str,
    *,
    identity_start: dict,
    identity_end: dict,
    build_info: dict,
    executed_artifact_start: dict,
    executed_artifact_end: dict,
) -> dict:
    """Shared top-level metadata for the immutable archive and rolling pointer."""
    representative = preflight_evidence.get("representative_workloads") or {}
    return {
        "evidence_id": evidence_id,
        "schema_version": OFFLINE_EVIDENCE_SCHEMA_VERSION,
        "recorded_at_utc": evidence_id,
        **_offline_git_provenance(identity_start, identity_end),
        "executed_artifact_start": executed_artifact_start,
        "executed_artifact_end": executed_artifact_end,
        "environment": _offline_environment_metadata(),
        "policy_versions": _offline_policy_versions(representative),
        "artifact_hashes": _offline_artifact_hashes(
            representative,
            build_info,
            identity_end=identity_end,
            executed_artifacts=executed_artifact_start,
        ),
    }


def write_offline_evidence_archive(
    preflight_evidence: dict,
    metadata: dict,
) -> tuple[Path, str, bytes]:
    """Write immutable offline preflight evidence; return path, evidence ID, and exact bytes."""
    run_id = metadata["evidence_id"]
    short = metadata.get("git_commit_short") or "unknown"
    OFFLINE_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    archive = OFFLINE_EVIDENCE_DIR / f"frame-model-offline-{short}-{run_id}.json"
    record = {**metadata, "preflight": preflight_evidence}
    body = (json.dumps(record, indent=2) + "\n").encode("utf-8")
    try:
        with archive.open("xb") as handle:
            handle.write(body)
    except FileExistsError as error:
        raise base.BenchmarkError(f"Refusing to overwrite immutable evidence archive: {archive}") from error
    return archive, run_id, body


def write_offline_evidence_pointer(
    preflight_evidence: dict,
    metadata: dict,
    archive_path: Path,
    archive_body: bytes,
) -> None:
    representative = preflight_evidence.get("representative_workloads") or {}
    recipes = representative.get("recipes") or []
    training = [entry for entry in recipes if (entry.get("recipe") or {}).get("role") != "held_out"]
    held = next((entry for entry in recipes if (entry.get("recipe") or {}).get("role") == "held_out"), None)
    admission_summary = tier1.summarize_admission(training, held, require_held_out=True)
    forensic_summary = tier1.summarize_forensic(training)
    recorded_policy = (metadata.get("policy_versions") or {}).get("tier1_causal_gate")
    pointer = {
        **metadata,
        "schema_version": OFFLINE_EVIDENCE_SCHEMA_VERSION,
        "archive_recorded_under": {
            "schema_version": metadata.get("schema_version"),
            "tier1_causal_policy": recorded_policy,
        },
        "current_replay_under": {
            "tier1_causal_policy": tier1.TIER1_CAUSAL_POLICY_VERSION,
        },
        "archive_path": str(archive_path.relative_to(ROOT)),
        "archive_sha256": hashlib.sha256(archive_body).hexdigest(),
        "status": preflight_evidence.get("status"),
        "overhead_gate": preflight_evidence.get("overhead_gate"),
        "offline_causal_admission": _offline_admission_pointer_summary(admission_summary),
        "offline_forensic_suitability": _offline_forensic_pointer_summary(forensic_summary),
        "representative_workloads": {
            "status": representative.get("status"),
            "gate_summary": _offline_gate_summary(representative),
        },
        "limitations": preflight_evidence.get("limitations"),
    }
    OFFLINE_EVIDENCE_POINTER.write_text(json.dumps(pointer, indent=2) + "\n")


def _publish_offline_immutable_evidence(
    evidence: dict,
    *,
    identity_start: dict,
    identity_end: dict,
    build_info: dict,
    artifact_start: dict,
    artifact_end: dict,
    update_rolling_pointer: bool = True,
) -> dict:
    evidence_id = _offline_evidence_id()
    metadata = build_offline_evidence_metadata(
        evidence,
        evidence_id,
        identity_start=identity_start,
        identity_end=identity_end,
        build_info=build_info,
        executed_artifact_start=artifact_start,
        executed_artifact_end=artifact_end,
    )
    archive_path, evidence_id, archive_body = write_offline_evidence_archive(evidence, metadata)
    archive_sha256 = hashlib.sha256(archive_body).hexdigest()
    evidence["immutable_evidence"] = {
        "evidence_id": evidence_id,
        "archive_path": str(archive_path.relative_to(ROOT)),
        "archive_sha256": archive_sha256,
    }
    if update_rolling_pointer:
        write_offline_evidence_pointer(evidence, metadata, archive_path, archive_body)
    return evidence


def profiler_overhead_diagnosis(run_gl: bool = True) -> dict:
    """Capture training-only offline workloads + A–F matrix; never blocks on causal failure."""
    if not run_gl:
        raise base.BenchmarkError("profiler-overhead-diagnosis requires GL workload execution")
    identity_start = _offline_git_identity_snapshot()
    if not identity_start.get("git_tree_clean"):
        violations = _git_tree_clean_violations_for_evidence()
        detail = ", ".join(violations[:8])
        if len(violations) > 8:
            detail += f", … (+{len(violations) - 8} more)"
        raise base.BenchmarkError(
            "Source tree dirty before profiler overhead diagnosis (excluding generated evidence outputs)"
            + (f": {detail}" if detail else ""),
        )
    static = build()
    artifact_start = _offline_executed_artifact_snapshot()
    _require_build_executed_artifacts_match(static, artifact_start)
    representative = offline_workloads(
        executed_artifacts_start=artifact_start,
        include_held_out=False,
    )
    artifact_end = _offline_executed_artifact_snapshot()
    _require_executed_artifacts_stable(artifact_start, artifact_end)
    identity_end = _offline_git_identity_snapshot()
    _require_git_identity_stable(identity_start, identity_end)
    causal_admission = representative["offline_causal_admission"]
    forensic_suitability = representative["offline_forensic_suitability"]
    evidence = {
        "purpose": PROFILER_OVERHEAD_DIAGNOSIS_PURPOSE,
        "validation_scope": TRAINING_ONLY_VALIDATION_SCOPE,
        "status": "diagnostic_complete",
        "build": static,
        "representative_workloads": representative,
        "offline_causal_admission": causal_admission,
        "offline_forensic_suitability": forensic_suitability,
        "overhead_gate": causal_admission["status"],
        "diagnostic_matrix_policy": OFFLINE_DIAGNOSTIC_POLICY_VERSION,
        "limitations": [
            "Training recipes only; held-out not measured.",
            "Diagnostic matrix comparisons are non-acceptance; causal gates may still fail.",
            "Register archive path in analysis/profiler-overhead-diagnosis-manifest.json.",
        ],
    }
    return _publish_offline_immutable_evidence(
        evidence,
        identity_start=identity_start,
        identity_end=identity_end,
        build_info=static,
        artifact_start=artifact_start,
        artifact_end=artifact_end,
        update_rolling_pointer=False,
    )


def preflight(run_gl: bool = True, require_privilege: bool = False, require_power_mode: bool=True) -> dict:
    if base.sha256(base.GOG_EXE) != EXPECTED:
        raise base.BenchmarkError("Installed GOG executable differs from the pinned v1.37.5 build")
    identity_start: dict | None = None
    artifact_start: dict | None = None
    if run_gl:
        identity_start = _offline_git_identity_snapshot()
        if not identity_start.get("git_tree_clean"):
            violations = _git_tree_clean_violations_for_evidence()
            detail = ", ".join(violations[:8])
            if len(violations) > 8:
                detail += f", … (+{len(violations) - 8} more)"
            raise base.BenchmarkError(
                "Source tree dirty before offline preflight (excluding generated evidence outputs)"
                + (f": {detail}" if detail else ""),
            )
    static = build()
    if run_gl:
        artifact_start = _offline_executed_artifact_snapshot()
        _require_build_executed_artifacts_match(static, artifact_start)
    detour = offline_detour_harness()
    render_gate=offline_render_gate_harness()
    arb=offline_arb_harness()
    producers=offline_producer_harnesses()
    producers["unowned_control"]=offline_control_harness()
    producers["production_writer"]=offline_writer_production_harness()
    producers["draw_alias"]=offline_draw_alias_harness()
    producers["alias_interpose"]=offline_alias_interpose_harness()
    auto_evidence = auto.preflight(require_privilege=require_privilege)
    power_helper = _power_helper_preflight() if require_privilege and require_power_mode else {"status":"not checked"}
    if not run_gl:
        return {"status": "static_ready", "build": static, "autonomous": auto_evidence,
                "detour_harness":detour,
                "render_gate_harness":render_gate,
                "arb_handle_harness":arb,"producer_harnesses":producers,
                "power_mode_helper":power_helper,
                "overhead_gate": "not measured", "live_hooks": "not installed in EU IV"}
    diagnostic_samples={}
    for name,target in (("bare",None),("counters",LIBRARY)):
        diagnostic_samples[name]=[offline_harness(target,loops=12000) for _ in range(3)]
    representative=offline_workloads(executed_artifacts_start=artifact_start)
    artifact_end = _offline_executed_artifact_snapshot()
    _require_executed_artifacts_stable(artifact_start, artifact_end)
    identity_end = _offline_git_identity_snapshot()
    _require_git_identity_stable(identity_start, identity_end)
    causal_admission = representative["offline_causal_admission"]
    forensic_suitability = representative["offline_forensic_suitability"]
    evidence={"status":"ready" if causal_admission["status"]=="passed" else "blocked", "build":static,
        "autonomous":auto_evidence,"detour_harness":detour,"render_gate_harness":render_gate,
        "arb_handle_harness":arb,"producer_harnesses":producers,"power_mode_helper":power_helper,
        "tiny_call_diagnostic":diagnostic_samples,"representative_workloads":representative,
        "offline_causal_admission":causal_admission,
        "offline_forensic_suitability":forensic_suitability,
        "overhead_gate":causal_admission["status"],
        "limitations":["Structural recipes reproduce observed draw counts and state-change frequencies with generated shaders/resources. Live calibration is independently mandatory.",
            "GPU evidence remains segmented and may be partial."]}
    evidence = _publish_offline_immutable_evidence(
        evidence,
        identity_start=identity_start,
        identity_end=identity_end,
        build_info=static,
        artifact_start=artifact_start,
        artifact_end=artifact_end,
    )
    archive_path = ROOT / evidence["immutable_evidence"]["archive_path"]
    if causal_admission["status"] != "passed":
        raise base.BenchmarkError(
            f"Offline causal admission failed; immutable evidence: {archive_path}")
    return evidence


def _diagnostic_matrix_paired_summary(
    diagnostic_trials: list[dict],
    instrumented_stage: str,
    reference_stage: str,
    *,
    frames: int,
) -> dict[str, dict]:
    summary = {}
    for axis in ("elapsed_ns", "cpu_ns"):
        comparison = workload.paired_summary(
            [
                {
                    "instrumented": trial["stages"][instrumented_stage][axis],
                    "reference": trial["stages"][reference_stage][axis],
                }
                for trial in diagnostic_trials
            ],
            1.0,
            frames,
        )
        comparison.pop("limit", None)
        comparison["status"] = "diagnostic"
        comparison["acceptance_gate"] = False
        summary[axis] = comparison
    return summary


def _validate_diagnostic_stage_features(
    stage: str,
    features: tuple[int, int, int],
    gpu_metrics: list[int],
    record_total: int,
    record_counts: dict[str, int],
) -> None:
    if features[0] and gpu_metrics[0] <= 0:
        raise base.BenchmarkError(
            f"{stage} enabled GPU timestamps but issued none (gpu_stamps={gpu_metrics[0]})",
        )
    if features[1]:
        detail_total = sum(record_counts.get(kind, 0) for kind in FORENSIC_DETAIL_RECORD_KINDS)
        if detail_total <= 0 and record_total <= 0:
            raise base.BenchmarkError(
                f"{stage} enabled forensic records but emitted none",
            )


def offline_workload_log_path(root: Path, recipe: dict, trial: int, stage: str) -> Path:
    return root / f"{recipe['name']}-{trial}-{stage}.csv"


def offline_workload_telemetry_paths_for_recipe_trial(recipe_name: str, trial: int) -> list[str]:
    """All telemetry CSV basenames for one recipe trial (causal block + diagnostic matrix)."""
    forensic_stages = ("sampled", "ablation_accounting", "ablation_writer", "ablation_preparation")
    causal = ("bare", "reference", "counters") + forensic_stages
    diagnostic = DIAGNOSTIC_MATRIX_TRIAL_STAGES
    paths = [f"{recipe_name}-{trial}-{stage}.csv" for stage in causal if stage != "bare"]
    paths.extend(f"{recipe_name}-{trial}-{stage}.csv" for stage in diagnostic)
    return paths


def _offline_workload_stage(root: Path, recipe: dict, trial: int, stage: str):
    control_path=root/"control.bin"
    log_path=offline_workload_log_path(root, recipe, trial, stage)
    if log_path.exists():
        raise base.BenchmarkError(
            f"Refusing to reuse telemetry path {log_path.name} (stale or colliding stage identity)",
        )
    frames=4
    mode=MODE["reference"] if stage=="reference" else MODE["profile"]
    fields=[FORMAT_VERSION,2,mode,1,1,0,0,0,1,0,0,0,0,0]
    control_path.write_bytes(CONTROL.pack(*fields)+bytes(CONTROL_SIZE-CONTROL.size))
    private=("DYLD_INSERT_LIBRARIES","EU4_FRAME_MODEL_CONTROL","EU4_FRAME_MODEL_LOG",
        "EU4_TEST_ABLATION","EU4_TEST_SAMPLED","EU4_TEST_GPU_TIMESTAMPS",
        "EU4_TEST_FORENSIC_RECORDS","EU4_TEST_CACHED_METADATA")
    env={k:v for k,v in os.environ.items() if k not in private}
    if stage.startswith("ablation_"):
        env["EU4_TEST_ABLATION"]=stage.removeprefix("ablation_")
    if stage!="bare":
        env.update(DYLD_INSERT_LIBRARIES=str(TEST_LIBRARY),
            EU4_FRAME_MODEL_CONTROL=str(control_path),EU4_FRAME_MODEL_LOG=str(log_path))
        sampled_stage = stage == "sampled" or stage.startswith("ablation_") or (
            stage.startswith("diag_") and stage != DIAGNOSTIC_MATRIX_COUNTERS_STAGE
        )
        if sampled_stage:
            env["EU4_TEST_SAMPLED"]="1"
    diagnostic=stage.startswith("diag_") and stage != DIAGNOSTIC_MATRIX_COUNTERS_STAGE
    if diagnostic:
        features = DIAGNOSTIC_MATRIX_FEATURES[stage]
        env.update(EU4_TEST_GPU_TIMESTAMPS=str(features[0]),
            EU4_TEST_FORENSIC_RECORDS=str(features[1]),EU4_TEST_CACHED_METADATA=str(features[2]))
    run=subprocess.run([str(WORKLOAD_HARNESS),recipe["path"],str(frames)],env=env,
        capture_output=True,text=True,timeout=60,check=False)
    if run.returncode:
        raise base.BenchmarkError(f"Valid workload {recipe['name']} {stage} failed ({run.returncode}): {run.stderr}")
    measured={k:int(v) for k,v in (p.split("=",1) for p in run.stdout.split() if "=" in p)}
    if stage=="bare": return measured,None
    rows=frame_rows(log_path);trace=read_rows(log_path)
    tree=scope_tree_summary(trace,[{"name":"A0"}])["phases"]["1"]
    failure=next((int(r[1]) for r in trace if r[0]=="Z"),None)
    dropped=next((int(r[3]) for r in trace if r[0]=="Z" and len(r)>3),0)
    if len(rows)!=frames or failure!=0 or dropped or tree["tree_reconciliation"]!="passed" or any(r["flags"]&(1|512|1024|2048|4096) for r in rows):
        raise base.BenchmarkError("Representative workload did not exercise valid production wrappers, scopes, and writer")
    if stage!="reference" and sum(r["draws"] for r in rows)!=frames*recipe["draws"]:
        raise base.BenchmarkError("Representative producer draw counts differ from passive recipe")
    if stage!="reference":
        counts={int(r[1]):tuple(map(int,r[2:5])) for r in trace if r[0]=="C"}
        for hook,field in ((7,"draws"),(9,"uniform_calls"),(11,"state_calls")):
            if counts[hook][0]!=sum(f[field] for f in rows):
                raise base.BenchmarkError(f"Accounting totals differ for {field}")
        if counts[8][0]!=sum(f["buffer_calls"]+f["texture_calls"] for f in rows):
            raise base.BenchmarkError("Upload totals differ")
        if counts[7][1:]!=tuple(sum(f[field] for f in rows)//256 for field in ("draw_wall_ns_est","draw_cpu_ns_est")):
            raise base.BenchmarkError("Sampled draw timing totals differ")
    measured.update(frames=len(rows),tree_reconciliation=tree["tree_reconciliation"])
    if diagnostic:
        gpu_metrics=next((list(map(int,row[1:4])) for row in trace if row and row[0]=="M"),[0,0,0])
        record_total=next((int(row[1]) for row in trace if row and row[0]=="N"),0)
        measured["features"]={"gpu_timestamps":bool(features[0]),"forensic_records":bool(features[1]),
            "cached_metadata":bool(features[2]),"sampled_draw_clocks":True}
        measured["operations"]={"gpu_stamps":gpu_metrics[0],"gpu_availability_polls":gpu_metrics[1],
            "gpu_results_read":gpu_metrics[2],"detail_records":record_total,
            "hook_failures":failure,"dropped_records":dropped}
        measured["record_counts"]={kind:sum(1 for row in trace if row and row[0]==kind)
            for kind in ("D","S","U","V","W","B","b","T","t","G")}
        if not features[0] and any(gpu_metrics):
            raise base.BenchmarkError(f"{stage} issued GPU timestamp/poll/result calls while disabled")
        if not features[1] and any(measured["record_counts"][kind]
            for kind in FORENSIC_DETAIL_RECORD_KINDS):
            raise base.BenchmarkError(f"{stage} emitted disabled forensic record families")
        _validate_diagnostic_stage_features(
            stage,
            features,
            gpu_metrics,
            record_total,
            measured["record_counts"],
        )
    return measured,trace


def _offline_recipe_evidence(root: Path, recipe: dict, *, frames: int = 4, include_forensic: bool = True) -> dict:
    causal_stages = ("bare", "reference", "counters")
    forensic_stages = ("sampled", "ablation_accounting", "ablation_writer", "ablation_preparation")
    base_stages = causal_stages + forensic_stages if include_forensic else causal_stages
    diagnostic_stages = DIAGNOSTIC_MATRIX_STAGES
    trials = []
    for trial in range(7):
        stages = tuple(reversed(base_stages)) if trial % 2 else base_stages
        values = {}
        for stage in stages:
            values[stage], _ = _offline_workload_stage(root, recipe, trial, stage)
        trials.append({"trial": trial, "order": list(stages), "stages": values})
    gates = {}
    for stage, reference, limit in (
        ("reference", "bare", 0.03),
        ("counters", "reference", 0.03),
        ("sampled", "reference", 0.05),
    ):
        if stage == "sampled" and not include_forensic:
            continue
        for axis in ("elapsed_ns", "cpu_ns"):
            gates[f"{stage}_{axis}"] = workload.paired_summary(
                [
                    {
                        "instrumented": trial["stages"][stage][axis],
                        "reference": trial["stages"][reference][axis],
                    }
                    for trial in trials
                ],
                limit,
                frames,
            )
    ablations = {}
    if include_forensic:
        for component in ("accounting", "writer", "preparation"):
            ablations[component] = {
                axis: workload.paired_summary(
                    [
                        {
                            "instrumented": trial["stages"][f"ablation_{component}"][axis],
                            "reference": trial["stages"]["sampled"][axis],
                        }
                        for trial in trials
                    ],
                    0.05,
                    frames,
                )
                for axis in ("elapsed_ns", "cpu_ns")
            }
    diagnostic_trials = []
    diagnostic_matrix = None
    if include_forensic:
        trial_stages = DIAGNOSTIC_MATRIX_TRIAL_STAGES
        for trial in range(7):
            stages = tuple(reversed(trial_stages)) if trial % 2 else trial_stages
            values = {}
            for stage in stages:
                values[stage], _ = _offline_workload_stage(root, recipe, trial, stage)
            diagnostic_trials.append({"trial": trial, "order": list(stages), "stages": values})
        vs_diag_a = {
            stage: _diagnostic_matrix_paired_summary(
                diagnostic_trials,
                stage,
                DIAGNOSTIC_MATRIX_BASELINE,
                frames=frames,
            )
            for stage in diagnostic_stages[1:]
        }
        vs_counters = {
            stage: _diagnostic_matrix_paired_summary(
                diagnostic_trials,
                stage,
                DIAGNOSTIC_MATRIX_COUNTERS_STAGE,
                frames=frames,
            )
            for stage in diagnostic_stages
        }
        vs_diag_counters = _diagnostic_matrix_paired_summary(
            diagnostic_trials,
            DIAGNOSTIC_MATRIX_BASELINE,
            DIAGNOSTIC_MATRIX_COUNTERS_STAGE,
            frames=frames,
        )
        diagnostic_matrix = {
            "policy_version": OFFLINE_DIAGNOSTIC_POLICY_VERSION,
            "trial_stages": list(trial_stages),
            "baseline_counters": DIAGNOSTIC_MATRIX_COUNTERS_STAGE,
            "baseline_sampled_minimal": DIAGNOSTIC_MATRIX_BASELINE,
            "baseline": DIAGNOSTIC_MATRIX_BASELINE,
            "vs_diag_A": vs_diag_a,
            "vs_counters": vs_counters,
            "diag_A_vs_diag_counters": vs_diag_counters,
            "comparisons": vs_diag_a,
            "trials": diagnostic_trials,
            "policy": (
                "Interleaved diag_counters baseline per trial (distinct telemetry path from Tier-1 counters); "
                "paired A–F vs diag_counters and vs diag_A. Non-acceptance diagnostics only."
            ),
        }
    recipe_meta = {key: value for key, value in recipe.items() if key != "path"}
    entry = {
        "ablations": ablations,
        "ablation_policy": (
            "Harness-only restoration of per-call accounting, unbatched writing, or measured "
            "state/query preparation; diagnostic, excluded from acceptance gates."
        ),
        "diagnostic_matrix": diagnostic_matrix,
        "recipe": recipe_meta,
        "trials": trials,
        "gates": gates,
    }
    return entry


def offline_workloads(
    *,
    executed_artifacts_start: dict | None = None,
    include_held_out: bool | None = None,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="eu4-representative-") as temporary:
        root = Path(temporary)
        if include_held_out is None:
            include_held_out = workload.held_out_fixture_ready()
        recipe_specs = workload.recipes(root, include_held_out=include_held_out)
        evidence = [
            _offline_recipe_evidence(
                root,
                recipe,
                include_forensic=recipe.get("role") != "held_out",
            )
            for recipe in recipe_specs
        ]
        training = [entry for entry in evidence if entry["recipe"].get("role") != "held_out"]
        held_out = next((entry for entry in evidence if entry["recipe"].get("role") == "held_out"), None)
        offline_causal_admission = tier1.summarize_admission(
            training,
            held_out,
            require_held_out=include_held_out,
        )
        if not include_held_out:
            offline_causal_admission = {
                **offline_causal_admission,
                "validation_scope": TRAINING_ONLY_VALIDATION_SCOPE,
            }
        offline_forensic_suitability = tier1.summarize_forensic(training)
        if executed_artifacts_start:
            test_library_sha256 = executed_artifacts_start["test_library_sha256"]
            workload_harness_sha256 = executed_artifacts_start["workload_harness_sha256"]
        else:
            test_library_sha256 = base.sha256(TEST_LIBRARY)
            workload_harness_sha256 = base.sha256(WORKLOAD_HARNESS)
        legacy_combined = (
            "passed"
            if all(gate["status"] == "passed" for entry in evidence for gate in entry["gates"].values())
            else "failed"
        )
        payload = {
            "status": legacy_combined,
            "offline_causal_admission": offline_causal_admission,
            "offline_forensic_suitability": offline_forensic_suitability,
            "recipes": evidence,
            "test_library_sha256": test_library_sha256,
            "workload_harness_sha256": workload_harness_sha256,
            "harness_source_sha256": base.sha256(ROOT / "tests/frame_model_workload_harness.c"),
            "profiler_source_sha256": base.sha256(SOURCE),
            "gpu_segments_source_sha256": base.sha256(ROOT / "benchmark/eu4_gpu_segments.h"),
        }
        if not include_held_out:
            payload["validation_scope"] = TRAINING_ONLY_VALIDATION_SCOPE
        return payload


def read_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    with path.open(newline="") as source:
        return [row for row in csv.reader(source) if row]


def decode_frames(rows):
    result = []
    layouts = {len(fields)+1: fields for fields in (FRAME_FIELDS, V2_FRAME_FIELDS, LEGACY_FRAME_FIELDS)}
    for row in rows:
        if not row or row[0] != "F" or len(row) not in layouts:
            continue
        try:
            result.append(dict(zip(layouts[len(row)], map(int, row[1:]))))
        except ValueError:
            continue
    return result


def frame_rows(path: Path) -> list[dict]:
    return decode_frames(read_rows(path))


class TelemetryReader:
    """Read each serialized byte once; retain an unfinished line until publication."""
    def __init__(self, path):
        self.path, self.offset, self.pending = path, 0, b""

    def poll(self):
        if not self.path.is_file(): return []
        with self.path.open("rb") as source:
            source.seek(self.offset)
            data = source.read()
            self.offset = source.tell()
        lines = (self.pending + data).split(b"\n")
        self.pending = lines.pop()
        return decode_frames(csv.reader(line.decode("ascii") for line in lines))


def eligible_frame(row: dict, window: dict | None) -> bool:
    if window is None or "measurement_epoch" not in window:
        return not (row.get("flags",0)&(1|512|1024))
    offset=window.get("clock_offset_ns",0)
    uncertainty=window.get("alignment_uncertainty_ns",0)
    return (row.get("measurement_epoch")==window["measurement_epoch"] and
            row["phase"]==PHASE_NUMBER[window["name"]] and
            not row.get("flags",0)&(1|512|1024|4096) and
            row.get("start_ns",0)+offset>=window["start_ns"]+uncertainty and
            row.get("end_ns",0)+offset<=window["end_ns"]-uncertainty)


def eligible_trace(trace: list[list[str]], frames: list[dict], windows: list[dict]) -> list[list[str]]:
    if not any("measurement_epoch" in w for w in windows): return trace
    eligible={(r["measurement_epoch"],r["update_id"]) for w in windows for r in frames
              if eligible_frame(r,w)}
    incomplete={(r.get("measurement_epoch"),r["update_id"]) for r in frames if r.get("flags",0)&2048}
    result=[]
    for row in trace:
        try:
            if row[0]=="Q": key=(int(row[10]),int(row[1])) if len(row) in (13,16) else None
            elif row[0]=="G": key=(int(row[8]),int(row[9])) if len(row)>=13 else None
            elif row[0] in {"D","S","U","u","V","W","B","b","T","t"}:
                key=(int(row[13]),int(row[14])) if len(row) in (16,20) else None
            else: result.append(row); continue
            if key in eligible and not (row[0] in {"D","S","U","u","V","W","B","b","T","t"} and key in incomplete): result.append(row)
        except (ValueError,IndexError): continue
    return result


def _distribution(values):
    if not values: return {"count":0,"median":None,"p95":None,"max":None}
    values=sorted(values)
    return {"count":len(values),"median":statistics.median(values),
            "p95":values[min(len(values)-1,int(.95*len(values)))],"max":values[-1]}


def unassociated_origins(trace, windows):
    result=[]
    for row in trace:
        if not row or row[0]!="O" or len(row)<6: continue
        try:
            epoch,thread,phase,timestamp,context=map(int,row[1:6])
            for window in windows:
                if (epoch==window.get("measurement_epoch") and phase==PHASE_NUMBER[window["name"]] and
                    window["start_ns"]<=timestamp+window.get("clock_offset_ns",0)<window["end_ns"]):
                    result.append({"measurement_epoch":epoch,"thread_id":thread,"phase":window["name"],
                        "timestamp_ns":timestamp,"context":context,"origin":"unassociated; no guessed frame"})
                    break
        except (ValueError,KeyError): continue
    return result


def phase_summary(rows: list[dict], name: str, seconds: float, window: dict | None=None, events_rows: list[list[str]] | None=None) -> dict:
    selected = [row for row in rows if row["phase"] == PHASE_NUMBER[name] and eligible_frame(row,window)]
    if not selected:
        return {"phase": name, "status": "missing", "updates": 0}
    def median(field: str) -> float:
        return statistics.median(row[field] for row in selected)
    observed_seconds=(seconds if seconds>0 else 1.0)
    result = {"phase": name, "status": "complete", "updates": len(selected),
              "update_attempts_s": len(selected)/observed_seconds,
              "render_attempts_s": sum(row["render_attempts"] for row in selected)/observed_seconds,
              "executed_renders_s": sum(row["render_executed"] for row in selected)/observed_seconds,
              "present_calls_s": sum(row["present_calls"] for row in selected)/observed_seconds,
              "median_loop_wall_ms": median("wall_ns")/1e6,
              "median_loop_cpu_ms": median("cpu_ns")/1e6,
              "median_update_wall_ms": median("update_wall_ns")/1e6,
              "median_update_cpu_ms": median("update_cpu_ns")/1e6,
              "median_idle_wall_ms": median("idle_wall_ns")/1e6,
              "median_idle_cpu_ms": median("idle_cpu_ns")/1e6,
              "median_render_wall_ms": median("render_wall_ns")/1e6,
              "median_render_cpu_ms": median("render_cpu_ns")/1e6,
              "mean_render_wall_ms_per_executed_render":(
                  sum(row["render_wall_ns"] for row in selected)/
                  sum(row["render_executed"] for row in selected)/1e6
                  if sum(row["render_executed"] for row in selected) else None),
              "mean_render_cpu_ms_per_executed_render":(
                  sum(row["render_cpu_ns"] for row in selected)/
                  sum(row["render_executed"] for row in selected)/1e6
                  if sum(row["render_executed"] for row in selected) else None),
              "median_present_wall_ms": median("present_wall_ns")/1e6,
              "median_present_cpu_ms": median("present_cpu_ns")/1e6,
              "median_cglflush_wall_ms": median("wait_ns")/1e6,
              "median_cglflush_cpu_ms": median("wait_cpu_ns")/1e6,
              "median_cadence_sleep_ms": median("sleep_ns")/1e6,
              "median_draws": median("draws"), "median_indices": median("indices"),
              "median_triangles_est": median("triangles"),
              "median_draw_submission_wall_ms_est": median("draw_wall_ns_est")/1e6,
              "median_draw_submission_cpu_ms_est": median("draw_cpu_ns_est")/1e6,
              "median_buffer_calls": median("buffer_calls"),
              "median_buffer_bytes": median("buffer_bytes"),
              "median_buffer_storage_bytes":median("buffer_storage_bytes"),
              "median_buffer_upload_bytes":median("buffer_upload_bytes"),
              "median_texture_calls": median("texture_calls"),
              "median_texture_bytes_est": median("texture_bytes"),
              "median_uniform_calls": median("uniform_calls"),
              "median_uniform_bytes": median("uniform_bytes"),
              "median_state_calls": median("state_calls"),
              "median_texture_binds": median("texture_binds"),
              "median_buffer_binds": median("buffer_binds"),
              "median_program_switches": median("program_switches"),
              "median_bucket_calls": median("bucket_calls"),
              "median_flush_records": median("append_calls"),
              "median_forwarded_draws": median("forwarded_draws"),
              "median_suppressed_draws": median("suppressed_draws"),
              "total_forwarded_draws": sum(row["forwarded_draws"] for row in selected),
              "total_suppressed_draws": sum(row["suppressed_draws"] for row in selected),
              "total_render_attempts": sum(row["render_attempts"] for row in selected),
              "total_executed_renders": sum(row["render_executed"] for row in selected),
              "total_present_scene_calls": sum(row["present_scene_calls"] for row in selected),
              "total_present_calls": sum(row["present_calls"] for row in selected),
              "updates_without_render_execution": sum(row["render_attempts"]>0 and
                  row["render_executed"]==0 for row in selected),
              "cadence_overruns": sum(bool(row["flags"] & 2) for row in selected),
              "target_render_rate":(1e9/window["render_period_ns"] if window and window.get("render_period_ns") else None),
              "skipped_render_fraction":(1-sum(r["render_executed"] for r in selected)/sum(r["render_attempts"] for r in selected) if sum(r["render_attempts"] for r in selected) else None),
              "render_missed_deadlines":sum(r.get("render_missed_deadlines",0) for r in selected),
              "render_overruns":sum(r.get("render_overruns",0) for r in selected),
              "render_lateness_ns":_distribution([r.get("render_lateness_ns",0) for r in selected if r["render_executed"]]),
              "raster_challenges":sum(r.get("raster_challenges",0) for r in selected),
              "record_flags": sorted({row["flags"] for row in selected})}
    if window and "measurement_epoch" in window:
        # Rates count timestamped events in [start,end), including events in frames
        # crossing a boundary. Cost distributions require whole eligible frames.
        population=[r for r in rows if r["phase"]==PHASE_NUMBER[name] and
                    r.get("measurement_epoch")==window["measurement_epoch"]]
        offset=window.get("clock_offset_ns",0)
        def events(field: str,count: str | None=None) -> int:
            return sum((r.get(count,0) if count else 1) for r in population
                       if window["start_ns"]<=r.get(field,0)+offset<window["end_ns"])
        result.update(update_attempts_s=events("start_ns")/observed_seconds,
                      render_attempts_s=events("render_start_ns","render_attempts")/observed_seconds,
                      executed_renders_s=events("render_start_ns","render_executed")/observed_seconds,
                      present_calls_s=events("present_time_ns","present_calls")/observed_seconds,
                      excluded_boundary_or_invalid_frames=len(population)-len(selected))
        if events_rows is not None:
            counts={kind:0 for kind in (1,2,3,4)}
            observed_kinds=set()
            for r in events_rows:
                if not r or r[0]!="E" or len(r)<6: continue
                epoch,update,phase,kind,timestamp=map(int,r[1:6])
                if (epoch==window["measurement_epoch"] and phase==PHASE_NUMBER[name] and
                    window["start_ns"]<=timestamp+offset<window["end_ns"] and kind in counts):
                    counts[kind]+=1
                    observed_kinds.add(kind)
            expected={1:events("start_ns"),
                2:events("render_start_ns","render_attempts"),
                3:events("render_start_ns","render_executed"),
                4:events("present_time_ns","present_calls")}
            complete_events=(bool(observed_kinds) and all(counts[kind]==expected[kind] for kind in counts))
            if complete_events:
                for kind,key in ((1,"update_attempts_s"),(2,"render_attempts_s"),(3,"executed_renders_s"),(4,"present_calls_s")):
                    result[key]=counts[kind]/observed_seconds
                result["rate_policy"]="complete timestamped events in acknowledged window; boundary frames excluded only from cost statistics"
            else:
                result["rate_policy"]="frame timestamps; timestamp-event stream absent or incomplete"
        result["invalid_scope_frames"]=sum(bool(r.get("flags",0)&(512|1024)) for r in population)

    result["exclusive_scope_coverage"]={"cpu_fraction":None,"wall_fraction":None,
        "status":"not instrumented","identified_cpu_ms":None,"identified_wall_ms":None,
        "unattributed_cpu_ms":None,"unattributed_wall_ms":None}
    result["bucket_records_per_add"] = (
        round(result["median_flush_records"]/result["median_bucket_calls"], 4)
        if result["median_bucket_calls"] else None)
    return result


def temporal_differences(trace: list[list[str]]) -> dict:
    draw_frames: dict[int,list[tuple[int,...]]] = {}
    state_frames: dict[int,list[tuple[int,int,int]]] = {}
    uniform_frames: dict[int,dict[tuple[int,int,int],tuple[int,int]]] = {}
    uniform_sequences: dict[int,list[tuple[tuple[int,int,int],tuple[int,int]]]] = {}
    buffer_frames: dict[int,dict[tuple[int,int,int,int],int]] = {}
    texture_frames: dict[int,dict[tuple[int,int],int]] = {}
    combined_frames: dict[int,list[tuple[str,tuple[str,...]]]] = {}
    origins={}
    for row in trace:
        if row and row[0] in {"D","S","U","u","B","b","T","t"} and len(row)==20:
            origins[int(row[1])]=(row[13],row[16],row[18]) # epoch/thread/sample window
    def same_window(a,b):
        return b==a+1 and (not origins or (a in origins and origins.get(a)==origins.get(b)))
    def command_payload(row):
        payload=tuple(row[2:13])
        return payload+("context",row[19]) if len(row)==20 else payload
    uniform_exact=[]; uniform_compared_calls=0; uniform_changed_elements=0
    uniform_omitted_elements=0; uniform_oversize_calls=0
    draw_samples=[];draw_sample_total=0
    for row in trace:
        try:
            if row[0]=="D" and len(row)>=13:
                draw_identity=list(map(int,row[2:13]))
                # The wire format preserves the signed GLint bit pattern in
                # uint32 form; expose the semantic signed base vertex here.
                draw_identity[9]=((draw_identity[9]+(1<<31))%(1<<32))-(1<<31)
                draw_frames.setdefault(int(row[1]),[]).append(tuple(draw_identity))
                draw_sample_total+=1
                if len(draw_samples)<128:
                    api_names={1:"glDrawElements",2:"glDrawElementsBaseVertex",
                        3:"glDrawArrays",4:"glDrawElementsInstanced",
                        5:"glDrawArraysInstanced",6:"glDrawElementsInstancedBaseVertex"}
                    draw_samples.append({"render_id":int(row[1]),"callsite_offset":draw_identity[0],
                        "api":api_names.get(draw_identity[1],f"unknown:{draw_identity[1]}"),
                        "program_id":draw_identity[2],"vertex_source_signature":draw_identity[3],
                        "element_buffer_id":draw_identity[4],"mode":draw_identity[5],
                        "count":draw_identity[6],"index_type":draw_identity[7],
                        "index_offset":draw_identity[8] if draw_identity[1] in (1,2,4,6) else None,
                        "first_vertex":draw_identity[8] if draw_identity[1] in (3,5) else None,
                        "base_vertex":draw_identity[9],"instance_count":draw_identity[10]})
                combined_frames.setdefault(int(row[1]),[]).append((row[0],command_payload(row)))
            elif row[0]=="S" and len(row)>=5:
                state_frames.setdefault(int(row[1]),[]).append((int(row[2]),int(row[3]),int(row[4])))
                combined_frames.setdefault(int(row[1]),[]).append((row[0],command_payload(row)))
            elif row[0]=="U" and len(row)>=6:
                frame,key_program,location=int(row[1]),int(row[3]),int(row[4])
                size_hash=int(row[5]); byte_size=size_hash>>32; payload_hash=size_hash&0xffffffff
                key=(int(row[2]),key_program,location)
                uniform_frames.setdefault(frame,{})[key]=(byte_size,payload_hash)
                uniform_sequences.setdefault(frame,[]).append((key,(byte_size,payload_hash)))
                combined_frames.setdefault(frame,[]).append((row[0],command_payload(row)))
            elif row[0]=="u" and len(row)>=6:
                frame=int(row[1]); program=int(row[3]); location=int(row[4])
                key=(int(row[2]),program,location); value=(4,int(row[5]))
                uniform_frames.setdefault(frame,{})[key]=value
                uniform_sequences.setdefault(frame,[]).append((key,value))
                combined_frames.setdefault(frame,[]).append((row[0],command_payload(row)))
            elif row[0]=="V" and len(row)>=8:
                if len(uniform_exact)<512:
                    uniform_exact.append({"render_id":int(row[1]),"callsite_offset":int(row[2]),
                        "program_id":int(row[3]),"uniform_location":int(row[4]),
                        "byte_offset":int(row[5]),"element_index":int(row[5])//4,
                        "previous_u32_hex":f"0x{int(row[6]):08x}",
                        "current_u32_hex":f"0x{int(row[7]):08x}"})
            elif row[0]=="W" and len(row)>=8:
                byte_size,changed,omitted=map(int,row[5:8])
                if omitted==1 and changed==0 and byte_size>256: uniform_oversize_calls+=1
                else:
                    uniform_compared_calls+=1
                    uniform_changed_elements+=changed
                    uniform_omitted_elements+=omitted
            elif row[0] in ("T","t") and len(row)>=4:
                frame,site,dimensions=int(row[1]),int(row[2]),int(row[3])
                texture_frames.setdefault(frame,{})[(ord(row[0]),site)]=dimensions
                combined_frames.setdefault(frame,[]).append((row[0],command_payload(row)))
            elif row[0]=="B" and len(row)>=6:
                frame,key,size,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4])
                buffer_frames.setdefault(frame,{})[(ord("B"),key,0,size)]=digest
                combined_frames.setdefault(frame,[]).append((row[0],command_payload(row)))
            elif row[0]=="b" and len(row)>=6:
                frame,key,size,offset,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4]),int(row[5])
                buffer_frames.setdefault(frame,{})[(ord("b"),key,offset,size)]=digest
                combined_frames.setdefault(frame,[]).append((row[0],command_payload(row)))
        except (ValueError,IndexError):
            continue

    def transitions(series: dict[int,dict], label: str) -> dict:
        ids=sorted(series)
        compared=identical=0; changes=[]
        for previous,current in zip(ids,ids[1:]):
            if not same_window(previous,current): continue
            left,right=series[previous],series[current]
            common=set(left)&set(right)
            compared+=len(common)
            for key in common:
                if left[key]==right[key]: identical+=1
                elif len(changes)<100:
                    changes.append({"previous_render_id":previous,"render_id":current,
                                    "key":list(key),"previous":left[key],"current":right[key]})
        return {"sampled_render_frames":len(ids),"compared_payloads":compared,
                "identical_fraction":identical/compared if compared else None,
                "observed_changes":changes,"change_limit":100,
                "key_fields":({"Uniform payloads":["game_callsite_offset","program_id","uniform_location"],
                               "Buffer upload payloads":["operation_code (66=BufferData, 98=BufferSubData)","target_and_buffer_id","offset","byte_size"],
                               "Texture upload call dimensions":["operation_code (84=TexImage2D, 116=TexSubImage2D)","game_callsite_offset"]}.get(label,[])),
                "note":f"{label}: hashes/dimensions identify changed records; exact changed byte ranges are not captured."}

    ids=sorted(draw_frames)
    draw_pairs=[(a,b) for a,b in zip(ids,ids[1:]) if same_window(a,b)]
    same=sum(draw_frames[a]==draw_frames[b] for a,b in draw_pairs)
    pairs=len(draw_pairs)
    state_ids=sorted(state_frames)
    state_pairs_list=[(a,b) for a,b in zip(state_ids,state_ids[1:]) if same_window(a,b)]
    same_state=sum(state_frames[a]==state_frames[b] for a,b in state_pairs_list)
    state_pairs=len(state_pairs_list)
    state_changes=[]
    for a,b in state_pairs_list:
        left,right=state_frames[a],state_frames[b]
        for index in range(max(len(left),len(right))):
            previous=left[index] if index<len(left) else None
            current=right[index] if index<len(right) else None
            if previous!=current and len(state_changes)<100:
                state_changes.append({"previous_render_id":a,"render_id":b,
                    "sequence_index":index,"previous_call":previous,"current_call":current})
    combined_ids=sorted(combined_frames)
    combined_pairs=[(a,b) for a,b in zip(combined_ids,combined_ids[1:]) if same_window(a,b)]
    combined_same=sum(combined_frames[a]==combined_frames[b] for a,b in combined_pairs)
    uniform_ids=sorted(uniform_sequences)
    uniform_pairs=[(a,b) for a,b in zip(uniform_ids,uniform_ids[1:]) if same_window(a,b)]
    uniform_changed=[]
    uniform_equal_calls=0; uniform_total_calls=0
    for previous,current in uniform_pairs:
        left,right=uniform_sequences[previous],uniform_sequences[current]
        for index in range(max(len(left),len(right))):
            old=left[index] if index<len(left) else None
            new=right[index] if index<len(right) else None
            uniform_total_calls+=1
            if old==new: uniform_equal_calls+=1
            elif len(uniform_changed)<256:
                uniform_changed.append({"previous_render_id":previous,"render_id":current,
                    "sequence_index":index,"previous":old,"current":new})
    return {"draw_identity":{"sampled_render_frames":len(ids),
              "identical_consecutive_fraction":same/pairs if pairs else None,
              "sampled_draws":draw_samples,"sampled_draw_count":draw_sample_total,
              "omitted_draw_records":max(0,draw_sample_total-len(draw_samples)),
              "identity_fields":["API","caller","program","vertex-input signature (VAO and enabled attribute VBO/formats/offsets)","IBO","mode","count","index type","index offset/first","signed base vertex","instance count"]},
            "draw_topology":{"sampled_render_frames":len(ids),
              "identical_consecutive_fraction":same/pairs if pairs else None},
            "combined_command_state_resource":{"sampled_render_frames":len(combined_ids),
              "compared_consecutive_pairs":len(combined_pairs),
              "identical_consecutive_fraction":combined_same/len(combined_pairs) if combined_pairs else None,
              "comparison":"ordered draw, state, uniform, resource upload, and texture records"},
            "render_state":{"sampled_render_frames":len(state_ids),
              "identical_consecutive_fraction":same_state/state_pairs if state_pairs else None,
              "changed_calls":state_changes,"change_limit":100,
              "note":"Call tuple is (game callsite offset, operation kind, state value)."},
            "uniform_payloads":transitions(uniform_frames,"Uniform payloads"),
            "uniform_call_sequence":{"compared_consecutive_pairs":len(uniform_pairs),
              "compared_calls":uniform_total_calls,
              "identical_fraction":uniform_equal_calls/uniform_total_calls if uniform_total_calls else None,
              "changes":uniform_changed,"change_limit":256,
              "comparison":"ordered calls retain repeated callsite/program/location updates"},
            "uniform_exact_elements":{"compared_calls":uniform_compared_calls,
              "changed_32bit_elements":uniform_changed_elements,
              "omitted_element_changes":uniform_omitted_elements,
              "oversize_payload_calls_without_byte_diff":uniform_oversize_calls,
              "captured_changes":uniform_exact,"capture_limit":512,
              "note":"Exact raw 32-bit elements for consecutive sampled glUniform4fv payloads up to 256 bytes; larger payloads retain hashes only."},
            "buffer_uploads":transitions(buffer_frames,"Buffer upload payloads"),
            "texture_upload_calls":transitions(texture_frames,"Texture upload call dimensions")}


def gpu_summary(trace: list[list[str]]) -> dict:
    groups={}; missing={}; legacy=[]
    for row in trace:
        if not row or row[0]!="G": continue
        try:
            if len(row)<13:
                legacy.append({"phase":int(row[1]),"render_id":int(row[2]),
                    "legacy_interval_ms":int(row[3])/1e6,"context":int(row[5]) if len(row)>5 else 0,
                    "status":"unqualified legacy interval; no whole-render interpretation"}); continue
            phase,render,start,end,lifetime,pass_id,seq,epoch,update,reason=map(int,row[1:11])
            if reason:
                counts=missing.setdefault(str(phase),{})
                counts[str(reason)]=counts.get(str(reason),0)+1; continue
            if end<start: continue
            groups.setdefault(str(phase),{}).setdefault((lifetime,pass_id),[]).append(
                {"epoch":epoch,"update_id":update,"render_id":render,"sequence":seq,
                 "start_timestamp_ns":start,"end_timestamp_ns":end,"interval_ms":(end-start)/1e6})
        except ValueError: continue
    result={}
    for phase in groups.keys() | missing.keys():
        contexts=groups.get(phase,{})
        result[phase]={"context_pass_segments":{f"{ctx}:{pass_id}":{
            "context_lifetime":ctx,"pass":pass_id,"available_samples":len(records),
            "median_interval_ms":statistics.median(r["interval_ms"] for r in records),
            "intervals":records} for (ctx,pass_id),records in contexts.items()},
            "context_count":len({ctx for ctx,_ in contexts}),
            "missing_segment_counts":missing.get(phase,{}),
            "median_whole_render_ms":None,"median_map_ms":None,
            "cross_context_aggregation":"not performed"}
    return {"status":"segmented_partial" if result else "unavailable_or_unsupported",
            "phases":result,"legacy_unqualified_samples":legacy,
            "missing_reasons":{"1":"null context","2":"unsupported","3":"context capacity",
                "4":"query pool exhaustion","5":"unexpected owner","6":"destroyed",
                "7":"uncollected at measurement stop","8":"invalid timestamps"},
            "query_policy":"Completed queries polled only in their owning current context; intervals are timeline elapsed time, never whole-render GPU busy time."}


PACING_SCOPES = {9}
SEMANTIC_SCOPES = {4,5,6,7}
ENVELOPE_SCOPES = {0,1,2,3,8}


def scope_tree_summary(trace: list[list[str]], phases: list[dict]) -> dict:
    populations={}; legacy=[]
    for row in trace:
        if not row or row[0]!="Q": continue
        if len(row) not in (13,16):
            if len(row)>=10:
                legacy.append({"update_id":row[1],"phase":row[2],"parent_scope":row[3],
                    "scope":row[4],"calls":row[5],"inclusive_wall_ns":row[6],
                    "inclusive_cpu_ns":row[7],"exclusive_wall_ns":row[8],"exclusive_cpu_ns":row[9]})
            continue
        try:
            update,phase,parent,scope,calls,iw,ic,ew,ec,epoch,node,flags=map(int,row[1:13])
            if not 0<=scope<len(SCOPE_NAMES): flags|=1024
            nodes=populations.setdefault((phase,epoch,update),{})
            if node in nodes: flags|=1024
            nodes[node]={"node":node,"parent_node":parent,"scope":scope,"calls":calls,
                         "iw":iw,"ic":ic,"ew":ew,"ec":ec,"flags":flags}
        except ValueError: continue
    phase_ids={PHASE_NUMBER[p["name"]] for p in phases if p.get("name") in PHASE_NUMBER}
    output={}; residuals=[]
    for phase in phase_ids:
        trees=[]; invalid=0
        for (pid,epoch,update),nodes in populations.items():
            if pid!=phase: continue
            good=bool(nodes) and sum(n["parent_node"]==-1 for n in nodes.values())==1
            for index,n in nodes.items():
                children=[c for c in nodes.values() if c["parent_node"]==index]
                good &= (not n["flags"] and n["calls"]>0 and 0<=n["scope"]<len(SCOPE_NAMES) and
                         (n["parent_node"]==-1 or n["parent_node"] in nodes) and
                         n["iw"]==n["ew"]+sum(c["iw"] for c in children) and
                         n["ic"]==n["ec"]+sum(c["ic"] for c in children))
                seen={index}; parent=n["parent_node"]
                while parent!=-1 and parent in nodes:
                    if parent in seen: good=False; break
                    seen.add(parent); parent=nodes[parent]["parent_node"]
            if not good: invalid+=1; continue
            trees.append(nodes)
        totals={}; gates={}; distributions={}
        def descendants(nodes,index):
            return [n for n in nodes.values() if under(nodes,n,index)]
        def under(nodes,n,index):
            seen=set()
            while n["node"] not in seen:
                if n["node"]==index: return True
                seen.add(n["node"])
                if n["parent_node"]==-1: return False
                n=nodes[n["parent_node"]]
            return False
        for scope in (0,2):
            inclusive=[0,0]; identified=[0,0]; ratios=[[],[]]
            for nodes in trees:
                # Only outermost occurrence supplies denominator; recursion remains
                # in its subtree and each exclusive interval is counted once.
                for index,n in nodes.items():
                    if n["scope"]!=scope: continue
                    parent=n["parent_node"]; recursive=False
                    while parent!=-1:
                        if nodes[parent]["scope"]==scope: recursive=True
                        parent=nodes[parent]["parent_node"]
                    if recursive: continue
                    semantic=[c for c in descendants(nodes,index) if c["scope"] in SEMANTIC_SCOPES and c["scope"]!=9]
                    for axis,(inc,exc) in enumerate((("ic","ec"),("iw","ew"))):
                        amount=sum(c[exc] for c in semantic)
                        inclusive[axis]+=n[inc]; identified[axis]+=amount
                        if n[inc]: ratios[axis].append(amount/n[inc])
            fractions=[identified[i]/inclusive[i] if inclusive[i] else None for i in (0,1)]
            passed=bool(trees) and not invalid and all(v is not None and v>=.95 for v in fractions)
            gates[SCOPE_NAMES[scope]]={"cpu_fraction":fractions[0],"wall_fraction":fractions[1],
                "inclusive_cpu_ms":inclusive[0]/1e6,"inclusive_wall_ms":inclusive[1]/1e6,
                "identified_cpu_ms":identified[0]/1e6,"identified_wall_ms":identified[1]/1e6,
                "residual_cpu_ms":(inclusive[0]-identified[0])/1e6,
                "residual_wall_ms":(inclusive[1]-identified[1])/1e6,
                "gate":"passed" if passed else "not met"}
            def distribution(values):
                if not values: return None
                ordered=sorted(values)
                return {"samples":len(values),"min":ordered[0],"median":statistics.median(values),
                        "p95":ordered[min(len(ordered)-1,int(.95*len(ordered)))],"max":ordered[-1]}
            distributions[SCOPE_NAMES[scope]]={"cpu":distribution(ratios[0]),"wall":distribution(ratios[1])}
        for nodes in trees:
            for n in nodes.values():
                path=[]; current=n
                while True:
                    path.append(current["scope"])
                    if current["parent_node"]==-1: break
                    current=nodes[current["parent_node"]]
                path=tuple(reversed(path))
                total=totals.setdefault(path,{"name":SCOPE_NAMES[n["scope"]],"scope_path":[SCOPE_NAMES[i] for i in path],
                    "classification":"envelope" if n["scope"] in ENVELOPE_SCOPES else "pacing" if n["scope"] in PACING_SCOPES else "semantic",
                    "calls":0,"inclusive_cpu_ms":0,"inclusive_wall_ms":0,"exclusive_cpu_ms":0,"exclusive_wall_ms":0,
                    "residual_cpu_ms":0,"residual_wall_ms":0,"non_cpu_elapsed_ms":0})
                total["calls"]+=n["calls"]
                for field,key in (("ic","inclusive_cpu_ms"),("iw","inclusive_wall_ms"),("ec","exclusive_cpu_ms"),("ew","exclusive_wall_ms")):
                    total[key]+=n[field]/1e6
                total["non_cpu_elapsed_ms"]+=(n["ew"]-n["ec"])/1e6
                if n["scope"] in ENVELOPE_SCOPES:
                    total["residual_cpu_ms"]+=n["ec"]/1e6;total["residual_wall_ms"]+=n["ew"]/1e6
        for path,total in totals.items():
            samples=[]
            for nodes in trees:
                costs=[0,0]
                for n in nodes.values():
                    node_path=[]; current=n
                    while True:
                        node_path.append(current["scope"])
                        if current["parent_node"]==-1: break
                        current=nodes[current["parent_node"]]
                    if tuple(reversed(node_path))==path:
                        costs[0]+=n["ec"]/1e6; costs[1]+=n["ew"]/1e6
                samples.append(costs)
            def costs_distribution(axis):
                values=sorted(cost[axis] for cost in samples)
                return {"samples":len(values),"median":statistics.median(values),
                        "p95":values[min(len(values)-1,int(.95*len(values)))],"max":values[-1]} if values else None
            total["per_frame_exclusive_cpu_ms"]=costs_distribution(0)
            total["per_frame_exclusive_wall_ms"]=costs_distribution(1)
            if total["classification"]=="envelope": residuals.append({"phase":phase,**total})
        output[str(phase)]={"nodes":list(totals.values()),"valid_frames":len(trees),"invalid_frames":invalid,
            "tree_reconciliation":"passed" if trees and not invalid else "unusable or missing",
            "envelope_coverage":gates,"per_frame_coverage_distributions":distributions,
            "pacing_cpu_ms":sum(n["ec"] for nodes in trees for n in nodes.values() if n["scope"]==9)/1e6,
            "pacing_wall_ms":sum(n["ew"] for nodes in trees for n in nodes.values() if n["scope"]==9)/1e6,
            "coverage_95_percent_gate":"passed" if all(g["gate"]=="passed" for g in gates.values()) else "not met"}
    return {"status":"available" if populations else "legacy_or_unavailable","phases":output,
            "legacy_records":legacy,"ranked_residuals":sorted(residuals,key=lambda n:n["residual_cpu_ms"],reverse=True),
            "coverage_policy":"Only explicitly semantic exclusive intervals count. Envelope exclusive time remains residual. Pacing never improves UpdateOneFrame or executed Render gates. Ratios use aggregate CPU and wall totals separately.",
            "wall_cpu_policy":"Wall minus thread CPU is non-CPU elapsed time and may include waiting or descheduling."}


PHASE_NUMBER = {name: i+1 for i, (name, *_rest) in enumerate(PHASES)}
PHASE_NUMBER.update({"P0": 100, "P1": 101, "P2": 102, "TAIL": 103,
                     "LPM0": 104, "LPM": 105, "LPM1": 106,
                     "LPMF0": 107, "LPMF": 108, "LPMF1": 109, "ANATIVE":110})


def analyze(run_dir: Path) -> dict:
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise base.BenchmarkError(f"Missing profiler manifest: {run_dir}")
    manifest = json.loads(manifest_path.read_text())
    frames = frame_rows(run_dir / "telemetry.csv")
    trace = read_rows(run_dir / "telemetry.csv")
    phases = list(manifest.get("calibration",{}).get("phases",[])) + list(manifest.get("phases",[]))
    if manifest.get("profiling_tail"):
        phases.append(manifest["profiling_tail"])
    event_rows=trace
    trace = eligible_trace(trace,frames,phases)
    summaries = [phase_summary(frames, item["name"], item["duration_s"],item,event_rows)
                 for item in phases if item.get("name") in PHASE_NUMBER and item.get("role")!="forensic"]
    counters = {}
    for row in trace:
        if row[0] == "C" and len(row) >= 5:
            index=int(row[1]); name=HOOK_NAMES[index] if index<len(HOOK_NAMES) else f"hook_{index}"
            counters[name] = {"calls": int(row[2]), "wall_ns_sum": int(row[3]),
                              "thread_cpu_ns_sum":int(row[4]),
                              "completeness":"partial supporting telemetry",
                              "timing":"sampled" if index==7 else "count_only" if index in (8,9,11) else "inclusive/scope"}
    totals=next((list(map(int,row[1:4])) for row in trace if row[0]=="X" and len(row)>=4),[0,0,0])
    hook_failures=next((int(row[1]) for row in reversed(trace) if row[0]=="Z" and len(row)>=2),None)
    detail = {key: sum(1 for row in trace if row[0] == key)
              for key in ("D", "S", "U", "V", "W", "B", "b", "T", "t", "G")}
    temporal = temporal_differences(trace)
    gpu = gpu_summary(trace)
    scope_tree=scope_tree_summary(trace,phases)
    format_qualified=(manifest.get("format_version")==FORMAT_VERSION and ["H","3"] in trace and
                      bool(frames) and all("generation" in f for f in frames))
    if not format_qualified:
        for coverage in scope_tree["phases"].values():
            coverage["coverage_95_percent_gate"]="not met"
            coverage["format_gate"]="legacy/missing v3 provenance"
    for summary in summaries:
        coverage=scope_tree["phases"].get(str(PHASE_NUMBER[summary["phase"]]),{})
        if summary.get("invalid_scope_frames"):
            coverage["tree_reconciliation"]="invalid frames excluded"
            coverage["coverage_95_percent_gate"]="not met"
        summary["exclusive_scope_coverage"]=coverage
    a_rows = [p for p in summaries if p["phase"] in {name for name,*_ in PHASES if name.startswith("A")} and p["status"] == "complete"]
    drift = {}
    for field in ("median_update_cpu_ms", "median_render_cpu_ms", "median_draws"):
        values = [p[field] for p in a_rows]
        drift[field] = (max(values)/min(values)-1) if values and min(values)>0 else None
    reference = json.loads((ROOT / "results/autonomous-reproducibility.json").read_text())
    base_cpu_ms_swap = (reference["summary"]["eu4_cpu_ms_per_s"]["median"] /
                        reference["summary"]["median_swaps_s"]["median"])
    report_gates=GateEvidence(dict(manifest.get("gates",{})))
    is_v3=format_qualified
    report_gates.record("format_v3","passed" if is_v3 else "failed","v3 header, manifest, and frame layout required")
    report_kind=manifest.get("report_kind","causal")
    expected_baselines=({name for name,*_ in PHASES if name.startswith("A")}
        if report_kind=="causal" else {"A0"} if report_kind=="residual_discovery" else set())
    baselines=[p for p in summaries if p["phase"] in expected_baselines]
    coverage_ok=({p["phase"] for p in baselines}==expected_baselines and all(
        p.get("exclusive_scope_coverage",{}).get("coverage_95_percent_gate")=="passed" for p in baselines))
    unknown=unassociated_origins(trace,phases)
    report_gates.record("origin_integrity","failed" if unknown else "passed" if format_qualified else "unavailable","Measured calls outside owned update frames must be attributed before causal acceptance",records=unknown)
    semantic_status=("unavailable" if not expected_baselines else "passed" if coverage_ok else "failed")
    semantic_reason=("No causal baseline is scheduled in calibration-only reports" if not expected_baselines else
        "Every scheduled baseline needs independent ≥95% Update/Render CPU and wall coverage")
    report_gates.record("semantic_coverage",semantic_status,semantic_reason)
    build_evidence=manifest.get("evidence",{}).get("build",{})
    candidate_manifest=build_evidence.get("draw_api_manifest")
    if candidate_manifest and (candidate_manifest.get("executable_sha256")!=manifest.get("executable_sha256") or
        candidate_manifest.get("observer_header_sha256")!=build_evidence.get("native_source_hashes",{}).get(draw_api.HEADER.name)):
        candidate_manifest=None
    qualified_frames=[f for w in phases for f in frames if eligible_frame(f,w)]
    residual_discovery=report_kind=="residual_discovery"
    present_phases={p.get("name") for p in phases if p.get("name") in PHASE_NUMBER}
    coverage_phases={PHASE_NUMBER[name] for name in present_phases
                     if name.startswith("A") or name in {"C","P0","P1","P2"}}
    draw_coverage=draw_api.coverage(candidate_manifest,qualified_frames,trace,
        coverage_phases,require_c=manifest.get("report_kind")=="causal" and "C" in present_phases)
    pre_c_only=(manifest.get("report_kind") in {"residual_discovery","calibration_only"}
                and "C" not in present_phases)
    if "C" not in present_phases:
        draw_coverage={**draw_coverage,"status":"unavailable",
            "stage":"not_applicable" if pre_c_only else "incomplete",
            "pre_c_diagnostics":draw_coverage,
            "reason":"C was not scheduled; pre-C draw diagnostics only" if pre_c_only else
                "C has no captured evidence in this causal report; pre-C diagnostics cannot qualify the intervention",
            "c_intervention":"not applicable"}
    report_gates.record("draw_api_coverage",draw_coverage["status"],draw_coverage["reason"],evidence=draw_coverage)

    required = required_gates_for_report_kind(report_kind)
    output = {"format_version":FORMAT_VERSION,"report_kind":report_kind,
              "phase_roles":{p.get("name"):p.get("role","causal") for p in phases},
              "metal_feasibility":"feasibility seed; go/no-go undetermined",
              "unassociated_origins":unknown,"release_gates":report_gates.entries,
              "release_blockers":report_gates.blockers(required),
              "status": manifest.get("status"), "run_dir": str(run_dir),
              "executable_sha256": manifest.get("executable_sha256"),
              "phases": summaries, "hook_totals": counters,
              "hook_totals_status":"partial supporting telemetry; worker producer flush completeness unproven",
              "draw_api_coverage":draw_coverage,
              "C_intervention":"not applicable — C not scheduled" if pre_c_only else
                  "complete draw suppression" if draw_coverage["status"]=="passed" else "partial draw-suppression intervention",
              "structural_evidence_incomplete":[render_identity(f) for f in qualified_frames if f.get("flags",0)&2048],
              "structural_evidence_missing_reasons":[r for r in trace if r and r[0]=="L"],
              "update_render_present_totals":{"update_calls":totals[0],"render_attempts":totals[1],"presents":totals[2]},
              "hook_failures":hook_failures,
              "sampled_trace_rows": detail, "normal_A_drift_fraction": drift,
              "temporal_differences": temporal,
              "engine_inventory":"analysis/frame-model-engine.json",
              "backend_graph": "analysis/frame-model-backend.json",
              "historical_process_cpu_ms_per_swap": round(base_cpu_ms_swap, 4),
              "coverage": manifest.get("coverage", {}),
              "power_by_phase": manifest.get("power_by_phase", {}),
              "gpu_by_phase": gpu,
              "exclusive_cost_tree":scope_tree,
              "causal_contrasts": causal_contrasts(summaries),
              "power_fps_scaling":power_fps_scaling(summaries,manifest.get("power_by_phase",{})),
              "conclusions": derive_conclusions(summaries, drift,manifest.get("power_by_phase",{})),
              "recommendations": derive_recommendations(summaries,manifest.get("power_by_phase",{}),temporal,gpu,report_gates.entries),
              "limitations": manifest.get("limitations", [])}
    baseline=[p for p in summaries if p["phase"] in {name for name,*_ in PHASES if name.startswith("A")}]
    output["diagnosis_status"]=("eligible for causal assessment" if manifest.get("status")=="complete" and
        not output["release_blockers"] and manifest.get("format_version")==FORMAT_VERSION and
        manifest.get("probe_integrity",{}).get("status")=="passed" and
        {p["phase"] for p in baseline}=={name for name,*_ in PHASES if name.startswith("A")} and all(
        p["exclusive_scope_coverage"].get("coverage_95_percent_gate")=="passed" for p in baseline)
        else "partial attribution")
    if output["report_kind"]=="residual_discovery":
        output["conclusions"]=["Residual discovery identifies missing attribution; reconciliation alone does not establish a diagnosis."]
        output["recommendations"]=[{"intervention":"expand verified semantic hooks in the largest residual envelopes",
            "signal":"ranked residual CPU/wall table","estimated_reduction_fraction":None,
            "evidence":"add 3–8 ABI-verified semantic hooks per iteration and repeat perturbation validation"}]
    elif output["diagnosis_status"]=="partial attribution":
        output["conclusions"]=["The captured evidence does not meet the full causal diagnosis gates."]
        output["recommendations"]=[]
    if draw_coverage["status"]!="passed":
        for key in ("C","C_minus_B"):
            if key in output["causal_contrasts"]:
                output["causal_contrasts"][key].update(status="partial intervention",interpretation="partial draw-suppression intervention; API coverage incomplete")
    (run_dir / "report.json").write_text(json.dumps(output, indent=2)+"\n")
    (run_dir / "report.md").write_text(render_report(output))
    return output


def causal_contrasts(phases: list[dict]) -> dict:
    by_name={item["phase"]:item for item in phases if item.get("status")=="complete"}
    brackets={"B":("A0","A1"),"C":("A1","A2"),"D":("A2","A3"),
              "E30":("A3","A4"),"E15":("A4","A5")}
    result={}
    for intervention,(before,after) in brackets.items():
        if not all(name in by_name for name in (before,intervention,after)):
            result[intervention]={"status":"missing bracket","controls":[before,after]}
            continue
        left,right,tested=(by_name[before],by_name[after],by_name[intervention])
        control_cpu=statistics.mean((left["median_update_cpu_ms"],right["median_update_cpu_ms"]))
        control_rate=statistics.mean((left["update_attempts_s"],right["update_attempts_s"]))
        drift=(max(left["median_update_cpu_ms"],right["median_update_cpu_ms"])/
               min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])-1
               if min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])>0 else None)
        cadence_delta=(tested["update_attempts_s"]/control_rate-1 if control_rate else None)
        result[intervention]={"status":"complete","controls":[before,after],
            "control_cpu_ms_per_update":control_cpu,
            "tested_cpu_ms_per_update":tested["median_update_cpu_ms"],
            "cpu_difference_control_minus_test_ms":control_cpu-tested["median_update_cpu_ms"],
            "control_update_attempts_s":control_rate,
            "tested_update_attempts_s":tested["update_attempts_s"],
            "cadence_delta_fraction":cadence_delta,"control_cpu_drift_fraction":drift,
            "cadence_valid":cadence_delta is not None and abs(cadence_delta)<=.03,
            "controls_stable":drift is not None and drift<=.05,
            "interpretation":"causal intervention contrast; values are not additive cost partitions"}
    if all(name in by_name for name in ("B","C")):
        raw=by_name["C"]["median_update_cpu_ms"]-by_name["B"]["median_update_cpu_ms"]
        result["C_minus_B"]={"status":"complete",
            "cpu_ms_per_update":raw,
            "C_cpu_ms_per_update":by_name["C"]["median_update_cpu_ms"],
            "B_cpu_ms_per_update":by_name["B"]["median_update_cpu_ms"],
            "interpretation":"preparation/state work remaining after draw suppression, subject to temporal drift"}
        if all(name in by_name for name in ("A0","A1","A2")):
            b_control=statistics.mean((by_name["A0"]["median_update_cpu_ms"],
                                       by_name["A1"]["median_update_cpu_ms"]))
            c_control=statistics.mean((by_name["A1"]["median_update_cpu_ms"],
                                       by_name["A2"]["median_update_cpu_ms"]))
            b_local=by_name["B"]["median_update_cpu_ms"]-b_control
            c_local=by_name["C"]["median_update_cpu_ms"]-c_control
            b_rate=statistics.mean((by_name["A0"]["update_attempts_s"],
                                    by_name["A1"]["update_attempts_s"]))
            c_rate=statistics.mean((by_name["A1"]["update_attempts_s"],
                                    by_name["A2"]["update_attempts_s"]))
            b_cadence=by_name["B"]["update_attempts_s"]/b_rate-1 if b_rate else None
            c_cadence=by_name["C"]["update_attempts_s"]/c_rate-1 if c_rate else None
            b_drift=(max(by_name["A0"]["median_update_cpu_ms"],by_name["A1"]["median_update_cpu_ms"])/
                     min(by_name["A0"]["median_update_cpu_ms"],by_name["A1"]["median_update_cpu_ms"])-1)
            c_drift=(max(by_name["A1"]["median_update_cpu_ms"],by_name["A2"]["median_update_cpu_ms"])/
                     min(by_name["A1"]["median_update_cpu_ms"],by_name["A2"]["median_update_cpu_ms"])-1)
            result["C_minus_B"].update({
                "B_minus_local_A_ms_per_update":b_local,
                "C_minus_local_A_ms_per_update":c_local,
                "local_control_sensitivity_ms_per_update":c_local-b_local,
                "neighboring_A_mean_drift_ms_per_update":c_control-b_control,
                "sensitivity_valid":b_cadence is not None and c_cadence is not None and
                    abs(b_cadence)<=.03 and abs(c_cadence)<=.03 and b_drift<=.05 and c_drift<=.05,
                "B_cadence_delta_fraction":b_cadence,"C_cadence_delta_fraction":c_cadence,
                "B_control_cpu_drift_fraction":b_drift,"C_control_cpu_drift_fraction":c_drift,
                "sensitivity_policy":"local-control adjusted contrast for drift sensitivity only; not an additive cost partition"})
    else:
        result["C_minus_B"]={"status":"missing phase"}
    return result


def power_fps_scaling(phases: list[dict],power: dict) -> dict:
    by_name={item["phase"]:item for item in phases if item.get("status")=="complete"}
    native=[name for name in ("A3","A4","A5") if name in by_name and name in power]
    if len(native)<2 or any(name not in by_name or name not in power for name in ("E30","E15")):
        return {"status":"incomplete","points":[]}
    native_fps=statistics.mean(by_name[name]["executed_renders_s"] for name in native)
    native_watts=[power[name].get("combined_w") for name in native]
    if any(value is None for value in native_watts):
        return {"status":"incomplete","points":[]}
    points=[{"phase":"native","render_fps":native_fps,
             "combined_w":statistics.mean(native_watts)}]
    for name in ("E30","E15"):
        watts=power[name].get("combined_w")
        if watts is None: return {"status":"incomplete","points":points}
        points.append({"phase":name,"render_fps":by_name[name]["executed_renders_s"],
                       "combined_w":watts})
    xs=[item["render_fps"] for item in points]
    ys=[item["combined_w"] for item in points]
    mean_x,mean_y=statistics.mean(xs),statistics.mean(ys)
    variance=sum((x-mean_x)**2 for x in xs)
    if variance==0: return {"status":"degenerate_cadence","points":points}
    slope=sum((x-mean_x)*(y-mean_y) for x,y in zip(xs,ys))/variance
    intercept=mean_y-slope*mean_x
    residuals=[y-(intercept+slope*x) for x,y in zip(xs,ys)]
    total=sum((y-mean_y)**2 for y in ys)
    return {"status":"complete","points":points,
            "linear_fit":{"fixed_floor_intercept_w":intercept,
                "frame_driven_w_per_fps":slope,
                "r_squared":1-sum(value*value for value in residuals)/total if total else None,
                "residuals_w":residuals,
                "interpretation":"three measured cadence points; the linear fit is descriptive and does not separate DVFS transitions"}}


def derive_conclusions(phases: list[dict], drift: dict,power: dict | None=None) -> list[str]:
    by_name = {phase["phase"]: phase for phase in phases}
    output = []
    def contrast(a: str, b: str, key: str) -> float | None:
        if a not in by_name or b not in by_name:
            return None
        left, right = by_name[a].get(key), by_name[b].get(key)
        return (left-right)/left if left not in (None, 0) and right is not None else None
    brackets={"B":("A0","A1"),"C":("A1","A2"),"D":("A2","A3"),
              "E30":("A3","A4"),"E15":("A4","A5")}
    labels={"B":"renderer skipped","C":"draw submission suppressed",
            "D":"raster-work suppression","E30":"30 FPS","E15":"15 FPS"}
    for name,(before,after) in brackets.items():
        if all(key in by_name for key in (before,name,after)):
            ref=statistics.mean((by_name[before]["median_update_cpu_ms"],
                                 by_name[after]["median_update_cpu_ms"]))
            value=by_name[name]["median_update_cpu_ms"]
            if ref:
                output.append(f"{labels[name]}: update CPU/frame changed {value/ref-1:+.1%} against the mean of neighboring {before}/{after} controls; attribution remains causal and includes phase-to-phase drift.")
    if all(name in by_name for name in ("B","C")):
        b,c=by_name["B"],by_name["C"]
        output.append(f"C−B preparation residual: {c['median_update_cpu_ms']-b['median_update_cpu_ms']:+.3f} CPU ms/update (C={c['median_update_cpu_ms']:.3f}, B={b['median_update_cpu_ms']:.3f}); phases are separated in time and must be interpreted alongside local A controls.")
    if drift.get("median_update_cpu_ms") is not None and drift["median_update_cpu_ms"]>.05:
        output.append("Normal A controls drifted by more than 5%; causal CPU contrasts are inconclusive.")
    power=power or {}
    for name,before,after,label in (("E30","A3","A4","30 FPS"),("E15","A4","A5","15 FPS")):
        if all(key in power for key in (name,before,after)):
            for field,title in (("combined_w","combined power"),("eu4_cputime_ms_per_s","EU IV CPU time per second")):
                values=[power[key].get(field) for key in (name,before,after)]
                if all(value is not None for value in values) and statistics.mean(values[1:]):
                    delta=values[0]/statistics.mean(values[1:])-1
                    output.append(f"{label} cadence: {title} changed {delta:+.1%} against neighboring A controls.")
    if all(name in by_name and name in power for name in ("LPM0","LPM","LPM1")):
        normal_rate=statistics.mean((by_name["LPM0"]["update_attempts_s"],
                                     by_name["LPM1"]["update_attempts_s"]))
        low_rate=by_name["LPM"]["update_attempts_s"]
        normal_work=statistics.mean((by_name["LPM0"]["median_update_cpu_ms"],
                                     by_name["LPM1"]["median_update_cpu_ms"]))
        low_work=by_name["LPM"]["median_update_cpu_ms"]
        output.append(f"Natural-cadence Low Power Mode: updates/s changed {low_rate/normal_rate-1:+.1%}; thread CPU ms/update changed {low_work/normal_work-1:+.1%}.")
        normal_cpu=statistics.mean((power["LPM0"].get("eu4_cputime_ms_per_s",0),
                                    power["LPM1"].get("eu4_cputime_ms_per_s",0)))
        low_cpu=power["LPM"].get("eu4_cputime_ms_per_s")
        normal_w=statistics.mean((power["LPM0"].get("combined_w",0),power["LPM1"].get("combined_w",0)))
        low_w=power["LPM"].get("combined_w")
        if normal_cpu and low_cpu is not None and normal_w and low_w is not None:
            output.append(f"Low Power Mode: EU IV CPU time per second changed {low_cpu/normal_cpu-1:+.1%}; combined power changed {low_w/normal_w-1:+.1%}. Compare frame work and frequency fields before interpreting this as a throughput effect.")
    if all(name in by_name and name in power for name in ("LPMF0","LPMF","LPMF1")):
        normal_rate=statistics.mean((by_name["LPMF0"]["update_attempts_s"],
                                     by_name["LPMF1"]["update_attempts_s"]))
        low_rate=by_name["LPMF"]["update_attempts_s"]
        normal_work=statistics.mean((by_name["LPMF0"]["median_update_cpu_ms"],
                                     by_name["LPMF1"]["median_update_cpu_ms"]))
        low_work=by_name["LPMF"]["median_update_cpu_ms"]
        normal_w=statistics.mean((power["LPMF0"].get("combined_w",0),
                                  power["LPMF1"].get("combined_w",0)))
        low_w=power["LPMF"].get("combined_w")
        rate_delta=f"{low_rate/normal_rate-1:+.1%}" if normal_rate else "unavailable"
        work_delta=f"{low_work/normal_work-1:+.1%}" if normal_work else "unavailable"
        power_delta=f"{low_w/normal_w-1:+.1%}" if normal_w and low_w is not None else "unavailable"
        output.append(f"Fixed-cadence Low Power DVFS check: update rate changed {rate_delta}; CPU ms/update changed {work_delta}; combined power changed {power_delta}. This is a fixed-throughput efficiency comparison, separate from natural-cadence LPM.")
    if not output:
        output.append("Insufficient valid phases for a causal recommendation.")
    return output


def derive_recommendations(phases: list[dict],power: dict,temporal: dict,gpu: dict,gates: dict | None=None) -> list[dict]:
    blockers=GateEvidence(gates or {}).blockers()
    if blockers:
        return [{"intervention":"complete release evidence", "status":"blocked",
                 "signal":"mandatory gates missing or failed","estimated_reduction_fraction":None,"evidence":blockers}]
    by_name={item["phase"]:item for item in phases if item.get("status")=="complete"}
    if any(p.get("exclusive_scope_coverage",{}).get("coverage_95_percent_gate")!="passed"
           for p in by_name.values() if p["phase"] in {name for name,*_ in PHASES if name.startswith("A")}):
        return [{"intervention":"no architectural recommendation yet","signal":"semantic coverage incomplete",
                 "estimated_reduction_fraction":None,"evidence":"UpdateOneFrame and executed Render require separate ≥95% CPU/wall gates"}]
    candidates=[]
    for name,before,after,label in (
        ("B","A0","A1","adaptive/event-driven render scheduling"),
        ("D","A2","A3","reduce raster/pixel work or cache static map layers")):
        if not all(key in by_name for key in (name,before,after)): continue
        left,right,tested=by_name[before],by_name[after],by_name[name]
        rate=statistics.mean((left["update_attempts_s"],right["update_attempts_s"]))
        if not rate or abs(tested["update_attempts_s"]/rate-1)>.03: continue
        control_drift=(max(left["median_update_cpu_ms"],right["median_update_cpu_ms"])/
                       min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])-1
                       if min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])>0 else 1)
        if control_drift>.05: continue
        control_cpu=statistics.mean((by_name[before]["median_update_cpu_ms"],by_name[after]["median_update_cpu_ms"]))
        tested_cpu=by_name[name]["median_update_cpu_ms"]
        if control_cpu>0:
            candidates.append({"intervention":label,"signal":"CPU ms/frame",
                "estimated_reduction_fraction":round(1-tested_cpu/control_cpu,4),
                "evidence":"causal phase bracketed by adjacent A controls"})
    if all(key in by_name for key in ("C","A1","A2")):
        left,right,tested=by_name["A1"],by_name["A2"],by_name["C"]
        cadence=statistics.mean((left["update_attempts_s"],right["update_attempts_s"]))
        drift=(max(left["median_update_cpu_ms"],right["median_update_cpu_ms"])/
               min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])-1
               if min(left["median_update_cpu_ms"],right["median_update_cpu_ms"])>0 else 1)
        if cadence and abs(tested["update_attempts_s"]/cadence-1)<=.03 and drift<=.05:
            control=statistics.mean((left["median_update_cpu_ms"],right["median_update_cpu_ms"]))
            if control>0:
                candidates.append({"intervention":"reduce graphics draw submission path",
                    "signal":"A−C CPU ms per update","estimated_reduction_fraction":round(1-tested["median_update_cpu_ms"]/control,4),
                    "evidence":"draw suppression under matched update cadence; includes driver queue/wait effects and does not directly equal achievable savings"})
    gpu_phases=gpu.get("phases",{})
    if all(str(PHASE_NUMBER[name]) in gpu_phases for name in ("A2","D","A3")):
        gpu_rows=[gpu_phases[str(PHASE_NUMBER[name])] for name in ("A2","D","A3")]
        if any(row.get("context_count")!=1 or row.get("median_whole_render_ms") is None
               for row in gpu_rows):
            gpu_rows=[]
    else:
        gpu_rows=[]
    if gpu_rows:
        reference=statistics.mean((gpu_phases[str(PHASE_NUMBER["A2"])]["median_whole_render_ms"],
                                   gpu_phases[str(PHASE_NUMBER["A3"])]["median_whole_render_ms"]))
        tested=gpu_phases[str(PHASE_NUMBER["D"])]["median_whole_render_ms"]
        if reference>0:
            candidates.append({"intervention":"reduce raster work or cache static map layers",
                "signal":"GPU ms/render","estimated_reduction_fraction":round(1-tested/reference,4),
                "evidence":"asynchronous GPU timestamps bracketed by A controls"})
    topology=temporal.get("combined_command_state_resource",{}).get("identical_consecutive_fraction")
    # Keep the report focused on the strongest measured CPU/GPU effects, then
    # use temporal stability only when it supplies a distinct caching signal.
    measured=sorted((item for item in candidates if item["estimated_reduction_fraction"] is not None),
                    key=lambda item:item["estimated_reduction_fraction"],reverse=True)
    selected=measured[:2]
    return selected or [{"intervention":"no architectural recommendation yet",
                         "signal":"coverage or causal phases incomplete",
                         "estimated_reduction_fraction":None,
                         "evidence":"review report limitations and missing timing samples"}]


def render_report(data: dict) -> str:
    lines=["# Paused EU IV frame model", "", f"Run status: {data['status']}; report kind: {data.get('report_kind','legacy')}.", "",
           f"Historical process CPU reference: {data['historical_process_cpu_ms_per_swap']:.2f} ms/swap.",
           "", "Context/pass GPU timeline segments appear when supported by the active CGL context; missing results leave GPU accounting partial.",
           "", "## Phase accounting", "",
           "| Phase | Updates/s | Render attempts/s | Executed renders/s | Presents/s | Loop wall ms | Loop CPU ms | Update CPU ms | Render CPU/update ms | Render CPU/executed ms | Present wall ms | Flush wall ms | Draws/update | Triangles est. | Bucket appends | Flags |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for p in data["phases"]:
        if p["status"]!="complete":
            lines.append(f"| {p['phase']} | — | — | — | — | — | — | — | — | — | — | — | — | — | — | missing |")
            continue
        render_per_frame=(f"{p['mean_render_cpu_ms_per_executed_render']:.3f}"
                          if p['mean_render_cpu_ms_per_executed_render'] is not None else "—")
        lines.append(f"| {p['phase']} | {p['update_attempts_s']:.2f} | {p['render_attempts_s']:.2f} | {p['executed_renders_s']:.2f} | {p['present_calls_s']:.2f} | {p['median_loop_wall_ms']:.3f} | {p['median_loop_cpu_ms']:.3f} | {p['median_update_cpu_ms']:.3f} | {p['median_render_cpu_ms']:.3f} | {render_per_frame} | {p['median_present_wall_ms']:.3f} | {p['median_cglflush_wall_ms']:.3f} | {p['median_draws']:.0f} | {p['median_triangles_est']:.0f} | {p['median_flush_records']:.0f} | {p['record_flags']} |")
    normal=[p for p in data["phases"] if p["phase"] in {name for name,*_ in PHASES if name.startswith("A")} and p["status"]=="complete"]
    if normal:
        lines.extend(["","## Exclusive CPU and wall cost tree", "",
                      "Wall and thread CPU remain separate. Root scopes do not count as explained cost; residuals and coverage are emitted per phase.",
                      json.dumps(data.get("exclusive_cost_tree",{}),indent=2,sort_keys=True)])
    if data.get("release_blockers"):
        lines.extend(["", "## Release blockers", ""])
        lines.extend(f"- {name}: {entry['reason']} ({entry['status']})" for name,entry in data["release_blockers"].items())
    lines.extend(["","## Ranked envelope residuals","",
        "| Phase | Envelope path | Residual CPU ms | Residual wall ms | Non-CPU elapsed ms |",
        "|---|---|---:|---:|---:|"])
    for r in data.get("exclusive_cost_tree",{}).get("ranked_residuals",[]):
        lines.append(f"| {r['phase']} | {' → '.join(r['scope_path'])} | {r['residual_cpu_ms']:.3f} | {r['residual_wall_ms']:.3f} | {r['non_cpu_elapsed_ms']:.3f} |")
    lines.extend(["", "## Per-frame graphics work", "",
                  "| Phase | Buffer calls | Buffer bytes | Buffer upload bytes | Texture calls | Texture bytes est. | Uniform calls | Uniform bytes | State calls | Program switches | Bucket adds | Flush records |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for p in data["phases"]:
        if p["status"]!="complete":
            lines.append(f"| {p['phase']} | — | — | — | — | — | — | — | — | — | — | — |")
        else:
            lines.append(f"| {p['phase']} | {p['median_buffer_calls']:.0f} | {p['median_buffer_bytes']:.0f} | {p['median_buffer_upload_bytes']:.0f} | {p['median_texture_calls']:.0f} | {p['median_texture_bytes_est']:.0f} | {p['median_uniform_calls']:.0f} | {p['median_uniform_bytes']:.0f} | {p['median_state_calls']:.0f} | {p['median_program_switches']:.0f} | {p['median_bucket_calls']:.0f} | {p['median_flush_records']:.0f} |")
    lines.extend(["", "## Detailed samples", "",
                  json.dumps(data["sampled_trace_rows"], sort_keys=True), "",
                  "## Hook totals and validation", "",
                  f"Update/render/present totals: `{data.get('update_render_present_totals',{})}`; hook failures: `{data.get('hook_failures')}`.", "",
                  json.dumps(data.get("hook_totals",{}),indent=2,sort_keys=True), "",
                  f"Engine inventory: `{data.get('engine_inventory','unavailable')}`.", "",
                  "## Power and frequency by phase", "",
                  json.dumps(data.get("power_by_phase", {}), indent=2, sort_keys=True), "",
                  "## Render cadence and power scaling", "",
                  json.dumps(data.get("power_fps_scaling",{}),indent=2,sort_keys=True), "",
                  "## Draw API coverage", "", json.dumps(data.get("draw_api_coverage",{}),indent=2), "",
                  data.get("C_intervention","partial draw-suppression intervention"), "",
                  "## Asynchronous GPU timing", "",
                  json.dumps(data.get("gpu_by_phase", {}), indent=2, sort_keys=True), "",
                  "## Consecutive-frame changes", "",
                  json.dumps(data.get("temporal_differences", {}), indent=2, sort_keys=True), "",
                  f"Static backend graph: `{data.get('backend_graph', 'unavailable')}`.", "",
                  "## Causal findings", "",
                  "### Bracketed contrasts", "",
                  json.dumps(data.get("causal_contrasts",{}),indent=2,sort_keys=True), ""])
    lines.extend(f"- {item}" for item in data["conclusions"])
    lines.extend(["", "## Recommended next interventions", ""])
    lines.extend(f"- **{item['intervention']}** — {item['signal']}: {item.get('estimated_reduction_fraction')}; {item['evidence']}"
                 for item in data.get("recommendations",[]))
    lines.extend(["", "## Coverage and limitations", "",
                  "The hook library suppresses six draw APIs and separately observes candidate submission paths from the executable/SDK manifest. Missing observers or resolver paths block complete C acceptance. Global hook totals are partial supporting telemetry. It counts common depth/blend/stencil/raster/viewport/scissor/texture-parameter state calls, selected resource binds/uploads, and observed scalar/vector/matrix float/int uniform families. Texture byte counts are estimates; PBO sources, mapped writes, unsigned/double/non-square uniforms, resource create/delete lifecycle, culling/LOD decisions, bucket sorting, buffer changed-byte ranges, and other draw entry points remain uncovered.",
                  "", "Sampled draw records include API, callsite, program, a VAO/enabled-attribute source signature, element buffer, mode, count, index type/offset or first vertex, signed base vertex, and instance count. Sampled uniform calls retain callsite/program/location/size/hash; consecutive `glUniform4fv` payloads up to 256 bytes report changed 32-bit elements. C requires complete draw API observer evidence, no submissions outside the suppression set in its baselines, positive suppression and zero forwarded covered draws. D uses `GL_RASTERIZER_DISCARD` around `Render()` and requires clean state flags.",
                  "", "Engine scopes emit actual parent, inclusive and exclusive wall/CPU, and per-update totals. Only verified semantic exclusive intervals count toward coverage; all envelope exclusive time remains residual. Pacing is separate. Update loop wall includes pacing sleep. `PresentScene` and `CGLFlushDrawable` are distinct. Render CPU is shown both per update and per executed render.",
                  "", "Frame flags: 1 frame queue overflow, 2 update cadence overrun, 4 unknown texture byte estimate, 8 unavailable raster-suppression state API/context, 16 restoration failure, 32 raster context capacity exceeded, 64 GPU query slot unavailable, 128 unknown primitive topology, 512 scope-stack overflow, 1024 scope mismatch/child-time inconsistency, and 2048 incomplete GL shadow identity (see L reason; structural claims excluded, CPU scopes retained), 4096 measurement/mode boundary crossed.",
                  "", "GPU timestamps stay segmented by CGL context and are not combined across contexts. They represent same-context GPU timeline intervals, not guaranteed busy time. The static backend graph remains a candidate mapping until implementation and resource/state dependencies are confirmed.", ""])
    return "\n".join(lines)


class SharedControl:
    def __init__(self, path: Path):
        self.path=path
        self.file=path.open("r+b")
        self.map=mmap.mmap(self.file.fileno(),CONTROL_SIZE)
        values=[FORMAT_VERSION]+[0]*13
        self.map[:CONTROL.size]=CONTROL.pack(*values)
        self.generation=0
        self.command_seq=0
        self.measurement_epoch=0
        self.measuring=False
    def set(self,mode: str,phase: int,update_period_ns: int=0,
            render_period_ns: int=0,detail_frames: int=0,
            measurement: bool=True,forensic: bool=False) -> int:
        if measurement and not self.measuring: self.measurement_epoch+=1
        self.measuring=measurement
        self.generation+=1
        self.command_sent_ns=time.monotonic_ns()
        self.command_seq+=1
        odd=self.command_seq*2-1
        even=self.command_seq*2
        struct.pack_into("<I",self.map,4,odd)
        self.map.flush()
        flags=(1 if mode!="off" else 0) | (2 if measurement else 0) | (4 if forensic else 0)
        command=CONTROL_COMMAND.pack(FORMAT_VERSION,odd,MODE[mode],phase,flags,
                                     detail_frames,update_period_ns,render_period_ns,
                                     self.generation,self.measurement_epoch if measurement else 0)
        self.map[:4]=command[:4]
        self.map[8:CONTROL_COMMAND.size]=command[8:]
        struct.pack_into("<I",self.map,4,even)
        self.map.flush()
        return self.generation
    def acknowledged(self) -> int:
        return struct.unpack_from("<Q",self.map,56)[0]
    def hook_failures(self) -> int:
        return struct.unpack_from("<Q",self.map,64)[0]
    def dropped_records(self) -> int:
        return struct.unpack_from("<Q",self.map,72)[0]
    def ack_time(self) -> int:
        return struct.unpack_from("<Q",self.map,80)[0]
    def close(self) -> None:
        self.map.close(); self.file.close()


def _wait_ack(game: subprocess.Popen, control: SharedControl, generation: int,
              label: str, timeout: float=4.0) -> None:
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU IV exited during {label}")
        if control.acknowledged()>=generation:
            return
        time.sleep(.01)
    raise base.BenchmarkError(f"Profiler did not acknowledge {label} generation {generation}")


def _start_power(path: Path) -> subprocess.Popen:
    with path.open("wb") as output, path.with_suffix(".stderr").open("wb") as errors:
        process=subprocess.Popen(["sudo","-n",str(auto.HELPER_INSTALLED)],stdout=output,stderr=errors)
    time.sleep(2)
    if process.poll() is not None:
        raise base.BenchmarkError(f"Powermetrics helper failed: {path.with_suffix('.stderr').read_text(errors='replace')}")
    return process


def _set_power_mode(mode: str, journal: Path | None = None) -> dict:
    if mode not in ("normal", "low"):
        raise ValueError(mode)
    state=base.power_state()
    if state.get("source")!="AC Power":
        raise base.BenchmarkError("Low Power comparison requires AC power")
    active=base.command("pmset","-g")
    key="powermode" if "powermode" in active else "lowpowermode"
    value=("0" if mode=="normal" else "1")
    if journal and not journal.exists():
        custom=base.command("pmset","-g","custom")
        section=None; previous_value=None
        for line in custom.splitlines():
            if line.strip()=="AC Power:": section="AC Power"; continue
            if line.endswith(" Power:"): section=line.strip()[:-1]
            if section=="AC Power":
                match=re.match(r"\s*"+key+r"\s+(\d+)",line)
                if match: previous_value=match.group(1)
        if previous_value is None:
            raise base.BenchmarkError(f"Could not read previous AC {key} value")
        entry={"previous":state,"setting":key,"value":previous_value,
               "recorded_at":dt.datetime.now(dt.timezone.utc).isoformat()}
        journal.write_text(json.dumps(entry,indent=2)+"\n")
    result=subprocess.run(["sudo","-n",str(POWER_HELPER_INSTALLED),key,value],
                          capture_output=True,text=True,check=False,timeout=10)
    if result.returncode:
        raise base.BenchmarkError(f"Could not set {mode} power mode: {result.stderr.strip()}")
    observed=base.power_state()
    if observed.get("mode")!=mode:
        raise base.BenchmarkError(f"Power mode transition requested {mode}, observed {observed.get('mode')}")
    return {"setting":key,"requested":mode,"observed":observed}


def _restore_power_mode(journal: Path) -> dict:
    if not journal.is_file():
        raise base.BenchmarkError("Power-mode recovery journal is missing")
    saved=json.loads(journal.read_text())
    key,value=saved.get("setting"),saved.get("value")
    if key not in ("powermode","lowpowermode") or value not in ("0","1","2"):
        raise base.BenchmarkError("Power-mode recovery journal has invalid setting data")
    result=subprocess.run(["sudo","-n",str(POWER_HELPER_INSTALLED),key,value],
                          capture_output=True,text=True,check=False,timeout=10)
    if result.returncode:
        raise base.BenchmarkError(f"Could not restore the saved AC power mode: {result.stderr.strip()}")
    observed=base.power_state()
    expected=saved["previous"].get("mode")
    if observed.get("mode")!=expected:
        raise base.BenchmarkError(f"Power-mode restore expected {expected}, observed {observed.get('mode')}")
    return {"setting":key,"value":value,"observed":observed}


def _power_helper_preflight() -> dict:
    if (not POWER_HELPER_INSTALLED.is_file() or POWER_HELPER_INSTALLED.stat().st_uid!=0 or
        POWER_HELPER_INSTALLED.stat().st_mode&0o022 or
        base.sha256(POWER_HELPER_INSTALLED)!=base.sha256(POWER_HELPER_SOURCE)):
        raise base.BenchmarkError("Install the reviewed allowlisted power-mode helper before the LPM comparison")
    key="powermode" if "powermode" in base.command("pmset","-g") else "lowpowermode"
    values=("0","1","2") if key=="powermode" else ("0","1")
    checks=[]
    for value in values:
        result=subprocess.run(["sudo","-n","-l",str(POWER_HELPER_INSTALLED),key,value],
                              capture_output=True,text=True,check=False)
        checks.append({"setting":key,"value":value,"authorized":result.returncode==0})
    if not all(row["authorized"] for row in checks):
        raise base.BenchmarkError("Passwordless authorization is missing for one or more exact power-mode helper commands")
    return {"status":"ready","setting":key,"commands":checks}


def render_identity(frame):
    return {key: frame.get(key) for key in
            ("phase", "measurement_epoch", "thread_id", "generation", "update_id", "render_id")}


def sampled_perturbation(frames, windows=6, consecutive=4):
    """Qualify a single render population and complete local paired windows."""
    participating = sorted((f for f in frames if f.get("render_executed", 0) or f.get("sample_window")),
                           key=lambda f: f["render_id"])
    populations = {(f.get("phase"), f.get("measurement_epoch"), f.get("thread_id")) for f in participating}
    if len(populations) != 1 or any(f.get("render_executed") != 1 for f in participating):
        return {"status":"unavailable", "reason":"unsupported render population; require one phase/epoch/thread and one render per frame"}
    groups = {}
    for f in participating:
        if f.get("sample_window"): groups.setdefault(f["sample_window"], []).append(f)
    if len(groups) != windows:
        return {"status":"unavailable", "reason":"complete sampled window count differs", "windows":len(groups)}
    evidence = []
    for identity, group in sorted(groups.items()):
        first = participating.index(group[0]); last = participating.index(group[-1])
        before = participating[max(0, first-consecutive):first]
        after = participating[last+1:last+1+consecutive]
        if len(group) != consecutive:
            return {"status":"unavailable", "reason":"sampled renders are incomplete or nonconsecutive", "window":identity}
        if len(before) != consecutive or len(after) != consecutive or any(f.get("sample_window") for f in before+after):
            return {"status":"unavailable", "reason":"full unsampled neighbors missing", "window":identity}
        local = before+group+after
        if any(b["render_id"] != a["render_id"]+1 for a,b in zip(local,local[1:])):
            return {"status":"unavailable", "reason":"sampled renders or neighbors are nonconsecutive", "window":identity}
        ratios, absolute = {}, {}
        for field in ("update_cpu_ns", "update_wall_ns", "render_cpu_ns", "render_wall_ns"):
            reference = statistics.median(f[field] for f in before+after)
            if reference <= 0: return {"status":"unavailable", "reason":"nonpositive neighbor cost", "window":identity}
            delta = statistics.median(f[field] for f in group)-reference
            ratios[field], absolute[field] = delta/reference, delta/1000
        evidence.append({"sample_window":identity, "renders":[f["render_id"] for f in group],
                         "sampled":[render_identity(f) for f in group],
                         "before":[render_identity(f) for f in before], "after":[render_identity(f) for f in after],
                         "fractions":ratios, "overhead_us_per_frame":absolute})
    passed = all(abs(value)<=.05 for w in evidence for value in w["fractions"].values())
    return {"status":"passed" if passed else "failed", "reason":"each window's CPU/wall update/render medians must remain within 5% of local neighbors", "windows":evidence}


def _wait_phase(game: subprocess.Popen, duration: int, control: SharedControl,
                events: Path, name: str, mode: str, cadence: int,
                detail: bool=False, render_period_ns: int=0,
                detail_windows: int=2,detail_frames_per_window: int=2) -> dict:
    if time.monotonic()+duration+17>getattr(control,"deadline",float("inf")):
        raise base.BenchmarkError("Controller deadline leaves insufficient measurement and restoration time")
    generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,0,False,detail)
    auto.mark(events,"phase_transition",phase=name,mode=mode,generation=generation)
    _wait_ack(game,control,generation,name+" transition")
    time.sleep(5)
    generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,0,True,detail)
    measurement_generation=generation
    _wait_ack(game,control,generation,name+" measurement enable")
    received_ns=time.monotonic_ns()
    ack_ns=control.ack_time()
    lower,upper=control.command_sent_ns-ack_ns,received_ns-ack_ns
    clock_offset=(lower+upper)//2
    uncertainty=max(abs(lower-clock_offset),abs(upper-clock_offset))
    epoch=control.measurement_epoch
    start=auto.mark(events,"phase_start",phase=name,measurement_epoch=epoch)
    auto.mark(events,"measurement_enabled",phase=name,generation=generation)
    # A post-start command lets unowned producers distinguish the timed window
    # from enable acknowledgement, without changing the measurement epoch.
    generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,0,True,detail)
    window_generation=generation
    measurement_started=start["monotonic_ns"]/1e9
    end_by=measurement_started+duration
    reader=TelemetryReader(events.parent/"telemetry.csv")
    window={"name":name,"measurement_epoch":epoch,"start_ns":start["monotonic_ns"],
            "end_ns":int(end_by*1e9),"clock_offset_ns":clock_offset,"alignment_uncertainty_ns":uncertainty}
    observed=[]; arms=[]; completed=0
    requested=[measurement_started+duration*i/(detail_windows+1) for i in range(1,detail_windows+1)] if detail else []
    last_focus=measurement_started-1
    while time.monotonic()<end_by:
        if game.poll() is not None: raise base.BenchmarkError(f"EU IV exited during {name}")
        now=time.monotonic()
        if now-last_focus>=1:
            if not auto.focus("interior",game.pid): raise base.BenchmarkError(f"EU IV lost focus during {name}")
            last_focus=now
        observed.extend(f for f in reader.poll() if eligible_frame(f,window))
        if arms and completed<len(arms):
            result=sampled_perturbation(observed,len(arms),detail_frames_per_window)
            if result["status"] in {"passed","failed"}: completed=len(arms)
        if detail and len(arms)<detail_windows and now>=requested[len(arms)] and completed==len(arms):
            plain=[f for f in observed if f.get("render_executed") and not f.get("sample_window")]
            preceding=plain[-detail_frames_per_window:]
            ready=(len(preceding)==detail_frames_per_window and
                   len({(f["phase"],f["measurement_epoch"],f["thread_id"]) for f in preceding})==1 and
                   all(f["render_executed"]==1 for f in preceding) and
                   all(b["render_id"]==a["render_id"]+1 for a,b in zip(preceding,preceding[1:])))
            if ready:
                generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,
                    detail_frames_per_window,True,detail)
                arm={"requested_arm_ns":int(requested[len(arms)]*1e9),"actual_arm_ns":control.command_sent_ns,
                     "generation":generation,"preceding_observed":[render_identity(f) for f in preceding]}
                arms.append(arm)
                auto.mark(events,"detail_sample_armed",phase=name,render_frames=detail_frames_per_window,window=len(arms),**arm)
        time.sleep(min(.05,max(0,end_by-time.monotonic())))
    end=auto.mark(events,"phase_end",phase=name)
    stop_generation=control.set(mode,0,cadence,render_period_ns,0,False)
    _wait_ack(game,control,stop_generation,name+" measurement stop")
    auto.mark(events,"measurement_disabled",phase=name)
    result={"name":name,"duration_s":(end["monotonic_ns"]-start["monotonic_ns"])/1e9,
            "requested_duration_s":duration,"start_ns":start["monotonic_ns"],
            "end_ns":end["monotonic_ns"],"settle_s":5,"mode":mode,
            "measurement_epoch":epoch,"clock_domain":"CLOCK_UPTIME_RAW calibrated to controller monotonic_ns by acknowledgement bracket",
            "clock_offset_ns":clock_offset,"alignment_uncertainty_ns":uncertainty,
            "ack_time_ns":ack_ns,"enable_generation":measurement_generation,"window_generation":window_generation,
            "update_period_ns":cadence,"render_period_ns":render_period_ns,
            "stop_generation":stop_generation,"sample_requests":arms}
    if detail:
        observed.extend(f for f in reader.poll() if eligible_frame(f,result))
        observed=[f for f in observed if eligible_frame(f,result)]
        result["sampled_window_evidence"]=sampled_perturbation(observed,detail_windows,detail_frames_per_window)
        if len(arms)!=detail_windows or result["sampled_window_evidence"]["status"]=="unavailable":
            auto.mark(events,"sample_calibration_failed",phase=name,evidence=result)
            raise base.BenchmarkError(f"{name} incomplete sample calibration: {result['sampled_window_evidence']}")
        actual={w["sample_window"] for w in result["sampled_window_evidence"]["windows"]}
        if name in {"P1","A0"} and result["sampled_window_evidence"]["status"]!="passed":
            raise base.BenchmarkError(f"{name} sampled perturbation gate failed: {result['sampled_window_evidence']}")
        if actual!={a["generation"] for a in arms}:
            raise base.BenchmarkError(f"{name} delayed or overwritten sample commands")
    return result


def causal_schedule(period: int, residual_discovery: bool=False) -> list[dict]:
    phases=(("A0","profile",20,False),) if residual_discovery else PHASES
    return [{"name":name,"mode":mode,"duration_s":duration,"detail":detail,
             "update_period_ns":period,"render_period_ns":int(1e9/30) if name=="E30" else
             int(1e9/15) if name=="E15" else 0} for name,mode,duration,detail in phases]


def run(output_root: Path, include_low_power: bool=True,
        include_fixed_cadence_lpm: bool=False, residual_discovery: bool=False,
        calibration_only: bool=False) -> Path:
    if residual_discovery and calibration_only:
        raise base.BenchmarkError("Choose either residual discovery or calibration-only mode")
    if residual_discovery or calibration_only:
        include_low_power=False; include_fixed_cadence_lpm=False
    try: budget=run_budget([] if calibration_only else causal_schedule(1,residual_discovery),
        include_low_power,include_fixed_cadence_lpm,residual_discovery or calibration_only)
    except ValueError as exc: raise base.BenchmarkError(str(exc)) from exc
    evidence=preflight(run_gl=True,require_privilege=True,require_power_mode=include_low_power)
    base.running_game_pid("idle")
    if not auto.SCENE.is_file() or not auto.SCENE_MANIFEST.is_file():
        raise base.BenchmarkError("Register the reviewed Venice scene before this diagnostic run")
    output_root.mkdir(parents=True,exist_ok=True)
    run_dir=output_root/f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-frame-model"
    run_dir.mkdir(parents=True,exist_ok=False)
    manifest_path=run_dir/"manifest.json"
    gates=GateEvidence()
    gates.record("format_v3","passed","Versioned producer and controller",version=FORMAT_VERSION)
    causal=evidence.get("offline_causal_admission") or {}
    forensic=evidence.get("offline_forensic_suitability") or {}
    causal_status=causal.get("status")
    forensic_status=forensic.get("status")
    gates.record(
        "offline_causal_admission",
        "passed" if causal_status == "passed" else "failed" if causal_status else "unavailable",
        "Tier-1 offline causal admission (reference + counters + held-out)",
        evidence=causal,
    )
    gates.record(
        "offline_forensic_suitability",
        "passed" if forensic_status == "passed" else "failed" if forensic_status else "unavailable",
        "Forensic sampled/ablation suitability (non-blocking for calibration-only)",
        evidence=forensic,
    )
    gates.record(
        "offline_overhead",
        "passed" if causal_status == "passed" else "failed" if causal_status else "unavailable",
        "Deprecated alias of offline_causal_admission",
        evidence=causal_status,
    )
    report_kind="calibration_only" if calibration_only else "residual_discovery" if residual_discovery else "causal"
    manifest={"gates":gates.entries,"worst_case_budget_s":budget,"format_version":FORMAT_VERSION,
              "report_kind":report_kind,
              "status":"starting","executable_sha256":evidence["build"]["executable_sha256"],
              "evidence":evidence,"coverage":{"engine_hooks":7,"suppressed_gl_draw_apis":6,
              "draw_candidate_entrypoints":len(evidence["build"]["draw_api_manifest"]["apis"]),
              "draw_observer_status":evidence["build"]["draw_api_manifest"]["status"],
              "global_hook_totals":"partial supporting telemetry",
              "gpu_timing":"sampled asynchronous context/pass timestamp segments when supported","exact_payload_diff":"sampled uniform4fv element-level; buffer uploads hash-level"},
              "limitations":["GPU timer query support depends on the active CGL context; absent results are reported as unavailable.",
                  "Thread CPU is measured on the UpdateOneFrame thread; worker-thread totals are outside the per-frame tree.",
                  "Scene membership, visibility decisions, LOD, sorting, and allocation counts are not instrumented.",
                  "Exact uniform4fv element changes cover sampled consecutive payloads up to 256 bytes; buffer payloads remain hash-level.",
                  "The backend graph maps Gfx sites to candidate GL calls; resource/state dependencies need runtime or implementation analysis."]}
    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
    events=run_dir/"events.jsonl"
    anchor=auto.mark(events,"clock_anchor")
    manifest["clock_anchor"]=anchor
    manifest["power_window_policy"]="Power samples use the same controller phase start/end timestamps as frame/event analysis"
    control_path=run_dir/"control.bin"
    control_path.write_bytes(bytes(CONTROL_SIZE))
    game=pm=None; control=None; installed=False; low_power_active=False
    power_journal=run_dir/"power-mode-journal.json"; power_tail=None
    try:
        with FixtureManager(output_root,base.USER_DATA,auto.FIXTURE,
                            evidence["autonomous"]["fixture"]["save_sha256"]) as fixture:
            try:
                fixture.recover(); working=fixture.install(); installed=True
                manifest["working_save"]=str(working)
                control=SharedControl(control_path)
                old_log=(base.USER_DATA/"logs/game.log").read_bytes() if (base.USER_DATA/"logs/game.log").is_file() else b""
                pm=_start_power(run_dir/"powermetrics.pliststream")
                control.deadline=time.monotonic()+RUN_DEADLINE_SECONDS
                # The frame-model library also emits the autonomous readiness
                # probe so only one CGLFlushDrawable interposer owns present IDs.
                libraries=str(LIBRARY)
                env={**os.environ,"DYLD_INSERT_LIBRARIES":libraries,
                     "EU4_AUTO_PROBE_LOG":str(run_dir/"auto-probe.csv"),
                     "EU4_FRAME_MODEL_LOG":str(run_dir/"telemetry.csv"),
                     "EU4_FRAME_MODEL_CONTROL":str(control_path)}
                with (run_dir/"game.stdout").open("wb") as stdout,(run_dir/"game.stderr").open("wb") as stderr:
                    game=subprocess.Popen(["./eu4",*evidence["autonomous"]["launcher_args"],"--continuelastsave"],
                        cwd=base.GOG_EXE.parent,env=env,stdout=stdout,stderr=stderr)
                manifest["game_pid"]=game.pid
                power_tail=auto.PowerTail(run_dir/"powermetrics.pliststream")
                ready=auto.wait_until_ready(game,run_dir/"auto-probe.csv",power_tail,anchor,
                    base.USER_DATA/"logs/game.log",old_log)
                if base.running_game_pid("paused")!=game.pid:
                    raise base.BenchmarkError("The launched GOG process is not verified paused")
                display=base.display_mode()
                if not display.get("verified") or abs(display.get("refresh_hz",0)-120)>1:
                    raise base.BenchmarkError("EU IV is not in the verified 120 Hz display mode")
                manifest["readiness"]=ready; manifest["display"]=display
                manifest["warmup"]=auto.warm_up(game,run_dir/"auto-probe.csv",power_tail,anchor)
                auto.capture_scene(run_dir/"ready-scene.png")
                manifest["scene_alignment"]=auto.verify_scene(run_dir/"ready-scene.png",False)

                calibration_phases=[]
                for name,mode,detail in (("P0","reference",False),("P1","profile",False),("P2","reference",False)):
                    calibration_phases.append(_wait_phase(game,15,control,events,name,mode,0,
                        detail=detail))
                manifest["calibration"]={"phases":calibration_phases}
                manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
                profile_rows=frame_rows(run_dir/"telemetry.csv")
                p1_phase=next(item for item in calibration_phases if item["name"]=="P1")
                p1=phase_summary(profile_rows,"P1",p1_phase["duration_s"],p1_phase,read_rows(run_dir/"telemetry.csv"))
                if p1["status"]!="complete" or p1["median_update_cpu_ms"]<=0 or p1["median_render_wall_ms"]<=0:
                    raise base.BenchmarkError("Profiler hooks did not record live update/render scopes")
                reference_summaries=[phase_summary(profile_rows,p["name"],p["duration_s"],p,
                    read_rows(run_dir/"telemetry.csv")) for p in calibration_phases if p["name"] in {"P0","P2"}]
                frame_perturbation={}
                for field in ("median_update_cpu_ms","mean_render_cpu_ms_per_executed_render","update_attempts_s","executed_renders_s","present_calls_s"):
                    values=[p.get(field) for p in reference_summaries]
                    if len(values)!=2 or any(v is None or v<=0 for v in values):
                        raise base.BenchmarkError(f"Reference timing unavailable for {field}")
                    frame_perturbation[field]=p1[field]/statistics.mean(values)-1
                if any(abs(v)>.03 for v in frame_perturbation.values()):
                    raise base.BenchmarkError(f"Frame-normalized counters perturbation failed: {frame_perturbation}")
                probe=auto.probe_rows(run_dir/"auto-probe.csv",anchor)
                power_tail.poll()
                calibration_power=diagnostic.summarize_power(power_tail.samples,calibration_phases,anchor,game.pid)
                cpu_rates=[calibration_power.get(name,{}).get("eu4_cputime_ms_per_s") for name in ("P0","P1","P2")]
                if any(value is None for value in cpu_rates):
                    raise base.BenchmarkError("Power samples do not cover the three profiler calibration phases")
                cpu_perturbation=cpu_rates[1]/statistics.mean((cpu_rates[0],cpu_rates[2]))-1
                if abs(cpu_perturbation)>.03:
                    raise base.BenchmarkError(f"Counters-only profiler CPU perturbation is {cpu_perturbation:+.1%}; stop before causal phases")
                measured_rate=statistics.median(row["swaps_s"] for row in probe
                    if calibration_phases[1]["start_ns"]<=row["monotonic_ns"]<calibration_phases[1]["end_ns"])
                controls=[]
                for item in (calibration_phases[0],calibration_phases[2]):
                    values=[row["swaps_s"] for row in probe if item["start_ns"]<=row["monotonic_ns"]<item["end_ns"]]
                    if values: controls.append(statistics.median(values))
                if len(controls)!=2: raise base.BenchmarkError("Swap probe missed a profiler control phase")
                swap_perturbation=measured_rate/statistics.mean(controls)-1
                if abs(swap_perturbation)>.03:
                    raise base.BenchmarkError(f"Counters-only profiler swap-rate perturbation is {swap_perturbation:+.1%}; stop before causal phases")
                historical=json.loads((ROOT/"results/autonomous-reproducibility.json").read_text())
                reference_rate=historical["summary"]["median_swaps_s"]["median"]
                if abs(measured_rate/reference_rate-1)>.03:
                    raise base.BenchmarkError("Profiler cadence differs from the established native swap rate by over 3%")
                gates.record("live_counters","passed","Calibrated process CPU and present cadence",cpu=cpu_perturbation,swaps=swap_perturbation)
                baseline_period=int(1e9/max(p1["update_attempts_s"],1))
                manifest["calibration"]={"phases":calibration_phases,"profile":p1,"power":calibration_power,
                    "frame_perturbation_fraction":frame_perturbation,"cpu_perturbation_fraction":cpu_perturbation,
                    "swap_rate":measured_rate,"historical_swap_rate":reference_rate,"target_cadence_ns":baseline_period,
                    "limits":{"counters_cpu_frame":.03,"swap_rate":.03}}
                if p1["median_update_cpu_ms"]>p1["median_update_wall_ms"]*1.05:
                    raise base.BenchmarkError("Thread CPU exceeds update wall time; timing is inconsistent")

                phases=[] if calibration_only else [_wait_phase(game,20,control,events,"ANATIVE","profile",0)]
                manifest["phases"]=phases
                for config in (() if calibration_only else causal_schedule(baseline_period,residual_discovery)):
                    name,mode=config["name"],config["mode"]
                    if name=="C":
                        raw=frame_rows(run_dir/"telemetry.csv")
                        qualified=[f for w in phases for f in raw if eligible_frame(f,w)]
                        check=draw_api.coverage(evidence["build"]["draw_api_manifest"],qualified,read_rows(run_dir/"telemetry.csv"),
                            {PHASE_NUMBER[w["name"]] for w in phases},require_c=False)
                        gates.record("draw_api_coverage",check["status"],check["reason"],evidence=check)
                        if check["status"]!="passed": raise base.BenchmarkError(f"Draw API coverage blocks C: {check}")
                    phase=_wait_phase(game,config["duration_s"],control,events,name,mode,
                        config["update_period_ns"],detail=config["detail"],render_period_ns=config["render_period_ns"])
                    phases.append(phase)
                    observed=phase_summary(frame_rows(run_dir/"telemetry.csv"),name,phase["duration_s"],phase,read_rows(run_dir/"telemetry.csv"))
                    if observed["status"]!="complete": raise base.BenchmarkError(f"No frame records in {name}")
                    unknown=unassociated_origins(read_rows(run_dir/"telemetry.csv"),[phase])
                    if unknown and not residual_discovery:
                        gates.record("origin_integrity","failed","Unassociated measured calls",records=unknown)
                        raise base.BenchmarkError(f"Unassociated calls block causal interventions in {name}")
                    if observed.get("invalid_scope_frames"):
                        raise base.BenchmarkError(f"Scope integrity failed in {name}")
                    if name in {"A0","A1","A2","A3","A4","A5","B","C","D","E30","E15"}:
                        update_delta=abs(observed["update_attempts_s"]/p1["update_attempts_s"]-1)
                        if update_delta>.03:
                            raise base.BenchmarkError(f"{name} changed update cadence by {update_delta:.1%}; its causal comparison is invalid")
                    if name in {"E30","E15"} and abs(observed["executed_renders_s"]/(30 if name=="E30" else 15)-1)>.03:
                        raise base.BenchmarkError(f"{name} did not achieve its render-rate target within 3%")
                    if name=="B" and (observed["total_executed_renders"]!=0 or
                                       observed["total_present_scene_calls"]!=0):
                        raise base.BenchmarkError("B did not skip Render/Present")
                    if name=="C" and (observed["total_suppressed_draws"]==0 or observed["total_forwarded_draws"]!=0):
                        raise base.BenchmarkError("C did not suppress every covered draw call")
                    if name=="C":
                        raw=frame_rows(run_dir/"telemetry.csv")
                        qualified=[f for w in phases for f in raw if eligible_frame(f,w)]
                        check=draw_api.coverage(evidence["build"]["draw_api_manifest"],qualified,read_rows(run_dir/"telemetry.csv"),
                            {PHASE_NUMBER[w["name"]] for w in phases})
                        gates.record("draw_api_coverage",check["status"],check["reason"],evidence=check)
                        if check["status"]!="passed": raise base.BenchmarkError(f"C is partial draw suppression: {check}")
                    if name=="D" and any(flags&(8|16|32) for flags in observed["record_flags"]):
                        raise base.BenchmarkError("D raster-work suppression state/context restoration failed")
                    if name.startswith("A") and observed["median_draws"]==0:
                        raise base.BenchmarkError(f"Control phase {name} had no observed draws")
                    manifest["phases"]=phases
                    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
                    if name.startswith("A") and not residual_discovery:
                        raw=frame_rows(run_dir/"telemetry.csv")
                        rows=eligible_trace(read_rows(run_dir/"telemetry.csv"),raw,[phase])
                        tree=scope_tree_summary(rows,[phase])["phases"][str(PHASE_NUMBER[name])]
                        if control.hook_failures() or control.dropped_records() or observed.get("invalid_scope_frames") or tree["coverage_95_percent_gate"]!="passed":
                            raise base.BenchmarkError("Baseline integrity/semantic CPU and wall coverage gates block causal interventions; use --residual-discovery to expand attribution")
                manifest["phases"]=phases
                if include_low_power:
                    low={"phases":[],"transition_settle_s":10}
                    low["phases"].append(_wait_phase(game,15,control,events,"LPM0","profile",0))
                    try:
                        low["low_mode"]=_set_power_mode("low",power_journal)
                    finally:
                        low_power_active=power_journal.is_file()
                    auto.mark(events,"power_mode_transition",mode="low"); time.sleep(10)
                    low["phases"].append(_wait_phase(game,30,control,events,"LPM","profile",0))
                    low["normal_mode"]=_restore_power_mode(power_journal)
                    low_power_active=False
                    auto.mark(events,"power_mode_transition",mode="normal"); time.sleep(10)
                    low["phases"].append(_wait_phase(game,15,control,events,"LPM1","profile",0))
                    manifest["low_power_comparison"]=low
                    manifest["phases"].extend(low["phases"])
                    if include_fixed_cadence_lpm:
                        fixed={"cadence_policy":"baseline update cadence held in both power modes",
                               "phases":[]}
                        fixed["phases"].append(_wait_phase(game,15,control,events,
                            "LPMF0","profile",baseline_period))
                        try:
                            fixed["low_mode"]=_set_power_mode("low",power_journal)
                        finally:
                            low_power_active=power_journal.is_file()
                        auto.mark(events,"power_mode_transition",mode="low",comparison="fixed_cadence")
                        time.sleep(10)
                        fixed["phases"].append(_wait_phase(game,30,control,events,
                            "LPMF","profile",baseline_period))
                        fixed["normal_mode"]=_restore_power_mode(power_journal)
                        low_power_active=False
                        auto.mark(events,"power_mode_transition",mode="normal",comparison="fixed_cadence")
                        time.sleep(10)
                        fixed["phases"].append(_wait_phase(game,15,control,events,
                            "LPMF1","profile",baseline_period))
                        manifest["fixed_cadence_dvfs_comparison"]=fixed
                        manifest["phases"].extend(fixed["phases"])
                profiling_tail=_wait_phase(game,20 if residual_discovery or calibration_only else 30,
                    control,events,"TAIL","profile",baseline_period,True,
                    detail_windows=6,detail_frames_per_window=4)
                profiling_tail["role"]="forensic"
                manifest["profiling_tail"]=profiling_tail
                manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
                tail_frames=[row for row in frame_rows(run_dir/"telemetry.csv")
                             if eligible_frame(row,profiling_tail)]
                trace_evidence=sampled_perturbation(tail_frames)
                trace_perturbation=(max(abs(value) for window in trace_evidence.get("windows",[])
                    for value in window["fractions"].values()) if trace_evidence.get("windows") else None)
                forensic_status="passed" if trace_evidence["status"]=="passed" else "failed"
                gates.record("forensic_capture",forensic_status,
                    "Post-causal six-window capture intrusion and pairing diagnostic",
                    perturbation_fraction=trace_perturbation,evidence=trace_evidence)
                manifest["forensic_capture"]={"phase":"TAIL","role":"forensic",
                    "sampled_trace_perturbation_fraction":trace_perturbation,
                    "sampled_window_evidence":trace_evidence}
                hook_failures=control.hook_failures()
                dropped_records=control.dropped_records()
                integrity={"hook_failures":hook_failures,"dropped_records":dropped_records,
                           "status":"passed" if hook_failures==0 and dropped_records==0 else "failed"}
                manifest["probe_integrity"]=integrity
                gates.record("integrity",integrity["status"],"Hook installation and record retention",**{k:v for k,v in integrity.items() if k!="status"})
                if hook_failures or dropped_records:
                    raise base.BenchmarkError(f"Profiler integrity failed: {integrity}")
                auto.stop_process(pm,5); pm=None; power_tail.poll()
                all_phases=calibration_phases+manifest["phases"]+([manifest["profiling_tail"]] if "profiling_tail" in manifest else [])
                manifest["power_by_phase"]=diagnostic.summarize_power(power_tail.samples,all_phases,anchor,game.pid)
                unknown=unassociated_origins(read_rows(run_dir/"telemetry.csv"),all_phases)
                gates.record("origin_integrity","failed" if unknown else "passed","Origin ownership checked; unknown calls block diagnosis",records=unknown)
                gates.record("cadence","passed","All scheduled phases met the cadence checks")
                if not residual_discovery and not calibration_only:
                    gates.record("semantic_coverage","passed","Every A baseline passed independent Update and Render CPU/wall gates")
                    gates.record("interventions","passed","B/C/D and achieved E targets checked")
                    summaries=[phase_summary(frame_rows(run_dir/"telemetry.csv"),p["name"],p["duration_s"],p,read_rows(run_dir/"telemetry.csv")) for p in phases if p["name"] in {n for n,*_ in PHASES}]
                    contrasts=causal_contrasts(summaries)
                    drift=[contrasts[n].get("control_cpu_drift_fraction") for n in ("B","C","D","E30","E15")]
                    gates.record("control_drift","passed" if drift and all(d is not None and d<=.03 for d in drift) else "failed","Adjacent baseline CPU drift must be within 3%",fractions=drift)
                try:
                    gates.require(required_gates_for_report_kind(manifest["report_kind"]))
                except ValueError as exc:
                    raise base.BenchmarkError(str(exc)) from exc
                manifest["status"]="complete"
                manifest["power_mode_comparison"]="natural cadence completed" if include_low_power else "not requested"
                manifest["fixed_cadence_dvfs_status"]=(
                    "completed" if include_low_power and include_fixed_cadence_lpm else
                    "not requested")
                manifest["power"]={"samples":len(power_tail.samples)}
                (run_dir/"power.samples.json").write_text(json.dumps(power_tail.samples,default=str))
            finally:
                restore_failure=None
                if low_power_active:
                    try: manifest["low_power_restore"]=_restore_power_mode(power_journal)
                    except BaseException as restore_error:
                        restore_failure=restore_error
                        manifest["status"]="incomplete"
                        manifest["power_restore_error"]=str(restore_error)
                auto.stop_process(pm,3)
                if game is not None and game.poll() is None:
                    auto.focus("terminate",game.pid); auto.stop_process(game,10)
                if control: control.close()
                if installed:
                    manifest["working_save_changed"]=fixture.finish(run_dir)
                manifest_path.write_text(json.dumps(manifest,indent=2,default=str)+"\n")
                if manifest.get("status")=="complete":
                    analyze(run_dir)
                if restore_failure:
                    raise base.BenchmarkError(f"Failed to restore Normal power mode: {restore_failure}")
    except BaseException as exc:
        manifest["status"]="incomplete"; manifest["error"]=str(exc)
        gates.record("run_completion","failed",str(exc))
        manifest_path.write_text(json.dumps(manifest,indent=2,default=str)+"\n")
        analyze(run_dir)
        raise
    return run_dir


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    pf=sub.add_parser("preflight",help="pin/build the profiler and validate offline probes")
    pf.add_argument("--static-only",action="store_true")
    pf.add_argument("--require-power-helper",action="store_true")
    dm=sub.add_parser(
        "diagnostic-matrix",
        help="training-only offline workloads + A–F matrix; writes immutable evidence without held-out",
    )
    dm.add_argument(
        "--registry-json",
        action="store_true",
        help="print only immutable_evidence (manifest fields) instead of the full capture JSON",
    )
    run_parser=sub.add_parser("run",help="run the unattended paused causal experiment")
    run_parser.add_argument("--output",default=str(ROOT/"results"))
    run_parser.add_argument("--residual-discovery",action="store_true",help="ANATIVE plus one paced A measurement; omit interventions and power-mode transitions")
    run_parser.add_argument("--calibration-only",action="store_true",
        help="run bounded reference/counters/reference calibration and forensic capture, then stop before ANATIVE or interventions")
    run_parser.add_argument("--fixed-cadence-lpm",action="store_true",
        help="add a separately reported fixed-throughput Low Power DVFS comparison")
    report=sub.add_parser("report",help="regenerate a report from a captured run")
    report.add_argument("run_dir")
    recover=sub.add_parser("recover-power",help="restore the AC power setting from a run journal")
    recover.add_argument("journal")
    args=parser.parse_args()
    try:
        if args.command=="preflight":
            result=preflight(not args.static_only,args.require_power_helper)
            print(json.dumps(result,indent=2))
        elif args.command=="diagnostic-matrix":
            result=profiler_overhead_diagnosis()
            if args.registry_json:
                print(json.dumps(result.get("immutable_evidence") or {},indent=2))
            else:
                print(json.dumps(result,indent=2))
        elif args.command=="report":
            print(json.dumps(analyze(Path(args.run_dir).expanduser().resolve()),indent=2))
        elif args.command=="recover-power":
            _power_helper_preflight()
            print(json.dumps(_restore_power_mode(Path(args.journal).expanduser().resolve()),indent=2))
        else:
            print(run(Path(args.output).expanduser().resolve(),
                      include_fixed_cadence_lpm=args.fixed_cadence_lpm,residual_discovery=args.residual_discovery,
                      calibration_only=args.calibration_only))
    except (base.BenchmarkError,OSError,ValueError,subprocess.SubprocessError,KeyboardInterrupt) as exc:
        print(f"Error: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
