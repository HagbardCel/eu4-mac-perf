#!/usr/bin/env python3
"""Checkpoint research for one vertex-buffer function, then check its replacement.

The research checkpoint is one file beside the workspace. It is linked into
place once. Later checks hash the bytes read back from that file.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

NAME = "_GfxUpdateVertexBufferMultiple"
NOTE_REL = "analysis/automation/pilot-gfx-update-vertex-buffer-multiple/note.md"
CHECKPOINT_NAME = "research-checkpoint.md"
HEADINGS = (
    "Entry",
    "Calling convention",
    "Memory",
    "Range layout",
    "Ownership",
    "Indirect calls",
    "OpenGL",
    "Failure",
    "Evidence classes",
)
CASE_LABELS = (
    "case:multiple-range",
    "case:multiple-unchanged",
    "case:multiple-lifecycle",
    "case:multiple-error",
)
RESEARCH_PATHS = {NOTE_REL, "progress.md"}
IMPLEMENTATION_PATHS = RESEARCH_PATHS | {
    "benchmark/eu4_gfx_buffers.h",
    "benchmark/eu4_gfx_buffers.mm",
    "benchmark/eu4_gfx_entries.def",
    "tests/gfx_buffers_host.mm",
    "tests/test_gfx_buffers.py",
    "tests/test_gfx_entry_registry.py",
}
PASS_DIAGNOSTIC = (
    "automated offline validation passed; ready for independent semantic review; "
    "this does not authorize merge or claim runtime equivalence"
)
_RVA_RE = re.compile(r"0x[0-9a-fA-F]+")
_FOLLOWUP = b"\n## Follow-up\n"


def trusted_root() -> Path:
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("trusted repository not found")


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def checkpoint_path(workspace: Path) -> Path:
    return workspace.resolve().parent / CHECKPOINT_NAME


def prepare_checkpoint_temp(directory: Path, payload: bytes) -> Path:
    """Write a complete temporary file. Does not publish it."""
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".research-checkpoint.{os.getpid()}.{time.time_ns()}.tmp"
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def publish_checkpoint(temporary: Path, final: Path) -> None:
    """Link the temporary file onto the final name. An existing file wins."""
    try:
        os.link(temporary, final)
    except FileExistsError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


def checkpoint_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_checkpoint(directory: Path, payload: bytes) -> tuple[Path, str]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / CHECKPOINT_NAME
    if final.is_file():
        return final, checkpoint_sha256(final)
    temporary = prepare_checkpoint_temp(directory, payload)
    publish_checkpoint(temporary, final)
    return final, checkpoint_sha256(final)


def preservation_problem(live: bytes, saved: bytes) -> str | None:
    if live == saved:
        return None
    if not live.startswith(saved):
        return "research checkpoint was modified"
    rest = live[len(saved):]
    if not rest.startswith(_FOLLOWUP):
        return "corrections must follow ## Follow-up"
    return None


def blocker_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("BLOCKER:"):
            return stripped
    return None


def sections(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                found[current] = "\n".join(body)
            current = line[3:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        found[current] = "\n".join(body)
    return found


def cited_rvas(text: str) -> set[int]:
    found = set()
    for match in _RVA_RE.finditer(text):
        found.add(int(match.group(0), 16))
    return found


def note_problem(text: str, entry: int | None) -> str | None:
    if NAME not in text:
        return f"research note does not name {NAME}"
    found = sections(text)
    missing = [heading for heading in HEADINGS if heading not in found]
    if missing:
        return "research note missing headings: " + ", ".join(missing)
    for heading in HEADINGS:
        if heading == "Evidence classes":
            continue
        if not cited_rvas(found[heading]):
            return f"research note heading {heading} cites no instruction RVA"
    classes = found["Evidence classes"]
    for word in ("instruction", "Ghidra", "safety"):
        if word not in classes:
            return f"research note evidence classes omit {word}"
    if entry is not None and entry not in cited_rvas(found["Entry"]):
        return "Entry section does not cite the entry RVA returned by research resolve"
    return None


def contiguous_end(entry: int, instructions: list[dict]) -> int | None:
    cursor = entry
    for item in instructions:
        try:
            rva = int(str(item["instruction_rva"]), 16)
            length = int(item["length"])
        except (KeyError, TypeError, ValueError):
            return None
        if rva != cursor or length <= 0:
            return None
        cursor += length
    return cursor if instructions else None


def aligned_prefix(instructions: list[dict], prefix: bytes) -> bool:
    blob = bytearray()
    for item in instructions:
        try:
            blob.extend(bytes.fromhex(str(item["bytes_hex"])))
        except (KeyError, ValueError):
            return False
        if len(blob) == len(prefix):
            return bytes(blob) == prefix
        if len(blob) > len(prefix):
            return False
    return False


def registration_problem(candidate_text: str, trusted_text: str, identity: dict, parse) -> str | None:
    try:
        candidate = parse(candidate_text)
        trusted = parse(trusted_text)
    except ValueError as exc:
        return f"candidate registry is invalid: {exc}"
    found = [item for item in candidate if item[0] == NAME]
    if not found:
        return f"missing {NAME} registration"
    if len(found) != 1:
        return f"duplicate {NAME} registration"
    others = tuple(item for item in candidate if item[0] != NAME)
    if others != trusted:
        trusted_names = {item[0] for item in trusted}
        other_names = {item[0] for item in others}
        extra = sorted(other_names - trusted_names)
        if extra and other_names >= trusted_names and len(others) == len(trusted) + len(extra):
            return "unexpected gfx entry: " + ", ".join(extra)
        return "pre-existing gfx entry changed"
    _name, entry, end, fn, prefix = found[0]
    if entry != identity["entry"]:
        return "entry rva does not match research"
    if end != identity["end"]:
        return "entry end does not match the instruction span from research"
    if not aligned_prefix(identity["instructions"], prefix):
        return "prefix does not match the instruction bytes from research"
    if not str(fn).isidentifier():
        return "replacement symbol is invalid"
    return None


def host_problem(host_text: str, symbol: str) -> str | None:
    missing = [label for label in CASE_LABELS if label not in host_text]
    if missing:
        return "host tests missing " + ", ".join(missing)
    if symbol not in host_text:
        return "host tests do not call the replacement"
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
        paths = [entry[3:].decode()]
        index += 1
        if ("R" in status or "C" in status) and index < len(parts) and parts[index]:
            paths.append(parts[index].decode())
            index += 1
        found.extend(path for path in paths if not _ignored(path))
    return found


def _ignored(path: str) -> bool:
    # shortcut: the host test writes its binary and bytecode here; they are not source
    return (
        path.startswith("benchmark/.build/")
        or path == "benchmark/.build"
        or "/__pycache__/" in f"/{path}"
        or path.endswith(".pyc")
        or path.endswith(".DS_Store")
    )


def pinned_commit() -> str:
    data = json.loads((Path(__file__).resolve().parent / "campaign.json").read_text())
    return str(data["workspace"]["commit"])


def _blocked(entry: str, workspace: Path) -> bool:
    if not entry:
        return True
    try:
        resolved = Path(entry).resolve()
    except OSError:
        return True
    return resolved == workspace or workspace in resolved.parents


def import_parser(workspace: Path):
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


def research_json(args: list[str]) -> tuple[dict | None, str | None]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "reverse_engineering.research", *args],
            cwd=trusted_root(),
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"research failed: {exc}"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "research failed").strip()
        return None, "research failed: " + detail[:500]
    try:
        document = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None, "research output was not json"
    if not isinstance(document, dict):
        return None, "research output was not an object"
    return document, None


def resolve_entry() -> tuple[int | None, str | None]:
    document, error = research_json(["resolve", NAME, "--limit", "5"])
    if error:
        return None, error
    if document.get("status") != "ok":
        return None, f"research resolve status {document.get('status')}"
    identity = document.get("identity") or {}
    if identity.get("name_mangled") != NAME:
        return None, "research resolve did not name this function"
    try:
        return int(str(identity["entry_rva"]), 16), None
    except (KeyError, TypeError, ValueError):
        return None, "research resolve omitted the entry RVA"


def load_instructions(entry: int) -> tuple[list[dict] | None, int | None, str | None]:
    offset = 0
    collected: list[dict] = []
    total: int | None = None
    for _page in range(8):
        document, error = research_json([
            "function", hex(entry), "--view", "static",
            "--static-offset", str(offset), "--limit", "256", "--max-bytes", "262144",
        ])
        if error:
            return None, None, error
        if document.get("status") != "ok":
            return None, None, f"research function status {document.get('status')}"
        evidence = document.get("evidence") or {}
        page = evidence.get("instructions") or []
        if not isinstance(page, list):
            return None, None, "research function omitted instructions"
        collected.extend(page)
        try:
            total = int(evidence["instruction_total"])
        except (KeyError, TypeError, ValueError):
            return None, None, "research function omitted instruction_total"
        if len(collected) >= total and not document.get("truncated"):
            return collected, contiguous_end(entry, collected), None
        nxt = document.get("next_static_offset")
        if not isinstance(nxt, int) or nxt <= offset:
            break
        offset = nxt
    if total is not None and len(collected) == total:
        return collected, contiguous_end(entry, collected), None
    return None, None, "research static view was truncated"


def _metal_sdk() -> bool:
    return sys.platform == "darwin" and Path("/System/Library/Frameworks/Metal.framework").is_dir()


def run_candidate_tests(workspace: Path) -> str | None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace)
    probe = subprocess.run(
        [sys.executable, "-c", "import tests.test_gfx_buffers as tests; print(tests.__file__)"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout or "import failed").strip()
        return "candidate tests failed to import: " + detail[:500]
    loaded = Path(probe.stdout.strip()).resolve()
    if workspace.resolve() not in loaded.parents:
        return "candidate tests did not load from the candidate worktree"
    try:
        ran = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_gfx_buffers", "tests.test_gfx_entry_registry"],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"candidate tests failed to run: {exc}"
    if ran.returncode != 0:
        detail = (ran.stderr or ran.stdout or "tests failed").strip()
        return "candidate tests failed: " + detail[:2000]
    return None


def _scope(paths: list[str], allowed: set[str]) -> str | None:
    illegal = [item for item in paths if item not in allowed]
    if illegal:
        return "source scope violated: " + ", ".join(illegal[:8])
    return None


def check(workspace: Path) -> dict:
    workspace = Path(workspace).resolve()
    note_file = workspace / NOTE_REL
    saved_path = checkpoint_path(workspace)
    try:
        head = _git(workspace, "rev-parse", "HEAD")
        if head != pinned_commit():
            return emit("fail", "worktree HEAD is not the pinned commit")
        if _common_dir(workspace) != _common_dir(trusted_root()):
            return emit("fail", "worktree is not this repository")
        paths = changed_paths(workspace)
    except OSError as exc:
        return emit("unavailable", f"worktree unavailable: {exc}")
    scope = _scope(paths, IMPLEMENTATION_PATHS)
    if scope:
        return emit("fail", scope)
    research_only = set(paths) <= RESEARCH_PATHS
    note_bytes = note_file.read_bytes() if note_file.is_file() else None
    if note_bytes is None:
        note_text = ""
    else:
        try:
            note_text = note_bytes.decode()
        except UnicodeError:
            return emit("fail", "research note is not utf-8")
    saved = saved_path.read_bytes() if saved_path.is_file() else None
    if saved is not None:
        if note_bytes is None:
            return emit("fail", "research note is missing")
        problem = preservation_problem(note_bytes, saved)
        if problem:
            return emit("fail", problem)
    blocker = blocker_line(note_text) if note_text else None
    if blocker:
        return emit("blocker", blocker)
    if saved is not None:
        digest = checkpoint_sha256(saved_path)
        if research_only:
            return emit(
                "inconclusive",
                f"research checkpoint preserved sha256={digest}; implementation may proceed",
            )
    else:
        if not research_only:
            return emit(
                "fail",
                "replacement edited before the research checkpoint; revert those edits and leave the note",
            )
        if note_bytes is None:
            return emit("fail", "research note is missing")
        entry, error = resolve_entry()
        if error:
            return emit("unavailable", error)
        problem = note_problem(note_text, entry)
        if problem:
            return emit("fail", problem)
        _final, digest = capture_checkpoint(saved_path.parent, note_bytes)
        return emit(
            "inconclusive",
            f"research checkpoint captured sha256={digest}; implementation may proceed",
        )
    if not _metal_sdk():
        return emit("unavailable", "no Metal SDK")
    if shutil.which("clang") is None:
        return emit("unavailable", "no C compiler on PATH")
    entry, error = resolve_entry()
    if error:
        return emit("unavailable", error)
    instructions, end, error = load_instructions(entry)
    if error:
        return emit("unavailable", error)
    if end is None or instructions is None:
        return emit("unavailable", "research instructions are not a contiguous span")
    try:
        parser = import_parser(workspace)
        problem = registration_problem(
            (workspace / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8"),
            (trusted_root() / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8"),
            {"entry": entry, "end": end, "instructions": instructions},
            parser.parse_gfx_entries,
        )
    except (OSError, RuntimeError, ImportError) as exc:
        return emit("unavailable", f"trusted parser unavailable: {exc}")
    if problem:
        return emit("fail", problem)
    symbol = next(
        item[3] for item in parser.parse_gfx_entries(
            (workspace / "benchmark" / "eu4_gfx_entries.def").read_text(encoding="utf-8")
        ) if item[0] == NAME
    )
    buffers = workspace / "benchmark" / "eu4_gfx_buffers.mm"
    header = workspace / "benchmark" / "eu4_gfx_buffers.h"
    host = workspace / "tests" / "gfx_buffers_host.mm"
    try:
        if symbol not in buffers.read_text(encoding="utf-8") or symbol not in header.read_text(encoding="utf-8"):
            return emit("fail", "replacement symbol is not defined beside the existing buffers")
        host_text = host.read_text(encoding="utf-8")
    except OSError as exc:
        return emit("fail", f"replacement source is missing: {exc}")
    problem = host_problem(host_text, symbol)
    if problem:
        return emit("fail", problem)
    problem = run_candidate_tests(workspace)
    if problem:
        return emit("fail", problem)
    return emit("pass", PASS_DIAGNOSTIC)


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
