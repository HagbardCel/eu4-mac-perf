#!/usr/bin/env python3
"""Compile the trusted index-buffer host against one candidate worktree.

The candidate header is the only buffer header on the include path. Init,
events, the host, and this checker stay in the trusted checkout.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ALLOWED = {
    "benchmark/eu4_gfx_buffers.mm",
    "benchmark/eu4_gfx_buffers.h",
    "benchmark/eu4_gfx_entries.def",
    "progress.md",
}
TRUSTED_SHA256 = {
    "analysis/metal-init/index-buffers/note.md": "7f41abf232cbb1b6b2bc7edba7d90978c41fc28c6adba81329f33f77f157dcde",
    "tests/gfx_buffers_host.mm": "a0efaadb5bc668e6536b541cfde68eda09d9a78fd5897b2aa201fb0f82a7abb1",
    "tests/test_gfx_buffers.py": "9145f289a2abfc5075648946fbb40d2a95e70e3b77d503c621f2c7c41c04fa6f",
    "campaigns/eu4-index-buffer/entry_probe.c": "9a19c14b4c684c5481e18ccb63e12aa674c1d7039921ec0ce98611d190c76914",
}
REQUIRED = (
    ("_GfxCreateIndexBuffer", 0x15EC154, 0x15EC1E6, "eu4_gfx_create_index_buffer",
     bytes.fromhex("554889e54157")),
    ("_GfxDestroyIndexBuffer", 0x15EC1E6, 0x15EC23C, "eu4_gfx_destroy_index_buffer",
     bytes.fromhex("4885ff7450")),
    ("_GfxUpdateIndexBuffer", 0x15EC23C, 0x15EC28E, "eu4_gfx_update_index_buffer",
     bytes.fromhex("554889e54156")),
    ("_GfxSetIndexBuffer", 0x15EC28E, 0x15EC2BC, "eu4_gfx_set_index_buffer",
     bytes.fromhex("4885f67428")),
)
INDEX_SYMBOLS = (
    "eu4_gfx_create_index_buffer",
    "eu4_gfx_destroy_index_buffer",
    "eu4_gfx_update_index_buffer",
    "eu4_gfx_set_index_buffer",
    "eu4_gfx_index_records",
    "eu4_gfx_index_gpus",
    "eu4_gfx_index_buffer_length",
    "eu4_gfx_index_buffer_read",
    "eu4_gfx_index_buffer_shared",
    "eu4_gfx_current_index_header",
)
HEADER_MARKER = "eu4-candidate-header-observed"
PROBE_SOURCE = """
#import "eu4_gfx_buffers.h"
int eu4_candidate_header_probe(void) { return EU4_GFX_VB_COUNT_RVA; }
"""


def trusted_root() -> Path:
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def note_path() -> Path:
    return trusted_root() / "analysis/metal-init/index-buffers/note.md"


def include_dirs(candidate_benchmark: Path) -> list[Path]:
    """Candidate buffers header first, then the trusted benchmark for init and events."""
    return [candidate_benchmark, trusted_root() / "benchmark"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trusted_files_match() -> str | None:
    root = trusted_root()
    for rel, expected in TRUSTED_SHA256.items():
        path = root / rel
        if not path.is_file():
            return f"trusted file missing: {rel}"
        if _sha256(path) != expected:
            return f"trusted file edited: {rel}"
    return None


def _blocked(entry: str, workspace: Path) -> bool:
    if not entry:
        return True
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return True
    return resolved == workspace or workspace in resolved.parents


def import_parser(workspace: Path):
    """Load the trusted entry parser. Never delete sys.modules."""
    root = trusted_root()
    workspace = workspace.resolve()
    expected = (root / "benchmark" / "eu4_metal_boot.py").resolve()
    loaded = sys.modules.get("eu4_metal_boot")
    if loaded is not None:
        origin = Path(getattr(loaded, "__file__", "")).resolve()
        if origin != expected or workspace in origin.parents:
            raise RuntimeError(f"untrusted module origin: {origin}")
        return loaded
    saved = sys.path
    try:
        sys.path = [
            str(root / "benchmark"),
            *[entry for entry in saved if not _blocked(entry, workspace)],
        ]
        import eu4_metal_boot as boot
        origin = Path(boot.__file__).resolve()
        if origin != expected or workspace in origin.parents:
            raise RuntimeError(f"untrusted module origin: {origin}")
        return boot
    finally:
        sys.path = saved


def registry_problems(candidate_text: str, trusted_text: str, parse) -> str | None:
    try:
        candidate = parse(candidate_text)
        trusted = parse(trusted_text)
    except ValueError as exc:
        return f"candidate registry is invalid: {exc}"
    index_names = {item[0] for item in REQUIRED}
    remaining = [item for item in candidate if item[0] not in index_names]
    trusted_rest = [item for item in trusted if item[0] not in index_names]
    if remaining != trusted_rest:
        return "pre-existing gfx entry changed"
    missing = [item[0] for item in REQUIRED if item not in candidate]
    if missing:
        return "missing index registration: " + ", ".join(missing)
    extra = [item[0] for item in candidate if item not in trusted and item not in REQUIRED]
    if extra:
        return "unexpected gfx entry: " + ", ".join(extra)
    return None


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True)
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "git failed")
    return completed.stdout.strip()


def _common_dir(path: Path) -> Path:
    raw = _git(path, "rev-parse", "--git-common-dir")
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
        if "R" in status or "C" in status:
            if index < len(parts) and parts[index]:
                found.append(parts[index].decode())
                index += 1
    return found


def _compiler() -> str | None:
    return shutil.which("clang")


def _metal_sdk() -> bool:
    return sys.platform == "darwin" and Path("/System/Library/Frameworks/Metal.framework").is_dir()


def _clang(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=300)


def header_edit_observed(candidate_header: Path) -> tuple[bool, str]:
    """A copy of the candidate header is what the acceptance include path compiles."""
    if not candidate_header.is_file():
        return False, "candidate header is missing"
    temporary = Path(tempfile.mkdtemp(prefix="eu4-ib-header-"))
    try:
        mutated = temporary / "eu4_gfx_buffers.h"
        mutated.write_text(f"#error {HEADER_MARKER}\n" + candidate_header.read_text(), encoding="utf-8")
        probe = temporary / "probe.m"
        probe.write_text(PROBE_SOURCE, encoding="utf-8")
        command = [
            "clang", "-arch", "x86_64", "-fsyntax-only", "-Werror", "-fobjc-arc",
            *[f"-I{path}" for path in include_dirs(temporary)],
            str(probe),
        ]
        completed = _clang(command)
        observed = HEADER_MARKER in (completed.stderr or "")
        if not observed:
            return False, "acceptance build did not observe the candidate header"
        return True, ""
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _flags(include: list[Path]) -> list[str]:
    trusted = trusted_root() / "benchmark"
    return [
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
        "-include", str(trusted / "eu4_gfx_init.h"),
        "-include", str(trusted / "eu4_metal_events.h"),
        *[f"-I{path}" for path in include],
        "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
    ]


def _missing_symbols(text: str) -> bool:
    return any(symbol in text for symbol in INDEX_SYMBOLS) and (
        "undefined" in text or "Undefined" in text
    )


def _prefixes(entries) -> str:
    by_name = {item[0]: item for item in entries}
    lines = []
    for index, spec in enumerate(REQUIRED):
        prefix = by_name[spec[0]][4]
        body = ", ".join(f"0x{byte:02x}" for byte in prefix)
        lines.append(f"static const uint8_t kPrefix{index}[] = {{{body}}};")
        lines.append(f"static const int kPrefixLen{index} = sizeof kPrefix{index};")
    return "\n".join(lines) + "\n"


def _pinned() -> tuple[str, Path]:
    data = json.loads((Path(__file__).resolve().parent / "campaign.json").read_text())
    return str(data["workspace"]["commit"]), trusted_root()


def check(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    if not _metal_sdk():
        return emit("unavailable", "no Metal SDK")
    if _compiler() is None:
        return emit("unavailable", "no C compiler on PATH")
    edited = trusted_files_match()
    if edited:
        return emit("fail", edited)
    note = note_path()
    visible = subprocess.run(["test", "-r", str(note)], cwd=workspace, check=False)
    if visible.returncode != 0:
        return emit("unavailable", "trusted index-buffer note is not readable")
    commit, repo = _pinned()
    try:
        if _git(workspace, "rev-parse", "HEAD") != commit:
            return emit("fail", "worktree HEAD is not the pinned commit")
        if _common_dir(workspace) != _common_dir(repo):
            return emit("fail", "worktree is not this repository")
        illegal = [item for item in changed_paths(workspace) if item not in ALLOWED]
    except OSError as exc:
        return emit("unavailable", f"worktree unavailable: {exc}")
    if illegal:
        return emit("fail", "source scope violated: " + ", ".join(illegal[:8]))
    header = workspace / "benchmark" / "eu4_gfx_buffers.h"
    observed, detail = header_edit_observed(header)
    if not observed:
        return emit("fail", detail)
    try:
        parser = import_parser(workspace)
        problem = registry_problems(
            (workspace / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8"),
            (repo / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8"),
            parser.parse_gfx_entries,
        )
    except (OSError, RuntimeError, ImportError) as exc:
        return emit("unavailable", f"trusted parser unavailable: {exc}")
    if problem:
        return emit("fail", problem)
    temporary = Path(tempfile.mkdtemp(prefix="eu4-ib-accept-"))
    try:
        host = _build_host(workspace, temporary)
        if host is not None:
            return host
        return _run_probe(workspace, temporary, parser.parse_gfx_entries)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _build_host(workspace: Path, temporary: Path) -> dict | None:
    root = trusted_root()
    output = temporary / "gfx_buffers_host"
    command = [
        *_flags(include_dirs(workspace / "benchmark")),
        "-o", str(output),
        str(root / "tests" / "gfx_buffers_host.mm"),
        str(root / "benchmark" / "eu4_gfx_init.mm"),
        str(workspace / "benchmark" / "eu4_gfx_buffers.mm"),
        str(root / "benchmark" / "eu4_metal_events.c"),
    ]
    try:
        built = _clang(command)
    except (OSError, subprocess.SubprocessError) as exc:
        return emit("fail", f"index host failed to compile: {exc}")
    if built.returncode != 0:
        detail = (built.stderr or built.stdout or "compile failed").strip()
        if _missing_symbols(detail):
            return emit("fail", "missing index implementation")
        return emit("fail", "index host failed to compile: " + detail[:2000])
    try:
        ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return emit("fail", f"index host failed to run: {exc}")
    if ran.returncode != 0:
        detail = (ran.stderr or ran.stdout or "index host failed").strip()
        return emit("fail", detail[:2000])
    return None


def _run_probe(workspace: Path, temporary: Path, parse) -> dict:
    root = trusted_root()
    entries = parse((workspace / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8"))
    (temporary / "prefixes.h").write_text(_prefixes(entries), encoding="utf-8")
    output = temporary / "entry_probe"
    command = [
        *_flags([temporary, *include_dirs(workspace / "benchmark")]),
        "-o", str(output),
        str(root / "campaigns" / "eu4-index-buffer" / "entry_probe.c"),
        str(workspace / "benchmark" / "eu4_gfx_buffers.mm"),
        str(root / "benchmark" / "eu4_gfx_init.mm"),
        str(root / "benchmark" / "eu4_metal_events.c"),
        str(root / "benchmark" / "eu4_gfx_entry_patch.c"),
        str(root / "benchmark" / "eu4_metal_boot_patch.c"),
    ]
    try:
        built = _clang(command)
    except (OSError, subprocess.SubprocessError) as exc:
        return emit("fail", f"index entry probe failed to compile: {exc}")
    if built.returncode != 0:
        detail = (built.stderr or built.stdout or "compile failed").strip()
        if _missing_symbols(detail):
            return emit("fail", "missing index implementation")
        return emit("fail", "index entry probe failed to compile: " + detail[:2000])
    try:
        ran = subprocess.run([str(output)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return emit("fail", f"index entry probe failed to run: {exc}")
    if ran.returncode != 0:
        detail = (ran.stderr or ran.stdout or "index entry probe failed").strip()
        return emit("fail", detail[:2000])
    return emit("pass", "index-buffer host and candidate registry passed")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    result = check(Path(args[0]))
    print(json.dumps(result))
    if result["verdict"] == "pass":
        return 0
    if result["verdict"] == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
