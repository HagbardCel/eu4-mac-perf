#!/usr/bin/env python3
"""Fail if the preserved EU IV Oracle v2 blueprint is not intact.

The blueprint was a local, untracked handoff; the preservation commit is its
first Git-backed identity. This checker establishes two *independent*
relationships so the promise "preserved verbatim" is continuously testable:

1. Manifest honesty  - current bytes match ``MANIFEST.json`` (path, size, SHA-256).
2. Immutability      - current bytes match the Git objects at a pinned
                       preservation commit, which the annotated tag must resolve to.

It also rejects missing files, unexpected files, symlinks/non-regular entries and
any tracked root-level OpenCode activation (untracked local configuration is
ignored, since OpenCode only loads a repository-root ``.opencode/``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

BUNDLE_DIR = "docs/proposals/EU4_Oracle_v2"
MANIFEST_NAME = "MANIFEST.json"
PRESERVATION_TAG = "oracle-v2-blueprint-20261016"
# Pinned independently of the editable manifest. A branch rebase would change this
# SHA, which is exactly the kind of drift the check is meant to surface.
PRESERVATION_COMMIT = "56207cc3a2bfc28e6bb3719c04125ed42f5b0a97"

# Git modes that are not regular blobs within a preserved tree.
NON_REGULAR_MODES = frozenset({"120000", "160000"})
ROOT_ACTIVATION = (".opencode", "opencode.json")


class BundleError(RuntimeError):
    """Raised when the preserved bundle cannot be inspected."""


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str, cwd: Path) -> bytes:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if proc.returncode != 0:
        raise BundleError(
            "git " + " ".join(args) + " failed: "
            + proc.stderr.decode("utf-8", "replace").strip()
        )
    return proc.stdout


def tracked_paths(prefixes: tuple[str, ...], root: Path) -> set[str]:
    """Repo-relative posix paths tracked under ``prefixes``."""
    out = _git("ls-files", "-z", "--", *prefixes, cwd=root)
    return {p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p}


def safe_relpath(value: object) -> bool:
    """True for a relative, in-tree posix path (no absolute, no ``..``)."""
    if not isinstance(value, str) or not value:
        return False
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        return False
    return value == p.as_posix()


def load_manifest(bundle: Path) -> list[dict]:
    manifest = bundle / MANIFEST_NAME
    if not manifest.is_file():
        raise BundleError(f"missing {BUNDLE_DIR}/{MANIFEST_NAME}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"unreadable {MANIFEST_NAME}: {exc}") from exc
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise BundleError(f"{MANIFEST_NAME} has no 'files' list")
    for entry in files:
        if not isinstance(entry, dict):
            raise BundleError(f"{MANIFEST_NAME} entry is not an object: {entry!r}")
    return files


def check_no_root_opencode_activation(root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(tracked_paths(ROOT_ACTIVATION, root)):
        if path == ".opencode" or path.startswith(".opencode/") or path == "opencode.json":
            errors.append(f"tracked root OpenCode activation: {path}")
    return errors


def check_worktree_vs_manifest(root: Path, tracked: set[str] | None = None) -> list[str]:
    """Manifest honesty: hashes, set equality, safety and regular-file checks."""
    bundle = root / BUNDLE_DIR
    if not bundle.is_dir():
        return [f"missing preserved bundle directory: {BUNDLE_DIR}"]
    errors: list[str] = []
    files = load_manifest(bundle)

    manifest_paths: set[str] = set()
    for entry in files:
        rel = entry.get("path")
        if not safe_relpath(rel):
            errors.append(f"unsafe manifest path: {rel!r}")
            continue
        manifest_paths.add(rel)
        target = bundle / rel
        if target.is_symlink():
            errors.append(f"manifest entry is a symlink: {rel}")
            continue
        if not target.is_file():
            errors.append(f"manifest entry missing: {rel}")
            continue
        blob = target.read_bytes()
        if len(blob) != entry.get("bytes"):
            errors.append(f"size mismatch: {rel} ({len(blob)} != {entry.get('bytes')})")
        if hashlib.sha256(blob).hexdigest() != entry.get("sha256"):
            errors.append(f"sha256 mismatch: {rel}")

    if tracked is None:
        tracked = tracked_paths((BUNDLE_DIR,), root)
    prefix = BUNDLE_DIR + "/"
    present = {p[len(prefix):] for p in tracked if p.startswith(prefix)}
    expected = manifest_paths | {MANIFEST_NAME}
    for rel in sorted(expected - present):
        errors.append(f"manifest-listed but not tracked: {rel}")
    for rel in sorted(present - expected):
        errors.append(f"tracked but not in manifest: {rel}")
    return errors


def check_modes(root: Path, commit: str = PRESERVATION_COMMIT) -> list[str]:
    """Reject non-regular entries (symlinks, gitlinks) in the preserved tree."""
    out = _git("ls-tree", "-r", "-z", f"{commit}:{BUNDLE_DIR}", cwd=root)
    errors: list[str] = []
    for record in out.split(b"\x00"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.split()
        mode = fields[0].decode("ascii") if fields else ""
        if mode in NON_REGULAR_MODES:
            errors.append(f"non-regular entry ({mode}) in preserved bundle: {name.decode('utf-8', 'replace')}")
    return errors


def check_immutability(root: Path, commit: str = PRESERVATION_COMMIT) -> list[str]:
    """Immutability: current bytes equal the Git blobs at the pinned commit."""
    bundle = root / BUNDLE_DIR
    if not bundle.is_dir():
        return [f"missing preserved bundle directory: {BUNDLE_DIR}"]
    errors: list[str] = []
    for entry in load_manifest(bundle):
        rel = entry.get("path")
        if not safe_relpath(rel):
            continue
        target = bundle / rel
        if target.is_symlink() or not target.is_file():
            continue  # already reported by check_worktree_vs_manifest
        current = target.read_bytes()
        try:
            pinned = _git("show", f"{commit}:{BUNDLE_DIR}/{rel}", cwd=root)
        except BundleError as exc:
            errors.append(f"cannot read pinned blob for {rel}: {exc}")
            continue
        if current != pinned:
            errors.append(f"bytes differ from preserved commit: {rel}")
    return errors


def check_tag(root: Path, tag: str = PRESERVATION_TAG, commit: str = PRESERVATION_COMMIT) -> list[str]:
    try:
        resolved = _git("rev-parse", f"{tag}^{{commit}}", cwd=root).decode("ascii").strip()
    except BundleError:
        return [f"missing or unresolvable tag: {tag}"]
    if resolved != commit:
        return [f"tag {tag} resolves to {resolved}, expected {commit}"]
    return []


def check_all(root: Path) -> list[str]:
    errors: list[str] = []
    errors += check_no_root_opencode_activation(root)
    errors += check_worktree_vs_manifest(root)
    errors += check_modes(root)
    errors += check_immutability(root)
    errors += check_tag(root)
    return errors


def main(argv: list[str] | None = None) -> int:
    root = repo_root()
    try:
        errors = check_all(root)
    except BundleError as exc:
        print(f"oracle bundle check failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("Preserved oracle bundle is not intact:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1
    files = load_manifest(root / BUNDLE_DIR)
    print(
        f"preserved oracle bundle intact: {len(files)} files match {MANIFEST_NAME} "
        f"and {PRESERVATION_COMMIT[:7]} ({PRESERVATION_TAG})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
