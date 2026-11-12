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


def _read_status(output: Path) -> dict:
    """Aggregate the explicit success levels from real artifact files."""
    checkpoint = None
    for path in _checkpoint_files(output):
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
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
    status = _read_status(output) if output.is_dir() else {}
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
    if status.get("pixels_captured"):
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
    prb.add_argument("--timeout", type=float, default=120.0)
    prb.add_argument("--output", required=True)
    prb.add_argument("--campaign-run-root")
    prb.add_argument("--reference")
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
