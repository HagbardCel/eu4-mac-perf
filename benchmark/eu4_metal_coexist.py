#!/usr/bin/env python3
"""Offscreen Metal clear inside the EU4 process. The GL window is not modified."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import eu4_benchmark as base


SOURCE = Path(__file__).resolve().with_suffix(".m")
LIBRARY = SOURCE.parent / ".build/libeu4_metal_coexist.dylib"
HOST_SOURCE = SOURCE.parents[1] / "tests/metal_coexist_host.c"
HOST = SOURCE.parent / ".build/metal_coexist_host"


def build_library(arch: str = "x86_64") -> Path:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    output = LIBRARY if arch == "x86_64" else LIBRARY.with_name(f"libeu4_metal_coexist_{arch}.dylib")
    command = [
        "clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
        "-framework", "Foundation", "-framework", "Metal", "-framework", "OpenGL",
        "-o", str(output), str(SOURCE),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Could not build {arch} Metal probe: {result.stderr.strip()}")
    if base.command("lipo", "-archs", str(output)).strip() != arch:
        raise base.BenchmarkError(f"Metal probe is not {arch}")
    return output


def build_host(arch: str = "x86_64") -> Path:
    HOST.parent.mkdir(parents=True, exist_ok=True)
    output = HOST if arch == "x86_64" else HOST.with_name(f"metal_coexist_host_{arch}")
    command = ["clang", "-arch", arch, "-O2", "-Wall", "-Wextra", "-o", str(output), str(HOST_SOURCE)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Could not build {arch} Metal probe host: {result.stderr.strip()}")
    return output


def run_probe(library: Path, receipt: Path, arch: str = "x86_64") -> dict:
    """Run the clear and readback in a host process. This is not the EU4 session."""
    host = build_host(arch)
    env = os.environ.copy()
    env["EU4_METAL_COEXIST_RECEIPT"] = str(receipt.resolve())
    env["EU4_METAL_COEXIST_LIB"] = str(library.resolve())
    result = subprocess.run([str(host)], capture_output=True, text=True, check=False, env=env)
    if result.returncode:
        raise base.BenchmarkError(result.stderr.strip() or "Metal probe host failed")
    return json.loads(receipt.read_text())


def require_offscreen(receipt: dict) -> None:
    if receipt.get("gpu_coexistence") != "offscreen_coexistence_verified" or not receipt.get("readback_matches"):
        raise base.BenchmarkError(f"Offscreen Metal readback failed: {receipt}")
    if receipt.get("window_drawable") not in ("window_drawable_not_tested", "window_drawable_tested"):
        raise base.BenchmarkError(f"Unexpected drawable field: {receipt}")


def windows_for_pid(pid: int) -> list[dict]:
    """On-screen windows owned by the EU4 process. Empty if CoreGraphics cannot see them."""
    import ctypes

    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    cg.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p

    def key(name: str):
        return cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)

    def text(value) -> str:
        if not value:
            return ""
        buf = ctypes.create_string_buffer(512)
        if cf.CFStringGetCString(value, buf, 512, 0x08000100):
            return buf.value.decode()
        return ""

    def number(value) -> int | None:
        if not value:
            return None
        parsed = ctypes.c_int64()
        if cf.CFNumberGetValue(value, 4, ctypes.byref(parsed)):  # kCFNumberSInt64Type
            return int(parsed.value)
        return None

    names = {item: key(item) for item in (
        "kCGWindowOwnerPID", "kCGWindowNumber", "kCGWindowOwnerName", "kCGWindowName", "kCGWindowLayer")}
    bounds_key = key("kCGWindowBounds")
    origin = key("X"), key("Y"), key("Width"), key("Height")
    listed = cg.CGWindowListCopyWindowInfo(1, 0)
    found = []
    for index in range(cf.CFArrayGetCount(listed)):
        info = cf.CFArrayGetValueAtIndex(listed, index)
        if number(cf.CFDictionaryGetValue(info, names["kCGWindowOwnerPID"])) != pid:
            continue
        bounds = cf.CFDictionaryGetValue(info, bounds_key)
        found.append({
            "window_id": number(cf.CFDictionaryGetValue(info, names["kCGWindowNumber"])),
            "owner": text(cf.CFDictionaryGetValue(info, names["kCGWindowOwnerName"])),
            "title": text(cf.CFDictionaryGetValue(info, names["kCGWindowName"])),
            "layer": number(cf.CFDictionaryGetValue(info, names["kCGWindowLayer"])),
            "bounds": {
                axis: number(cf.CFDictionaryGetValue(bounds, origin[i])) if bounds else None
                for i, axis in enumerate(("x", "y", "width", "height"))
            },
        })
    return found


def wait_for_receipt(path: Path, game: subprocess.Popen, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        if game.poll() is not None:
            raise base.BenchmarkError(f"EU4 exited {game.returncode} before the Metal receipt was written")
        time.sleep(0.2)
    raise base.BenchmarkError("EU4 did not write the Metal receipt")


def launch_session(run_dir: Path, settle_seconds: float = 45) -> dict:
    """One EU4 launch. Does not replace the GL view. Stops the process after the receipt."""
    identity = base.gog_identity()
    if base.command("lipo", "-archs", str(base.GOG_EXE)).strip() != "x86_64":
        raise base.BenchmarkError("This probe is for the installed x86_64 GOG EU IV build only")
    base.running_game_pid("idle")
    library = build_library("x86_64")
    launcher = json.loads(base.GOG_LAUNCHER.read_text())
    run_dir.mkdir(parents=True, exist_ok=False)
    receipt_path = run_dir / "receipt.json"
    metadata = {
        "status": "launching",
        "started_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gog": identity,
        "library": str(library),
        "library_sha256": base.sha256(library),
        "source_sha256": base.sha256(SOURCE),
    }
    metadata_path = run_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    env = os.environ.copy()
    env["EU4_METAL_COEXIST_RECEIPT"] = str(receipt_path)
    env["DYLD_INSERT_LIBRARIES"] = str(library)
    stdout = (run_dir / "game.stdout").open("wb")
    stderr = (run_dir / "game.stderr").open("wb")
    game = subprocess.Popen(
        ["./eu4", *launcher["exeArgs"]],
        cwd=base.GOG_EXE.parent,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )
    metadata["game_pid"] = game.pid
    try:
        receipt = wait_for_receipt(receipt_path, game, 30)
        require_offscreen(receipt)
        metadata["receipt"] = receipt
        windows = []
        flushes_path = receipt_path.with_name(receipt_path.name + ".flushes")
        flushes = 0
        deadline = time.monotonic() + settle_seconds
        while time.monotonic() < deadline and game.poll() is None:
            if not windows:
                windows = windows_for_pid(game.pid)
            if flushes_path.is_file():
                try:
                    flushes = int(flushes_path.read_text().strip() or "0")
                except ValueError:
                    flushes = 0
                if flushes > 0 and windows:
                    break
            time.sleep(0.5)
        alive = game.poll() is None
        metadata["windows"] = windows
        metadata["gl_flush_success_count"] = flushes
        metadata["eu4_window_enumerated"] = bool(windows)
        metadata["metal_readback_matched"] = bool(receipt.get("readback_matches"))
        metadata["visual_frame_inspected"] = False
        metadata["status"] = "complete" if alive and flushes > 0 and windows and receipt.get("readback_matches") else "process_exited"
    finally:
        if game.poll() is None:
            game.send_signal(signal.SIGTERM)
            try:
                game.wait(timeout=10)
            except subprocess.TimeoutExpired:
                game.kill()
                game.wait(timeout=5)
        stdout.close()
        stderr.close()
        metadata["ended_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
        metadata["game_returncode"] = game.returncode
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> int:
    parser_dir = base.ROOT / "results" / (
        dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-metal-coexist"
    )
    try:
        metadata = launch_session(parser_dir)
    except (base.BenchmarkError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": metadata["status"],
        "receipt": metadata.get("receipt"),
        "gl_flush_success_count": metadata.get("gl_flush_success_count"),
        "eu4_window_enumerated": metadata.get("eu4_window_enumerated"),
        "run_dir": str(parser_dir),
    }, indent=2))
    return 0 if metadata["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
