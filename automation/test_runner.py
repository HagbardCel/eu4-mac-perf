"""Focused, inspectable test execution for autonomous agents.

The M7.0 canary launched the full suite three times and waited on it twice more
than necessary.  This utility is deliberately small: it runs ``unittest``
modules (or the full suite once), preserves the child exit code, writes the
complete log to a known location, returns a compact JSON summary, and refuses
to start an identical suite that is already running.

It is not a CI scheduler.  It does not discover work, retry, parallelise or
schedule anything.

Usage::

    python -m automation.test_runner run \
        --module tests.test_agent_tools --module tests.test_gfx_case
    python -m automation.test_runner status --id <run-id>
    python -m automation.test_runner list --limit 10
    python -m automation.test_runner compare --base <id|path> --candidate <id|path>

State lives under ``.local/test-runs`` by default (gitignored), so the log and
the summary survive the shell that started them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from automation import test_profiles

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "eu4-test-run/v1"
COMPARE_SCHEMA = "eu4-test-compare/v1"
STATE_ENV = "EU4_TEST_RUN_ROOT"
DEFAULT_STATE = ROOT / ".local" / "test-runs"

_RAN = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s")
_OK = re.compile(r"^OK(?: \((.*)\))?")
_FAILED = re.compile(r"^FAILED \((.*)\)")
_FAILURE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]*)\)")
_SKIPPED = re.compile(r"skipped=(\d+)")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _state_dir(value: str | os.PathLike[str] | None = None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    configured = os.environ.get(STATE_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_STATE


def _suite(modules: list[str], pattern: str | None, cwd: Path,
           profile: str = test_profiles.OFFLINE) -> dict:
    modules = list(modules)
    if modules:
        kind = "focused"
        argv = ["-m", "unittest", *modules]
    else:
        kind = "full"
        argv = ["-m", "unittest", "discover", "-s", "tests"]
        if pattern:
            kind = "pattern"
            argv += ["-p", pattern]
    key = hashlib.sha256(json.dumps(
        {"argv": argv, "cwd": str(cwd), "profile": profile},
        sort_keys=True).encode("utf-8")).hexdigest()[:16]
    # Cwd is deliberately not part of the selection identity: the same modules
    # may legitimately run in separate worktrees.  The executable selection is
    # what a base/candidate comparison must match.
    selection = {"kind": kind, "modules": sorted(modules),
                 "pattern": pattern or None, "profile": profile}
    return {"kind": kind, "modules": modules, "pattern": pattern,
            "argv": argv, "key": key, "selection": selection}


def _pending_envelope(duplicate: dict | None, message: str) -> dict:
    """A duplicate invocation is pending, not a completed verification."""
    return {
        "schema": SCHEMA,
        "ok": False,
        "status": "pending",
        "pending": True,
        "completed": False,
        "duplicate_of": (duplicate or {}).get("id"),
        "exit_code": None,
        "record": duplicate,
        "log": (duplicate or {}).get("log"),
        "message": message,
        "reason": message,
        "truncated": False,
    }


def _record_path(state: Path, run_id: str) -> Path:
    return state / "runs" / f"{run_id}.json"


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def _alive(pid) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _records(state: Path) -> list[dict]:
    rows = []
    directory = state / "runs"
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("*.json")):
        value = _read(path)
        if value:
            value["record_path"] = str(path)
            rows.append(value)
    rows.sort(key=lambda row: str(row.get("started_utc") or ""), reverse=True)
    return rows


def _running(state: Path, key: str) -> dict | None:
    for row in _records(state):
        if row.get("key") != key or row.get("status") != "running":
            continue
        if row.get("host") not in (None, os.uname().nodename):
            continue
        if _alive(row.get("pid")):
            return row
    return None


def _normalize_owner(owner: str) -> str:
    """Make a failure id stable across module and discover invocation.

    ``python -m unittest tests.test_x`` prints ``tests.test_x.Class.test``,
    while ``unittest discover`` prints ``test_x.Class.test``. Removing the
    top-level package prefix lets the same failure compare across both modes.
    """
    parts = owner.split(".")
    if len(parts) > 1 and parts[0] in ("tests", "benchmark"):
        return ".".join(parts[1:])
    return owner


def _parse_log(text: str) -> dict:
    counts = {"ran": 0, "failures": 0, "errors": 0, "skipped": 0}
    failures: list[dict] = []
    duration = None
    for line in text.splitlines():
        match = _RAN.match(line.strip())
        if match:
            counts["ran"] = int(match.group(1))
            duration = float(match.group(2))
            continue
        failed = _FAILED.match(line.strip())
        if failed:
            for part in failed.group(1).split(","):
                name, _, value = part.strip().partition("=")
                if name in counts and value.isdigit():
                    counts[name] = int(value)
            continue
        skipped = _SKIPPED.search(line.strip())
        if skipped:
            counts["skipped"] = int(skipped.group(1))
        failure = _FAILURE.match(line.strip())
        if failure:
            kind, name, owner = failure.groups()
            owner = _normalize_owner(owner)
            identifier = owner
            if owner and not owner.endswith("." + name):
                identifier = f"{owner}.{name}"
            failures.append({
                "kind": "failure" if kind == "FAIL" else "error",
                "test": name,
                "owner": owner,
                "id": identifier or name,
            })
    return {"counts": counts, "duration_seconds": duration, "failures": failures}


def _tail(path: Path, *, lines: int = 40) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            return [line.rstrip("\n") for line in source.readlines()[-lines:]]
    except OSError:
        return []


def run_suite(
    *,
    modules: list[str] | None = None,
    pattern: str | None = None,
    cwd: str | os.PathLike[str] | None = None,
    state_dir: str | os.PathLike[str] | None = None,
    label: str | None = None,
    base_ref: str | None = None,
    timeout: float = 3600.0,
    compare_to: str | None = None,
    profile: str = test_profiles.OFFLINE,
    min_tests: int = 1,
) -> dict:
    """Run one focused unittest invocation, or return the in-flight duplicate."""
    if profile not in test_profiles.PROFILES:
        raise ValueError(
            f"unknown test profile: {profile!r}; expected one of "
            f"{', '.join(test_profiles.PROFILES)}")
    if profile == test_profiles.LIVE and not (modules or pattern):
        raise ValueError(
            "the live profile must name an explicit module or pattern; it is "
            "never selected implicitly for a full-suite run")
    if type(min_tests) is not int or min_tests < 1:
        raise ValueError("min_tests must be a positive integer")
    state = _state_dir(state_dir)
    workdir = Path(cwd).expanduser().resolve() if cwd else ROOT
    if not workdir.is_dir():
        raise ValueError(f"cwd is not a directory: {workdir}")
    suite = _suite(list(modules or []), pattern, workdir, profile)
    duplicate = _running(state, suite["key"])
    if duplicate is not None:
        return _pending_envelope(
            duplicate,
            "an identical suite is already running; its current status is "
            "returned instead of starting another copy")
    run_id = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
              + "-" + uuid.uuid4().hex[:8])
    record_path = _record_path(state, run_id)
    log_path = state / "runs" / f"{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": SCHEMA,
        "id": run_id,
        "key": suite["key"],
        "kind": suite["kind"],
        "modules": suite["modules"],
        "pattern": suite["pattern"],
        "selection": suite["selection"],
        "profile": profile,
        "argv": suite["argv"],
        "cwd": str(workdir),
        "label": label,
        "base_ref": base_ref,
        "host": os.uname().nodename,
        "pid": os.getpid(),
        "started_utc": _now(),
        "status": "running",
        "pending": True,
        "completed": False,
        "exit_code": None,
        "log": str(log_path),
        "record_path": str(record_path),
    }
    lock_path = state / "locks" / f"{suite['key']}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            duplicate = _running(state, suite["key"])
            if duplicate is not None:
                return _pending_envelope(
                    duplicate,
                    "an identical suite holds the execution lock")
            return {
                "schema": SCHEMA,
                "ok": False,
                "status": "busy",
                "reason": ("another test invocation holds the execution lock "
                           f"for this suite: {lock_path}"),
                "log": None,
                "truncated": False,
            }
        record["lock"] = str(lock_path)
        _write(record_path, record)
        started = time.monotonic()
        environment = dict(os.environ)
        environment.pop("EU4_DSH_PYTHON", None)
        if profile == test_profiles.LIVE:
            environment.pop(test_profiles.OFFLINE_ONLY_ENV, None)
        environment.update(test_profiles.profile_environment(profile))
        existing_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (str(workdir), existing_path) if item)
        status = "error"
        exit_code = None
        timed_out = False
        with log_path.open("w", encoding="utf-8") as log:
            child = subprocess.Popen(
                [sys.executable, *suite["argv"]],
                cwd=str(workdir), env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            record["child_pid"] = child.pid
            _write(record_path, record)

            def stop() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    child.send_signal(signal.SIGTERM)
                except OSError:
                    pass

                def hard_kill() -> None:
                    if child.poll() is None:
                        try:
                            child.kill()
                        except OSError:
                            pass

                killer = threading.Timer(10.0, hard_kill)
                killer.daemon = True
                killer.start()

            watchdog = threading.Timer(float(timeout), stop)
            watchdog.daemon = True
            watchdog.start()
            assert child.stdout is not None
            try:
                for line in child.stdout:
                    log.write(line)
                    log.flush()
                exit_code = child.wait()
            finally:
                watchdog.cancel()
                try:
                    child.stdout.close()
                except OSError:
                    pass
            if timed_out:
                try:
                    exit_code = child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    exit_code = child.wait()
                log.write(f"\n[test_runner] timeout after {timeout}s\n")
        text = log_path.read_text(encoding="utf-8", errors="replace")
        parsed = _parse_log(text)
        if timed_out:
            status = "timeout"
        elif parsed["counts"]["ran"] < min_tests:
            # A process can exit 0 while executing nothing (or fewer tests than
            # the requested gate floor).  That is not a passing run.
            status = "incomplete"
        elif exit_code == 0:
            status = "passed"
        elif parsed["failures"]:
            status = "failed"
        else:
            status = "errored"
        failure_kind = {
            "passed": None,
            "failed": "test_failure",
            "errored": "test_error",
            "timeout": "execution_timeout",
            "incomplete": "test_not_executed",
        }.get(status, "runner_failure")
        record.update({
            "status": status,
            "pending": False,
            "completed": True,
            "failure_kind": failure_kind,
            "exit_code": exit_code,
            "selection": suite["selection"],
            "finished_utc": _now(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "counts": parsed["counts"],
            "min_tests": min_tests,
            "unittest_duration_seconds": parsed["duration_seconds"],
            "failures": parsed["failures"],
            "reason": ("" if status in ("passed", "failed")
                       else ("unittest executed zero tests"
                             if parsed["counts"]["ran"] == 0
                             else (f"unittest executed {parsed['counts']['ran']} "
                                   f"test(s), below the required floor of "
                                   f"{min_tests}"
                                   if parsed["counts"]["ran"] < min_tests
                                   else f"test run ended as {status}"))),
            "timed_out": timed_out,
            "log_bytes": log_path.stat().st_size if log_path.is_file() else 0,
        })
        comparison = None
        if compare_to:
            try:
                comparison = compare_records(compare_to, record_path,
                                             state_dir=state)
            except (OSError, ValueError) as exc:
                comparison = {"error": str(exc)}
        record["base_comparison"] = comparison
        _write(record_path, record)
        result = dict(record)
        result.update({
            "schema": SCHEMA,
            "ok": status == "passed",
            "duplicate_of": None,
            "log_tail": _tail(log_path),
            "truncated": False,
        })
        return result
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def status(run_id: str | None = None, *,
           state_dir: str | os.PathLike[str] | None = None) -> dict:
    state = _state_dir(state_dir)
    if run_id is None:
        rows = _records(state)
        if not rows:
            raise ValueError(f"no test runs under {state}")
        record = rows[0]
    else:
        record = _read(_record_path(state, run_id))
        if not record:
            candidate = Path(run_id).expanduser()
            record = _read(candidate) if candidate.is_file() else {}
            if not record:
                raise ValueError(f"unknown test run: {run_id}")
        if record.get("status") == "running" and not _alive(record.get("pid")):
            record["status"] = "lost"
    record = dict(record)
    log = record.get("log")
    record["running"] = record.get("status") == "running"
    record["pending"] = record["running"]
    record["completed"] = not record["running"] and record.get("exit_code") is not None
    record["log_tail"] = _tail(Path(log)) if log else []
    return record


def list_runs(*, state_dir: str | os.PathLike[str] | None = None,
              limit: int = 20) -> dict:
    state = _state_dir(state_dir)
    rows = _records(state)
    page = []
    for row in rows[:max(1, limit)]:
        page.append({
            "id": row.get("id"),
            "status": row.get("status"),
            "failure_kind": row.get("failure_kind"),
            "kind": row.get("kind"),
            "profile": row.get("profile"),
            "modules": row.get("modules"),
            "exit_code": row.get("exit_code"),
            "started_utc": row.get("started_utc"),
            "finished_utc": row.get("finished_utc"),
            "counts": row.get("counts"),
            "log": row.get("log"),
            "record_path": row.get("record_path"),
        })
    return {
        "schema": SCHEMA,
        "runs": page,
        "counts": {"total": len(rows), "returned": len(page),
                   "omitted": max(0, len(rows) - len(page))},
        "state_dir": str(state),
    }


def _load_record(value: str,
                 state: Path) -> tuple[dict, str]:
    path = Path(value).expanduser()
    if path.is_file():
        record = _read(path)
        return record, str(path)
    record = _read(_record_path(state, value))
    return record, str(_record_path(state, value))


def _selection(record: dict) -> dict | None:
    """Normalize the executed test selection, ignoring the checkout path."""
    selection = record.get("selection")
    if isinstance(selection, dict) and selection.get("kind"):
        modules = selection.get("modules")
        if not isinstance(modules, list):
            return None
        return {
            "kind": str(selection["kind"]),
            "modules": sorted(str(item) for item in modules),
            "pattern": selection.get("pattern") or None,
            "profile": selection.get("profile") or test_profiles.OFFLINE,
        }
    kind = record.get("kind")
    modules = record.get("modules")
    if not isinstance(kind, str) or not isinstance(modules, list):
        return None
    return {
        "kind": kind,
        "modules": sorted(str(item) for item in modules),
        "pattern": record.get("pattern") or None,
        "profile": record.get("profile") or test_profiles.OFFLINE,
    }


def _incomparable_reason(record: dict, label: str) -> str | None:
    """Return why a record cannot participate in a regression comparison."""
    if record.get("timed_out") is True:
        return f"{label} timed out"
    status = record.get("status")
    if status not in ("passed", "failed"):
        return f"{label} is {status or 'unknown'}, not a completed test run"
    if type(record.get("exit_code")) is not int:
        return f"{label} has no preserved child exit code"
    if record.get("completed") is False:
        return f"{label} is not marked completed"
    counts = record.get("counts")
    if not isinstance(counts, dict) or type(counts.get("ran")) is not int:
        return f"{label} has no test-count summary"
    if counts.get("ran", 0) <= 0:
        return f"{label} executed zero tests"
    if not isinstance(record.get("failures"), list):
        return f"{label} has no failure list"
    return None


def compare_records(base: str, candidate: str, *,
                    state_dir: str | os.PathLike[str] | None = None) -> dict:
    """Compare failing test IDs only when the two selections are comparable.

    Different checkout directories are allowed; the executed module/pattern
    selection must match.  Running, timed-out, interrupted, zero-test or
    summary-less invocations are ``incomparable`` rather than a clean result.
    """
    state = _state_dir(state_dir)
    base_record, base_path = _load_record(base, state)
    candidate_record, candidate_path = _load_record(candidate, state)
    if not base_record or not candidate_record:
        missing = base if not base_record else candidate
        raise ValueError(f"unknown test run: {missing}")
    base_selection = _selection(base_record)
    candidate_selection = _selection(candidate_record)
    problems = []
    if base_selection is None:
        problems.append("base selection is not recorded")
    if candidate_selection is None:
        problems.append("candidate selection is not recorded")
    if (base_selection is not None and candidate_selection is not None
            and base_selection != candidate_selection):
        problems.append(
            "base and candidate test selections differ: "
            f"{base_selection} != {candidate_selection}")
    for record, label in ((base_record, "base"),
                          (candidate_record, "candidate")):
        reason = _incomparable_reason(record, label)
        if reason:
            problems.append(reason)
    base_failures = {row.get("id") or row.get("test")
                     for row in base_record.get("failures") or []}
    candidate_failures = {row.get("id") or row.get("test")
                          for row in candidate_record.get("failures") or []}
    new = sorted(candidate_failures - base_failures)
    fixed = sorted(base_failures - candidate_failures)
    preexisting = sorted(base_failures & candidate_failures)
    comparable = not problems
    return {
        "schema": COMPARE_SCHEMA,
        "comparable": comparable,
        "base": {"id": base_record.get("id"), "path": base_path,
                 "status": base_record.get("status"),
                 "cwd": base_record.get("cwd"),
                 "selection": base_selection,
                 "failures": sorted(base_failures)},
        "candidate": {"id": candidate_record.get("id"),
                      "path": candidate_path,
                      "status": candidate_record.get("status"),
                      "cwd": candidate_record.get("cwd"),
                      "selection": candidate_selection,
                      "failures": sorted(candidate_failures)},
        "new_failures": new if comparable else [],
        "fixed_failures": fixed if comparable else [],
        "preexisting_failures": preexisting if comparable else [],
        "counts": {"base": len(base_failures),
                   "candidate": len(candidate_failures),
                   "new": len(new) if comparable else None,
                   "fixed": len(fixed) if comparable else None,
                   "preexisting": len(preexisting) if comparable else None},
        "verdict": ("incomparable" if not comparable
                    else ("no_new_failures" if not new else "new_failures")),
        "reasons": problems,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=None)
    sub = parser.add_subparsers(dest="operation", required=True)

    run = sub.add_parser("run")
    run.add_argument("--module", action="append", default=[],
                     help="unittest module, e.g. tests.test_agent_tools")
    run.add_argument("--pattern", default=None,
                     help="directory passed to unittest discover -s")
    run.add_argument("--cwd", default=None)
    run.add_argument("--label", default=None)
    run.add_argument("--base-ref", default=None,
                     help="informational base commit/ref for later comparison")
    run.add_argument("--timeout", type=float, default=3600.0)
    run.add_argument("--state-dir", dest="state_dir_run", default=None)
    run.add_argument("--compare-to", default=None,
                     help="base run id or record path to compare failures with")
    run.add_argument("--profile", choices=test_profiles.PROFILES,
                     default=test_profiles.OFFLINE,
                     help="offline (default, never launches EU4) or an explicit "
                          "live module/pattern")
    run.add_argument("--min-tests", type=int, default=1,
                     help="fail closed when fewer tests execute than this floor")

    show = sub.add_parser("status")
    show.add_argument("--id", default=None)
    show.add_argument("--state-dir", dest="state_dir_run", default=None)

    listing = sub.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--state-dir", dest="state_dir_run", default=None)

    compare = sub.add_parser("compare")
    compare.add_argument("--base", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--state-dir", dest="state_dir_run", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if getattr(args, "state_dir_run", None):
        args.state_dir = args.state_dir_run
    if args.operation == "run":
        try:
            value = run_suite(
                modules=args.module, pattern=args.pattern, cwd=args.cwd,
                state_dir=args.state_dir, label=args.label,
                base_ref=args.base_ref, timeout=args.timeout,
                compare_to=args.compare_to, profile=args.profile,
                min_tests=args.min_tests)
        except (OSError, ValueError) as exc:
            print(json.dumps({"schema": SCHEMA, "ok": False, "status": "error",
                              "reason": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps(value, indent=2, sort_keys=True))
        if value.get("pending") is True or value.get("status") == "pending":
            return 2
        if value.get("status") in ("busy", "error", "incomplete", "invalid"):
            return 2
        return int(value.get("exit_code") or 0)
    if args.operation == "status":
        try:
            value = status(args.id, state_dir=args.state_dir)
        except (OSError, ValueError) as exc:
            print(json.dumps({"schema": SCHEMA, "ok": False,
                              "reason": f"{type(exc).__name__}: {exc}"}))
            return 2
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    if args.operation == "list":
        print(json.dumps(list_runs(state_dir=args.state_dir, limit=args.limit),
                         indent=2, sort_keys=True))
        return 0
    try:
        value = compare_records(args.base, args.candidate,
                                state_dir=args.state_dir)
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": COMPARE_SCHEMA, "ok": False,
                          "reason": f"{type(exc).__name__}: {exc}"}))
        return 2
    print(json.dumps(value, indent=2, sort_keys=True))
    if value["verdict"] == "incomparable":
        return 2
    return 0 if value["verdict"] == "no_new_failures" else 1


if __name__ == "__main__":
    raise SystemExit(main())
