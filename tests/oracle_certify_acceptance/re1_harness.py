"""Acceptance-owned RE1 harness build (same recipe as tests/test_function_replay.py)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECON_DIR = ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer" / "reconstruction"
COMPILER_FALLBACKS = ("c++", "clang++", "g++")
SANITIZER_FLAGS = ("-fsanitize=address,undefined", "-fno-sanitize-recover=all")
BUILD_FLAGS = ["-std=c++20", "-O0", "-g", "-Wall", "-Wextra", "-Werror", *SANITIZER_FLAGS]

_HARNESS: Path | None = None
_BUILD_NOTE = "not built"


def _find_compiler() -> str | None:
    for candidate in COMPILER_FALLBACKS:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _compile(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"compile failed:\n{result.stderr}")


def build_re1_harness() -> Path | None:
    global _HARNESS, _BUILD_NOTE
    if _HARNESS is not None:
        return _HARNESS
    compiler = _find_compiler()
    if compiler is None:
        _BUILD_NOTE = "no C++ compiler on PATH"
        return None
    workdir = Path(tempfile.mkdtemp(prefix="e2-re1-"))
    binary = workdir / "harness-bin"
    _compile([
        compiler,
        *BUILD_FLAGS,
        str(RECON_DIR / "gfx_set_constant_buffer.cpp"),
        str(RECON_DIR / "harness.cpp"),
        str(RECON_DIR / "arith_literals.cpp"),
        "-o",
        str(binary),
    ])
    _HARNESS = binary
    _BUILD_NOTE = "sanitized host build of the tracked RE1 reconstruction"
    return _HARNESS


def build_note() -> str:
    return _BUILD_NOTE


def require_re1_harness_for_preflight() -> Path:
    harness = build_re1_harness()
    if harness is None:
        raise unittest.SkipTest(
            f"RE1 harness unavailable for 49/1 preflight: {_BUILD_NOTE}")
    return harness


def require_re1_harness_for_integration() -> Path:
    harness = build_re1_harness()
    if harness is None:
        raise AssertionError(
            f"RE1 harness required when oracle.certify is present: {_BUILD_NOTE}")
    return harness


def oracle_certify_present() -> bool:
    return importlib.util.find_spec("oracle.certify") is not None
