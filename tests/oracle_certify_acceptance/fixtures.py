"""Mock sealed PR D sessions for E1 acceptance (no live.report on the tree)."""
from __future__ import annotations

import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402
from oracle.function import fixture, synthetic  # noqa: E402

SESSION = "gfx-e1-acceptance"
ACCEPTANCE = 50
DESCRIPTOR_PATH = ROOT / "oracle/function/descriptors/gfx_set_constant_buffer.json"


def descriptor_sha256() -> str:
    return base.sha256(DESCRIPTOR_PATH)


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _scenario_for_thread(thread: int) -> str:
    return "s03" if thread % 2 == 0 else "s10"


def _rewrite_capture_identity(text: str, thread: int) -> str:
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("CAPTURE "):
            tokens = dict(part.split("=", 1) for part in line.split()[1:])
            tokens["thread"] = str(thread)
            tokens["sequence"] = "1"
            out.append(
                "CAPTURE " + " ".join(f"{key}={tokens[key]}" for key in fixture.CAPTURE_FIELDS))
        else:
            out.append(line)
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    return body


def transcript_for_thread(thread: int, *, synthetic_flag: int = 0) -> str:
    scenario = _scenario_for_thread(thread)
    text = synthetic.capture_text(fixture.load_descriptor(), scenario)
    if scenario == "s03":
        text = text.replace(
            "capture=re0-pilot scenario=s03:length-from-buffer epoch=synthetic-a2",
            f"capture={SESSION} scenario=live:length-from-buffer epoch=p5a-live",
        )
    else:
        text = text.replace(
            "capture=re0-pilot scenario=s10:explicit-length epoch=synthetic-a2",
            f"capture={SESSION} scenario=live:explicit-length epoch=p5a-live",
        )
    text = _rewrite_capture_identity(text, thread)
    text = text.replace("synthetic=1", f"synthetic={synthetic_flag}")
    return text


def assert_mock_session_capture_identities(root: Path) -> None:
    """Each mock capture must have a unique (thread, sequence) CAPTURE identity."""
    seen: set[tuple[int, int]] = set()
    for index, path in enumerate(capture_paths(root)):
        record = fixture.parse_capture(path.read_text(encoding="utf-8"))
        capture = record["capture"]
        thread = int(capture["thread"])
        sequence = int(capture["sequence"])
        if thread != index:
            raise AssertionError(
                f"{path.name}: expected thread={index}, got thread={thread}")
        if sequence != 1:
            raise AssertionError(
                f"{path.name}: expected sequence=1, got sequence={sequence}")
        key = (thread, sequence)
        if key in seen:
            raise AssertionError(f"duplicate CAPTURE identity {key}")
        seen.add(key)


def rewrite_exit_ctx_word_count(text: str, slot: int, word_count: int) -> str:
    layout = fixture.load_descriptor()["layout"]
    rel = (layout["word_count_base"] + slot * layout["slot_stride"]
           - layout["observation_start"])
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("EXIT name=ctx "):
            continue
        parts = line.split()[1:]
        fields = dict(part.split("=", 1) for part in parts)
        data = bytearray.fromhex(fields["bytes"])
        data[rel:rel + 4] = word_count.to_bytes(4, "little")
        fields["bytes"] = data.hex()
        lines[index] = "EXIT " + " ".join(f"{key}={value}"
                                          for key, value in fields.items())
        return "\n".join(lines) + "\n"
    raise AssertionError("the transcript has no EXIT ctx region")


def _write_tables(root: Path, capture_names: list[str],
                  summary: dict[str, str], startup: dict[str, str],
                  metadata: dict) -> None:
    def table(header: str, values: dict[str, str]) -> str:
        return "\n".join([header, *(f"{k}={v}" for k, v in values.items()),
                          "END"]) + "\n"

    (root / "summary.native").write_text(
        table(live.SUMMARY_HEADER, summary), encoding="utf-8")
    (root / "startup.status").write_text(
        table(live.STARTUP_HEADER, startup), encoding="utf-8")
    (root / "session.done").write_text(
        table(live.DONE_HEADER, {"session": SESSION, "state": "sealed"}),
        encoding="utf-8")
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def reseal_manifest(root: Path, *, status: str = "accepted") -> None:
    """Recompute manifest file hashes (synthetic test fixtures only)."""
    descriptor = fixture.load_descriptor()
    descriptor_sha = descriptor_sha256()
    library_sha = base.sha256(live.LIBRARY) if live.LIBRARY.is_file() else None
    file_hashes: dict[str, str] = {}
    for rel in ("summary.native", "startup.status", "session.done", "metadata.json"):
        file_hashes[rel] = _file_hash(root / rel)
    captures = root / "captures"
    for path in sorted(captures.glob("*.capture")):
        file_hashes[f"captures/{path.name}"] = _file_hash(path)
    manifest = {
        "schema": "eu4-gfx-live-manifest/v1",
        "session": SESSION,
        "status": status,
        "files": file_hashes,
        "descriptor_sha256": descriptor_sha,
        "library_sha256": library_sha,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def verify_manifest_matches_files(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for rel, expected in manifest["files"].items():
        path = root / rel
        if not path.is_file():
            raise AssertionError(f"manifest lists missing file: {rel}")
        actual = _file_hash(path)
        if actual != expected:
            raise AssertionError(f"manifest digest mismatch for {rel}")


def _default_summary(file_count: int, written: int) -> dict[str, str]:
    return {
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
        "written": str(written),
        "write_failed": "0",
        "undrained": "0",
        "reserved": "0",
        "ready": "0",
    }


def build_mock_live_session(
        *,
        file_count: int = ACCEPTANCE,
        synthetic_flag: int = 0,
        transcript_mutator: Callable[[int, str], str] | None = None,
) -> Path:
    root = Path(tempfile.mkdtemp(prefix="e1-accept-"))
    descriptor = fixture.load_descriptor()
    binary_sha = descriptor["binary"]["sha256"]
    library_sha = base.sha256(live.LIBRARY) if live.LIBRARY.is_file() else None

    captures = root / "captures"
    captures.mkdir()
    names: list[str] = []
    for thread in range(file_count):
        name = f"{SESSION}.{thread}.capture"
        text = transcript_for_thread(thread, synthetic_flag=synthetic_flag)
        if transcript_mutator is not None:
            text = transcript_mutator(thread, text)
        (captures / name).write_text(text, encoding="utf-8")
        names.append(name)

    startup = {
        "state": "installed",
        "session": SESSION,
        "pid": "123",
        "executable": str(base.GOG_EXE),
        "arch": "x86_64",
        "image_base": hex(0x100000000),
        "site": hex(0x100000000 + live.TARGET_RVA),
        "module": binary_sha,
        "error": "",
    }
    metadata = {
        "status": "captured",
        "session_id": SESSION,
        "game_pid": 123,
        "capture_files": len(names),
        "save_sha256": "a" * 64,
        "save_after_sha256": "a" * 64,
        "descriptor_sha256": descriptor_sha256(),
        "library_sha256": library_sha,
    }
    _write_tables(root, names, _default_summary(file_count, len(names)),
                  startup, metadata)
    reseal_manifest(root)
    verify_manifest_matches_files(root)
    return root


def case_a_delete_one_capture(root: Path) -> None:
    """Post-seal tamper: remove one capture without updating manifest."""
    verify_manifest_matches_files(root)
    first = next((root / "captures").glob("*.capture"))
    first.unlink()


def case_b_descriptor_pin_mismatch(root: Path) -> None:
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    metadata["descriptor_sha256"] = "f" * 64
    (root / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reseal_manifest(root)
    verify_manifest_matches_files(root)


def case_b_pid_disagreement(root: Path) -> None:
    summary = (root / "summary.native").read_text(encoding="utf-8")
    summary = summary.replace("pid=123", "pid=999")
    (root / "summary.native").write_text(summary, encoding="utf-8")
    reseal_manifest(root)
    verify_manifest_matches_files(root)


def case_empty_metadata_resealed(root: Path) -> None:
    """Resealed empty metadata object (adversarial; requires E1 metadata fix)."""
    verify_manifest_matches_files(root)
    (root / "metadata.json").write_text("{}\n", encoding="utf-8")
    reseal_manifest(root)
    verify_manifest_matches_files(root)


def build_forty_nine_one_mismatch_session() -> Path:
    """One observation has a mismatched but structurally valid EXIT ctx word count."""

    def mutator(thread: int, text: str) -> str:
        if thread != 0:
            return text
        return rewrite_exit_ctx_word_count(text, slot=2, word_count=99)

    return build_mock_live_session(transcript_mutator=mutator)


def capture_paths(root: Path) -> list[Path]:
    paths = list((root / "captures").glob("*.capture"))

    def thread_index(path: Path) -> int:
        return int(path.stem.rsplit(".", 1)[-1])

    return sorted(paths, key=thread_index)
