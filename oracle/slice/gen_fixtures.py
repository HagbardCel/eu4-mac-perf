"""Generate and verify the frozen fixture corpora for the P3 slice.

Fixtures are *frozen ground truth*: the expected observation is whatever the
reference implementation produced when the corpus was authored. This tool
documents that provenance. In CI it runs in ``--check`` mode only, which proves
the reference implementation passes every visible and held-out fixture - the
baseline qualification required before any agent is invoked. It never
regenerates fixtures during acceptance.

Usage::

    python -m oracle.slice.gen_fixtures --write    # author fixtures (dev-time)
    python -m oracle.slice.gen_fixtures --check    # verify reference passes all
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from oracle.slice import harness
from oracle.slice.corpus import FIXTURES_DIR, HELD_OUT, VISIBLE, Fixture, load_corpus

FUNCTION_ID = "synth:apply"

#: Agent-visible inputs. Mode 0 exercises the defect; the rest pin the
#: neighbouring behaviour so a repair cannot regress modes 1 and 2.
VISIBLE_INPUTS: list[tuple[str, dict[str, int]]] = [
    ("vis-000", {"mode": 0, "arg": 7, "dirty": 0, "value": 3, "counter": 5}),
    ("vis-001", {"mode": 0, "arg": 0, "dirty": 1, "value": 9, "counter": 2}),
    ("vis-002", {"mode": 1, "arg": 4, "dirty": 0, "value": 0, "counter": 10}),
    ("vis-003", {"mode": 1, "arg": -3, "dirty": 0, "value": 0, "counter": 1}),
    ("vis-004", {"mode": 2, "arg": 42, "dirty": 0, "value": 1, "counter": 1}),
    ("vis-005", {"mode": 2, "arg": -1, "dirty": 1, "value": 5, "counter": 5}),
    ("vis-006", {"mode": 7, "arg": 3, "dirty": 0, "value": 0, "counter": 0}),
    ("vis-007", {"mode": 1, "arg": 0, "dirty": 0, "value": 7, "counter": 7}),
    ("vis-008", {"mode": 0, "arg": 100, "dirty": 5, "value": 6, "counter": 7}),
    ("vis-009", {"mode": -1, "arg": 1, "dirty": 1, "value": 1, "counter": 1}),
    ("vis-010", {"mode": 0, "arg": -5, "dirty": 0, "value": 0, "counter": 0}),
    ("vis-011", {"mode": 2, "arg": 0, "dirty": 0, "value": 8, "counter": 8}),
]

#: Held-out inputs. Same contract, values the agent never saw, including
#: wraparound and extreme arguments, so a narrow fix does not generalize.
HELD_OUT_INPUTS: list[tuple[str, dict[str, int]]] = [
    ("hid-000", {"mode": 0, "arg": 123456, "dirty": 9, "value": 8, "counter": 7}),
    ("hid-001", {"mode": 0, "arg": -2147483648, "dirty": 0, "value": 0, "counter": 0}),
    ("hid-002", {"mode": 0, "arg": 2147483647, "dirty": 1, "value": 1, "counter": 1}),
    ("hid-003", {"mode": 1, "arg": 2147483647, "dirty": 0, "value": 0, "counter": 1}),
    ("hid-004", {"mode": 1, "arg": -2147483648, "dirty": 0, "value": 2, "counter": 2}),
    ("hid-005", {"mode": 2, "arg": -2147483648, "dirty": 1, "value": 1, "counter": 3}),
    ("hid-006", {"mode": 3, "arg": 0, "dirty": 0, "value": 0, "counter": 0}),
    ("hid-007", {"mode": 0, "arg": 0, "dirty": 0, "value": 0, "counter": 0}),
    ("hid-008", {"mode": 1, "arg": -1, "dirty": 0, "value": 0, "counter": 0}),
    ("hid-009", {"mode": 2, "arg": 2147483647, "dirty": 0, "value": 2147483647, "counter": 2147483647}),
]

SETS = {VISIBLE: VISIBLE_INPUTS, HELD_OUT: HELD_OUT_INPUTS}


def _template(fixture_id: str, set_name: str, inputs: dict[str, int]) -> Fixture:
    return Fixture(
        fixture_id=fixture_id,
        function_id=FUNCTION_ID,
        set_name=set_name,
        input=dict(inputs),
        expected={},
        sha256="",
        path=Path(f"{fixture_id}.json"),
    )


def _fixture_document(set_name: str, fixture_id: str, inputs: dict[str, int],
                      observation: dict) -> dict:
    return {
        "schema_version": "eu4-oracle-fixture/v1",
        "fixture_id": fixture_id,
        "function_id": FUNCTION_ID,
        "set": set_name,
        "input": dict(inputs),
        "expected": {
            "capture": {"complete": True, "lost_events": 0, "unknown_dependencies": []},
            "return_hex": observation["return_hex"],
            "exit": "normal",
            "post_memory": dict(observation["post_memory"]),
            "effects": list(observation["effects"]),
        },
    }


def _observe_reference(reference: Path, fixture: Fixture, tmp: Path) -> dict:
    build = harness.build(reference, tmp / "build_ref")
    raw, observation, problems = harness.observe(build, fixture)
    if observation is None or problems or raw.exit_kind != "normal":
        raise SystemExit(
            f"reference did not produce a complete observation for {fixture.fixture_id}: "
            f"{raw.exit_kind} {problems}"
        )
    return observation


def write_fixtures() -> int:
    reference = Path(harness.SLICE_DIR) / "reference.c"
    written = 0
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        build = harness.build(reference, tmp / "build_ref")
        for set_name, inputs in SETS.items():
            target_dir = FIXTURES_DIR / set_name
            target_dir.mkdir(parents=True, exist_ok=True)
            for fixture_id, fixture_input in inputs:
                fixture = _template(fixture_id, set_name, fixture_input)
                raw, observation, problems = harness.observe(build, fixture)
                if observation is None or problems:
                    raise SystemExit(f"reference failed {fixture_id}: {raw.exit_kind} {problems}")
                document = _fixture_document(set_name, fixture_id, fixture_input, observation)
                (target_dir / f"{fixture_id}.json").write_text(
                    json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
                )
                written += 1
    print(f"wrote {written} fixtures under {FIXTURES_DIR}")
    return 0


def check_fixtures() -> int:
    """Baseline qualification: the reference must pass every fixture."""
    reference = Path(harness.SLICE_DIR) / "reference.c"
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        build = harness.build(reference, tmp / "build_ref")
        failures: list[str] = []
        for set_name in (VISIBLE, HELD_OUT):
            fixtures = load_corpus(set_name)
            for fixture in fixtures:
                diagnostic = harness.evaluate(build, fixture, run_id=f"qualify-{set_name}")
                if diagnostic["status"] != "PASS":
                    failures.append(f"{set_name}/{fixture.fixture_id}: {diagnostic['status']}")
                _, observation, _ = harness.observe(build, fixture)
                if observation != fixture.expected:
                    failures.append(f"{set_name}/{fixture.fixture_id}: observation != frozen expected")
    if failures:
        for failure in failures:
            print(f"reference qualification FAILED: {failure}")
        return 1
    print("reference passes visible and held-out corpora")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="author fixtures from the reference")
    group.add_argument("--check", action="store_true", help="verify the reference passes all fixtures")
    args = parser.parse_args(argv)
    return write_fixtures() if args.write else check_fixtures()


if __name__ == "__main__":
    raise SystemExit(main())
