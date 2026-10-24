"""F1 shadow invocation acceptance contract (independent of implementation layout)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

RESULT_SCHEMA = "eu4-oracle-shadow-result/v0"
TERMINAL_OUTCOMES = frozenset({
    "completed", "timeout", "crash", "refused", "dropped",
})
COMPARISONS = frozenset({"match", "mismatch", "infrastructure", "none"})


def assert_result_shape(result: Mapping[str, Any], snapshot_count: int) -> None:
    if result.get("schema") != RESULT_SCHEMA:
        raise AssertionError(f"schema must be {RESULT_SCHEMA!r}")
    for key in ("outcomes", "comparisons", "output_bytes", "accounting"):
        if key not in result:
            raise AssertionError(f"missing result key: {key}")
    if len(result["outcomes"]) != snapshot_count:
        raise AssertionError("outcomes length must match submitted snapshots")
    if len(result["comparisons"]) != snapshot_count:
        raise AssertionError("comparisons length must match submitted snapshots")
    for outcome in result["outcomes"]:
        if outcome not in TERMINAL_OUTCOMES:
            raise AssertionError(f"unknown outcome: {outcome}")
    for comparison in result["comparisons"]:
        if comparison not in COMPARISONS:
            raise AssertionError(f"unknown comparison: {comparison}")


def assert_accounting_partition(
        result: Mapping[str, Any],
        submitted: int,
        *,
        dropped_before_accept: int = 0) -> None:
    """Every submitted snapshot is dropped or accepted exactly once.

    Malformed snapshots are accepted then refused (not dropped before admission).
    """
    acc = result["accounting"]
    for key in ("submitted", "accepted", "completed", "refused",
                "timed_out", "crashed", "dropped"):
        if key not in acc:
            raise AssertionError(f"accounting missing {key}")
    if acc["submitted"] != submitted:
        raise AssertionError("submitted count mismatch")
    if acc["submitted"] != acc["accepted"] + acc["dropped"]:
        raise AssertionError(
            "N_submitted must equal N_accepted + N_dropped")
    if acc["accepted"] != (
            acc["completed"] + acc["refused"] + acc["timed_out"] + acc["crashed"]):
        raise AssertionError(
            "N_accepted must equal completed + refused + timed_out + crashed")
    if dropped_before_accept and acc["dropped"] != dropped_before_accept:
        raise AssertionError("unexpected dropped count")


def assert_flood_outcome(result: Mapping[str, Any],
                         output_limit_bytes: int) -> None:
    """Process-group flood must not complete as a match within the output cap."""
    if result["output_bytes"] > output_limit_bytes:
        raise AssertionError(
            f"output_bytes {result['output_bytes']} exceeds cap {output_limit_bytes}")
    if result["comparisons"] and result["comparisons"][0] == "match":
        raise AssertionError("flood fixture must not report match")
    if result["outcomes"] and result["outcomes"][0] == "completed":
        raise AssertionError("flood fixture must not report completed")


def assert_executable_identity_enforced(
        result: Mapping[str, Any],
        *,
        expect_refusal: bool = True) -> None:
    if expect_refusal:
        if result["outcomes"][0] not in ("refused", "crashed", "timeout"):
            raise AssertionError(
                "replacing harness without updating executable_sha256 must not "
                "complete successfully")
        if result["comparisons"][0] == "match":
            raise AssertionError("identity mismatch must not report match")


def weak_supervisor_stand_in(
        snapshots: Sequence[str],
        **_kwargs: Any) -> dict[str, Any]:
    """Reaps only the direct child; reports success despite a flood."""
    return {
        "schema": RESULT_SCHEMA,
        "outcomes": ["completed"] * len(snapshots),
        "comparisons": ["match"] * len(snapshots),
        "output_bytes": 64,
        "accounting": {
            "submitted": len(snapshots),
            "accepted": len(snapshots),
            "completed": len(snapshots),
            "refused": 0,
            "timed_out": 0,
            "crashed": 0,
            "dropped": 0,
        },
    }
