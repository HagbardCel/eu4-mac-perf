"""Resolve and verify the one Python interpreter the diagnostic core uses.

The DSH native tools, the shell launchers and the preflight must all agree on
which interpreter runs ``automation.agent_tools``.  An earlier canary proved
that they can silently disagree: the native ``eu4_replay`` tool spawned a
PATH-resolved interpreter without NumPy while the documented CLI interpreter
had it.

This module is the single resolver:

* an explicitly requested interpreter (``--requested`` or
  ``EU4_DSH_PYTHON``) is always used as given -- it is never replaced by a
  different installation when a dependency is missing;
* without an explicit request the current interpreter's absolute path is used;
* the selected path is absolute, is checked for executability and is probed
  for the required dependencies in one subprocess (or in-process when it is
  this interpreter);
* a missing dependency is a hard failure with an actionable reason.

It deliberately imports only the standard library so it can bootstrap itself
with an interpreter that lacks NumPy.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "eu4-python-runtime/v1"
DEFAULT_DEPENDENCY = "numpy"

_PROBE = r"""
import importlib.util, json, sys
value = {
    "executable": sys.executable,
    "version": list(sys.version_info[:3]),
    "dependencies": {},
}
for name in sys.argv[1:]:
    spec = importlib.util.find_spec(name)
    value["dependencies"][name] = None if spec is None else (spec.origin or "present")
print(json.dumps(value))
"""


def absolute(candidate: str | os.PathLike[str], *,
             path: str | None = None) -> str:
    """Resolve one path spelling to an absolute, normalized executable path.

    ``path`` overrides the process PATH for a bare name; this keeps a caller's
    explicit environment from being silently replaced by the process one.
    """
    text = os.fspath(candidate)
    if not text:
        raise ValueError("empty interpreter path")
    if os.sep in text or (os.altsep and os.altsep in text):
        # A path spelling is resolved from the caller's cwd, never searched.
        return str(Path(text).expanduser().resolve())
    found = shutil.which(text, path=path)
    if not found:
        raise ValueError(f"cannot resolve {text!r} on PATH")
    return str(Path(found).resolve())


def _executable(candidate: str, *, path: str | None = None) -> str:
    value = absolute(candidate, path=path)
    if not os.path.isfile(value):
        raise ValueError(f"interpreter does not exist: {value}")
    if not os.access(value, os.X_OK):
        raise ValueError(f"interpreter is not executable: {value}")
    return value


def probe(executable: str, dependencies: tuple[str, ...] = (DEFAULT_DEPENDENCY,)) -> dict:
    """Return interpreter identity and dependency availability.

    The probe never imports the dependency itself, so a missing NumPy cannot
    raise during discovery.  When ``executable`` is this process's interpreter
    the result is computed in-process; otherwise exactly one subprocess runs.
    """
    resolved = _executable(executable)
    if os.path.samefile(resolved, sys.executable):
        dependencies_found: dict[str, str | None] = {}
        for name in dependencies:
            spec = importlib.util.find_spec(name)
            dependencies_found[name] = None if spec is None else (spec.origin or "present")
        value = {
            "executable": str(Path(sys.executable).resolve()),
            "version": list(sys.version_info[:3]),
            "dependencies": dependencies_found,
        }
    else:
        completed = subprocess.run(
            [resolved, "-c", _PROBE, *dependencies],
            capture_output=True, text=True, check=False, timeout=60,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(
                f"cannot probe interpreter {resolved}: "
                f"{detail[-800:] or 'no output'}")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"interpreter probe returned invalid JSON: {exc}") from exc
    missing = [name for name, origin in value["dependencies"].items() if origin is None]
    value.update({
        "schema": SCHEMA,
        "absolute": True,
        "required": list(dependencies),
        "missing": missing,
        "ok": not missing,
        "reason": "" if not missing else (
            f"{resolved} cannot import {', '.join(missing)}; install "
            f"{', '.join(missing)} for this interpreter or point "
            f"EU4_DSH_PYTHON at one that has it"),
    })
    return value


def resolve(requested: str | None = None,
            dependencies: tuple[str, ...] = (DEFAULT_DEPENDENCY,),
            *,
            environ: dict | None = None) -> dict:
    """Resolve the interpreter and verify its dependencies.

    ``requested`` (or ``EU4_DSH_PYTHON``) is authoritative: if it is set, no
    fallback to another installation happens, even when a dependency is
    missing.  Without an explicit request the running interpreter is the
    single source of truth -- the same interpreter that imported this module
    also runs the tool core.
    """
    environment = os.environ if environ is None else environ
    configured = environment.get("EU4_DSH_PYTHON") or None
    explicit = configured if requested is None else requested
    source = "requested" if requested is not None else (
        "EU4_DSH_PYTHON" if configured else "current")
    search_path = environment.get("PATH") if environ is not None else None
    try:
        executable = _executable(explicit if explicit else sys.executable,
                                 path=search_path)
    except (OSError, ValueError) as exc:
        return {
            "schema": SCHEMA,
            "ok": False,
            "absolute": True,
            "source": source,
            "requested": explicit or "",
            "executable": str(explicit or ""),
            "required": list(dependencies),
            "missing": list(dependencies),
            "reason": f"cannot resolve the configured interpreter: {exc}",
        }
    value = probe(executable, dependencies)
    # The launcher will spawn exactly this path; keep the probe's self-report
    # for diagnosis but make the selected path canonical.
    value["probe_executable"] = value.get("executable")
    value["executable"] = executable
    value["source"] = source
    value["requested"] = str(explicit or "")
    return value


def require(dependencies: tuple[str, ...] = (DEFAULT_DEPENDENCY,)) -> dict:
    """Return ``resolve()`` for this process, raising when it is unusable."""
    value = resolve(None, dependencies)
    if not value["ok"]:
        raise RuntimeError(value["reason"])
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("resolve", "probe"):
        item = sub.add_parser(name)
        item.add_argument("--requested", default=None)
        item.add_argument("--executable", default=None)
        item.add_argument("--require", action="append", default=[])
        item.add_argument("--json", action="store_true")
        item.add_argument("--print-path", action="store_true",
                          help="print only the selected absolute path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    dependencies = tuple(args.require or (DEFAULT_DEPENDENCY,))
    try:
        if args.operation == "resolve":
            value = resolve(args.requested, dependencies)
        else:
            value = probe(args.executable or sys.executable, dependencies)
    except (OSError, ValueError, RuntimeError) as exc:
        value = {
            "schema": SCHEMA,
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if args.print_path:
        if value.get("ok"):
            print(value["executable"])
            return 0
        print(value.get("reason", "interpreter resolution failed"), file=sys.stderr)
        return 1
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0 if value.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
