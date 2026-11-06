#!/usr/bin/env python3
"""P5.1.1 lifecycle correction acceptance (post-#163). Task-specific."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PIN = "4ff4addc29d8e0a0ddfde05f1dd0746f7abccd79"
ALLOWED = {
    "analysis/automation/eu4-p511-lifecycle/note.md",
    "analysis/automation/eu4-p511-lifecycle/progress.md",
    "progress.md",
    "analysis/gfx-render/v0/hidpi-contract.md",
    "analysis/gfx-render/v0/default-target-contract.md",
    "benchmark/eu4_gfx_init.h",
    "benchmark/eu4_gfx_init.mm",
    "benchmark/eu4_metal_boot.m",
    "benchmark/eu4_metal_geometry_view.h",
    "benchmark/eu4_metal_geometry_view.m",
    "tests/gfx_hidpi_host.mm",
    "tests/test_gfx_hidpi.py",
    "tests/gfx_hidpi_appkit_host.mm",
    "tests/test_gfx_hidpi_appkit.py",
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


def source_gates(workspace: Path) -> str | None:
    body = (workspace / "benchmark" / "eu4_gfx_init.mm").read_text(encoding="utf-8")
    header = (workspace / "benchmark" / "eu4_gfx_init.h").read_text(encoding="utf-8")
    boot = (workspace / "benchmark" / "eu4_metal_boot.m").read_text(encoding="utf-8")
    contract = workspace / "analysis" / "gfx-render" / "v0" / "hidpi-contract.md"
    if not contract.is_file():
        return "hidpi-contract.md missing"
    contract_text = contract.read_text(encoding="utf-8")

    if "pending_is_obsolete" not in body:
        return "init.mm missing pending_is_obsolete"
    if "intern_generation" in body or "_seenGeometry" in body:
        return "intern_generation / _seenGeometry must be removed"
    if re.search(r"1x\s*->\s*2x\s*->\s*1x.*identit|recovers its generation|generation identity",
                 contract_text, re.I):
        return "hidpi-contract still claims generation identity reclaim"
    if "monotonic" not in contract_text.lower() and "monotonically" not in contract_text.lower():
        if "geometryGeneration +=" not in contract_text and "G_t+1" not in contract_text:
            if "strictly increasing" not in contract_text.lower():
                return "hidpi-contract missing monotonic generation policy"

    # Two-phase publication API markers
    two_phase = (
        "transition" in body.lower()
        and (
            "in_progress" in body.lower()
            or "inProgress" in body
            or "geometry_transition" in body.lower()
            or "commit" in body.lower()
        )
    )
    if not two_phase and "eu4_gfx_publish_physical_geometry" in body:
        # allow dedicated begin/commit APIs
        if "begin" not in body.lower() and "commit" not in body.lower():
            return "init.mm missing two-phase geometry transition publication"
    pub_apis = (
        "eu4_gfx_begin_physical_geometry_transition" in header
        or "eu4_gfx_commit_physical_geometry" in header
        or ("transition" in header.lower() and "physical" in header.lower())
        or "eu4_gfx_publish_physical_geometry" in header
    )
    if not pub_apis:
        return "header missing physical geometry publication / transition API"

    set_rt = re.search(
        r"eu4_gfx_clear_status\s+eu4_gfx_set_render_target_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not set_rt:
        return "set_render_target_checked not found"
    if "pending_is_obsolete" not in set_rt.group("body"):
        return "SetRT missing pending_is_obsolete drain path"

    clear_game = re.search(
        r"static\s+eu4_gfx_clear_status\s+clear_game_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not clear_game:
        return "clear_game_checked not found"
    cg = clear_game.group("body")
    if "pending_is_obsolete" not in cg:
        return "clear_game_checked missing pending_is_obsolete"
    if "GEOMETRY_STALE" not in cg:
        return "clear_game_checked missing GEOMETRY_STALE"

    present = re.search(
        r"eu4_gfx_clear_status\s+eu4_gfx_present_checked\s*\([^)]*\)\s*\{(?P<body>.*?)^\}",
        body,
        re.M | re.S,
    )
    if not present:
        return "present_checked not found"
    pc = present.group("body")
    if "GEOMETRY_STALE" not in pc:
        return "present_checked missing GEOMETRY_STALE"

    # Production view still present in boot or extracted shared file
    geom = workspace / "benchmark" / "eu4_metal_geometry_view.m"
    view_src = geom.read_text(encoding="utf-8") if geom.is_file() else boot
    if "EU4MetalChildView" not in view_src and "EU4MetalChildView" not in boot:
        return "production EU4MetalChildView missing from boot/shared view"
    if "syncDrawableGeometry" not in view_src and "syncDrawableGeometry" not in boot:
        return "production syncDrawableGeometry missing"

    return None


def host_scenario_gates(host: str) -> str | None:
    lowered = host.lower()
    needed = (
        "pending_is_obsolete",
        "geometry_stale",
        "a_to_b_to_a" if "a_to_b_to_a" in lowered else "aba",
        "setrt",
        "monotonic",
    )
    # flexible markers
    markers_ok = (
        ("pending" in lowered and "obsolete" in lowered)
        or "pending_is_obsolete" in lowered
        or "obsolete pending" in lowered
    )
    if not markers_ok and "pending" not in lowered:
        return "hidpi host missing obsolete-pending scenario"
    if "geometry_stale" not in lowered and "geometry_stale" not in host:
        if "EU4_GFX_CLEAR_GEOMETRY_STALE" not in host:
            return "hidpi host missing GEOMETRY_STALE coverage"
    if not any(x in lowered for x in ("a_to_b_to_a", "aba", "1x", "generation")):
        return "hidpi host missing A→B→A / generation scenario"
    if "set_render_target" not in host and "setrt" not in lowered:
        return "hidpi host missing SetRT recovery scenario"
    if "interleav" not in lowered and "in_progress" not in lowered and "two.phase" not in lowered.replace("_", "."):
        if "transition" not in lowered:
            return "hidpi host missing two-phase / interleaving race coverage"
    return None


def production_view_linked(workspace: Path) -> str | None:
    """Require AppKit host (or hidpi host) to reference production view class/symbol."""
    candidates = [
        workspace / "tests" / "gfx_hidpi_appkit_host.mm",
        workspace / "tests" / "gfx_hidpi_host.mm",
    ]
    texts = [p.read_text(encoding="utf-8") for p in candidates if p.is_file()]
    if not texts:
        return "missing hidpi/appkit host"
    joined = "\n".join(texts)
    if "EU4MetalChildView" not in joined:
        return "hosts do not reference production EU4MetalChildView"
    if "NSWindow" not in joined:
        return "AppKit host missing NSWindow"
    # Prefer dedicated appkit host for window-backed real drawable
    appkit = workspace / "tests" / "gfx_hidpi_appkit_host.mm"
    if appkit.is_file():
        t = appkit.read_text(encoding="utf-8")
        if "nextDrawable" not in t and "CAMetalDrawable" not in t:
            return "appkit host missing real drawable path"
    elif "NSWindow" not in (workspace / "tests" / "gfx_hidpi_host.mm").read_text(encoding="utf-8"):
        return "no window-backed AppKit host"
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
        return f"unavailable: candidate tests failed to run: {exc}", None
    try:
        payload = json.loads(ran.stdout)
    except json.JSONDecodeError:
        detail = (ran.stderr or ran.stdout or "tests produced no result").strip()
        kind = "fail: " if ran.returncode else "unavailable: "
        return kind + detail[:2000], None
    loaded = Path(str(payload.get("file", ""))).resolve()
    if workspace not in loaded.parents:
        return "fail: candidate tests did not load from the candidate worktree", None
    return None, payload


def run_real_drawable_gate(workspace: Path) -> str | None:
    """Individually mandatory window-backed real drawable; 77/skip => unavailable."""
    # Prefer dedicated appkit module if present
    modules = []
    if (workspace / "tests" / "test_gfx_hidpi_appkit.py").is_file():
        modules.append("tests.test_gfx_hidpi_appkit")
    modules.append("tests.test_gfx_hidpi")

    for name in modules:
        err, payload = run_module(workspace, name)
        if err:
            if err.startswith("unavailable:"):
                return err
            # module missing load is fail for appkit if expected
            if name.endswith("appkit") and "No module" in (err or ""):
                continue
            if name.endswith("appkit"):
                return err
            continue
        assert payload is not None
        skips = [s.lower() for s in (payload.get("skips") or [])]
        # If this module has a real/window test that skipped → unavailable
        real_skip = any(
            "real" in s or "appkit" in s or "drawable" in s or "window" in s for s in skips
        )
        if real_skip and name.endswith("appkit"):
            return "unavailable: window-backed real drawable skipped"
        if name.endswith("appkit"):
            ran_count = int(payload.get("ran") or 0) - int(payload.get("skipped") or 0)
            if ran_count < 1:
                return "unavailable: window-backed AppKit tests did not run"
            if (
                int(payload.get("failed") or 0)
                or payload.get("successful") is not True
            ):
                return f"fail: {name}: {payload.get('detail') or ''}"[:2000]
            return None  # appkit module ran successfully

    # Fallback: invoke host binary real/appkit mode directly if python split absent
    host = workspace / "benchmark" / ".build" / "gfx_hidpi_appkit_host"
    alt = workspace / "benchmark" / ".build" / "gfx_hidpi_host"
    # Ensure tests built via unittest first
    err, payload = run_module(workspace, "tests.test_gfx_hidpi")
    if err and err.startswith("unavailable:"):
        return err
    # Look for skipped real drawable in hidpi module without appkit
    if payload:
        skips = [s.lower() for s in (payload.get("skips") or [])]
        if any("real" in s or "drawable" in s for s in skips):
            # Without a separate non-skipped appkit module, this is unavailable
            if not (workspace / "tests" / "test_gfx_hidpi_appkit.py").is_file():
                return "unavailable: real drawable skipped without window-backed AppKit module"
    # Require appkit module for pass
    if not (workspace / "tests" / "test_gfx_hidpi_appkit.py").is_file():
        return "fail: tests/test_gfx_hidpi_appkit.py missing (mandatory window-backed gate)"
    return "fail: window-backed AppKit gate did not run"


def run_tests(workspace: Path) -> str | None:
    if sys.platform != "darwin":
        return "unavailable: P5.1.1 lifecycle acceptance requires macOS Metal"
    workspace = Path(workspace).resolve()

    real = run_real_drawable_gate(workspace)
    if real:
        return real

    modules = list(TEST_MODULES)
    if (workspace / "tests" / "test_gfx_hidpi_appkit.py").is_file():
        modules = ("tests.test_gfx_hidpi_appkit",) + tuple(
            m for m in TEST_MODULES if m != "tests.test_gfx_hidpi_appkit"
        )

    for name in modules:
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

    note = workspace / "analysis" / "automation" / "eu4-p511-lifecycle" / "note.md"
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
    if (
        "Project-derived" not in note_text
        and "SDK" not in note_text
        and "Unresolved" not in note_text
    ):
        return emit("fail", "note missing provenance labels")
    if "pending_is_obsolete" not in note_text and "obsolete" not in note_text.lower():
        return emit("fail", "note missing obsolete-pending discussion")

    if not dirty:
        return emit("fail", "no lifecycle edits yet")

    gate = source_gates(workspace)
    if gate:
        return emit("fail", gate)

    hidpi_host = workspace / "tests" / "gfx_hidpi_host.mm"
    if not hidpi_host.is_file():
        return emit("fail", "tests/gfx_hidpi_host.mm missing")
    host_gate = host_scenario_gates(hidpi_host.read_text(encoding="utf-8"))
    if host_gate:
        # also allow scenarios in appkit host
        appkit = workspace / "tests" / "gfx_hidpi_appkit_host.mm"
        if appkit.is_file():
            combined = hidpi_host.read_text(encoding="utf-8") + "\n" + appkit.read_text(
                encoding="utf-8"
            )
            host_gate = host_scenario_gates(combined)
        if host_gate:
            return emit("fail", host_gate)

    view_gate = production_view_linked(workspace)
    if view_gate:
        return emit("fail", view_gate)

    test_problem = run_tests(workspace)
    if test_problem:
        if test_problem.startswith("unavailable:"):
            return emit("unavailable", test_problem.removeprefix("unavailable: ").strip())
        return emit("fail", test_problem.removeprefix("fail: ").strip())
    return emit("pass", "p511 lifecycle gates held")


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
