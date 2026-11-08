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
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_op_capture as capture_reader  # noqa: E402
import eu4_gfx_startup_trace as startup  # noqa: E402

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
) -> str:
    run_root = run_root.expanduser().resolve()
    if not run_root.is_dir():
        raise LaunchLimit("missing", f"campaign run root does not exist: {run_root}")
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


def _finish_launch(
    run_root: Path, token: str, *, pid: int | None, status: str
) -> None:
    run_root = run_root.expanduser().resolve()
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


def _capture_status(report: dict) -> tuple[str, str]:
    status = report.get("status")
    if not isinstance(status, dict) or status.get("state") != "installed":
        return "unavailable", "startup attestation was not installed"
    operations = report.get("operations")
    if not isinstance(operations, dict):
        return "unavailable", "operations summary was not written"
    if operations.get("reason"):
        return "incomplete", str(operations["reason"])
    if operations.get("events") == 0:
        return "empty", "no matching operation was observed"
    return "complete", ""


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
    try:
        evidence = startup.preflight_ops(
            args.target,
            max_events=args.max_events,
            duration_s=args.duration,
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
            )
        except LaunchLimit as exc:
            print(f"campaign launch refused: {exc}", file=sys.stderr)
            return EXIT_LIMIT

    try:
        report = startup.run_ops(
            targets=args.target,
            max_events=args.max_events,
            duration_s=args.duration,
            timeout_s=args.timeout,
            output=output,
            authorize_live=True,
            evidence=evidence,
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

    status, reason = _capture_status(report)
    coverage: dict = {}
    validation: dict = {}
    if status == "complete":
        try:
            artifact = capture_reader.load_capture(output)
        except capture_reader.CaptureIncomplete as exc:
            status = "incomplete" if exc.reason != "op_no_events" else "empty"
            reason = exc.reason
            validation = {"reason": exc.reason, "detail": exc.detail}
        else:
            coverage = artifact.coverage
            validation = {
                "ok": True,
                "source_hashes": artifact.source_hashes,
                "scope": "function_cpu_state",
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
        "launch": {
            "mode": "campaign" if token is not None else "manual",
        },
    }
    (output / "capture-report.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result, indent=2))
    return EXIT_COMPLETE if status == "complete" else EXIT_INCOMPLETE


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", description="bounded live original capture")
    cap.add_argument("--target", action="append", required=True)
    cap.add_argument("--max-events", type=int, default=256)
    cap.add_argument("--duration", type=float, default=10.0)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
