#!/usr/bin/env python3
"""Deterministic Metal draw-stream benchmark (PR P0).

Builds the native live-draw host with the real backend modules and drives a
synthetic draw stream through the same ``_Gfx*`` replacement route the game
uses. It measures backend overhead only; it is not a gameplay benchmark and
makes no EU4 performance claim.

Output is one JSON object with per-frame samples and aggregate medians/p95.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_live_draw_host.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_live_draw_host"
SOURCES = [
    ROOT / "benchmark" / "eu4_gfx_shader.mm",
    ROOT / "benchmark" / "eu4_gfx_buffers.mm",
    ROOT / "benchmark" / "eu4_gfx_perf.mm",
    ROOT / "benchmark" / "eu4_gfx_texture.mm",
    ROOT / "benchmark" / "eu4_gfx_init.mm",
    ROOT / "benchmark" / "eu4_gfx_frame_observe.c",
    ROOT / "benchmark" / "eu4_gfx_simple_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_sprite_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_draw_encode.mm",
    ROOT / "benchmark" / "eu4_gfx_draw_capture.c",
    ROOT / "benchmark" / "eu4_gfx_draw_route.mm",
    ROOT / "benchmark" / "eu4_gfx_generic_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_logical_capture.mm",
    ROOT / "benchmark" / "eu4_gfx_checkpoint.mm",
    ROOT / "benchmark" / "eu4_metal_events.c",
]


def build_host(force: bool = False) -> Path:
    HOST.parent.mkdir(parents=True, exist_ok=True)
    newest = max([HOST_SOURCE, *SOURCES, Path(__file__)], key=lambda p: p.stat().st_mtime)
    if HOST.exists() and not force and HOST.stat().st_mtime >= newest.stat().st_mtime:
        return HOST
    command = [
        "clang", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
        "-I", str(ROOT / "benchmark"),
        "-framework", "Foundation", "-framework", "Metal",
        "-framework", "QuartzCore",
        "-o", str(HOST), str(HOST_SOURCE), *[str(source) for source in SOURCES],
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False)
    if built.returncode:
        raise RuntimeError(built.stderr.strip() or "draw-stream benchmark build failed")
    return HOST


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = max(1, int(round(fraction * len(ordered))))
    return ordered[min(rank, len(ordered)) - 1]


def run_kind(kind: str, draws: int, frames: int, *, force_build: bool = False,
             extra_env: dict | None = None, debug_layer: bool = True) -> dict:
    host = build_host(force_build)
    env = os.environ.copy()
    if debug_layer:
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    else:
        env.pop("MTL_DEBUG_LAYER", None)
        env.pop("MTL_DEBUG_LAYER_ERROR_MODE", None)
    if extra_env:
        env.update(extra_env)
    ran = subprocess.run(
        [str(host), "--bench", kind, str(draws), str(frames)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if ran.returncode:
        raise RuntimeError(ran.stderr.strip() or ran.stdout.strip() or "benchmark failed")
    records = []
    for line in ran.stdout.splitlines():
        if not line.startswith("BENCH "):
            continue
        record = json.loads(line[len("BENCH "):])
        if record.get("kind") != kind or record.get("draws") != draws:
            continue
        records.append(record)
    if len(records) != frames:
        raise RuntimeError(
            f"expected {frames} measured frames for {kind}, got {len(records)}"
        )
    cpu = [record["perf"]["cpu_encode_ns"] for record in records]
    wall = [record["wall_ns"] for record in records]
    gpu = [record["perf"]["gpu_execution_ns"] for record in records]
    counters = {}
    for record in records:
        for key, value in record["perf"].items():
            if key in ("schema", "flush_reasons"):
                continue
            counters.setdefault(key, []).append(value)
    summary = {
        "kind": kind,
        "draws": draws,
        "frames": frames,
        "samples": len(records),
        "cpu_encode_ns": {
            "median": statistics.median(cpu),
            "p95": percentile(cpu, 0.95),
            "per_draw_median": statistics.median(cpu) / draws,
        },
        "wall_ns": {
            "median": statistics.median(wall),
            "p95": percentile(wall, 0.95),
        },
        "gpu_execution_ns": {
            "median": statistics.median(gpu),
            "p95": percentile(gpu, 0.95),
        },
        "counters": {
            key: {"median": statistics.median(values), "p95": percentile(values, 0.95)}
            for key, values in counters.items()
        },
        "flush_reasons": {
            reason: [
                record["perf"]["flush_reasons"].get(reason, 0) for record in records
            ]
            for reason in records[0]["perf"].get("flush_reasons", {})
        },
    }
    return {"summary": summary, "records": records}


def measure(kinds: list[str], draws: int, frames: int, *,
            force_build: bool = False, include_records: bool = False,
            debug_layer: bool = True) -> dict:
    report = {
        "schema": "gfx-bench/v1",
        "note": (
            "Synthetic draw-stream backend overhead. Not a gameplay benchmark; "
            "no EU4 performance claim is made from it."
        ),
        "workload": {"draws_per_frame": draws, "measured_frames": frames, "warmup_frames": 3},
        "kinds": {},
    }
    for kind in kinds:
        measured = run_kind(kind, draws, frames, force_build=force_build,
                            debug_layer=debug_layer)
        if not include_records:
            # Complete per-frame samples are reproducible from the command; the
            # committed evidence keeps the compact summary only.
            measured = {"summary": measured["summary"]}
        report["kinds"][kind] = measured
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draws", type=int, default=1000)
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--kinds", default="simple,indexed,mixed")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--force-build", action="store_true")
    parser.add_argument("--records", action="store_true",
                        help="include every per-frame sample (large)")
    parser.add_argument("--no-debug-layer", action="store_true",
                        help="performance runs: do not enable Metal validation")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    kinds = [kind.strip() for kind in args.kinds.split(",") if kind.strip()]
    report = measure(kinds, args.draws, args.frames,
                     force_build=args.force_build,
                     include_records=args.records,
                     debug_layer=not args.no_debug_layer)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    if args.output is None or args.summary:
        for kind, measured in report["kinds"].items():
            summary = measured["summary"]
            print(
                f"{kind}: draws={summary['draws']} frames={summary['frames']} "
                f"cpu_median={summary['cpu_encode_ns']['median']:.0f}ns "
                f"cpu_p95={summary['cpu_encode_ns']['p95']:.0f}ns "
                f"wall_median={summary['wall_ns']['median']:.0f}ns "
                f"command_buffers={summary['counters']['command_buffers_created']['median']:.0f} "
                f"encoders={summary['counters']['render_encoders_created']['median']:.0f} "
                f"index_temp={summary['counters']['index_temp_allocs']['median']:.0f}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
