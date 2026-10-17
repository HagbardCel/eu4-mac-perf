"""Completeness-checked differential comparator for the Oracle P3 slice.

``oracle.core.compare_fixture`` is an offline prototype: it refuses to pass when
the *expected* evidence is incomplete, but it cannot establish that the *actual*
observation is complete. In P3 the trusted harness - never the candidate -
constructs the actual observation, so this module validates that envelope before
delegating semantic comparison to the seed comparator.

Rules enforced here:

* A missing domain, malformed hex, or absent terminator is ``INCOMPLETE_ORACLE``.
* Abnormal termination is ``CANDIDATE_CRASH`` (signal / non-zero exit) or
  ``CANDIDATE_TIMEOUT``, and is never reported as ``MISMATCH``.
* Only a complete actual observation, compared against complete expected
  evidence, can yield ``PASS``.
"""
from __future__ import annotations

from hashlib import sha256 as _sha256
from typing import Any

from oracle.core import canonical_bytes, compare_fixture

DIAGNOSTIC_SCHEMA_VERSION = "eu4-oracle-diagnostic/v2"

STATUS_PASS = "PASS"
STATUS_MISMATCH = "MISMATCH"
STATUS_INCOMPLETE = "INCOMPLETE_ORACLE"
STATUS_CRASH = "CANDIDATE_CRASH"
STATUS_TIMEOUT = "CANDIDATE_TIMEOUT"
STATUS_HARNESS_ERROR = "HARNESS_ERROR"
#: The candidate could not be compiled at all. A candidate fault, distinct from a
#: trusted-harness fault (``HARNESS_ERROR``) and from incomplete evidence
#: (``INCOMPLETE_ORACLE``).
STATUS_BUILD_ERROR = "CANDIDATE_BUILD_ERROR"

STATUSES = (
    STATUS_PASS,
    STATUS_MISMATCH,
    STATUS_INCOMPLETE,
    STATUS_CRASH,
    STATUS_TIMEOUT,
    STATUS_BUILD_ERROR,
    STATUS_HARNESS_ERROR,
)

EXIT_KIND_STATUS = {
    "crash": STATUS_CRASH,
    "timeout": STATUS_TIMEOUT,
    "harness_error": STATUS_HARNESS_ERROR,
}

_REQUIRED_OBSERVATION_FIELDS = ("return_hex", "exit", "post_memory", "effects")

_DOMAIN_MAP = {
    "return": "return",
    "exception": "exception",
    "memory": "memory",
    "effects": "effects",
    "ABI": "ABI",
    "resource": "resource",
    "visual": "visual",
}


def _is_hex(text: Any) -> bool:
    if not isinstance(text, str) or len(text) % 2 != 0:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in text)


def validate_observation(observation: Any) -> list[str]:
    """Return a list of structural problems with an actual observation envelope."""
    if not isinstance(observation, dict):
        return [f"observation is not an object: {type(observation).__name__}"]

    problems: list[str] = []
    for field in _REQUIRED_OBSERVATION_FIELDS:
        if field not in observation:
            problems.append(f"missing required observation field: {field}")
    if problems:
        return problems

    if not _is_hex(observation["return_hex"]):
        problems.append("return_hex is not even-length hex")

    if not isinstance(observation["exit"], str) or not observation["exit"]:
        problems.append("exit is not a non-empty string")

    post_memory = observation["post_memory"]
    if not isinstance(post_memory, dict) or not post_memory:
        problems.append("post_memory must be a non-empty object")
    else:
        for obj, value in post_memory.items():
            if not isinstance(obj, str) or not obj:
                problems.append(f"post_memory key is not a string: {obj!r}")
            elif not _is_hex(value):
                problems.append(f"post_memory[{obj!r}] is not even-length hex")

    effects = observation["effects"]
    if not isinstance(effects, list):
        problems.append("effects is not a list")
    else:
        for index, effect in enumerate(effects):
            if not isinstance(effect, dict):
                problems.append(f"effects[{index}] is not an object")
                continue
            if not isinstance(effect.get("field"), int) or isinstance(effect.get("field"), bool):
                problems.append(f"effects[{index}].field is not an int")
            if not isinstance(effect.get("value"), int) or isinstance(effect.get("value"), bool):
                problems.append(f"effects[{index}].value is not an int")
    return problems


def _map_divergence(divergence: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reshape the seed comparator's divergence into the preserved v2 schema."""
    if divergence is None:
        return None
    domain = _DOMAIN_MAP.get(str(divergence.get("domain")), "memory")
    mapped: dict[str, Any] = {"domain": domain, "comparison_policy": "exact_bytes"}
    if divergence.get("object") is not None:
        mapped["object_id"] = divergence["object"]
    if divergence.get("byte_offset") is not None:
        mapped["byte_offset"] = divergence["byte_offset"]
    if divergence.get("field") is not None:
        mapped["field_name"] = divergence["field"]
    if divergence.get("expected") is not None:
        mapped["expected_hex"] = divergence["expected"]
    if divergence.get("actual") is not None:
        mapped["actual_hex"] = divergence["actual"]
    return mapped


def _diagnostic(
    *,
    function_id: str,
    fixture_id: str,
    run_id: str,
    status: str,
    capture: dict[str, Any],
    first_divergence: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "function_id": function_id,
        "fixture_id": fixture_id,
        "run_id": run_id,
        "status": status,
        "first_divergence": first_divergence,
        "capture": capture,
    }
    if note:
        result["note"] = note
    return result


def compare(
    expected: dict[str, Any],
    actual: Any,
    *,
    function_id: str,
    fixture_id: str,
    run_id: str,
    exit_kind: str = "normal",
    note: str | None = None,
) -> dict[str, Any]:
    """Compare one actual observation against frozen expected evidence.

    ``exit_kind`` is the process-level classification produced by the harness
    (``normal``, ``crash``, ``timeout``, ``harness_error``, ``incomplete``).
    """
    capture = expected.get("capture") if isinstance(expected, dict) else None
    if not isinstance(capture, dict):
        capture = {"complete": False, "lost_events": 0, "unknown_dependencies": ["missing_expected_capture"]}

    if exit_kind in EXIT_KIND_STATUS:
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=EXIT_KIND_STATUS[exit_kind],
            capture=capture,
            note=note or f"candidate did not complete: {exit_kind}",
        )

    if exit_kind == "incomplete":
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=STATUS_INCOMPLETE,
            capture=capture,
            note=note or "candidate produced an incomplete observation",
        )

    problems = validate_observation(actual)
    if problems:
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=STATUS_INCOMPLETE,
            capture=capture,
            note="invalid observation: " + "; ".join(sorted(problems)[:5]),
        )

    if not capture.get("complete", False) or capture.get("lost_events", 0) or capture.get("unknown_dependencies"):
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=STATUS_INCOMPLETE,
            capture=capture,
            note="expected evidence is incomplete",
        )

    seed = compare_fixture(expected, actual)
    status = seed["status"]
    if status == "PASS":
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=STATUS_PASS,
            capture=capture,
        )
    if status == "INCOMPLETE_ORACLE":
        return _diagnostic(
            function_id=function_id,
            fixture_id=fixture_id,
            run_id=run_id,
            status=STATUS_INCOMPLETE,
            capture=capture,
            note="seed comparator reported incomplete oracle evidence",
        )
    return _diagnostic(
        function_id=function_id,
        fixture_id=fixture_id,
        run_id=run_id,
        status=STATUS_MISMATCH,
        capture=capture,
        first_divergence=_map_divergence(seed.get("first_divergence")),
    )


def verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-fixture diagnostics into a corpus verdict.

    The corpus verdict is ``PASS`` only when *every* fixture passed; any other
    status is surfaced verbatim so an incomplete or crashed run cannot look green.
    """
    if not results:
        raise ValueError("no results to aggregate")
    statuses = [result["status"] for result in results]
    if all(status == STATUS_PASS for status in statuses):
        aggregate = STATUS_PASS
    elif STATUS_BUILD_ERROR in statuses:
        aggregate = STATUS_BUILD_ERROR
    elif STATUS_CRASH in statuses or STATUS_TIMEOUT in statuses:
        aggregate = STATUS_CRASH if STATUS_CRASH in statuses else STATUS_TIMEOUT
    elif STATUS_HARNESS_ERROR in statuses:
        aggregate = STATUS_HARNESS_ERROR
    elif STATUS_INCOMPLETE in statuses:
        aggregate = STATUS_INCOMPLETE
    else:
        aggregate = STATUS_MISMATCH
    failures = [result["fixture_id"] for result in results if result["status"] != STATUS_PASS]
    return {
        "status": aggregate,
        "total": len(results),
        "passed": sum(1 for status in statuses if status == STATUS_PASS),
        "failures": failures,
    }


def observation_digest(observation: dict[str, Any]) -> str:
    """Content address of an observation envelope (canonical JSON)."""
    return _sha256(canonical_bytes(observation)).hexdigest()
