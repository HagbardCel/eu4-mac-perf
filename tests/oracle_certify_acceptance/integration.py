"""Black-box helpers for E1 integration acceptance tests."""
from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from oracle.function import fixture

from tests.oracle_certify_acceptance.fixtures import DESCRIPTOR_PATH, descriptor_sha256
from tests.oracle_certify_acceptance.production_binding import test_local_build_record

ROOT = Path(__file__).resolve().parents[2]

# Portable gate patches where E1 imports the loader: oracle.certify.certify.load_production_build_record


def tree_sha256(root: Path) -> dict[str, str]:
    root = root.resolve()
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            result[rel] = sha256(path.read_bytes()).hexdigest()
    return result


def assert_session_immutable(before: dict[str, str], after: dict[str, str]) -> None:
    if before != after:
        raise AssertionError("certify_session modified sealed evidence bytes")


# Mock fixtures alternate s03 (nonzero buffer) and s10 (absent buffer): two behaviors.
MOCK_OBSERVED_DISTINCT_BEHAVIORS = 2
MOCK_CERTIFIED_DIVERSITY_FLOOR = 2
MOCK_SCOPED_DIVERSITY_FLOOR = 3


def write_test_policy(path: Path, *, min_distinct_behaviors: int = 2) -> Path:
    descriptor = fixture.load_descriptor()
    policy = {
        "schema": "eu4-oracle-coverage-policy/v1",
        "function_id": descriptor["function_id"],
        "descriptor_sha256": descriptor_sha256(),
        "claim": "acceptance-mock",
        "scope": {
            "slots": [2, 3],
            "selectors": ["fixed-length", "length-from-buffer"],
            "input_classes": ["absent", "nonzero"],
        },
        "required_cells": [
            "slot:2",
            "slot:3",
            "selector:length-from-buffer",
            "selector:fixed-length",
            "input:nonzero",
            "input:absent",
        ],
        "optional_cells": [],
        "diversity": {"min_distinct_behaviors": min_distinct_behaviors},
    }
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


@contextmanager
def portable_production_record_patch(
        candidate: dict[str, Any]) -> Iterator[None]:
    record = test_local_build_record(candidate)
    with patch(
            "oracle.certify.certify.load_production_build_record",
            return_value=record,
            create=True):
        yield


def call_certify(certify_session, session_dir: Path, candidate: dict[str, Any],
                 *, policy_path: Path | None = None,
                 output_dir: Path | None = None) -> dict[str, Any]:
    descriptor = fixture.load_descriptor()
    with tempfile.TemporaryDirectory() as tmp:
        policy = policy_path or write_test_policy(Path(tmp) / "policy.json")
        out = output_dir or Path(tmp) / "out"
        out.mkdir(parents=True, exist_ok=True)
        with portable_production_record_patch(candidate):
            return certify_session(
                session_dir,
                descriptor,
                policy,
                candidate,
                certificate_dir=out,
            )


def call_certify_production_binding(
        certify_session,
        session_dir: Path,
        candidate: dict[str, Any],
        *,
        policy_path: Path | None = None,
        output_dir: Path | None = None) -> dict[str, Any]:
    """Production-artifact gate: unpatched loader and production build record."""
    descriptor = fixture.load_descriptor()
    with tempfile.TemporaryDirectory() as tmp:
        policy = policy_path or write_test_policy(Path(tmp) / "policy.json")
        out = output_dir or Path(tmp) / "out"
        out.mkdir(parents=True, exist_ok=True)
        return certify_session(
            session_dir,
            descriptor,
            policy,
            candidate,
            certificate_dir=out,
        )
