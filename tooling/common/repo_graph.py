#!/usr/bin/env python3
"""Conservative *reference-discovery* graph over a frozen source snapshot.

This is deliberately not a complete dependency oracle. It records individual,
confidence-qualified edges so a lifecycle classification can be investigated:

* ``import``       — Python ``import``/``from`` resolved to a tracked module (AST)
* ``include``      — C/C++/ObjC quoted ``#include`` resolved to a tracked header
* ``ci_ref``       — a tracked path referenced by a workflow/hook (operational use)
* ``doc_link``     — a Markdown link to a tracked document (documentation only)
* ``manifest_ref`` — a path mentioned inside a manifest/receipt/``.sha256`` file

Static discovery cannot see dynamic imports, ``sys.path`` mutation, reflection,
``subprocess`` invocation, C macro includes, or generated artifacts. Unresolved
and unparsed files stay visible in ``analysis`` rather than disappearing.
"""
from __future__ import annotations

import ast
import posixpath
import re
from dataclasses import dataclass

from .repo_inventory import TEXT_EXT, ext_of
from .repo_snapshot import RepoSnapshot

MAX_PARSE_BYTES = 2 * 1024 * 1024
MIN_REF_LEN = 4

# Files treated as dependency *consumers*: a positive reference from one of
# these relationships is evidence a path is still used. Doc links and manifest
# mentions are recorded but are not consumer evidence.
CONSUMER_RELS = frozenset({"import", "include", "ci_ref"})

METADATA_BASENAMES = frozenset(
    {"target.json", "manifest.json", "freeze_manifest.json", "batch_plan.json", "index.json"}
)

PY_EXT = frozenset({"py"})
C_EXT = frozenset({"c", "h", "cc", "cpp", "hpp", "m", "mm", "S", "s"})
MD_EXT = frozenset({"md"})

INCLUDE_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.MULTILINE)
LINK_RE = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    relationship: str
    confidence: str
    location: str


class Graph:
    def __init__(self, edges, analysis):
        deduped = {(e.source, e.target, e.relationship, e.confidence, e.location) for e in edges}
        self.edges = tuple(
            sorted((Edge(*key) for key in deduped), key=lambda e: (e.source, e.relationship, e.target, e.location))
        )
        self.analysis = dict(analysis)  # path -> (status, reason)
        self.entrypoints: frozenset[str] = frozenset()

    def consumer_targets(self) -> set[str]:
        return {e.target for e in self.edges if e.relationship in CONSUMER_RELS}

    def summary_rows(self, paths) -> dict[str, dict]:
        rows = {
            p: {
                "entrypoint": False,
                "py_imports_internal": 0,
                "py_imported_by": 0,
                "py_imported_by_tests": 0,
                "c_includes_internal": 0,
                "ci_refs": 0,
                "doc_refs": 0,
                "manifest_refs": 0,
                "consumer_refs": 0,
            }
            for p in paths
        }
        for p in paths:
            if p in self.entrypoints:
                src = rows.get(p)
                if src is not None:
                    src["entrypoint"] = True
        for e in self.edges:
            src = rows.get(e.source)
            if src is not None and e.relationship == "import":
                src["py_imports_internal"] += 1
            tgt = rows.get(e.target)
            if tgt is None:
                continue
            if e.relationship == "import":
                tgt["py_imported_by"] += 1
                if e.source.startswith("tests/"):
                    tgt["py_imported_by_tests"] += 1
                tgt["consumer_refs"] += 1
            elif e.relationship == "include":
                tgt["c_includes_internal"] += 1
                tgt["consumer_refs"] += 1
            elif e.relationship == "ci_ref":
                tgt["ci_refs"] += 1
                tgt["consumer_refs"] += 1
            elif e.relationship == "doc_link":
                tgt["doc_refs"] += 1
            elif e.relationship == "manifest_ref":
                tgt["manifest_refs"] += 1
        return rows


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _is_parseable(path: str) -> bool:
    if path.startswith(".githooks/") or path.rsplit("/", 1)[-1] == "Makefile":
        return True
    name = path.rsplit("/", 1)[-1]
    return name in METADATA_BASENAMES or ext_of(path) == "sha256" or ext_of(path) in TEXT_EXT


class _PyResolver:
    def __init__(self, py_paths: list[str]):
        self.py_paths = py_paths

    def files_for_module(self, modslash: str) -> list[str]:
        if not modslash:
            return []
        init_rel = posixpath.join(modslash, "__init__.py")
        cands = set()
        for p in self.py_paths:
            if p == modslash + ".py" or p.endswith("/" + modslash + ".py"):
                cands.add(p)
            elif p == init_rel or p.endswith("/" + init_rel):
                cands.add(p)
        return sorted(cands)


def _py_edges(resolver: _PyResolver, source: str, text: str, analysis: dict):
    edges = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        analysis[source] = ("unparseable", f"python syntax error: {exc.msg}")
        return edges

    def emit(modslash: str, line: int):
        cands = resolver.files_for_module(modslash)
        if not cands:
            return
        confidence = "high" if len(cands) == 1 else "low"
        for cand in cands:
            if cand != source:
                edges.append(Edge(source, cand, "import", confidence, f"{source}:{line}"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                emit(alias.name.replace(".", "/"), node.lineno)
        elif isinstance(node, ast.ImportFrom):
            level = node.level or 0
            base = posixpath.dirname(source)
            for _ in range(max(level - 1, 0)):
                base = posixpath.dirname(base)
            if node.module:
                emit(node.module.replace(".", "/"), node.lineno)
                for alias in node.names:
                    if alias.name != "*":
                        emit(node.module.replace(".", "/") + "/" + alias.name, node.lineno)
            else:
                for alias in node.names:
                    if alias.name != "*":
                        modslash = alias.name.replace(".", "/")
                        if level > 0:
                            modslash = posixpath.normpath(posixpath.join(base, modslash)) if base else modslash
                        emit(modslash, node.lineno)
    return edges


def _include_edges(source: str, text: str, known: set[str]):
    edges = []
    for match in INCLUDE_RE.finditer(text):
        inc = match.group(1)
        candidates = set()
        for base in (posixpath.dirname(source), ""):
            candidate = posixpath.normpath(posixpath.join(base, inc)) if base else posixpath.normpath(inc)
            if candidate in known:
                candidates.add(candidate)
        line = _line_of(text, match.start())
        for candidate in candidates:
            edges.append(Edge(source, candidate, "include", "high", f"{source}:{line}"))
    return edges


def _doc_edges(source: str, text: str, known: set[str]):
    edges = []
    base = posixpath.dirname(source)
    for match in LINK_RE.finditer(text):
        raw = match.group(1)
        if "://" in raw or raw.startswith("#") or raw.startswith("mailto:"):
            continue
        target = raw.split("#", 1)[0]
        if not target:
            continue
        candidate = posixpath.normpath(posixpath.join(base, target)) if base else posixpath.normpath(target)
        if candidate in known:
            line = _line_of(text, match.start())
            edges.append(Edge(source, candidate, "doc_link", "high", f"{source}:{line}"))
    return edges


def _mention_edges(source: str, text: str, ref_regex, relationship: str, confidence: str):
    edges = []
    for match in ref_regex.finditer(text):
        line = _line_of(text, match.start())
        edges.append(Edge(source, match.group(0), relationship, confidence, f"{source}:{line}"))
    return edges


def build(snapshot: RepoSnapshot) -> Graph:
    paths = snapshot.paths()
    known = set(paths)
    py_paths = [p for p in paths if ext_of(p) in PY_EXT]

    analysis: dict[str, tuple[str, str]] = {}
    to_read: list[str] = []
    for path in paths:
        if not _is_parseable(path):
            analysis[path] = ("skipped_not_text", "extension is not parsed as text")
            continue
        entry = snapshot.entry(path)
        if entry is not None and entry.size >= 0 and entry.size > MAX_PARSE_BYTES:
            analysis[path] = ("skipped_oversized", f"{entry.size} bytes exceeds limit {MAX_PARSE_BYTES}")
            continue
        to_read.append(path)

    blobs = snapshot.read_many(to_read, max_bytes=None)
    texts: dict[str, str] = {}
    for path in to_read:
        data = blobs.get(path)
        if data is None:
            analysis[path] = ("unreadable", "blob not available in snapshot")
            continue
        texts[path] = data.decode("utf-8", "surrogateescape")
        analysis[path] = ("analyzed", "")

    ref_regex = re.compile("|".join(re.escape(p) for p in sorted(known, key=len, reverse=True) if len(p) >= MIN_REF_LEN))

    edges: list[Edge] = []
    resolver = _PyResolver(py_paths)
    for path, text in texts.items():
        ext = ext_of(path)
        name = path.rsplit("/", 1)[-1]
        if ext in PY_EXT:
            edges.extend(_py_edges(resolver, path, text, analysis))
        if ext in C_EXT:
            edges.extend(_include_edges(path, text, known))
        if ext in MD_EXT:
            edges.extend(_doc_edges(path, text, known))
        if path.startswith(".github/") or path.startswith(".githooks/"):
            if ext in {"yml", "yaml", "sh"} or name == "pre-commit":
                edges.extend(_mention_edges(path, text, ref_regex, "ci_ref", "high"))
        if name in METADATA_BASENAMES or ext == "sha256":
            edges.extend(_mention_edges(path, text, ref_regex, "manifest_ref", "medium"))

    graph = Graph(edges, analysis)
    # Entrypoint detection is stored on the graph for summary rendering.
    graph.entrypoints = frozenset(
        p for p, text in texts.items() if ext_of(p) in PY_EXT and "__main__" in text
    )
    return graph
