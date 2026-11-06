#!/usr/bin/env python3
"""P5.1.1 HiDPI lifecycle acceptance. Task-specific; not reconstruction_accept."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PIN = "d873e5c2336709700aaff1abf31c9e989122da50"
ALLOWED = {
    "analysis/automation/eu4-p511-hidpi/note.md",
    "analysis/automation/eu4-p511-hidpi/progress.md",
    "progress.md",
    "analysis/gfx-render/v0/hidpi-contract.md",
    "analysis/gfx-render/v0/default-target-contract.md",
    "benchmark/eu4_gfx_init.h",
    "benchmark/eu4_gfx_init.mm",
    "benchmark/eu4_metal_boot.m",
    "tests/gfx_default_target_host.mm",
    "tests/test_gfx_default_target.py",
    "tests/gfx_hidpi_host.mm",
    "tests/test_gfx_hidpi.py",
}
TEST_MODULES = (
    "tests.test_gfx_hidpi",
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


def require_substrings(text: str, needles: list[str], label: str) -> str | None:
    missing = [n for n in needles if n not in text]
    if missing:
        return f"{label} missing: " + ", ".join(missing[:8])
    return None


def source_gates(workspace: Path) -> str | None:
    header = (workspace / "benchmark" / "eu4_gfx_init.h").read_text(encoding="utf-8")
    body = (workspace / "benchmark" / "eu4_gfx_init.mm").read_text(encoding="utf-8")
    boot = (workspace / "benchmark" / "eu4_metal_boot.m").read_text(encoding="utf-8")

    if "EU4_GFX_CLEAR_GEOMETRY_STALE" not in header:
        return "header missing EU4_GFX_CLEAR_GEOMETRY_STALE"
    if "EU4_GFX_CLEAR_GEOMETRY_STALE" not in body:
        return "init.mm missing GEOMETRY_STALE handling"

    if "reconcile_game_geometry" not in body and "reconcileGameGeometry" not in body:
        # allow either C or ObjC naming if clearly a reconcile helper
        if not re.search(r"reconcile[_A-Za-z]*[Gg]eometr", body):
            return "init.mm missing reconcile_game_geometry helper"

    # Production present must not stop on GEOMETRY_STALE.
    present_fn = re.search(
        r"int\s+eu4_gfx_present\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not present_fn:
        return "eu4_gfx_present definition not found"
    present_body = present_fn.group("body")
    if "EU4_GFX_CLEAR_GEOMETRY_STALE" not in present_body:
        return "production present missing GEOMETRY_STALE branch"
    if "eu4_gfx_stop" in present_body:
        # stop allowed only for non-stale paths; require stale returns before stop
        stale_idx = present_body.find("EU4_GFX_CLEAR_GEOMETRY_STALE")
        stop_idx = present_body.find("eu4_gfx_stop")
        if stop_idx < stale_idx:
            return "production present appears to stop before GEOMETRY_STALE handling"

    clear_fn = re.search(
        r"void\s+eu4_gfx_clear\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not clear_fn:
        return "eu4_gfx_clear definition not found"
    clear_body = clear_fn.group("body")
    if "EU4_GFX_CLEAR_GEOMETRY_STALE" not in clear_body:
        return "production clear missing GEOMETRY_STALE branch"

    # Present must call reconcile (or equivalent) — not only SetRT/clear.
    present_checked = re.search(
        r"eu4_gfx_clear_status\s+eu4_gfx_present_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not present_checked:
        return "eu4_gfx_present_checked definition not found"
    pc = present_checked.group("body")
    if not re.search(r"reconcile[_A-Za-z]*[Gg]eometr", pc):
        return "present_checked missing geometry reconcile call"

    if "drawableSize" not in boot:
        return "metal_boot missing drawableSize updates"
    if "viewDidChangeBackingProperties" not in boot and "backing" not in boot.lower():
        return "metal_boot missing backing-scale observation"
    if "autoresizingMask" not in boot and "NSViewWidthSizable" not in boot:
        # allow explicit layout path
        if "setFrame" not in boot and "frame =" not in boot:
            return "metal_boot missing child-view resize / autoresizing path"

    contract = workspace / "analysis" / "gfx-render" / "v0" / "hidpi-contract.md"
    if not contract.is_file():
        return "hidpi-contract.md missing"
    contract_text = contract.read_text(encoding="utf-8")
    for needle in (
        "GEOMETRY_STALE",
        "reconcile",
        "logical",
        "physical",
        "generation",
        "Present",
    ):
        if needle.lower() not in contract_text.lower() and needle not in contract_text:
            return f"hidpi-contract missing topic: {needle}"
    return None


def hidpi_host_gates(host: str) -> str | None:
    stripped = host.replace("eu4_gfx_clear_checked", "CHECKED_CLEAR")
    stripped = stripped.replace("eu4_gfx_present_checked", "CHECKED_PRESENT")
    stripped = stripped.replace("eu4_gfx_set_render_target_checked", "CHECKED_SET")
    for name, pattern in (
        ("production clear", r"eu4_gfx_clear\s*\("),
        ("production present", r"eu4_gfx_present\s*\("),
        ("production set_render_target", r"eu4_gfx_set_render_target\s*\("),
    ):
        if not re.search(pattern, stripped):
            return f"hidpi host missing {name} call"

    lowered = host.lower()
    markers = (
        "geometry_stale",
        "1x",
        "2x",
        "scale",
        "logical",
        "generation",
        "pending",
        "nextDrawable" if "nextDrawable" in host else "nextdrawable",
    )
    # nextDrawable may be via selector; accept either
    if "nextdrawable" not in lowered and "cametaldrawable" not in lowered:
        # real-drawable may be gated; still require a marker string for the path
        if "real_drawable" not in lowered and "real-drawable" not in lowered:
            return "hidpi host missing real-drawable / CAMetalDrawable coverage marker"
    needed = ("geometry_stale", "2x", "logical", "pending")
    if not all(m in lowered for m in needed):
        missing = [m for m in needed if m not in lowered]
        return "hidpi host missing scenario markers: " + ", ".join(missing)
    return None


def run_tests(workspace: Path) -> str | None:
    if sys.platform != "darwin":
        return "unavailable: P5.1.1 acceptance requires macOS Metal"
    workspace = Path(workspace).resolve()
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
        "if result.unexpectedSuccesses and not detail:\n"
        " detail='unexpected success'\n"
        "json.dump({'file':module.__file__,'ran':result.testsRun,"
        "'skipped':len(result.skipped),'failed':len(failed),"
        "'successful':result.wasSuccessful(),'detail':detail},sys.stdout)\n"
    )
    for name in TEST_MODULES:
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
            return f"unavailable: candidate tests failed to run: {exc}"
        try:
            payload = json.loads(ran.stdout)
        except json.JSONDecodeError:
            detail = (ran.stderr or ran.stdout or "tests produced no result").strip()
            kind = "fail: " if ran.returncode else "unavailable: "
            return kind + detail[:2000]
        loaded = Path(str(payload.get("file", ""))).resolve()
        if workspace not in loaded.parents:
            return "fail: candidate tests did not load from the candidate worktree"
        ran_count = int(payload.get("ran") or 0) - int(payload.get("skipped") or 0)
        if ran_count < 1:
            return f"unavailable: {name} did not run a test"
        if (
            ran.returncode != 0
            or int(payload.get("failed") or 0)
            or payload.get("successful") is not True
        ):
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

    note = workspace / "analysis" / "automation" / "eu4-p511-hidpi" / "note.md"
    try:
        dirty = changed_paths(workspace)
    except OSError as exc:
        return emit("unavailable", str(exc))
    extra = [path for path in dirty if path not in ALLOWED]
    if extra:
        return emit("fail", "edits outside allowlist: " + ", ".join(extra[:12]))

    blocker = note_blocker(note) if note.is_file() else None
    impl_touched = any(
        path.startswith("benchmark/") or path.startswith("tests/") for path in dirty
    )
    if blocker and not impl_touched:
        return emit("blocked", blocker)
    if blocker and impl_touched:
        return emit("fail", f"BLOCKER present with implementation edits: {blocker}")

    if not note.is_file():
        return emit("fail", "research note missing")
    note_text = note.read_text(encoding="utf-8")
    if require_substrings(
        note_text,
        ["Project-derived"] if "SDK" not in note_text and "Unresolved" not in note_text else [],
        "note",
    ):
        if "Project-derived" not in note_text and "SDK" not in note_text and "Unresolved" not in note_text:
            return emit("fail", "note missing provenance labels")
    if "GEOMETRY_STALE" not in note_text and "geometry" not in note_text.lower():
        return emit("fail", "note missing geometry-transition discussion")

    if not dirty:
        return emit("fail", "no HiDPI edits yet")

    gate = source_gates(workspace)
    if gate:
        return emit("fail", gate)

    hidpi_host = workspace / "tests" / "gfx_hidpi_host.mm"
    hidpi_py = workspace / "tests" / "test_gfx_hidpi.py"
    if not hidpi_host.is_file() or not hidpi_py.is_file():
        return emit("fail", "tests/gfx_hidpi_host.mm or tests/test_gfx_hidpi.py missing")
    host_gate = hidpi_host_gates(hidpi_host.read_text(encoding="utf-8"))
    if host_gate:
        return emit("fail", host_gate)

    test_problem = run_tests(workspace)
    if test_problem:
        if test_problem.startswith("unavailable:"):
            return emit("unavailable", test_problem.removeprefix("unavailable: ").strip())
        return emit("fail", test_problem.removeprefix("fail: ").strip())
    return emit("pass", "p511 hidpi gates held")


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
