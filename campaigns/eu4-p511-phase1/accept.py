#!/usr/bin/env python3
"""P5.1.1 phase-1 pending clear/SetRT + deferred SetRT acceptance."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PIN = "5b5545e9aee316f333430b37a5889b77b13a533d"
ALLOWED = {
    "analysis/automation/eu4-p511-phase1/note.md",
    "analysis/automation/eu4-p511-phase1/progress.md",
    "progress.md",
    "analysis/gfx-render/v0/hidpi-contract.md",
    "benchmark/eu4_gfx_init.h",
    "benchmark/eu4_gfx_init.mm",
    "benchmark/eu4_metal_geometry_view.h",
    "benchmark/eu4_metal_geometry_view.m",
    "tests/gfx_hidpi_host.mm",
    "tests/test_gfx_hidpi.py",
    "tests/test_gfx_hidpi_appkit.py",
    "tests/gfx_hidpi_appkit_host.mm",
}
TEST_MODULES = (
    "tests.test_gfx_hidpi",
    "tests.test_gfx_hidpi_appkit",
    "tests.test_gfx_default_target",
    "tests.test_metal_boot",
    "tests.test_gfx_init",
    "tests.test_gfx_buffers",
    "tests.test_gfx_shadow_render",
    "tests.test_gfx_shadow_worker",
    "tests.test_gfx_startup_diff",
    "tests.test_gfx_startup_diff_metal",
    "tests.test_gfx_entry_registry",
)


def trusted_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.decode().strip() or "git failed")
    return completed.stdout.decode().strip()


def _common_dir(path: Path) -> Path:
    raw = str(_git(path, "rev-parse", "--git-common-dir"))
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = path / candidate
    return candidate.resolve()


def changed_paths(workspace: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        capture_output=True,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.decode() or "git status failed")
    parts = completed.stdout.split(b"\0")
    found: list[str] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        if not entry:
            break
        status = entry[:2].decode()
        found.append(entry[3:].decode())
        index += 1
        if ("R" in status or "C" in status) and index < len(parts) and parts[index]:
            found.append(parts[index].decode())
            index += 1
    return found


def note_blocker(note: Path) -> str | None:
    if not note.is_file():
        return None
    for line in note.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("BLOCKER:"):
            return line.strip()
    return None


def extract_fn(body: str, pattern: str) -> str | None:
    match = re.search(pattern, body, re.M | re.S)
    return match.group("body") if match else None


def source_gates(workspace: Path) -> str | None:
    body = (workspace / "benchmark" / "eu4_gfx_init.mm").read_text(encoding="utf-8")
    header = (workspace / "benchmark" / "eu4_gfx_init.h").read_text(encoding="utf-8")
    contract = workspace / "analysis" / "gfx-render" / "v0" / "hidpi-contract.md"
    if not contract.is_file():
        return "hidpi-contract.md missing"
    contract_text = contract.read_text(encoding="utf-8")

    clear = extract_fn(
        body,
        r"static\s+eu4_gfx_clear_status\s+clear_game_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
    )
    if not clear:
        return "clear_game_checked not found"
    # Order: obsolete, then transition_in_progress, then pending unsupported
    obs = clear.find("pending_is_obsolete")
    trans = clear.find("geometry_transition_in_progress")
    pending_unsup = -1
    for m in re.finditer(
        r"pendingGameCommand\s*!=\s*nil[\s\S]{0,120}UNSUPPORTED", clear
    ):
        pending_unsup = m.start()
        break
    if obs < 0 or trans < 0 or pending_unsup < 0:
        return "clear_game_checked missing obsolete/transition/pending order markers"
    if not (obs < trans < pending_unsup):
        return "clear_game_checked must check transition before same-gen pending UNSUPPORTED"

    set_rt = extract_fn(
        body,
        r"eu4_gfx_clear_status\s+eu4_gfx_set_render_target_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
    )
    if not set_rt:
        return "set_render_target_checked not found"
    st = set_rt.find("geometry_transition_in_progress")
    so = set_rt.find("pending_is_obsolete")
    if st < 0:
        return "SetRT missing geometry_transition_in_progress"
    if so >= 0 and st > so:
        return "SetRT must check phase-1 transition before obsolete drain"
    if "GEOMETRY_STALE" not in set_rt:
        return "SetRT missing GEOMETRY_STALE for phase 1"

    prod_set = extract_fn(
        body,
        r"void\s+eu4_gfx_set_render_target\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
    )
    if not prod_set or "EU4_GFX_CLEAR_GEOMETRY_STALE" not in prod_set:
        return "production SetRT missing GEOMETRY_STALE nonfatal branch"

    if (
        "deferredDefaultTargetSelect" not in body
        and "deferred_default_target" not in body
        and "deferredDefault" not in body
    ):
        return "missing deferred default-target SetRT state"
    if "deferred" not in header.lower() and "deferredDefault" not in body:
        # flag may be only on ObjC backend ivar in .mm
        if "deferredDefaultTargetSelect" not in body:
            return "deferred SetRT flag not found"

    if "deferred" not in contract_text.lower():
        return "hidpi-contract missing deferred SetRT documentation"
    return None


def host_gates(workspace: Path) -> str | None:
    host = workspace / "tests" / "gfx_hidpi_host.mm"
    if not host.is_file():
        return "gfx_hidpi_host.mm missing"
    text = host.read_text(encoding="utf-8")
    lowered = text.lower()
    need = (
        ("phase" in lowered and "pending" in lowered)
        or "transition_in_progress" in lowered
        or "geometry_transition" in lowered
    )
    if not need:
        return "host missing phase-1 pending scenario"
    if "deferred" not in lowered and "logical b" not in lowered:
        if "viewport" not in lowered or "setrt" not in lowered.replace("_", ""):
            return "host missing deferred SetRT / logical-resize recovery scenario"
    if "aba" not in lowered and "a_to_b_to_a" not in lowered:
        if "pending" not in lowered or "generation" not in lowered:
            return "host missing ABA pending scenario"
    # production wrappers
    stripped = text.replace("eu4_gfx_clear_checked", "X").replace(
        "eu4_gfx_set_render_target_checked", "Y"
    )
    if not re.search(r"eu4_gfx_clear\s*\(", stripped):
        return "host missing production eu4_gfx_clear"
    if not re.search(r"eu4_gfx_set_render_target\s*\(", stripped):
        return "host missing production eu4_gfx_set_render_target"
    return None


def run_module(workspace: Path, name: str) -> tuple[str | None, dict | None]:
    env = os.environ.copy()
    env["MTL_DEBUG_LAYER"] = "1"
    env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    env["PYTHONPATH"] = str(workspace / "benchmark") + os.pathsep + str(workspace)
    driver = (
        "import importlib,json,sys,unittest\n"
        "module=importlib.import_module(sys.argv[1])\n"
        "result=unittest.TextTestRunner(stream=sys.stderr,verbosity=0).run("
        "unittest.defaultTestLoader.loadTestsFromModule(module))\n"
        "failed=result.failures+result.errors\n"
        "detail='\\n'.join(item[1] for item in failed)[:2000]\n"
        "skips=[str(s[0]) for s in result.skipped]\n"
        "json.dump({'file':module.__file__,'ran':result.testsRun,"
        "'skipped':len(result.skipped),'skips':skips,'failed':len(failed),"
        "'successful':result.wasSuccessful(),'detail':detail},sys.stdout)\n"
    )
    try:
        ran = subprocess.run(
            [sys.executable, "-c", driver, name],
            cwd=str(workspace),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}", None
    try:
        payload = json.loads(ran.stdout)
    except json.JSONDecodeError:
        detail = (ran.stderr or ran.stdout or "no result").strip()
        kind = "fail: " if ran.returncode else "unavailable: "
        return kind + detail[:2000], None
    loaded = Path(str(payload.get("file", ""))).resolve()
    if workspace not in loaded.parents:
        return "fail: tests not loaded from worktree", None
    return None, payload


def run_tests(workspace: Path) -> str | None:
    if sys.platform != "darwin":
        return "unavailable: requires macOS Metal"
    # AppKit gate individually mandatory
    err, payload = run_module(workspace, "tests.test_gfx_hidpi_appkit")
    if err:
        return err
    assert payload is not None
    skips = [s.lower() for s in (payload.get("skips") or [])]
    if any("appkit" in s or "drawable" in s or "window" in s or "real" in s for s in skips):
        return "unavailable: window-backed AppKit real drawable skipped"
    ran_count = int(payload.get("ran") or 0) - int(payload.get("skipped") or 0)
    if ran_count < 1:
        return "unavailable: AppKit module did not run a test"
    if int(payload.get("failed") or 0) or payload.get("successful") is not True:
        return f"fail: tests.test_gfx_hidpi_appkit: {payload.get('detail') or ''}"[:2000]

    for name in TEST_MODULES:
        if name == "tests.test_gfx_hidpi_appkit":
            continue
        err, payload = run_module(workspace, name)
        if err:
            return err
        assert payload is not None
        ran_count = int(payload.get("ran") or 0) - int(payload.get("skipped") or 0)
        if ran_count < 1:
            return f"unavailable: {name} did not run a test"
        if int(payload.get("failed") or 0) or payload.get("successful") is not True:
            return f"fail: {name}: {payload.get('detail') or ''}"[:2000]
    return None


def check(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    root = trusted_root()
    try:
        head = str(_git(workspace, "rev-parse", "HEAD"))
    except OSError as exc:
        return emit("unavailable", str(exc))
    if head != PIN:
        return emit("fail", f"worktree HEAD is not the pinned commit ({head})")
    try:
        if _common_dir(workspace) != _common_dir(root):
            return emit("unavailable", "worktree belongs to a different repository")
    except OSError as exc:
        return emit("unavailable", str(exc))
    if (workspace / ".git").is_dir():
        return emit("unavailable", "refusing primary checkout")

    note = workspace / "analysis" / "automation" / "eu4-p511-phase1" / "note.md"
    try:
        dirty = changed_paths(workspace)
    except OSError as exc:
        return emit("unavailable", str(exc))
    extra = [p for p in dirty if p not in ALLOWED]
    if extra:
        return emit("fail", "edits outside allowlist: " + ", ".join(extra[:12]))

    blocker = note_blocker(note) if note.is_file() else None
    impl_touched = any(p.startswith("benchmark/") or p.startswith("tests/") for p in dirty)
    if blocker and not impl_touched:
        return emit("blocked", blocker)
    if blocker and impl_touched:
        return emit("fail", f"BLOCKER present with implementation edits: {blocker}")

    if not note.is_file():
        return emit("fail", "research note missing")
    note_text = note.read_text(encoding="utf-8")
    if (
        "Project-derived" not in note_text
        and "SDK" not in note_text
        and "Unresolved" not in note_text
    ):
        return emit("fail", "note missing provenance labels")

    if not dirty:
        return emit("fail", "no phase-1 edits yet")

    gate = source_gates(workspace)
    if gate:
        return emit("fail", gate)
    host = host_gates(workspace)
    if host:
        return emit("fail", host)

    test_problem = run_tests(workspace)
    if test_problem:
        if test_problem.startswith("unavailable:"):
            return emit("unavailable", test_problem.removeprefix("unavailable: ").strip())
        return emit("fail", test_problem.removeprefix("fail: ").strip())
    return emit("pass", "p511 phase-1 gates held")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(sys.argv[1]))
    print(json.dumps(result))
    if result["verdict"] == "pass":
        return 0
    if result["verdict"] == "fail":
        return 1
    if result["verdict"] == "blocked":
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
