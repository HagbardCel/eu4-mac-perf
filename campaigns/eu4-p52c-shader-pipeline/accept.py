#!/usr/bin/env python3
"""P5.2c pipeline + Simple GL–Metal equivalence acceptance."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TEST_MODULES = (
    "tests.test_gfx_shader",
    "tests.test_metal_simple_ref",
    "tests.test_gfx_entry_registry",
)


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def check(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    for rel in (
        "analysis/gfx-shader/v0/equivalence-tolerances.md",
        "analysis/gfx-shader/v0/simple.metal",
        "analysis/gfx-shader/v0/binding-schema.json",
        "tests/metal_simple_ref_host.m",
        "tests/test_metal_simple_ref.py",
    ):
        if not (workspace / rel).is_file():
            return emit("fail", f"missing {rel}")
    header = (workspace / "benchmark" / "eu4_gfx_shader.h").read_text(encoding="utf-8")
    if "eu4_gfx_shader_pipeline_for_key" not in header:
        return emit("fail", "missing production pipeline-for-key API")
    body = (workspace / "benchmark" / "eu4_gfx_shader.mm").read_text(encoding="utf-8")
    if "pipeline_cache_key" not in body and "EU4GfxPipelineKey" not in body:
        return emit("fail", "missing effective-Metal PSO cache key")
    if sys.platform != "darwin":
        return emit("unavailable", "P5.2c requires macOS Metal")
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
        "p52c production PSO/cache + controlled offline Simple gates held; "
        "not P5.3/P5.4 cross-function sequences",
    )


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(sys.argv[1]))
    print(json.dumps(result))
    return 0 if result["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
