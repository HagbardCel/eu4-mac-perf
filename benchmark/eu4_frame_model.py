#!/usr/bin/env python3
"""One-launch causal frame accounting for the pinned GOG EU IV build."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mmap
import os
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
CONTROL_SIZE = 4096
CONTROL = struct.Struct("<6I5Q")
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
                "forwarded_draws", "suppressed_draws")
HOOK_NAMES = ("UpdateOneFrame", "CInGameIdler::Idle", "CInGameIdler::Render",
              "CEU3GraphicalMap::Render", "CGraphics::PresentScene",
              "CPdxMeshObject::AddToBucket", "CArray<SFlushData>::Append",
              "GL draw", "GL buffer/texture upload", "GL uniform4fv",
              "CGLFlushDrawable", "GL state call")


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
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Build failed: {result.stderr.strip()}")
    for path in (LIBRARY, HARNESS):
        if base.command("lipo", "-archs", str(path)).strip() != "x86_64":
            raise base.BenchmarkError(f"{path.name} is not x86_64")
    return {"executable_sha256": base.sha256(base.GOG_EXE),
            "engine_inventory_sha256": base.sha256(engine.OUTPUT),
            "backend_graph_sha256":base.sha256(engine.BACKEND_GRAPH),
            "engine_sites": len(json.loads(engine.OUTPUT.read_text())["hooks"]),
            "library_sha256": base.sha256(LIBRARY),
            "library_source_sha256": base.sha256(SOURCE),
            "architecture": "x86_64"}


def offline_harness(library: Path | None = None, mode: int = 1, loops: int = 15000) -> dict:
    if not LIBRARY.is_file() or not HARNESS.is_file():
        build()
    with tempfile.TemporaryDirectory(prefix="eu4-frame-model-") as directory:
        root = Path(directory)
        control_path, log_path = root / "control.bin", root / "trace.csv"
        fields = [1, mode, 0, 0, 0, 0, 0, 0, 0, 0, 0]
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
    auto_evidence = auto.preflight(require_privilege=require_privilege)
    if not run_gl:
        return {"status": "static_ready", "build": static, "autonomous": auto_evidence,
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
    result = {"phase": name, "status": "complete", "updates": len(selected),
              "update_attempts_s": len(selected)/seconds,
              "median_loop_wall_ms": median("wall_ns")/1e6,
              "median_loop_cpu_ms": median("cpu_ns")/1e6,
              "median_update_wall_ms": median("update_wall_ns")/1e6,
              "median_update_cpu_ms": median("update_cpu_ns")/1e6,
              "median_idle_wall_ms": median("idle_wall_ns")/1e6,
              "median_idle_cpu_ms": median("idle_cpu_ns")/1e6,
              "median_render_wall_ms": median("render_wall_ns")/1e6,
              "median_render_cpu_ms": median("render_cpu_ns")/1e6,
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
              "median_render_skipped": sum(row["render_wall_ns"] == 0 for row in selected),
              "cadence_overruns": sum(bool(row["flags"] & 2) for row in selected),
              "record_flags": sorted({row["flags"] for row in selected})}
    result["median_wall_accounted_fraction"]=statistics.median(
        min(1.0,(row["update_wall_ns"]+row["sleep_ns"])/row["wall_ns"]) if row["wall_ns"] else 0
        for row in selected)
    result["median_thread_cpu_accounted_fraction"]=statistics.median(
        min(1.0,row["update_cpu_ns"]/row["cpu_ns"]) if row["cpu_ns"] else 0
        for row in selected)
    result["bucket_records_per_add"] = (
        round(result["median_flush_records"]/result["median_bucket_calls"], 4)
        if result["median_bucket_calls"] else None)
    return result


def temporal_differences(trace: list[list[str]]) -> dict:
    draw_frames: dict[int,list[tuple[int,int,int,int]]] = {}
    state_frames: dict[int,list[tuple[int,int,int]]] = {}
    uniform_frames: dict[int,dict[tuple[int,int,int],tuple[int,int]]] = {}
    buffer_frames: dict[int,dict[tuple[int,int,int,int],int]] = {}
    texture_frames: dict[int,dict[tuple[int,int],int]] = {}
    uniform_exact=[]; uniform_compared_calls=0; uniform_changed_elements=0
    uniform_omitted_elements=0; uniform_oversize_calls=0
    for row in trace:
        try:
            if row[0]=="D" and len(row)>=6:
                draw_frames.setdefault(int(row[1]),[]).append(tuple(map(int,row[2:6])))
            elif row[0]=="S" and len(row)>=5:
                state_frames.setdefault(int(row[1]),[]).append((int(row[2]),int(row[3]),int(row[4])))
            elif row[0]=="U" and len(row)>=6:
                frame,key_program,location=int(row[1]),int(row[3]),int(row[4])
                size_count_hash=int(row[5]); count=size_count_hash>>32; payload_hash=size_count_hash&0xffffffff
                key=(int(row[2]),key_program,location)
                uniform_frames.setdefault(frame,{})[key]=(count*16,payload_hash)
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
            elif row[0]=="B" and len(row)>=6:
                frame,key,size,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4])
                buffer_frames.setdefault(frame,{})[(ord("B"),key,0,size)]=digest
            elif row[0]=="b" and len(row)>=6:
                frame,key,size,offset,digest=int(row[1]),int(row[2]),int(row[3]),int(row[4]),int(row[5])
                buffer_frames.setdefault(frame,{})[(ord("b"),key,offset,size)]=digest
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
    return {"draw_topology":{"sampled_render_frames":len(ids),
              "identical_consecutive_fraction":same/pairs if pairs else None},
            "render_state":{"sampled_render_frames":len(state_ids),
              "identical_consecutive_fraction":same_state/state_pairs if state_pairs else None,
              "changed_calls":state_changes,"change_limit":100,
              "note":"Call tuple is (game callsite offset, operation kind, state value)."},
            "uniform_payloads":transitions(uniform_frames,"Uniform payloads"),
            "uniform_exact_elements":{"compared_calls":uniform_compared_calls,
              "changed_32bit_elements":uniform_changed_elements,
              "omitted_element_changes":uniform_omitted_elements,
              "oversize_payload_calls_without_byte_diff":uniform_oversize_calls,
              "captured_changes":uniform_exact,"capture_limit":512,
              "note":"Exact raw 32-bit elements for consecutive sampled glUniform4fv payloads up to 256 bytes; larger payloads retain hashes only."},
            "buffer_uploads":transitions(buffer_frames,"Buffer upload payloads"),
            "texture_upload_calls":transitions(texture_frames,"Texture upload call dimensions")}


def gpu_summary(trace: list[list[str]]) -> dict:
    by_phase: dict[int,dict[str,list[int]]] = {}
    for row in trace:
        if not row or row[0]!="G" or len(row)<5: continue
        try:
            phase,render_id,whole,map_ns=map(int,row[1:5])
        except ValueError:
            continue
        slot=by_phase.setdefault(phase,{"whole_ns":[],"map_ns":[],"render_ids":[]})
        slot["whole_ns"].append(whole); slot["render_ids"].append(render_id)
        if map_ns: slot["map_ns"].append(map_ns)
    result={}
    for phase,values in by_phase.items():
        result[str(phase)]={"available_samples":len(values["whole_ns"]),
            "median_whole_render_ms":statistics.median(values["whole_ns"])/1e6,
            "median_map_ms":statistics.median(values["map_ns"])/1e6 if values["map_ns"] else None,
            "map_samples":len(values["map_ns"]),"first_render_id":min(values["render_ids"]),
            "last_render_id":max(values["render_ids"])}
    return {"status":"available" if result else "unavailable_or_unsupported",
            "phases":result,"query_policy":"timestamp queries; availability checked before results are read"}


PHASE_NUMBER = {name: i+1 for i, (name, *_rest) in enumerate(PHASES)}
PHASE_NUMBER.update({"P0": 100, "P1": 101, "P2": 102, "TAIL": 103,
                     "LPM0": 104, "LPM": 105, "LPM1": 106})


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
              "conclusions": derive_conclusions(summaries, drift,manifest.get("power_by_phase",{})),
              "recommendations": derive_recommendations(summaries,manifest.get("power_by_phase",{}),temporal,gpu),
              "limitations": manifest.get("limitations", [])}
    (run_dir / "report.json").write_text(json.dumps(output, indent=2)+"\n")
    (run_dir / "report.md").write_text(render_report(output))
    return output


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
        normal_cpu=statistics.mean((power["LPM0"].get("eu4_cputime_ms_per_s",0),
                                    power["LPM1"].get("eu4_cputime_ms_per_s",0)))
        low_cpu=power["LPM"].get("eu4_cputime_ms_per_s")
        normal_w=statistics.mean((power["LPM0"].get("combined_w",0),power["LPM1"].get("combined_w",0)))
        low_w=power["LPM"].get("combined_w")
        if normal_cpu and low_cpu is not None and normal_w and low_w is not None:
            output.append(f"Low Power Mode: EU IV CPU time per second changed {low_cpu/normal_cpu-1:+.1%}; combined power changed {low_w/normal_w-1:+.1%}. Compare frame work and frequency fields before interpreting this as a throughput effect.")
    if not output:
        output.append("Insufficient valid phases for a causal recommendation.")
    return output


def derive_recommendations(phases: list[dict],power: dict,temporal: dict,gpu: dict) -> list[dict]:
    by_name={item["phase"]:item for item in phases if item.get("status")=="complete"}
    candidates=[]
    for name,before,after,label in (
        ("B","A0","A1","adaptive/event-driven render scheduling"),
        ("C","A1","A2","cache reusable scene/bucket preparation"),
        ("D","A2","A3","reduce raster/pixel work or cache static map layers")):
        if not all(key in by_name for key in (name,before,after)): continue
        control_cpu=statistics.mean((by_name[before]["median_update_cpu_ms"],by_name[after]["median_update_cpu_ms"]))
        tested_cpu=by_name[name]["median_update_cpu_ms"]
        if control_cpu>0:
            candidates.append({"intervention":label,"signal":"CPU ms/frame",
                "estimated_reduction_fraction":round(1-tested_cpu/control_cpu,4),
                "evidence":"causal phase bracketed by adjacent A controls"})
    gpu_phases=gpu.get("phases",{})
    if all(str(PHASE_NUMBER[name]) in gpu_phases for name in ("A2","D","A3")):
        reference=statistics.mean((gpu_phases[str(PHASE_NUMBER["A2"])]["median_whole_render_ms"],
                                   gpu_phases[str(PHASE_NUMBER["A3"])]["median_whole_render_ms"]))
        tested=gpu_phases[str(PHASE_NUMBER["D"])]["median_whole_render_ms"]
        if reference>0:
            candidates.append({"intervention":"reduce raster work or cache static map layers",
                "signal":"GPU ms/render","estimated_reduction_fraction":round(1-tested/reference,4),
                "evidence":"asynchronous GPU timestamps bracketed by A controls"})
    topology=temporal.get("draw_topology",{}).get("identical_consecutive_fraction")
    if topology is not None and topology>=.95:
        candidates.append({"intervention":"reuse stable render command/bucket structures",
            "signal":"consecutive sampled draw topology",
            "estimated_reduction_fraction":None,
            "evidence":f"{topology:.1%} of sampled adjacent render command sequences are identical; this indicates a cache opportunity, not measured savings"})
    # Keep the report focused on the strongest measured CPU/GPU effects, then
    # use temporal stability only when it supplies a distinct caching signal.
    measured=sorted((item for item in candidates if item["estimated_reduction_fraction"] is not None),
                    key=lambda item:item["estimated_reduction_fraction"],reverse=True)
    selected=measured[:2]
    if topology is not None and topology>=.95 and not any("reuse stable" in item["intervention"] for item in selected):
        selected=([*selected[:1],next(item for item in candidates if "reuse stable" in item["intervention"])])
    return selected or [{"intervention":"no architectural recommendation yet",
                         "signal":"coverage or causal phases incomplete",
                         "estimated_reduction_fraction":None,
                         "evidence":"review report limitations and missing timing samples"}]


def render_report(data: dict) -> str:
    lines=["# Paused EU IV frame model", "", f"Run status: {data['status']}.", "",
           f"Historical process CPU reference: {data['historical_process_cpu_ms_per_swap']:.2f} ms/swap.",
           "", "Asynchronous render/map timestamp results appear when supported by the active CGL context; missing results leave GPU accounting partial.",
           "", "## Phase accounting", "",
           "| Phase | Updates/s | Loop wall ms | Loop CPU ms | Idle wall ms | Update wall ms | Update CPU ms | Render wall ms | Render CPU ms | Present wall ms | Flush wall ms | Flush CPU ms | Draws/update | Triangles est. | Bucket appends | Flags |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for p in data["phases"]:
        if p["status"]!="complete":
            lines.append(f"| {p['phase']} | — | — | — | — | — | — | — | — | — | — | — | — | — | — | missing |")
            continue
        lines.append(f"| {p['phase']} | {p['update_attempts_s']:.2f} | {p['median_loop_wall_ms']:.3f} | {p['median_loop_cpu_ms']:.3f} | {p['median_idle_wall_ms']:.3f} | {p['median_update_wall_ms']:.3f} | {p['median_update_cpu_ms']:.3f} | {p['median_render_wall_ms']:.3f} | {p['median_render_cpu_ms']:.3f} | {p['median_present_wall_ms']:.3f} | {p['median_cglflush_wall_ms']:.3f} | {p['median_cglflush_cpu_ms']:.3f} | {p['median_draws']:.0f} | {p['median_triangles_est']:.0f} | {p['median_flush_records']:.0f} | {p['record_flags']} |")
    normal=[p for p in data["phases"] if p["phase"].startswith("A") and p["status"]=="complete"]
    if normal:
        wall=statistics.median(p["median_wall_accounted_fraction"] for p in normal)
        cpu=statistics.median(p["median_thread_cpu_accounted_fraction"] for p in normal)
        lines.extend(["",f"Root scope accounts for median {wall:.1%} of normal A wall time and {cpu:.1%} of UpdateOneFrame-thread CPU time. The remaining fraction is shown explicitly; nested scope times overlap."])
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
                  "## Asynchronous GPU timing", "",
                  json.dumps(data.get("gpu_by_phase", {}), indent=2, sort_keys=True), "",
                  "## Consecutive-frame changes", "",
                  json.dumps(data.get("temporal_differences", {}), indent=2, sort_keys=True), "",
                  f"Static backend graph: `{data.get('backend_graph', 'unavailable')}`.", "",
                  "## Causal findings", ""])
    lines.extend(f"- {item}" for item in data["conclusions"])
    lines.extend(["", "## Recommended next interventions", ""])
    lines.extend(f"- **{item['intervention']}** — {item['signal']}: {item.get('estimated_reduction_fraction')}; {item['evidence']}"
                 for item in data.get("recommendations",[]))
    lines.extend(["", "## Coverage and limitations", "",
                  "The hook library counts six draw APIs, selected GL state calls, selected buffer/texture uploads, and `glUniform4fv`. Texture byte counts are estimates; pixel unpack state, PBO sources, mapped writes, other uniform families, object culling/LOD decisions, buffer changed-byte ranges, and unwrapped draw entry points remain uncovered.",
                  "", "Draw submission timing is sampled 1/256 and expanded. Sampled consecutive `glUniform4fv` payloads up to 256 bytes report exact changed 32-bit elements by caller/program/location; larger uniforms retain hashes. Buffer payload stability is hash-level by object/range. C requires zero forwarded draws among covered APIs. D uses `GL_RASTERIZER_DISCARD` around `Render()` and requires clean context/state flags.",
                  "", "CPU scope times are inclusive and overlap; GPU timestamps are a separate asynchronous lane. Update loop wall time includes intentional cadence sleep. `PresentScene` CPU/wall and `CGLFlushDrawable` wall time are recorded independently.",
                  "", "Frame flags: 1 queue overflow, 2 cadence overrun, 4 unknown texture byte estimate, 8 unavailable raster-suppression state API/context, 16 restoration failure, 32 raster context capacity exceeded, 64 GPU query slot unavailable, 128 unknown primitive topology for triangle estimation, and 256 GPU timestamps disabled after a CGL context switch.",
                  "", "The static backend graph lists Gfx callsites, candidate GL operations, and Metal draw equivalents. Resource and state dependencies are explicitly marked unknown pending implementation or runtime attribution.", ""])
    return "\n".join(lines)


class SharedControl:
    def __init__(self, path: Path):
        self.path=path
        self.file=path.open("r+b")
        self.map=mmap.mmap(self.file.fileno(),CONTROL_SIZE)
        values=[1,0,0,0,0,0,0,0,0,0,0]
        self.map[:CONTROL.size]=CONTROL.pack(*values)
        self.generation=0
    def set(self,mode: str,phase: int,cadence_ns: int=0,detail_frames: int=0) -> int:
        self.generation+=1
        self.map[:CONTROL.size]=CONTROL.pack(1,MODE[mode],phase,0,detail_frames,0,
                                             cadence_ns,self.generation,0,0,0)
        self.map.flush()
        return self.generation
    def acknowledged(self) -> int:
        return struct.unpack_from("<Q",self.map,40)[0]
    def close(self) -> None:
        self.map.close(); self.file.close()


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
    entry={"requested":mode,"previous":state,"setting":key,"value":value,
           "recorded_at":dt.datetime.now(dt.timezone.utc).isoformat()}
    if journal:
        journal.write_text(json.dumps(entry,indent=2)+"\n")
    result=subprocess.run(["sudo","-n","pmset","-c",key,value],capture_output=True,text=True,check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Could not set {mode} power mode: {result.stderr.strip()}")
    observed=base.power_state()
    if observed.get("mode")!=mode:
        raise base.BenchmarkError(f"Power mode transition requested {mode}, observed {observed.get('mode')}")
    return {"setting":key,"requested":mode,"observed":observed}


def _wait_phase(game: subprocess.Popen, duration: int, control: SharedControl,
                events: Path, name: str, mode: str, cadence: int,
                detail: bool=False) -> dict:
    generation=control.set(mode,PHASE_NUMBER[name],cadence,0)
    auto.mark(events,"phase_transition",phase=name,mode=mode,generation=generation)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if game.poll() is not None: raise base.BenchmarkError(f"EU IV exited during {name} transition")
        if control.acknowledged()>=generation:
            remaining=deadline-time.monotonic()
            if remaining>0: time.sleep(remaining)
            break
        time.sleep(.1)
    else:
        raise base.BenchmarkError(f"Profiler did not acknowledge {name} mode transition")
    if detail:
        # Arm sampled tracing only after the settling interval, immediately
        # before the measurement window, so those frames are not discarded.
        generation=control.set(mode,PHASE_NUMBER[name],cadence,2)
        auto.mark(events,"detail_sample_armed",phase=name,generation=generation,render_frames=2)
    start=auto.mark(events,"phase_start",phase=name)
    end_by=time.monotonic()+duration
    while time.monotonic()<end_by:
        if game.poll() is not None: raise base.BenchmarkError(f"EU IV exited during {name}")
        if not auto.focus("interior",game.pid): raise base.BenchmarkError(f"EU IV lost focus during {name}")
        time.sleep(min(1,max(0,end_by-time.monotonic())))
    end=auto.mark(events,"phase_end",phase=name)
    return {"name":name,"duration_s":duration,"start_ns":start["monotonic_ns"],
            "end_ns":end["monotonic_ns"],"settle_s":5,"mode":mode}


def run(output_root: Path, include_low_power: bool=True) -> Path:
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
                    calibration_phases.append(_wait_phase(game,15,control,events,name,mode,0,detail))
                profile_rows=frame_rows(run_dir/"telemetry.csv")
                p1=phase_summary(profile_rows,"P1",15)
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
                if trace_perturbation is None or abs(trace_perturbation)>.05:
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
                baseline_period=int(1e9/max(measured_rate,1))
                manifest["calibration"]={"phases":calibration_phases,"profile":p1,"power":calibration_power,
                    "cpu_perturbation_fraction":cpu_perturbation,"sampled_trace_perturbation_fraction":trace_perturbation,
                    "swap_rate":measured_rate,"historical_swap_rate":reference_rate,"target_cadence_ns":baseline_period,
                    "limits":{"counters_cpu_frame":.03,"swap_rate":.03,"sampled_trace":.05}}
                if p1["median_update_cpu_ms"]>p1["median_update_wall_ms"]*1.05:
                    raise base.BenchmarkError("Thread CPU exceeds update wall time; timing is inconsistent")

                phases=[]
                for name,mode,duration,detail in PHASES:
                    cadence=0 if name=="A0" else int(1e9/30) if name=="E30" else int(1e9/15) if name=="E15" else baseline_period
                    phase=_wait_phase(game,duration,control,events,name,mode,cadence,detail)
                    phases.append(phase)
                    observed=phase_summary(frame_rows(run_dir/"telemetry.csv"),name,duration)
                    if observed["status"]!="complete": raise base.BenchmarkError(f"No frame records in {name}")
                    if name=="B" and (observed["median_render_wall_ms"]!=0 or observed["median_present_wall_ms"]!=0):
                        raise base.BenchmarkError("B did not skip Render/Present")
                    if name=="C" and (observed["median_suppressed_draws"]==0 or observed["median_forwarded_draws"]!=0):
                        raise base.BenchmarkError("C did not suppress every covered draw call")
                    if name=="D" and any(flags&(8|16|32) for flags in observed["record_flags"]):
                        raise base.BenchmarkError("D raster-work suppression state/context restoration failed")
                    if name.startswith("A") and observed["median_draws"]==0:
                        raise base.BenchmarkError(f"Control phase {name} had no observed draws")
                manifest["phases"]=phases
                manifest["profiling_tail"]=_wait_phase(game,30,control,events,"TAIL","profile",baseline_period,True)
                if include_low_power:
                    low={"phases":[],"transition_settle_s":10}
                    low["phases"].append(_wait_phase(game,15,control,events,"LPM0","profile",baseline_period))
                    low_power_active=True
                    low["low_mode"]=_set_power_mode("low",power_journal)
                    auto.mark(events,"power_mode_transition",mode="low"); time.sleep(10)
                    low["phases"].append(_wait_phase(game,30,control,events,"LPM","profile",baseline_period))
                    low["normal_mode"]=_set_power_mode("normal")
                    low_power_active=False
                    auto.mark(events,"power_mode_transition",mode="normal"); time.sleep(10)
                    low["phases"].append(_wait_phase(game,15,control,events,"LPM1","profile",baseline_period))
                    manifest["low_power_comparison"]=low
                    manifest["phases"].extend(low["phases"])
                auto.stop_process(pm,5); pm=None; power_tail.poll()
                all_phases=calibration_phases+manifest["phases"]+[manifest["profiling_tail"]]
                manifest["power_by_phase"]=diagnostic.summarize_power(power_tail.samples,all_phases,anchor,game.pid)
                manifest["status"]="complete"
                manifest["power_mode_comparison"]="completed" if include_low_power else "not requested"
                manifest["power"]={"samples":len(power_tail.samples)}
                (run_dir/"power.samples.json").write_text(json.dumps(power_tail.samples,default=str))
            finally:
                restore_failure=None
                if low_power_active:
                    try: manifest["low_power_restore"]=_set_power_mode("normal")
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
    report=sub.add_parser("report",help="regenerate a report from a captured run")
    report.add_argument("run_dir")
    args=parser.parse_args()
    try:
        if args.command=="preflight":
            result=preflight(not args.static_only,args.require_power_helper)
            print(json.dumps(result,indent=2))
        elif args.command=="report":
            print(json.dumps(analyze(Path(args.run_dir).expanduser().resolve()),indent=2))
        else:
            print(run(Path(args.output).expanduser().resolve()))
    except (base.BenchmarkError,OSError,ValueError,subprocess.SubprocessError,KeyboardInterrupt) as exc:
        print(f"Error: {exc}",file=sys.stderr); return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
