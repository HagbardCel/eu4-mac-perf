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
import signal
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


SEAL_SCHEMA = "gfx-frame-seal/v1"
SEAL_ABI_VERSION = 2


def load_seal(
    frame_dir: Path,
    *,
    trace: dict | None = None,
    verify_counts: bool = True,
    verify_blobs: bool = True,
) -> dict:
    """Loads and validates the authoritative same-process frame seal.

    The seal is the handoff contract: a consumer may act only on a document
    whose ``sealed`` flag is true, whose trace manifest, GL reference and
    immutable event stream still match their declared digests, and whose
    ordered operation accounting matches the event stream on disk.  Referenced
    blobs are checked by the canonical ``load_trace`` verifier; pass an already
    verified ``trace`` to avoid hashing them twice.
    """
    frame_dir = Path(frame_dir)
    path = frame_dir / "frame-seal.json"
    seal = _read_json(path)
    if seal.get("schema") != SEAL_SCHEMA:
        raise FrameError("unexpected frame seal schema")
    if seal.get("sealed") is not True:
        raise FrameError(
            "frame is not sealed: "
            + str(seal.get("failure_reason") or "unknown")
        )
    if seal.get("complete") is not True:
        raise FrameError("sealed descriptor is not complete")
    try:
        struct_size = int(seal.get("struct_size"))
        abi_version = int(seal.get("abi_version"))
    except (TypeError, ValueError) as exc:
        raise FrameError("frame seal has no ABI identity") from exc
    if abi_version != SEAL_ABI_VERSION:
        raise FrameError(f"unsupported frame seal ABI {abi_version}")
    if struct_size < 2256 or struct_size > 65536:
        raise FrameError("frame seal descriptor size is invalid")
    if seal.get("trace_complete") is not True:
        raise FrameError("sealed trace manifest is not complete")
    if seal.get("trace_integrity") is not True:
        raise FrameError("sealed trace integrity is not validated")
    if seal.get("readback_ok") is not True:
        raise FrameError("sealed frame has no reference readback")
    if seal.get("event_stream_verified") is not True:
        raise FrameError("sealed event stream is not verified")
    try:
        capture_id = int(seal.get("capture_frame_id"))
        pid = int(seal.get("pid"))
    except (TypeError, ValueError) as exc:
        raise FrameError("frame seal has no numeric identity") from exc
    if capture_id <= 0 or pid <= 0 or not seal.get("session_id"):
        raise FrameError("frame seal identity is invalid")
    declared_dir = Path(str(seal.get("capture_dir") or ""))
    if not declared_dir.is_absolute() or declared_dir.resolve() != frame_dir.resolve():
        raise FrameError("frame seal capture directory does not match")

    def member(name: object, label: str) -> Path:
        text = str(name or "")
        path = Path(text)
        if not text or path.is_absolute() or path.name != text or "/" in text:
            raise FrameError(f"sealed {label} path is invalid")
        return frame_dir / path

    manifest_path = member(seal.get("manifest_file"), "manifest")
    if not manifest_path.is_file():
        raise FrameError("sealed trace manifest is absent")
    if _sha256(manifest_path) != seal.get("manifest_sha256"):
        raise FrameError("sealed trace manifest digest does not match")

    reference_path = member(seal.get("reference_file"), "reference")
    if not reference_path.is_file():
        raise FrameError("sealed reference pixels are absent")
    if reference_path.stat().st_size != seal.get("reference_bytes"):
        raise FrameError("sealed reference size does not match")
    if _sha256(reference_path) != seal.get("reference_sha256"):
        raise FrameError("sealed reference digest does not match")

    events_path = member(seal.get("event_stream_file"), "event stream")
    if not events_path.is_file():
        raise FrameError("sealed event stream is absent")
    if events_path.stat().st_size != seal.get("event_stream_bytes"):
        raise FrameError("sealed event stream size does not match")
    if _sha256(events_path) != seal.get("event_stream_sha256"):
        raise FrameError("sealed event stream digest does not match")

    if trace is None:
        trace = load_trace(frame_dir, verify_blobs=verify_blobs)
    else:
        if Path(str(trace.get("_frame_dir") or "")).resolve() != frame_dir.resolve():
            raise FrameError("supplied trace belongs to another frame directory")
        if verify_blobs and "_blob_count_verified" not in trace:
            raise FrameError("supplied trace was not verified for referenced blobs")
    events = trace.get("_events") or []
    if verify_counts:
        kinds = [event.get("kind") for event in events]
        counts = {
            "operation_count": len(events),
            "draw_count": kinds.count("draw"),
            "clear_count": kinds.count("clear"),
            "copy_count": kinds.count("resolve"),
        }
        for field, actual in counts.items():
            try:
                declared = int(seal.get(field, -1))
            except (TypeError, ValueError) as exc:
                raise FrameError(f"frame seal {field} is not an integer") from exc
            if declared != actual:
                raise FrameError(
                    f"frame seal {field} {seal.get(field)!r} != {actual}"
                )
        manifest = _read_json(manifest_path)
        if manifest.get("events") != len(events):
            raise FrameError("sealed manifest event count does not match")
        if manifest.get("complete") is not True:
            raise FrameError("sealed manifest is incomplete")
    seal["verification"] = {
        "event_stream": True,
        "manifest": True,
        "reference": True,
        "blobs": (
            trace.get("_blob_count_verified") if verify_blobs else None
        ),
        "counts": bool(verify_counts),
    }
    seal["_verified"] = True
    seal["_seal_path"] = str(path)
    seal["_frame_dir"] = str(frame_dir)
    return seal


def load_notification(output: Path) -> dict:
    """Loads the one completion notification emitted by the injected consumer."""
    output = Path(output)
    notification = _read_json(output / "frame-seal-notification.json")
    if notification.get("schema") != "gfx-frame-seal-notification/v1":
        raise FrameError("unexpected seal notification schema")
    if notification.get("notification_index") != 1:
        raise FrameError("more than one completion notification was emitted")
    if notification.get("verified_manifest") is not True:
        raise FrameError("consumer could not verify the sealed manifest")
    if notification.get("verified_reference") is not True:
        raise FrameError("consumer could not verify the sealed reference")
    return notification


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
    resolve_count = 0
    copy_count = 0
    indexed = 0
    attributes = 0
    uniforms = 0
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind == "clear":
            clear_count += 1
            continue
        if kind in ("resolve", "copy"):
            if kind == "resolve":
                resolve_count += 1
            else:
                copy_count += 1
            from automation.gfx_frame_graph import copy_contract_problems

            for detail in copy_contract_problems(event, index):
                problems.append({
                    "event": index,
                    "issue": "invalid_transfer",
                    "kind": kind,
                    "detail": detail,
                })
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
        "resolve_count": resolve_count,
        "copy_count": copy_count,
        "transfer_count": resolve_count + copy_count,
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
    background_plan = None
    if getattr(args, "background", False):
        from automation import gfx_display, gfx_launch

        try:
            user_data = gfx_launch.preflight_user_data(startup.base.GOG_EXE)
            background_plan = gfx_display.plan_background(
                user_data["user_data"]
            )
        except (gfx_display.DisplayError, gfx_launch.LaunchError, OSError) as exc:
            print(f"capture refused: background display unavailable: {exc}",
                  file=sys.stderr)
            return 2
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
    if getattr(args, "timeline", False):
        env_extra["EU4_GFX_FRAME_TRACE_TIMELINE"] = "1"
    if getattr(args, "copy_trace", None) is not None:
        env_extra["EU4_GFX_COPY_TRACE_DEST"] = str(args.copy_trace)
        env_extra["EU4_GFX_COPY_TRACE_PATH"] = str(
            (output / "copy-trace.jsonl").resolve()
        )
        env_extra["EU4_GFX_COPY_TRACE_LIMIT"] = "4096"
    if getattr(args, "copy_source", False):
        env_extra["EU4_GFX_FRAME_TRACE_CAPTURE_COPY_SOURCE"] = "1"
    shadow_enabled = bool(getattr(args, "shadow", False))
    seal_enabled = bool(getattr(args, "seal", False)) or shadow_enabled
    if getattr(args, "seal_log", False) and not seal_enabled:
        print("capture refused: --seal-log requires --seal", file=sys.stderr)
        return 2
    shadow_dir = (
        Path(args.shadow_dir).expanduser().resolve()
        if getattr(args, "shadow_dir", "")
        else output / "shadow"
    )
    if shadow_enabled:
        if args.duration > 600 or args.timeout > 600:
            print("capture refused: invalid shadow launch bounds", file=sys.stderr)
            return 2
        if not 1 <= args.shadow_timeout_ms <= 3_600_000:
            print("capture refused: invalid shadow GPU timeout", file=sys.stderr)
            return 2
        if not 1 <= args.shadow_wait_ms <= 3_600_000:
            print("capture refused: invalid shadow shader wait", file=sys.stderr)
            return 2
        if shadow_dir.exists():
            print(
                f"capture refused: shadow output already exists: {shadow_dir}",
                file=sys.stderr,
            )
            return 2
        env_extra["EU4_GFX_SHADOW_MODE"] = "single-frame"
        env_extra["EU4_GFX_SHADOW_OUTPUT_DIR"] = str(shadow_dir)
        env_extra["EU4_GFX_SHADOW_SHADER_DIR"] = str(shadow_dir / "shaders")
        env_extra["EU4_GFX_SHADOW_TIMEOUT_MS"] = str(args.shadow_timeout_ms)
        env_extra["EU4_GFX_SHADOW_WAIT_MS"] = str(args.shadow_wait_ms)
    if seal_enabled:
        timeout_ms = int(getattr(args, "seal_timeout_ms", 0) or 0)
        if timeout_ms < 0 or timeout_ms > 3_600_000:
            print("capture refused: invalid seal timeout", file=sys.stderr)
            return 2
        env_extra["EU4_GFX_FRAME_SEAL"] = "1"
        if timeout_ms:
            env_extra["EU4_GFX_FRAME_TRACE_TIMEOUT_MS"] = str(timeout_ms)
        if getattr(args, "seal_log", False):
            env_extra["EU4_GFX_FRAME_SEAL_LOG"] = "1"
    coordinator = None
    if shadow_enabled:
        coordinator = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "automation.m6_frame",
                "shadow-coordinator",
                "--run",
                str(output),
                "--shadow-dir",
                str(shadow_dir),
                "--timeout",
                str(
                    max(
                        300.0,
                        args.duration
                        + args.shadow_wait_ms / 1000.0
                        + args.shadow_timeout_ms / 1000.0
                        + 120.0,
                    )
                ),
                "--seal-timeout",
                str(max(120.0, args.duration + 60.0)),
                "--translation-timeout",
                str(args.shadow_wait_ms / 1000.0 + 30.0),
                "--replay-timeout",
                str(args.shadow_timeout_ms / 1000.0 + 60.0),
            ],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
            background_plan=background_plan,
            game_args=list(getattr(args, "game_arg", []) or []) or None,
        )
    except (startup.StartupError, OSError, RuntimeError) as exc:
        if coordinator is not None:
            coordinator.terminate()
            try:
                coordinator.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                coordinator.kill()
                coordinator.communicate()
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
        "display": report.get("display"),
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
    trace = None
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
    if shadow_enabled:
        try:
            if coordinator is not None:
                try:
                    coordinator.communicate(timeout=30)
                except subprocess.TimeoutExpired:
                    coordinator.terminate()
                    coordinator.communicate(timeout=10)
            coordinator_path = shadow_dir / "coordinator.json"
            if not coordinator_path.is_file():
                raise FrameError("in-process shadow coordinator produced no report")
            shadow_report = _read_json(coordinator_path)
            native_failure = apply_shadow_report(result, shadow_report)
            if native_failure is not None:
                raise FrameError(
                    "in-process shadow failed: " + native_failure
                )
        except (FrameError, OSError, subprocess.TimeoutExpired) as exc:
            result["shadow_error"] = str(exc)
            status = 7 if status == 0 else status
    if seal_enabled:
        try:
            seal = load_seal(frame_dir)
            notification = None
            if not shadow_enabled:
                notification = load_notification(output)
                if int(notification.get("pid", -1)) != int(seal.get("pid", -2)):
                    raise FrameError("notification pid does not match the seal")
                if notification.get("capture_frame_id") != seal.get(
                    "capture_frame_id"
                ):
                    raise FrameError(
                        "notification frame id does not match the seal"
                    )
                for field in (
                    "manifest_sha256",
                    "reference_sha256",
                    "operation_count",
                    "draw_count",
                    "clear_count",
                    "copy_count",
                ):
                    if notification.get(field) != seal.get(field):
                        raise FrameError(
                            f"notification {field} does not match the seal"
                        )
            if int(seal.get("pid", -1)) != int(report.get("pid", -2)):
                raise FrameError("sealed frame pid does not match the launch")
            result["seal"] = {
                key: seal.get(key)
                for key in (
                    "schema",
                    "capture_frame_id",
                    "session_id",
                    "pid",
                    "boundary",
                    "present_index",
                    "scene_index",
                    "operation_count",
                    "draw_count",
                    "clear_count",
                    "copy_count",
                    "width",
                    "height",
                    "pixel_format",
                    "manifest_sha256",
                    "reference_sha256",
                    "readback_ns",
                    "finalize_ns",
                    "seal_ns",
                )
            }
            if notification is not None:
                result["notification"] = notification
            status = 0 if status == 0 else status
        except FrameError as exc:
            result["seal_error"] = str(exc)
            status = 6
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
REPLAY_HOST_CLI_SOURCE = BENCH / "eu4_gfx_frame_replay_cli.mm"
REPLAY_HOST = BENCH / ".build" / "gfx_frame_replay"


def build_replay_host(force: bool = False) -> Path:
    dependencies = [REPLAY_HOST_SOURCE, REPLAY_HOST_CLI_SOURCE, Path(__file__)]
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
            str(REPLAY_HOST_CLI_SOURCE),
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
    if args.only_event is not None:
        try:
            trace = load_trace(frame_dir)
        except FrameError as exc:
            print(json.dumps({"status": "invalid", "reason": str(exc)}, indent=2))
            return 1
        events = trace.get("_events") or []
        if not 0 <= args.only_event < len(events):
            print(
                json.dumps(
                    {
                        "status": "invalid",
                        "reason": (
                            f"event {args.only_event} is outside the "
                            f"{len(events)}-event frame"
                        ),
                    },
                    indent=2,
                )
            )
            return 1
        selected = events[args.only_event]
        if selected.get("kind") != "draw":
            print(
                json.dumps(
                    {
                        "status": "invalid",
                        "reason": (
                            f"event {args.only_event} is a "
                            f"{selected.get('kind')} event, not a draw"
                        ),
                    },
                    indent=2,
                )
            )
            return 1
        if not selected.get("pre_image"):
            print(
                json.dumps(
                    {
                        "status": "invalid",
                        "reason": (
                            f"event {args.only_event} has no authentic GL "
                            "pre-image"
                        ),
                    },
                    indent=2,
                )
            )
            return 1
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
    if args.only_event is not None:
        command += ["--only-event", str(args.only_event)]
        command.append("--capture-pre")
        command.append("--capture-post")
    if args.depth_from_prefix:
        command.append("--depth-from-prefix")
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
        "isolated": result.get("isolated"),
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


def _bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def selected_event_report(
    frame_dir: Path, candidate: Path, event_index: int
) -> dict:
    """One selected draw's authentic GL pre/post versus its isolated Metal run."""
    from automation import m6_compare

    try:
        trace = load_trace(frame_dir)
    except FrameError as exc:
        return {"status": "invalid", "reason": str(exc)}
    events = trace.get("_events") or []
    if not 0 <= event_index < len(events):
        return {
            "status": "invalid",
            "reason": f"event {event_index} is outside the {len(events)}-event frame",
        }
    event = events[event_index]
    if event.get("kind") != "draw":
        return {
            "status": "invalid",
            "reason": f"event {event_index} is a {event.get('kind')} event",
        }
    pre = event.get("pre_image")
    post = event.get("post_image")
    if not isinstance(pre, dict) or not isinstance(post, dict):
        return {
            "status": "missing_pre_post",
            "reason": f"event {event_index} has no GL pre/post pair",
        }
    width = int(post.get("width") or pre.get("width") or 0)
    height = int(post.get("height") or pre.get("height") or 0)
    gl_pre_path = frame_dir / "blobs" / str(pre.get("file") or "")
    gl_post_path = frame_dir / "blobs" / str(post.get("file") or "")
    metal_pre_path = candidate / f"isolated-pre-{event_index}.bgra"
    metal_post_path = candidate / f"isolated-post-{event_index}.bgra"
    missing = [
        str(path)
        for path in (
            gl_pre_path,
            gl_post_path,
            metal_pre_path,
            metal_post_path,
        )
        if not path.is_file()
    ]
    if missing:
        return {
            "status": "missing",
            "reason": "missing image files",
            "missing": missing,
            "event": {
                "index": event_index,
                "caller": (event.get("draw") or {}).get("caller_rva"),
            },
        }
    gl_pre = gl_pre_path.read_bytes()
    gl_post = gl_post_path.read_bytes()
    metal_pre = metal_pre_path.read_bytes()
    metal_post = metal_post_path.read_bytes()
    if width <= 0 or height <= 0:
        return {"status": "invalid", "reason": "non-positive image dimensions"}
    size = width * height * 4
    for name, data in (
        ("gl_pre", gl_pre),
        ("gl_post", gl_post),
        ("metal_pre", metal_pre),
        ("metal_post", metal_post),
    ):
        if len(data) != size:
            return {
                "status": "invalid",
                "reason": f"{name} size {len(data)} does not match {size}",
            }
    replay_path = candidate / "replay.json"
    replay = {}
    if replay_path.is_file():
        try:
            replay = json.loads(replay_path.read_text())
        except (OSError, json.JSONDecodeError):
            replay = {}
    isolated = replay.get("isolated") or {}
    replay_valid = bool(
        replay.get("status") == "ok"
        and replay.get("failed") == 0
        and replay.get("command_status") == 4
        and isolated.get("event") == event_index
        and isolated.get("pre_image_supplied") is True
        and isolated.get("pre_capture_ok") is True
        and isolated.get("color_initialization") == "supplied_pre_image"
        and isolated.get("depth_contents") in ("not_required", "replayed_prefix")
    )
    gl_change = m6_compare.compare_images(gl_pre, gl_post, width, height)
    metal_change = m6_compare.compare_images(metal_pre, metal_post, width, height)
    pre_match = gl_pre == metal_pre
    post_match = gl_post == metal_post
    post_difference = m6_compare.compare_images(
        gl_post, metal_post, width, height
    )
    target = event.get("target") or {}
    attachment = target.get("color_attachment") or {}
    report = {
        "schema": "gfx-isolated-event-differential/v1",
        "frame": str(frame_dir),
        "candidate": str(candidate),
        "status": "compared",
        "event": {
            "index": event_index,
            "caller": (event.get("draw") or {}).get("caller_rva"),
            "program": (event.get("program") or {}).get("name"),
            "draw": event.get("draw") or {},
            "target": {
                "framebuffer": target.get("framebuffer"),
                "name": attachment.get("name"),
                "internal_format": attachment.get("internal_format"),
                "width": width,
                "height": height,
            },
            "pre_image": {
                "gl_sha256": _bytes_sha256(gl_pre),
                "metal_sha256": _bytes_sha256(metal_pre),
                "sha256": pre.get("sha256"),
                "match": pre_match,
                "bytes": len(gl_pre),
            },
            "post_image": {
                "gl_sha256": _bytes_sha256(gl_post),
                "metal_sha256": _bytes_sha256(metal_post),
                "sha256": post.get("sha256"),
            },
            "changed_pixels": {
                "gl": gl_change["mismatched_pixels"],
                "metal": metal_change["mismatched_pixels"],
            },
            "gl_pre_to_post": gl_change,
            "metal_pre_to_post": metal_change,
            "post_difference": post_difference,
            "pre_match": pre_match,
            "post_match": post_match,
            "exact_isolated_replay": bool(isolated.get("exact")),
            "replay_valid": replay_valid,
            "depth": {
                "matters": isolated.get("depth_matters"),
                "contents": isolated.get("depth_contents"),
            },
            "color_initialization": isolated.get("color_initialization"),
        },
        "ok": replay_valid and pre_match and post_match,
    }
    (candidate / f"isolated-event-{event_index}.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report


def isolate_command(args: argparse.Namespace) -> int:
    """Compares isolated replay output with authentic GL pre/post images."""
    from automation import m6_compare

    frame_dir = Path(args.frame).expanduser().resolve()
    candidate = Path(args.candidate).expanduser().resolve()
    if args.event is not None:
        report = selected_event_report(frame_dir, candidate, args.event)
        print(json.dumps(report, indent=2))
        return 0 if report.get("ok") else 2
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
# In-process shadow coordinator
# ---------------------------------------------------------------------------


def shadow_failure_detail(report: dict) -> str:
    """Return the native shadow failure without degrading it to a generic text.

    The bridge keeps the native stage and error in both the top-level report and
    ``metal``; replay.json additionally carries a structured ``failure`` object.
    Prefer those over any coordinator fallback so a specific manifest, shader,
    event or GPU failure survives into coordinator.json and capture-report.json.
    """
    if not isinstance(report, dict):
        return "Metal replay failed"
    metal = report.get("metal")
    if not isinstance(metal, dict):
        metal = {}
    failure = report.get("failure")
    if not isinstance(failure, dict):
        failure = {}
    error = report.get("error")
    stage = failure.get("stage") or metal.get("stage") or report.get("stage")
    if not error or (stage and not str(error).startswith(str(stage))):
        error = metal.get("error") or failure.get("error") or error
    if not error:
        error = "Metal replay failed"
    error = str(error)
    if stage and not error.startswith(str(stage)):
        return f"{stage}: {error}"
    return error


def apply_shadow_report(result: dict, shadow_report: dict) -> str | None:
    """Copy the coordinator report into the final capture summary.

    Returns the native failure text when the shadow run failed, otherwise None.
    The capture summary therefore carries the same stage, error and structured
    failure object as shadow-report.json and coordinator.json.
    """
    result["shadow"] = shadow_report
    result["shadow_stage"] = shadow_report.get("stage")
    result["shadow_timed_out"] = bool(shadow_report.get("timed_out", False))
    failure = shadow_report.get("failure")
    if isinstance(failure, dict) and failure:
        result["shadow_failure"] = failure
    result["pixel_verdict"] = shadow_report.get("pixel_verdict")
    result["qualified"] = bool(shadow_report.get("qualified"))
    result["classification"] = shadow_report.get("classification")
    result["first_divergence"] = shadow_report.get("first_divergence")
    result["checkpoint_validation"] = shadow_report.get(
        "checkpoint_validation")
    result["state_coverage"] = shadow_report.get("state_coverage")
    result["diagnostic"] = {
        "metal": "complete" if shadow_report.get("status") == "ok" else "failed",
        "pixel": shadow_report.get("pixel_verdict"),
        "classification": shadow_report.get("classification"),
        "axes": (shadow_report.get("diagnosis") or {}).get("axes"),
        "diagnostic_qualified": shadow_report.get("diagnostic_qualified"),
        "first_divergence": shadow_report.get("first_divergence"),
        "checkpoint_validation": shadow_report.get("checkpoint_validation"),
        "diagnostic_report": (shadow_report.get("diagnosis") or {}).get(
            "report"),
    }
    if shadow_report.get("status") == "ok":
        return None
    return shadow_failure_detail(shadow_report)


def shadow_coordinator(args: argparse.Namespace) -> int:
    """Coordinates the one external translator step for a sealed live frame.

    This process never launches EU4.  It waits for the PR 1 seal, runs the
    existing shader translator into the shadow directory, publishes the ready
    marker the in-process bridge waits for, then waits for the Metal report and
    runs the existing differential comparison.
    """
    run = Path(args.run).expanduser().resolve()
    shadow = (
        Path(args.shadow_dir).expanduser().resolve()
        if args.shadow_dir
        else run / "shadow"
    )
    if args.timeout <= 0 or args.seal_timeout <= 0 or             args.translation_timeout <= 0 or args.replay_timeout <= 0 or             args.compare_timeout <= 0:
        shadow.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(
            json.dumps(
                {"schema": "m7-shadow-coordinator/v1", "status": "failed",
                 "error": "invalid phase budget"},
                indent=2,
            )
            + "\n"
        )
        return 1
    frame_dir = run / "frame"
    shader_dir = shadow / "shaders"
    report_path = shadow / "shadow-report.json"
    result: dict = {
        "schema": "m7-shadow-coordinator/v1",
        "run": str(run),
        "shadow_dir": str(shadow),
        "status": "incomplete",
    }
    overall_deadline = time.monotonic() + args.timeout
    seal_deadline = min(overall_deadline, time.monotonic() + args.seal_timeout)
    seal_path = frame_dir / "frame-seal.json"
    while not seal_path.is_file() and time.monotonic() < seal_deadline:
        time.sleep(0.1)
    if not seal_path.is_file():
        result["status"] = "failed"
        result["error"] = "frame seal did not appear"
        (shadow / "coordinator.json").parent.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    try:
        seal = load_seal(frame_dir)
    except FrameError as exc:
        result["status"] = "failed"
        result["error"] = f"frame seal is not usable: {exc}"
        (shadow / "coordinator.json").parent.mkdir(parents=True, exist_ok=True)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["seal"] = {
        "capture_frame_id": seal.get("capture_frame_id"),
        "pid": seal.get("pid"),
        "operation_count": seal.get("operation_count"),
        "reference_sha256": seal.get("reference_sha256"),
    }
    shadow.mkdir(parents=True, exist_ok=True)
    shader_dir.mkdir(parents=True, exist_ok=True)
    for stale in (
        shader_dir / "ready",
        shadow / "shadow-report.json",
        shadow / "replay.json",
        shadow / "comparison.json",
    ):
        if stale.exists():
            result["status"] = "failed"
            result["error"] = f"stale shadow artifact: {stale}"
            (shadow / "coordinator.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            return 1
    try:
        translated = subprocess.run(
            [
                sys.executable,
                "-m",
                "automation.m6_frame",
                "translate",
                "--frame",
                str(frame_dir),
                "--out",
                str(shader_dir),
                "--force",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=min(
                args.translation_timeout,
                max(0.0, overall_deadline - time.monotonic()),
            ),
            check=False,
        )
    except subprocess.TimeoutExpired:
        result["status"] = "failed"
        result["error"] = "shader translation timed out"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    if translated.returncode != 0:
        result["status"] = "failed"
        result["error"] = "shader translation failed"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    # Build the dependency graph and publish the checkpoint request before the
    # ready marker.  The bridge binds this request's digest into the receipt,
    # so a stale or mismatched plan cannot be executed silently.
    try:
        from automation import gfx_frame_graph

        graph = gfx_frame_graph.build(frame_dir)
        if graph.get("status") == "invalid":
            raise FrameError(
                "frame graph is invalid: "
                + "; ".join(graph.get("reasons") or ["unknown"]))
        request_path = shadow / "checkpoint-request.json"
        request = gfx_frame_graph.write_checkpoint_request(graph, request_path)
        request_sha256 = _sha256(request_path)
    except (FrameError, OSError, ValueError) as exc:
        result["status"] = "failed"
        result["error"] = f"checkpoint request preparation failed: {exc}"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["checkpoint_request"] = {
        "file": request_path.name,
        "sha256": request_sha256,
        "count": len(request.get("checkpoints") or []),
        "source": str(request_path),
        "graph_status": graph.get("status"),
    }
    def artifact_digest(directory: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        count = 0
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name in ("ready", "shader-receipt.json"):
                continue
            digest.update(str(path.relative_to(directory)).encode())
            digest.update(b"\0")
            digest.update(_sha256(path).encode())
            digest.update(b"\n")
            count += 1
        return digest.hexdigest(), count

    shaders_json = shader_dir / "shaders.json"
    artifacts_sha256, artifact_count = artifact_digest(shader_dir)
    receipt = {
        "schema": "m7-shadow-shader-receipt/v1",
        "capture_frame_id": seal.get("capture_frame_id"),
        "pid": seal.get("pid"),
        "session_id": seal.get("session_id"),
        "trace_manifest_sha256": seal.get("manifest_sha256"),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "event_stream_bytes": seal.get("event_stream_bytes"),
        "shaders_json_sha256": _sha256(shaders_json),
        "artifacts_sha256": artifacts_sha256,
        "artifact_count": artifact_count,
        "checkpoint_request_file": request_path.name,
        "checkpoint_request_sha256": request_sha256,
        "checkpoint_count": len(request.get("checkpoints") or []),
    }
    receipt_path = shadow / "shader-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (shader_dir / "ready").write_text(
        json.dumps(
            {
                "status": "ready",
                "capture_frame_id": seal.get("capture_frame_id"),
                "shader_receipt_sha256": _sha256(receipt_path),
            }
        )
        + "\n"
    )
    # Translation time does not consume the in-process replay budget.
    report_deadline = min(
        overall_deadline, time.monotonic() + args.replay_timeout
    )
    while not report_path.is_file() and time.monotonic() < report_deadline:
        time.sleep(0.1)
    if not report_path.is_file():
        result["status"] = "failed"
        result["error"] = "in-process Metal replay report did not appear"
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    try:
        report = _read_json(report_path)
    except FrameError as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
        return 1
    result["metal"] = report
    result["stage"] = report.get("stage")
    result["timed_out"] = bool(report.get("timed_out", False))
    if isinstance(report.get("failure"), dict) and report.get("failure"):
        result["failure"] = report.get("failure")
    provenance_error = None
    for report_field, seal_field in (
        ("capture_frame_id", "capture_frame_id"),
        ("pid", "pid"),
        ("gl_reference_sha256", "reference_sha256"),
        ("trace_manifest_sha256", "manifest_sha256"),
        ("operation_count", "operation_count"),
    ):
        if report.get(report_field) != seal.get(seal_field):
            provenance_error = (
                f"shadow report {report_field} does not match the sealed frame"
            )
            break
    execution_failed = report.get("status") != "ok"
    result["provenance_ok"] = provenance_error is None
    result["execution_failed"] = execution_failed
    if provenance_error is not None:
        result["status"] = "failed"
        result["provenance_error"] = provenance_error
        if execution_failed:
            # Keep the native execution failure as the actionable error while
            # recording the independent identity mismatch.
            result["error"] = shadow_failure_detail(report)
        else:
            result["error"] = provenance_error
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    if execution_failed:
        result["status"] = "failed"
        result["error"] = shadow_failure_detail(report)
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    if report.get("checkpoint_request_sha256") != request_sha256:
        result["status"] = "failed"
        result["provenance_error"] = (
            "shadow report checkpoint request digest does not match the "
            "published request")
        result["provenance_ok"] = False
        result["error"] = result["provenance_error"]
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n")
        return 1
    if report.get("checkpoint_request_file") != request_path.name:
        result["status"] = "failed"
        result["provenance_error"] = (
            "shadow report checkpoint request file does not match the "
            "published request")
        result["provenance_ok"] = False
        result["error"] = result["provenance_error"]
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n")
        return 1
    policy_path = (
        Path(args.policy).expanduser().resolve()
        if args.policy
        else ROOT / "analysis" / "m6" / "comparison-policy.json"
    )
    try:
        from automation import gfx_independent_replay

        class _DiagnosisTimeout(Exception):
            pass

        def _diagnosis_alarm(signum, frame):
            raise _DiagnosisTimeout(
                f"diagnosis exceeded {args.compare_timeout}s")

        previous = signal.signal(signal.SIGALRM, _diagnosis_alarm)
        diagnosis_budget = min(
            args.compare_timeout,
            max(0.0, overall_deadline - time.monotonic()),
        )
        if diagnosis_budget <= 0:
            raise _DiagnosisTimeout("overall coordinator deadline expired")
        signal.setitimer(signal.ITIMER_REAL, diagnosis_budget)
        try:
            diagnosis = gfx_independent_replay.diagnose_frame(
                frame_dir, shadow, shadow, policy_path)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    except (OSError, ValueError, Exception) as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = f"diagnosis failed: {exc}"
        (shadow / "coordinator.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
        return 1
    qualification = diagnosis.get("qualification") or {}
    final = (diagnosis.get("pixels") or {}).get("final") or {}
    comparison = final.get("detail")
    result["diagnosis"] = {
        "schema": diagnosis.get("schema"),
        "classification": diagnosis.get("classification"),
        "axes": qualification.get("axes"),
        "qualified": qualification.get("qualified"),
        "diagnostic_qualified": qualification.get("diagnostic_qualified"),
        "first_divergence": diagnosis.get("first_divergence"),
        "checkpoint_validation": diagnosis.get("checkpoint_validation"),
        "pixels": final.get("statistics"),
        "report": str(shadow / "diagnostic-report.json"),
    }
    result["classification"] = diagnosis.get("classification")
    result["qualified"] = bool(qualification.get("qualified"))
    result["diagnostic_qualified"] = bool(
        qualification.get("diagnostic_qualified"))
    result["first_divergence"] = diagnosis.get("first_divergence")
    result["checkpoint_validation"] = diagnosis.get("checkpoint_validation")
    result["state_coverage"] = diagnosis.get("state_coverage")
    (shadow / "state-coverage.json").write_text(
        json.dumps(diagnosis.get("state_coverage"), indent=2) + "\n")
    result["execution"] = {
        "capture": "complete",
        "metal": (diagnosis.get("execution") or {}).get("status", "failed"),
        "operation_coverage": (
            diagnosis.get("operation_coverage") or {}).get("status", "invalid"),
    }
    if isinstance(comparison, dict):
        result["comparison"] = comparison
        result["pixel_verdict"] = comparison.get("status")
        (shadow / "comparison.json").write_text(
            json.dumps(comparison, indent=2) + "\n")
    else:
        result["pixel_verdict"] = "unavailable"
        result["comparison_error"] = final.get("reason") or (
            "the shared comparison engine produced no report")
    result["status"] = "ok"
    (shadow / "coordinator.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


def diagnose_command(args: argparse.Namespace) -> int:
    """Diagnose an already-produced in-process Metal result offline."""
    from automation import gfx_independent_replay

    frame_dir = Path(args.frame).expanduser().resolve()
    metal_dir = Path(args.metal).expanduser().resolve()
    out = (Path(args.out).expanduser().resolve()
           if args.out else metal_dir)
    try:
        report = gfx_independent_replay.diagnose_frame(
            frame_dir, metal_dir, out, args.policy,
            package=bool(args.package),
            shaders=(Path(args.shaders).expanduser().resolve()
                     if args.shaders else None))
    except (gfx_independent_replay.ReplayError, FrameError, OSError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "schema": report.get("schema"),
        "status": report.get("status"),
        "classification": report.get("classification"),
        "qualification": report.get("qualification", {}).get("axes"),
        "first_divergence": {
            "kind": (report.get("first_divergence") or {}).get("kind"),
            "event": (report.get("first_divergence") or {}).get("event"),
            "confidence": (report.get("first_divergence") or {}).get("confidence"),
        },
        "checkpoint_validation": report.get("checkpoint_validation"),
        "report": str(out / "diagnostic-report.json"),
    }, indent=2))
    return gfx_independent_replay.diagnosis_exit_code(report)


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
    seal_path = frame_dir / "frame-seal.json"
    if seal_path.is_file():
        try:
            summary["seal"] = load_seal(frame_dir, trace=trace)
        except FrameError as exc:
            summary["seal"] = {"invalid": str(exc)}
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
    cap.add_argument("--timeline", action="store_true")
    cap.add_argument("--copy-trace", type=int, default=None,
                     help="bounded full-copy trace for one destination texture")
    cap.add_argument("--copy-source", action="store_true",
                     help="capture the default-framebuffer pixels of in-frame copies")
    cap.add_argument("--seal", action="store_true",
                     help="publish the authoritative frame-seal/v1 handoff")
    cap.add_argument("--shadow", action="store_true",
                     help="execute the sealed frame through the in-process Metal bridge")
    cap.add_argument("--shadow-dir", default="",
                     help="shadow output directory (default: <output>/shadow)")
    cap.add_argument("--shadow-timeout-ms", type=int, default=120000)
    cap.add_argument("--shadow-wait-ms", type=int, default=180000)
    cap.add_argument("--seal-log", action="store_true",
                     help="register the in-process logging consumer for the seal")
    cap.add_argument("--seal-timeout-ms", type=int, default=600000,
                     help="bounded time from arming to a sealed/incomplete result")
    cap.add_argument("--background", action="store_true")
    cap.add_argument("--game-arg", action="append", default=[])
    cap.add_argument("--max-draws", type=int, default=256)
    cap.add_argument("--duration", type=float, default=90.0)
    cap.add_argument("--timeout", type=float, default=180.0)
    cap.set_defaults(handler=capture)

    coord = sub.add_parser(
        "shadow-coordinator",
        help="translate shaders and compare one sealed frame's in-process Metal replay",
    )
    coord.add_argument("--run", required=True)
    coord.add_argument("--shadow-dir", default="")
    coord.add_argument("--timeout", type=float, default=900.0)
    coord.add_argument("--seal-timeout", type=float, default=300.0)
    coord.add_argument("--translation-timeout", type=float, default=180.0)
    coord.add_argument("--replay-timeout", type=float, default=180.0)
    coord.add_argument("--compare-timeout", type=float, default=60.0)
    coord.add_argument("--policy")
    coord.add_argument("--no-compare", action="store_true")
    coord.set_defaults(handler=shadow_coordinator)

    diag = sub.add_parser(
        "diagnose",
        help="diagnose an already-produced Metal result without re-executing it",
    )
    diag.add_argument("--frame", required=True)
    diag.add_argument("--metal", required=True)
    diag.add_argument("--out", default="")
    diag.add_argument("--policy")
    diag.add_argument("--shaders", default="")
    diag.add_argument("--package", action="store_true")
    diag.set_defaults(handler=diagnose_command)

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
    rep.add_argument(
        "--only-event",
        type=int,
        default=None,
        help="replay one selected draw against its authentic GL pre-image",
    )
    rep.add_argument(
        "--depth-from-prefix",
        action="store_true",
        help="replay preceding events so the selected draw's depth input is defined",
    )
    rep.set_defaults(handler=replay_command)

    iso = sub.add_parser("isolate", help="isolated marked-draw differential")
    iso.add_argument("--frame", required=True)
    iso.add_argument("--candidate", required=True)
    iso.add_argument(
        "--event",
        type=int,
        default=None,
        help="compare one selected event (keeps the all-events mode when omitted)",
    )
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
