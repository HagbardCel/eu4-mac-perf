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
* the selected path is absolute, is checked for executability and is always
  probed in a subprocess of that exact executable;
* a virtualenv entry point (for example ``.venv/bin/python3``) keeps its path;
  it is never dereferenced to the base interpreter, so a probe cannot silently
  test the system environment instead of the virtualenv;
* dependencies are imported, not merely located: an import failure is a
  missing dependency even when ``find_spec`` would return a module;
* a missing dependency is a hard failure with an actionable reason.

It deliberately imports only the standard library so it can bootstrap itself
with an interpreter that lacks NumPy.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA = "eu4-python-runtime/v1"
DEFAULT_DEPENDENCY = "numpy"

_PROBE = r"""
import importlib, json, sys
value = {
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "virtualenv": sys.prefix != sys.base_prefix,
    "version": list(sys.version_info[:3]),
    "dependencies": {},
}
for name in sys.argv[1:]:
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - an import failure is the finding
        value["dependencies"][name] = {
            "ok": False,
            "origin": None,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    else:
        value["dependencies"][name] = {
            "ok": True,
            "origin": getattr(module, "__file__", None) or "present",
            "version": str(getattr(module, "__version__", "")) or None,
            "error": None,
        }
print(json.dumps(value))
"""


def absolute(candidate: str | os.PathLike[str], *,
             path: str | None = None) -> str:
    """Expand a path spelling to an absolute executable path.

    The final symlink is deliberately preserved: ``.venv/bin/python3`` is a
    distinct environment from the base interpreter it points at.  ``path``
    overrides the process PATH for a bare name so a caller's explicit
    environment is not silently replaced.
    """
    text = os.fspath(candidate)
    if not text:
        raise ValueError("empty interpreter path")
    text = os.path.expanduser(text)
    if os.sep not in text and not (os.altsep and os.altsep in text):
        found = shutil.which(text, path=path)
        if not found:
            raise ValueError(f"cannot resolve {text!r} on PATH")
        text = found
    # abspath normalizes the spelling; it does not dereference the final link.
    return os.path.abspath(text)


def _executable(candidate: str, *, path: str | None = None) -> str:
    value = absolute(candidate, path=path)
    if not os.path.isfile(value):
        raise ValueError(f"interpreter does not exist: {value}")
    if not os.access(value, os.X_OK):
        raise ValueError(f"interpreter is not executable: {value}")
    return value


def probe(executable: str, dependencies: tuple[str, ...] = (DEFAULT_DEPENDENCY,)) -> dict:
    """Return identity and dependency availability of exactly this executable.

    The probe always spawns the selected interpreter.  There is no in-process
    shortcut: two virtualenvs can share a base binary, and an in-process check
    would test the launcher environment instead of the one that will run the
    diagnostic core.  Dependencies are imported, so a broken module is reported
    as unusable even when its specification is present.
    """
    selected = _executable(executable)
    completed = subprocess.run(
        [selected, "-c", _PROBE, *dependencies],
        capture_output=True, text=True, check=False, timeout=60,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(
            f"cannot probe interpreter {selected}: "
            f"{detail[-800:] or 'no output'}")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"interpreter probe returned invalid JSON: {exc}") from exc
    dependencies_found = value.get("dependencies") or {}
    missing = [
        name for name in dependencies
        if (dependencies_found.get(name) or {}).get("ok") is not True
    ]
    errors = {
        name: (dependencies_found.get(name) or {}).get("error")
        for name in missing
    }
    value.update({
        "schema": SCHEMA,
        "absolute": True,
        # The selected path is authoritative and exactly what was probed.
        "executable": selected,
        "probe_executable": value.get("executable"),
        "environment": value.get("prefix") or selected,
        "prefix": value.get("prefix"),
        "base_prefix": value.get("base_prefix"),
        "virtualenv": bool(value.get("virtualenv")),
        "required": list(dependencies),
        "missing": missing,
        "dependency_errors": errors,
        "ok": not missing,
        "reason": "" if not missing else (
            f"{selected} cannot import {', '.join(missing)}; install "
            f"{', '.join(missing)} for this interpreter or point "
            f"EU4_DSH_PYTHON at one that has it"
            + (f" ({errors[missing[0]]})" if errors.get(missing[0]) else "")),
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
        attempted = str(explicit or "")
        try:
            attempted = absolute(explicit or sys.executable, path=search_path)
        except (OSError, ValueError):
            pass
        return {
            "schema": SCHEMA,
            "ok": False,
            "absolute": True,
            "source": source,
            "requested": str(explicit or ""),
            "executable": attempted,
            "required": list(dependencies),
            "missing": list(dependencies),
            "reason": f"cannot resolve the configured interpreter: {exc}",
        }
    value = probe(executable, dependencies)
    value["source"] = source
    value["requested"] = str(explicit or "")
    value["configured"] = str(explicit or "")
    return value


def environment_root(executable: str | os.PathLike[str]) -> str | None:
    """Return the virtualenv root containing ``executable``, if any.

    This identifies an environment independently of the binary path: two
    virtualenvs can share the same base interpreter, and a system interpreter
    can be reached through several symlinks.  A ``pyvenv.cfg`` above the
    executable is the only accepted marker.
    """
    try:
        path = Path(absolute(executable))
    except (OSError, ValueError):
        return None
    for candidate in (path.parent, path.parent.parent):
        if (candidate / "pyvenv.cfg").is_file():
            return str(candidate)
    return None


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
