"""Launch entry for one `_GfxSetConstantBuffer` trace.

Verifies the pin recorded in read-only ``reverse_engineering/target.json``,
refuses while operator authorization is absent, and with ``--authorize-live``
launches the pinned GOG EU IV process with the TR3 startup dylib loaded, waits
for the one-shot record, and hands that run to the EU4 observation builder.
``image_base`` is the preferred image base, not the process load address.
Tests may call the offline refusal and mocked launch paths. They do not start EU4.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from benchmark import eu4_benchmark as base
from reconstruction_trace.tr3 import gfx, image, startup

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "reverse_engineering" / "target.json"
BUILD_DIR = Path.home() / ".cache" / "eu4-mac-perf" / "tr3-build"
EXECUTION_BOUND_S = 120
STOP_WAIT_S = 30

PIN_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PIN_ENTRY_RVA = "0x15ec36a"
PIN_END_RVA = "0x15ec3b5"
PIN_WINDOW_SHA256 = "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"
PIN_IMAGE_BASE = 0x100000000
WINDOW_LEN = int(PIN_END_RVA, 16) - int(PIN_ENTRY_RVA, 16)
UNAUTHORIZED = "refused: operator authorization is absent"
CAPTURED = ("captured_complete", "captured_incomplete")
_DROP_ENV = ("TR3_LEGACY_REGIONS", "TR3_CHECK_SYMBOL", "TR3_SYMBOL",
             "TR3_MAX_INSTRUCTIONS", "TR3_MAX_ACCESSES", "TR3_MAX_BYTES")


def decide(target: dict) -> str:
    """Refuse a pin mismatch, and otherwise refuse because the run is unauthorized."""
    window = target["code_windows"]["_GfxSetConstantBuffer"]
    pinned = (target["sha256"] == PIN_SHA256
              and window["entry_rva"] == PIN_ENTRY_RVA
              and window["end_rva"] == PIN_END_RVA
              and window["sha256"] == PIN_WINDOW_SHA256)
    if not pinned:
        return "refused: binary pin mismatch"
    return UNAUTHORIZED


def _refuse_pin(target: dict, window: dict) -> str | None:
    """Metadata pin for a direct ``launch_live`` caller. This does not hash the file."""
    decision = decide(target)
    # UNAUTHORIZED is decide()'s pin-match signal, not a live-authorization failure here.
    if decision != UNAUTHORIZED:
        return decision
    pinned = target["code_windows"]["_GfxSetConstantBuffer"]
    if target.get("image_base") != PIN_IMAGE_BASE or window != pinned:
        return "refused: binary pin mismatch"
    return None


def live_descriptor(target: dict) -> dict:
    """One ``trace-target/v1`` descriptor from the pin and ``startup.CEILING``."""
    desc = {
        "schema": "trace-target/v1",
        "binary": {
            "sha256": PIN_SHA256,
            "image_base": hex(target["image_base"]),
            "window_sha256": PIN_WINDOW_SHA256,
        },
        "function": {
            "name": "_GfxSetConstantBuffer",
            "entry_rva": PIN_ENTRY_RVA,
            "ranges": [[PIN_ENTRY_RVA, PIN_END_RVA]],
        },
        "trigger": {"mode": "first-invocation"},
        "capture": dict(startup.CEILING),
    }
    startup.check_descriptor(desc, PIN_IMAGE_BASE)
    minimum = startup.envelope(WINDOW_LEN)
    if desc["capture"]["max_bytes"] < minimum:
        raise RuntimeError(f"max_bytes below mandatory envelope of {minimum}")
    return desc


def _live_env(dylib: Path, log_path: Path, session: str, descriptor: dict) -> dict:
    """startup.target_env forces TR3_CHECK_SYMBOL. EU4 is pinned by file and RVA."""
    env = os.environ.copy()
    for name in _DROP_ENV:
        env.pop(name, None)
    function, binary, capture = descriptor["function"], descriptor["binary"], descriptor["capture"]
    end = int(function["ranges"][0][1], 16)
    entry = int(function["entry_rva"], 16)
    env["DYLD_INSERT_LIBRARIES"] = str(dylib)
    env["TR3_EXPECT_FILE_SHA256"] = binary["sha256"]
    env["TR3_EXPECT_WINDOW_SHA256"] = binary["window_sha256"]
    env["TR3_ENTRY_RVA"] = function["entry_rva"]
    env["TR3_WINDOW_LEN"] = str(end - entry)
    env["TR3_LOG"] = str(log_path)
    env["TR3_SESSION"] = session
    env["TR3_MAX_INSTRUCTIONS"] = str(capture["max_instructions"])
    env["TR3_MAX_ACCESSES"] = str(capture["max_memory_accesses"])
    env["TR3_MAX_BYTES"] = str(capture["max_bytes"])
    return env


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--authorize-live", action="store_true",
                        help="authorize one bounded launch of the pinned EU IV process")
    parser.add_argument("--output", type=Path, required=True,
                        help="path of the live acquisition result")
    return parser


def live_candidate(raw: dict) -> dict:
    """One live QBDI capture shaped for the EU4 observation builder."""
    return {
        "evidence": "qbdi-rosetta",
        "qbdi_version": raw.get("qbdi_version") or "unverified",
        "run": raw.get("run") or {},
        "refusal": raw.get("refusal"),
        "image": raw.get("image") or {},
        "file_sha256": raw.get("file_sha256"),
        "window_sha256": raw.get("window_sha256"),
        "expected_file_sha256": raw.get("expected_file_sha256"),
        "expected_window_sha256": raw.get("expected_window_sha256"),
    }


def read_log(path: Path) -> dict | None:
    """Read one dylib log. A missing or partial file is not a previous run."""
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def mapped_from_log(raw: dict) -> bytes | None:
    """Bytes the process copied before the patch. A missing field is not the file."""
    text = raw.get("mapped_window")
    if not isinstance(text, str) or len(text) != WINDOW_LEN * 2:
        return None
    try:
        return bytes.fromhex(text)
    except ValueError:
        return None


def take_log(path: Path, session: str, pid: int) -> tuple[dict | None, bool]:
    """Return a sealed log for this launch, and whether a sealed log was someone else's."""
    raw = read_log(path)
    if raw is None or raw.get("sealed") is not True:
        return None, False
    if raw.get("session") == session and raw.get("pid") == pid:
        return raw, False
    return None, True


def hook_gap(raw: dict) -> str | None:
    """Why this sealed log is not one installed, restored, entered hook."""
    if raw.get("proc_translated") != 1:
        return "proc_translated is not 1"
    if raw.get("patched") is not True:
        return "hook was not patched"
    hook = raw.get("hook")
    if not isinstance(hook, dict):
        return "hook record is absent"
    if hook.get("count") != 1:
        return "hook count is not 1"
    if hook.get("qbdi_invocations") != 1:
        return "qbdi invocations is not 1"
    if hook.get("restored_before_qbdi") is not True:
        return "entry was not restored before QBDI"
    if hook.get("entry_bytes_match_after") is not True:
        return "entry bytes do not match after the run"
    run = raw.get("run")
    if not isinstance(run, dict) or run.get("ran") is not True or run.get("entered") is not True:
        return "QBDI did not enter the invocation"
    return None


def await_record(process: subprocess.Popen, log_path: Path, session: str,
                 timeout: float) -> tuple[dict | None, str, bool]:
    """Wait for this launch's sealed log. Process exit is not the capture signal."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exited = process.poll() is not None
        raw, mismatch = take_log(log_path, session, process.pid)
        if raw is not None:
            return raw, "capture", False
        if mismatch:
            return None, "rejected", True
        if exited:
            return None, "exited", False
        time.sleep(0.05)
    exited = process.poll() is not None
    raw, mismatch = take_log(log_path, session, process.pid)
    if raw is not None:
        return raw, "capture", False
    return None, "exited" if exited else "timeout", mismatch


def _confirm_stopped(process: subprocess.Popen, code: int | None) -> tuple[int | None, bool]:
    """Recheck the owned child. A lookup failure means it is already gone."""
    try:
        polled = process.poll()
    except ProcessLookupError:
        return code, True
    except OSError:
        return code, False
    if polled is None:
        return code, False
    return polled, True


def stop_owned(process: subprocess.Popen | None) -> dict:
    """Stop the process this launcher started. An already-exited child is not an error."""
    if process is None:
        return {"action": "none", "returncode": None, "stopped": True}
    error = None
    try:
        result = _stop_owned(process)
        action, code = result["action"], result["returncode"]
    except OSError as exc:
        error = str(exc)
        action, code = "none", None
    code, stopped = _confirm_stopped(process, code)
    if not stopped and action == "kill":
        error = error or "kill wait timed out"
    elif not stopped:
        action = "kill"
        try:
            process.kill()
        except ProcessLookupError:
            stopped = True
        except OSError as exc:
            error = f"{error}; {exc}" if error else str(exc)
        else:
            try:
                code = process.wait(timeout=STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                error = f"{error}; kill wait timed out" if error else "kill wait timed out"
            except OSError as exc:
                error = f"{error}; {exc}" if error else str(exc)
        code, stopped = _confirm_stopped(process, code)
    result = {"action": action, "returncode": code, "stopped": stopped}
    if error and not stopped:
        result["error"] = error
    return result


def _stop_owned(process: subprocess.Popen) -> dict:
    try:
        code = process.poll()
    except ProcessLookupError:
        return {"action": "none", "returncode": None}
    if code is not None:
        return {"action": "none", "returncode": code}
    try:
        process.terminate()
    except ProcessLookupError:
        return {"action": "none", "returncode": process.poll()}
    try:
        return {"action": "terminate", "returncode": process.wait(timeout=STOP_WAIT_S)}
    except subprocess.TimeoutExpired:
        pass
    except ProcessLookupError:
        return {"action": "terminate", "returncode": process.poll()}
    try:
        process.kill()
    except ProcessLookupError:
        return {"action": "kill", "returncode": process.poll()}
    try:
        return {"action": "kill", "returncode": process.wait(timeout=STOP_WAIT_S)}
    except subprocess.TimeoutExpired:
        return {"action": "kill", "returncode": process.poll()}


def classify_termination(wait: str, stop: dict) -> dict:
    """How the owned process ended. ``wait`` separates a deadline from a launcher error."""
    code = stop["returncode"]
    action = stop["action"]
    if stop.get("stopped") is False:
        kind = "launcher_stop"
    elif action == "kill":
        kind = "forced_kill"
    elif action == "terminate" and wait == "capture":
        kind = "capture_stop"
    elif action == "terminate" and wait == "timeout":
        kind = "timeout_termination"
    elif action == "terminate":
        kind = "launcher_stop"
    elif code == 0:
        kind = "normal_exit"
    else:
        kind = "unexpected_crash"
    result = {"wait": wait, "action": action, "returncode": code, "classification": kind,
              "stopped": stop.get("stopped", True)}
    if stop.get("error"):
        result["error"] = stop["error"]
    return result


def _blank(diagnostic: str) -> dict:
    return {"diagnostic": diagnostic, "observation": None, "limits": gfx.LIMITS}


def _outcome_name(raw: dict | None, outcome: dict, termination: dict, *,
                  launch_error: BaseException | None,
                  interpret_error: BaseException | None) -> str:
    if raw is None:
        crashed = termination["classification"] in ("unexpected_crash", "forced_kill")
        if launch_error or crashed:
            return "process_failed"
        return "capture_missing"
    if interpret_error is not None:
        return "interpretation_failed"
    if raw.get("refusal"):
        return "hook_refused"
    if outcome["observation"] is None:
        return "hook_unattested"
    run = raw.get("run")
    native_complete = isinstance(run, dict) and run.get("execution_complete") is True
    if native_complete and outcome["observation"].get("completeness") == "complete":
        return "captured_complete"
    return "captured_incomplete"


def _interpret(raw: dict, path: Path) -> tuple[dict, str | None, BaseException | None]:
    if raw.get("refusal"):
        try:
            return gfx.interpret([live_candidate(raw)]), None, None
        except Exception as exc:
            return _blank(f"interpretation failed: {exc}"), None, exc
    gap = hook_gap(raw)
    if gap:
        return _blank(gap), gap, None
    try:
        return gfx.interpret([live_candidate(raw)], image_path=path,
                              mapped=mapped_from_log(raw)), None, None
    except Exception as exc:
        return _blank(f"interpretation failed: {exc}"), None, exc


def _launcher_args() -> list[str] | None:
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    args = launcher.get("exeArgs")
    if (launcher.get("exePath") != "./eu4.app/Contents/MacOS/eu4"
            or not isinstance(args, list) or not all(isinstance(item, str) for item in args)):
        return None
    return args


def launch_live(output: Path, target: dict, window: dict, path: Path,
                timeout: float = EXECUTION_BOUND_S) -> int:
    """Launch the pinned EU IV process and wait for one sealed record.

    One operator-authorized launch. No retry, reselection, or reinstall.
    ``timeout`` bounds the wait for the record. The command line keeps the default.
    """
    output = Path(output).expanduser().resolve()
    if output.exists():
        print("refused: output already exists")
        return 1
    if path.resolve() != base.GOG_EXE.resolve():
        print("refused: executable is not the GOG binary")
        return 1
    exe_args = _launcher_args()
    if exe_args is None:
        print("refused: unexpected GOG launcher executable or arguments")
        return 1
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        print(f"refused: {exc}")
        return 1
    try:
        reason = _refuse_pin(target, window)
        if reason:
            print(reason)
            return 1
        descriptor = live_descriptor(target)
    except (RuntimeError, KeyError, TypeError) as exc:
        print(f"refused: {exc}")
        return 1

    output.parent.mkdir(parents=True, exist_ok=True)
    session = uuid.uuid4().hex
    acquire = output.parent / f".tr3-{session}"
    acquire.mkdir(exist_ok=False)
    log_path = acquire / "insert.json"
    _host, dylib = startup.build(BUILD_DIR)
    env = _live_env(dylib, log_path, session, descriptor)

    game: subprocess.Popen | None = None
    stop = {"action": "none", "returncode": None}
    raw = None
    wait = "error"
    mismatch = False
    launch_error = None
    try:
        with (acquire / "game.stdout").open("wb") as stdout, (
                acquire / "game.stderr").open("wb") as stderr:
            game = subprocess.Popen(
                ["./eu4", *exe_args], cwd=str(base.GOG_EXE.parent), env=env,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            raw, wait, mismatch = await_record(game, log_path, session, timeout)
    except Exception as exc:
        launch_error = exc
        if raw is None:
            wait = "error"
    finally:
        stop = stop_owned(game)
    if raw is None and game is not None:
        late, late_mismatch = take_log(log_path, session, game.pid)
        raw = late
        mismatch = mismatch or late_mismatch
    termination = classify_termination(wait, stop)
    if raw is None:
        if mismatch:
            diagnostic = "sealed log does not match this launch"
        elif launch_error is not None:
            diagnostic = f"launch failed: {launch_error}"
        else:
            diagnostic = f"dylib wrote no log ({termination['classification']})"
        outcome = _blank(diagnostic)
        interpret_error = None
    else:
        outcome, _gap, interpret_error = _interpret(raw, path)
    if stop.get("stopped") is False:
        extra = f"process cleanup failed: {stop.get('error') or 'process still running'}"
        text = outcome["diagnostic"]
        outcome = {**outcome, "diagnostic": f"{text}; {extra}" if text else extra}
    name = _outcome_name(raw, outcome, termination, launch_error=launch_error,
                         interpret_error=interpret_error)
    payload = {
        "raw": raw, "descriptor": descriptor, "diagnostic": outcome["diagnostic"],
        "observation": outcome["observation"], "limits": outcome["limits"],
        "termination": termination, "outcome": name, "qualification": "not claimed",
    }
    try:
        output.write_text(json.dumps(payload, indent=2) + "\n")
    except OSError as exc:
        print(f"refused: could not write {output}: {exc}")
        return 1
    print(name)
    if stop.get("stopped") is False:
        return 1
    return 0 if name in CAPTURED else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.authorize_live:
        print(UNAUTHORIZED)
        return 1
    target = json.loads(TARGET.read_text())
    decision = decide(target)
    if decision != UNAUTHORIZED:
        print(decision)
        return 1
    window = target["code_windows"]["_GfxSetConstantBuffer"]
    path = Path(target["default_executable"])
    if not path.is_file():
        print("refused: executable missing")
        return 1
    reason = image.check(path, {
        "file_sha256": target["sha256"],
        "window_sha256": window["sha256"],
        "image_base": target["image_base"],
        "entry_rva": int(window["entry_rva"], 16),
        "end_rva": int(window["end_rva"], 16),
    })
    if reason:
        print(f"refused: {reason}")
        return 1
    return launch_live(args.output, target, window, path)


if __name__ == "__main__":
    raise SystemExit(main())
