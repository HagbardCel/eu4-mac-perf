"""Immutable source snapshots for read-only repository audits.

An audit result is a function of three separately identified inputs::

    AuditResult = f(SourceSnapshot, Policy, ToolVersion)

This module owns the *SourceSnapshot* input. It resolves a Git tree-ish (or the
index) to a fixed commit + tree + ordered path/object-id manifest, and reads file
contents from Git objects rather than the working tree. That keeps the audit
reproducible regardless of the checked-out branch or unrelated worktree changes.

``git ls-tree``/``git ls-files`` output is consumed NUL-delimited (``-z``) so
non-ASCII filenames survive unquoted; Git's default quoting would emit octal
escapes and corrupt both paths and extension classification.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Guard against reading huge evidence blobs during textual analysis.
MAX_BLOB_BYTES_DEFAULT = 2 * 1024 * 1024

# Git index modes: regular (100644), executable (100755) and symlink (120000)
# entries all reference a blob; a gitlink/submodule (160000) references a commit
# that need not exist in this repository.
BLOB_MODES = frozenset({"100644", "100755", "120000"})
GITLINK_MODE = "160000"


class SnapshotError(RuntimeError):
    """Raised when Git cannot produce the requested snapshot."""


def _intent_to_add_paths(root: Path) -> set[str]:
    """Index paths that only carry an ``git add -N`` intent-to-add entry.

    Git records intent-to-add entries with the *empty blob*, so they are
    indistinguishable from a genuinely staged empty file by object id alone.
    Porcelain v2 does distinguish them: an intent-to-add entry has an unmodified
    index status (``.A``) and an all-zero index object id, whereas a staged file
    carries its real blob id. Detection must therefore be flag-based, never
    oid-based.
    """
    proc = subprocess.run(
        [
            "git", "-C", str(root), "status", "--porcelain=v2", "-z",
            "--untracked-files=no", "--no-renames",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SnapshotError(
            "git status --porcelain=v2 failed: "
            + proc.stderr.decode("utf-8", "replace").strip()
        )
    paths: set[str] = set()
    for record in proc.stdout.decode("utf-8", "surrogateescape").split("\x00"):
        if not record or record[0] != "1":
            continue
        fields = record.split(" ", 8)
        if len(fields) != 9:
            continue
        xy, index_oid, path = fields[1], fields[7], fields[8]
        if xy == ".A" and index_oid and set(index_oid) == {"0"}:
            paths.add(path)
    return paths


def _batch_check_blobs(root: Path, obj_ids: list[str]) -> dict[str, int]:
    """Resolve blob sizes via ``git cat-file --batch-check``, failing closed.

    Every requested object must exist, be a ``blob`` and report a non-negative
    size. A missing or malformed object raises ``SnapshotError``; callers must
    never silently fall back to a sentinel size for an expected blob.
    """
    unique = list(dict.fromkeys(obj_ids))
    if not unique:
        return {}
    stdin = "".join(oid + "\n" for oid in unique).encode()
    proc = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch-check"],
        input=stdin,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SnapshotError(
            "git cat-file --batch-check failed: "
            + proc.stderr.decode("utf-8", "replace").strip()
        )
    lines = proc.stdout.decode("utf-8", "replace").splitlines()
    if len(lines) != len(unique):
        raise SnapshotError(
            f"expected {len(unique)} object records from git cat-file, got {len(lines)}"
        )
    sizes: dict[str, int] = {}
    for oid, line in zip(unique, lines):
        fields = line.split()
        if len(fields) == 2 and fields[1] == "missing":
            raise SnapshotError(f"index blob {oid} is missing from the object database")
        if len(fields) != 3 or fields[0] != oid or fields[1] != "blob":
            raise SnapshotError(f"unexpected git cat-file response for {oid}: {line!r}")
        try:
            size = int(fields[2])
        except ValueError as exc:
            raise SnapshotError(f"unexpected size for {oid}: {fields[2]!r}") from exc
        if size < 0:
            raise SnapshotError(f"negative size reported for {oid}: {size}")
        sizes[oid] = size
    return sizes


@dataclass(frozen=True)
class Entry:
    path: str
    mode: str
    obj_type: str
    obj_id: str
    size: int  # bytes, or -1 when unknown (index mode / non-blob)


def _git(root: Path, args: list[str], *, binary: bool = False) -> bytes | str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise SnapshotError(
            "git " + " ".join(args) + " failed: " + proc.stderr.decode("utf-8", "replace").strip()
        )
    return proc.stdout if binary else proc.stdout.decode("utf-8", "surrogateescape")


class RepoSnapshot:
    """An immutable, sorted view of a repository at one revision."""

    def __init__(self, root, mode, ref, commit, tree, entries):
        self.root = Path(root).resolve()
        self.mode = mode  # "ref" | "index"
        self.ref = ref  # tree-ish, or None in index mode
        self.commit = commit
        self.tree = tree
        self.entries = tuple(entries)
        self._by_path = {e.path: e for e in self.entries}
        self._ordered = tuple(sorted(self._by_path))

    # -- constructors -----------------------------------------------------
    @classmethod
    def from_ref(cls, root, ref: str = "HEAD") -> "RepoSnapshot":
        root = Path(root).resolve()
        commit = _git(root, ["rev-parse", ref]).strip()
        tree = _git(root, ["rev-parse", f"{ref}^{{tree}}"]).strip()
        raw = _git(root, ["ls-tree", "-r", "-z", "-l", ref], binary=True)
        entries: list[Entry] = []
        for record in raw.split(b"\x00"):
            if not record:
                continue
            meta, _, path_b = record.partition(b"\t")
            fields = meta.split()
            size_s = fields[3].decode() if len(fields) > 3 else "-"
            entries.append(
                Entry(
                    path=path_b.decode("utf-8", "surrogateescape"),
                    mode=fields[0].decode(),
                    obj_type=fields[1].decode(),
                    obj_id=fields[2].decode(),
                    size=int(size_s) if size_s.isdigit() else -1,
                )
            )
        return cls(root, "ref", ref, commit, tree, entries)

    @classmethod
    def from_index(cls, root) -> "RepoSnapshot":
        root = Path(root).resolve()
        intent_to_add = _intent_to_add_paths(root)
        raw = _git(root, ["ls-files", "-s", "-z"], binary=True)
        entries: list[Entry] = []
        for record in raw.split(b"\x00"):
            if not record:
                continue
            meta, _, path_b = record.partition(b"\t")
            fields = meta.split()
            if len(fields) < 3:
                raise SnapshotError(f"malformed index entry: {record!r}")
            mode = fields[0].decode()
            obj_id = fields[1].decode()
            stage = fields[2].decode()
            path = path_b.decode("utf-8", "surrogateescape")
            if stage != "0":
                raise SnapshotError(
                    f"unmerged index entry for {path!r} (stage {stage}); "
                    "resolve the merge before auditing the index"
                )
            if path in intent_to_add:
                raise SnapshotError(
                    f"intent-to-add index entry for {path!r}; "
                    "stage the file content before auditing the index"
                )
            if mode != GITLINK_MODE and mode not in BLOB_MODES:
                raise SnapshotError(f"unsupported index mode {mode!r} for {path!r}")
            # Gitlinks reference a submodule commit that may be absent here; its
            # size is intentionally unknown. Every other mode must resolve to a blob.
            entries.append(
                Entry(
                    path=path,
                    mode=mode,
                    obj_type="commit" if mode == GITLINK_MODE else "blob",
                    obj_id=obj_id,
                    size=-1,
                )
            )
        blob_sizes = _batch_check_blobs(
            root, [e.obj_id for e in entries if e.obj_type == "blob"]
        )
        entries = [
            Entry(
                e.path,
                e.mode,
                e.obj_type,
                e.obj_id,
                blob_sizes[e.obj_id] if e.obj_type == "blob" else -1,
            )
            for e in entries
        ]
        return cls(root, "index", None, "", "", entries)

    # -- accessors --------------------------------------------------------
    def paths(self) -> list[str]:
        return list(self._ordered)

    def entry(self, path: str) -> Entry | None:
        return self._by_path.get(path)

    def has(self, path: str) -> bool:
        return path in self._by_path

    def read_many(self, paths, max_bytes: int | None = MAX_BLOB_BYTES_DEFAULT):
        """Return ``{path: bytes | None}`` read from Git objects via one batch call.

        ``None`` marks a path that is absent, not a blob, or larger than
        ``max_bytes``. Pass ``max_bytes=None`` to read everything requested.
        """
        results: dict[str, bytes | None] = {}
        wanted = []
        for path in paths:
            entry = self._by_path.get(path)
            if entry is None or entry.obj_type != "blob":
                results[path] = None
                continue
            if max_bytes is not None and entry.size >= 0 and entry.size > max_bytes:
                results[path] = None
                continue
            wanted.append(entry)

        if not wanted:
            return results

        stdin = "".join(e.obj_id + "\n" for e in wanted).encode()
        proc = subprocess.run(
            ["git", "-C", str(self.root), "cat-file", "--batch"],
            input=stdin,
            capture_output=True,
        )
        out = proc.stdout
        pos = 0
        for entry in wanted:
            newline = out.find(b"\n", pos)
            if newline < 0:
                results[entry.path] = None
                continue
            header = out[pos:newline].decode("utf-8", "replace")
            pos = newline + 1
            fields = header.split()
            if len(fields) >= 3 and fields[1] != "missing":
                size = int(fields[2])
                results[entry.path] = out[pos : pos + size]
                pos += size
                if out[pos : pos + 1] == b"\n":
                    pos += 1
            else:
                results[entry.path] = None
        return results

    def read_text(self, path: str, max_bytes: int | None = MAX_BLOB_BYTES_DEFAULT):
        data = self.read_many([path], max_bytes=max_bytes).get(path)
        if data is None:
            return None
        return data.decode("utf-8", "surrogateescape")

    # -- identity ---------------------------------------------------------
    def blob_identity_sha256(self) -> str:
        digest = hashlib.sha256()
        for path in self._ordered:
            digest.update(path.encode("utf-8", "surrogateescape"))
            digest.update(b"\x00")
            digest.update(self._by_path[path].obj_id.encode())
            digest.update(b"\n")
        return digest.hexdigest()

    def identity(self) -> dict:
        return {
            "mode": self.mode,
            "ref": self.ref,
            "commit": self.commit,
            "tree": self.tree,
            "path_count": len(self._ordered),
            "blob_identity_sha256": self.blob_identity_sha256(),
        }
