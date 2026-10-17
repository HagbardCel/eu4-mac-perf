"""Fixture corpora for the Oracle P3 synthetic slice.

A fixture is frozen ground truth: an input triple plus the observation the
reference implementation produced for it. Fixtures live in two disjoint sets:

* ``fixtures/visible`` - agent-accessible through the read-only Oracle tools.
* ``fixtures/held_out`` - trusted-host only; used exactly once by the
  controller's terminal certification, and never returned to the agent.

Nothing in this module decides equality; it only loads, validates and hashes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FIXTURE_SCHEMA_VERSION = "eu4-oracle-fixture/v1"
VISIBLE = "visible"
HELD_OUT = "held_out"

SLICE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = SLICE_DIR / "fixtures"

_INPUT_FIELDS = ("mode", "arg", "dirty", "value", "counter")


class CorpusError(RuntimeError):
    """Raised when a fixture corpus cannot be read or is malformed."""


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    function_id: str
    set_name: str
    input: dict[str, int]
    expected: dict[str, Any]
    sha256: str
    path: Path

    def driver_argv(self) -> list[str]:
        """Positional arguments for the trusted driver, in its fixed order."""
        return [str(self.input[field]) for field in _INPUT_FIELDS]

    def summary(self) -> dict[str, Any]:
        """Agent-safe projection: input and capture adequacy, never expectations."""
        capture = self.expected.get("capture", {})
        return {
            "fixture_id": self.fixture_id,
            "function_id": self.function_id,
            "input": dict(self.input),
            "capture": {
                "complete": bool(capture.get("complete", False)),
                "lost_events": int(capture.get("lost_events", 0)),
                "unknown_dependencies": list(capture.get("unknown_dependencies", [])),
            },
        }


def corpus_dir(set_name: str) -> Path:
    if set_name not in (VISIBLE, HELD_OUT):
        raise CorpusError(f"unknown fixture set: {set_name!r}")
    return FIXTURES_DIR / set_name


def _load_one(path: Path, set_name: str) -> Fixture:
    try:
        raw = path.read_bytes()
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise CorpusError(f"cannot read fixture {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"fixture {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CorpusError(f"fixture {path} is not an object")

    if data.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise CorpusError(
            f"fixture {path} has schema_version {data.get('schema_version')!r}, "
            f"expected {FIXTURE_SCHEMA_VERSION!r}"
        )
    if data.get("set") != set_name:
        raise CorpusError(f"fixture {path} declares set {data.get('set')!r}, expected {set_name!r}")

    fixture_id = data.get("fixture_id")
    function_id = data.get("function_id")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise CorpusError(f"fixture {path} has no fixture_id")
    if not isinstance(function_id, str) or not function_id:
        raise CorpusError(f"fixture {path} has no function_id")

    fixture_input = data.get("input")
    if not isinstance(fixture_input, dict) or set(fixture_input) != set(_INPUT_FIELDS):
        raise CorpusError(f"fixture {path} input must have exactly {_INPUT_FIELDS}")
    for field, value in fixture_input.items():
        if not isinstance(value, int) or isinstance(value, bool):
            raise CorpusError(f"fixture {path} input.{field} must be an int")

    expected = data.get("expected")
    if not isinstance(expected, dict):
        raise CorpusError(f"fixture {path} has no 'expected' object")

    return Fixture(
        fixture_id=fixture_id,
        function_id=function_id,
        set_name=set_name,
        input=dict(fixture_input),
        expected=expected,
        sha256=hashlib.sha256(raw).hexdigest(),
        path=path,
    )


def load_corpus(set_name: str, *, root: Path | None = None) -> list[Fixture]:
    """Load a fixture set, sorted by id, rejecting duplicates."""
    directory = (root or FIXTURES_DIR) / set_name
    if not directory.is_dir():
        raise CorpusError(f"missing fixture directory: {directory}")
    fixtures: list[Fixture] = []
    for path in sorted(directory.glob("*.json")):
        fixtures.append(_load_one(path, set_name))
    if not fixtures:
        raise CorpusError(f"fixture set {set_name!r} is empty")
    seen: set[str] = set()
    for fixture in fixtures:
        if fixture.fixture_id in seen:
            raise CorpusError(f"duplicate fixture_id: {fixture.fixture_id}")
        seen.add(fixture.fixture_id)
    return fixtures


def functions(fixtures: Iterable[Fixture]) -> list[str]:
    return sorted({fixture.function_id for fixture in fixtures})


def manifest(fixtures: Iterable[Fixture], *, set_name: str) -> dict[str, Any]:
    """Content-addressed description of a fixture set, bound into receipts."""
    ordered = sorted(fixtures, key=lambda f: f.fixture_id)
    digest = hashlib.sha256()
    entries = []
    for fixture in ordered:
        digest.update(fixture.fixture_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(fixture.sha256.encode("ascii"))
        digest.update(b"\x00")
        entries.append(
            {"fixture_id": fixture.fixture_id, "sha256": fixture.sha256, "bytes": fixture.path.stat().st_size}
        )
    return {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "set": set_name,
        "count": len(ordered),
        "function_ids": functions(ordered),
        "sha256": digest.hexdigest(),
        "fixtures": entries,
    }
