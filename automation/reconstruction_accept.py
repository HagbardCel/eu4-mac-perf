#!/usr/bin/env python3
"""Offline qualification for one reconstructed gfx function.

A pass means the entry is authentic, the candidate library links, and the
configured tests ran. It does not prove the agent understood the function.
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

from automation.state import require_worktree

CHECKPOINT_NAME = "research-checkpoint.json"
FOLLOWUP = b"\n## Follow-up\n"
PASS_DIAGNOSTIC = (
    "automated offline validation passed; ready for independent semantic review; "
    "this does not authorize merge or claim runtime equivalence"
)
_HEX = re.compile(r"0x[0-9a-fA-F]+")
_MODULE = Path(__file__).resolve()


def trusted_root() -> Path:
    if _MODULE.name != "reconstruction_accept.py" or _MODULE.parent.name != "automation":
        raise RuntimeError("shared checker is not the trusted module")
    root = _MODULE.parents[1]
    if not (root / ".git").exists():
        raise RuntimeError("shared checker is not the trusted module")
    return root


def emit(verdict: str, diagnostics: str) -> dict:
    return {"verdict": verdict, "diagnostics": diagnostics}


def config_sha256(config: dict) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def checkpoint_path(workspace: Path) -> Path:
    return workspace.resolve().parent / CHECKPOINT_NAME


def checkpoint_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(path: Path, *args: str, binary: bool = False):
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True)
    if completed.returncode != 0:
        raise OSError((completed.stderr or b"git failed").decode()[:500])
    return completed.stdout if binary else completed.stdout.decode().strip()


def _worktree_problem(workspace: Path, root: Path, commit: str) -> tuple[str, str] | None:
    try:
        require_worktree(workspace, root, commit)
        if _git(workspace, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
            return ("fail", "worktree is not detached")
    except RuntimeError as exc:
        return ("fail", str(exc))
    except OSError as exc:
        return ("unavailable", f"worktree unavailable: {exc}")
    return None


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
    # shortcut: host and library builds write bytecode and dylibs here
    return (
        path.startswith("benchmark/.build/") or path == "benchmark/.build"
        or "/__pycache__/" in f"/{path}" or path.endswith((".pyc", ".DS_Store"))
    )


def pinned_show(workspace: Path, commit: str, relative: str) -> str:
    return _git(workspace, "show", f"{commit}:{relative}")


def checker_blob_problem(workspace: Path, commit: str) -> str | None:
    message = "shared checker does not match the pinned commit"
    try:
        blob = _git(workspace, "show", f"{commit}:automation/reconstruction_accept.py", binary=True)
    except OSError:
        return message
    if blob != _MODULE.read_bytes():
        return message
    return None


def research_json(args: list[str]) -> tuple[dict | None, str | None]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "reverse_engineering.research", *args],
            cwd=trusted_root(), capture_output=True, text=True, timeout=180,
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


def _executable_sha() -> tuple[str | None, str | None]:
    document, error = research_json(["status"])
    if error:
        return None, error
    if document.get("status") != "ok":
        return None, f"research status {document.get('status')}"
    sha = (document.get("evidence") or {}).get("pinned_sha256")
    if not sha:
        return None, "research status omitted the executable"
    return str(sha), None


def _resolve(symbol: str) -> tuple[int | None, str | None]:
    document, error = research_json(["resolve", symbol, "--limit", "5"])
    if error:
        return None, error
    if document.get("status") != "ok":
        return None, f"research resolve status {document.get('status')}"
    identity = document.get("identity") or {}
    if identity.get("name_mangled") != symbol:
        return None, "research resolve did not name this function"
    try:
        return int(str(identity["entry_rva"]), 16), None
    except (KeyError, TypeError, ValueError):
        return None, "research resolve omitted the entry RVA"


def _normalize(instructions: list[dict]) -> list[dict]:
    return [
        {
            "rva": str(item["instruction_rva"]),
            "length": int(item["length"]),
            "bytes": str(item["bytes_hex"]),
        }
        for item in instructions
    ]


def _contiguous_end(entry: int, instructions: list[dict]) -> int | None:
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


def load_span(symbol: str) -> tuple[int | None, int | None, list[dict] | None, str | None]:
    entry, error = _resolve(symbol)
    if error:
        return None, None, None, error
    offset = 0
    collected: list[dict] = []
    total: int | None = None
    for _page in range(8):
        document, error = research_json([
            "function", hex(entry), "--view", "static",
            "--static-offset", str(offset), "--limit", "256", "--max-bytes", "262144",
        ])
        if error:
            return None, None, None, error
        if document.get("status") != "ok":
            return None, None, None, f"research function status {document.get('status')}"
        evidence = document.get("evidence") or {}
        page = evidence.get("instructions") or []
        if not isinstance(page, list):
            return None, None, None, "research function omitted instructions"
        collected.extend(page)
        try:
            total = int(evidence["instruction_total"])
        except (KeyError, TypeError, ValueError):
            return None, None, None, "research function omitted instruction_total"
        if len(collected) >= total and not document.get("truncated"):
            break
        nxt = document.get("next_static_offset")
        if not isinstance(nxt, int) or nxt <= offset:
            break
        offset = nxt
    else:
        return None, None, None, "research static view was truncated"
    if total is None or len(collected) != total:
        return None, None, None, "research static view was truncated"
    end = _contiguous_end(entry, collected)
    if end is None:
        return None, None, None, "research instructions are not a contiguous span"
    return entry, end, _normalize(collected), None


def _aligned(instructions: list[dict], prefix: bytes) -> bool:
    blob = bytearray()
    for item in instructions:
        try:
            blob.extend(bytes.fromhex(str(item["bytes"])))
        except ValueError:
            return False
        if len(blob) == len(prefix):
            return bytes(blob) == prefix
        if len(blob) > len(prefix):
            return False
    return False


def _names_function(text: str, symbol: str, entry: int) -> bool:
    return symbol in text and any(int(item, 16) == entry for item in _HEX.findall(text))


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        pass
    finally:
        temporary.unlink(missing_ok=True)


def _preservation(live: bytes, saved: str) -> str | None:
    original = saved.encode()
    if live == original:
        return None
    if not live.startswith(original):
        return "research checkpoint was modified"
    if not live[len(original):].startswith(FOLLOWUP):
        return "corrections must follow ## Follow-up"
    return None


def _blocker(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip().startswith("BLOCKER:"):
            return line.strip()
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
        if Path(boot.__file__).resolve() != expected or workspace in Path(boot.__file__).resolve().parents:
            raise RuntimeError(f"untrusted module origin: {boot.__file__}")
        return boot
    finally:
        sys.path = saved


def registration_problem(candidate_text: str, baseline_text: str, symbol: str, entry: int, end: int, instructions: list[dict], parse) -> str | None:
    try:
        candidate = parse(candidate_text)
        baseline = parse(baseline_text)
    except ValueError as exc:
        return f"candidate registry is invalid: {exc}"
    found = [item for item in candidate if item[0] == symbol]
    if len(found) != 1:
        return f"missing {symbol} registration" if not found else f"duplicate {symbol} registration"
    others = tuple(item for item in candidate if item[0] != symbol)
    if others != baseline:
        baseline_names = {item[0] for item in baseline}
        other_names = {item[0] for item in others}
        extra = sorted(other_names - baseline_names)
        if extra and other_names >= baseline_names and len(others) == len(baseline) + len(extra):
            return "unexpected gfx entry: " + ", ".join(extra)
        return "pre-existing gfx entry changed"
    _name, got_entry, got_end, fn, prefix = found[0]
    if got_entry != entry:
        return "entry rva does not match research"
    if got_end != end:
        return "entry end does not match the instruction span from research"
    if not _aligned(instructions, prefix):
        return "prefix does not match the instruction bytes from research"
    if not str(fn).isidentifier():
        return "replacement symbol is invalid"
    return None


def toolchain_problem() -> str | None:
    if sys.platform != "darwin" or not Path("/System/Library/Frameworks/Metal.framework").is_dir():
        return "no Metal SDK"
    if shutil.which("clang") is None:
        return "no C compiler on PATH"
    return None


def build_candidate(workspace: Path) -> str | None:
    bench = workspace / "benchmark"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(bench)
    try:
        ran = subprocess.run(
            [sys.executable, "-c", "import eu4_metal_boot; eu4_metal_boot.build_library('x86_64')"],
            cwd=bench, env=env, capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"candidate library failed to build: {exc}"
    if ran.returncode != 0:
        detail = (ran.stderr or ran.stdout or "build failed").strip()
        return "candidate library failed to build: " + detail[:2000]
    return None


def run_modules(workspace: Path, names: list[str]) -> str | None:
    workspace = Path(workspace).resolve()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace)
    env["MTL_DEBUG_LAYER"] = "1"
    env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    driver = (
        "import importlib,json,sys,unittest\n"
        "module=importlib.import_module(sys.argv[1])\n"
        "result=unittest.TextTestRunner(stream=sys.stderr,verbosity=0).run("
        "unittest.defaultTestLoader.loadTestsFromModule(module))\n"
        "failed=result.failures+result.errors\n"
        "json.dump({'file':module.__file__,'ran':result.testsRun,"
        "'skipped':len(result.skipped),'failed':len(failed),"
        "'detail':'\\n'.join(item[1] for item in failed)[:2000]},sys.stdout)\n"
    )
    for name in names:
        try:
            ran = subprocess.run(
                [sys.executable, "-c", driver, name],
                cwd=workspace, env=env, capture_output=True, text=True, timeout=600,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"unavailable: candidate tests failed to run: {exc}"
        try:
            payload = json.loads(ran.stdout)
        except json.JSONDecodeError:
            detail = (ran.stderr or ran.stdout or "tests produced no result").strip()
            kind = "candidate tests failed: " if ran.returncode else "unavailable: "
            return kind + detail[:2000]
        loaded = Path(str(payload.get("file", ""))).resolve()
        if workspace not in loaded.parents:
            return "candidate tests did not load from the candidate worktree"
        ran_count = int(payload.get("ran") or 0) - int(payload.get("skipped") or 0)
        if ran_count < 1:
            return f"unavailable: {name} did not run a test"
        if ran.returncode != 0 or int(payload.get("failed") or 0):
            return f"candidate tests failed: {name}: {payload.get('detail') or ''}"[:2000]
    return None


def _config(config: dict) -> tuple[str, str, set[str], list[str]]:
    try:
        symbol = str(config["symbol"])
        note = str(config["note"])
        allowed = {str(item) for item in config["allowed_paths"]}
        modules = [str(item) for item in config["test_modules"]]
    except (KeyError, TypeError):
        raise ValueError("reconstruction configuration is incomplete") from None
    if not symbol or not note or not modules:
        raise ValueError("reconstruction configuration is incomplete")
    if note not in allowed or "progress.md" not in allowed:
        raise ValueError("reconstruction allowlist omits the research note or progress.md")
    return symbol, note, allowed, modules


def _scope(paths: list[str], allowed: set[str]) -> str | None:
    illegal = [item for item in paths if item not in allowed]
    if illegal:
        return "source scope violated: " + ", ".join(illegal[:8])
    return None


def check(workspace: Path, campaign: dict, config: dict) -> dict:
    workspace = Path(workspace).resolve()
    try:
        root = trusted_root()
        symbol, note_rel, allowed, modules = _config(config)
        pinned = str(campaign["workspace"]["commit"])
        campaign_id = str(campaign["id"])
    except (KeyError, TypeError, RuntimeError, ValueError) as exc:
        return emit("fail", str(exc))
    problem = _worktree_problem(workspace, root, pinned)
    if problem:
        return emit(problem[0], problem[1])
    blob = checker_blob_problem(workspace, pinned)
    if blob:
        return emit("fail", blob)
    try:
        paths = changed_paths(workspace)
        baseline = pinned_show(workspace, pinned, "benchmark/eu4_gfx_entries.def")
        target = json.loads(pinned_show(workspace, pinned, "reverse_engineering/target.json"))
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        return emit("unavailable", f"pinned source unavailable: {exc}")
    scope = _scope(paths, allowed)
    if scope:
        return emit("fail", scope)
    exe, error = _executable_sha()
    if error:
        return emit("unavailable", error)
    if str(target.get("sha256", "")).lower() != exe.lower():
        return emit("fail", "executable identity does not match the pinned metadata")
    research_only = set(paths) <= {note_rel, "progress.md"}
    saved_path = checkpoint_path(workspace)
    saved = None
    if saved_path.is_file():
        try:
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return emit("fail", "research checkpoint was modified")
        if not isinstance(saved, dict):
            return emit("fail", "research checkpoint was modified")
    note_path = workspace / note_rel
    note_bytes = note_path.read_bytes() if note_path.is_file() else None
    if saved is not None:
        if saved.get("reconstruction_sha256") != config_sha256(config):
            return emit("fail", "reconstruction configuration changed")
        if (
            saved.get("version") != 1 or saved.get("campaign_id") != campaign_id
            or saved.get("commit") != pinned or saved.get("executable_sha256") != exe
            or (saved.get("function") or {}).get("symbol") != symbol
        ):
            return emit("fail", "research checkpoint identity changed")
        if note_bytes is None:
            return emit("fail", "research note is missing")
        preserved = _preservation(note_bytes, str(saved.get("note") or ""))
        if preserved:
            return emit("fail", preserved)
        entry, end, instructions, error = load_span(symbol)
        if error:
            return emit("unavailable", error)
        try:
            function = saved["function"]
            changed = (
                int(function["entry_rva"], 16) != entry
                or int(function["end_rva"], 16) != end
                or function["instructions"] != instructions
            )
        except (KeyError, TypeError, ValueError):
            return emit("fail", "research checkpoint identity changed")
        if changed:
            return emit("fail", "original instruction span changed")
    elif not research_only:
        return emit(
            "fail",
            "replacement edited before the research checkpoint; revert those edits and leave the note",
        )
    if note_bytes is None:
        text = ""
    else:
        try:
            text = note_bytes.decode()
        except UnicodeError:
            return emit("fail", "research note is not utf-8")
    blocker = _blocker(text) if text else None
    if blocker:
        return emit("blocked", blocker)
    if saved is None:
        if not text.strip():
            return emit("fail", "research note is missing" if note_bytes is None else "research note is empty")
        entry, end, instructions, error = load_span(symbol)
        if error:
            return emit("unavailable", error)
        if not _names_function(text, symbol, entry):
            return emit("fail", "research note does not name the resolved function")
        payload = {
            "campaign_id": campaign_id,
            "commit": pinned,
            "executable_sha256": exe,
            "function": {
                "end_rva": format(end, "#x"),
                "entry_rva": format(entry, "#x"),
                "instructions": instructions,
                "symbol": symbol,
            },
            "note": text,
            "reconstruction_sha256": config_sha256(config),
            "version": 1,
        }
        body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        _write_once(saved_path, body)
        digest = checkpoint_sha256(saved_path)
        return emit("inconclusive", f"research checkpoint captured sha256={digest}; implementation may proceed")
    if research_only:
        digest = checkpoint_sha256(saved_path)
        return emit("inconclusive", f"research checkpoint preserved sha256={digest}; implementation may proceed")
    registry = workspace / "benchmark" / "eu4_gfx_entries.def"
    if not registry.is_file():
        return emit("fail", "candidate registry is missing")
    try:
        parser = import_parser(workspace)
        candidate = registry.read_text(encoding="utf-8")
    except (OSError, RuntimeError, ImportError) as exc:
        return emit("unavailable", f"trusted parser unavailable: {exc}")
    function = saved["function"]
    problem_text = registration_problem(
        candidate, baseline, symbol, int(function["entry_rva"], 16), int(function["end_rva"], 16),
        function["instructions"], parser.parse_gfx_entries,
    )
    if problem_text:
        return emit("fail", problem_text)
    missing = toolchain_problem()
    if missing:
        return emit("unavailable", missing)
    built = build_candidate(workspace)
    if built:
        return emit("fail", built)
    ran = run_modules(workspace, modules)
    if ran:
        verdict = "unavailable" if ran.startswith("unavailable:") else "fail"
        return emit(verdict, ran.removeprefix("unavailable: ").strip() if verdict == "unavailable" else ran)
    return emit("pass", PASS_DIAGNOSTIC)
