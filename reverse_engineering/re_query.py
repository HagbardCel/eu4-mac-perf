"""Structured reverse-engineering knowledge query over existing extracts.

This is the single read-only entry point used by the agent tools.  It queries
the SQLite corpus and the sealed renderer snapshot; it performs no disassembly,
starts no Ghidra process and writes nothing.

Resolution order for the index:

1. ``EU4_RE_INDEX`` (explicit index file; used by tests and temporary corpora);
2. ``EU4_RE_EXPORT/exports/<revision>/index.sqlite``;
3. the repository-local ``.local/eu4/<sha>/exports/<revision>`` layout.

A missing corpus is ``unavailable`` with an actionable reason, never an empty
success.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

from reverse_engineering import index_db, paths

SCHEMA = "eu4.re_query.v1"


def _zero_rva(value: str | int) -> str:
    if type(value) is int:
        number = value
    else:
        text = str(value).strip()
        number = int(text, 0)
    return f"0x{number:016x}"


def _display_rva(value: str | None) -> str | None:
    if not value:
        return value
    try:
        number = int(value, 0)
    except (TypeError, ValueError):
        return value
    return f"0x{number:x}"


def resolve_index(explicit: Path | None = None) -> tuple[Path, dict]:
    if explicit is not None:
        return Path(explicit), {"source": "explicit"}
    override = os.environ.get("EU4_RE_INDEX")
    if override:
        return Path(override), {"source": "EU4_RE_INDEX"}
    try:
        binary = paths.local_binary_root()
        revision = os.environ.get("EU4_RE_REVISION")
        export = paths.resolve_export_dir(binary, revision)
    except Exception as exc:  # noqa: BLE001
        raise FileNotFoundError(f"RE corpus is not available: {exc}") from exc
    return export / "index.sqlite", {"source": "export", "export": str(export)}


def _provenance(conn: sqlite3.Connection) -> dict:
    try:
        rows = conn.execute("SELECT key, value FROM provenance").fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {row["key"]: row["value"] for row in rows}


def _document(operation: str, *, status: str, value: dict, reason: str = "",
              identity: dict | None = None, truncated: bool = False) -> dict:
    return {
        "schema": SCHEMA,
        "operation": operation,
        "status": status,
        "reason": reason,
        "identity": identity or {},
        "truncated": truncated,
        "result": value,
    }


def _open(index: Path | None) -> tuple[sqlite3.Connection | None, dict, dict, str]:
    try:
        path, identity = resolve_index(index)
    except FileNotFoundError as exc:
        return None, {}, {}, str(exc)
    if not path.is_file():
        return None, {}, identity, f"index is absent: {path}"
    try:
        conn = index_db.connect_readonly(path)
    except (FileNotFoundError, sqlite3.DatabaseError) as exc:
        return None, {}, identity, f"index is unreadable: {exc}"
    provenance = _provenance(conn)
    identity.update({
        "index": str(path),
        "binary_sha256": provenance.get("binary_sha256"),
        "baseline_revision": provenance.get("baseline_revision"),
        "inventory_bundle_digest": provenance.get("inventory_bundle_digest"),
    })
    if provenance.get("binary_sha256") and provenance["binary_sha256"] != paths.binary_sha256():
        return None, provenance, identity, "index provenance does not match the pinned target"
    return conn, provenance, identity, ""


def _function_row(conn: sqlite3.Connection, rva: str) -> dict | None:
    row = conn.execute("SELECT * FROM functions WHERE entry_rva = ?", (rva,)).fetchone()
    return dict(row) if row is not None else None


def query_resolve(name: str, *, index: Path | None = None, limit: int = 10) -> dict:
    conn, provenance, identity, error = _open(index)
    if conn is None:
        return _document("resolve", status="unavailable", value={}, reason=error,
                         identity=identity)
    if not isinstance(name, str) or not name.strip():
        return _document("resolve", status="not_found", value={},
                         reason="a name is required", identity=identity)
    rows = index_db.search_symbol(conn, name)
    results = [
        {
            "entry_rva": _display_rva(row["entry_rva"]),
            "name_mangled": row["name_mangled"],
            "name_demangled": row["name_demangled"],
            "kind": row["kind"],
        }
        for row in rows[:max(1, min(limit, 100))]
    ]
    conn.close()
    return _document("resolve", status="ok" if results else "not_found",
                     value={"matches": results}, identity=identity)


def query_function(target: str, *, index: Path | None = None,
                   max_bytes: int = 24000) -> dict:
    conn, provenance, identity, error = _open(index)
    if conn is None:
        return _document("function", status="unavailable", value={}, reason=error,
                         identity=identity)
    row = None
    rva = None
    try:
        rva = _zero_rva(target)
        row = _function_row(conn, rva)
    except (TypeError, ValueError):
        rows = index_db.search_symbol(conn, str(target))
        if rows:
            row = dict(rows[0])
            rva = row["entry_rva"]
    if row is None:
        conn.close()
        return _document("function", status="not_found", value={}, identity=identity)
    pseudo = conn.execute("SELECT body FROM pseudocode WHERE entry_rva = ?", (rva,)).fetchone()
    body = pseudo["body"] if pseudo is not None else ""
    truncated = len(body) > max_bytes
    result = {
        "entry_rva": _display_rva(rva),
        "name_mangled": row.get("name_mangled"),
        "name_demangled": row.get("name_demangled"),
        "kind": row.get("kind"),
        "instruction_count": row.get("instruction_count"),
        "decoded_instruction_bytes": row.get("decoded_instruction_bytes"),
        "decompile_status": row.get("decompile_status"),
        "pseudocode": body[:max_bytes],
    }
    conn.close()
    return _document("function", status="ok", value=result, identity=identity,
                     truncated=truncated)


def _edge_rows(conn: sqlite3.Connection, rva: str, *, direction: str) -> list[dict]:
    column = "callee_internal_rva" if direction == "callers" else "caller_rva"
    rows = conn.execute(
        f"SELECT * FROM call_edges WHERE {column} = ? ORDER BY callsite_rva LIMIT 500",
        (rva,),
    ).fetchall()
    result = []
    for row in rows:
        other = row["caller_rva"] if direction == "callers" else row["callee_internal_rva"]
        name = None
        if other:
            function = conn.execute(
                "SELECT name_demangled, name_mangled FROM functions WHERE entry_rva = ?",
                (other,),
            ).fetchone()
            if function is not None:
                name = function["name_demangled"] or function["name_mangled"]
        result.append({
            "callsite_rva": _display_rva(row["callsite_rva"]),
            "caller_rva": _display_rva(row["caller_rva"]),
            "callee_rva": _display_rva(row["callee_internal_rva"]),
            "callee_external_symbol": row["callee_external_symbol"],
            "ultimate_external_symbol": row["ultimate_external_symbol"],
            "resolution": row["resolution"],
            "other_rva": _display_rva(other),
            "other_name": name,
        })
    return result


def query_edges(target: str, *, direction: str, index: Path | None = None) -> dict:
    conn, provenance, identity, error = _open(index)
    if conn is None:
        return _document(direction, status="unavailable", value={}, reason=error,
                         identity=identity)
    try:
        rva = _zero_rva(target)
    except (TypeError, ValueError):
        rows = index_db.search_symbol(conn, str(target))
        if not rows:
            conn.close()
            return _document(direction, status="not_found", value={}, identity=identity)
        rva = rows[0]["entry_rva"]
    edges = _edge_rows(conn, rva, direction=direction)
    conn.close()
    return _document(direction, status="ok", value={
        "entry_rva": _display_rva(rva),
        "edges": edges,
        "count": len(edges),
    }, identity=identity)


def query_xrefs(target: str, *, index: Path | None = None) -> dict:
    edges = query_edges(target, direction="callers", index=index)
    if edges["status"] != "ok":
        return edges | {"operation": "xrefs"}
    value = dict(edges["result"])
    value["limitations"] = [
        "offline extracts contain call edges only; data cross-references require Ghidra",
    ]
    result = _document("xrefs", status="partial", value=value,
                       reason="call xrefs only", identity=edges["identity"])
    return result


_SNAPSHOT_ABI: dict[str, dict] | None = None


def _snapshot_prototypes() -> dict[str, dict]:
    global _SNAPSHOT_ABI
    if _SNAPSHOT_ABI is not None:
        return _SNAPSHOT_ABI
    mapping: dict[str, dict] = {}
    try:
        binary = paths.local_binary_root()
        revision = os.environ.get("EU4_RE_REVISION")
        export = paths.resolve_export_dir(binary, revision)
    except Exception:  # noqa: BLE001
        _SNAPSHOT_ABI = mapping
        return mapping
    batches = export / "renderer_snapshot" / "v1" / "batches"
    if not batches.is_dir():
        _SNAPSHOT_ABI = mapping
        return mapping
    for archive in sorted(batches.glob("gfx-batch-*.zip")):
        try:
            with zipfile.ZipFile(archive) as handle:
                if "static.jsonl" not in handle.namelist():
                    continue
                for line in handle.read("static.jsonl").decode("utf-8", "replace").splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rva = row.get("entry_rva")
                    prototype = row.get("prototype")
                    if isinstance(rva, str) and isinstance(prototype, str):
                        mapping.setdefault(_zero_rva(rva), {
                            "prototype": prototype,
                            "package": archive.name,
                            "body_ranges": row.get("body_ranges"),
                        })
        except (OSError, zipfile.BadZipFile):
            continue
    _SNAPSHOT_ABI = mapping
    return mapping


def query_abi(target: str, *, index: Path | None = None) -> dict:
    try:
        rva = _zero_rva(target)
    except (TypeError, ValueError):
        conn, _provenance, identity, _error = _open(index)
        if conn is None:
            return _document("abi", status="unavailable", value={}, identity={})
        rows = index_db.search_symbol(conn, str(target))
        conn.close()
        if not rows:
            return _document("abi", status="not_found", value={})
        rva = rows[0]["entry_rva"]
    prototypes = _snapshot_prototypes()
    if rva not in prototypes:
        return _document("abi", status="unavailable", value={"entry_rva": _display_rva(rva)},
                         reason="ABI is not in the sealed renderer snapshot; "
                                "live Ghidra analysis is required")
    record = prototypes[rva]
    return _document("abi", status="ok", value={
        "entry_rva": _display_rva(rva),
        "prototype": record["prototype"],
        "source": "sealed renderer snapshot static.jsonl",
        "package": record["package"],
        "limitations": ["a Ghidra prototype can be undefined; it is not a qualified ABI contract"],
    })


def query_struct(name: str | None, *, limit: int = 20) -> dict:
    analysis = paths.ROOT / "analysis"
    files = sorted(analysis.glob("**/*layout*.json"))
    matches = []
    for path in files:
        if name and name.lower() not in path.name.lower() and name.lower() not in str(path).lower():
            continue
        try:
            size = path.stat().st_size
            if size > 256 * 1024:
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        matches.append({
            "name": path.stem,
            "path": str(path.relative_to(paths.ROOT)),
            "bytes": size,
            "layout": value,
        })
        if len(matches) >= max(1, min(limit, 100)):
            break
    return _document("struct", status="ok" if matches else "not_found",
                     value={"layouts": matches})


def query_provenance(*, index: Path | None = None) -> dict:
    conn, provenance, identity, error = _open(index)
    if conn is None:
        return _document("provenance", status="unavailable", value={}, reason=error,
                         identity=identity)
    conn.close()
    return _document("provenance", status="ok", value={"provenance": provenance},
                     identity=identity)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("name")
    resolve.add_argument("--limit", type=int, default=10)
    function = sub.add_parser("function")
    function.add_argument("target")
    function.add_argument("--max-bytes", type=int, default=24000)
    callers = sub.add_parser("callers")
    callers.add_argument("target")
    callees = sub.add_parser("callees")
    callees.add_argument("target")
    decompile = sub.add_parser("decompile")
    decompile.add_argument("target")
    decompile.add_argument("--max-bytes", type=int, default=24000)
    xrefs = sub.add_parser("xrefs")
    xrefs.add_argument("target")
    abi = sub.add_parser("abi")
    abi.add_argument("target")
    struct = sub.add_parser("struct")
    struct.add_argument("name", nargs="?", default=None)
    sub.add_parser("provenance")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "resolve":
            value = query_resolve(args.name, index=args.index, limit=args.limit)
        elif args.command == "function":
            value = query_function(args.target, index=args.index, max_bytes=args.max_bytes)
        elif args.command == "callers":
            value = query_edges(args.target, direction="callers", index=args.index)
        elif args.command == "callees":
            value = query_edges(args.target, direction="callees", index=args.index)
        elif args.command == "decompile":
            value = query_function(args.target, index=args.index, max_bytes=args.max_bytes)
            value["operation"] = "decompile"
        elif args.command == "xrefs":
            value = query_xrefs(args.target, index=args.index)
        elif args.command == "abi":
            value = query_abi(args.target, index=args.index)
        elif args.command == "struct":
            value = query_struct(args.name)
        else:
            value = query_provenance(index=args.index)
    except Exception as exc:  # noqa: BLE001 - one JSON envelope for the agent
        value = _document(args.command, status="error", value={},
                          reason=f"{type(exc).__name__}: {exc}")
    print(json.dumps(value, indent=2 if args.pretty else None, sort_keys=False))
    return 0 if value["status"] in ("ok", "partial") else 2


if __name__ == "__main__":
    raise SystemExit(main())
