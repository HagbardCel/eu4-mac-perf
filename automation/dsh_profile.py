"""Resolve and verify the packages actually linked into a DSH profile.

`preflight.sh` previously hashed the tracked source ``package.json`` while the
profile could link ``@eu4/dsh-goal`` or ``@eu4/dsh-tools`` from a different
checkout. Preflight must validate what DSH will execute, so this module resolves
the package through the profile's own ``node_modules`` and checks both package
identity and the canonical checkout destination.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class ProfileError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def package_json_path(profile_dir: Path | str, name: str) -> Path:
    """The package.json that Node resolves for ``name`` in this profile."""
    return Path(profile_dir) / "node_modules" / name / "package.json"


def resolve_installed_package(profile_dir: Path | str, name: str) -> dict:
    """Read the installed package manifest and its canonical package root."""
    path = package_json_path(profile_dir, name)
    if not path.is_file():
        raise ProfileError(
            "not_installed",
            f"{name} is not installed in {profile_dir}; run dsh/scripts/provision.sh --init",
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileError("manifest", f"{path}: {exc}") from exc
    root = path.parent.resolve()
    return {
        "name": manifest.get("name"),
        "package_json": str(path),
        "root": str(root),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def link_matches(link: Path | str, expected: Path | str) -> bool:
    """True when ``link`` resolves to the canonical ``expected`` package root."""
    try:
        return Path(link).resolve() == Path(expected).resolve()
    except OSError:
        return False


def verify_local_plugin(plugin_id: str, spec: dict, profile_dir: Path | str,
                        infra: Path | str) -> list[str]:
    """Return the problems with one tracked local plugin link.

    ``spec`` is one ``required_plugins`` entry. Its ``package_json`` is the
    repository-relative expected manifest; the installed package is resolved
    from the profile, not from that source path.
    """
    relative = spec.get("package_json")
    if not relative:
        return []
    expected = Path(infra) / relative
    expected_root = expected.parent.resolve()
    try:
        installed = resolve_installed_package(profile_dir, spec["name"])
    except ProfileError as exc:
        return [str(exc)]

    problems: list[str] = []
    if installed["name"] != spec["name"]:
        problems.append(
            f"{plugin_id} installed manifest names {installed['name']!r}, "
            f"expected {spec['name']!r}"
        )
    expected_digest = spec.get("package_json_sha256")
    if not expected_digest:
        problems.append(f"{plugin_id} has no package_json_sha256 pin")
    elif installed["sha256"] != expected_digest:
        problems.append(
            f"{plugin_id} installed package.json sha256 {installed['sha256']} "
            f"!= pinned {expected_digest}"
        )
    if Path(installed["root"]) != expected_root:
        problems.append(
            f"{plugin_id} is linked from {installed['root']}, but this checkout "
            f"provides {expected_root}; run dsh/scripts/provision.sh --init or "
            f"relink with dsh plugin --profile <profile> add link:{expected_root}"
        )
    return problems
