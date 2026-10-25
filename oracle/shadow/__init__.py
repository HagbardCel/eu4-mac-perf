"""F1: offline shadow invocation of the frozen function candidate.

Tier 2 feeds an *owned copy* of one ``FUNCTION-CAPTURE v1`` transcript to an
isolated worker and returns a bounded verdict. This package implements only the
offline half: no live game process, no pointer, no session directory and no
sealed-evidence path crosses the handoff.

The public contract is :mod:`oracle.shadow.contract` (snapshot and result
schemas); :mod:`oracle.shadow.worker` is the small subprocess entry point and
:mod:`oracle.shadow.supervisor` owns the queue, the deadline, the output cap and
the accounting.
"""
from __future__ import annotations

from oracle.shadow.supervisor import run_owned
from oracle.shadow.contract import (
    CANDIDATE_FIELDS,
    DEFAULT_DEADLINE_SECONDS,
    FAILURES,
    OUTCOMES,
    REPORT_SCHEMA,
    RESULT_FIELDS,
    RESULT_SCHEMA,
    SNAPSHOT_FIELDS,
    SNAPSHOT_SCHEMA,
    SUPPORTED_FUNCTION,
    CandidateIdentity,
    ResultError,
    ShadowContractError,
    Snapshot,
    SnapshotError,
    accounting,
    admission_failure,
    build_snapshot,
    expected_descriptor_digest,
    load_snapshot,
    make_result,
    replay_failure,
    result_text,
    snapshot_text,
    validate_result,
    validate_snapshot,
)

__all__ = [
    "CANDIDATE_FIELDS",
    "DEFAULT_DEADLINE_SECONDS",
    "FAILURES",
    "OUTCOMES",
    "REPORT_SCHEMA",
    "RESULT_FIELDS",
    "RESULT_SCHEMA",
    "SNAPSHOT_FIELDS",
    "SNAPSHOT_SCHEMA",
    "SUPPORTED_FUNCTION",
    "CandidateIdentity",
    "ResultError",
    "ShadowContractError",
    "Snapshot",
    "SnapshotError",
    "accounting",
    "admission_failure",
    "build_snapshot",
    "expected_descriptor_digest",
    "load_snapshot",
    "make_result",
    "replay_failure",
    "result_text",
    "snapshot_text",
    "run_owned",
    "validate_result",
    "validate_snapshot",
]
