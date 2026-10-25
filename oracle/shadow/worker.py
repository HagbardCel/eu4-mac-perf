"""One owned snapshot per process. The supervisor owns the queue and the cap.

The process group and the retained-output bound live in
:mod:`oracle.shadow.supervisor`. This entry point only reads the job directory
the supervisor prepared and calls the frozen replay path.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from oracle.shadow.supervisor import _report, execute_replay


def _write_report(job_dir: Path, report: dict) -> None:
    target = job_dir / "report.json"
    temporary = job_dir / "report.json.tmp"
    data = json.dumps(report, sort_keys=True).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 1:
        print("usage: python -m oracle.shadow.worker <job-dir>", file=sys.stderr)
        return 2
    job_dir = Path(arguments[0])
    try:
        report = execute_replay(job_dir)
    except Exception as error:
        report = _report(
            "crash", "infrastructure", [f"infrastructure:worker: {error}"], 0)
    try:
        _write_report(job_dir, report)
    except OSError as error:
        print(f"infrastructure:worker: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
