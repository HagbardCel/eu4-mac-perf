"""Catalog and held-out boundary helpers for the G3a adapter seam.

Two trust properties are checked here, and both are *assertions over the existing
certified P3 surface* rather than a reimplementation of it:

* the agent-facing catalog is exactly the five tools
  ``inspect / fixtures / coverage / test / diff`` and ``certify`` is not
  discoverable through it;
* a materialized agent workspace contains no held-out fixture, no Oracle source
  and no controller token.

The held-out check is a **detection** net over an application-level boundary
(the same boundary P3 documents). It is not an OS sandbox.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from oracle.slice import bridge, corpus
from oracle.slice.opencode import held_out_fingerprints, scan_for_leaks

#: The five tools the agent may see. Kept in one place so the descriptor and the
#: adapter validate against the same vocabulary.
AGENT_VISIBLE_TOOLS: tuple[str, ...] = ("inspect", "fixtures", "coverage", "test", "diff")

#: Names that must never be reachable from the agent-facing catalog. OpenCode
#: prefixes a raw name with the server name, so both spellings are listed.
CONTROLLER_ONLY_TOOL_NAMES: tuple[str, ...] = (
    "certify",
    "oracle_certify",
    "certify_candidate",
    "oracle_certify_candidate",
)

#: Files that belong to the trusted host, never to the workspace.
CONTROLLER_ONLY_FILES: tuple[str, ...] = (
    "controller.token",
    "budget.sqlite3",
    "run-state.json",
    "tool-calls.json",
    "run-id.txt",
)

#: Suffixes read as text when scanning a workspace for held-out fingerprints.
_TEXT_SUFFIXES = (".c", ".h", ".json", ".md", ".txt", ".log")
#: Do not read an arbitrarily large agent-written file into memory.
_MAX_SCAN_BYTES = 4 * 1024 * 1024


class CatalogError(RuntimeError):
    """The agent-facing catalog is not the certified five-tool surface."""


class HeldOutLeak(RuntimeError):
    """Held-out material, Oracle source or a controller secret reached a workspace."""


def tool_definitions() -> tuple[dict[str, Any], ...]:
    """The definitions the certified P3 bridge actually serves."""
    return bridge.TOOL_DEFINITIONS


def tool_names(definitions: Iterable[dict[str, Any]] | None = None) -> tuple[str, ...]:
    source = tool_definitions() if definitions is None else definitions
    return tuple(str(definition["name"]) for definition in source)


def validate_catalog(definitions: Iterable[dict[str, Any]] | None = None) -> None:
    """Raise unless the catalog is exactly the five agent-visible tools."""
    source = tool_definitions() if definitions is None else definitions
    names = tool_names(source)
    if names != AGENT_VISIBLE_TOOLS:
        raise CatalogError(
            f"agent-facing catalog is {list(names)}, expected {list(AGENT_VISIBLE_TOOLS)}")
    forbidden = [name for name in names
                 if name in CONTROLLER_ONLY_TOOL_NAMES or "certify" in name.lower()]
    if forbidden:
        raise CatalogError(f"controller-only tool exposed to the agent: {forbidden}")
    for definition in source:
        description = str(definition.get("description", "")).lower()
        if "certify" in description:
            raise CatalogError("a catalog description mentions controller-only certification")


def catalog_report(definitions: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A machine-checkable statement of the catalog boundary."""
    source = tool_definitions() if definitions is None else definitions
    validate_catalog(source)
    names = tool_names(source)
    return {
        "tools": list(names),
        "count": len(names),
        "certify_discoverable": False,
        "controller_only": list(CONTROLLER_ONLY_TOOL_NAMES),
        "server": bridge.SERVER_NAME,
    }


class CatalogBoundTools:
    """The agent's tool client, restricted to a package's tool vocabulary.

    The certified bridge already refuses an unknown tool. This wrapper is a second,
    cheap assertion *inside the seam*: a package or a stubbed agent cannot even
    reach the bridge with ``certify`` or a widened catalog, so the five-tool
    surface is preserved by construction rather than by luck.
    """

    def __init__(self, tools: Any, *, allowed: Iterable[str] = AGENT_VISIBLE_TOOLS):
        self._tools = tools
        self._allowed = frozenset(allowed)
        if self._allowed != frozenset(AGENT_VISIBLE_TOOLS):
            raise CatalogError(
                f"package tool vocabulary {sorted(self._allowed)} is not the certified "
                f"five-tool surface {list(AGENT_VISIBLE_TOOLS)}")

    @property
    def calls(self) -> int:
        return int(getattr(self._tools, "calls", 0))

    def call(self, method: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if method not in self._allowed:
            raise CatalogError(f"tool {method!r} is not in the agent-facing catalog")
        return self._tools.call(method, args or {})


def _fingerprint_scan_problems(
        path: Path, name: str, fingerprints: list[str]) -> list[str]:
    if not fingerprints or path.suffix not in _TEXT_SUFFIXES:
        return []
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_SCAN_BYTES + 1)
    except OSError:
        return [f"{name}: unreadable"]
    if len(data) > _MAX_SCAN_BYTES:
        return [f"{name}: scan incomplete"]
    leaks = scan_for_leaks(data.decode("utf-8", "ignore"), fingerprints)
    if leaks:
        return [f"{name}: {len(leaks)} held-out fingerprint(s) present"]
    return []


def held_out_report(workspace: Path, *, fixtures_root: Path | None = None) -> dict[str, Any]:
    """Check that a materialized workspace cannot reach held-out material.

    Returns a report with ``ok`` and a list of problems. Held-out identifiers are
    never echoed into the report - only the workspace-relative path that carried a
    fingerprint - so the check itself cannot become a leak.
    """
    workspace = Path(workspace).resolve()
    held_root = (Path(fixtures_root) if fixtures_root else corpus.FIXTURES_DIR) / corpus.HELD_OUT
    held_root = held_root.resolve()
    problems: list[str] = []
    files: list[Path] = []
    for path in sorted(workspace.rglob("*")):
        name = path.relative_to(workspace).as_posix()
        if path.is_symlink():
            problems.append(f"{name}: symlinks are not allowed in the workspace")
            continue
        if not path.is_file():
            continue
        files.append(path)

    relative = [path.relative_to(workspace).as_posix() for path in files]

    if workspace == held_root or held_root.is_relative_to(workspace):
        problems.append("the workspace contains the held-out corpus directory")
    if "held_out" in workspace.parts:
        problems.append("the workspace itself is inside the held-out corpus")

    try:
        fingerprints = held_out_fingerprints(
            Path(fixtures_root) if fixtures_root is not None else None)
    except Exception as exc:  # noqa: BLE001 - an unavailable corpus is not "isolated"
        fingerprints = []
        problems.append(f"held-out corpus unavailable, so isolation cannot be verified: "
                        f"{type(exc).__name__}")

    for path, name in zip(files, relative):
        if path.resolve().is_relative_to(held_root):
            problems.append(f"{name}: resolves into the held-out corpus")
        if "held_out" in path.parts:
            problems.append(f"{name}: held-out path component")
        if path.name in CONTROLLER_ONLY_FILES:
            problems.append(f"{name}: controller-only file is present")
        if path.suffix == ".py":
            problems.append(f"{name}: Oracle source is present")
        problems.extend(_fingerprint_scan_problems(path, name, fingerprints))

    return {
        "ok": not problems,
        "workspace": str(workspace),
        "files": relative,
        "held_out_fingerprints": len(fingerprints),
        "problems": problems,
    }


def assert_isolated(workspace: Path, *, fixtures_root: Path | None = None) -> dict[str, Any]:
    """Raise :class:`HeldOutLeak` unless the workspace report is clean."""
    report = held_out_report(workspace, fixtures_root=fixtures_root)
    if not report["ok"]:
        raise HeldOutLeak("; ".join(report["problems"]))
    return report
