"""Owned-snapshot and result contract for offline shadow invocation.

``shadow-invocation/v0`` is the frozen handoff: one owned ``FUNCTION-CAPTURE v1``
transcript, the frozen candidate identity, and a bounded result. Comparison
vocabulary stays in :func:`oracle.function.replay.replay_capture`. This module
only names the snapshot, the identity and the failure partition.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from oracle.function import fixture
from oracle.function.replay import SUPPORTED_FUNCTION

SNAPSHOT_SCHEMA = "eu4-oracle-shadow-snapshot/v0"
RESULT_SCHEMA = "eu4-oracle-shadow-result/v0"
REPORT_SCHEMA = "eu4-oracle-shadow-worker-report/v0"

OUTCOMES = ("completed", "timeout", "crash", "refused", "dropped")
COMPARISONS = ("match", "mismatch", "infrastructure", "none")
FAILURES = (
    "malformed",
    "missing-input",
    "unsupported-function",
    "identity",
    "harness-unavailable",
    "timeout",
    "crash",
    "output-cap",
    "queue",
)

SNAPSHOT_FIELDS = ("schema", "transcript", "transcript_sha256")
CANDIDATE_FIELDS = (
    "function_id",
    "source_sha256",
    "executable_sha256",
    "harness_path",
)
RESULT_FIELDS = ("schema", "outcomes", "comparisons", "output_bytes", "accounting")
ACCOUNTING_FIELDS = (
    "submitted",
    "accepted",
    "completed",
    "refused",
    "timed_out",
    "crashed",
    "dropped",
)

DEFAULT_DEADLINE_SECONDS = 5.0
DEFAULT_OUTPUT_LIMIT_BYTES = 65536
DEFAULT_QUEUE_LIMIT = 64

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_OUTCOME_TO_ACCOUNTING = {
    "completed": "completed",
    "refused": "refused",
    "timeout": "timed_out",
    "crash": "crashed",
    "dropped": "dropped",
}


class ShadowContractError(ValueError):
    """A shadow handoff or result violates the invocation contract."""


class SnapshotError(ShadowContractError):
    """An owned snapshot or candidate identity cannot be admitted."""


class ResultError(ShadowContractError):
    """A published result does not reconcile."""


@dataclass(frozen=True)
class Snapshot:
    """An owned copy of one transcript. The text is not a session path."""

    transcript: str
    transcript_sha256: str
    schema: str = SNAPSHOT_SCHEMA


@dataclass(frozen=True)
class CandidateIdentity:
    """Frozen candidate the worker may execute. Not a live game pointer."""

    function_id: str
    source_sha256: str
    executable_sha256: str
    harness_path: str


def build_snapshot(transcript: str) -> Snapshot:
    """Copy ``transcript`` so the caller's object is not the owned snapshot."""
    if not isinstance(transcript, str):
        raise SnapshotError("snapshot transcript must be a string")
    owned = transcript.encode("utf-8").decode("utf-8")
    return Snapshot(
        transcript=owned,
        transcript_sha256=sha256(owned.encode("utf-8")).hexdigest(),
    )


def snapshot_text(snapshot: Snapshot) -> str:
    validate_snapshot(snapshot)
    return snapshot.transcript


def validate_snapshot(snapshot: Snapshot) -> None:
    if not isinstance(snapshot, Snapshot) or snapshot.schema != SNAPSHOT_SCHEMA:
        raise SnapshotError("snapshot schema is not owned-snapshot/v0")
    if not isinstance(snapshot.transcript, str):
        raise SnapshotError("snapshot transcript must be a string")
    digest = sha256(snapshot.transcript.encode("utf-8")).hexdigest()
    if digest != snapshot.transcript_sha256:
        raise SnapshotError("snapshot digest does not match its transcript")


def load_snapshot(path: str | Path) -> Snapshot:
    """Load a transcript file the supervisor wrote. The path is not a session."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SnapshotError(f"owned snapshot is unreadable: {error}") from error
    snapshot = build_snapshot(text)
    validate_snapshot(snapshot)
    return snapshot


def expected_descriptor_digest() -> str:
    """Descriptor policy digest the frozen A2 fixture already publishes."""
    return fixture.capture_policy_digest(fixture.load_descriptor())


def executable_digest(path: str | Path) -> str | None:
    """SHA-256 of a regular file, or ``None`` when it cannot be read."""
    file_path = Path(path)
    if not file_path.is_file():
        return None
    digest = sha256()
    try:
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def parse_candidate(value: Mapping[str, Any]) -> CandidateIdentity:
    """Require the frozen identity fields. Digests are lowercase sha256."""
    if not isinstance(value, Mapping):
        raise SnapshotError("missing descriptor input: candidate identity")
    missing = [
        field for field in CANDIDATE_FIELDS
        if not isinstance(value.get(field), str) or value.get(field) == ""
    ]
    if missing:
        raise SnapshotError("missing descriptor input: " + ", ".join(missing))
    source = str(value["source_sha256"])
    executable = str(value["executable_sha256"])
    if _SHA256.fullmatch(source) is None or _SHA256.fullmatch(executable) is None:
        raise SnapshotError("missing descriptor input: candidate digest")
    return CandidateIdentity(
        function_id=str(value["function_id"]),
        source_sha256=source,
        executable_sha256=executable,
        harness_path=str(value["harness_path"]),
    )


def admission_failure(transcript: str, candidate: CandidateIdentity) -> str | None:
    """A reason to refuse *before* executing the harness.

    Malformed transcripts and missing capture inputs are not decided here.
    :func:`oracle.function.replay.replay_capture` owns that vocabulary.
    """
    if candidate.function_id != SUPPORTED_FUNCTION:
        return "unsupported-function"
    digest = executable_digest(candidate.harness_path)
    if digest is None:
        return "harness-unavailable"
    if digest != candidate.executable_sha256:
        return "identity"
    if not isinstance(transcript, str):
        return "malformed"
    return None


def classify_replay(result: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    """Map one frozen replay verdict onto the shadow outcome partition.

    A candidate ``mismatch`` stays a completed comparison. Infrastructure and
    evidence refusals stay refusals, and only infrastructure failures use the
    ``infrastructure`` comparison.
    """
    failures = [str(item) for item in result.get("failures") or []]
    verdict = result.get("verdict")
    if verdict == "match":
        return "completed", "match", failures
    if verdict == "mismatch":
        return "completed", "mismatch", failures
    if verdict == "refused":
        infrastructure = any(item.startswith("infrastructure:") for item in failures)
        return "refused", "infrastructure" if infrastructure else "none", failures
    failures.append(f"infrastructure:unknown-verdict: {verdict}")
    return "crash", "infrastructure", failures


def replay_failure(result: Mapping[str, Any]) -> str | None:
    """The first replay failure, when the verdict is not a match."""
    _outcome, _comparison, failures = classify_replay(result)
    if result.get("verdict") == "match":
        return None
    return failures[0] if failures else None


def accounting(outcomes: Sequence[str]) -> dict[str, int]:
    """Exact partition: submitted = accepted + dropped, and accepted splits."""
    counts = Counter(outcomes)
    dropped = counts["dropped"]
    accepted = len(outcomes) - dropped
    report = {
        "submitted": len(outcomes),
        "accepted": accepted,
        "completed": counts["completed"],
        "refused": counts["refused"],
        "timed_out": counts["timeout"],
        "crashed": counts["crash"],
        "dropped": dropped,
    }
    for outcome, key in _OUTCOME_TO_ACCOUNTING.items():
        if counts[outcome] != report[key]:
            raise ResultError(f"accounting drifted for {outcome}")
    if report["submitted"] != report["accepted"] + report["dropped"]:
        raise ResultError("submitted must equal accepted + dropped")
    if report["accepted"] != (
            report["completed"] + report["refused"]
            + report["timed_out"] + report["crashed"]):
        raise ResultError("accepted must equal completed + refused + timed_out + crashed")
    return report


def make_result(outcomes: Sequence[str], comparisons: Sequence[str],
                output_bytes: int, failures: Sequence[Sequence[str]]) -> dict[str, Any]:
    result = {
        "schema": RESULT_SCHEMA,
        "outcomes": list(outcomes),
        "comparisons": list(comparisons),
        "output_bytes": output_bytes,
        "failures": [list(item) for item in failures],
        "accounting": accounting(outcomes),
    }
    validate_result(result)
    return result


def validate_result(result: Mapping[str, Any]) -> None:
    if result.get("schema") != RESULT_SCHEMA:
        raise ResultError(f"schema must be {RESULT_SCHEMA}")
    for key in RESULT_FIELDS:
        if key not in result:
            raise ResultError(f"missing result key: {key}")
    outcomes = result["outcomes"]
    comparisons = result["comparisons"]
    if not isinstance(outcomes, Sequence) or isinstance(outcomes, str):
        raise ResultError("outcomes must be a sequence")
    if len(outcomes) != len(comparisons):
        raise ResultError("outcomes and comparisons differ in length")
    for outcome in outcomes:
        if outcome not in OUTCOMES:
            raise ResultError(f"unknown outcome: {outcome}")
    for comparison in comparisons:
        if comparison not in COMPARISONS:
            raise ResultError(f"unknown comparison: {comparison}")
    if not isinstance(result["output_bytes"], int) or result["output_bytes"] < 0:
        raise ResultError("output_bytes must be a non-negative integer")
    account = result["accounting"]
    if not isinstance(account, Mapping):
        raise ResultError("accounting must be an object")
    for key in ACCOUNTING_FIELDS:
        if key not in account:
            raise ResultError(f"accounting missing {key}")
    expected = accounting(list(outcomes))
    for key in ACCOUNTING_FIELDS:
        if account[key] != expected[key]:
            raise ResultError(f"accounting.{key} does not match outcomes")
    if "failures" in result and len(result["failures"]) != len(outcomes):
        raise ResultError("failures length must match outcomes")


def result_text(result: Mapping[str, Any]) -> str:
    validate_result(result)
    return json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
