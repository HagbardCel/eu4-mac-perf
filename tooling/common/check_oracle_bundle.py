#!/usr/bin/env python3
"""Fail if the preserved EU IV Oracle v2 blueprint is not intact.

The blueprint was a local, untracked handoff; the preservation commit is its
first Git-backed identity. The *pinned commit* - not the editable manifest - is
the authority for what the bundle contains.

Three independent conditions are enforced:

1. Historical tree identity - the current Git index equals the pinned tree on
   the exact ``(path, mode, object id)`` tuples, so committed or staged
   additions, deletions, content changes and mode changes are all rejected.
2. Worktree integrity - every pinned path (``MANIFEST.json`` included) exists in
   the worktree as a regular file with the pinned bytes and executable status, no
   path or directory component traverses a symlink, and no additional regular
   file, symlink or special entry exists inside the bundle. This catches
   *unstaged* filesystem changes that Git status reports but the index does not.
3. Manifest consistency - ``MANIFEST.json`` lists exactly the pinned payload
   files (excluding itself), with no duplicates and with sizes and SHA-256 values
   derived from the pinned blobs. Its own bytes are covered by condition 2.

A tracked root-level OpenCode activation is also rejected; untracked local
configuration is ignored, since OpenCode only loads a repository-root
``.opencode/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

BUNDLE_DIR = "docs/proposals/EU4_Oracle_v2"
MANIFEST_NAME = "MANIFEST.json"
PRESERVATION_TAG = "oracle-v2-blueprint-20261016"
# Pinned independently of the editable manifest, so the manifest cannot redefine
# the expected historical file set. A branch rebase would change this SHA, which
# is exactly the kind of drift the check is meant to surface.
PRESERVATION_COMMIT = "56207cc3a2bfc28e6bb3719c04125ed42f5b0a97"

# Regular-file modes: value is whether the executable bit is expected.
REGULAR_MODES = {"100644": False, "100755": True}
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


def safe_relpath(value: object) -> bool:
    """True for a relative, in-tree posix path (no absolute, no ``..``)."""
    if not isinstance(value, str) or not value:
        return False
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts:
        return False
    return value == p.as_posix()


# --- inventory ----------------------------------------------------------------


def pinned_entries(root: Path, commit: str = PRESERVATION_COMMIT, bundle_dir: str = BUNDLE_DIR) -> dict[str, tuple[str, str]]:
    """Authoritative ``{bundle-relative path: (mode, object id)}`` at ``commit``."""
    out = _git("ls-tree", "-r", "-z", f"{commit}:{bundle_dir}", cwd=root)
    entries: dict[str, tuple[str, str]] = {}
    for record in out.split(b"\x00"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.split()
        if len(fields) != 3:
            raise BundleError(f"unexpected ls-tree record: {record!r}")
        mode, _type, oid = (f.decode("ascii") for f in fields)
        rel = name.decode("utf-8", "surrogateescape")
        entries[rel] = (mode, oid)
    return entries


def index_entries(root: Path, bundle_dir: str = BUNDLE_DIR) -> dict[str, tuple[str, str]]:
    """Current index ``{bundle-relative path: (mode, object id)}``; stage 0 only."""
    out = _git("ls-files", "-s", "-z", "--", bundle_dir, cwd=root)
    prefix = bundle_dir + "/"
    entries: dict[str, tuple[str, str]] = {}
    for record in out.split(b"\x00"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.split()
        if len(fields) != 3:
            raise BundleError(f"unexpected ls-files record: {record!r}")
        mode, oid, stage = (f.decode("ascii") for f in fields)
        path = name.decode("utf-8", "surrogateescape")
        if stage != "0":
            raise BundleError(f"unmerged index entry for {path!r} (stage {stage})")
        if not path.startswith(prefix):
            raise BundleError(f"index entry outside bundle: {path!r}")
        rel = path[len(prefix):]
        if rel in entries:
            raise BundleError(f"duplicate index entry: {rel}")
        entries[rel] = (mode, oid)
    return entries


# --- condition 1: historical tree identity ------------------------------------


def check_pinned_tree_identity(root: Path, commit: str = PRESERVATION_COMMIT, bundle_dir: str = BUNDLE_DIR) -> list[str]:
    errors: list[str] = []
    pinned = pinned_entries(root, commit, bundle_dir)
    current = index_entries(root, bundle_dir)
    for rel, (mode, _oid) in sorted(pinned.items()):
        if mode not in REGULAR_MODES:
            errors.append(f"non-regular mode ({mode}) in preserved tree: {rel}")
    for rel in sorted(set(pinned) - set(current)):
        errors.append(f"historical file missing from index: {rel}")
    for rel in sorted(set(current) - set(pinned)):
        errors.append(f"tracked file not in preservation commit: {rel}")
    for rel in sorted(set(pinned) & set(current)):
        pinned_mode, pinned_oid = pinned[rel]
        cur_mode, cur_oid = current[rel]
        if cur_mode != pinned_mode:
            errors.append(f"mode changed: {rel} ({pinned_mode} -> {cur_mode})")
        elif cur_oid != pinned_oid:
            errors.append(f"content identity changed: {rel}")
    return errors


# --- condition 2: worktree integrity ------------------------------------------


def _walk(root: Path) -> list[tuple[str, str]]:
    """Physical entries under ``root`` as ``(relpath, kind)``.

    Directory entries are inspected directly (not via ``os.walk``), so a symlinked
    directory is classified as a symlink rather than descended into.
    """
    found: list[tuple[str, str]] = []
    stack: list[tuple[str, Path]] = [("", root)]
    while stack:
        rel_dir, directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise BundleError(f"cannot read {directory}: {exc}") from exc
        for entry in entries:
            rel = f"{rel_dir}/{entry.name}" if rel_dir else entry.name
            if entry.is_symlink():
                found.append((rel, "symlink"))
            elif entry.is_dir(follow_symlinks=False):
                found.append((rel, "dir"))
                stack.append((rel, Path(entry.path)))
            elif entry.is_file(follow_symlinks=False):
                found.append((rel, "file"))
            else:
                found.append((rel, "other"))
    return sorted(found)


def _has_symlink_component(bundle: Path, rel: str) -> bool:
    current = bundle
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def check_worktree_integrity(root: Path, commit: str = PRESERVATION_COMMIT, bundle_dir: str = BUNDLE_DIR) -> list[str]:
    bundle = root / bundle_dir
    if not bundle.is_dir():
        return [f"missing preserved bundle directory: {bundle_dir}"]
    errors: list[str] = []
    if bundle.is_symlink():
        errors.append(f"preserved bundle root is a symlink: {bundle_dir}")

    pinned = pinned_entries(root, commit, bundle_dir)
    for rel, (mode, _oid) in sorted(pinned.items()):
        target = bundle / rel
        if _has_symlink_component(bundle, rel):
            errors.append(f"path traverses a symlink: {rel}")
            continue
        if not target.is_file():
            errors.append(f"missing or non-regular worktree file: {rel}")
            continue
        executable = bool(target.stat().st_mode & 0o111)
        if executable != REGULAR_MODES.get(mode, executable):
            errors.append(f"executable bit mismatch: {rel}")
        if target.read_bytes() != _git("show", f"{commit}:{bundle_dir}/{rel}", cwd=root):
            errors.append(f"worktree bytes differ from preserved commit: {rel}")

    for rel, kind in _walk(bundle):
        if kind == "symlink":
            errors.append(f"symlink inside preserved bundle: {rel}")
        elif kind == "other":
            errors.append(f"special file inside preserved bundle: {rel}")
        elif kind == "file" and rel not in pinned:
            errors.append(f"unexpected file inside preserved bundle: {rel}")
    return errors


# --- condition 3: manifest consistency ----------------------------------------


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


def check_manifest_consistency(root: Path, commit: str = PRESERVATION_COMMIT, bundle_dir: str = BUNDLE_DIR) -> list[str]:
    bundle = root / bundle_dir
    errors: list[str] = []
    try:
        files = load_manifest(bundle)
    except BundleError as exc:
        return [str(exc)]

    manifest_paths: list[str] = []
    for entry in files:
        rel = entry.get("path")
        if not safe_relpath(rel):
            errors.append(f"unsafe manifest path: {rel!r}")
            continue
        manifest_paths.append(rel)
    if len(manifest_paths) != len(set(manifest_paths)):
        errors.append("duplicate path in manifest")

    pinned_payload = set(pinned_entries(root, commit, bundle_dir)) - {MANIFEST_NAME}
    listed = set(manifest_paths)
    for rel in sorted(pinned_payload - listed):
        errors.append(f"manifest missing pinned file: {rel}")
    for rel in sorted(listed - pinned_payload):
        errors.append(f"manifest lists file not in preservation commit: {rel}")

    for entry in files:
        rel = entry.get("path")
        if not safe_relpath(rel) or rel not in pinned_payload:
            continue
        pinned_bytes = _git("show", f"{commit}:{bundle_dir}/{rel}", cwd=root)
        if entry.get("bytes") != len(pinned_bytes):
            errors.append(f"manifest size mismatch: {rel}")
        if entry.get("sha256") != hashlib.sha256(pinned_bytes).hexdigest():
            errors.append(f"manifest sha256 mismatch: {rel}")
    return errors


# --- tag and CI-activation guards ---------------------------------------------


def check_tag(root: Path, tag: str = PRESERVATION_TAG, commit: str = PRESERVATION_COMMIT) -> list[str]:
    try:
        kind = _git("cat-file", "-t", tag, cwd=root).decode("ascii").strip()
    except BundleError:
        return [f"missing or unresolvable tag: {tag}"]
    if kind != "tag":
        return [f"tag {tag} is not annotated (object type {kind})"]
    resolved = _git("rev-parse", f"{tag}^{{commit}}", cwd=root).decode("ascii").strip()
    if resolved != commit:
        return [f"tag {tag} resolves to {resolved}, expected {commit}"]
    return []


def check_no_root_opencode_activation(root: Path) -> list[str]:
    out = _git("ls-files", "-z", "--", *ROOT_ACTIVATION, cwd=root)
    errors: list[str] = []
    for path in (p.decode("utf-8", "surrogateescape") for p in out.split(b"\x00") if p):
        if path == ".opencode" or path.startswith(".opencode/") or path == "opencode.json":
            errors.append(f"tracked root OpenCode activation: {path}")
    return errors


def check_all(root: Path, commit: str = PRESERVATION_COMMIT, tag: str = PRESERVATION_TAG, bundle_dir: str = BUNDLE_DIR) -> list[str]:
    errors: list[str] = []
    errors += check_no_root_opencode_activation(root)
    errors += check_pinned_tree_identity(root, commit, bundle_dir)
    errors += check_worktree_integrity(root, commit, bundle_dir)
    errors += check_manifest_consistency(root, commit, bundle_dir)
    errors += check_tag(root, tag, commit)
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
    pinned = pinned_entries(root)
    payload = len(pinned) - (1 if MANIFEST_NAME in pinned else 0)
    print(
        f"preserved oracle bundle intact: {payload} files match {MANIFEST_NAME} "
        f"and {PRESERVATION_COMMIT[:7]} ({PRESERVATION_TAG})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
