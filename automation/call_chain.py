"""Supported Simple construction-path call-chain closure helpers (P5.2b)."""

from __future__ import annotations

import json
from pathlib import Path

# Direct construction path from semantic dossiers / frame model.
SIMPLE_CONSTRUCTION = (
    "_GfxBuildAndAddVertexShader",
    "_GfxBuildAndAddPixelShader",
    "BuildGLSL",
    "_GfxAddVertexShader",
    "_GfxAddPixelShader",
)

# OpenGL / GL-adjacent symbols that must not remain reachable on a completed
# Metal Simple construction path (direct edges). Indirect unresolved sites are
# reported separately — never assumed covered.
GL_BLOCKERS = {
    "glGetString",
    "glCompileShader",
    "glLinkProgram",
    "glCreateShader",
    "glCreateProgram",
    "glShaderSource",
    "glUseProgram",
    "CGL",
    "SDL_GL_",
}


def load_frame_edges(frame_model: Path) -> dict[str, set[str]]:
    data = json.loads(frame_model.read_text(encoding="utf-8"))
    nodes = (data.get("graph") or {}).get("nodes") or {}
    edges: dict[str, set[str]] = {}
    for name, node in nodes.items():
        if not isinstance(node, dict):
            continue
        targets = set()
        for edge in node.get("edges") or []:
            if isinstance(edge, dict) and isinstance(edge.get("target"), str):
                targets.add(edge["target"])
        edges[name] = targets
    return edges


def demangle_hint(symbol: str) -> str:
    if symbol.startswith("__Z"):
        return symbol
    return symbol


def reachable_gl_symbols(edges: dict[str, set[str]], roots: list[str]) -> list[str]:
    """BFS on direct call edges; report targets that look GL-related."""
    found: list[str] = []
    seen: set[str] = set()
    queue = list(roots)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for target in edges.get(name, ()):
            lower = target.lower()
            if any(token.lower() in lower for token in GL_BLOCKERS):
                found.append(target)
            if target not in seen:
                queue.append(target)
    return sorted(set(found))


def assess_construction_closure(frame_model: Path) -> dict:
    edges = load_frame_edges(frame_model)
    # Prefer mangled BuildGLSL / BuildAndAdd names when present.
    roots = []
    for name in edges:
        if "BuildAndAddVertexShader" in name or "BuildAndAddPixelShader" in name:
            roots.append(name)
        if name.endswith("BuildGLSL") or "BuildGLSLP19" in name:
            roots.append(name)
    gl = reachable_gl_symbols(edges, roots)
    return {
        "schema": "eu4-call-chain-closure/v0",
        "roots": roots,
        "reachable_gl_symbols": gl,
        "engine_path_complete": len(gl) == 0 and bool(roots),
        "note": (
            "Direct-edge closure only. Indirect call sites remain unresolved "
            "unless separately proven. Offline ABI tests do not prove a live "
            "session reaches the replacement."
        ),
    }
