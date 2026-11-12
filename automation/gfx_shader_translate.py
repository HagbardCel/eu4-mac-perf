#!/usr/bin/env python3
"""GLSL 1.20 compiler inputs -> Metal Shading Language.

The engine's generated GLSL is a stable dialect: a shared prelude (``float4``
macros, ``tex2D`` helpers, ``mul``) plus a generated ``main``. This module
normalizes that dialect to GLSL 4.50, gives every attribute, varying, uniform
block and sampler an explicit layout, compiles through glslang to SPIR-V and
emits MSL with SPIRV-Cross. Nothing here is scene specific: every program is
identified by the exact source SHA-256 recorded in the frame trace.

Outputs under ``<out>/``:

    shaders.json          program -> stage sources, MSL files, bindings, layout
    <sha>.vert.metal      translated vertex stage
    <sha>.frag.metal      translated fragment stage
    <sha>.vert.spv        intermediate SPIR-V (diagnostics)
    translation.json      per-program status, errors and coverage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GLSL_VERSION = "#version 450\n"
UNIFORM_BLOCK_NAME = "EngineUniforms"
UNIFORM_BLOCK_INSTANCE = "engine"
UNIFORM_BLOCK_BINDING = 7
MAX_SAMPLER_BINDINGS = 31

# The engine's generated GLSL occasionally declares uniforms with the HLSL
# spellings that its prelude later #defines (float4 -> vec4). The uniform block
# is emitted before the prelude, so canonical GLSL names are used there.
TYPE_ALIASES = {
    "float2": "vec2",
    "float3": "vec3",
    "float4": "vec4",
    "float2x2": "mat2",
    "float3x3": "mat3",
    "float4x4": "mat4",
    "int2": "ivec2",
    "int3": "ivec3",
    "int4": "ivec4",
    "uint": "int",
    "uint2": "ivec2",
    "uint3": "ivec3",
    "uint4": "ivec4",
    "bool2": "ivec2",
    "bool3": "ivec3",
    "bool4": "ivec4",
}

VEC_TYPES = {
    "float": (1, 4),
    "vec2": (2, 8),
    "vec3": (3, 12),
    "vec4": (4, 16),
    "int": (1, 4),
    "ivec2": (2, 8),
    "ivec3": (3, 12),
    "ivec4": (4, 16),
    "mat2": (4, 16),
    "mat3": (9, 36),
    "mat4": (16, 64),
    "bool": (1, 4),
}


class TranslationError(RuntimeError):
    pass


def _compile_tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise TranslationError(f"required tool is not installed: {name}")
    return found


def _round_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _std140_layout(declarations: list[dict]) -> tuple[list[dict], str | None]:
    offset = 0
    members: list[dict] = []
    for item in declarations:
        name = item["name"]
        kind = item["type"]
        elements = item["elements"]
        if kind not in VEC_TYPES:
            return [], f"unsupported uniform type {kind}"
        components, size = VEC_TYPES[kind]
        if kind.startswith("mat"):
            columns = {"mat2": 2, "mat3": 3, "mat4": 4}[kind]
            column_size = {"mat2": 8, "mat3": 12, "mat4": 16}[kind]
            alignment = 16
            if elements > 1:
                # std140 matrix arrays: column stride rounded to 16
                column_bytes = _round_up(column_size, 16)
                if column_size != column_bytes:
                    return [], f"std140 matrix array layout mismatch for {name}"
                element_stride = column_bytes * columns
            else:
                element_stride = column_bytes * columns
            if elements == 1 and kind == "mat2":
                element_stride = 32
            if elements == 1 and kind == "mat3":
                element_stride = 48
            offset = _round_up(offset, alignment)
            members.append(
                {
                    "name": name,
                    "type": kind,
                    "elements": elements,
                    "offset": offset,
                    "components": components,
                    "array": item.get("array", elements > 1),
                }
            )
            offset += element_stride * elements
            continue
        alignment = size if elements == 1 else _round_up(size, 16)
        if elements > 1 and kind in ("float", "int", "bool", "vec2"):
            # std140 rounds the array element stride up to 16; MSL does not.
            if size % 16 != 0:
                return [], (
                    f"std140 array stride for {name} ({kind}[{elements}]) "
                    "does not match MSL natural layout"
                )
        offset = _round_up(offset, alignment if elements == 1 else 16)
        members.append(
            {
                "name": name,
                "type": kind,
                "elements": elements,
                "offset": offset,
                "components": components,
                "array": item.get("array", elements > 1),
            }
        )
        stride = size if elements == 1 else _round_up(size, 16)
        offset += stride * elements
    return members, None


UNIFORM_DECL = re.compile(
    r"^[ \t]*uniform[ \t]+(?P<type>[A-Za-z_][A-Za-z0-9_]*)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*(?:\[(?P<count>[^\]]+)\])?[ \t]*;",
    re.M,
)
ATTRIBUTE_DECL = re.compile(
    r"^[ \t]*attribute[ \t]+(?P<type>[A-Za-z_][A-Za-z0-9_]*)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*;",
    re.M,
)
VARYING_DECL = re.compile(
    r"^[ \t]*varying[ \t]+(?P<type>[A-Za-z_][A-Za-z0-9_]*)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*;",
    re.M,
)
VERSION_LINE = re.compile(r"^[ \t]*#version[^\n]*\n", re.M)
EXTENSION_LINE = re.compile(
    r"^[ \t]*#extension[ \t]+GL_ARB_shader_texture_lod[^\n]*\n", re.M
)

LEGACY_TEXTURE_FUNCTIONS = [
    ("texture2DProj", "textureProj"),
    ("texture2DLod", "textureLod"),
    ("texture2D", "texture"),
    ("textureCubeLod", "textureLod"),
    ("textureCube", "texture"),
    ("texture1DLod", "textureLod"),
    ("texture1D", "texture"),
    ("texture3DLod", "textureLod"),
    ("texture3D", "texture"),
]


def compiler_input(source: str) -> str:
    """The exact bytes the engine passes to _GfxAddVertexShader/_GfxAddPixelShader.

    The GL driver-visible source (glGetShaderSource) carries the
    GL_ARB_shader_texture_lod extension line; BuildGLSL hands the compiler the
    NO_SHADER_TEXTURE_LOD define instead. The live shader cache must be keyed by
    that compiler input, not by the driver copy.
    """
    return source.replace(
        "#extension GL_ARB_shader_texture_lod : enable",
        "#define NO_SHADER_TEXTURE_LOD",
    )


def _stage_kind(stage: str) -> str:
    if stage == "vertex":
        return "vert"
    if stage == "pixel":
        return "frag"
    raise TranslationError(f"unsupported shader stage {stage}")


def _parse_declarations(source: str) -> list[dict]:
    found: list[dict] = []
    seen: dict[str, dict] = {}
    for match in UNIFORM_DECL.finditer(source):
        kind = match.group("type")
        if kind.startswith("sampler"):
            continue
        count = match.group("count")
        if count is None:
            elements = 1
        else:
            try:
                elements = int(count.strip())
            except ValueError as exc:
                raise TranslationError(
                    f"non-literal uniform array size for {match.group('name')}"
                ) from exc
        canonical = TYPE_ALIASES.get(kind, kind)
        if match.group("name") in seen:
            # The engine emits a prelude declaration and the generated one.
            previous = seen[match.group("name")]
            if previous["type"] != canonical or previous["elements"] != elements:
                raise TranslationError(
                    f"conflicting redeclaration of uniform {match.group('name')}"
                )
            continue
        record = {
                "name": match.group("name"),
                "type": canonical,
                "elements": elements,
                "array": count is not None,
                "span": match.span(),
        }
        found.append(record)
        seen[record["name"]] = record
    return found


def _normalize_source(source: str, stage: str) -> dict:
    text = source
    text = VERSION_LINE.sub("", text)
    text = EXTENSION_LINE.sub("", text)
    for old, new in LEGACY_TEXTURE_FUNCTIONS:
        text = text.replace(old, new)
    if "gl_FragData" in text:
        raise TranslationError("gl_FragData is not supported by the single-output path")
    for declaration in _parse_declarations(text):
        pass
    uniforms = _parse_declarations(text)
    members, layout_error = _std140_layout(uniforms)
    if layout_error is not None:
        raise TranslationError(layout_error)
    # Remove uniform declarations; they move into the std140 block.
    for item in sorted(uniforms, key=lambda value: value["span"][0], reverse=True):
        start, end = item["span"]
        text = text[:start] + text[end:]
    samplers: list[dict] = []
    sampler_binding = 0

    def sampler_replacement(match: re.Match) -> str:
        nonlocal sampler_binding
        kind = match.group("type")
        name = match.group("name")
        if sampler_binding > MAX_SAMPLER_BINDINGS:
            raise TranslationError("too many sampler bindings")
        samplers.append(
            {"name": name, "type": kind, "binding": sampler_binding}
        )
        sampler_binding += 1
        return f"layout(binding={samplers[-1]['binding']}) uniform {kind} {name};"

    text = re.sub(
        r"^[ \t]*uniform[ \t]+(?P<type>sampler[0-9A-Za-z_]*)[ \t]+"
        r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*;",
        sampler_replacement,
        text,
        flags=re.M,
    )
    block = ""
    if members:
        lines = [
            f"layout(std140, binding={UNIFORM_BLOCK_BINDING}) "
            f"uniform {UNIFORM_BLOCK_NAME} {{"
        ]
        for item in members:
            suffix = (
                f"[{item['elements']}]"
                if item.get("array") or item["elements"] > 1
                else ""
            )
            lines.append(f"    {item['type']} {item['name']}{suffix};")
        lines.append(f"}} {UNIFORM_BLOCK_INSTANCE};")
        lines.extend(
            f"#define {item['name']} {UNIFORM_BLOCK_INSTANCE}.{item['name']}"
            for item in members
        )
        block = "\n".join(lines) + "\n"

    attributes: list[dict] = []
    for match in ATTRIBUTE_DECL.finditer(text):
        attributes.append(
            {"name": match.group("name"), "type": match.group("type")}
        )
    varyings: list[dict] = []
    for match in VARYING_DECL.finditer(text):
        varyings.append(
            {"name": match.group("name"), "type": match.group("type")}
        )

    if stage == "vertex":
        text = re.sub(r"\battribute\b", "in", text)
        text = re.sub(r"\bvarying\b", "out", text)
    else:
        text = re.sub(r"\bvarying\b", "in", text)
        text = text.replace("gl_FragColor", "fragColor")
        if "void main(" in text:
            text = text.replace(
                "void main(", "out vec4 fragColor;\nvoid main(", 1
            )
        elif "void main (" in text:
            text = text.replace(
                "void main (", "out vec4 fragColor;\nvoid main (", 1
            )
        else:
            raise TranslationError("fragment stage has no main entry point")
    text = GLSL_VERSION + block + text
    return {
        "source": text,
        "uniforms": members,
        "samplers": samplers,
        "attributes": attributes,
        "varyings": varyings,
    }


def _inject_attribute_locations(source: str, locations: dict[str, int]) -> str:
    for name, location in sorted(locations.items(), key=lambda item: item[1]):
        pattern = re.compile(
            r"^[ \t]*in[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+"
            + re.escape(name)
            + r"[ \t]*;",
            re.M,
        )
        source = pattern.sub(
            lambda match: f"layout(location={location}) in {match.group(1)} {name};",
            source,
        )
    return source


def _inject_varying_locations(
    source: str, locations: dict[str, int], direction: str
) -> str:
    for name, location in sorted(locations.items(), key=lambda item: item[1]):
        pattern = re.compile(
            r"^[ \t]*" + direction + r"[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]+"
            + re.escape(name)
            + r"[ \t]*;",
            re.M,
        )
        source = pattern.sub(
            lambda match: (
                f"layout(location={location}) {direction} "
                f"{match.group(1)} {name};"
            ),
            source,
        )
    return source


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=timeout
    )


def translate_stage(
    source_text: str,
    stage: str,
    out_dir: Path,
    sha: str,
    attribute_locations: dict[str, int] | None,
    varying_locations: dict[str, int] | None,
) -> dict:
    glslang = _compile_tool("glslangValidator")
    spirv_cross = _compile_tool("spirv-cross")
    normalized = _normalize_source(source_text, stage)
    text = normalized["source"]
    complete_locations: dict[str, int] = {}
    if stage == "vertex":
        # Every declared attribute needs an explicit location: glslang's
        # automatic mapping would otherwise collide with the captured GL
        # locations. Attributes the draw never enabled receive a high,
        # collision-free index and are supplied as constant values at replay.
        declared = [item["name"] for item in normalized["attributes"]]
        used: set[int] = set()
        for name, location in (attribute_locations or {}).items():
            if name in declared:
                complete_locations[name] = int(location)
                used.add(int(location))
        next_index = 16
        for name in sorted(declared):
            if name in complete_locations:
                continue
            while next_index in used:
                next_index += 1
            complete_locations[name] = next_index
            used.add(next_index)
            next_index += 1
        text = _inject_attribute_locations(text, complete_locations)
    if varying_locations:
        text = _inject_varying_locations(
            text, varying_locations, "out" if stage == "vertex" else "in"
        )
    suffix = _stage_kind(stage)
    glsl_path = out_dir / f"{sha}.{suffix}.glsl"
    spv_path = out_dir / f"{sha}.{suffix}.spv"
    msl_path = out_dir / f"{sha}.{suffix}.metal"
    glsl_path.write_text(text)
    compiled = _run(
        [glslang, "-V", "--aml", "-S", suffix, str(glsl_path), "-o", str(spv_path)]
    )
    if compiled.returncode != 0 or not spv_path.is_file():
        raise TranslationError(
            (compiled.stderr or compiled.stdout or "glslang failed").strip()
        )
    translated = _run(
        [
            spirv_cross,
            str(spv_path),
            "--msl",
            "--msl-version",
            "20100",
            "--output",
            str(msl_path),
        ]
    )
    if translated.returncode != 0 or not msl_path.is_file():
        raise TranslationError(
            (translated.stderr or translated.stdout or "spirv-cross failed").strip()
        )
    msl = msl_path.read_text()
    return {
        "stage": stage,
        "source_sha256": sha,
        "compiler_sha256": hashlib.sha256(
            compiler_input(source_text).encode()
        ).hexdigest(),
        "attribute_locations": complete_locations if stage == "vertex" else {},
        "glsl_file": glsl_path.name,
        "spv_file": spv_path.name,
        "msl_file": msl_path.name,
        "msl_entry": _msl_entry(msl, stage),
        "msl_uniform_buffer": _msl_uniform_buffer(msl),
        "msl_samplers": _msl_samplers(msl),
        "uniforms": normalized["uniforms"],
        "samplers": normalized["samplers"],
        "attributes": normalized["attributes"],
        "varyings": normalized["varyings"],
    }


def _msl_entry(msl: str, stage: str) -> str | None:
    keyword = "vertex" if stage == "vertex" else "fragment"
    match = re.search(
        rf"^{keyword}\s+\w+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", msl, re.M
    )
    return match.group(1) if match else None


def _msl_uniform_buffer(msl: str) -> int | None:
    match = re.search(
        rf"constant\s+{UNIFORM_BLOCK_NAME}\s*&\s*\w+\s*\[\[buffer\((\d+)\)\]\]",
        msl,
    )
    return int(match.group(1)) if match else None


def _msl_samplers(msl: str) -> list[dict]:
    found = []
    for match in re.finditer(
        r"(texture2d|texturecube|texture3d|texture2d_array|depth2d)"
        r"\s*<\s*float\s*>\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"\s*\[\[texture\((\d+)\)\]\]",
        msl,
    ):
        found.append(
            {
                "kind": match.group(1),
                "name": match.group(2),
                "texture_index": int(match.group(3)),
            }
        )
    for match in re.finditer(
        r"sampler\s+([A-Za-z_][A-Za-z0-9_]*)\s*\[\[sampler\((\d+)\)\]\]", msl
    ):
        name = match.group(1)
        for item in found:
            if name == item["name"] + "Smplr":
                item["sampler_index"] = int(match.group(2))
    return found


def translate_trace(frame_dir: Path, out_dir: Path) -> dict:
    frame_dir = Path(frame_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = frame_dir / "frame-events.jsonl"
    try:
        events = [
            json.loads(line)
            for line in events_path.read_text().splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationError(f"unreadable frame events: {exc}") from exc
    blobs = frame_dir / "blobs"
    programs: dict[int, dict] = {}
    for event in events:
        if event.get("kind") != "draw":
            continue
        program = event.get("program") or {}
        name = program.get("name")
        if name is None:
            continue
        stages = {
            item.get("stage"): item for item in program.get("shaders") or []
        }
        if "vertex" not in stages or "pixel" not in stages:
            raise TranslationError(f"program {name} lacks both stages")
        attribute_map = {}
        for attribute in event.get("attributes") or []:
            attribute_map[str(attribute.get("name"))] = int(attribute["location"])
        record = programs.setdefault(
            int(name),
            {
                "sources": {},
                "attributes": {},
                "draws": 0,
                "varying_types": {},
            },
        )
        record["draws"] += 1
        record["attributes"].update(attribute_map)
        for stage, item in stages.items():
            digest = item.get("sha256")
            path = blobs / str(item.get("file"))
            if not path.is_file():
                raise TranslationError(f"missing shader blob {path}")
            source = path.read_text()
            actual = hashlib.sha256(source.encode()).hexdigest()
            if actual != digest:
                raise TranslationError(f"shader blob digest mismatch {path}")
            record["sources"].setdefault(stage, {"sha256": digest, "source": source})
            for match in VARYING_DECL.finditer(source):
                record["varying_types"].setdefault(
                    match.group("name"), match.group("type")
                )
    report: dict = {
        "schema": "gfx-shader-translation/v1",
        "frame": str(frame_dir),
        "programs": [],
        "ok": True,
    }
    for name, record in sorted(programs.items()):
        entry = {
            "program": name,
            "draws": record["draws"],
            "status": "ok",
            "sources": [
                {"stage": stage, "sha256": item["sha256"]}
                for stage, item in record["sources"].items()
            ],
        }
        try:
            varying_locations = {
                name_: index
                for index, name_ in enumerate(sorted(record["varying_types"]))
            }
            stages_out = {}
            for stage, item in record["sources"].items():
                stages_out[stage] = translate_stage(
                    item["source"],
                    stage,
                    out_dir,
                    item["sha256"],
                    record["attributes"] if stage == "vertex" else None,
                    varying_locations,
                )
            entry["stages"] = stages_out
            entry["varying_locations"] = varying_locations
            entry["attribute_locations"] = record["attributes"]
        except (TranslationError, OSError, subprocess.SubprocessError) as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
            report["ok"] = False
        report["programs"].append(entry)
    (out_dir / "shaders.json").write_text(json.dumps(report, indent=2) + "\n")
    (out_dir / "translation.json").write_text(json.dumps(report, indent=2) + "\n")
    write_shader_cache(report, out_dir)
    return report


def translate_live_sources(source_dir: Path, out_dir: Path,
                           existing: Path | None = None) -> dict:
    """Translates the engine's own compiler inputs into the runtime cache.

    The live backend writes every _GfxAddVertexShader/_GfxAddPixelShader source
    verbatim (`shader-source-<stage>-<sha>.glsl`). Those bytes are the compiler
    input, so their SHA-256 is the cache key; no normalization is needed.
    Existing entries (with captured attribute locations) win on a hash match.
    """
    source_dir = Path(source_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stages: dict[str, dict] = {}
    if existing is not None:
        try:
            prior = json.loads(Path(existing).read_text())
        except (OSError, json.JSONDecodeError):
            prior = {}
        for key, entry in (prior.get("stages") or {}).items():
            if isinstance(entry, dict):
                stages[key] = entry
                msl = entry.get("msl_file")
                if msl:
                    src = Path(existing).parent / msl
                    dst = out_dir / msl
                    if src.is_file() and not dst.exists():
                        shutil.copyfile(src, dst)
    translated = 0
    failed = []
    for path in sorted(source_dir.glob("shader-source-*.glsl")):
        name = path.name
        if "-vs-" in name:
            stage = "vertex"
        elif "-ps-" in name:
            stage = "pixel"
        else:
            continue
        text = path.read_text()
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest in stages:
            continue
        try:
            record = translate_stage(text, stage, out_dir, digest, None, None)
        except TranslationError as exc:
            failed.append({"file": name, "error": str(exc)})
            continue
        stages[digest] = record
        translated += 1
    report = {
        "schema": "gfx-shader-cache/v1",
        "source": str(source_dir),
        "stages": stages,
        "programs": {},
        "translated": translated,
        "failed": failed,
    }
    (out_dir / "shader-cache.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def write_shader_cache(report: dict, out_dir: Path) -> dict:
    """Emits the runtime shader cache used by the live Metal backend.

    The live path admits a stage only by its exact source SHA-256, exactly like
    the offline replay: no markers, names or caller-specific selection. Each
    entry carries the MSL entry point, attribute/varying locations, std140
    uniform layout and sampler bindings the generic encoder needs.
    """
    stages: dict[str, dict] = {}
    programs: dict[str, dict] = {}
    for program in report.get("programs") or []:
        if program.get("status") != "ok":
            continue
        hashes = {}
        entry = {"program": program["program"], "stages": {}}
        for stage_name, stage in (program.get("stages") or {}).items():
            source_sha = None
            for item in program.get("sources") or []:
                if item.get("stage") == stage_name:
                    source_sha = item.get("sha256")
            if source_sha is None:
                # The translation report does not repeat source hashes; derive
                # them from the recorded stage source files' digests instead.
                source_sha = stage.get("source_sha256")
            hashes[stage_name] = source_sha
            record = {
                "stage": stage_name,
                "source_sha256": source_sha,
                "compiler_sha256": stage.get("compiler_sha256"),
                "msl_file": stage.get("msl_file"),
                "msl_entry": stage.get("msl_entry"),
                "msl_uniform_buffer": stage.get("msl_uniform_buffer"),
                "msl_samplers": stage.get("msl_samplers") or [],
                "attribute_locations": stage.get("attribute_locations") or {},
                "varyings": stage.get("varyings") or [],
                "uniforms": stage.get("uniforms") or [],
                "samplers": stage.get("samplers") or [],
            }
            key_sha = stage.get("compiler_sha256") or source_sha
            if key_sha:
                stages[key_sha] = record
            entry["stages"][stage_name] = record
        key = "|".join(
            str((entry["stages"].get(stage_name) or {}).get("compiler_sha256") or "")
            for stage_name in ("vertex", "pixel")
        )
        if key.strip("|"):
            programs[key] = entry
    cache = {
        "schema": "gfx-shader-cache/v1",
        "note": (
            "Runtime cache for the live Metal backend. Stages are admitted by "
            "exact compiler-input SHA-256; the cache contains no game logic."
        ),
        "stages": stages,
        "programs": programs,
    }
    (out_dir / "shader-cache.json").write_text(json.dumps(cache, indent=2) + "\n")
    return cache


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "translate-live":
        parser = argparse.ArgumentParser(prog="gfx_shader_translate translate-live")
        parser.add_argument("--sources", required=True)
        parser.add_argument("--out", required=True)
        parser.add_argument("--merge", default=None)
        args = parser.parse_args(argv[1:])
        report = translate_live_sources(
            Path(args.sources).expanduser().resolve(),
            Path(args.out).expanduser().resolve(),
            Path(args.merge).expanduser().resolve() if args.merge else None,
        )
        print(json.dumps({
            "status": "ok" if not report["failed"] else "partial",
            "stages": len(report["stages"]),
            "translated": report["translated"],
            "failed": report["failed"][:5],
            "out": str(Path(args.out).expanduser().resolve()),
        }, indent=2))
        return 0 if not report["failed"] else 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", required=True)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    frame_dir = Path(args.frame).expanduser().resolve()
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else frame_dir / "shaders"
    )
    try:
        report = translate_trace(frame_dir, out_dir)
    except TranslationError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    summary = {
        "status": "ok" if report["ok"] else "partial",
        "programs": len(report["programs"]),
        "failed": [
            {"program": entry["program"], "error": entry.get("error")}
            for entry in report["programs"]
            if entry["status"] != "ok"
        ],
        "out": str(out_dir),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["ok"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
