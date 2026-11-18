"""Explicit offline/live test profiles for unattended M7 qualification.

The default profile is ``offline``.  Offline tests may build native harnesses,
render through the existing offscreen Metal hosts and inspect immutable cases,
but they must never start EU4, move the desktop, use BetterDisplay or require a
live GPU capture.  Tests that genuinely need a real game process are marked
with :func:`requires_live` and only run under ``EU4_TEST_PROFILE=live``.

The profile is not a second test framework.  It is one environment value plus a
launch guard used by the existing capture/launch entry points, so an offline
test cannot silently escalate into an interactive EU4 launch.
"""

from __future__ import annotations

import functools
import importlib
import os
import unittest
from pathlib import Path

PROFILE_ENV = "EU4_TEST_PROFILE"
OFFLINE = "offline"
LIVE = "live"
PROFILES = (OFFLINE, LIVE)
# An explicit offline marker also survives a child process that was launched
# without inheriting EU4_TEST_PROFILE (for example a test runner nested in a
# shell pipeline).
OFFLINE_ONLY_ENV = "EU4_OFFLINE_ONLY"

# Tests that are required for the offline gate.  The full unittest discovery
# still executes every portable module; this manifest exists so deleting or
# renaming one of these qualification suites fails a normal test instead of
# quietly shrinking coverage.  A module may be added here only when it is
# deterministic, portable and does not start EU4.
MANDATORY_OFFLINE_MODULES = (
    "tests.test_agent_tools",
    "tests.test_case_baseline",
    "tests.test_dsh_efficiency",
    "tests.test_dsh_pilot",
    "tests.test_failure_taxonomy",
    "tests.test_gfx_frame_graph",
    "tests.test_gfx_replay_case",
    "tests.test_gfx_replay_case_sequence",
    "tests.test_m7_checkpoint_provenance",
    "tests.test_m7_diagnosis",
    "tests.test_m7_evidence_identity",
    "tests.test_p2_workflow",
    "tests.test_test_profiles",
)


class LiveProfileError(RuntimeError):
    """An offline profile attempted an operation that requires a live run."""

    def __init__(self, operation: str):
        super().__init__(
            f"{operation} requires EU4_TEST_PROFILE=live; the active profile is "
            f"{OFFLINE!r} and offline qualification must not launch EU4")
        self.operation = operation


def active_profile() -> str:
    """Return the active profile.

    An absent value means the historical/default offline profile.  An invalid
    value is refused rather than guessed, so a typo cannot accidentally enable
    a live run.
    """
    value = os.environ.get(PROFILE_ENV)
    if value is None or value == "":
        return OFFLINE
    if value not in PROFILES:
        raise ValueError(
            f"unknown {PROFILE_ENV}={value!r}; expected one of "
            f"{', '.join(PROFILES)}")
    return value


def profile_environment(profile: str) -> dict[str, str]:
    """Environment additions for one profile."""
    if profile not in PROFILES:
        raise ValueError(f"unknown test profile: {profile!r}")
    environment = {PROFILE_ENV: profile}
    if profile == OFFLINE:
        environment[OFFLINE_ONLY_ENV] = "1"
    else:
        environment.pop(OFFLINE_ONLY_ENV, None)
    return environment


def live_allowed() -> bool:
    """Whether an actual EU4 launch/display operation is permitted."""
    if os.environ.get(OFFLINE_ONLY_ENV) == "1":
        return False
    try:
        return active_profile() == LIVE
    except ValueError:
        return False


def ensure_live_launch(operation: str) -> None:
    """Fail closed when an offline run reaches a real launch entry point.

    Production invocations do not set ``EU4_TEST_PROFILE``; they are allowed by
    default.  The guard only constrains explicitly profiled runs.
    """
    if os.environ.get(PROFILE_ENV) is None and os.environ.get(
            OFFLINE_ONLY_ENV) != "1":
        return
    if not live_allowed():
        raise LiveProfileError(operation)


def requires_live(reason: str):
    """Mark one test that needs a real EU4/display qualification run.

    The skip is deliberate and carries a reason, so an offline gate reports it
    as a classified skip rather than an unexplained omission.
    """

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if not live_allowed():
                raise unittest.SkipTest(
                    f"live profile required: {reason}; set "
                    f"{PROFILE_ENV}={LIVE} and run this module explicitly")
            return function(*args, **kwargs)

        return wrapped

    return decorate


def mandatory_modules() -> tuple[str, ...]:
    return MANDATORY_OFFLINE_MODULES


def verify_offline_gate() -> dict:
    """Check the offline gate manifest without executing the suites.

    Import errors are reported per module.  This is the cheap preflight used by
    CI and by ``python -m automation.test_profiles verify``; the actual suite
    execution is still ``unittest`` through the existing test runner.
    """
    problems: list[dict] = []
    modules: list[dict] = []
    for name in MANDATORY_OFFLINE_MODULES:
        try:
            module = importlib.import_module(name)
            path = Path(getattr(module, "__file__", "") or "")
            if not path.is_file():
                problems.append({"code": "missing_test_module",
                                 "detail": f"{name} has no source file"})
            modules.append({"module": name, "path": str(path),
                            "present": path.is_file()})
        except Exception as exc:  # noqa: BLE001 - report the import failure
            problems.append({"code": "unimportable_test_module",
                             "detail": f"{name}: {type(exc).__name__}: {exc}"})
            modules.append({"module": name, "path": None, "present": False})
    if len(set(MANDATORY_OFFLINE_MODULES)) != len(MANDATORY_OFFLINE_MODULES):
        problems.append({"code": "duplicate_mandatory_module",
                         "detail": "the mandatory offline manifest repeats a "
                                   "module"})
    try:
        profile = active_profile()
    except ValueError as exc:
        problems.append({"code": "invalid_profile", "detail": str(exc)})
        profile = None
    return {
        "schema": "eu4-test-profile/v1",
        "ok": not problems,
        "profile": profile,
        "offline_default": profile == OFFLINE,
        "live_allowed": live_allowed(),
        "mandatory_modules": modules,
        "problems": problems,
        "problems_count": len(problems),
    }


def _parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--profile", choices=PROFILES, default=None,
                        help="require this profile (default: inspect the "
                             "environment, which defaults to offline)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.profile is not None:
        os.environ[PROFILE_ENV] = args.profile
        if args.profile == OFFLINE:
            os.environ[OFFLINE_ONLY_ENV] = "1"
    result = verify_offline_gate()
    if args.profile is not None and result.get("profile") != args.profile:
        result["problems"].append({
            "code": "profile_mismatch",
            "detail": f"active profile is {result.get('profile')!r}, expected "
                      f"{args.profile!r}"})
        result["problems_count"] = len(result["problems"])
        result["ok"] = False
    import json
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
