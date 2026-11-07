#!/usr/bin/env python3
"""P5.2b lifecycle acceptance — completion requires engine_path_complete."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REQUIRED_ENTRIES = ("link_shader", "destroy_shader", "set_shader")
TEST_MODULES = (
    "tests.test_gfx_shader",
    "tests.test_gfx_entry_registry",
)
BUILD_FUNCS = (
    "_GfxBuildAndAddVertexShader",
    "_GfxBuildAndAddPixelShader",
    "BuildGLSL",
)


def trusted_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def check_build_dispositions(workspace: Path) -> str | None:
    path = workspace / "analysis" / "gfx-shader" / "v0" / "lifecycle-disposition.json"
    if not path.is_file():
        return "missing lifecycle-disposition.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "lifecycle-disposition.json unreadable"
    broader = data.get("broader_callees")
    if not isinstance(broader, list):
        return "lifecycle-disposition.broader_callees required"
    by_name = {
        e.get("name"): e
        for e in broader
        if isinstance(e, dict) and isinstance(e.get("name"), str)
    }
    for name in BUILD_FUNCS:
        entry = by_name.get(name)
        if entry is None:
            return f"lifecycle broader_callees missing {name}"
        if entry.get("disposition") not in {
            "replace-metal",
            "verified-non-execution",
            "BLOCKER",
        }:
            return f"{name} disposition required (replace-metal|verified-non-execution|BLOCKER)"
    return None


def check_markers_default_off(workspace: Path) -> str | None:
    body = (workspace / "benchmark" / "eu4_gfx_shader.mm").read_text(encoding="utf-8")
    if "g_allow_transitional_markers = 0" not in body:
        return "g_allow_transitional_markers must default to 0 (stage markers off)"
    if "eu4_gfx_shader_set_allow_transitional_markers" not in body:
        return "missing eu4_gfx_shader_set_allow_transitional_markers opt-in"
    return None


def check(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    entries = (workspace / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8")
    for name in REQUIRED_ENTRIES:
        if f"GFX_ENTRY({name}," not in entries:
            return emit("fail", f"missing GFX_ENTRY {name}")
    header = (workspace / "benchmark" / "eu4_gfx_shader.h").read_text(encoding="utf-8")
    for needle in (
        "eu4_gfx_link_shader",
        "eu4_gfx_set_shader",
        "eu4_gfx_destroy_shader",
        "eu4_gfx_shader_lifetime_id",
        "eu4_gfx_shader_register_qualified_sha256",
        "eu4_gfx_shader_is_current",
        "eu4_gfx_shader_set_allow_transitional_markers",
    ):
        if needle not in header:
            return emit("fail", f"header missing {needle}")
    body = (workspace / "benchmark" / "eu4_gfx_shader.mm").read_text(encoding="utf-8")
    if "simple_vs" not in body or "MTLRenderPipelineState" not in body:
        return emit("fail", "shader.mm missing Simple Metal stages / pipeline path")
    if "hash_is_qualified" not in body and "g_qualified_hashes" not in body:
        return emit("fail", "shader.mm missing exact-hash admission path")
    if "record.linked = NO" not in body:
        return emit("fail", "shader.mm missing fail-closed link invalidation")
    err = check_markers_default_off(workspace)
    if err:
        return emit("fail", err)
    err = check_build_dispositions(workspace)
    if err:
        return emit("fail", err)

    frame = workspace / "analysis" / "frame-model-static.json"
    if not frame.is_file():
        return emit(
            "fail",
            "engine_path_complete required for P5.2b completion; "
            "missing analysis/frame-model-static.json call-chain evidence "
            "(offline lifecycle tests alone are not completion)",
        )
    sys.path.insert(0, str(trusted_root()))
    from automation.call_chain import assess_construction_closure

    assessment = assess_construction_closure(frame)
    if not assessment.get("engine_path_complete"):
        gl = assessment.get("reachable_gl_symbols") or []
        return emit(
            "fail",
            "engine_path_complete required for P5.2b completion campaign; "
            "offline_lifecycle_qualification_only is not a pass. "
            f"reachable_gl={gl[:12]}",
        )

    qualified = workspace / "analysis" / "gfx-shader" / "v0" / "qualified-sources.json"
    if not qualified.is_file():
        return emit("fail", "missing qualified-sources.json")
    try:
        q = json.loads(qualified.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return emit("fail", "qualified-sources.json unreadable")
    sources = q.get("sources") or []
    if not isinstance(sources, list) or not sources:
        return emit(
            "fail",
            "qualified-sources.sources must list exact SHA-256 admissions; "
            "transitional markers alone do not complete the engine path",
        )

    if sys.platform != "darwin":
        return emit("unavailable", "P5.2b requires macOS Metal")
    env = os.environ.copy()
    env["MTL_DEBUG_LAYER"] = "1"
    env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    env["PYTHONPATH"] = str(workspace / "benchmark") + os.pathsep + str(workspace)
    for name in TEST_MODULES:
        ran = subprocess.run(
            [sys.executable, "-m", "unittest", name],
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        if ran.returncode != 0:
            return emit("fail", f"{name}: {(ran.stderr or ran.stdout)[-1500:]}")
    return emit(
        "pass",
        "p52b lifecycle + engine-path call-chain closure held; "
        "semantic review still required",
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(sys.argv[1]))
    print(json.dumps(result))
    return 0 if result["verdict"] == "pass" else (3 if result["verdict"] == "blocked" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
