"""Local artifact paths for the pinned EU IV binary."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RE_DIR = Path(__file__).resolve().parent
GHIDRA_SCRIPTS = RE_DIR / "ghidra"
TARGET_PATH = RE_DIR / "target.json"


class ReError(Exception):
    pass


def load_target() -> dict:
    return json.loads(TARGET_PATH.read_text())


def executable_path() -> Path:
    override = os.environ.get("EU4_EXE")
    if override:
        return Path(override).expanduser()
    return Path(load_target()["default_executable"])


def binary_sha256() -> str:
    return load_target()["sha256"]


def local_binary_root(digest: str | None = None) -> Path:
    digest = digest or binary_sha256()
    override = os.environ.get("EU4_RE_EXPORT")
    if override:
        return Path(override).expanduser()
    return ROOT / ".local" / "eu4" / digest


def export_root(binary_root: Path, baseline_revision: str) -> Path:
    return binary_root / "exports" / baseline_revision


def resolve_export_dir(binary_root: Path, baseline_revision: str | None) -> Path:
    exports = binary_root / "exports"
    if baseline_revision:
        return export_root(binary_root, baseline_revision)
    if not exports.is_dir():
        raise ReError("no exports directory; run import first")
    revisions = sorted(p.name for p in exports.iterdir() if p.is_dir())
    if not revisions:
        raise ReError("no export revisions found")
    return export_root(binary_root, revisions[-1])


def ghidra_project_meta_path(binary_root: Path) -> Path:
    return binary_root / "ghidra_project.json"


def ghidra_work_root(digest: str | None = None) -> Path:
    """Directory for Ghidra project files (must not live under dot-prefixed paths)."""
    digest = digest or binary_sha256()
    export_override = os.environ.get("EU4_RE_EXPORT")
    if export_override:
        return Path(export_override).expanduser()
    work_override = os.environ.get("EU4_GHIDRA_WORK")
    if work_override:
        return Path(work_override).expanduser() / digest
    return Path.home() / "GhidraEU4" / digest


def ghidra_project_dir(binary_root: Path) -> Path:
    if os.environ.get("EU4_RE_EXPORT"):
        return binary_root / "ghidra-project"
    return ghidra_work_root(binary_root.name) / "ghidra-project"


def assert_ghidra_safe_path(path: Path) -> None:
    """Ghidra headless rejects path components starting with '.' (e.g. .local)."""
    resolved = path.expanduser().resolve()
    for part in resolved.parts:
        if part.startswith("."):
            raise ReError(
                f"Ghidra project path has forbidden component {part!r}: {resolved}. "
                "Use the default ~/GhidraEU4/<sha>/ghidra-project layout or set EU4_RE_EXPORT."
            )


def default_baseline_revision() -> str:
    from datetime import date

    return f"auto-v1-{date.today().isoformat().replace('-', '')}"
