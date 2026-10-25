"""E1: sealed-session admission for the Tier 1 certifier.

This module reads one PR D live session and recomputes admission from the sealed
artifacts. It never writes, never creates acquisition provenance and never
imports PR D's mutable reporting command. The declared acquisition constants are
copied from the frozen PR D contract; a test pins them to the live module so a
drift is a contract amendment, not a silent change.

Admission is split deliberately:

* **session level** - is the complete sealed session an accepted acquisition?
* **fixture level** - is an individual transcript qualified for the comparison?

A session can be admitted while containing unqualified transcripts. They are
accounted for and excluded at fixture level; a malformed transcript makes the
session inadmissible.

PR D records two different descriptor commitments. ``manifest.json`` and
``metadata.json`` store the raw descriptor-file digest. Each A2 record stores
``fixture.descriptor_digest`` (canonical descriptor bytes). Both are checked.
Neither is inferred from the other.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.function import fixture
from oracle.certify.errors import CertifyError

# Frozen PR D acquisition contract. These are not inferred from local state.
QUOTA = 64
THREADS = 64
ACCEPTANCE = 50
EPOCH = "p5a-live"
SCENARIOS = frozenset({"live:length-from-buffer", "live:explicit-length"})
SUMMARY_HEADER = "EU4-GFX-LIVE-SUMMARY v1"
STARTUP_HEADER = "EU4-GFX-LIVE-STARTUP v1"
DONE_HEADER = "EU4-GFX-LIVE-DONE v1"

EVIDENCE_FILES = ("summary.native", "startup.status", "session.done", "metadata.json")
CAPTURE_DIR = "captures"
CAPTURE_SUFFIX = ".capture"

SESSION_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
DECIMAL_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
HEX_RE = re.compile(r"\A0x[0-9a-f]{1,16}\Z")
TICKET_RE = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")

NUMERIC_KEYS = (
    "intercepted", "not_admitted", "admitted", "incomplete", "completed",
    "written", "write_failed", "undrained", "reserved", "ready", "quota",
    "threads", "pid", "started_ns", "armed_ns", "closed_ns",
    "not_admitted_busy", "not_admitted_slot", "not_admitted_pointer",
    "not_admitted_address", "not_admitted_quota", "not_admitted_closed",
    "not_admitted_thread_limit",
)
REASON_KEYS = (
    "not_admitted_busy", "not_admitted_slot", "not_admitted_pointer",
    "not_admitted_address", "not_admitted_quota", "not_admitted_closed",
    "not_admitted_thread_limit",
)


@dataclass(frozen=True)
class Transcript:
    """One parsed, materialized transcript from an admitted session."""

    name: str
    text: str
    capture: dict[str, str]
    record: dict[str, Any]
    manifest: dict[str, Any]


@dataclass(frozen=True)
class Admission:
    eligible: bool
    reasons: tuple[str, ...]
    fixtures: str
    fixture_reasons: tuple[str, ...]
    transcripts: tuple[Transcript, ...]
    eligible_transcripts: tuple[Transcript, ...]
    transcript_count: int
    ineligible: int
    malformed: int
    loss: dict[str, int | None]
    manifest_sha256: str | None
    #: Recorded capture-library digest, or ``None`` when the seal left it null.
    library_sha256: str | None


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path) -> dict[str, str]:
    """Every regular file under ``root`` by POSIX-relative path.

    A symlink is evidence the sealed record did not declare; it is hashed by its
    target text so a swap changes the snapshot rather than escaping the root.
    """
    root = root.resolve()
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = "symlink:" + str(path.readlink())
        elif path.is_file():
            result[relative] = file_sha256(path)
    return result


def _read_bytes(path: Path, reasons: list[str], label: str) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError as error:
        reasons.append(f"missing or unreadable {label}: {type(error).__name__}")
        return None


def _decode(data: bytes, reasons: list[str], label: str) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeError:
        reasons.append(f"{label} is not UTF-8")
        return None


def _read_table(path: Path, header: str, reasons: list[str],
                label: str) -> dict[str, str] | None:
    data = _read_bytes(path, reasons, label)
    if data is None:
        return None
    text = _decode(data, reasons, label)
    if text is None:
        return None
    lines = text.splitlines()
    if not lines or lines[0] != header:
        reasons.append(f"{label} has an unexpected header")
        return None
    if lines[-1] != "END":
        reasons.append(f"{label} is not terminated by END")
        return None
    fields: dict[str, str] = {}
    for line in lines[1:-1]:
        key, separator, value = line.partition("=")
        if not separator or not key:
            reasons.append(f"{label} has a non-key=value line")
            return None
        if key in fields:
            reasons.append(f"{label} repeats the {key!r} field")
            return None
        fields[key] = value
    if not fields:
        reasons.append(f"{label} has no fields")
        return None
    return fields


def _read_json(path: Path, reasons: list[str], label: str) -> dict[str, Any] | None:
    data = _read_bytes(path, reasons, label)
    if data is None:
        return None
    text = _decode(data, reasons, label)
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        reasons.append(f"{label} is not valid JSON")
        return None
    if not isinstance(parsed, dict):
        reasons.append(f"{label} is not a JSON object")
        return None
    return parsed


def _sha_value(value: Any) -> str | None:
    return value if isinstance(value, str) and SHA256_RE.match(value) else None


def _decimal(fields: dict[str, str], key: str, reasons: list[str],
             label: str) -> int | None:
    value = fields.get(key)
    if value is None:
        reasons.append(f"{label} is missing the {key!r} field")
        return None
    if re.fullmatch(r"-(?:0|[1-9][0-9]*)", value):
        reasons.append(f"{label} has a negative {key!r} value")
        return None
    if not DECIMAL_RE.match(value):
        reasons.append(f"{label} has a non-canonical {key!r} value")
        return None
    return int(value)


def _hex(fields: dict[str, str], key: str, reasons: list[str],
         label: str) -> int | None:
    value = fields.get(key)
    if value is None:
        reasons.append(f"{label} is missing the {key!r} field")
        return None
    if not HEX_RE.match(value):
        reasons.append(f"{label} has a non-canonical {key!r} value")
        return None
    return int(value, 16)


def _manifest_files(manifest: dict[str, Any], reasons: list[str]
                    ) -> dict[str, str] | None:
    raw = manifest.get("files")
    if not isinstance(raw, dict) or not raw:
        reasons.append("manifest files is missing or empty")
        return None
    files: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            reasons.append("manifest contains a non-text file entry")
            return None
        if key.startswith("/") or "\\" in key or ".." in key.split("/"):
            reasons.append(f"manifest file entry escapes the session: {key!r}")
            return None
        digest = _sha_value(value)
        if digest is None:
            reasons.append(f"manifest digest for {key!r} is not a sha256")
            return None
        files[key] = digest
    return files


def _parse_captures(session_dir: Path, session: str | None, descriptor: dict[str, Any],
                    domain_sha: str, reasons: list[str],
                    fixture_reasons: list[str]) -> tuple[
                        list[Transcript], list[str], int, int]:
    captures_dir = session_dir / CAPTURE_DIR
    if not captures_dir.is_dir():
        reasons.append("captures directory is missing")
        return [], [], 0, 0
    files = sorted(captures_dir.iterdir())
    transcripts: list[Transcript] = []
    names: list[str] = []
    malformed = 0
    ineligible = 0
    tickets: set[int] = set()
    calls: set[tuple[int, int]] = set()
    binary_sha = descriptor["binary"]["sha256"]
    function_id = descriptor["function_id"]
    for path in files:
        relative = path.relative_to(session_dir).as_posix()
        if path.is_dir() or not path.name.endswith(CAPTURE_SUFFIX):
            reasons.append(f"unexpected capture-dir entry: {relative}")
            malformed += 1
            continue
        names.append(path.name)
        if session is None:
            reasons.append(f"cannot name transcript {relative} without a session")
            malformed += 1
            continue
        prefix = f"{session}."
        ticket_text = (path.name[len(prefix):-len(CAPTURE_SUFFIX)]
                       if path.name.startswith(prefix) else "")
        if (not ticket_text or not TICKET_RE.match(ticket_text)
                or str(int(ticket_text)) != ticket_text):
            reasons.append(f"unexpected ticket filename: {relative}")
            malformed += 1
            continue
        ticket = int(ticket_text)
        if ticket >= QUOTA:
            reasons.append(f"ticket outside 0..{QUOTA - 1}: {relative}")
            malformed += 1
            continue
        if ticket in tickets:
            reasons.append(f"duplicate ticket filename: {relative}")
            malformed += 1
            continue
        tickets.add(ticket)

        data = _read_bytes(path, reasons, relative)
        if data is None:
            malformed += 1
            continue
        text = _decode(data, reasons, relative)
        if text is None:
            malformed += 1
            continue
        try:
            capture = fixture.parse_capture(text)["capture"]
            if capture["synthetic"] is not False:
                raise fixture.FixtureError("Transcript is synthetic")
            if capture["capture"] != session:
                raise fixture.FixtureError(
                    "Transcript capture ID does not match the session")
            if capture["scenario"] not in SCENARIOS:
                raise fixture.FixtureError(
                    f"Unexpected live scenario: {capture['scenario']}")
            if capture["epoch"] != EPOCH:
                raise fixture.FixtureError(f"Unexpected live epoch: {capture['epoch']}")
            call = (int(capture["thread"]), int(capture["sequence"]))
            if call in calls:
                raise fixture.FixtureError(f"Duplicate thread/sequence: {call}")
            calls.add(call)
            materialized = fixture.materialize_capture(text, descriptor)
            record = materialized["record"]
            manifest = materialized["manifest"]
            provenance = record["provenance"]
            if provenance.get("module_sha256") != binary_sha:
                raise fixture.FixtureError(
                    "Transcript module digest does not match the descriptor")
            if provenance.get("function_id") != function_id:
                raise fixture.FixtureError(
                    "Transcript function does not match the descriptor")
            if record["raw"].get("descriptor_sha256") != domain_sha:
                raise fixture.FixtureError(
                    "Transcript descriptor digest does not match the certifier")
        except Exception as error:  # one bad transcript must not hide the others
            reasons.append(f"malformed transcript {relative}: "
                           f"{type(error).__name__}: {error}")
            malformed += 1
            continue
        if manifest["verdict"] != "qualified":
            missing = manifest.get("missing") or []
            fixture_reasons.append(
                f"excluded {relative}: unqualified: missing {missing}")
            ineligible += 1
            continue
        transcripts.append(Transcript(relative, text, capture, record, manifest))
    if not names:
        reasons.append("the session has no transcripts")
    return transcripts, names, malformed, ineligible


def admit_session(session_dir: Path, descriptor: dict[str, Any],
                  descriptor_sha: str) -> Admission:
    """Recompute session and fixture admission from sealed evidence."""
    session_dir = session_dir.expanduser().resolve()
    if not session_dir.is_dir():
        raise CertifyError(f"session directory does not exist: {session_dir}")
    reasons: list[str] = []
    fixture_reasons: list[str] = []

    manifest_path = session_dir / "manifest.json"
    manifest_sha: str | None = None
    manifest_bytes = _read_bytes(manifest_path, reasons, "manifest.json")
    if manifest_bytes is not None:
        manifest_sha = sha256(manifest_bytes).hexdigest()

    manifest = _read_json(manifest_path, reasons, "manifest.json") or {}
    if manifest.get("schema") != "eu4-gfx-live-manifest/v1":
        reasons.append("manifest schema is not eu4-gfx-live-manifest/v1")
    session = manifest.get("session")
    if not isinstance(session, str) or not SESSION_RE.match(session):
        reasons.append("manifest session is missing or invalid")
        session = None
    if manifest.get("status") != "accepted":
        reasons.append("manifest does not record an accepted session")
    if manifest.get("descriptor_sha256") != descriptor_sha:
        reasons.append("manifest descriptor digest does not match the descriptor")
    if "library_sha256" not in manifest:
        reasons.append("manifest library digest is missing")
        library_sha = None
    elif manifest.get("library_sha256") is None:
        # PR D writes null when the capture library file is absent at seal time.
        # The certifier must not require that host file. A recorded digest is
        # still required to be a sha256, and metadata must agree either way.
        library_sha = None
    else:
        library_sha = _sha_value(manifest.get("library_sha256"))
        if library_sha is None:
            reasons.append("manifest library digest is missing or invalid")
    declared_files = _manifest_files(manifest, reasons)

    summary = _read_table(session_dir / "summary.native", SUMMARY_HEADER,
                          reasons, "summary.native")
    startup = _read_table(session_dir / "startup.status", STARTUP_HEADER,
                          reasons, "startup.status")
    done = _read_table(session_dir / "session.done", DONE_HEADER,
                       reasons, "session.done")
    metadata = _read_json(session_dir / "metadata.json", reasons, "metadata.json")
    if metadata is None:
        metadata = {}
    elif not metadata:
        reasons.append("metadata is empty")

    domain_sha = fixture.descriptor_digest(descriptor)
    transcripts, capture_names, malformed, ineligible = _parse_captures(
        session_dir, session, descriptor, domain_sha, reasons, fixture_reasons)

    if summary is not None:
        if summary.get("session") != session:
            reasons.append("native summary session does not match the manifest")
        if summary.get("state") != "sealed":
            reasons.append("native summary is not sealed")
        if summary.get("quiescence") != "ok":
            reasons.append("native summary quiescence is not ok")
        numbers: dict[str, int] = {}
        for key in NUMERIC_KEYS:
            value = _decimal(summary, key, reasons, "summary.native")
            if value is not None:
                numbers[key] = value
        if len(numbers) == len(NUMERIC_KEYS):
            if any(value < 0 for value in numbers.values()):
                reasons.append("a native counter is negative")
            if numbers["pid"] <= 0:
                reasons.append("native PID is not positive")
            if numbers["quota"] != QUOTA:
                reasons.append(f"native quota is not {QUOTA}")
            if numbers["admitted"] > numbers["quota"]:
                reasons.append("native admitted exceeds quota")
            if numbers["threads"] > THREADS:
                reasons.append("native thread count exceeds the thread table")
            if numbers["armed_ns"] > 0 and numbers["threads"] == 0:
                reasons.append("native armed session has no registered threads")
            if not (numbers["started_ns"] > 0
                    and numbers["armed_ns"] >= numbers["started_ns"]
                    and numbers["closed_ns"] >= numbers["armed_ns"]):
                reasons.append("native epoch is not monotonic and armed")
            if numbers["not_admitted"] != sum(numbers[key] for key in REASON_KEYS):
                reasons.append("native not-admitted total is inconsistent")
            if numbers["admitted"] != numbers["incomplete"] + numbers["completed"]:
                reasons.append("native admitted != incomplete + completed")
            if numbers["completed"] != (numbers["written"] + numbers["write_failed"]
                                        + numbers["undrained"]):
                reasons.append(
                    "native completed != written + write_failed + undrained")
            if numbers["intercepted"] != (numbers["admitted"]
                                          + numbers["not_admitted"]):
                reasons.append("native intercepted != admitted + not_admitted")
            if numbers["ready"] != 0 or numbers["reserved"] != 0:
                reasons.append("native session retained READY or RESERVED tickets")
            if numbers["written"] != len(capture_names):
                reasons.append("native written count does not match transcript files")
            if numbers["write_failed"] or numbers["undrained"]:
                reasons.append("native session lost persisted captures")
            parsed_threads = {int(item.capture["thread"]) for item in transcripts}
            if len(parsed_threads) > numbers["threads"]:
                reasons.append(
                    "distinct transcript threads exceed the native thread table")

    if startup is not None:
        if startup.get("session") != session:
            reasons.append("startup session does not match the manifest")
        if startup.get("state") != "installed":
            reasons.append("dylib startup did not install")
        if startup.get("arch") != "x86_64":
            reasons.append("startup architecture is not x86_64")
        if startup.get("module") != descriptor["binary"]["sha256"]:
            reasons.append("startup module digest does not match the descriptor")
        image_base = _hex(startup, "image_base", reasons, "startup.status")
        site = _hex(startup, "site", reasons, "startup.status")
        entry_rva = int(descriptor["binary"]["entry_rva"], 16)
        if (image_base is not None and site is not None
                and site != image_base + entry_rva):
            reasons.append("startup site is not image_base + pinned RVA")
        startup_pid = _decimal(startup, "pid", reasons, "startup.status")
        if startup_pid is not None and startup_pid <= 0:
            reasons.append("startup PID is not positive")
        executable = startup.get("executable")
        if (not isinstance(executable, str) or not executable
                or not Path(executable).is_absolute()):
            reasons.append("startup executable is not an absolute recorded path")

    if done is not None:
        if done.get("session") != session:
            reasons.append("session-done marker does not match the manifest")
        if done.get("state") != "sealed":
            reasons.append("session-done marker is not sealed")
        if summary is not None and done.get("state") != summary.get("state"):
            reasons.append("session-done state does not match the native summary")

    if metadata:
        if metadata.get("status") != "captured":
            reasons.append("metadata status is not captured")
        if metadata.get("session_id") != session:
            reasons.append("metadata session does not match the manifest")
        if metadata.get("error"):
            reasons.append("metadata reports an error")
        capture_files = metadata.get("capture_files")
        if type(capture_files) is not int or capture_files != len(capture_names):
            reasons.append("metadata capture-file count does not match the transcripts")
        if metadata.get("descriptor_sha256") != descriptor_sha:
            reasons.append("metadata descriptor digest does not match the descriptor")
        if metadata.get("library_sha256") != library_sha:
            reasons.append("metadata and manifest library digests disagree")
        save_before = _sha_value(metadata.get("save_sha256"))
        save_after = _sha_value(metadata.get("save_after_sha256"))
        if save_before is None or save_before != save_after:
            reasons.append("metadata does not prove the disposable save unchanged")

    pids = []
    if metadata:
        game_pid = metadata.get("game_pid")
        if type(game_pid) is int and game_pid > 0:
            pids.append(game_pid)
        else:
            reasons.append("metadata game PID is missing or invalid")
    if startup is not None:
        startup_pid = startup.get("pid", "")
        if DECIMAL_RE.match(startup_pid) and int(startup_pid) > 0:
            pids.append(int(startup_pid))
    if summary is not None:
        native_pid = summary.get("pid", "")
        if DECIMAL_RE.match(native_pid) and int(native_pid) > 0:
            pids.append(int(native_pid))
    if len(pids) == 3 and len(set(pids)) != 1:
        reasons.append("launch, startup and native PIDs do not agree")

    if declared_files is not None:
        expected = set(EVIDENCE_FILES) | {
            f"{CAPTURE_DIR}/{name}" for name in capture_names}
        missing = sorted(expected - set(declared_files))
        extra = sorted(set(declared_files) - expected)
        for name in missing:
            reasons.append(f"manifest omits {name}")
        for name in extra:
            if not (session_dir / name).is_file():
                reasons.append(f"manifest lists missing file {name}")
            else:
                reasons.append(f"manifest lists unexpected file {name}")
        for name in sorted(expected & set(declared_files)):
            path = session_dir / name
            if not path.is_file():
                reasons.append(f"manifest-listed file is missing: {name}")
                continue
            try:
                actual = file_sha256(path)
            except OSError:
                reasons.append(f"manifest-listed file is unreadable: {name}")
                continue
            if actual != declared_files[name]:
                reasons.append(f"manifest digest does not match {name}")

    loss = {
        "write_failed": _optional_number(summary, "write_failed"),
        "undrained": _optional_number(summary, "undrained"),
        "incomplete": _optional_number(summary, "incomplete"),
    }
    if len(transcripts) < ACCEPTANCE:
        reasons.append(
            f"qualified transcripts {len(transcripts)} < {ACCEPTANCE}")

    # Session admission and fixture admission are independent. A PID or
    # accounting failure does not erase a qualified fixture, and an unqualified
    # transcript does not by itself make the fixture axis ineligible.
    eligible = not reasons
    fixtures = "eligible" if transcripts else "ineligible"
    if not transcripts:
        fixture_reasons.append("no eligible fixtures")
    return Admission(
        eligible=eligible,
        reasons=tuple(reasons),
        fixtures=fixtures,
        fixture_reasons=tuple(sorted(fixture_reasons)),
        transcripts=tuple(transcripts),
        eligible_transcripts=tuple(transcripts),
        transcript_count=len(capture_names),
        ineligible=ineligible,
        malformed=malformed,
        loss=loss,
        manifest_sha256=manifest_sha,
        library_sha256=library_sha,
    )


def _optional_number(fields: dict[str, str] | None, key: str) -> int | None:
    if fields is None:
        return None
    value = fields.get(key)
    if value is None or not DECIMAL_RE.match(value):
        return None
    return int(value)
