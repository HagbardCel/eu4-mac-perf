"""Build-and-run harness for the P4b synthetic graphics subject.

The build discipline is the recorder harness's: the harness - never the subject -
assembles each build, copying the trusted sources into a private directory and
compiling them there, so the observation protocol and the ABI cannot be altered by
what is being observed. Only the *spec* differs, so this module reuses that one
implementation of "assemble and run trusted native code" instead of a lookalike.

This is a scaffold for golden-output gates. It never launches EU IV.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from oracle.recorder.harness import HarnessError, build_spec, run_transcript

GFX_DIR = Path(__file__).resolve().parent / "synthetic"

#: The P4b subject: a portable C11 library linked against its frame driver, plus its
#: deterministic golden capture transcript.
SPEC: dict[str, Any] = {
    "exe": "frame",
    "sources": (GFX_DIR / "driver.c", GFX_DIR / "gfx.c"),
    "headers": (GFX_DIR / "gfx.h",),
    "golden": GFX_DIR / "golden.txt",
}


def build(build_dir: Path, *, cc: str = "cc", timeout_s: float = 120.0) -> Path:
    """Compile the graphics subject into ``build_dir`` and return its executable."""
    return build_spec(SPEC, build_dir, cc=cc, timeout_s=timeout_s)


def reproduce(*, cc: str = "cc") -> str:
    """Build in a scratch directory and return the subject's capture transcript."""
    with tempfile.TemporaryDirectory(prefix="oracle-p4b-frame-") as tmp:
        return run_transcript(build(Path(tmp), cc=cc))


def golden_transcript() -> str:
    """The committed golden capture transcript for the graphics subject."""
    return Path(SPEC["golden"]).read_text(encoding="utf-8")


__all__ = ["GFX_DIR", "HarnessError", "SPEC", "build", "golden_transcript", "reproduce"]
