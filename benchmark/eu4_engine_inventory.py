#!/usr/bin/env python3
"""Inspect the version-pinned EU IV renderer entry points without changing the binary."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "analysis/frame-model-engine.json"
HEADER = ROOT / "benchmark/eu4_frame_model_sites.h"
BACKEND_GRAPH = ROOT / "analysis/frame-model-backend.json"
EXPECTED_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
IMAGE_BASE = 0x100000000

# Function signatures are implemented by the runtime probe. Do not add a hook
# until its ABI and 13-byte relocatable prologue have been verified here.
HOOKS = (
    ("update_one_frame", "__ZN12CApplication14UpdateOneFrameEb", "void(this,bool)", "loop"),
    ("idler_idle", "__ZN12CInGameIdler4IdleEb", "void(this,bool)", "idle"),
    ("idler_render", "__ZN12CInGameIdler6RenderEv", "void(this)", "render"),
    ("graphical_map_render", "__ZN16CEU3GraphicalMap6RenderEP21GfxDeferredContextGFXRK10CEU3Camerafb", "void(this,context,camera,float,bool)", "map"),
    ("present_scene", "__ZN9CGraphics12PresentSceneEv", "void(this)", "present_scene"),
    ("mesh_add_to_bucket", "__ZNK14CPdxMeshObject11AddToBucketEiPK7CCamera", "void(this,int,camera)", "bucket"),
    ("append_flush_data", "__ZN6CArrayI10SFlushDataE6AppendERKS0_", "void(this,SFlushData*)", "bucket_append"),
)
PROLOGUE_PREFIX = bytes.fromhex("55 48 89 e5")
GFX_GL = {
    "GfxDraw": ["glDrawArrays", "glDrawElements", "glDrawElementsBaseVertex"],
    "GfxDrawIndexed": ["glDrawElements", "glDrawElementsBaseVertex"],
    "GfxDrawInstanced": ["glDrawArraysInstanced", "glDrawElementsInstanced"],
    "GfxDraw2dLines": ["glDrawArrays", "glDrawElements"],
}
BACKEND_AREAS = (
    ("device_context_lifecycle", ["CGLCreateContext", "CGLSetCurrentContext", "CGLDestroyContext"],
     "context and device creation/destruction", "CGL compatibility context is observed; creation paths/frequencies are not counted", "Metal device, command queue, and drawable lifecycle; medium risk"),
    ("shader_program_lifecycle", ["glCreateShader", "glShaderSource", "glCompileShader", "glCreateProgram", "glAttachShader", "glLinkProgram", "glUseProgram"],
     "shader/program objects and GLSL source", "program binds are sampled; shader sources, compile/link calls, and GLSL feature corpus are not inventoried", "GLSL translation and shader validation; high risk until corpus is captured"),
    ("textures", ["glGenTextures", "glBindTexture", "glTexImage*", "glTexSubImage*", "glDeleteTextures"],
     "texture objects, formats, allocation, updates, and sampling", "binds and selected uploads are counted; creation, deletion, formats, sampler state, and PBO paths are partial", "MTLTexture and sampler conversion; format and lifetime mapping unknown"),
    ("vertex_index_buffers", ["glGenBuffers", "glBindBuffer", "glBufferData", "glBufferSubData", "glDeleteBuffers"],
     "vertex/index buffer objects and uploaded ranges", "selected binds/uploads are counted; mapping, storage flags, full lifecycle, and changed ranges are unknown", "MTLBuffer allocation and update strategy; lifecycle mapping unknown"),
    ("constants_uniforms", ["glUniform*", "glBindBufferRange", "glMapBuffer*"],
     "uniform payloads and constant data", "scalar float/int, float/int vectors, matrix2/3/4, glUniform1i and glUniform4fv are counted; sampled payload hashes are recorded, exact element diffs cover glUniform4fv only; unsigned/double/non-square families and buffer-backed constants remain uncovered", "argument buffers or per-frame constant buffers; layout/reflection work required"),
    ("render_targets", ["glGenFramebuffers", "glBindFramebuffer", "glFramebufferTexture*", "glBlitFramebuffer"],
     "FBO attachments, render targets, and resolve/copy paths", "not inventoried by the current runtime hooks", "MTLRenderPassDescriptor and attachment lifecycle; topology unknown"),
    ("depth_stencil_blend_raster", ["glDepth*", "glStencil*", "glBlend*", "glCullFace", "glPolygon*", "glEnable", "glDisable"],
     "depth/stencil, blend, culling, and raster state", "only selected enable/disable and raster-discard state are observed; complete values and transitions unknown", "MTLDepthStencilState and pipeline-state mapping; state key coverage unknown"),
    ("vertex_input", ["glVertexAttribPointer", "glEnableVertexAttribArray", "glVertexAttribDivisor", "glBindVertexArray"],
     "vertex formats, VAOs, divisors, and element-buffer bindings", "vertex source identity is sampled for detailed draws; lifecycle and all attribute state are partial", "MTLVertexDescriptor and argument mapping; compatibility client arrays must be ruled out"),
    ("draw_submission", ["GfxDraw*", "glDrawArrays*", "glDrawElements*"],
     "direct, indexed, instanced, and base-vertex draws", "static direct Gfx callsites are inventoried; six GL draw forms are hooked; indirect/multi-draw and extension paths remain uncovered", "MTLRenderCommandEncoder draw variants; straightforward only after full call coverage"),
    ("copies_readback", ["glCopyTex*", "glReadPixels", "glGetTexImage", "glBlitFramebuffer"],
     "resource copies, readback, and capture operations", "not inventoried by the current runtime hooks", "blit/compute/readback equivalents depend on actual usage"),
    ("synchronization", ["glFenceSync", "glClientWaitSync", "glFinish", "CGLFlushDrawable"],
     "GPU fences, waits, flush, and drawable presentation", "CGLFlushDrawable wait is timed; GL sync-object coverage is absent", "MTLSharedEvent/command-buffer completion and CAMetalLayer presentation; ordering semantics unknown"),
)
ADDRESS = re.compile(r"^([0-9a-f]{16}) \(__TEXT,__text\) .*? (\S+)$")


def inventory() -> dict:
    binary = base.GOG_EXE
    digest = base.sha256(binary)
    if digest != EXPECTED_SHA256:
        raise base.BenchmarkError("Installed GOG EU IV is not the pinned v1.37.5 executable")
    listed = subprocess.run(["nm", "-nm", str(binary)], capture_output=True,
                            text=True, check=True).stdout
    symbols: dict[str, int] = {}
    for line in listed.splitlines():
        match = ADDRESS.match(line)
        if match:
            symbols[match[2]] = int(match[1], 16)
    rows = []
    for name, symbol, abi, scope in HOOKS:
        address = symbols.get(symbol)
        if address is None:
            raise base.BenchmarkError(f"Required renderer symbol missing: {symbol}")
        offset = address - IMAGE_BASE
        disassembly = subprocess.run(
            ["xcrun", "llvm-objdump", "--disassemble-symbols=" + symbol,
             "--no-show-raw-insn", str(binary)], capture_output=True,
            text=True, check=True).stdout
        raw = subprocess.run(["xcrun", "llvm-objdump", "--disassemble-symbols=" + symbol,
                              str(binary)], capture_output=True, text=True, check=True).stdout
        instructions = []
        first = []
        for line in raw.splitlines():
            match = re.match(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2}\s+)+)\s*\t([a-z][a-z0-9.]*)\s*(.*)$", line)
            if not match:
                continue
            blob = bytes.fromhex(match[2])
            instructions.append({"address": int(match[1], 16),
                                 "bytes": blob.hex(), "mnemonic": match[3],
                                 "operands": match[4]})
            if len(first) < 13:
                first.extend(blob)
        boundaries = [sum(len(bytes.fromhex(row["bytes"])) for row in instructions[:i])
                      for i in range(1, len(instructions)+1)]
        patch_bytes = next((value for value in boundaries if 13 <= value <= 24), None)
        if patch_bytes is None or bytes(first[:4]) != PROLOGUE_PREFIX:
            raise base.BenchmarkError(f"Hook prologue is not relocatable/pinned: {symbol}")
        prologue=[item for item in instructions
                  if item["address"]<address+patch_bytes]
        if any(re.search(r"\(%rip\)|\brip\b",item["operands"]) or
               item["mnemonic"].startswith(("call","jmp","loop","j"))
               for item in prologue):
            raise base.BenchmarkError(f"Hook prologue needs relative-instruction relocation: {symbol}")
        if address%4096+patch_bytes>4096:
            raise base.BenchmarkError(f"Hook patch crosses a page boundary: {symbol}")
        rows.append({"name": name, "symbol": symbol, "offset": offset,
                     "abi": abi, "scope": scope, "patch_bytes": patch_bytes,
                     "expected_prefix": bytes(first[:patch_bytes]).hex(),
                     "instructions": instructions[:8]})

    # Build an offline call graph from this executable's symbolized disassembly.
    calls = subprocess.run(["xcrun", "llvm-objdump", "--disassemble-symbols=" +
                            ",".join(row["symbol"] for row in rows),
                            "--symbolize-operands", str(binary)],
                           capture_output=True, text=True, check=True).stdout
    graph: dict[str, list[str]] = {}
    current = None
    for line in calls.splitlines():
        header = re.search(r"<(__Z[^>]+)>:", line)
        if header:
            current = header[1]
            graph.setdefault(current, [])
            continue
        if current:
            edge = re.search(r"\b(?:callq|jmpq)\s+.*?<(__Z[^>]+)>", line)
            if edge and edge[1] not in graph[current]:
                graph[current].append(edge[1])
    return {"schema": 1, "executable_sha256": digest, "image_base": IMAGE_BASE,
            "hooks": rows, "call_graph": graph,
            "backend_inventory": {"status": "static Gfx callsites mapped to candidate GL operations; runtime resource/state mapping remains partial",
                                  "known_gl_sites": "analysis/draw-callers.json",
                                  "backend_graph": "analysis/frame-model-backend.json"},
            "limitations": ["Indirect edges and runtime-loaded graphics calls require dynamic attribution.",
                            "SFlushData records expose established fields only; unknown fields remain opaque."]}


def backend_graph() -> dict:
    source = ROOT / "analysis/draw-callers.json"
    data = json.loads(source.read_text())
    if data.get("executable_sha256") != EXPECTED_SHA256:
        raise base.BenchmarkError("Gfx callsite inventory belongs to a different executable")
    grouped = {}
    for row in data.get("direct_sites", []):
        operation = row.get("operation", "unknown")
        item = grouped.setdefault(operation, {"gfx_operation": operation, "callers": [],
            "candidate_opengl_operations": GFX_GL.get(operation, []),
            "resources_consumed_or_produced": "unknown from callsite alone",
            "state_dependencies": "unknown without implementation/runtime state capture",
            "compatibility_profile_assumptions": "unverified",
            "metal_equivalent": "draw operations have direct Metal equivalents; resource translation is unverified"})
        item["callers"].append({"symbol": row.get("owner_symbol"), "name": row.get("owner"),
                                "category": row.get("category"),
                                "callsite_offset": row.get("callsite_offset")})
    return {"schema": 3, "status":"feasibility seed", "metal_go_no_go":"undetermined",
            "executable_sha256": data.get("executable_sha256"),
            "source": "analysis/draw-callers.json", "operations": list(grouped.values()),
            "backend_areas":[{"area":area,"candidate_gl_operations":gl_ops,
                "resources_and_contract":resources,"runtime_coverage":coverage,
                "metal_equivalent_and_difficulty":metal,
                "clausewitz_gfx_implementation":"not yet mapped to a concrete implementation symbol",
                "runtime_frequency":"not measured or not covered by current hook set",
                "compatibility_profile_assumptions":"must be confirmed from the pinned binary and live contexts"}
                for area,gl_ops,resources,coverage,metal in BACKEND_AREAS],
            "observed_runtime_gl_entrypoints": ["draw", "buffer", "texture", "uniform", "program", "state", "present"],
            "mapping_confidence": "seed feasibility matrix; Gfx implementation and resource/state semantics still need disassembly or runtime tracing",
            "known_limits": data.get("limits", [])}


def write_header(data: dict) -> None:
    lines = ["// Generated by eu4_engine_inventory.py for the pinned GOG binary.",
             "typedef struct { const char *name; uintptr_t offset; unsigned patch; const char *prefix; } EU4HookSite;",
             f"#define EU4_HOOK_COUNT {len(data['hooks'])}u",
             "static const EU4HookSite eu4_hook_sites[EU4_HOOK_COUNT] = {"]
    lines.extend(f'    {{"{row["name"]}", 0x{row["offset"]:x}u, {row["patch_bytes"]}u, "{row["expected_prefix"]}"}},'
                 for row in data["hooks"])
    lines.append("};")
    HEADER.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    args = parser.parse_args()
    try:
        data = inventory()
        if args.command == "generate":
            OUTPUT.write_text(json.dumps(data, indent=2) + "\n")
            write_header(data)
            BACKEND_GRAPH.write_text(json.dumps(backend_graph(), indent=2) + "\n")
        elif (not OUTPUT.is_file() or json.loads(OUTPUT.read_text()) != data or
              not HEADER.is_file() or not BACKEND_GRAPH.is_file() or
              json.loads(BACKEND_GRAPH.read_text()) != backend_graph()):
            raise base.BenchmarkError("Generated engine inventory is stale; run generate")
        print(f"{len(data['hooks'])} pinned entry points; {args.command} passed")
        return 0
    except (base.BenchmarkError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
