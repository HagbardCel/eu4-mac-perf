#!/usr/bin/env python3
"""One-launch causal frame accounting for the pinned GOG EU IV build."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mmap
import os
import re
import statistics
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import autonomous_runner as auto
import eu4_benchmark as base
import eu4_diagnostic as diagnostic
import eu4_engine_inventory as engine
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
POWER_HELPER_SOURCE = ROOT / "benchmark/power_mode_helper"
POWER_HELPER_INSTALLED = Path("/usr/local/libexec/eu4-power-mode")
CONTROL_SIZE = 4096
CONTROL = struct.Struct("<6I6Q")
CONTROL_COMMAND = struct.Struct("<6I3Q")
EXPECTED = engine.EXPECTED_SHA256
MODE = {"off": 0, "profile": 1, "skip_render": 2, "drop_draws": 3,
        "raster_suppress": 4, "cadence_30": 5, "cadence_15": 6}
PHASES = (("A0", "profile", 20, True), ("B", "skip_render", 20, False),
          ("A1", "profile", 20, False), ("C", "drop_draws", 20, True),
          ("A2", "profile", 20, False), ("D", "raster_suppress", 20, True),
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
                "render_executed", "present_scene_calls", "present_calls")
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
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Build failed: {result.stderr.strip()}")
    for path in (LIBRARY, HARNESS, ARB_HARNESS, DETOUR_HARNESS, RENDER_GATE_HARNESS):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")
    return {"executable_sha256": base.sha256(base.GOG_EXE),
            "engine_inventory_sha256": base.sha256(engine.OUTPUT),
            "backend_graph_sha256":base.sha256(engine.BACKEND_GRAPH),
            "engine_sites": len(json.loads(engine.OUTPUT.read_text())["hooks"]),
            "library_sha256": base.sha256(LIBRARY),
            "library_source_sha256": base.sha256(SOURCE),
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
        fields = [1,2,mode,1,3,0,0,0,1,0,0,0]
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


def preflight(run_gl: bool = True, require_privilege: bool = False) -> dict:
    if base.sha256(base.GOG_EXE) != EXPECTED:
        raise base.BenchmarkError("Installed GOG executable differs from the pinned v1.37.5 build")
    static = build()
    detour = offline_detour_harness()
    render_gate=offline_render_gate_harness()
    arb=offline_arb_harness()
    auto_evidence = auto.preflight(require_privilege=require_privilege)
    power_helper = _power_helper_preflight() if require_privilege else {"status":"not checked"}
    if not run_gl:
        return {"status": "static_ready", "build": static, "autonomous": auto_evidence,
                "detour_harness":detour,
                "render_gate_harness":render_gate,
                "arb_handle_harness":arb,
                "power_mode_helper":power_helper,
                "overhead_gate": "not measured", "live_hooks": "not installed in EU IV"}
    bare, wrapped = [], []
    for _ in range(3):
        bare.append(offline_harness(loops=12000))
        wrapped.append(offline_harness(LIBRARY, loops=12000))
    median_bare=statistics.median(item["elapsed_ns"] for item in bare)
    median_wrapped=statistics.median(item["elapsed_ns"] for item in wrapped)
    overhead = median_wrapped/median_bare-1 if median_bare else 1.0
    bare_cpu,wrapped_cpu=statistics.median([item["cpu_ns"] for item in bare]),statistics.median([item["cpu_ns"] for item in wrapped])
    cpu_overhead=wrapped_cpu/bare_cpu-1 if bare_cpu else 1.0
    if max(abs(overhead),abs(cpu_overhead)) > .03:
        raise base.BenchmarkError(f"Counters-only instrumentation adds wall {overhead:.1%}, thread CPU {cpu_overhead:.1%}; reduce it before a game run")
    return {"status": "ready", "build": static, "autonomous": auto_evidence,
            "detour_harness":detour,
            "render_gate_harness":render_gate,
            "arb_handle_harness":arb,
            "power_mode_helper":power_helper,
            "bare_ns": [item["elapsed_ns"] for item in bare],
            "counters_ns": [item["elapsed_ns"] for item in wrapped],
            "bare_cpu_ns": [item["cpu_ns"] for item in bare],
            "counters_cpu_ns": [item["cpu_ns"] for item in wrapped],
            "counters_overhead_fraction": round(overhead, 5),
            "counters_cpu_overhead_fraction": round(cpu_overhead,5),
            "overhead_gate": "passed", "limitations": [
                "Synthetic GL test validates draw interposition and overhead, not engine detours under Rosetta.",
                "Coarse asynchronous timestamp queries are sampled by render and map scope; compatibility varies by CGL context.",
                "Six draw APIs are wrapped; draw-range, multi-draw, and extension-specific entry points remain uncovered."]}


def read_rows(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    with path.open(newline="") as source:
        return [row for row in csv.reader(source) if row]


def frame_rows(path: Path) -> list[dict]:
    result = []
    for row in read_rows(path):
        if not row or row[0] != "F" or len(row) != len(FRAME_FIELDS)+1:
            continue
        try:
            result.append(dict(zip(FRAME_FIELDS, map(int, row[1:]))))
        except ValueError:
            continue
    return result


def phase_summary(rows: list[dict], name: str, seconds: float) -> dict:
    selected = [row for row in rows if row["phase"] == PHASE_NUMBER[name]]
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
              "record_flags": sorted({row["flags"] for row in selected})}
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
                combined_frames.setdefault(int(row[1]),[]).append((row[0],tuple(row[2:])))
            elif row[0]=="S" and len(row)>=5:
                state_frames.setdefault(int(row[1]),[]).append((int(row[2]),int(row[3]),int(row[4])))
                combined_frames.setdefault(int(row[1]),[]).append((row[0],tuple(row[2:])))
            elif row[0]=="U" and len(row)>=6:
                frame,key_program,location=int(row[1]),int(row[3]),int(row[4])
                size_hash=int(row[5]); byte_size=size_hash>>32; payload_hash=size_hash&0xffffffff
                key=(int(row[2]),key_program,location)
                uniform_frames.setdefault(frame,{})[key]=(byte_size,payload_hash)
                uniform_sequences.setdefault(frame,[]).append((key,(byte_size,payload_hash)))
                combined_frames.setdefault(frame,[]).append((row[0],tuple(row[2:])))
            elif row[0]=="u" and len(row)>=6:
                frame=int(row[1]); program=int(row[3]); location=int(row[4])
                key=(int(row[2]),program,location); value=(4,int(row[5]))
                uniform_frames.setdefault(frame,{})[key]=value
                uniform_sequences.setdefault(frame,[]).append((key,value))
                combined_frames.setdefault(frame,[]).append((row[0],tuple(row[2:])))
            elif row[0]=="V" and len(row)>=8:
                if len(uniform_exact)<512:
                    uniform_exact.append({"render_id":int(row[1]),"callsite_offset":int(row[2]),
                        "program_id":int(row[3]),"uniform_location":int(row[4]),
                        "byte_offset":int(row[5]),"element_index":int(row[5])//4,
                        "previous_u32_hex":f"0x{int(row[6]):08x}",
                        "current_u32_hex":f"0x{int(row[7]):08x}"})
                combined_frames.setdefault(int(row[1]),[]).append((row[0],tuple(row[2:])))
            elif row[0]=="W" and len(row)>=8:
                byte_size,changed,omitted=map(int,row[5:8])
                if omitted==1 and changed==0 and byte_size>256: uniform_oversize_calls+=1
                else:
                    uniform_compared_calls+=1
                    uniform_changed_elements+=changed
                    uniform_omitted_elements+=omitted
                combined_frames.setdefault(int(row[1]),[]).append((row[0],tuple(row[2:])))
            elif row[0] in ("T","t") and len(row)>=4:
                frame,site,dimensions=int(row[1]),int(row[2]),int(row[3])
                texture_frames.setdefault(frame,{})[(ord(row[0]),site)]=dimensions
                combined_frames.setdefault(frame,[]).append((row[0],tuple(row[2:])))
            elif row[0]=="B" and len(row)>=6:
                frame,key,size,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4])
                buffer_frames.setdefault(frame,{})[(ord("B"),key,0,size)]=digest
                combined_frames.setdefault(frame,[]).append((row[0],tuple(row[2:])))
            elif row[0]=="b" and len(row)>=6:
                frame,key,size,offset,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4]),int(row[5])
                buffer_frames.setdefault(frame,{})[(ord("b"),key,offset,size)]=digest
                combined_frames.setdefault(frame,[]).append((row[0],tuple(row[2:])))
        except (ValueError,IndexError):
            continue

    def transitions(series: dict[int,dict], label: str) -> dict:
        ids=sorted(series)
        compared=identical=0; changes=[]
        for previous,current in zip(ids,ids[1:]):
            if current!=previous+1: continue
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
    draw_pairs=[(a,b) for a,b in zip(ids,ids[1:]) if b==a+1]
    same=sum(draw_frames[a]==draw_frames[b] for a,b in draw_pairs)
    pairs=len(draw_pairs)
    state_ids=sorted(state_frames)
    state_pairs_list=[(a,b) for a,b in zip(state_ids,state_ids[1:]) if b==a+1]
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
    combined_pairs=[(a,b) for a,b in zip(combined_ids,combined_ids[1:]) if b==a+1]
    combined_same=sum(combined_frames[a]==combined_frames[b] for a,b in combined_pairs)
    uniform_ids=sorted(uniform_sequences)
    uniform_pairs=[(a,b) for a,b in zip(uniform_ids,uniform_ids[1:]) if b==a+1]
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
    by_phase: dict[int,dict[int,dict[str,list[int]]]] = {}
    for row in trace:
        if not row or row[0]!="G" or len(row)<5: continue
        try:
            phase,render_id,whole,map_ns=map(int,row[1:5])
            context=int(row[5]) if len(row)>5 else 0
        except ValueError:
            continue
        slot=by_phase.setdefault(phase,{}).setdefault(context,
            {"whole_ns":[],"map_ns":[],"render_ids":[]})
        slot["whole_ns"].append(whole); slot["render_ids"].append(render_id)
        if map_ns: slot["map_ns"].append(map_ns)
    result={}
    for phase,values in by_phase.items():
        segments={str(context):{"available_samples":len(sample["whole_ns"]),
            "median_whole_render_ms":statistics.median(sample["whole_ns"])/1e6,
            "median_map_ms":statistics.median(sample["map_ns"])/1e6 if sample["map_ns"] else None,
            "map_samples":len(sample["map_ns"]),"first_render_id":min(sample["render_ids"]),
            "last_render_id":max(sample["render_ids"])} for context,sample in values.items()}
        one=next(iter(segments.values())) if len(segments)==1 else None
        result[str(phase)]={"context_segments":segments,
            "available_samples":sum(item["available_samples"] for item in segments.values()),
            "median_whole_render_ms":one["median_whole_render_ms"] if one else None,
            "median_map_ms":one["median_map_ms"] if one else None,
            "context_count":len(segments),"cross_context_aggregation":"not performed"}
    return {"status":"available" if result else "unavailable_or_unsupported",
            "phases":result,"query_policy":"per-context asynchronous timestamps; unavailable results are never synchronously requested"}


def scope_tree_summary(trace: list[list[str]], phases: list[dict]) -> dict:
    phase_ids={PHASE_NUMBER[item["name"]] for item in phases
               if item.get("name") in PHASE_NUMBER}
    totals={phase:{} for phase in phase_ids}
    per_update={phase:{} for phase in phase_ids}
    for row in trace:
        if not row or row[0]!="Q" or len(row)<10: continue
        try:
            update,phase,parent,child,calls,in_wall,in_cpu,ex_wall,ex_cpu=map(int,row[1:10])
        except ValueError:
            continue
        if phase not in phase_ids or not 0<=child<len(SCOPE_NAMES): continue
        scope=totals[phase].setdefault((parent,child),[0,0,0,0,0])
        for index,value in enumerate((calls,in_wall,in_cpu,ex_wall,ex_cpu)):
            scope[index]+=value
        update_scopes=per_update[phase].setdefault(update,{})
        update_scope=update_scopes.setdefault((parent,child),[0,0,0,0,0])
        for index,value in enumerate((calls,in_wall,in_cpu,ex_wall,ex_cpu)):
            update_scope[index]+=value
    output={}
    for phase_id,entries in totals.items():
        nodes=[]
        for (parent,child),values in sorted(entries.items()):
            samples=[scope_values.get((parent,child),[0,0,0,0,0])
                     for scope_values in per_update[phase_id].values()]
            nodes.append({"parent":SCOPE_NAMES[parent] if 0<=parent<len(SCOPE_NAMES) else None,
                "name":SCOPE_NAMES[child],"calls":values[0],
                "inclusive_wall_ms":values[1]/1e6,"inclusive_cpu_ms":values[2]/1e6,
                "exclusive_wall_ms":values[3]/1e6,"exclusive_cpu_ms":values[4]/1e6,
                "median_inclusive_wall_ms_per_update":statistics.median(x[1] for x in samples)/1e6,
                "median_inclusive_cpu_ms_per_update":statistics.median(x[2] for x in samples)/1e6,
                "median_exclusive_wall_ms_per_update":statistics.median(x[3] for x in samples)/1e6,
                "median_exclusive_cpu_ms_per_update":statistics.median(x[4] for x in samples)/1e6})
        root=entries.get((-1,SCOPE_LOOP),[0,0,0,0,0])
        # Every named scope's exclusive interval is disjoint from its ancestors
        # and children. Include render/map exclusive time as named work; counting
        # only leaf scopes would mislabel all work inside those scopes as residual.
        identified_ids=tuple(index for index in range(len(SCOPE_NAMES))
                             if index!=SCOPE_LOOP)
        identified_cpu=sum(values[4] for (parent,child),values in entries.items()
                           if child in identified_ids)
        identified_wall=sum(values[3] for (parent,child),values in entries.items()
                            if child in identified_ids)
        cpu_coverage=[];wall_coverage=[];cpu_residual=[];wall_residual=[]
        for scope_values in per_update[phase_id].values():
            update_root=scope_values.get((-1,SCOPE_LOOP),[0,0,0,0,0])
            if update_root[2]:
                leaf_cpu=sum(value[4] for (_parent,child),value in scope_values.items()
                             if child in identified_ids)
                cpu_coverage.append(leaf_cpu/update_root[2])
            if update_root[1]:
                leaf_wall=sum(value[3] for (_parent,child),value in scope_values.items()
                              if child in identified_ids)
                wall_coverage.append(leaf_wall/update_root[1])
            cpu_residual.append(update_root[2]-sum(value[2] for (parent,_),value in scope_values.items() if parent==SCOPE_LOOP))
            wall_residual.append(update_root[1]-sum(value[1] for (parent,_),value in scope_values.items() if parent==SCOPE_LOOP))
        immediate_cpu=sum(values[2] for (parent,_child),values in entries.items() if parent==SCOPE_LOOP)
        immediate_wall=sum(values[1] for (parent,_child),values in entries.items() if parent==SCOPE_LOOP)
        output[str(phase_id)]={"nodes":nodes,"root_loop_cpu_ms":root[2]/1e6,
            "root_loop_wall_ms":root[1]/1e6,
            "root_unattributed_exclusive_cpu_ms":max(0,root[2]-immediate_cpu)/1e6,
            "root_unattributed_exclusive_wall_ms":max(0,root[1]-immediate_wall)/1e6,
            "identified_exclusive_scope_cpu_ms":identified_cpu/1e6,
            "identified_exclusive_scope_wall_ms":identified_wall/1e6,
            "identified_exclusive_scope_cpu_fraction":identified_cpu/root[2] if root[2] else None,
            "identified_exclusive_scope_wall_fraction":identified_wall/root[1] if root[1] else None,
            "median_identified_exclusive_scope_cpu_fraction_per_update":statistics.median(cpu_coverage) if cpu_coverage else None,
            "median_identified_exclusive_scope_wall_fraction_per_update":statistics.median(wall_coverage) if wall_coverage else None,
            "median_root_unattributed_exclusive_cpu_ms_per_update":statistics.median(cpu_residual)/1e6 if cpu_residual else None,
            "median_root_unattributed_exclusive_wall_ms_per_update":statistics.median(wall_residual)/1e6 if wall_residual else None,
            "coverage_95_percent_gate":"passed" if cpu_coverage and wall_coverage and
                statistics.median(cpu_coverage)>=.95 and statistics.median(wall_coverage)>=.95 else "not met",
            "coverage_status":"partial" if root[2] else "missing scope records",
            "coverage_policy":"Exclusive intervals from every instrumented child scope (including UpdateOneFrame, Render, map, and cadence sleep) are summed once; HookedUpdateLoop exclusive time remains explicit residual"}
    return {"scope_names":list(SCOPE_NAMES),"phases":output,
            "status":"available" if output else "unavailable",
            "wall_cpu_policy":"exclusive CPU and wall are separate; HookedUpdateLoop spans UpdateOneFrame plus pacing sleep; wall residual can include blocking and descheduling"}


PHASE_NUMBER = {name: i+1 for i, (name, *_rest) in enumerate(PHASES)}
PHASE_NUMBER.update({"P0": 100, "P1": 101, "P2": 102, "TAIL": 103,
                     "LPM0": 104, "LPM": 105, "LPM1": 106,
                     "LPMF0": 107, "LPMF": 108, "LPMF1": 109})


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
    summaries = [phase_summary(frames, item["name"], item["duration_s"])
                 for item in phases if item.get("name") in PHASE_NUMBER]
    counters = {}
    for row in trace:
        if row[0] == "C" and len(row) >= 5:
            index=int(row[1]); name=HOOK_NAMES[index] if index<len(HOOK_NAMES) else f"hook_{index}"
            counters[name] = {"calls": int(row[2]), "wall_ns_sum": int(row[3]),
                              "thread_cpu_ns_sum":int(row[4]),
                              "timing":"sampled" if index==7 else "count_only" if index in (8,9,11) else "inclusive/scope"}
    totals=next((list(map(int,row[1:4])) for row in trace if row[0]=="X" and len(row)>=4),[0,0,0])
    hook_failures=next((int(row[1]) for row in reversed(trace) if row[0]=="Z" and len(row)>=2),None)
    detail = {key: sum(1 for row in trace if row[0] == key)
              for key in ("D", "S", "U", "V", "W", "B", "b", "T", "t", "G")}
    temporal = temporal_differences(trace)
    gpu = gpu_summary(trace)
    scope_tree=scope_tree_summary(trace,phases)
    a_rows = [p for p in summaries if p["phase"].startswith("A") and p["status"] == "complete"]
    drift = {}
    for field in ("median_update_cpu_ms", "median_render_cpu_ms", "median_draws"):
        values = [p[field] for p in a_rows]
        drift[field] = (max(values)/min(values)-1) if values and min(values)>0 else None
    reference = json.loads((ROOT / "results/autonomous-reproducibility.json").read_text())
    base_cpu_ms_swap = (reference["summary"]["eu4_cpu_ms_per_s"]["median"] /
                        reference["summary"]["median_swaps_s"]["median"])
    output = {"status": manifest.get("status"), "run_dir": str(run_dir),
              "executable_sha256": manifest.get("executable_sha256"),
              "phases": summaries, "hook_totals": counters,
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
              "recommendations": derive_recommendations(summaries,manifest.get("power_by_phase",{}),temporal,gpu),
              "limitations": manifest.get("limitations", [])}
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


def derive_recommendations(phases: list[dict],power: dict,temporal: dict,gpu: dict) -> list[dict]:
    by_name={item["phase"]:item for item in phases if item.get("status")=="complete"}
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
    lines=["# Paused EU IV frame model", "", f"Run status: {data['status']}.", "",
           f"Historical process CPU reference: {data['historical_process_cpu_ms_per_swap']:.2f} ms/swap.",
           "", "Asynchronous render/map timestamp results appear when supported by the active CGL context; missing results leave GPU accounting partial.",
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
    normal=[p for p in data["phases"] if p["phase"].startswith("A") and p["status"]=="complete"]
    if normal:
        lines.extend(["","## Exclusive CPU and wall cost tree", "",
                      "Wall and thread CPU remain separate. Root scopes do not count as explained cost; residuals and coverage are emitted per phase.",
                      json.dumps(data.get("exclusive_cost_tree",{}),indent=2,sort_keys=True)])
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
                  "The hook library counts six draw APIs, common depth/blend/stencil/raster/viewport/scissor/texture-parameter state calls, selected resource binds/uploads, and observed scalar/vector/matrix float/int uniform families. Texture byte counts are estimates; PBO sources, mapped writes, unsigned/double/non-square uniforms, resource create/delete lifecycle, culling/LOD decisions, bucket sorting, buffer changed-byte ranges, and other draw entry points remain uncovered.",
                  "", "Sampled draw records include API, callsite, program, a VAO/enabled-attribute source signature, element buffer, mode, count, index type/offset or first vertex, signed base vertex, and instance count. Sampled uniform calls retain callsite/program/location/size/hash; consecutive `glUniform4fv` payloads up to 256 bytes report changed 32-bit elements. C requires zero forwarded draws among covered APIs. D uses `GL_RASTERIZER_DISCARD` around `Render()` and requires clean state flags.",
                  "", "Engine scopes emit actual parent, inclusive and exclusive wall/CPU, and per-update totals. Exclusive intervals from named scopes count toward coverage; the unscoped root residual remains explicit. Update loop wall includes pacing sleep. `PresentScene` and `CGLFlushDrawable` are distinct. Render CPU is shown both per update and per executed render.",
                  "", "Frame flags: 1 frame queue overflow, 2 update cadence overrun, 4 unknown texture byte estimate, 8 unavailable raster-suppression state API/context, 16 restoration failure, 32 raster context capacity exceeded, 64 GPU query slot unavailable, 128 unknown primitive topology, 512 scope-stack overflow, 1024 scope mismatch/child-time inconsistency, and 2048 unavailable sampled GL-state seed.",
                  "", "GPU timestamps stay segmented by CGL context and are not combined across contexts. They represent same-context GPU timeline intervals, not guaranteed busy time. The static backend graph remains a candidate mapping until implementation and resource/state dependencies are confirmed.", ""])
    return "\n".join(lines)


class SharedControl:
    def __init__(self, path: Path):
        self.path=path
        self.file=path.open("r+b")
        self.map=mmap.mmap(self.file.fileno(),CONTROL_SIZE)
        values=[1,0,0,0,0,0,0,0,0,0,0,0]
        self.map[:CONTROL.size]=CONTROL.pack(*values)
        self.generation=0
        self.command_seq=0
    def set(self,mode: str,phase: int,update_period_ns: int=0,
            render_period_ns: int=0,detail_frames: int=0,
            measurement: bool=True) -> int:
        self.generation+=1
        self.command_seq+=1
        odd=self.command_seq*2-1
        even=self.command_seq*2
        struct.pack_into("<I",self.map,4,odd)
        self.map.flush()
        flags=(1 if mode!="off" else 0) | (2 if measurement else 0)
        command=CONTROL_COMMAND.pack(1,odd,MODE[mode],phase,flags,
                                     detail_frames,update_period_ns,render_period_ns,
                                     self.generation)
        self.map[:4]=command[:4]
        self.map[8:CONTROL_COMMAND.size]=command[8:]
        struct.pack_into("<I",self.map,4,even)
        self.map.flush()
        return self.generation
    def acknowledged(self) -> int:
        return struct.unpack_from("<Q",self.map,48)[0]
    def hook_failures(self) -> int:
        return struct.unpack_from("<Q",self.map,56)[0]
    def dropped_records(self) -> int:
        return struct.unpack_from("<Q",self.map,64)[0]
    def close(self) -> None:
        self.map.close(); self.file.close()


def _wait_ack(game: subprocess.Popen, control: SharedControl, generation: int,
              label: str, timeout: float=5.0) -> None:
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
                          capture_output=True,text=True,check=False)
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
                          capture_output=True,text=True,check=False)
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


def _wait_phase(game: subprocess.Popen, duration: int, control: SharedControl,
                events: Path, name: str, mode: str, cadence: int,
                detail: bool=False, render_period_ns: int=0,
                detail_windows: int=2,detail_frames_per_window: int=2) -> dict:
    generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,0,False)
    auto.mark(events,"phase_transition",phase=name,mode=mode,generation=generation)
    _wait_ack(game,control,generation,name+" transition")
    time.sleep(5)
    generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,
                           detail_frames_per_window if detail else 0,True)
    measurement_generation=generation
    start=auto.mark(events,"phase_start",phase=name)
    auto.mark(events,"measurement_enabled",phase=name,generation=generation)
    if detail:
        auto.mark(events,"detail_sample_armed",phase=name,generation=generation,
                  render_frames=detail_frames_per_window)
    end_by=time.monotonic()+duration
    measurement_started=end_by-duration
    next_detail=(measurement_started+duration/max(detail_windows,1)
                 if detail and detail_windows>1 else end_by)
    detail_armed=1 if detail else 0
    while time.monotonic()<end_by:
        if game.poll() is not None: raise base.BenchmarkError(f"EU IV exited during {name}")
        if not auto.focus("interior",game.pid): raise base.BenchmarkError(f"EU IV lost focus during {name}")
        now=time.monotonic()
        if detail and detail_armed<detail_windows and now>=next_detail:
            generation=control.set(mode,PHASE_NUMBER[name],cadence,render_period_ns,
                                   detail_frames_per_window,True)
            measurement_generation=generation
            detail_armed+=1
            auto.mark(events,"detail_sample_armed",phase=name,generation=generation,
                      render_frames=detail_frames_per_window,window=detail_armed)
            next_detail=measurement_started+duration*(detail_armed+1)/detail_windows
        next_wake=min(end_by,next_detail) if detail_armed<detail_windows else end_by
        time.sleep(min(1,max(0,next_wake-time.monotonic())))
    end=auto.mark(events,"phase_end",phase=name)
    stop_generation=control.set(mode,0,cadence,render_period_ns,0,False)
    _wait_ack(game,control,stop_generation,name+" measurement stop")
    auto.mark(events,"measurement_disabled",phase=name)
    return {"name":name,"duration_s":(end["monotonic_ns"]-start["monotonic_ns"])/1e9,
            "requested_duration_s":duration,"start_ns":start["monotonic_ns"],
            "end_ns":end["monotonic_ns"],"settle_s":5,"mode":mode,
            "measurement_generation":measurement_generation,
            "stop_generation":stop_generation}


def run(output_root: Path, include_low_power: bool=True,
        include_fixed_cadence_lpm: bool=False) -> Path:
    evidence=preflight(run_gl=True,require_privilege=True)
    base.running_game_pid("idle")
    if not auto.SCENE.is_file() or not auto.SCENE_MANIFEST.is_file():
        raise base.BenchmarkError("Register the reviewed Venice scene before this diagnostic run")
    if subprocess.run(["sudo","-v"],check=False).returncode:
        raise base.BenchmarkError("Administrator authorization is required for powermetrics")
    output_root.mkdir(parents=True,exist_ok=True)
    run_dir=output_root/f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-frame-model"
    run_dir.mkdir(parents=True,exist_ok=False)
    manifest_path=run_dir/"manifest.json"
    manifest={"status":"starting","executable_sha256":evidence["build"]["executable_sha256"],
              "evidence":evidence,"coverage":{"engine_hooks":7,"gl_draw_apis":6,
              "gpu_timing":"asynchronous render/map timestamps when supported","exact_payload_diff":"sampled uniform4fv element-level; buffer uploads hash-level"},
              "limitations":["GPU timer query support depends on the active CGL context; absent results are reported as unavailable.",
                  "Thread CPU is measured on the UpdateOneFrame thread; worker-thread totals are outside the per-frame tree.",
                  "Scene membership, visibility decisions, LOD, sorting, and allocation counts are not instrumented.",
                  "Exact uniform4fv element changes cover sampled consecutive payloads up to 256 bytes; buffer payloads remain hash-level.",
                  "The backend graph maps Gfx sites to candidate GL calls; resource/state dependencies need runtime or implementation analysis."]}
    manifest_path.write_text(json.dumps(manifest,indent=2)+"\n")
    events=run_dir/"events.jsonl"
    anchor=auto.mark(events,"clock_anchor")
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
                for name,mode,detail in (("P0","off",False),("P1","profile",True),("P2","off",False)):
                    calibration_phases.append(_wait_phase(game,15,control,events,name,mode,0,
                        detail=detail,detail_windows=6 if name=="P1" else 2,
                        detail_frames_per_window=1 if name=="P1" else 2))
                profile_rows=frame_rows(run_dir/"telemetry.csv")
                p1_phase=next(item for item in calibration_phases if item["name"]=="P1")
                p1=phase_summary(profile_rows,"P1",p1_phase["duration_s"])
                if p1["status"]!="complete" or p1["median_update_cpu_ms"]<=0 or p1["median_render_wall_ms"]<=0:
                    raise base.BenchmarkError("Profiler hooks did not record live update/render scopes")
                probe=auto.probe_rows(run_dir/"auto-probe.csv",anchor)
                power_tail.poll()
                calibration_power=diagnostic.summarize_power(power_tail.samples,calibration_phases,anchor,game.pid)
                cpu_rates=[calibration_power.get(name,{}).get("eu4_cputime_ms_per_s") for name in ("P0","P1","P2")]
                if any(value is None for value in cpu_rates):
                    raise base.BenchmarkError("Power samples do not cover the three profiler calibration phases")
                cpu_perturbation=cpu_rates[1]/statistics.mean((cpu_rates[0],cpu_rates[2]))-1
                if abs(cpu_perturbation)>.03:
                    raise base.BenchmarkError(f"Counters-only profiler CPU perturbation is {cpu_perturbation:+.1%}; stop before causal phases")
                detail_rows=[row for row in profile_rows if row["phase"]==PHASE_NUMBER["P1"]]
                detail_ids={int(row[1]) for row in read_rows(run_dir/"telemetry.csv") if row[0]=="D" and len(row)>1}
                traced=[row["update_cpu_ns"] for row in detail_rows if row["render_id"] in detail_ids]
                plain=[row["update_cpu_ns"] for row in detail_rows if row["render_id"] not in detail_ids]
                trace_perturbation=(statistics.median(traced)/statistics.median(plain)-1
                    if traced and plain and statistics.median(plain)>0 else None)
                if len(traced)<6 or trace_perturbation is None or abs(trace_perturbation)>.05:
                    raise base.BenchmarkError(f"Sampled detail trace perturbation {trace_perturbation} exceeds the 5% gate")
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
                baseline_period=int(1e9/max(p1["update_attempts_s"],1))
                manifest["calibration"]={"phases":calibration_phases,"profile":p1,"power":calibration_power,
                    "cpu_perturbation_fraction":cpu_perturbation,"sampled_trace_perturbation_fraction":trace_perturbation,
                    "swap_rate":measured_rate,"historical_swap_rate":reference_rate,"target_cadence_ns":baseline_period,
                    "limits":{"counters_cpu_frame":.03,"swap_rate":.03,"sampled_trace":.05}}
                if p1["median_update_cpu_ms"]>p1["median_update_wall_ms"]*1.05:
                    raise base.BenchmarkError("Thread CPU exceeds update wall time; timing is inconsistent")

                phases=[]
                for name,mode,duration,detail in PHASES:
                    update_period=0 if name=="A0" else baseline_period
                    render_period=int(1e9/30) if name=="E30" else int(1e9/15) if name=="E15" else 0
                    phase=_wait_phase(game,duration,control,events,name,mode,update_period,
                                      detail=detail,render_period_ns=render_period)
                    phases.append(phase)
                    observed=phase_summary(frame_rows(run_dir/"telemetry.csv"),name,phase["duration_s"])
                    if observed["status"]!="complete": raise base.BenchmarkError(f"No frame records in {name}")
                    if name in {"B","C","D","E30","E15"}:
                        update_delta=abs(observed["update_attempts_s"]/p1["update_attempts_s"]-1)
                        if update_delta>.03:
                            raise base.BenchmarkError(f"{name} changed update cadence by {update_delta:.1%}; its causal comparison is invalid")
                    if name=="B" and (observed["total_executed_renders"]!=0 or
                                       observed["total_present_scene_calls"]!=0):
                        raise base.BenchmarkError("B did not skip Render/Present")
                    if name=="C" and (observed["total_suppressed_draws"]==0 or observed["total_forwarded_draws"]!=0):
                        raise base.BenchmarkError("C did not suppress every covered draw call")
                    if name=="D" and any(flags&(8|16|32) for flags in observed["record_flags"]):
                        raise base.BenchmarkError("D raster-work suppression state/context restoration failed")
                    if name.startswith("A") and observed["median_draws"]==0:
                        raise base.BenchmarkError(f"Control phase {name} had no observed draws")
                manifest["phases"]=phases
                manifest["profiling_tail"]=_wait_phase(game,30,control,events,"TAIL","profile",baseline_period,True)
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
                hook_failures=control.hook_failures()
                dropped_records=control.dropped_records()
                integrity={"hook_failures":hook_failures,"dropped_records":dropped_records,
                           "status":"passed" if hook_failures==0 and dropped_records==0 else "failed"}
                manifest["probe_integrity"]=integrity
                if hook_failures or dropped_records:
                    raise base.BenchmarkError(f"Profiler integrity failed: {integrity}")
                auto.stop_process(pm,5); pm=None; power_tail.poll()
                all_phases=calibration_phases+manifest["phases"]+[manifest["profiling_tail"]]
                manifest["power_by_phase"]=diagnostic.summarize_power(power_tail.samples,all_phases,anchor,game.pid)
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
        manifest_path.write_text(json.dumps(manifest,indent=2,default=str)+"\n")
        raise
    return run_dir


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    pf=sub.add_parser("preflight",help="pin/build the profiler and validate offline probes")
    pf.add_argument("--static-only",action="store_true")
    pf.add_argument("--require-power-helper",action="store_true")
    run_parser=sub.add_parser("run",help="run the unattended paused causal experiment")
    run_parser.add_argument("--output",default=str(ROOT/"results"))
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
        elif args.command=="report":
            print(json.dumps(analyze(Path(args.run_dir).expanduser().resolve()),indent=2))
        elif args.command=="recover-power":
            _power_helper_preflight()
            print(json.dumps(_restore_power_mode(Path(args.journal).expanduser().resolve()),indent=2))
        else:
            print(run(Path(args.output).expanduser().resolve(),
                      include_fixed_cadence_lpm=args.fixed_cadence_lpm))
    except (base.BenchmarkError,OSError,ValueError,subprocess.SubprocessError,KeyboardInterrupt) as exc:
        print(f"Error: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
