#!/usr/bin/env python3
"""Agent-facing bounded live observation and offline Metal replay.

    python3 -m automation.live_gfx capture --target _GfxSetViewPort ...
    python3 -m automation.live_gfx replay --capture ... --output ...

Live capture is authorized once by the operator: either the manual
``--authorize-live`` flag or a campaign-launch counter initialized by the
existing campaign runner. There is no separate authorization database.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_op_capture as capture_reader  # noqa: E402
import eu4_gfx_startup_trace as startup  # noqa: E402
from automation import launch_ledger  # noqa: E402

LAUNCH_SCHEMA = "live-launch/v0"
EXIT_COMPLETE = 0
EXIT_INCOMPLETE = 2
EXIT_LIMIT = 3
EXIT_UNAVAILABLE = 4


class LaunchLimit(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _rva_value(text: str) -> int:
    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid RVA: {text}") from exc
    if not 0 < value <= 0xFFFFFFFF:
        raise argparse.ArgumentTypeError(f"RVA out of range: {text}")
    return value


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def _claim_launch(
    run_root: Path,
    output: Path,
    targets: list[str],
    *,
    requested_timeout: float,
    executable_sha256: str | None = None,
    executable_path: str | None = None,
    require_pinned: bool = False,
) -> str:
    try:
        requested_timeout = launch_ledger.validate_timeout(
            requested_timeout, field="requested_timeout"
        )
    except launch_ledger.LedgerError as exc:
        raise LaunchLimit(exc.kind, exc.detail) from exc
    run_root = run_root.expanduser().resolve()
    if not run_root.is_dir():
        raise LaunchLimit("missing", f"campaign run root does not exist: {run_root}")
    try:
        pointer = launch_ledger.read_pointer(run_root)
    except launch_ledger.LedgerError as exc:
        raise LaunchLimit("malformed", str(exc)) from exc
    if pointer is not None:
        try:
            token = launch_ledger.claim(
                Path(pointer["ledger"]),
                campaign_id=run_root.name,
                run_root=run_root,
                output=output,
                targets=targets,
                requested_timeout=requested_timeout,
                executable_sha256=executable_sha256,
                executable_path=executable_path,
                require_pinned=require_pinned,
            )
        except launch_ledger.LedgerError as exc:
            raise LaunchLimit(exc.kind, exc.detail) from exc
        _mirror_claim(run_root, token, output, targets)
        return token
    if require_pinned:
        raise LaunchLimit(
            "unauthorized",
            "the Metal probe requires a modern pinned workflow authorization pointer",
        )
    lock_path = run_root / "live-launch.lock"
    counter_path = run_root / "live-launch.json"
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LaunchLimit("busy", "another capture holds the launch counter") from exc
        try:
            if not counter_path.is_file():
                raise LaunchLimit(
                    "missing",
                    f"campaign launch counter is absent: {counter_path}",
                )
            try:
                data = json.loads(counter_path.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LaunchLimit("malformed", str(exc)) from exc
            if data.get("schema") != LAUNCH_SCHEMA:
                raise LaunchLimit("malformed", "unexpected launch counter schema")
            limit = data.get("limit")
            used = data.get("used")
            if type(limit) is not int or type(used) is not int or limit < 0:
                raise LaunchLimit("malformed", "invalid launch counter values")
            if used >= limit:
                raise LaunchLimit(
                    "exhausted",
                    f"{used}/{limit} campaign launches already used",
                )
            deadline = data.get("deadline_seconds")
            if deadline is not None:
                if type(deadline) not in (int, float) or deadline < 0:
                    raise LaunchLimit("malformed", "invalid deadline_seconds")
                if requested_timeout > float(deadline):
                    raise LaunchLimit(
                        "deadline",
                        f"requested {requested_timeout:g}s exceeds campaign "
                        f"limit {float(deadline):g}s",
                    )
            token = f"{time.time_ns()}-{os.getpid()}"
            data["used"] = used + 1
            receipts = data.setdefault("launches", [])
            if not isinstance(receipts, list):
                raise LaunchLimit("malformed", "launches is not a list")
            receipts.append(
                {
                    "token": token,
                    "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "output": str(output),
                    "targets": list(targets),
                    "status": "claimed",
                }
            )
            _atomic_json(counter_path, data)
            return token
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _mirror_claim(
    run_root: Path, token: str, output: Path, targets: list[str]
) -> None:
    """Best-effort compatibility mirror; never a second authority."""
    counter_path = run_root / "live-launch.json"
    if not counter_path.is_file():
        return
    lock_path = run_root / "live-launch.lock"
    try:
        with lock_path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                data = json.loads(counter_path.read_text())
                if not isinstance(data, dict):
                    return
                data["used"] = int(data.get("used", 0)) + 1
                receipts = data.setdefault("launches", [])
                if not isinstance(receipts, list):
                    return
                receipts.append(
                    {
                        "token": token,
                        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "output": str(output),
                        "targets": list(targets),
                        "status": "claimed",
                    }
                )
                _atomic_json(counter_path, data)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return


def _mirror_finish(
    run_root: Path, token: str, *, pid: int | None, status: str
) -> None:
    counter_path = run_root / "live-launch.json"
    if not counter_path.is_file():
        return
    lock_path = run_root / "live-launch.lock"
    try:
        with lock_path.open("a+") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            try:
                data = json.loads(counter_path.read_text())
                receipts = data.get("launches") if isinstance(data, dict) else None
                if not isinstance(receipts, list):
                    return
                for receipt in receipts:
                    if isinstance(receipt, dict) and receipt.get("token") == token:
                        receipt["pid"] = pid
                        receipt["status"] = status
                        _atomic_json(counter_path, data)
                        return
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return


def _finish_launch(
    run_root: Path, token: str, *, pid: int | None, status: str
) -> None:
    run_root = run_root.expanduser().resolve()
    try:
        pointer = launch_ledger.read_pointer(run_root)
    except launch_ledger.LedgerError:
        pointer = None
    if pointer is not None:
        ledger_status = "failed" if status.startswith("failed") else "completed"
        try:
            launch_ledger.finish(
                Path(pointer["ledger"]), token, pid=pid, status=ledger_status
            )
        except launch_ledger.LedgerError:
            pass
        _mirror_finish(run_root, token, pid=pid, status=status)
        return
    lock_path = run_root / "live-launch.lock"
    counter_path = run_root / "live-launch.json"
    if not counter_path.is_file():
        return
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            try:
                data = json.loads(counter_path.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError):
                return
            receipts = data.get("launches")
            if not isinstance(receipts, list):
                return
            for receipt in receipts:
                if isinstance(receipt, dict) and receipt.get("token") == token:
                    receipt["pid"] = pid
                    receipt["status"] = status
                    _atomic_json(counter_path, data)
                    return
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _capture_status(report: dict, *, until_target_draw: bool = False) -> tuple[str, str]:
    status = report.get("status")
    if not isinstance(status, dict) or status.get("state") != "installed":
        return "unavailable", "startup attestation was not installed"
    operations = report.get("operations")
    if not isinstance(operations, dict):
        return "unavailable", "operations summary was not written"
    if until_target_draw:
        if operations.get("state") != "sealed":
            return "unavailable", "capture did not seal"
        if operations.get("integrity_ok") is not True:
            return "unavailable", str(operations.get("reason") or "integrity")
        if operations.get("retention_budget_exhausted") is True:
            return "budget_exhausted", "retention_budget"
        if operations.get("target_draw_qualified") is True:
            return "complete", ""
        return "draw_not_qualified", "no_target_draw"
    if operations.get("reason"):
        return "incomplete", str(operations["reason"])
    if operations.get("events") == 0:
        return "empty", "no matching operation was observed"
    return "complete", ""


def _draw_records(output: Path) -> list[dict]:
    path = output / "operations.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and row.get("symbol") == "_GfxDraw":
                rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows


def _draw_block(report: dict, output: Path) -> dict:
    operations = report.get("operations") or {}
    target = operations.get("target_draw_caller")
    block: dict = {
        "qualified": operations.get("target_draw_qualified") is True,
        "caller_rva": f"0x{target:x}" if isinstance(target, int) and target else None,
        "observed": operations.get("draw_observed", 0),
        "at_caller": operations.get("draw_at_caller", 0),
        "qualified_count": operations.get("draw_qualified", 0),
        "modes": {
            str(index): count
            for index, count in enumerate(operations.get("draw_modes") or [])
            if count
        },
        "counts": operations.get("draw_counts") or {},
        "callers": operations.get("draw_callers") or [],
        "retention_budget_exhausted": operations.get("retention_budget_exhausted")
        is True,
    }
    records = _draw_records(output)
    samples = []
    for row in records[-8:]:
        samples.append(
            {
                "call_id": row.get("call_id"),
                "completion_seq": row.get("completion_seq"),
                "caller_rva": row.get("caller_rva"),
                "mode": row.get("mode"),
                "raw_count": row.get("raw_count"),
                "effective_count": row.get("effective_count"),
                "qualified": row.get("qualified"),
            }
        )
    block["samples"] = samples
    target_call = operations.get("target_draw_call_id")
    if block["qualified"] and isinstance(target_call, int):
        for row in records:
            if row.get("call_id") == target_call:
                block["target"] = {
                    "call_id": row.get("call_id"),
                    "completion_seq": row.get("completion_seq"),
                    "thread_id": row.get("thread_id"),
                    "caller_rva": row.get("caller_rva"),
                    "mode": row.get("mode"),
                    "raw_count": row.get("raw_count"),
                    "first": row.get("first"),
                    "effective_count": row.get("effective_count"),
                    "count_source": row.get("count_source"),
                    "primitive_enum": row.get("primitive_enum"),
                    "primitive_call_id": row.get("primitive_call_id"),
                }
                break
    return block


def _file_sha256_text(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gl_reference_block(
    output: Path, evidence: dict, caller_rva: int | None
) -> dict:
    """Validate the original-OpenGL framebuffer capture written by the observer.

    Fail closed: every field the comparator relies on is checked here, and a
    missing or truncated payload is never reported as a usable reference.
    """
    path = output / "gl-reference.json"
    if not path.is_file():
        failure = output / "gl-reference-failure.json"
        pre_failure = output / "gl-reference-pre-failure.json"
        reason = "gl-reference.json was not written"
        if failure.is_file() or pre_failure.is_file():
            reason = "the original GL readback failed"
        blocked = output / "blocked_at.json"
        if blocked.is_file():
            reason = f"GL capture was blocked: {blocked.name}"
        return {"status": "missing", "ok": False, "reason": reason}
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "ok": False, "reason": f"unreadable: {exc}"}
    problems: list[str] = []
    if not isinstance(data, dict) or data.get("schema") != "gfx-gl-reference/v1":
        return {"status": "invalid", "ok": False, "reason": "unexpected schema"}
    if data.get("backend") != "opengl":
        problems.append("backend is not opengl")
    provenance = data.get("provenance") if isinstance(data.get("provenance"), dict) else {}
    if provenance.get("executable_sha256") != evidence.get("executable_sha256"):
        problems.append("executable identity does not match the pinned launch")
    if not provenance.get("executable"):
        problems.append("executable path is missing")
    draw = data.get("draw") if isinstance(data.get("draw"), dict) else {}
    if draw.get("symbol") != "_GfxDraw":
        problems.append("draw symbol is not _GfxDraw")
    if caller_rva is not None:
        expected = f"0x{caller_rva:x}"
        if draw.get("caller_rva") != expected:
            problems.append(
                f"caller is {draw.get('caller_rva')!r}, expected {expected}"
            )
    if draw.get("mode") != 5:
        problems.append(f"draw mode is {draw.get('mode')!r}, expected 5")
    count = draw.get("effective_count")
    if not isinstance(count, int) or count < 3:
        problems.append("effective draw count is below 3")
    target = data.get("target") if isinstance(data.get("target"), dict) else {}
    width, height = target.get("width"), target.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        problems.append("target extent is missing or invalid")
        width = height = 0
    if target.get("origin") != "lower_left":
        problems.append("image origin is not declared as lower_left")
    if "draw_buffer" in target and target.get("draw_buffer") != 0x0405:
        problems.append("draw buffer is not GL_BACK")
    if "read_buffer" in target and target.get("read_buffer") != 0x0405:
        problems.append("read buffer is not GL_BACK")
    pixel_format = target.get("pixel_format")
    if pixel_format not in ("BGRA8", "RGBA8"):
        problems.append(f"unsupported pixel format {pixel_format!r}")
    gpu = data.get("gpu") if isinstance(data.get("gpu"), dict) else {}
    if gpu.get("read_pixels") is not True:
        problems.append("glReadPixels completion is not recorded")
    if gpu.get("gl_error") != 0:
        problems.append(f"GL error {gpu.get('gl_error')!r} during readback")
    if not isinstance(gpu.get("finish_ns"), int) or gpu.get("finish_ns") < 0:
        problems.append("glFinish boundary is not recorded")
    capture = data.get("capture") if isinstance(data.get("capture"), dict) else {}
    raw_name = capture.get("raw_file")
    raw_bytes = capture.get("raw_bytes")
    raw_sha = capture.get("raw_sha256")
    if not isinstance(raw_name, str) or not raw_name:
        problems.append("raw payload file is missing")
        raw_path = None
    else:
        raw_path = output / raw_name
    if width and height:
        expected_bytes = width * height * 4
        if raw_bytes != expected_bytes:
            problems.append(
                f"raw_bytes {raw_bytes!r} != {width}x{height}x4={expected_bytes}"
            )
        if capture.get("pixel_format") != pixel_format:
            problems.append("capture and target pixel formats disagree")
        if target.get("row_stride") != width * 4:
            problems.append("row_stride does not match the width")
    if raw_path is not None:
        if not raw_path.is_file():
            problems.append(f"raw payload is absent: {raw_name}")
        else:
            size = raw_path.stat().st_size
            if raw_bytes != size:
                problems.append(f"raw payload is {size} bytes, declared {raw_bytes!r}")
            digest = _file_sha256_text(raw_path)
            if digest != raw_sha:
                problems.append("raw payload sha256 does not match its declaration")
    pre = data.get("pre_draw")
    if pre is not None:
        if not isinstance(pre, dict):
            problems.append("pre_draw metadata is malformed")
        else:
            pre_path = output / str(pre.get("raw_file") or "")
            if pre.get("raw_file") and not pre_path.is_file():
                problems.append("pre-draw payload is absent")
    shader_sources = data.get("shader_sources")
    if shader_sources is not None:
        if not isinstance(shader_sources, list):
            problems.append("shader_sources metadata is malformed")
            shader_sources = []
        else:
            for entry in shader_sources:
                if not isinstance(entry, dict):
                    problems.append("shader source entry is malformed")
                    continue
                stage = entry.get("stage")
                name = entry.get("file")
                if stage not in ("vertex", "pixel", "other"):
                    problems.append(f"unexpected shader stage {stage!r}")
                if not isinstance(name, str) or not name:
                    problems.append("shader source file is missing")
                    continue
                source_path = output / name
                if not source_path.is_file():
                    problems.append(f"shader source payload is absent: {name}")
                    continue
                if source_path.stat().st_size != entry.get("bytes"):
                    problems.append(f"shader source size differs: {name}")
                if _file_sha256_text(source_path) != entry.get("sha256"):
                    problems.append(f"shader source sha256 differs: {name}")
    live_vertex = data.get("live_vertex")
    if live_vertex is not None and not isinstance(live_vertex, dict):
        problems.append("live_vertex metadata is malformed")
        live_vertex = None
    if isinstance(live_vertex, dict) and live_vertex.get("status") == "ok":
        raw_name = live_vertex.get("raw_file")
        live_path = output / str(raw_name or "")
        if not isinstance(raw_name, str) or not raw_name or not live_path.is_file():
            problems.append("live vertex payload is absent")
        else:
            if live_path.stat().st_size != live_vertex.get("bytes"):
                problems.append("live vertex size differs from its declaration")
            if _file_sha256_text(live_path) != live_vertex.get("sha256"):
                problems.append("live vertex sha256 differs from its declaration")
            stride = live_vertex.get("stride")
            count = live_vertex.get("count")
            if (
                not isinstance(stride, int)
                or not isinstance(count, int)
                or stride <= 0
                or count <= 0
                or stride * count != live_vertex.get("bytes")
            ):
                problems.append("live vertex range does not match its layout")
    return {
        "status": "ok" if not problems else "invalid",
        "ok": not problems,
        "reason": "; ".join(problems) if problems else "",
        "path": str(path),
        "raw_file": raw_name,
        "raw_sha256": raw_sha,
        "width": width,
        "height": height,
        "pixel_format": pixel_format,
        "caller_rva": draw.get("caller_rva"),
        "call_id": draw.get("call_id"),
        "executable_sha256": provenance.get("executable_sha256"),
        "draw": draw,
        "target": target,
        "gpu": gpu,
        "pre_draw": pre,
        "shader_sources": shader_sources or [],
        "live_vertex": live_vertex,
    }


def capture(args: argparse.Namespace) -> int:
    if bool(args.authorize_live) == bool(args.campaign_run_root):
        print(
            "capture requires exactly one of --authorize-live or "
            "--campaign-run-root",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"capture output already exists: {output}", file=sys.stderr)
        return EXIT_INCOMPLETE
    until_target_draw = bool(args.until_target_draw)
    if args.gl_reference and not until_target_draw:
        print("--gl-reference requires --until-target-draw", file=sys.stderr)
        return EXIT_INCOMPLETE
    if until_target_draw:
        if args.max_events is not None or args.duration is not None:
            print(
                "--until-target-draw does not accept --max-events or --duration",
                file=sys.stderr,
            )
            return EXIT_INCOMPLETE
        if not args.caller_rva:
            print("--until-target-draw requires --caller-rva", file=sys.stderr)
            return EXIT_INCOMPLETE
        max_events = 1024
        duration = 0.0
    else:
        if args.caller_rva:
            print("--caller-rva requires --until-target-draw", file=sys.stderr)
            return EXIT_INCOMPLETE
        max_events = 256 if args.max_events is None else args.max_events
        duration = 10.0 if args.duration is None else args.duration
    try:
        evidence = startup.preflight_ops(
            args.target,
            max_events=max_events,
            duration_s=duration,
            until_target_draw=until_target_draw,
            target_caller_rva=args.caller_rva,
            gl_reference=bool(args.gl_reference),
        )
    except (startup.StartupError, OSError, RuntimeError) as exc:
        print(f"capture preflight failed: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    token = None
    if args.campaign_run_root:
        try:
            token = _claim_launch(
                Path(args.campaign_run_root),
                output,
                args.target,
                requested_timeout=args.timeout,
                executable_sha256=evidence.get("executable_sha256"),
            )
        except LaunchLimit as exc:
            print(f"campaign launch refused: {exc}", file=sys.stderr)
            return EXIT_LIMIT

    try:
        report = startup.run_ops(
            targets=args.target,
            max_events=max_events,
            duration_s=duration,
            timeout_s=args.timeout,
            output=output,
            authorize_live=True,
            evidence=evidence,
            until_target_draw=until_target_draw,
            target_caller_rva=args.caller_rva,
            gl_reference=bool(args.gl_reference),
        )
    except (startup.StartupError, OSError, RuntimeError) as exc:
        if token is not None:
            _finish_launch(
                Path(args.campaign_run_root),
                token,
                pid=None,
                status=f"failed: {exc}",
            )
        print(f"capture failed: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    status, reason = _capture_status(
        report, until_target_draw=until_target_draw
    )
    gl_reference = {"status": "not_requested", "ok": True}
    gl_vertex = {"status": "not_requested", "ok": True}
    if args.gl_reference:
        gl_reference = _gl_reference_block(output, evidence, args.caller_rva)
        gl_vertex = _gl_vertex_block(output, gl_reference)
        gl_reference["vertex"] = gl_vertex
        if status == "complete" and not gl_reference["ok"]:
            status = "incomplete"
            reason = f"gl_reference_{gl_reference['status']}"
        elif status == "complete" and not gl_vertex["ok"]:
            status = "incomplete"
            reason = f"gl_vertex_{gl_vertex['status']}"
    coverage: dict = {}
    validation: dict = {}
    draw_block: dict = {}
    if status in ("complete", "draw_not_qualified", "budget_exhausted"):
        draw_block = _draw_block(report, output)
        try:
            artifact = capture_reader.load_capture(
                output, require_events=status == "complete"
            )
        except capture_reader.CaptureIncomplete as exc:
            if status == "complete":
                status = "unavailable"
                reason = exc.reason
            validation = {"reason": exc.reason, "detail": exc.detail}
        else:
            coverage = artifact.coverage
            validation = {
                "ok": status != "budget_exhausted",
                "source_hashes": artifact.source_hashes,
                "scope": "draw_probe" if until_target_draw else "function_cpu_state",
            }
    if token is not None:
        _finish_launch(
            Path(args.campaign_run_root),
            token,
            pid=report.get("pid"),
            status=status,
        )
    result = {
        "status": status,
        "reason": reason,
        "capture": str(output),
        "session": report.get("session"),
        "pid": report.get("pid"),
        "coverage": coverage,
        "validation": validation,
        "draw": draw_block,
        "gl_reference": gl_reference,
        "gl_vertex": gl_vertex,
        "launch": {
            "mode": "campaign" if token is not None else "manual",
        },
    }
    (output / "capture-report.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))
    if status == "complete":
        return EXIT_COMPLETE
    if status == "budget_exhausted":
        return EXIT_UNAVAILABLE
    return EXIT_INCOMPLETE


def _default_replay_output(capture_path: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / ".local" / "gfx-diffs" / f"{capture_path.name}-{stamp}"


def _write_replay_error(output: Path, result: dict) -> bool:
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"replay output already exists: {output}", file=sys.stderr)
        return False
    (output / "comparison.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    return True


def replay(args: argparse.Namespace) -> int:
    import eu4_gfx_op_diff

    capture_path = Path(args.capture).expanduser().resolve()
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else _default_replay_output(capture_path)
    )
    if output.exists():
        print(f"replay output already exists: {output}", file=sys.stderr)
        return EXIT_INCOMPLETE
    try:
        result = eu4_gfx_op_diff.run(
            capture_path, output, skip_unknown=args.skip_unknown
        )
    except capture_reader.CaptureIncomplete as exc:
        result = {
            "status": "incomplete",
            "reason": exc.reason,
            "detail": exc.detail,
            "capture": str(capture_path),
            "output": str(output),
        }
        if not _write_replay_error(output, result):
            return EXIT_INCOMPLETE
    except eu4_gfx_op_diff.DiffUsage as exc:
        print(f"replay refused: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    except eu4_gfx_op_diff.DiffUnavailable as exc:
        result = {
            "status": "unavailable",
            "reason": exc.reason,
            "detail": exc.detail,
            "capture": str(capture_path),
            "output": str(output),
        }
        if not _write_replay_error(output, result):
            return EXIT_INCOMPLETE
        print(json.dumps(result, indent=2))
        return EXIT_UNAVAILABLE
    print(json.dumps(result, indent=2))
    return eu4_gfx_op_diff.EXIT_CODES.get(result.get("status"), EXIT_UNAVAILABLE)


def packet(args: argparse.Namespace) -> int:
    import eu4_gfx_draw_packet

    capture_path = Path(args.capture).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"packet output already exists: {output}", file=sys.stderr)
        return EXIT_INCOMPLETE
    try:
        result = eu4_gfx_draw_packet.write_packet(
            capture_path, output, expected_caller=args.caller_rva
        )
    except eu4_gfx_draw_packet.PacketError as exc:
        print(f"packet refused: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    print(json.dumps(result["packet"], indent=2))
    status = result["packet"].get("status")
    if status == "ready":
        return EXIT_COMPLETE
    if status == "unsupported":
        return EXIT_LIMIT
    if status == "unavailable":
        return EXIT_UNAVAILABLE
    return EXIT_INCOMPLETE


def next_blocker(args: argparse.Namespace) -> int:
    from automation.next_blocker import classify

    result = classify(Path(args.run))
    print(json.dumps(result, indent=2))
    if result["status"] in ("actionable", "advanced"):
        return 0
    if result["status"] == "needs_evidence":
        return EXIT_INCOMPLETE
    return EXIT_UNAVAILABLE


def _authority_dir() -> Path:
    override = os.environ.get("EU4_LAUNCH_AUTHORITY_DIR")
    if override:
        return Path(override).expanduser()
    # The preferred location is outside the worktree, but a workspace-write
    # sandbox cannot create ~/Library. .local is gitignored and outside the
    # candidate's normal source tree; it is not a protection against malicious
    # same-user code (the project's documented trust model).
    return ROOT / ".local" / "launch-authority"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def authorize(args: argparse.Namespace) -> int:
    """Operator-only: pin an executable and grant a bounded launch budget."""
    from reverse_engineering import paths as re_paths

    run_root = Path(args.run_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    executable = Path(
        args.executable or re_paths.load_target()["default_executable"]
    ).expanduser().resolve(strict=False)
    if not executable.is_file():
        print(f"authorize refused: executable is absent: {executable}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    if args.limit < 0:
        print("authorize refused: --limit must be non-negative", file=sys.stderr)
        return EXIT_INCOMPLETE
    deadline_seconds = None
    if args.deadline_seconds is not None:
        try:
            deadline_seconds = launch_ledger.validate_timeout(
                args.deadline_seconds, field="--deadline-seconds"
            )
        except launch_ledger.LedgerError as exc:
            print(f"authorize refused: {exc}", file=sys.stderr)
            return EXIT_INCOMPLETE
    workflow_id = args.workflow_id or run_root.name
    ledger = (
        Path(args.ledger).expanduser().resolve()
        if args.ledger
        else (_authority_dir() / f"{workflow_id}.json").resolve()
    )
    try:
        result = launch_ledger.authorize_workflow(
            ledger,
            run_root,
            workflow_id=workflow_id,
            limit=args.limit,
            deadline_seconds=deadline_seconds,
            executable={"path": str(executable), "sha256": _file_sha256(executable)},
            output_root=output_root,
        )
    except launch_ledger.LedgerError as exc:
        print(f"authorize refused: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    output_root.mkdir(parents=True, exist_ok=True)
    (run_root / "authorization.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return EXIT_COMPLETE


CHECKPOINTS = {
    "after-clear": {"symbol": "_GfxClear", "draw": False},
    "after-draw": {"symbol": "_GfxDraw", "draw": True},
    "before-present": {"symbol": "_GfxPresent", "draw": False},
    "after-present": {"symbol": "_GfxPresent", "draw": True},
}


def _png_size(path: Path) -> tuple[int, int] | None:
    try:
        header = path.read_bytes()[:33]
    except OSError:
        return None
    if len(header) < 24 or not header.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def _window_list_helper() -> Path | None:
    """Compile (once) the window-server lister used for visible evidence."""
    source = ROOT / "benchmark" / "eu4_window_list.m"
    output = ROOT / "benchmark" / ".build" / "eu4_window_list"
    if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "clang", "-O2", "-Wall", "-Wextra",
                "-framework", "ApplicationServices",
                "-framework", "CoreFoundation",
                "-o", str(output), str(source),
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return output if result.returncode == 0 else None


def _image_stats(path: Path) -> dict:
    """Cheap evidence that a screenshot contains an image, not a blank frame."""
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return {"uniform": None, "reason": "PIL is not installed"}
    try:
        with Image.open(path) as image:
            small = image.convert("RGB").resize((64, 64))
            pixels = list(small.getdata())
    except (OSError, ValueError) as exc:
        return {"uniform": None, "reason": str(exc)}
    colors = {(r // 8, g // 8, b // 8) for r, g, b in pixels}
    mean = sum(sum(pixel) for pixel in pixels) / (len(pixels) * 3)
    variance = sum(
        (sum(pixel) / 3 - mean) ** 2 for pixel in pixels
    ) / len(pixels)
    return {
        "uniform": len(colors) <= 2,
        "distinct_color_buckets": len(colors),
        "mean_brightness": round(mean, 2),
        "variance": round(variance, 2),
        "width": small.width,
        "height": small.height,
    }


def similarity_to_drawable(
    shot: Path,
    drawable: Path,
    drawable_width: int,
    drawable_height: int,
    pixel_format: str = "BGRA8",
) -> dict:
    """Compare a visible screenshot with the presented drawable loosely.

    This is a visibility check, not the authoritative pixel comparison: it
    tolerates compositor scaling, colour management and occlusion. The exact
    comparison is the comparator's job on the drawable payload. Dimensions come
    from the capture descriptor, never from a fixed fixture.
    """
    try:
        from PIL import Image, ImageChops, ImageStat  # type: ignore
    except Exception:  # noqa: BLE001
        return {"status": "unavailable", "reason": "PIL is not installed"}
    if not drawable.is_file():
        return {"status": "unavailable", "reason": "drawable payload is absent"}
    if drawable_width <= 0 or drawable_height <= 0:
        return {"status": "unavailable", "reason": "drawable extent is absent"}
    payload = drawable.read_bytes()
    if len(payload) != drawable_width * drawable_height * 4:
        return {"status": "unavailable", "reason": "drawable payload size mismatch"}
    if pixel_format not in ("BGRA8", "RGBA8"):
        return {"status": "unavailable", "reason": "unsupported drawable format"}
    try:
        expected = Image.frombuffer(
            "RGBA",
            (drawable_width, drawable_height),
            payload,
            "raw",
            "BGRA" if pixel_format == "BGRA8" else "RGBA",
            0,
            1,
        ).convert("RGB")
        with Image.open(shot) as image:
            actual = image.convert("RGB")
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": str(exc)}
    width = 256
    height = max(1, round(width * drawable_height / drawable_width))
    size = (width, height)
    left = expected.resize(size)
    right = actual.resize(size)
    diff = ImageChops.difference(left, right)
    stat = ImageStat.Stat(diff)
    mean = sum(stat.mean) / 3.0
    maximum = max(stat.extrema[channel][1] for channel in range(3))
    return {
        "status": "compared",
        "mean_abs_error": round(mean, 3),
        "max_channel_error": maximum,
        "exact_size": list(actual.size),
    }


def _similarity_ok(similarity: dict, threshold: float) -> bool:
    return (
        similarity.get("status") == "compared"
        and float(similarity.get("mean_abs_error", 999.0)) <= threshold
    )


def _window_retry_should_stop(window: dict) -> bool:
    """A captured but over-threshold screenshot must not end discovery."""
    return window.get("status") == "captured" and window.get("qualified") is True


def _sck_helper() -> Path | None:
    source = ROOT / "benchmark" / "eu4_window_sck.swift"
    output = ROOT / "benchmark" / ".build" / "eu4_window_sck"
    if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
        return output
    cache = ROOT / ".local" / "swift-module-cache"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                "swiftc", "-parse-as-library", "-O",
                "-module-cache-path", str(cache),
                "-framework", "ScreenCaptureKit", "-framework", "AppKit",
                "-o", str(output), str(source),
            ],
            capture_output=True, text=True, check=False, timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return output if result.returncode == 0 else None


def _sck_capture(output: Path, pid: int, drawable: Path | None,
                 drawable_size: tuple[int, int] | None = None,
                 similarity_threshold: float = 18.0) -> dict:
    helper = _sck_helper()
    if helper is None:
        return {"status": "unavailable", "reason": "ScreenCaptureKit helper did not build"}
    shot = output / "window-sck.png"
    try:
        captured = subprocess.run(
            [str(helper), str(pid), str(shot)],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "reason": f"ScreenCaptureKit failed: {exc}"}
    size = _png_size(shot) if shot.is_file() else None
    stats = _image_stats(shot) if size else {"uniform": True}
    if captured.returncode != 0 or size is None or stats.get("uniform", True):
        return {
            "status": "blank",
            "reason": captured.stderr.strip() or "ScreenCaptureKit produced a blank image",
            "file": shot.name if shot.is_file() else None,
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "stats": stats,
        }
    block = {
        "status": "captured",
        "shown": True,
        "method": "screen_capture_kit",
        "file": shot.name,
        "width": size[0],
        "height": size[1],
        "sha256": _file_sha256(shot),
        "stats": stats,
        "source": "SCScreenshotManager(desktopIndependentWindow)",
    }
    if drawable is not None and drawable_size is not None:
        block["similarity"] = similarity_to_drawable(
            shot, drawable, drawable_size[0], drawable_size[1]
        )
        block["qualified"] = _similarity_ok(
            block["similarity"], similarity_threshold
        )
    else:
        block["qualified"] = drawable is None
    return block


def _window_capture(output: Path, pid: int, drawable: Path | None = None,
                    preferred_window: int | None = None,
                    drawable_size: tuple[int, int] | None = None,
                    similarity_threshold: float = 18.0) -> dict:
    """Independent visible-window capture of the game's own window.

    Enumerates every layer-0 window the game owns, tries the plausible main
    windows, and falls back to a full-screen capture. A candidate is accepted
    only if it is non-uniform and (when PIL is available) resembles the
    presented drawable. The chosen image and every attempt are recorded.
    """
    helper = _window_list_helper()
    if helper is None:
        return {"status": "unavailable", "reason": "window lister did not build"}
    try:
        listed = subprocess.run(
            [str(helper), str(pid)], capture_output=True, text=True,
            check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "reason": f"window listing failed: {exc}"}
    windows: list[dict] = []
    for line in listed.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("window_id"):
            windows.append(row)
    (output / "windows.json").write_text(json.dumps(windows, indent=2) + "\n")
    candidates = [
        window for window in windows
        if float(window.get("alpha", 0)) > 0.05
        and int(window.get("width", 0)) >= 400 and int(window.get("height", 0)) >= 400
    ]
    candidates.sort(
        key=lambda w: (
            int(w.get("window_id", 0)) != preferred_window,
            not w.get("onscreen"),
            -int(w["width"]) * int(w["height"]),
        )
    )
    if preferred_window is not None and not any(
        int(window.get("window_id", 0)) == preferred_window for window in candidates
    ):
        candidates.insert(0, {"window_id": preferred_window, "onscreen": False,
                              "alpha": 1.0, "width": 0, "height": 0})
    attempts: list[dict] = []
    accepted: dict | None = None
    sck = _sck_capture(output, pid, drawable, drawable_size, similarity_threshold)
    attempts.append(dict(sck, kind="screen_capture_kit"))
    if sck.get("status") == "captured":
        similarity = sck.get("similarity") or {}
        if sck.get("qualified"):
            return dict(sck, attempts=attempts, windows=windows,
                        preferred_window=preferred_window)
    for index, window in enumerate(candidates[:6]):
        path = output / f"window-{window['window_id']}.png"
        try:
            captured = subprocess.run(
                ["screencapture", "-x", "-o", "-l", str(window["window_id"]), str(path)],
                capture_output=True, text=True, check=False, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            attempts.append({"window_id": window["window_id"], "status": "error",
                             "reason": str(exc)})
            continue
        size = _png_size(path) if path.is_file() else None
        stats = _image_stats(path) if size else {"uniform": True}
        attempt = {
            "window_id": window["window_id"],
            "window": window,
            "file": path.name,
            "status": "captured" if size and not stats.get("uniform") else "blank",
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "stats": stats,
        }
        if size and drawable is not None and drawable_size is not None and not stats.get("uniform"):
            attempt["similarity"] = similarity_to_drawable(
                path, drawable, drawable_size[0], drawable_size[1]
            )
        attempts.append(attempt)
        if attempt["status"] == "captured":
            attempt["qualified"] = drawable is None or _similarity_ok(
                attempt.get("similarity") or {}, similarity_threshold
            )
            if attempt["qualified"] and accepted is None:
                accepted = attempt
        if accepted is not None:
            break
    if accepted is None:
        # Full-screen fallback. Try to bring the game forward first; failure is
        # recorded, not hidden.
        activation = {"status": "not_attempted"}
        try:
            ran = subprocess.run(
                [
                    "osascript", "-e",
                    "tell application \"System Events\" to set frontmost of "
                    f"first process whose unix id is {pid} to true",
                ],
                capture_output=True, text=True, check=False, timeout=20,
            )
            activation = {
                "status": "ok" if ran.returncode == 0 else "refused",
                "stderr": ran.stderr.strip(),
            }
        except (OSError, subprocess.SubprocessError) as exc:
            activation = {"status": "error", "reason": str(exc)}
        time.sleep(0.5)
        path = output / "window-fullscreen.png"
        try:
            captured = subprocess.run(
                ["screencapture", "-x", str(path)],
                capture_output=True, text=True, check=False, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "unavailable", "reason": f"screencapture failed: {exc}",
                    "attempts": attempts, "windows": windows}
        size = _png_size(path) if path.is_file() else None
        stats = _image_stats(path) if size else {"uniform": True}
        attempt = {
            "window_id": None,
            "file": path.name,
            "status": "captured" if size and not stats.get("uniform") else "blank",
            "width": size[0] if size else None,
            "height": size[1] if size else None,
            "stats": stats,
            "activation": activation,
            "kind": "fullscreen",
        }
        if size and drawable is not None and drawable_size is not None and not stats.get("uniform"):
            attempt["similarity"] = similarity_to_drawable(
                path, drawable, drawable_size[0], drawable_size[1]
            )
        attempts.append(attempt)
        if attempt["status"] == "captured":
            attempt["qualified"] = drawable is None or _similarity_ok(
                attempt.get("similarity") or {}, similarity_threshold
            )
            if attempt["qualified"]:
                accepted = attempt
    if accepted is None:
        captured = [
            attempt for attempt in attempts if attempt.get("status") == "captured"
        ]
        if captured:
            best = min(
                captured,
                key=lambda attempt: float(
                    (attempt.get("similarity") or {}).get("mean_abs_error", 999.0)
                ),
            )
            return {
                "status": "captured",
                "shown": True,
                "qualified": False,
                "reason": "captured screenshot did not meet the similarity threshold",
                "method": best.get("kind") or "window_server_screencapture",
                "window_id": best.get("window_id"),
                "file": best.get("file"),
                "width": best.get("width"),
                "height": best.get("height"),
                "sha256": _file_sha256(output / str(best.get("file"))),
                "source": "CGWindowList + screencapture",
                "stats": best.get("stats"),
                "similarity": best.get("similarity"),
                "attempts": attempts,
                "windows": windows,
            }
        return {
            "status": "missing",
            "reason": "no non-blank window or screen image was captured",
            "window_id": None,
            "attempts": attempts,
            "windows": windows,
        }
    shot = output / accepted["file"]
    return {
        "status": "captured",
        "shown": True,
        "qualified": True,
        "preferred_window": preferred_window,
        "method": accepted.get("kind") or "window_server_screencapture",
        "window_id": accepted.get("window_id"),
        "file": shot.name,
        "width": accepted["width"],
        "height": accepted["height"],
        "sha256": _file_sha256(shot),
        "source": "CGWindowList + screencapture",
        "stats": accepted.get("stats"),
        "similarity": accepted.get("similarity"),
        "attempts": attempts,
        "windows": windows,
    }


def _read_operations(output: Path) -> list[dict]:
    path = output / "operations.jsonl"
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    return rows


def _gl_vertex_block(output: Path, reference: dict) -> dict:
    """Extract the live slot-0 vertex payload that fed the selected draw.

    The payload is the engine's own _GfxCreateVertexBuffer /
    _GfxUpdateVertexBuffer argument, linked through the _GfxSetVertexBuffers
    resource ids observed in the same session. It is copied out with a digest
    so the comparator can reject unmatched inputs.
    """
    if not reference.get("ok"):
        return {"status": "missing", "ok": False, "reason": "no reference draw"}
    draw = reference.get("draw") or {}
    draw_call = draw.get("call_id")
    if not isinstance(draw_call, int):
        return {"status": "missing", "ok": False, "reason": "draw call id is absent"}
    live = reference.get("live_vertex")
    if isinstance(live, dict) and live.get("status") == "ok":
        raw_name = str(live.get("raw_file") or "")
        path = output / raw_name
        if not raw_name or not path.is_file():
            return {"status": "missing", "ok": False,
                    "reason": "live vertex payload file is absent"}
        data = path.read_bytes()
        if len(data) != live.get("bytes"):
            return {"status": "invalid", "ok": False,
                    "reason": "live vertex payload size mismatch"}
        if hashlib.sha256(data).hexdigest() != live.get("sha256"):
            return {"status": "invalid", "ok": False,
                    "reason": "live vertex payload sha256 mismatch"}
        stride = live.get("stride")
        count = live.get("count")
        if (
            not isinstance(stride, int)
            or not isinstance(count, int)
            or stride <= 0
            or count <= 0
            or len(data) != stride * count
        ):
            return {"status": "invalid", "ok": False,
                    "reason": "live vertex payload does not match its layout"}
        if count != draw.get("effective_count"):
            return {"status": "invalid", "ok": False,
                    "reason": "live vertex count differs from the draw count"}
        out_name = "gl-reference-vertex.bin"
        (output / out_name).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        block = {
            "schema": "gfx-gl-vertex/v1",
            "status": "ok",
            "ok": True,
            "reason": "",
            "source": "live_gl_vbo",
            "raw_file": out_name,
            "bytes": len(data),
            "sha256": digest,
            "hex": data.hex(),
            "stride": stride,
            "count": count,
            "offset": live.get("offset"),
            "first": live.get("first"),
            "buffer": live.get("buffer"),
            "buffer_size": live.get("buffer_size"),
            "call_id": draw_call,
            "symbol": "_GfxDraw",
            "source_record": {
                "file": "gl-reference.json",
                "field": "live_vertex",
                "raw_file": raw_name,
                "payload_sha256": digest,
            },
        }
        (output / "gl-reference-vertex.json").write_text(
            json.dumps(block, indent=2) + "\n"
        )
        return block
    rows = _read_operations(output)
    if not rows:
        return {"status": "missing", "ok": False, "reason": "operations.jsonl is absent"}

    def before(row: dict) -> bool:
        call = row.get("call_id")
        return isinstance(call, int) and call < draw_call

    binds = [
        row for row in rows
        if row.get("symbol") == "_GfxSetVertexBuffers" and before(row)
    ]
    if not binds:
        return {"status": "missing", "ok": False, "reason": "no vertex bind before the draw"}
    bind = max(binds, key=lambda row: row["call_id"])
    ids = bind.get("ids")
    if not isinstance(ids, list) or not ids:
        return {"status": "invalid", "ok": False, "reason": "bind record has no resource ids"}
    slot0 = ids[0]
    candidates = [
        row for row in rows
        if row.get("resource_id") == slot0
        and row.get("symbol") in ("_GfxCreateVertexBuffer", "_GfxUpdateVertexBuffer")
        and before(row)
        and (row.get("payload") or {}).get("file")
    ]
    if not candidates:
        return {
            "status": "missing",
            "ok": False,
            "reason": f"no payload for bound resource {slot0}",
            "resource_id": slot0,
        }
    source = max(candidates, key=lambda row: row["call_id"])
    payload = source.get("payload") or {}
    path = output / str(payload.get("file") or "")
    if not path.is_file():
        return {"status": "missing", "ok": False, "reason": "vertex payload file is absent"}
    data = path.read_bytes()
    if len(data) != payload.get("size"):
        return {"status": "invalid", "ok": False, "reason": "vertex payload size mismatch"}
    stride = source.get("stride")
    count = source.get("count")
    if not isinstance(stride, int) or not isinstance(count, int):
        return {"status": "invalid", "ok": False, "reason": "vertex layout is not recorded"}
    if stride <= 0 or count <= 0 or len(data) != stride * count:
        return {"status": "invalid", "ok": False, "reason": "vertex payload does not match its layout"}
    raw_name = "gl-reference-vertex.bin"
    (output / raw_name).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    block = {
        "schema": "gfx-gl-vertex/v1",
        "status": "ok",
        "ok": True,
        "reason": "",
        "raw_file": raw_name,
        "bytes": len(data),
        "sha256": digest,
        "hex": data.hex(),
        "stride": stride,
        "count": count,
        "resource_id": slot0,
        "call_id": source.get("call_id"),
        "symbol": source.get("symbol"),
        "source_record": {
            "file": "operations.jsonl",
            "call_id": source.get("call_id"),
            "symbol": source.get("symbol"),
            "payload_file": payload.get("file"),
            "payload_sha256": digest,
        },
    }
    (output / "gl-reference-vertex.json").write_text(json.dumps(block, indent=2) + "\n")
    return block


def _presentation_marker(output: Path) -> dict:
    marker = output / "presented.json"
    if not marker.is_file():
        return {}
    try:
        value = json.loads(marker.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _presentation_descriptor(output: Path, occurrence: int = 1) -> tuple[Path, dict]:
    """Resolve the requested after-present descriptor, marker first."""
    marker = _presentation_marker(output)
    name = marker.get("checkpoint_file")
    if not isinstance(name, str) or not name:
        name = f"checkpoint-after-present-{occurrence}.json"
    path = output / name
    if not path.is_file():
        path = output / f"checkpoint-after-present-{occurrence}.json"
    if not path.is_file():
        return path, {}
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return path, {}
    if not isinstance(data, dict):
        return path, {}
    if data.get("occurrence") not in (None, occurrence):
        return path, {}
    marker_occurrence = marker.get("occurrence", 1)
    if marker_occurrence not in (None, occurrence):
        return path, {}
    return path, data


def _presentation_block(output: Path, occurrence: int = 1,
                        similarity_threshold: float = 18.0) -> dict:
    path, data = _presentation_descriptor(output, occurrence)
    marker = output / "presented.json"
    if not path.is_file():
        return {
            "status": "missing",
            "presented": False,
            "reason": "no after-present checkpoint descriptor",
            "marker": marker.is_file(),
            "occurrence": occurrence,
        }
    if not data:
        return {
            "status": "invalid",
            "presented": False,
            "reason": "checkpoint descriptor is unreadable or for another occurrence",
            "marker": marker.is_file(),
            "occurrence": occurrence,
        }
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "presented": False, "reason": str(exc)}
    presentation = data.get("presentation") or {}
    capture = data.get("capture") or {}
    raw = output / str(capture.get("raw_file") or "")
    problems = []
    if presentation.get("shown") is not True:
        problems.append("presentation is not marked as shown")
    if presentation.get("drawable_readback") is not True:
        problems.append("no drawable readback proves the presented image")
    if not raw.is_file():
        problems.append("drawable payload is absent")
    elif raw.stat().st_size != capture.get("raw_bytes"):
        problems.append("drawable payload size does not match its declaration")
    elif _file_sha256(raw) != capture.get("raw_sha256"):
        problems.append("drawable payload sha256 does not match its declaration")
    gpu = data.get("gpu") or {}
    if gpu.get("completed") is not True:
        problems.append("present command did not complete")
    if not marker.is_file():
        problems.append("presented.json marker is absent")
    return {
        "status": "ok" if not problems else "invalid",
        "presented": not problems,
        "reason": "; ".join(problems),
        "checkpoint": str(path),
        "occurrence": occurrence,
        "raw_file": capture.get("raw_file"),
        "raw_sha256": capture.get("raw_sha256"),
        "extent": [
            (data.get("target") or {}).get("width"),
            (data.get("target") or {}).get("height"),
        ],
        "origin": (data.get("target") or {}).get("origin"),
        "presentation": presentation,
        "draw": data.get("draw"),
        "marker": marker.is_file(),
        "window_number": presentation.get("window_number"),
    }


def _logical_capture_block(output: Path, identity: dict) -> dict:
    path = output / "metal-logical.json"
    if not path.is_file():
        return {"status": "missing", "ok": False, "reason": "no logical capture"}
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "ok": False, "reason": str(exc)}
    problems = []
    if data.get("schema") != "gfx-metal-logical/v1":
        problems.append("unexpected schema")
    provenance = data.get("provenance") or {}
    if provenance.get("executable_sha256") != identity.get("executable_sha256"):
        problems.append("executable identity does not match the pinned launch")
    capture = data.get("capture") or {}
    raw = output / str(capture.get("raw_file") or "")
    target = data.get("target") or {}
    if not raw.is_file():
        problems.append("payload is absent")
    else:
        if raw.stat().st_size != capture.get("raw_bytes"):
            problems.append("payload size does not match its declaration")
        elif _file_sha256(raw) != capture.get("raw_sha256"):
            problems.append("payload sha256 does not match its declaration")
        expected = (target.get("width") or 0) * (target.get("height") or 0) * 4
        if capture.get("raw_bytes") != expected:
            problems.append("payload is not width*height*4")
    if (data.get("gpu") or {}).get("completed") is not True:
        problems.append("GPU command completion is not recorded")
    if target.get("origin") != "top_left":
        problems.append("image origin is not declared as top_left")
    return {
        "status": "ok" if not problems else "invalid",
        "ok": not problems,
        "reason": "; ".join(problems),
        "path": str(path),
        "raw_file": capture.get("raw_file"),
        "raw_sha256": capture.get("raw_sha256"),
        "target": target,
        "draw": data.get("draw"),
    }


def _write_png(path: Path, bgra: bytes, width: int, height: int) -> None:
    """Minimal PNG preview (standard library only). BGRA8 -> RGBA8."""
    stride = width * 4
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: none
        row = bgra[y * stride:(y + 1) * stride]
        for x in range(0, len(row), 4):
            rows.extend((row[x + 2], row[x + 1], row[x], row[x + 3]))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def _checkpoint_files(output: Path) -> list[Path]:
    return sorted(output.glob("checkpoint-*.json"))


def _inspect(output: Path) -> dict:
    result_path = output / "probe-result.json"
    if result_path.is_file():
        return json.loads(result_path.read_text())
    checkpoints = []
    for path in _checkpoint_files(output):
        try:
            checkpoints.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    route = None
    route_path = output / "draw-route.json"
    if route_path.is_file():
        route = json.loads(route_path.read_text())
    stop = None
    stop_path = output / "stop.json"
    if stop_path.is_file():
        stop = json.loads(stop_path.read_text())
    return {
        "schema": "gfx-experiment-inspection/v1",
        "run": str(output),
        "checkpoints": checkpoints,
        "route": route,
        "stop": stop,
        "artifacts": [str(path) for path in sorted(output.iterdir()) if path.is_file()],
    }


def _read_status(output: Path, occurrence: int | None = None) -> dict:
    """Aggregate the explicit success levels from real artifact files."""
    checkpoint = None
    files = _checkpoint_files(output)
    if occurrence is not None:
        exact = output / f"checkpoint-after-present-{occurrence}.json"
        if exact.is_file():
            files = [exact] + [path for path in files if path != exact]
    for path in files:
        try:
            value = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            checkpoint = value
            break
    route = None
    route_path = output / "draw-route.json"
    if route_path.is_file():
        try:
            route = json.loads(route_path.read_text())
        except json.JSONDecodeError:
            route = None
    stop = None
    stop_path = output / "stop.json"
    if stop_path.is_file():
        try:
            stop = json.loads(stop_path.read_text())
        except json.JSONDecodeError:
            stop = None
    unsupported = output / "draw-unsupported.json"
    failed = output / "draw-failed.json"
    clear = output / "clear-unsupported.json"
    clear_diagnostic = None
    if clear.is_file():
        try:
            clear_diagnostic = json.loads(clear.read_text())
        except json.JSONDecodeError:
            clear_diagnostic = None

    draw_reached = bool(route) or (
        isinstance(stop, dict) and stop.get("symbol") == "_GfxDraw"
    ) or bool(checkpoint and checkpoint.get("status", {}).get("draw_reached"))
    draw_encoded = bool(route and route.get("route") == "live") or bool(
        checkpoint and checkpoint.get("status", {}).get("draw_encoded")
    )
    gpu_completed = bool(
        checkpoint and checkpoint.get("status", {}).get("gpu_completed")
    )
    pixels_captured = bool(
        checkpoint
        and checkpoint.get("status", {}).get("pixels_captured")
        and (output / str(checkpoint.get("capture", {}).get("raw_file", ""))).is_file()
    )
    presented = (output / "present.json").is_file() or bool(
        checkpoint and checkpoint.get("status", {}).get("presented")
    )
    return {
        "draw_reached": draw_reached,
        "draw_encoded": draw_encoded,
        "gpu_completed": gpu_completed,
        "pixels_captured": pixels_captured,
        "presented": presented,
        "reference_matched": None,
        "unsupported": unsupported.is_file() or clear.is_file(),
        "failed": failed.is_file(),
        "checkpoint": checkpoint,
        "route": route,
        "clear": clear_diagnostic,
        "stop": stop,
    }


def probe(args: argparse.Namespace) -> int:
    """Bounded Metal live observation with one selected GPU checkpoint."""
    if args.backend != "metal":
        print("probe currently supports --backend metal only", file=sys.stderr)
        return EXIT_INCOMPLETE
    if not 1 <= args.occurrence <= 0x7FFFFFFF:
        print(
            f"--occurrence must be between 1 and {0x7FFFFFFF}: {args.occurrence}",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    if args.checkpoint not in CHECKPOINTS:
        print(f"unknown checkpoint: {args.checkpoint}", file=sys.stderr)
        return EXIT_INCOMPLETE
    try:
        timeout = launch_ledger.validate_timeout(args.timeout, field="--timeout")
    except launch_ledger.LedgerError as exc:
        print(f"probe refused: {exc}", file=sys.stderr)
        return EXIT_INCOMPLETE
    if not args.campaign_run_root:
        print(
            "probe requires --campaign-run-root; the unrestricted manual "
            "--authorize-live switch is not a substitute for operator authorization",
            file=sys.stderr,
        )
        return EXIT_INCOMPLETE
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        print(f"probe output already exists: {output}", file=sys.stderr)
        return EXIT_INCOMPLETE
    # The generic probe has no Simple-specific caller default. An explicit
    # --caller-rva filters; otherwise the first qualifying invocation wins.
    caller_rva = args.caller_rva or 0
    selector = CHECKPOINTS[args.checkpoint]
    try:
        import eu4_benchmark as bench_base
        import eu4_metal_boot
    except Exception as exc:  # noqa: BLE001
        print(f"probe unavailable: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    try:
        identity = bench_base.gog_identity()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"probe preflight failed: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE
    try:
        token = _claim_launch(
            Path(args.campaign_run_root),
            output,
            [selector["symbol"]],
            requested_timeout=timeout,
            executable_sha256=identity["executable_sha256"],
            executable_path=identity["executable"],
            require_pinned=True,
        )
    except LaunchLimit as exc:
        print(f"campaign launch refused: {exc}", file=sys.stderr)
        return EXIT_LIMIT
    session = f"{int(time.time())}-{os.getpid()}"
    env = {
        "EU4_GFX_CHECKPOINT": args.checkpoint,
        "EU4_GFX_CHECKPOINT_DIR": str(output),
        "EU4_GFX_CHECKPOINT_CALLER_RVA": f"0x{caller_rva:x}" if caller_rva else "0",
        "EU4_GFX_CHECKPOINT_OCCURRENCE": str(args.occurrence),
        "EU4_GFX_STARTUP_ID": session,
        "EU4_GFX_EXPECTED_EXE": identity["executable"],
        "EU4_GFX_EXECUTABLE_SHA256": identity["executable_sha256"],
        "EU4_GFX_QUALIFIED_SOURCES": str(
            Path(args.qualified_sources).expanduser().resolve()
        ),
    }
    if args.skip_unsupported:
        env["EU4_GFX_DRAW_SKIP_UNSUPPORTED"] = "1"
    if args.texture_diagnostics:
        env["EU4_GFX_TEXTURE_TRACE"] = "1"
        env["EU4_GFX_TEXTURE_PLACEHOLDER"] = "1"
    if args.uniform_trace:
        env["EU4_GFX_UNIFORM_TRACE"] = "1"
    if args.swap_dump:
        env["EU4_GFX_SWAP_DUMP"] = str(Path(output) / "swap-frame.bgra")
    if args.texture_bind_trace:
        env["EU4_GFX_TEXTURE_BIND_TRACE"] = "1"
    if args.uniform_dump:
        env["EU4_GFX_UNIFORM_DUMP"] = str(Path(output))
    if args.pipeline_stats:
        env["EU4_GFX_PIPELINE_STATS"] = "1"
    if args.order_trace:
        env["EU4_GFX_ORDER_TRACE"] = "1"
    if args.no_cull:
        env["EU4_GFX_NO_CULL"] = "1"
    if args.no_blend:
        env["EU4_GFX_NO_BLEND"] = "1"
    if args.element_trace:
        env["EU4_GFX_ELEMENT_TRACE"] = "1"
    if args.depth_trace:
        env["EU4_GFX_DEPTH_TRACE"] = "1"
    if args.sampler_trace:
        env["EU4_GFX_SAMPLER_TRACE"] = "1"
    if args.vertex_trace:
        env["EU4_GFX_VERTEX_TRACE"] = "1"
        if args.vertex_trace_stage:
            env["EU4_GFX_VERTEX_TRACE_STAGE"] = args.vertex_trace_stage
    if args.sample_texture_dump:
        env["EU4_GFX_SAMPLE_TEXTURE_DUMP"] = str(Path(output))
        if args.sample_texture_stage:
            env["EU4_GFX_SAMPLE_TEXTURE_STAGE"] = args.sample_texture_stage
    if args.texture_type_trace:
        env["EU4_GFX_TEXTURE_TYPE_TRACE"] = "1"
    if args.metal_validation:
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "nslog"
    if args.resolve_trace:
        env["EU4_GFX_RESOLVE_TRACE"] = "1"
        env["EU4_GFX_RESOLVE_DUMP"] = str(Path(output))
    if args.composite_dump:
        env["EU4_GFX_COMPOSITE_DUMP"] = str(Path(output))
        env["EU4_GFX_COMPOSITE_STAGE"] = "11a05cd9fd8f7533"
    if args.min_uptime_ms:
        env["EU4_GFX_CHECKPOINT_MIN_UPTIME_MS"] = str(int(args.min_uptime_ms))
    if args.dump_targets:
        env["EU4_GFX_DUMP_TARGETS"] = str(Path(output))
    if args.shader_cache:
        env["EU4_GFX_SHADER_CACHE"] = str(
            Path(args.shader_cache).expanduser().resolve()
        )
    if args.draw_sequence:
        env["EU4_GFX_DRAW_SEQUENCE"] = "1"
    if args.logical_prefix:
        env["EU4_GFX_LOGICAL_CAPTURE"] = "prefix"
        if caller_rva:
            env["EU4_GFX_LOGICAL_STOP_CALLER"] = f"0x{caller_rva:x}"
    elif args.checkpoint == "after-present":
        env["EU4_GFX_LOGICAL_CAPTURE"] = "1"
        env["EU4_GFX_DRAW_SKIP_UNSUPPORTED"] = "1"
        env["EU4_GFX_CHECKPOINT_HOLD_MS"] = str(args.hold_ms)
    window_evidence: dict = {}
    requested_occurrence = args.occurrence
    similarity_threshold = args.similarity_threshold
    if similarity_threshold is None:
        similarity_threshold = 18.0
        if args.checkpoint == "after-present":
            policy_path = ROOT / "analysis" / "m4" / "comparison-policy.json"
            try:
                similarity_threshold = float(
                    json.loads(policy_path.read_text()).get(
                        "presentation_similarity_max_mean_error", 18.0
                    )
                )
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
                similarity_threshold = 18.0

    def while_running(run_dir: Path, game) -> bool:
        if window_evidence:
            return True
        descriptor, data = _presentation_descriptor(run_dir, requested_occurrence)
        if not data:
            return False
        marker = _presentation_marker(run_dir)
        preferred = marker.get("window_number")
        target = data.get("target") or {}
        width = target.get("width")
        height = target.get("height")
        raw = run_dir / str((data.get("capture") or {}).get("raw_file") or "")
        # A fullscreen Space transition and the Core Animation commit can take
        # a few seconds; a capture during the transition is a uniform gray
        # placeholder. Try several times and keep the first informative image.
        window = None
        for delay in (2.0, 2.0, 3.0):
            time.sleep(delay)
            window = _window_capture(
                run_dir,
                game.pid,
                drawable=raw,
                preferred_window=preferred if isinstance(preferred, int) and preferred > 0 else None,
                drawable_size=(width, height) if isinstance(width, int) and isinstance(height, int) else None,
                similarity_threshold=similarity_threshold,
            )
            if _window_retry_should_stop(window):
                break
        window_evidence["window"] = window
        return True

    metadata = None
    error = None
    try:
        library = eu4_metal_boot.build_library("x86_64")
        env["EU4_GFX_LIBRARY_SHA256"] = bench_base.sha256(library)
        output.parent.mkdir(parents=True, exist_ok=True)
        metadata = eu4_metal_boot.launch_session(
            output,
            wait_seconds=timeout,
            metal_init=True,
            env_overrides=env,
            strict=False,
            while_running=while_running if args.checkpoint == "after-present" else None,
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    status = (
        _read_status(output, occurrence=args.occurrence if args.checkpoint == "after-present" else None)
        if output.is_dir()
        else {}
    )
    # The launcher creates the directory; it may be absent only on an early
    # build/preflight failure, where the launch is still accounted for.
    if output.is_dir():
        checkpoint = status.get("checkpoint")
        if checkpoint and status.get("pixels_captured"):
            raw = output / checkpoint["capture"]["raw_file"]
            png = raw.with_suffix(".png")
            try:
                _write_png(png, raw.read_bytes(), checkpoint["target"]["width"],
                           checkpoint["target"]["height"])
            except (OSError, ValueError) as exc:
                png = None
                error = error or f"PNG preview failed: {exc}"
    presentation = {"status": "not_requested", "presented": None}
    logical_capture = {"status": "not_requested", "ok": None}
    if args.checkpoint == "after-present":
        presentation = _presentation_block(
            output, args.occurrence, similarity_threshold
        )
        window = window_evidence.get("window") or {
            "status": "missing",
            "reason": "the live window capture did not run",
        }
        presentation["window"] = window
        similarity = window.get("similarity") or {}
        looks_right = _similarity_ok(similarity, similarity_threshold)
        presentation["visible"] = bool(
            presentation.get("presented")
            and window.get("status") == "captured"
            and window.get("shown")
            and looks_right
        )
        if window.get("status") == "captured" and not looks_right:
            presentation["reason"] = (
                "screenshot comparison "
                + ("unavailable" if similarity.get("status") != "compared"
                   else f"exceeds threshold: {similarity.get('mean_abs_error')}")
            )
        logical_capture = _logical_capture_block(output, identity)
        (output / "presentation-evidence.json").write_text(
            json.dumps(
                {
                    "schema": "gfx-presentation-evidence/v1",
                    "presentation": presentation,
                    "logical_capture": logical_capture,
                    "provenance": {
                        "executable": identity["executable"],
                        "executable_sha256": identity["executable_sha256"],
                        "library_sha256": env.get("EU4_GFX_LIBRARY_SHA256"),
                        "session": session,
                    },
                },
                indent=2,
            )
            + "\n"
        )
    reference = {"status": "not_provided"}
    if args.reference:
        reference = _compare_reference(
            Path(args.reference).expanduser().resolve(),
            status.get("checkpoint"),
            output,
        )
        reference["kind"] = "operator_provided_png"
    file_hashes = []
    if output.is_dir():
        for path in sorted(output.iterdir()):
            if path.is_file() and path.suffix in (".json", ".bgra", ".png"):
                try:
                    file_hashes.append(
                        {"path": path.name, "sha256": _file_sha256(path)}
                    )
                except OSError:
                    continue
    overall = "unavailable"
    if args.checkpoint == "after-present":
        if presentation.get("visible") and logical_capture.get("ok"):
            overall = "presented"
        elif presentation.get("presented"):
            overall = "presented_unverified"
        elif status.get("pixels_captured"):
            overall = "captured"
        elif status.get("draw_reached"):
            overall = "draw_reached"
        elif status.get("unsupported"):
            overall = "unsupported"
        elif error:
            overall = "failed"
    elif status.get("pixels_captured"):
        overall = "captured"
    elif status.get("draw_reached"):
        overall = "draw_reached"
    elif status.get("unsupported"):
        overall = "unsupported"
    elif error:
        overall = "failed"
    result = {
        "schema": "gfx-experiment/v1",
        "command": "probe",
        "backend": "metal",
        "checkpoint": args.checkpoint,
        "caller_rva": f"0x{caller_rva:x}" if caller_rva else None,
        "occurrence": args.occurrence,
        "overall": overall,
        "status": {
            key: status.get(key)
            for key in (
                "draw_reached",
                "draw_encoded",
                "gpu_completed",
                "pixels_captured",
                "presented",
                "reference_matched",
            )
        },
        "provenance": {
            "executable": identity["executable"],
            "executable_sha256": identity["executable_sha256"],
            "library_sha256": env.get("EU4_GFX_LIBRARY_SHA256"),
            "session": session,
            "pid": None if not metadata else metadata.get("pid"),
            "game_pid": None if not status.get("checkpoint") else status["checkpoint"].get("caller", {}).get("pid"),
        },
        "launch": {
            "mode": "campaign",
            "token": token,
            "run_root": str(Path(args.campaign_run_root).resolve()),
        },
        "artifacts": {
            "checkpoint": status.get("checkpoint"),
            "route": status.get("route"),
            "clear": status.get("clear"),
            "stop": status.get("stop"),
            "files": file_hashes,
        },
        "reference": reference,
        "presentation": presentation,
        "logical_capture": logical_capture,
        "error": error,
        "metadata": metadata,
    }
    if output.is_dir():
        (output / "probe-result.json").write_text(json.dumps(result, indent=2) + "\n")
    _finish_launch(
        Path(args.campaign_run_root),
        token,
        pid=None if not metadata else metadata.get("pid"),
        status="completed" if status.get("pixels_captured") else f"failed: {overall}",
    )
    print(json.dumps(result, indent=2))
    if args.checkpoint == "after-present":
        if overall == "presented":
            return EXIT_COMPLETE
        if overall in ("presented_unverified", "captured", "draw_reached"):
            return EXIT_INCOMPLETE
        if overall == "unsupported":
            return EXIT_LIMIT
        return EXIT_UNAVAILABLE
    if status.get("pixels_captured"):
        return EXIT_COMPLETE
    if status.get("unsupported"):
        return EXIT_LIMIT
    return EXIT_UNAVAILABLE


def _compare_reference(reference: Path, checkpoint: dict | None, output: Path) -> dict:
    if checkpoint is None:
        return {
            "status": "unavailable",
            "comparable": False,
            "pixels_identical": None,
            "authenticated_opengl": False,
            "reason": "no checkpoint image",
        }
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        return {
            "status": "unavailable",
            "comparable": False,
            "pixels_identical": None,
            "authenticated_opengl": False,
            "reason": "PIL is not installed; existing reference comparison cannot decode PNG",
        }
    raw_path = output / checkpoint["capture"]["raw_file"]
    width = checkpoint["target"]["width"]
    height = checkpoint["target"]["height"]
    try:
        actual = Image.frombytes("RGBA", (width, height), _bgra_to_rgba(raw_path.read_bytes()))
        expected = Image.open(reference).convert("RGBA")
    except (OSError, ValueError) as exc:
        return {
            "status": "unavailable",
            "comparable": False,
            "pixels_identical": None,
            "authenticated_opengl": False,
            "reason": str(exc),
        }
    if expected.size != actual.size:
        return {
            "status": "incomparable",
            "comparable": False,
            "pixels_identical": None,
            "authenticated_opengl": False,
            "reason": f"reference is {expected.size[0]}x{expected.size[1]}, capture is {width}x{height}",
            "reference": str(reference),
        }
    mismatched = 0
    maximum = 0
    diff_pixels = []
    for got, want in zip(actual.getdata(), expected.getdata()):
        delta = max(abs(got[i] - want[i]) for i in range(4))
        if delta:
            mismatched += 1
            maximum = max(maximum, delta)
        rgb = [min(255, abs(got[i] - want[i]) * 4) for i in range(3)]
        alpha_delta = min(255, abs(got[3] - want[3]) * 4)
        if rgb == [0, 0, 0] and alpha_delta:
            # An alpha-only difference must be visible in the preview.
            rgb = [255, 0, 255]
        diff_pixels.append((rgb[0], rgb[1], rgb[2], 255))
    diff = Image.new("RGBA", (width, height))
    diff.putdata(diff_pixels)
    diff_path = output / "difference.png"
    diff.save(diff_path)
    return {
        "status": "compared",
        "comparable": True,
        "pixels_identical": mismatched == 0,
        "authenticated_opengl": False,
        "reference": str(reference),
        "mismatched_pixels": mismatched,
        "maximum_channel_error": maximum,
        "difference_image": str(diff_path),
    }


def _bgra_to_rgba(payload: bytes) -> bytes:
    out = bytearray(len(payload))
    for i in range(0, len(payload), 4):
        out[i] = payload[i + 2]
        out[i + 1] = payload[i + 1]
        out[i + 2] = payload[i]
        out[i + 3] = payload[i + 3]
    return bytes(out)


def inspect(args: argparse.Namespace) -> int:
    output = Path(args.run).expanduser().resolve()
    if not output.is_dir():
        print(f"experiment output is absent: {output}", file=sys.stderr)
        return EXIT_INCOMPLETE
    print(json.dumps(_inspect(output), indent=2))
    return EXIT_COMPLETE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", description="bounded live original capture")
    cap.add_argument("--target", action="append", required=True)
    cap.add_argument("--max-events", type=int)
    cap.add_argument("--duration", type=float)
    cap.add_argument("--until-target-draw", action="store_true")
    cap.add_argument("--caller-rva", type=_rva_value)
    cap.add_argument("--gl-reference", action="store_true")
    cap.add_argument("--timeout", type=float, default=120.0)
    cap.add_argument("--output", required=True)
    cap.add_argument("--authorize-live", action="store_true")
    cap.add_argument("--campaign-run-root")
    cap.set_defaults(func=capture)

    rep = sub.add_parser("replay", description="offline Metal differential replay")
    rep.add_argument("--capture", required=True)
    rep.add_argument("--output")
    rep.add_argument("--skip-unknown", action="store_true")
    rep.set_defaults(func=replay)

    pkt = sub.add_parser(
        "packet", description="build the L3.2a draw packet from one capture"
    )
    pkt.add_argument("--capture", required=True)
    pkt.add_argument("--output", required=True)
    pkt.add_argument("--caller-rva", type=_rva_value, default=0xEDFCD1)
    pkt.set_defaults(func=packet)

    prb = sub.add_parser(
        "probe", description="bounded live Metal GPU checkpoint (operator-authorized)"
    )
    prb.add_argument("--backend", default="metal")
    prb.add_argument("--checkpoint", required=True, choices=sorted(CHECKPOINTS))
    prb.add_argument("--caller-rva", type=_rva_value)
    prb.add_argument("--occurrence", type=int, default=1)
    prb.add_argument("--hold-ms", type=int, default=8000)
    prb.add_argument(
        "--draw-sequence",
        action="store_true",
        help="write a bounded diagnostic sequence of every _GfxDraw-family call",
    )
    prb.add_argument(
        "--logical-prefix",
        action="store_true",
        help="encode every handled draw into one persistent diagnostic logical target",
    )
    prb.add_argument("--similarity-threshold", type=float, default=None)
    prb.add_argument("--timeout", type=float, default=120.0)
    prb.add_argument("--output", required=True)
    prb.add_argument("--campaign-run-root")
    prb.add_argument("--reference")
    prb.add_argument("--shader-cache")
    prb.add_argument("--skip-unsupported", action="store_true")
    prb.add_argument("--texture-diagnostics", action="store_true")
    prb.add_argument("--uniform-trace", action="store_true")
    prb.add_argument("--swap-dump", action="store_true")
    prb.add_argument("--dump-targets", action="store_true")
    prb.add_argument("--min-uptime-ms", type=int, default=0)
    prb.add_argument("--texture-bind-trace", action="store_true")
    prb.add_argument("--uniform-dump", action="store_true")
    prb.add_argument("--composite-dump", action="store_true")
    prb.add_argument("--resolve-trace", action="store_true")
    prb.add_argument("--order-trace", action="store_true")
    prb.add_argument("--pipeline-stats", action="store_true")
    prb.add_argument("--metal-validation", action="store_true")
    prb.add_argument("--texture-type-trace", action="store_true")
    prb.add_argument("--sample-texture-dump", action="store_true")
    prb.add_argument("--element-trace", action="store_true")
    prb.add_argument("--depth-trace", action="store_true")
    prb.add_argument("--sampler-trace", action="store_true")
    prb.add_argument("--vertex-trace", action="store_true")
    prb.add_argument("--vertex-trace-stage", default="")
    prb.add_argument("--no-cull", action="store_true")
    prb.add_argument("--no-blend", action="store_true")
    prb.add_argument("--sample-texture-stage", default="")
    prb.add_argument(
        "--qualified-sources",
        default=str(ROOT / "analysis" / "gfx-shader" / "v0" / "qualified-sources.json"),
    )
    prb.set_defaults(func=probe)

    ins = sub.add_parser("inspect", description="inspect one experiment's artifacts")
    ins.add_argument("--run", required=True)
    ins.set_defaults(func=inspect)

    aut = sub.add_parser(
        "authorize",
        description="operator: pin the executable and grant a bounded launch budget",
    )
    aut.add_argument("--run-root", required=True)
    aut.add_argument("--output-root", required=True)
    aut.add_argument("--executable")
    aut.add_argument("--limit", type=int, required=True)
    aut.add_argument("--deadline-seconds", type=float)
    aut.add_argument("--workflow-id")
    aut.add_argument("--ledger")
    aut.set_defaults(func=authorize)

    nxt = sub.add_parser(
        "next-blocker", description="classify the next actionable Metal-only blocker"
    )
    nxt.add_argument("--run", required=True)
    nxt.add_argument("--json", action="store_true")
    nxt.set_defaults(func=next_blocker)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
