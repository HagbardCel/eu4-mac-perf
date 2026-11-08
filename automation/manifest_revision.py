"""Pinned-revision manifest verification helpers.

``automation/verification_manifest.py`` is frozen by the A0 trusted-validation
manifest and must stay byte-identical. These helpers add verification against a
specific Git revision without changing that module.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from automation.verification_manifest import verify_manifest


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """True when *ancestor* is reachable from *descendant* in the repo."""
    completed = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", ancestor, descendant],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def verify_manifest_at_commit(repo: Path, manifest: dict, commit: str) -> list[str]:
    """Verify every relative member against the tree at *commit*.

    ``verify_manifest`` checks the working checkout. This additionally proves
    the trusted files have the declared bytes at the pinned trusted revision,
    so a mutable checkout cannot silently substitute them.
    """
    errors = verify_manifest(repo, manifest)
    for member in manifest.get("members") or []:
        if not isinstance(member, dict):
            continue
        rel = member.get("path")
        expected = member.get("sha256")
        if not isinstance(rel, str) or not isinstance(expected, str):
            continue
        if Path(rel).is_absolute():
            continue
        completed = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            errors.append(f"member missing at {commit}: {rel}")
            continue
        actual = hashlib.sha256(completed.stdout).hexdigest()
        if actual != expected:
            errors.append(f"hash mismatch at {commit} for {rel}")
    return errors
