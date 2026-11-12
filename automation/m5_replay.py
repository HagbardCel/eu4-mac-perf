#!/usr/bin/env python3
"""Offline GPU replay of the M5 sprite draw.

The live engine now reaches and GPU-completes the selected sprite draw. This
module remains the exact isolated pixel qualification: it drives the same
production encoder with the exact original-GL inputs captured for that draw,
seeding the authentic pre-draw so the animated loading-screen background does
not affect the sprite comparison. It is a replay boundary, not a second
renderer.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
HOST_SOURCE = ROOT / "tests" / "gfx_m5_replay_host.mm"
HOST = BENCH / ".build" / "gfx_m5_replay_host"
HEADERS = sorted(BENCH.glob("*.h"))
SOURCES = [
    BENCH / "eu4_gfx_shader.mm",
    BENCH / "eu4_gfx_buffers.mm",
    BENCH / "eu4_gfx_texture.mm",
    BENCH / "eu4_gfx_init.mm",
    BENCH / "eu4_gfx_simple_draw.mm",
    BENCH / "eu4_gfx_sprite_draw.mm",
    BENCH / "eu4_gfx_draw_encode.mm",
    BENCH / "eu4_gfx_logical_capture.mm",
    BENCH / "eu4_metal_events.c",
]


class ReplayError(RuntimeError):
    pass


def _cache_current(host: Path, dependencies: list[Path]) -> bool:
    if not host.is_file():
        return False
    present = [path for path in dependencies if path.is_file()]
    if not present:
        return False
    newest = max(path.stat().st_mtime for path in present)
    return host.stat().st_mtime >= newest


def build_host(force: bool = False) -> Path:
    dependencies = [HOST_SOURCE, *SOURCES, *HEADERS, Path(__file__)]
    if not force and _cache_current(HOST, dependencies):
        return HOST
    HOST.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        "-O2",
        "-Wall",
        "-Wextra",
        "-fobjc-arc",
        "-lc++",
        "-I",
        str(BENCH),
        "-framework",
        "Foundation",
        "-framework",
        "Metal",
        "-framework",
        "QuartzCore",
        "-o",
        str(HOST),
        str(HOST_SOURCE),
        *[str(source) for source in SOURCES],
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False,
                           timeout=180)
    if built.returncode:
        raise ReplayError(built.stderr.strip() or "M5 replay host failed to build")
    return HOST


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_replay(reference_dir: Path, output: Path, *, force_build: bool = False) -> dict:
    reference_dir = Path(reference_dir).resolve()
    descriptor_path = reference_dir / "gl-reference.json"
    if not descriptor_path.is_file():
        raise ReplayError(f"reference descriptor is absent: {descriptor_path}")
    reference = json.loads(descriptor_path.read_text())
    provenance = reference.get("provenance") or {}
    executable = provenance.get("executable")
    executable_sha = provenance.get("executable_sha256")
    if not executable or not executable_sha:
        raise ReplayError("reference does not declare executable provenance")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    host = build_host(force=force_build)
    env = os.environ.copy()
    env["EU4_GFX_EXPECTED_EXE"] = str(executable)
    env["EU4_GFX_EXECUTABLE_SHA256"] = str(executable_sha)
    ran = subprocess.run(
        [str(host), "--reference", str(reference_dir), "--out", str(output)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=180,
    )
    candidate_path = output / "metal-logical.json"
    if ran.returncode != 0 or not candidate_path.is_file():
        raise ReplayError(
            (ran.stderr or ran.stdout or "M5 replay host failed").strip()
        )
    candidate = json.loads(candidate_path.read_text())
    capture = candidate.get("capture") or {}
    raw = output / str(capture.get("raw_file") or "")
    if not raw.is_file():
        raise ReplayError("M5 replay did not write its raw payload")
    if raw.stat().st_size != capture.get("raw_bytes") or _sha(raw) != capture.get("raw_sha256"):
        raise ReplayError("M5 replay raw payload does not match its declaration")
    if (candidate.get("gpu") or {}).get("completed") is not True:
        raise ReplayError("M5 replay GPU command did not complete")
    return {
        "candidate": candidate,
        "candidate_path": str(candidate_path),
        "raw_path": str(raw),
        "host": str(host),
        "stdout": ran.stdout.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--force-build", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_replay(
            Path(args.reference), Path(args.output), force_build=args.force_build
        )
    except (ReplayError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}))
        return 1
    print(json.dumps({
        "status": "ok",
        "candidate": result["candidate_path"],
        "raw": result["raw_path"],
        "host": result["host"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
