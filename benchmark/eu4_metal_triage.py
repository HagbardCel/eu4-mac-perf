#!/usr/bin/env python3
"""Read-only summary of one recorded metal_init directory. Does not launch EU4."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import eu4_metal_boot as boot


_PRIMARY = ("metadata.json", "init.json", "stop.json")
_FILES = _PRIMARY + ("create.json", "draw_invocation.json", "blocked_at.json")
_DRAW_SYMBOLS = {"_GfxDraw", "_GfxDrawIndexed", "_GfxDrawIndexedInstanced"}
_BACKEND = {"unsupported", "reentry", "unresolved_forward"}
_DISCLAIMER = (
    "A diagnostic stop does not show that a replacement ran or that a frame was drawn."
)


def _load(path: Path) -> tuple[dict[str, bool], dict[str, dict | None]]:
    exists: dict[str, bool] = {}
    loaded: dict[str, dict | None] = {}
    for name in _FILES:
        file = path / name
        exists[name] = file.is_file()
        loaded[name] = boot.read_event(file) if exists[name] else None
    return exists, loaded


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _bad_pid(event: dict) -> bool:
    if "pid" not in event:
        return False
    pid = event["pid"]
    return isinstance(pid, bool) or not isinstance(pid, int)


def _pids(loaded: dict[str, dict | None]) -> list[int] | None:
    found = []
    for event in loaded.values():
        if event is None:
            continue
        if _bad_pid(event):
            return None
        if "pid" in event:
            found.append(event["pid"])
    return found


def _kind(exists: dict[str, bool], loaded: dict[str, dict | None]) -> str:
    if not any(exists[name] for name in _PRIMARY):
        return "missing"
    malformed = any(exists[name] and loaded[name] is None for name in _FILES)
    pids = None if malformed else _pids(loaded)
    if malformed or pids is None or len(set(pids)) > 1:
        return "incomplete"
    stop = loaded["stop.json"]
    meta = loaded["metadata.json"]
    init = loaded["init.json"]
    if stop is not None and stop.get("reason") in _BACKEND:
        return "unsupported"
    why = None if meta is None else meta.get("stop_reason")
    code = None if meta is None else meta.get("exit_code")
    if why == "deadline":
        return "timeout"
    if type(code) is int and code < 0 and meta is not None and meta.get("launcher_signaled") is False:
        return "crash"
    if meta is not None and init is not None and stop is not None:
        create = loaded["create.json"]
        pids = [meta.get("pid"), init.get("pid"), stop.get("pid")]
        gog = meta.get("gog")
        sha_ok = (
            not isinstance(gog, dict)
            or "executable_sha256" not in gog
            or gog["executable_sha256"] == boot.PINNED_SHA256
        )
        identity_ok = (
            all(_positive_int(pid) for pid in pids)
            and len(set(pids)) == 1
            and (create is None or create.get("pid") == pids[0])
            and _positive_int(init.get("thread"))
            and _positive_int(stop.get("thread"))
            and init["thread"] == stop["thread"]
            and sha_ok
        )
        if identity_ok:
            # A missing flag is not evidence the launcher stayed quiet.
            signaled = meta.get("launcher_signaled")
            outcome = boot.metal_init_outcome(
                init.get("success") == 1,
                stop.get("phase"),
                why,
                code,
                init.get("thread"),
                stop.get("thread"),
                signaled if isinstance(signaled, bool) else True,
                stop.get("reason"),
                stop.get("image"),
                stop.get("rva"),
            )
            if outcome["status"] == "complete":
                return "gl_diagnostic"
            if outcome["status"] in ("blocked_at", "draw_reached"):
                return outcome["status"]
    if (
        why == "exit"
        and isinstance(code, int)
        and not isinstance(code, bool)
        and code == 0
        and init is not None
        and init.get("success") == 1
    ):
        return "clean_exit"
    return "incomplete"


def _receipt(create: dict | None, present: bool, pid: int | None) -> str:
    if not present:
        return "absent"
    if (
        create is not None
        and create.get("success") == 1
        and create.get("symbol") == "_GfxCreateVertexBuffer"
        and isinstance(pid, int)
        and not isinstance(pid, bool)
        and create.get("pid") == pid
    ):
        return "validated"
    return "unvalidated"


def _same_pid(event: dict | None, pid: object) -> bool:
    return event is not None and type(pid) is int and event.get("pid") == pid


def _draw_game(draw: dict, loaded: dict[str, dict | None]) -> bool:
    if draw.get("provenance") != "game" or draw.get("symbol") not in _DRAW_SYMBOLS:
        return False
    invocation = draw.get("invocation_id")
    if type(invocation) is not int or isinstance(invocation, bool) or invocation <= 0:
        return False
    caller = boot.parse_rva(draw.get("caller_rva"))
    chain = draw.get("call_chain")
    if caller is None or not isinstance(chain, list) or not chain:
        return False
    if boot.parse_rva(chain[0]) != caller:
        return False
    if not boot.same_pinned_executable(draw.get("image")):
        return False
    complete = draw.get("context_complete")
    want = draw.get("context_want")
    got = draw.get("context_got")
    if type(complete) is not bool or type(want) is not int or type(got) is not int:
        return False
    if isinstance(want, bool) or isinstance(got, bool) or want < 0 or got < 0 or got > want:
        return False
    if complete and got != want:
        return False
    meta = loaded.get("metadata.json")
    init = loaded.get("init.json")
    stop = loaded.get("stop.json")
    pid = draw.get("pid")
    if type(pid) is not int or isinstance(pid, bool):
        return False
    for event in (meta, init, stop):
        if event is not None and "pid" in event and event.get("pid") != pid:
            return False
    if meta is not None and isinstance(meta.get("gog"), dict) and "executable_sha256" in meta["gog"]:
        if meta["gog"]["executable_sha256"] != boot.PINNED_SHA256:
            return False
    if init is not None and stop is not None:
        if not _positive_int(init.get("thread")) or init.get("thread") != stop.get("thread"):
            return False
        if "thread" in draw and draw.get("thread") != init.get("thread"):
            return False
    return True


def d1_state(exists: dict[str, bool], loaded: dict[str, dict | None]) -> str:
    """D1 progress. Independent of the init-loop kind."""
    name = "draw_invocation.json"
    if exists.get(name) and loaded.get(name) is None:
        return "error"
    draw = loaded.get(name)
    if draw is not None:
        return "draw_reached" if _draw_game(draw, loaded) else "error"
    if exists.get("blocked_at.json") and loaded.get("blocked_at.json") is None:
        return "error"
    stop = loaded.get("stop.json")
    if stop is not None and stop.get("reason") == "blocked_at":
        meta = loaded.get("metadata.json")
        if meta is not None and "pid" in meta and not _same_pid(stop, meta.get("pid")):
            return "error"
        blocked = loaded.get("blocked_at.json")
        if blocked is not None and blocked.get("pid") != stop.get("pid"):
            return "error"
        return "blocked_at"
    return "not_started"


def diagnose(path: Path) -> dict:
    """Interpret recorded files. Does not create, rename, or delete anything."""
    exists, loaded = _load(path)
    meta = loaded["metadata.json"]
    init = loaded["init.json"]
    stop = loaded["stop.json"]
    create = loaded["create.json"]
    pids = _pids(loaded)
    agreed = pids[0] if pids and len(set(pids)) == 1 else None
    rva = boot.parse_rva(None if stop is None else stop.get("rva"))
    threads = [event["thread"] for event in loaded.values() if event and "thread" in event]
    image = None if stop is None or "image" not in stop else boot.same_pinned_executable(stop.get("image"))
    sha = None
    if meta is not None and isinstance(meta.get("gog"), dict) and "executable_sha256" in meta["gog"]:
        sha = meta["gog"]["executable_sha256"] == boot.PINNED_SHA256
    signaled = None if meta is None else meta.get("launcher_signaled")
    code = None if meta is None else meta.get("exit_code")
    return {
        "kind": _kind(exists, loaded),
        "process": agreed,
        "init_success": None if init is None else init.get("success") == 1,
        "vertex_buffer_receipt": _receipt(create, exists["create.json"], agreed),
        "stop_symbol": None if stop is None else stop.get("symbol"),
        "stop_reason": None if stop is None else stop.get("reason"),
        "return_rva": rva,
        "preceding_byte_rva": None if rva is None else rva - 1,
        "inside_replaced_body": None if rva is None else not boot.caller_outside_replacements(rva),
        "threads_agree": None if not threads else all(item == threads[0] for item in threads),
        "pinned_image": image,
        "pinned_sha256": sha,
        "launcher_signaled": signaled if isinstance(signaled, bool) else None,
        "exit_code": code if isinstance(code, int) and not isinstance(code, bool) else None,
        "d1": d1_state(exists, loaded),
    }


def format_diagnosis(diag: dict) -> str:
    lines = ["Metal migration diagnostic", f"kind: {diag['kind']}", f"d1: {diag['d1']}"]
    if diag["process"] is not None:
        lines.append(f"process: {diag['process']}")
    if diag["init_success"] is not None:
        lines.append("metal_init: " + ("success" if diag["init_success"] else "failed"))
    receipt = diag["vertex_buffer_receipt"]
    if receipt == "validated":
        lines.append("Validated _GfxCreateVertexBuffer success receipt, recorded from this process.")
        lines.append("The receipt shows the replacement reached its success-reporting point.")
    elif receipt == "absent":
        lines.append("No vertex-buffer receipt was recorded.")
    else:
        lines.append("vertex_buffer_receipt: unvalidated")
    for key in ("stop_symbol", "stop_reason"):
        if diag[key]:
            lines.append(f"{key}: {diag[key]}")
    for key in ("return_rva", "preceding_byte_rva"):
        if diag[key] is not None:
            lines.append(f"{key}: {diag[key]:#x}")
    flags = (
        "inside_replaced_body", "threads_agree", "pinned_image", "pinned_sha256", "launcher_signaled",
    )
    for key in flags:
        if diag[key] is not None:
            lines.append(f"{key}: {'yes' if diag[key] else 'no'}")
    if diag["exit_code"] is not None:
        lines.append(f"exit_code: {diag['exit_code']}")
    lines.append(_DISCLAIMER)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Metal migration diagnostic.")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    if not args.run_dir.is_dir():
        print("not a directory", file=sys.stderr)
        return 1
    print(format_diagnosis(diagnose(args.run_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
