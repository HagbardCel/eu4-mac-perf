#!/usr/bin/env python3
"""P5.2b lifecycle acceptance — requires Link/Set/Destroy entries and green tests."""

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


def trusted_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


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
    ):
        if needle not in header:
            return emit("fail", f"header missing {needle}")
    body = (workspace / "benchmark" / "eu4_gfx_shader.mm").read_text(encoding="utf-8")
    if "simple_vs" not in body or "MTLRenderPipelineState" not in body:
        return emit("fail", "shader.mm missing Simple Metal stages / pipeline path")
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
    return emit("pass", "p52b lifecycle gates held; semantic review still required")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(sys.argv[1]))
    print(json.dumps(result))
    return 0 if result["verdict"] == "pass" else (3 if result["verdict"] == "blocked" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
