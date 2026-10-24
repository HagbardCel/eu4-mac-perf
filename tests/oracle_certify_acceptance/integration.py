"""Black-box helpers for E1 integration acceptance tests."""
from __future__ import annotations

import json
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.function import fixture

from tests.oracle_certify_acceptance.fixtures import DESCRIPTOR_PATH, descriptor_sha256

ROOT = Path(__file__).resolve().parents[2]


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
            "input_classes": ["absent", "nonzero", "zero"],
        },
        "required_cells": [
            "slot:2",
            "selector:length-from-buffer",
            "input:nonzero",
        ],
        "optional_cells": [],
        "diversity": {"min_distinct_behaviors": min_distinct_behaviors},
    }
    path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def call_certify(certify_session, session_dir: Path, candidate: dict[str, Any],
                 *, policy_path: Path | None = None,
                 output_dir: Path | None = None) -> dict[str, Any]:
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
