#!/usr/bin/env python3
"""Complete-frame capture, translation, replay and differential validation.

The M6 workflow is draw-centric and reusable:

    capture    one authentic original-OpenGL frame (frame boundary, every
               draw's effective GL state/resources, seed and reference pixels)
    translate  GLSL 1.20 compiler inputs -> Metal Shading Language sources
    replay     execute the captured frame through the shared Metal encoder
    compare    full-frame and prefix differential with attribution
    report     coverage and missing-dependency diagnostics

Raw payloads live under ``.local/m6`` (gitignored). Only digests and
provenance belong in tracked evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

TRACE_SCHEMA = "gfx-frame-trace/v1"
TARGETS = [
    "_GfxDraw",
    "_GfxDrawIndexed",
    "_GfxDrawIndexedInstanced",
    "_GfxClear",
    "_GfxPresent",
    # The main menu skips _GfxPresent while its present flag is clear but the
    # frontend calls CGraphics::PresentScene every rendered frame.
    "PresentScene",
]


class FrameError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FrameError(f"not a JSON object: {path}")
    return value


# ---------------------------------------------------------------------------
# Trace loading and validation
# ---------------------------------------------------------------------------


def load_trace(frame_dir: Path, *, verify_blobs: bool = True) -> dict:
    """Loads and validates one frame trace. Fails closed on any gap."""
    frame_dir = Path(frame_dir)
    manifest = _read_json(frame_dir / "frame-trace.json")
    if manifest.get("schema") != TRACE_SCHEMA:
        raise FrameError("unexpected frame trace schema")
    if manifest.get("complete") is not True:
        raise FrameError(
            f"frame trace is incomplete: {manifest.get('reason') or 'unknown'}"
        )
    events_path = frame_dir / "frame-events.jsonl"
    events: list[dict] = []
    try:
        for line in events_path.read_text().splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise FrameError("frame event is not an object")
            events.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrameError(f"unreadable frame events: {exc}") from exc
    draws = [event for event in events if event.get("kind") == "draw"]
    if not draws:
        raise FrameError("frame trace contains no draws")
    if manifest.get("events") != len(events):
        raise FrameError(
            f"manifest declares {manifest.get('events')} events, file has {len(events)}"
        )
    frame = manifest.get("frame") or {}
    reference = frame_dir / str(frame.get("reference") or "")
    if not frame.get("reference") or not reference.is_file():
        raise FrameError("frame reference pixels are absent")
    if reference.stat().st_size != frame.get("reference_bytes"):
        raise FrameError("frame reference size does not match its declaration")
    if _sha256(reference) != frame.get("reference_sha256"):
        raise FrameError("frame reference sha256 does not match its declaration")
    if verify_blobs:
        blobs: dict[str, str] = {}
        for event in events:
            members = list(_iter_blob_members(event))
            # Vertex attribute payloads use "data" rather than "file".
            for attribute in event.get("attributes") or []:
                if isinstance(attribute, dict) and attribute.get("data"):
                    members.append(
                        {
                            "file": attribute["data"],
                            "sha256": attribute.get("sha256"),
                        }
                    )
            for member in members:
                name = member.get("file")
                if not name:
                    continue
                if name in blobs:
                    continue
                path = frame_dir / "blobs" / str(name)
                if not path.is_file():
                    raise FrameError(f"missing blob: {name}")
                digest = _sha256(path)
                if member.get("sha256") and member["sha256"] != digest:
                    raise FrameError(f"blob digest mismatch: {name}")
                blobs[name] = digest
        manifest["_blob_count_verified"] = len(blobs)
    manifest["_events"] = events
    manifest["_draws"] = draws
    manifest["_frame_dir"] = str(frame_dir)
    return manifest


def _iter_blob_members(value):
    """Yields every dict that declares a blob file, recursively."""
    if isinstance(value, dict):
        if isinstance(value.get("file"), str) and value.get("file"):
            yield value
        for item in value.values():
            yield from _iter_blob_members(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_blob_members(item)


def coverage_report(trace: dict) -> dict:
    """Actionable coverage and missing-dependency diagnostics."""
    events = trace.get("_events") or []
    draws = trace.get("_draws") or []
    problems: list[dict] = []
    shaders: set[tuple[str, str]] = set()
    textures: dict[str, dict] = {}
    stages_seen: set[str] = set()
    modes: dict[str, int] = {}
    entries: dict[str, int] = {}
    clear_count = 0
    indexed = 0
    attributes = 0
    uniforms = 0
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind == "clear":
            clear_count += 1
            continue
        if kind != "draw":
            problems.append({"event": index, "issue": "unknown_event_kind", "kind": kind})
            continue
        draw = event.get("draw") or {}
        entry = str(draw.get("entry") or "unknown")
        entries[entry] = entries.get(entry, 0) + 1
        mode = draw.get("mode")
        modes[str(mode)] = modes.get(str(mode), 0) + 1
        if draw.get("indexed"):
            indexed += 1
        program = event.get("program") or {}
        draw_shaders = program.get("shaders") or []
        names = {str(item.get("stage")) for item in draw_shaders if item.get("file")}
        stages_seen |= names
        if "vertex" not in names or "pixel" not in names:
            problems.append(
                {
                    "event": index,
                    "issue": "missing_shader_stage",
                    "stages": sorted(names),
                    "program": program.get("name"),
                }
            )
        for item in draw_shaders:
            if item.get("file") and item.get("sha256"):
                shaders.add((str(item.get("stage")), str(item["sha256"])))
            if item.get("compile") is False:
                problems.append(
                    {
                        "event": index,
                        "issue": "shader_not_compiled",
                        "sha256": item.get("sha256"),
                    }
                )
        diagnostics = event.get("program_diagnostics") or {}
        if int(draw.get("count") or 0) > 0 and not (event.get("attributes") or []):
            problems.append(
                {
                    "event": index,
                    "issue": "draw_without_vertex_attributes",
                    "count": draw.get("count"),
                    "active_attributes": diagnostics.get("active_attributes"),
                    "enabled_bits": diagnostics.get("enabled_bits"),
                    "link": diagnostics.get("link"),
                }
            )
        for attribute in event.get("attributes") or []:
            attributes += 1
            if not attribute.get("data") and attribute.get("range_bytes"):
                problems.append(
                    {
                        "event": index,
                        "issue": "missing_vertex_bytes",
                        "location": attribute.get("location"),
                    }
                )
        for uniform in program.get("uniforms") or []:
            uniforms += 1
            if uniform.get("type") == "sampler":
                continue
            if not uniform.get("file"):
                problems.append(
                    {
                        "event": index,
                        "issue": "missing_uniform_bytes",
                        "name": uniform.get("name"),
                    }
                )
        for texture in event.get("textures") or []:
            key = str(texture.get("name"))
            textures[key] = {
                "name": texture.get("name"),
                "width": texture.get("width"),
                "height": texture.get("height"),
                "internal_format": texture.get("internal_format"),
                "levels": len(texture.get("levels") or []),
            }
            if texture.get("capture_error"):
                problems.append(
                    {
                        "event": index,
                        "issue": "texture_capture_error",
                        "name": texture.get("name"),
                        "internal_format": texture.get("internal_format"),
                    }
                )
            if not texture.get("levels"):
                problems.append(
                    {
                        "event": index,
                        "issue": "texture_without_levels",
                        "name": texture.get("name"),
                    }
                )
        target = event.get("target") or {}
        if not target.get("width") or not target.get("height"):
            problems.append({"event": index, "issue": "missing_target_extent"})
        if event.get("seed_error"):
            problems.append({"event": index, "issue": "seed_capture_failed"})
    seed = None
    for draw in draws:
        if draw.get("seed"):
            seed = draw.get("seed")
            break
    if not seed:
        problems.append({"event": 0, "issue": "missing_frame_seed"})
    return {
        "schema": "gfx-frame-coverage/v1",
        "event_count": len(events),
        "draw_count": len(draws),
        "clear_count": clear_count,
        "indexed_draw_count": indexed,
        "entries": entries,
        "primitive_modes": modes,
        "stages": sorted(stages_seen),
        "shader_pairs": len(shaders),
        "shaders": [
            {"stage": stage, "sha256": digest}
            for stage, digest in sorted(shaders)
        ],
        "texture_count": len(textures),
        "textures": sorted(textures.values(), key=lambda item: str(item.get("name"))),
        "attribute_count": attributes,
        "uniform_count": uniforms,
        "seed": {
            "width": (seed or {}).get("width"),
            "height": (seed or {}).get("height"),
            "sha256": (seed or {}).get("sha256"),
        },
        "frame": {
            "width": (trace.get("frame") or {}).get("width"),
            "height": (trace.get("frame") or {}).get("height"),
            "reference_sha256": (trace.get("frame") or {}).get("reference_sha256"),
        },
        "missing": problems,
        "ok": not problems,
    }


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


def capture(args: argparse.Namespace) -> int:
    import eu4_gfx_startup_trace as startup
    from automation import live_gfx

    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"capture output already exists: {output}", file=sys.stderr)
        return 2
    frame_dir = output / "frame"
    max_events = 1024
    evidence = startup.preflight_ops(
        TARGETS,
        max_events=max_events,
        duration_s=args.duration,
    )
    token = None
    if args.campaign_run_root:
        try:
            token = live_gfx._claim_launch(
                Path(args.campaign_run_root),
                output,
                TARGETS,
                requested_timeout=args.timeout,
                executable_sha256=evidence.get("executable_sha256"),
            )
        except live_gfx.LaunchLimit as exc:
            print(f"campaign launch refused: {exc}", file=sys.stderr)
            return 3
    if args.boundary_fb < -1 or args.after_ms < 0 or args.max_draws < 0:
        print("capture refused: invalid negative selection value", file=sys.stderr)
        return 2
    if args.boundary not in ("", "scene"):
        print(f"capture refused: unknown boundary {args.boundary!r}", file=sys.stderr)
        return 2
    env_extra = {
        "EU4_GFX_FRAME_TRACE_DIR": str(frame_dir),
        "EU4_GFX_FRAME_TRACE_CALLERS": args.callers or "",
        "EU4_GFX_FRAME_TRACE_MIN_DRAWS": str(args.min_draws),
        "EU4_GFX_FRAME_TRACE_MAX_FRAME_DRAWS": str(args.max_frame_draws or 0),
        "EU4_GFX_FRAME_TRACE_BOUNDARY_FB": str(args.boundary_fb),
        "EU4_GFX_FRAME_TRACE_BOUNDARY": args.boundary,
        "EU4_GFX_FRAME_TRACE_PRE_POST_CALLERS": args.pre_post_callers or "",
        "EU4_GFX_FRAME_TRACE_OCCURRENCE": str(args.occurrence),
        "EU4_GFX_FRAME_TRACE_MAX_DRAWS": str(args.max_draws),
    }
    if args.at_frame is not None:
        env_extra["EU4_GFX_FRAME_TRACE_AT_FRAME"] = str(args.at_frame)
    if args.after_ms:
        env_extra["EU4_GFX_FRAME_TRACE_AFTER_MS"] = str(args.after_ms)
    if args.log:
        env_extra["EU4_GFX_FRAME_TRACE_LOG"] = str(
            (output / "frame-boundary.log").resolve()
        )
    try:
        report = startup.run_ops(
            targets=TARGETS,
            max_events=max_events,
            duration_s=args.duration,
            timeout_s=args.timeout,
            output=output,
            authorize_live=True,
            evidence=evidence,
            env_extra=env_extra,
            retention="state",
        )
    except (startup.StartupError, OSError, RuntimeError) as exc:
        if token is not None:
            live_gfx._finish_launch(
                Path(args.campaign_run_root), token, pid=None,
                status=f"failed: {exc}",
            )
        print(f"capture failed: {exc}", file=sys.stderr)
        return 4
    result: dict = {
        "schema": "m6-frame-capture/v1",
        "session": report.get("session"),
        "pid": report.get("pid"),
        "frame_dir": str(frame_dir),
        "targets": TARGETS,
        "selection": {
            "callers": args.callers,
            "min_draws": args.min_draws,
            "max_frame_draws": args.max_frame_draws,
            "occurrence": args.occurrence,
            "at_frame": args.at_frame,
            "after_ms": args.after_ms,
            "boundary_fb": args.boundary_fb,
            "boundary": args.boundary,
            "pre_post_callers": args.pre_post_callers,
        },
    }
    status = 0
    try:
        trace = load_trace(frame_dir)
        coverage = coverage_report(trace)
        result["trace"] = {
            "schema": trace.get("schema"),
            "present_index": trace.get("present_index"),
            "events": trace.get("events"),
            "frame": trace.get("frame"),
            "gl": trace.get("gl"),
            "selection": trace.get("selection"),
            "blobs_verified": trace.get("_blob_count_verified"),
        }
        result["coverage"] = coverage
        if not coverage["ok"]:
            status = 5
    except FrameError as exc:
        result["error"] = str(exc)
        status = 5
    (output / "capture-report.json").write_text(json.dumps(result, indent=2) + "\n")
    if token is not None:
        live_gfx._finish_launch(
            Path(args.campaign_run_root),
            token,
            pid=report.get("pid"),
            status="completed" if status == 0 else "failed: frame-trace",
        )
    print(json.dumps(result, indent=2))
    return status


# ---------------------------------------------------------------------------
# Shader translation, replay and comparison
# ---------------------------------------------------------------------------

REPLAY_HOST_SOURCE = BENCH / "eu4_gfx_frame_replay.mm"
REPLAY_HOST = BENCH / ".build" / "gfx_frame_replay"


def build_replay_host(force: bool = False) -> Path:
    dependencies = [REPLAY_HOST_SOURCE, Path(__file__)]
    if not force and REPLAY_HOST.is_file():
        newest = max(path.stat().st_mtime for path in dependencies if path.is_file())
        if REPLAY_HOST.stat().st_mtime >= newest:
            return REPLAY_HOST
    REPLAY_HOST.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(
        [
            "clang",
            "-arch",
            "arm64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-lc++",
            "-framework",
            "Foundation",
            "-framework",
            "Metal",
            "-o",
            str(REPLAY_HOST),
            str(REPLAY_HOST_SOURCE),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    if built.returncode:
        raise FrameError(built.stderr.strip() or "frame replay host build failed")
    return REPLAY_HOST


def translate_command(args: argparse.Namespace) -> int:
    from automation import gfx_shader_translate

    frame_dir = Path(args.frame).expanduser().resolve()
    out_dir = (
        Path(args.out).expanduser().resolve()
        if args.out
        else frame_dir / "shaders"
    )
    if out_dir.exists() and args.force:
        shutil.rmtree(out_dir)
    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    report = gfx_shader_translate.translate_trace(frame_dir, out_dir)
    summary = {
        "status": "ok" if report["ok"] else "partial",
        "programs": len(report["programs"]),
        "failed": [
            {"program": entry["program"], "error": entry.get("error")}
            for entry in report["programs"]
            if entry["status"] != "ok"
        ],
        "out": str(out_dir),
        "draws": len(trace.get("_draws") or []),
    }
    print(json.dumps(summary, indent=2))
    return 0 if report["ok"] else 2


def replay_command(args: argparse.Namespace) -> int:
    frame_dir = Path(args.frame).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if out == frame_dir or frame_dir in out.parents or out in frame_dir.parents:
        raise FrameError(
            "replay output must not overlap the captured source frame directory"
        )
    shaders = (
        Path(args.shaders).expanduser().resolve()
        if args.shaders
        else frame_dir / "shaders"
    )
    if out.exists() and not args.force:
        print(f"replay output already exists: {out}", file=sys.stderr)
        return 2
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    try:
        host = build_replay_host(force=args.force_build)
    except FrameError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    command = [
        str(host),
        "--frame",
        str(frame_dir),
        "--shaders",
        str(shaders),
        "--out",
        str(out),
    ]
    if args.stop_after is not None:
        command += ["--stop-after", str(args.stop_after)]
    if args.seed_marked:
        command.append("--seed-marked")
    if args.capture_post:
        command.append("--capture-post")
    ran = subprocess.run(command, capture_output=True, text=True, check=False, timeout=1200)
    result_path = out / "replay.json"
    if ran.returncode != 0 or not result_path.is_file():
        print(
            json.dumps(
                {
                    "status": "error",
                    "returncode": ran.returncode,
                    "stderr": ran.stderr[-4000:],
                },
                indent=2,
            )
        )
        return 1
    result = json.loads(result_path.read_text())
    summary = {
        "status": result.get("status"),
        "events": result.get("events"),
        "encoded": result.get("encoded"),
        "failed": result.get("failed"),
        "command_status": result.get("command_status"),
        "readback": result.get("readback"),
        "frame": result.get("frame"),
        "failures": (result.get("failures") or [])[:8],
    }
    print(json.dumps(summary, indent=2))
    return 0 if result.get("status") == "ok" else 1


def compare_command(args: argparse.Namespace) -> int:
    from automation import m6_compare

    reference = Path(args.reference).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    policy_path = (
        Path(args.policy).expanduser().resolve()
        if args.policy
        else ROOT / "analysis" / "m6" / "comparison-policy.json"
    )
    out = (
        Path(args.out).expanduser().resolve()
        if args.out
        else candidate / "differential"
    )
    out.mkdir(parents=True, exist_ok=True)
    try:
        policy = json.loads(policy_path.read_text())
        report = m6_compare.compare(reference, candidate, policy)
        if args.difference:
            report["difference"] = m6_compare.write_difference(
                reference,
                candidate,
                report["reference"]["width"],
                report["reference"]["height"],
                out,
            )
    except (m6_compare.CompareError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "mismatched_pixels": report["statistics"]["mismatched_pixels"],
                "pixels": report["statistics"]["pixels"],
                "max_channel_error": report["statistics"]["max_channel_error"],
                "mean_max_channel_error": report["statistics"][
                    "mean_max_channel_error"
                ],
                "mean_abs_error": report["statistics"]["mean_abs_error"],
                "worst_region": report["worst_region"],
            },
            indent=2,
        )
    )
    return 0 if report["status"] == "pass" else 2


def isolate_command(args: argparse.Namespace) -> int:
    """Compares the isolated replay of marked draws with their GL post-images."""
    from automation import m6_compare

    frame_dir = Path(args.frame).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    results = []
    for index, event in enumerate(trace.get("_events") or []):
        if event.get("kind") != "draw":
            continue
        post = event.get("post_image")
        pre = event.get("pre_image")
        if not isinstance(post, dict) or not isinstance(pre, dict):
            continue
        replay_path = candidate / f"isolated-post-{index}.bgra"
        gl_path = frame_dir / "blobs" / str(post.get("file") or "")
        if not replay_path.is_file() or not gl_path.is_file():
            results.append(
                {
                    "event": index,
                    "caller": (event.get("draw") or {}).get("caller_rva"),
                    "status": "missing",
                }
            )
            continue
        width = int(post.get("width") or 0)
        height = int(post.get("height") or 0)
        reference = gl_path.read_bytes()
        candidate_bytes = replay_path.read_bytes()
        if width <= 0 or height <= 0 or len(reference) != width * height * 4:
            results.append({"event": index, "status": "invalid_reference"})
            continue
        if len(candidate_bytes) != len(reference):
            results.append({"event": index, "status": "size_mismatch"})
            continue
        statistics = m6_compare.compare_images(
            reference, candidate_bytes, width, height
        )
        results.append(
            {
                "event": index,
                "caller": (event.get("draw") or {}).get("caller_rva"),
                "program": (event.get("program") or {}).get("name"),
                "textures": [
                    item.get("name") for item in event.get("textures") or []
                ],
                "status": "compared",
                "statistics": statistics,
            }
        )
    report = {
        "schema": "gfx-isolated-draw-differential/v1",
        "frame": str(frame_dir),
        "candidate": str(candidate),
        "draws": results,
        "ok": bool(results)
        and all(
            item.get("status") == "compared"
            and item["statistics"]["mismatched_pixels"] == 0
            for item in results
        ),
    }
    (candidate / "isolated-differential.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(json.dumps({
        "ok": report["ok"],
        "draws": [
            {
                "event": item["event"],
                "caller": item.get("caller"),
                "program": item.get("program"),
                "status": item.get("status"),
                "mismatched": (item.get("statistics") or {}).get("mismatched_pixels"),
                "pixels": (item.get("statistics") or {}).get("pixels"),
                "max": (item.get("statistics") or {}).get("max_channel_error"),
            }
            for item in results
        ],
    }, indent=2))
    return 0 if report["ok"] else 2


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def inspect(args: argparse.Namespace) -> int:
    frame_dir = Path(args.frame).expanduser().resolve()
    try:
        trace = load_trace(frame_dir)
        coverage = coverage_report(trace)
    except FrameError as exc:
        print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
        return 1
    summary = {
        "schema": "m6-frame-inspection/v1",
        "frame_dir": str(frame_dir),
        "manifest": {
            "present_index": trace.get("present_index"),
            "events": trace.get("events"),
            "frame": trace.get("frame"),
            "gl": trace.get("gl"),
        },
        "coverage": coverage,
        "draws": [
            {
                "index": (event.get("draw") or {}).get("entry"),
                "caller": (event.get("draw") or {}).get("caller_rva"),
                "mode": (event.get("draw") or {}).get("mode"),
                "count": (event.get("draw") or {}).get("count"),
                "program": (event.get("program") or {}).get("name"),
                "target": (event.get("target") or {}).get("framebuffer"),
            }
            for event in trace.get("_draws") or []
        ],
    }
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="capture one complete original-GL frame")
    cap.add_argument("--output", required=True)
    cap.add_argument("--campaign-run-root")
    cap.add_argument("--callers", default="0x10d52fc,0x1486ad9")
    cap.add_argument("--min-draws", type=int, default=8)
    cap.add_argument("--max-frame-draws", type=int, default=0)
    cap.add_argument("--occurrence", type=int, default=1)
    cap.add_argument("--at-frame", type=int, default=None)
    cap.add_argument("--after-ms", type=int, default=0)
    cap.add_argument("--boundary-fb", type=int, default=-1)
    cap.add_argument("--boundary", default="")
    cap.add_argument("--pre-post-callers", default="")
    cap.add_argument("--log", action="store_true")
    cap.add_argument("--max-draws", type=int, default=256)
    cap.add_argument("--duration", type=float, default=90.0)
    cap.add_argument("--timeout", type=float, default=180.0)
    cap.set_defaults(handler=capture)

    ins = sub.add_parser("inspect", help="validate and summarize one frame trace")
    ins.add_argument("--frame", required=True)
    ins.set_defaults(handler=inspect)

    tr = sub.add_parser("translate", help="translate a frame's shaders to MSL")
    tr.add_argument("--frame", required=True)
    tr.add_argument("--out")
    tr.add_argument("--force", action="store_true")
    tr.set_defaults(handler=translate_command)

    rep = sub.add_parser("replay", help="execute a traced frame through Metal")
    rep.add_argument("--frame", required=True)
    rep.add_argument("--shaders")
    rep.add_argument("--out", required=True)
    rep.add_argument("--stop-after", type=int, default=None)
    rep.add_argument("--force", action="store_true")
    rep.add_argument("--force-build", action="store_true")
    rep.add_argument("--seed-marked", action="store_true")
    rep.add_argument("--capture-post", action="store_true")
    rep.set_defaults(handler=replay_command)

    iso = sub.add_parser("isolate", help="isolated marked-draw differential")
    iso.add_argument("--frame", required=True)
    iso.add_argument("--candidate", required=True)
    iso.set_defaults(handler=isolate_command)

    cmp = sub.add_parser("compare", help="full-frame differential")
    cmp.add_argument("--reference", required=True)
    cmp.add_argument("--candidate", required=True)
    cmp.add_argument("--policy")
    cmp.add_argument("--out")
    cmp.add_argument("--difference", action="store_true")
    cmp.set_defaults(handler=compare_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
