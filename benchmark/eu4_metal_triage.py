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
# Bodies installed before the draw and texture entries. Sessions without
# gfx_entry_ranges are classified by probe_sha256, never by the current file.
_PRE_DRAW_RANGES = (
    (0x15ED388, 0x15ED9FC),
    (0x15E8EA0, 0x15E9D11),
    (0x15EA0BF, 0x15EA261),
    (0x15EA261, 0x15EA35A),
    (0x15EA35A, 0x15EA42C),
    (0x15EA42C, 0x15EA460),
    (0x15EA46B, 0x15EA49B),
    (0x15EA49B, 0x15EA4F2),
    (0x15EBCB3, 0x15EBD4C),
    (0x15EBD4C, 0x15EBD81),
    (0x15EBD81, 0x15EBE5F),
    (0x15EA4F8, 0x15EA52C),
)
_PR8_RANGES = _PRE_DRAW_RANGES + (
    (0x15EA789, 0x15EAA77),
    (0x15EAA77, 0x15EAC96),
    (0x15EAC9C, 0x15EAD87),
    (0x15EC43B, 0x15EC61D),
)
_PROBE_RANGES = {
    "40911df531832b0450ae5584b5ff850d3e81bd69aec2ef5ecb5e6b3d960fadfb": _PRE_DRAW_RANGES,
    "96b1e40d6ab4a98ede7c7ff354fa5c9bf00abf7b5cc8a33a111da0619bd94020": _PRE_DRAW_RANGES,
    "9d88806aa662cecf63cebeb7e909f6111bec7b7a27babb1e4aae6c9a110eb507": _PR8_RANGES,
}
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
            ranges = session_ranges(meta)
            outcome = _outcome(loaded, () if ranges is None else ranges)
            if outcome["status"] == "complete":
                return "gl_diagnostic"
            if outcome["status"] == "blocked_at" and blocked_session(exists, loaded):
                return "blocked_at"
            if outcome["status"] == "draw_reached" and draw_session(exists, loaded):
                return "draw_reached"
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


def _parse_ranges(raw: object) -> tuple | None:
    if not isinstance(raw, list) or not raw:
        return None
    spans = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        start, end = item
        if type(start) is not int or type(end) is not int or start < 0 or end <= start:
            return None
        spans.append((start, end))
    return tuple(spans)


def session_ranges(meta: dict | None) -> tuple | None:
    """Ranges installed for this run. None means the inventory is unknown."""
    if not isinstance(meta, dict):
        return None
    if "gfx_entry_ranges" in meta:
        return _parse_ranges(meta.get("gfx_entry_ranges"))
    probe = meta.get("probe_sha256")
    if not isinstance(probe, str):
        return None
    return _PROBE_RANGES.get(probe)


def _outcome(loaded: dict[str, dict | None], ranges: tuple):
    meta = loaded["metadata.json"]
    init = loaded["init.json"]
    stop = loaded["stop.json"]
    assert meta is not None and init is not None and stop is not None
    signaled = meta.get("launcher_signaled")
    return boot.metal_init_outcome(
        init.get("success") == 1,
        stop.get("phase"),
        meta.get("stop_reason"),
        meta.get("exit_code"),
        init.get("thread") if _positive_int(init.get("thread")) else None,
        stop.get("thread") if _positive_int(stop.get("thread")) else None,
        signaled if isinstance(signaled, bool) else True,
        stop.get("reason"),
        stop.get("image"),
        stop.get("rva"),
        ranges,
    )


def create_pid_matches(exists: dict[str, bool], loaded: dict[str, dict | None], pid: int) -> bool:
    if not exists["create.json"]:
        return True
    create = loaded["create.json"]
    return create is not None and _positive_int(create.get("pid")) and create["pid"] == pid


def draw_session(exists: dict[str, bool], loaded: dict[str, dict | None]) -> bool:
    """One live execution: terminal launcher stop plus a matching game draw receipt."""
    names = ("metadata.json", "init.json", "stop.json", "draw_invocation.json")
    if any(not exists.get(name) or loaded.get(name) is None for name in names):
        return False
    meta = loaded["metadata.json"]
    init = loaded["init.json"]
    stop = loaded["stop.json"]
    draw = loaded["draw_invocation.json"]
    assert meta is not None and init is not None and stop is not None and draw is not None
    ranges = session_ranges(meta)
    if ranges is None or _outcome(loaded, ranges)["status"] != "draw_reached":
        return False
    if stop.get("symbol") not in _DRAW_SYMBOLS or stop.get("symbol") != draw.get("symbol"):
        return False
    if draw.get("provenance") != "game":
        return False
    invocation = draw.get("invocation_id")
    if type(invocation) is not int or invocation <= 0:
        return False
    caller = boot.parse_rva(draw.get("caller_rva"))
    chain = draw.get("call_chain")
    if caller is None or boot.parse_rva(stop.get("rva")) != caller:
        return False
    if not isinstance(chain, list) or not chain or boot.parse_rva(chain[0]) != caller:
        return False
    if not boot.same_pinned_executable(draw.get("image")):
        return False
    complete = draw.get("context_complete")
    want = draw.get("context_want")
    got = draw.get("context_got")
    if type(complete) is not bool or type(want) is not int or type(got) is not int:
        return False
    if want < 0 or got < 0 or got > want or (complete and got != want):
        return False
    pid = draw.get("pid")
    if not _positive_int(pid):
        return False
    for event in (meta, init, stop):
        if not _positive_int(event.get("pid")) or event.get("pid") != pid:
            return False
    thread = init.get("thread")
    if not _positive_int(thread) or stop.get("thread") != thread or draw.get("thread") != thread:
        return False
    gog = meta.get("gog")
    if not isinstance(gog, dict) or gog.get("executable_sha256") != boot.PINNED_SHA256:
        return False
    return create_pid_matches(exists, loaded, pid)


def blocked_session(exists: dict[str, bool], loaded: dict[str, dict | None]) -> bool:
    """A blocked_at stop and its receipt are one terminal session."""
    names = ("metadata.json", "init.json", "stop.json", "blocked_at.json")
    if any(not exists.get(name) or loaded.get(name) is None for name in names):
        return False
    meta = loaded["metadata.json"]
    init = loaded["init.json"]
    stop = loaded["stop.json"]
    blocked = loaded["blocked_at.json"]
    assert meta is not None and init is not None and stop is not None and blocked is not None
    ranges = session_ranges(meta)
    if ranges is None or _outcome(loaded, ranges)["status"] != "blocked_at":
        return False
    if stop.get("symbol") != blocked.get("symbol") or not isinstance(stop.get("symbol"), str):
        return False
    caller = boot.parse_rva(stop.get("rva"))
    if caller is None or boot.parse_rva(blocked.get("caller_rva")) != caller:
        return False
    pid = stop.get("pid")
    if not _positive_int(pid):
        return False
    for event in (meta, init, blocked):
        if not _positive_int(event.get("pid")) or event.get("pid") != pid:
            return False
    thread = init.get("thread")
    if not _positive_int(thread) or stop.get("thread") != thread or blocked.get("thread") != thread:
        return False
    gog = meta.get("gog")
    if not isinstance(gog, dict) or gog.get("executable_sha256") != boot.PINNED_SHA256:
        return False
    return create_pid_matches(exists, loaded, pid)


def d1_state(exists: dict[str, bool], loaded: dict[str, dict | None]) -> str:
    """D1 progress. Independent of the init-loop kind."""
    name = "draw_invocation.json"
    if exists.get(name) and loaded.get(name) is None:
        return "error"
    draw = loaded.get(name)
    stop = loaded.get("stop.json")
    if draw is not None or (stop is not None and stop.get("reason") == "draw"):
        return "draw_reached" if draw_session(exists, loaded) else "error"
    if exists.get("blocked_at.json") and loaded.get("blocked_at.json") is None:
        return "error"
    blocked = loaded.get("blocked_at.json")
    if blocked is not None or (stop is not None and stop.get("reason") == "blocked_at"):
        return "blocked_at" if blocked_session(exists, loaded) else "error"
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
    ranges = session_ranges(meta)
    inside = None if rva is None or ranges is None else not boot.caller_outside_replacements(rva, ranges)
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
        "inside_replaced_body": inside,
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
