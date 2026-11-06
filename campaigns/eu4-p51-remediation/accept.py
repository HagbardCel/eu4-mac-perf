#!/usr/bin/env python3
"""P5.1 remediation acceptance for PR #162. Task-specific; not reconstruction_accept."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PIN = "407d515663f8d90a6b20f2c159a9c2afc26cf385"
ALLOWED = {
    "analysis/automation/eu4-p51-remediation/note.md",
    "progress.md",
    "benchmark/eu4_gfx_init.h",
    "benchmark/eu4_gfx_init.mm",
    "benchmark/eu4_gfx_entries.def",
    "benchmark/eu4_gfx_entry_patch.h",
    "benchmark/eu4_gfx_entry_patch.c",
    "tests/gfx_default_target_host.mm",
    "tests/test_gfx_default_target.py",
    "tests/test_gfx_entry_registry.py",
    "tests/metal_boot_host.c",
    "analysis/gfx-render/v0/default-target-contract.md",
}
TEST_MODULES = (
    "tests.test_gfx_default_target",
    "tests.test_gfx_init",
    "tests.test_gfx_buffers",
    "tests.test_gfx_shadow_render",
    "tests.test_gfx_shadow_worker",
    "tests.test_gfx_startup_diff",
    "tests.test_gfx_startup_diff_metal",
    "tests.test_gfx_entry_registry",
)
# Complete Present prologue from the pinned x86_64 eu4 image (verified offline).
PRESENT_COMPLETE = bytes.fromhex("554889e5488bbf78010000")
PRESENT_BAD = bytes.fromhex("554889e5488b")
PRESENT_ENTRY = 0x15EA6CB
PRESENT_END = 0x15EA6DF
_GFX_ENTRY = re.compile(
    r'^GFX_ENTRY\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*'
    r'(0x[0-9a-fA-F]+)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"((?:\\x[0-9a-fA-F]{2})+)"\s*\)\s*$'
)


def trusted_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def _git(path: Path, *args: str, binary: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.decode().strip() or "git failed")
    if binary:
        return completed.stdout
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


def decode_prefix(encoded: str) -> bytes:
    parts = re.findall(r"\\x([0-9a-fA-F]{2})", encoded)
    return bytes(int(p, 16) for p in parts)


def parse_entries(text: str) -> list[tuple]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        match = _GFX_ENTRY.fullmatch(line)
        if not match:
            raise ValueError(f"bad GFX_ENTRY: {line}")
        name, entry, end, fn, prefix = match.groups()
        out.append((name, int(entry, 16), int(end, 16), fn, decode_prefix(prefix)))
    return out


def aligned(complete: bytes, prefix: bytes) -> bool:
    return complete.startswith(prefix) and len(prefix) >= 5 and len(complete) >= len(prefix) and (
        len(prefix) == len(complete) or _boundary(complete, len(prefix))
    )


def _boundary(blob: bytes, length: int) -> bool:
    # Present's only complete lengths under 16 are 1, 4, 11.
    ends = {1, 4, 11}
    return length in ends and blob[:length] == PRESENT_COMPLETE[:length]


def present_prefix_problem(entries: list[tuple]) -> str | None:
    found = [item for item in entries if item[0] == "present"]
    if len(found) != 1:
        return "missing or duplicate present registration"
    name, entry, end, fn, prefix = found[0]
    if entry != PRESENT_ENTRY or end != PRESENT_END:
        return "present span does not match pinned body"
    if fn != "eu4_gfx_present":
        return "present replacement symbol changed"
    if prefix == PRESENT_BAD:
        return "present prefix ends mid-instruction"
    if prefix != PRESENT_COMPLETE:
        return "present prefix is not the complete 11-byte instruction prologue"
    return None


def note_blocker(note: Path) -> str | None:
    if not note.is_file():
        return None
    for line in note.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("BLOCKER:"):
            return line.strip()
    return None


def source_gates(workspace: Path) -> str | None:
    text = (workspace / "benchmark" / "eu4_gfx_init.mm").read_text(encoding="utf-8")
    # Fail closed: do not accept destination larger than the game backbuffer.
    if re.search(
        r"destination\.width\s*<\s*backend\.gameWidth|destination\.height\s*<\s*backend\.gameHeight",
        text,
    ):
        return "present still accepts oversized destinations"
    if "pixelFormat" not in text or "MTLPixelFormatBGRA8Unorm" not in text:
        return "present missing destination pixel-format gate"
    if "gameWidth" not in text or "gameHeight" not in text:
        return "present missing game dimension checks"
    # Exact match required for the frozen fail-closed policy.
    if not re.search(
        r"destination\.width\s*!=\s*backend\.gameWidth|destination\.width\s*==\s*backend\.gameWidth",
        text,
    ):
        return "present missing exact width comparison"
    contract = workspace / "analysis" / "gfx-render" / "v0" / "default-target-contract.md"
    if not contract.is_file():
        return "default-target contract missing"
    contract_text = contract.read_text(encoding="utf-8")
    if "drawable" not in contract_text.lower() and "dimension" not in contract_text.lower():
        return "contract missing dimension/present coverage policy"
    if "injected" not in contract_text.lower() and "CAMetalDrawable" not in contract_text:
        return "contract missing injected-drawable limitation"
    patch_h = (workspace / "benchmark" / "eu4_gfx_entry_patch.h").read_text(encoding="utf-8")
    entries = parse_entries((workspace / "benchmark" / "eu4_gfx_entries.def").read_text())
    present = next(item for item in entries if item[0] == "present")
    if len(present[4]) > 8:
        if "11" not in patch_h and "16" not in patch_h:
            # allow if .c raises the bound even when header comment lags
            patch_c = (workspace / "benchmark" / "eu4_gfx_entry_patch.c").read_text(encoding="utf-8")
            if "length > 11" not in patch_c and "length > 16" not in patch_c and "> 8" in patch_c:
                if not re.search(r"length\s*>\s*1[1-6]", patch_c):
                    return "present prefix exceeds patcher max without a justified raise"
    return None


def production_wrapper_ok(host: str) -> str | None:
    stripped = host.replace("eu4_gfx_set_render_target_checked", "CHECKED_SET")
    stripped = stripped.replace("eu4_gfx_present_checked", "CHECKED_PRESENT")
    if not re.search(r"eu4_gfx_set_render_target\s*\(", stripped):
        return "host missing production eu4_gfx_set_render_target call"
    if not re.search(r"eu4_gfx_present\s*\(", stripped):
        return "host missing production eu4_gfx_present call"
    return None


def failure_coverage_ok(host: str) -> str | None:
    lowered = host.lower()
    markers = ("oversize", "unequal", "larger", "bad_format", "rgba8", "wrong format", "format seam")
    if not any(m in lowered for m in markers):
        return "host missing size/format failure coverage"
    return None


def run_tests(workspace: Path) -> str | None:
    env = os.environ.copy()
    env["MTL_DEBUG_LAYER"] = "1"
    env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    env["PYTHONPATH"] = str(workspace / "benchmark") + os.pathsep + env.get("PYTHONPATH", "")
    ran = subprocess.run(
        [sys.executable, "-m", "unittest", *TEST_MODULES],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if ran.returncode != 0:
        tail = (ran.stderr or ran.stdout or "tests failed")[-4000:]
        return tail
    # Require evidence of a real native host run (gfx_default_target).
    if "test_default_target" not in (ran.stderr + ran.stdout) and "OK" not in (ran.stderr + ran.stdout):
        return "unittest produced no usable result"
    if "skipped=8" in (ran.stderr + ran.stdout) and "Ran 0" in (ran.stderr + ran.stdout):
        return "all tests skipped"
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

    note = workspace / "analysis" / "automation" / "eu4-p51-remediation" / "note.md"
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
    if "_GfxPresent" not in note_text or "0x15ea6cb" not in note_text.lower() and "0x15EA6CB" not in note_text:
        if "15ea6cb" not in note_text.lower():
            return emit("fail", "note missing Present entry evidence")
    if "Project-derived" not in note_text and "SDK" not in note_text and "Unresolved" not in note_text:
        return emit("fail", "note missing provenance labels")

    try:
        entries = parse_entries((workspace / "benchmark" / "eu4_gfx_entries.def").read_text())
    except ValueError as exc:
        return emit("fail", str(exc))
    prefix_problem = present_prefix_problem(entries)
    if prefix_problem:
        return emit("fail", prefix_problem)

    gate = source_gates(workspace)
    if gate:
        return emit("fail", gate)
    host = (workspace / "tests" / "gfx_default_target_host.mm").read_text(encoding="utf-8")
    wrap = production_wrapper_ok(host)
    if wrap:
        return emit("fail", wrap)
    fail_cov = failure_coverage_ok(host)
    if fail_cov:
        return emit("fail", fail_cov)

    if not dirty:
        return emit("fail", "no remediation edits yet")

    test_problem = run_tests(workspace)
    if test_problem:
        return emit("fail", test_problem)
    return emit("pass", "p51 remediation gates held")


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
