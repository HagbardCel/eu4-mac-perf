"""Launch entry for one `_GfxSetConstantBuffer` trace.

Verifies the pin recorded in read-only ``reverse_engineering/target.json``,
refuses while operator authorization is absent, and with ``--authorize-live``
launches the pinned GOG EU IV process with the TR3 startup dylib loaded, waits
for the one-shot interception, and hands the captured run to the EU4 observation
builder. ``image_base`` is the preferred image base, not the process load
address. Tests and CI do not execute the live path.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from benchmark import eu4_benchmark as base
from reconstruction_trace.tr3 import gfx, image, startup
from reverse_engineering.macho_rva import MachOMapper

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "reverse_engineering" / "target.json"
BUILD_DIR = Path.home() / ".cache" / "eu4-mac-perf" / "tr3-build"
EXECUTION_BOUND_S = 120

PIN_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PIN_ENTRY_RVA = "0x15ec36a"
PIN_END_RVA = "0x15ec3b5"
PIN_WINDOW_SHA256 = "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"
UNAUTHORIZED = "refused: operator authorization is absent"


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


def live_mapped(image_path: Path, target: dict, window: dict) -> bytes | None:
    """Authenticated mapped-window bytes for the verified pinned executable."""
    entry = int(window["entry_rva"], 16)
    length = int(window["end_rva"], 16) - entry
    try:
        with MachOMapper(image_path) as mapper:
            return bytes(mapper.read_bytes_at_rva(int(target["image_base"]), entry, length))
    except Exception:
        return None


def read_log(path: Path) -> dict | None:
    """Read the dylib log this launch wrote; a missing log is not a previous run."""
    if not path.is_file():
        return None
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def await_exit(process: subprocess.Popen, timeout: float) -> dict:
    """Bound the owned process; terminate only that process on expiry."""
    try:
        process.wait(timeout=timeout)
        return {"status": "exited"}
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return {"status": "terminated"}


def launch_live(output: Path, target: dict, window: dict, path: Path) -> int:
    """Launch the pinned EU IV process with the TR3 dylib and capture one run.

    One operator-authorized launch. No retry, reselection, or reinstall.
    """
    _host, dylib = startup.build(BUILD_DIR)
    env = os.environ.copy()
    env["DYLD_INSERT_LIBRARIES"] = str(dylib)
    env["TR3_EXPECT_FILE_SHA256"] = str(target["sha256"])
    env["TR3_EXPECT_WINDOW_SHA256"] = str(window["sha256"])
    env["TR3_ENTRY_RVA"] = PIN_ENTRY_RVA
    env["TR3_WINDOW_LEN"] = str(int(PIN_END_RVA, 16) - int(PIN_ENTRY_RVA, 16))
    output.parent.mkdir(parents=True, exist_ok=True)
    log_path = output.parent / "insert.json"
    env["TR3_LOG"] = str(log_path)
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    argv = ["./eu4", *launcher["exeArgs"]]
    try:
        base.running_game_pid("idle")
    except base.BenchmarkError as exc:
        print(f"refused: {exc}")
        return 1
    with (output.parent / "game.stdout").open("wb") as stdout, (
            output.parent / "game.stderr").open("wb") as stderr:
        game = subprocess.Popen(argv, cwd=str(base.GOG_EXE.parent), env=env,
                                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
    termination = await_exit(game, EXECUTION_BOUND_S)
    raw = read_log(log_path)
    if raw is None:
        outcome = {"diagnostic": f"dylib wrote no log ({termination['status']})",
                   "observation": None, "limits": gfx.LIMITS}
    else:
        outcome = gfx.interpret([live_candidate(raw)], image_path=path,
                                 mapped=live_mapped(path, target, window))
    payload = {"raw": raw, "diagnostic": outcome["diagnostic"],
               "observation": outcome["observation"], "limits": outcome["limits"],
               "termination": termination}
    output.write_text(json.dumps(payload, indent=2) + "\n")
    return 0 if outcome["observation"] is not None else 1


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
