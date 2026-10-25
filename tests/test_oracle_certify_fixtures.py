"""Synthetic sealed sessions for the E1 certifier tests.

These sessions are built in-process. They are not live EU IV captures, and
nothing here calls ``eu4_gfx_live_capture.report``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402
from oracle.certify.certify import TRUSTED_BUILD_FLAGS, frozen_source_sha256  # noqa: E402
from oracle.function import fixture, synthetic  # noqa: E402

SESSION = "gfx-e1-certify"
POLICY_PATH = ROOT / "docs" / "oracle" / "policies" / "gfx-set-constant-buffer.json"
RECON_DIR = (ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer"
             / "reconstruction")
COMPILER_FALLBACKS = ("c++", "clang++", "g++")


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def descriptor_file_sha256() -> str:
    return file_sha256(fixture.DESCRIPTOR_PATH)


def tree_sha256(root: Path) -> dict[str, str]:
    root = root.resolve()
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            result[relative] = file_sha256(path)
    return result


def _table(header: str, values: dict[str, str]) -> str:
    return "\n".join([header, *(f"{key}={value}" for key, value in values.items()),
                      "END"]) + "\n"


def _rewrite_capture(text: str, thread: int, *, scenario: str, epoch: str,
                     synthetic_flag: int) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("CAPTURE "):
            continue
        tokens = dict(part.split("=", 1) for part in line.split()[1:])
        tokens["capture"] = SESSION
        tokens["scenario"] = scenario
        tokens["epoch"] = epoch
        tokens["thread"] = str(thread)
        tokens["sequence"] = "1"
        tokens["synthetic"] = str(synthetic_flag)
        lines[index] = "CAPTURE " + " ".join(
            f"{key}={tokens[key]}" for key in fixture.CAPTURE_FIELDS)
        break
    body = "\n".join(lines)
    return body if body.endswith("\n") else body + "\n"


def live_transcript(thread: int, scenario_name: str, *, synthetic_flag: int = 0) -> str:
    scenario = "live:length-from-buffer" if scenario_name == "s03" else "live:explicit-length"
    text = synthetic.capture_text(fixture.load_descriptor(), scenario_name)
    return _rewrite_capture(text, thread, scenario=scenario, epoch="p5a-live",
                            synthetic_flag=synthetic_flag)


def evidence_refusal_transcript(thread: int) -> str:
    """Qualified explicit-length capture whose RSI does not name the buffer.

    A2 does not require the buffer read on this path, so the transcript stays
    qualified. PR C replay refuses it before launching a harness.
    """
    text = live_transcript(thread, "s10")
    lines: list[str] = []
    inserted = False
    for line in text.splitlines():
        if line.startswith("CARRIER ") and not inserted:
            lines.append("ENTRY name=buffer address=0x400000000 bytes=0d000000")
            lines.append("EXIT name=buffer address=0x400000000 bytes=0d000000")
            inserted = True
        if line.startswith("END "):
            line = line.replace("regions=8", "regions=10")
        lines.append(line)
    if not inserted:
        raise AssertionError("the transcript has no CARRIER record")
    return "\n".join(lines) + "\n"


def flip_exit_canary(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("EXIT name=canary-after "):
            continue
        fields = dict(part.split("=", 1) for part in line.split()[1:])
        data = bytearray.fromhex(fields["bytes"])
        data[0] ^= 0xFF
        fields["bytes"] = data.hex()
        lines[index] = "EXIT " + " ".join(f"{key}={value}" for key, value in fields.items())
        return "\n".join(lines) + "\n"
    raise AssertionError("the transcript has no EXIT canary-after region")


def rewrite_word_count(text: str, slot: int, word_count: int) -> str:
    layout = fixture.load_descriptor()["layout"]
    relative = (layout["word_count_base"] + slot * layout["slot_stride"]
                - layout["observation_start"])
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("EXIT name=ctx "):
            continue
        fields = dict(part.split("=", 1) for part in line.split()[1:])
        data = bytearray.fromhex(fields["bytes"])
        data[relative:relative + 4] = word_count.to_bytes(4, "little")
        fields["bytes"] = data.hex()
        lines[index] = "EXIT " + " ".join(f"{key}={value}" for key, value in fields.items())
        return "\n".join(lines) + "\n"
    raise AssertionError("the transcript has no EXIT ctx region")


def reseal(root: Path, *, status: str = "accepted") -> None:
    library = file_sha256(live.LIBRARY) if live.LIBRARY.is_file() else None
    files: dict[str, str] = {}
    for name in ("summary.native", "startup.status", "session.done", "metadata.json"):
        files[name] = file_sha256(root / name)
    for path in sorted((root / "captures").glob("*.capture")):
        files[f"captures/{path.name}"] = file_sha256(path)
    manifest = {
        "schema": "eu4-gfx-live-manifest/v1",
        "session": SESSION,
        "status": status,
        "files": files,
        "descriptor_sha256": descriptor_file_sha256(),
        "library_sha256": library,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_session(*, count: int = 50, synthetic_flag: int = 0,
                  transcripts: list[str] | None = None,
                  summary_overrides: dict[str, str] | None = None,
                  startup_overrides: dict[str, str] | None = None,
                  metadata_overrides: dict[str, Any] | None = None,
                  mutator: Callable[[int, str], str] | None = None,
                  seal: bool = True) -> Path:
    root = Path(tempfile.mkdtemp(prefix="e1-certify-"))
    captures = root / "captures"
    captures.mkdir()
    if transcripts is None:
        transcripts = []
        for thread in range(count):
            scenario = "s03" if thread % 2 == 0 else "s10"
            text = live_transcript(thread, scenario, synthetic_flag=synthetic_flag)
            if mutator is not None:
                text = mutator(thread, text)
            transcripts.append(text)
    for index, text in enumerate(transcripts):
        (captures / f"{SESSION}.{index}.capture").write_text(text, encoding="utf-8")
    file_count = len(transcripts)
    summary = {
        "session": SESSION,
        "pid": "123",
        "state": "sealed",
        "closed_reason": "operator",
        "quiescence": "ok",
        "started_ns": "1",
        "armed_ns": "2",
        "closed_ns": "3",
        "quota": "64",
        "threads": str(max(file_count, 1)),
        "intercepted": str(file_count),
        "not_admitted": "0",
        "not_admitted_busy": "0",
        "not_admitted_slot": "0",
        "not_admitted_pointer": "0",
        "not_admitted_address": "0",
        "not_admitted_quota": "0",
        "not_admitted_closed": "0",
        "not_admitted_thread_limit": "0",
        "admitted": str(file_count),
        "incomplete": "0",
        "completed": str(file_count),
        "written": str(file_count),
        "write_failed": "0",
        "undrained": "0",
        "reserved": "0",
        "ready": "0",
    }
    summary.update(summary_overrides or {})
    descriptor = fixture.load_descriptor()
    startup = {
        "state": "installed",
        "session": SESSION,
        "pid": "123",
        "executable": str(base.GOG_EXE),
        "arch": "x86_64",
        "image_base": hex(0x100000000),
        "site": hex(0x100000000 + live.TARGET_RVA),
        "module": descriptor["binary"]["sha256"],
        "error": "",
    }
    startup.update(startup_overrides or {})
    library = file_sha256(live.LIBRARY) if live.LIBRARY.is_file() else None
    metadata = {
        "status": "captured",
        "session_id": SESSION,
        "game_pid": 123,
        "capture_files": file_count,
        "save_sha256": "a" * 64,
        "save_after_sha256": "a" * 64,
        "descriptor_sha256": descriptor_file_sha256(),
        "library_sha256": library,
    }
    metadata.update(metadata_overrides or {})
    (root / "summary.native").write_text(
        _table(live.SUMMARY_HEADER, summary), encoding="utf-8")
    (root / "startup.status").write_text(
        _table(live.STARTUP_HEADER, startup), encoding="utf-8")
    (root / "session.done").write_text(
        _table(live.DONE_HEADER, {"session": SESSION, "state": summary["state"]}),
        encoding="utf-8")
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if seal:
        reseal(root)
    return root


def write_stub(path: Path) -> None:
    path.write_text("#!/bin/sh\nprintf 'error=usage\\n'\nexit 2\n", encoding="utf-8")
    path.chmod(0o755)


def candidate_for(executable: Path, *, identity: str = "test-binding",
                  flags: list[str] | None = None,
                  executable_sha256: str | None = None,
                  source_sha256: str | None = None) -> dict[str, Any]:
    return {
        "source_sha256": source_sha256 if source_sha256 is not None else frozen_source_sha256(),
        "executable_sha256": executable_sha256 if executable_sha256 is not None
        else file_sha256(executable),
        "build_identity": identity,
        "build_flags": list(TRUSTED_BUILD_FLAGS if flags is None else flags),
        "harness_path": str(executable),
    }


def write_policy(path: Path, *, slots: list[int], selectors: list[str],
                 inputs: list[str], required: list[str], optional: list[str],
                 floor: int) -> Path:
    descriptor = fixture.load_descriptor()
    body = {
        "schema": "eu4-oracle-coverage-policy/v1",
        "function_id": descriptor["function_id"],
        "descriptor_sha256": descriptor_file_sha256(),
        "claim": "certify-test",
        "scope": {"slots": slots, "selectors": selectors, "input_classes": inputs},
        "required_cells": required,
        "optional_cells": optional,
        "diversity": {"min_distinct_behaviors": floor},
    }
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def narrow_policy(path: Path, *, floor: int = 2, slots: list[int] | None = None,
                  optional: list[str] | None = None) -> Path:
    chosen = [2, 3] if slots is None else slots
    required = [f"slot:{slot}" for slot in chosen]
    required.extend([
        "selector:length-from-buffer",
        "selector:fixed-length",
        "input:nonzero",
        "input:absent",
    ])
    return write_policy(
        path, slots=chosen,
        selectors=["fixed-length", "length-from-buffer"],
        inputs=["absent", "nonzero"],
        required=required,
        optional=[] if optional is None else optional,
        floor=floor)


def compile_re1_harness() -> Path | None:
    compiler = next((shutil.which(name) for name in COMPILER_FALLBACKS
                     if shutil.which(name)), None)
    if compiler is None:
        return None
    work = Path(tempfile.mkdtemp(prefix="e1-re1-"))
    binary = work / "harness-bin"
    command = [
        compiler, *TRUSTED_BUILD_FLAGS,
        str(RECON_DIR / "gfx_set_constant_buffer.cpp"),
        str(RECON_DIR / "harness.cpp"),
        str(RECON_DIR / "arith_literals.cpp"),
        "-o", str(binary),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise AssertionError(f"RE1 harness compile failed:\n{result.stderr}")
    return binary
