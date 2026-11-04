#!/usr/bin/env python3
"""Read-only research CLI for the sealed renderer corpus and symbol index.

The CLI does not rebuild the index, rewrite sealed packages, launch Ghidra, or
launch EU4. EU4_RE_EXPORT selects the corpus root that contains exports/.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from reverse_engineering import index_db
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import ReError, ghidra_project_meta_path
from reverse_engineering.renderer_extract import (
    assert_frozen_batch_plan,
    batch_zip_path,
    load_frozen_context,
    load_inventory_index,
    load_jsonl_text,
    read_zip_package,
    validate_batch_package,
)
from reverse_engineering.renderer_scope import DEFAULT_SCOPE_DIR

SCHEMA = "eu4.research.v1"
DEFAULT_MAX_BYTES = 24 * 1024
HARD_MAX_BYTES = 256 * 1024
DEFAULT_RESOLVE_LIMIT = 20
_RVA_RE = re.compile(r"0x[0-9a-fA-F]+$")
_PACKAGE_MARKERS = (
    "corrupt zip",
    "expected members",
    "static_sha256",
    "decompiled_sha256",
    "decode error",
    "receipt JSON",
    "jsonl",
    "JSON",
)
_XREF_LIMITS = (
    "unresolved indirect dispatch is not enumerated beyond the recorded unresolved rows",
    "the static call graph does not include every tail call",
)

Loader = Callable[[str | None], tuple["ResearchContext | None", Exception | None]]


@dataclass
class ResearchContext:
    exp: Path
    summary: dict[str, Any]
    plan: dict[str, Any]
    bundle_digest: str
    ordered_rvas: list[str]
    revision: str
    rva_to_batch: dict[str, str]


def assemble_context(
    exp: Path,
    summary: dict[str, Any],
    plan: dict[str, Any],
    bundle_digest: str,
    ordered_rvas: list[str],
    revision: str,
) -> ResearchContext:
    """Accept a context only when its batch plan matches the frozen partition."""
    assert_frozen_batch_plan(plan, summary, bundle_digest, ordered_rvas)
    mapping: dict[str, str] = {}
    for batch_id, rvas in plan["batches"].items():
        for rva in rvas:
            mapping[_normalize_rva(rva)] = batch_id
    return ResearchContext(
        exp=exp,
        summary=summary,
        plan=plan,
        bundle_digest=bundle_digest,
        ordered_rvas=list(ordered_rvas),
        revision=revision,
        rva_to_batch=mapping,
    )


def load_production_context(baseline: str | None) -> tuple[ResearchContext | None, Exception | None]:
    try:
        exp, summary, plan, bundle, ordered, revision = load_frozen_context(DEFAULT_SCOPE_DIR, baseline)
        return assemble_context(exp, summary, plan, bundle, ordered, revision), None
    except ReError as exc:
        return None, exc


def main(argv: list[str] | None = None, *, loader: Loader | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    if args.max_bytes < 1 or args.max_bytes > HARD_MAX_BYTES:
        print(f"max-bytes must be from 1 to {HARD_MAX_BYTES}", file=sys.stderr)
        return 2
    if getattr(args, "static_offset", 0) < 0 or getattr(args, "decompile_offset", 0) < 0:
        print("offsets must be zero or positive", file=sys.stderr)
        return 2
    if getattr(args, "limit", None) is not None and args.limit < 1:
        print("limit must be a positive integer", file=sys.stderr)
        return 2
    load = loader or load_production_context
    try:
        context, err = load(args.baseline_revision)
    except ReError as exc:
        context, err = None, exc
    if err is not None or context is None:
        _emit(_failure(args.command, err or ReError("missing research context")))
        return 0
    if args.command == "status":
        document = cmd_status(context)
    elif args.command == "resolve":
        document = cmd_resolve(context, args.query, args.limit, args.max_bytes)
    elif args.command == "function":
        document = cmd_function(
            context,
            args.rva,
            view=args.view,
            static_offset=args.static_offset,
            decompile_offset=args.decompile_offset,
            limit=args.limit,
            budget=args.max_bytes,
        )
    else:
        document = cmd_xrefs(context, args.rva, args.max_bytes)
    _emit(document)
    return 0


def cmd_status(ctx: ResearchContext) -> dict[str, Any]:
    index_state, _conn = _open_index(ctx)
    ghidra = ghidra_project_meta_path(ctx.exp.parent.parent)
    evidence = {
        "pinned_sha256": ctx.summary.get("binary_sha256"),
        "baseline_revision": ctx.revision,
        "function_count": ctx.plan.get("selected_function_count"),
        "index": index_state,
        "snapshot": "valid",
        "ghidra_project_metadata": "present" if ghidra.is_file() else "unavailable",
        "manifest_provenance": "pass",
    }
    if index_state == "identity_error":
        status, reason = "identity_error", "identity"
    elif index_state == "unavailable":
        status, reason = "unavailable", "missing_index"
    else:
        status, reason = "ok", None
    return _document(
        "status",
        status,
        provenance=_provenance(ctx, index_state),
        evidence=evidence,
        reason=reason,
    )


def cmd_resolve(ctx: ResearchContext, query: str, limit: int, budget: int) -> dict[str, Any]:
    state, conn = _open_index(ctx)
    if state != "present" or conn is None:
        return _index_blocked("resolve", ctx, state)
    try:
        if _RVA_RE.match(query.strip()):
            try:
                rva = _normalize_rva(query)
            except (ValueError, ReError) as exc:
                return _document(
                    "resolve",
                    "identity_error",
                    provenance=_provenance(ctx, state),
                    reason="identity",
                    detail=str(exc),
                )
            row = index_db.function_row(conn, rva)
            matches = [_match_record(row, "exact", ctx)] if row is not None else []
            kind = "exact"
            truncated = False
        else:
            matches, kind, truncated = _name_matches(conn, query, limit, ctx)
    finally:
        conn.close()
    if not matches:
        return _document(
            "resolve",
            "not_found",
            identity={"query": query},
            provenance=_provenance(ctx, "present"),
            evidence={"matches": [], "match": kind},
        )
    status = "ambiguous" if len(matches) > 1 or truncated else "ok"
    identity: dict[str, Any] = {"query": query}
    if status == "ok":
        identity = dict(matches[0])
    document = _document(
        "resolve",
        status,
        identity=identity,
        provenance=_provenance(ctx, "present"),
        evidence={"matches": matches, "match": kind},
        truncated=truncated,
        reason="truncated" if truncated else None,
    )
    return _shrink_matches(document, budget)


def cmd_function(
    ctx: ResearchContext,
    raw_rva: str,
    *,
    view: str,
    static_offset: int,
    decompile_offset: int,
    limit: int | None,
    budget: int,
) -> dict[str, Any]:
    if not _RVA_RE.match(raw_rva.strip()):
        return _document(
            "function",
            "identity_error",
            identity={"query": raw_rva},
            provenance=_provenance(ctx, "unchecked"),
            reason="identity",
            detail="function expects an RVA",
        )
    try:
        rva = _normalize_rva(raw_rva)
    except (ValueError, ReError) as exc:
        return _document(
            "function",
            "identity_error",
            provenance=_provenance(ctx, "unchecked"),
            reason="identity",
            detail=str(exc),
        )
    state, conn = _open_index(ctx)
    if state != "present" or conn is None:
        return _index_blocked("function", ctx, state)
    try:
        found = index_db.function_row(conn, rva)
        row = dict(found) if found is not None else None
    finally:
        conn.close()
    in_snapshot = rva in ctx.rva_to_batch
    if not in_snapshot:
        status = "not_in_snapshot" if row is not None else "not_found"
        identity = _match_record(row, "exact", ctx) if row is not None else {"entry_rva": rva}
        return _document(
            "function",
            status,
            identity=identity,
            provenance=_provenance(ctx, "present"),
        )
    try:
        static_row, decomp_row, receipt, batch_id = _read_function(ctx, rva)
    except ReError as exc:
        return _failure("function", exc, provenance=_provenance(ctx, "present"), identity={"entry_rva": rva})
    except FileNotFoundError as exc:
        return _document(
            "function",
            "unavailable",
            identity={"entry_rva": rva},
            provenance=_provenance(ctx, "present"),
            reason="missing_source",
            detail=exc.filename or "missing source",
        )
    identity = _match_record(row, "exact", ctx) if row is not None else {"entry_rva": rva, "in_snapshot": True}
    return _page_function(
        identity=identity,
        provenance=_provenance(ctx, "present"),
        static_row=static_row,
        decomp_row=decomp_row,
        receipt=_public_receipt(receipt),
        batch_id=batch_id,
        view=view,
        static_offset=static_offset,
        decompile_offset=decompile_offset,
        limit=limit,
        budget=budget,
    )


def cmd_xrefs(ctx: ResearchContext, raw_rva: str, budget: int) -> dict[str, Any]:
    if not _RVA_RE.match(raw_rva.strip()):
        return _document(
            "xrefs",
            "identity_error",
            identity={"query": raw_rva},
            provenance=_provenance(ctx, "unchecked"),
            reason="identity",
            detail="xrefs expects an RVA",
        )
    try:
        rva = _normalize_rva(raw_rva)
    except (ValueError, ReError) as exc:
        return _document("xrefs", "identity_error", provenance=_provenance(ctx, "unchecked"), reason="identity", detail=str(exc))
    state, conn = _open_index(ctx)
    if state != "present" or conn is None:
        return _index_blocked("xrefs", ctx, state)
    try:
        found = index_db.function_row(conn, rva)
        row = dict(found) if found is not None else None
        incoming = [_edge(item) for item in index_db.callers(conn, rva)]
        outgoing = [_edge(item) for item in index_db.callees(conn, rva)]
    finally:
        conn.close()
    census_path = ctx.exp / "audits" / "callsites-v1.jsonl"
    if not census_path.is_file():
        return _document(
            "xrefs",
            "unavailable",
            identity={"entry_rva": rva},
            provenance=_provenance(ctx, "present"),
            reason="missing_callsites",
            detail="callsites-v1.jsonl",
        )
    if row is None and rva not in ctx.rva_to_batch and not incoming and not outgoing:
        return _document(
            "xrefs",
            "not_found",
            identity={"entry_rva": rva},
            provenance=_provenance(ctx, "present"),
        )
    try:
        callsites = _stream_callsites(census_path, {rva}).get(rva, [])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _document(
            "xrefs",
            "identity_error",
            identity={"entry_rva": rva},
            provenance=_provenance(ctx, "present"),
            reason="package",
            detail=str(exc),
        )
    document = _document(
        "xrefs",
        "ok",
        identity=_match_record(row, "exact", ctx) if row is not None else {"entry_rva": rva, "in_snapshot": rva in ctx.rva_to_batch},
        provenance=_provenance(ctx, "present"),
        evidence={
            "incoming": incoming,
            "outgoing": outgoing,
            "callsites": [_callsite(item) for item in callsites],
            "limits": list(_XREF_LIMITS),
        },
    )
    return _trim_lists(document, budget, ("callsites", "outgoing", "incoming"))


def _read_function(ctx: ResearchContext, rva: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    batch_id = ctx.rva_to_batch[rva]
    path = batch_zip_path(ctx.exp, batch_id)
    if not path.is_file():
        raise FileNotFoundError("missing package")
    census_path = ctx.exp / "audits" / "callsites-v1.jsonl"
    inventory_path = ctx.exp / "inventory" / "functions.jsonl"
    if not census_path.is_file():
        raise FileNotFoundError("missing callsites")
    if not inventory_path.is_file():
        raise FileNotFoundError("missing inventory")
    static_text, decomp_text, receipt = read_zip_package(path)
    batch_rvas = [_normalize_rva(item) for item in ctx.plan["batches"][batch_id]]
    callsites = _stream_callsites(census_path, set(batch_rvas))
    inventory = load_inventory_index(ctx.exp)
    validate_batch_package(
        batch_id=batch_id,
        static_text=static_text,
        decomp_text=decomp_text,
        receipt=receipt,
        rvas=batch_rvas,
        inventory=inventory,
        callsites_by_caller=callsites,
        plan=ctx.plan,
        summary=ctx.summary,
    )
    static_rows = {_normalize_rva(row["entry_rva"]): row for row in load_jsonl_text(static_text)}
    decomp_rows = {_normalize_rva(row["entry_rva"]): row for row in load_jsonl_text(decomp_text)}
    if rva not in static_rows or rva not in decomp_rows:
        raise ReError(f"function {rva} missing from package {batch_id}")
    return static_rows[rva], decomp_rows[rva], receipt, batch_id


def _page_function(
    *,
    identity: dict[str, Any],
    provenance: dict[str, Any],
    static_row: dict[str, Any],
    decomp_row: dict[str, Any],
    receipt: dict[str, Any],
    batch_id: str,
    view: str,
    static_offset: int,
    decompile_offset: int,
    limit: int | None,
    budget: int,
) -> dict[str, Any]:
    instructions = list(static_row.get("instructions") or [])
    pseudocode = str(decomp_row.get("pseudocode") or "")
    total_ins = len(instructions)
    total_chars = len(pseudocode)
    ins_start = min(static_offset, total_ins)
    text_start = min(decompile_offset, total_chars)
    ins_budget = 0 if view == "decompiled" else total_ins - ins_start
    if limit is not None and view != "decompiled":
        ins_budget = min(ins_budget, limit)
    text_budget = 0 if view == "static" else total_chars - text_start

    def build(n_ins: int, n_chars: int) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "batch_id": batch_id,
            "static_status": static_row.get("status"),
            "decompile_status": decomp_row.get("status"),
            "receipt": receipt,
        }
        if static_row.get("error"):
            evidence["static_error"] = static_row.get("error")
        if decomp_row.get("error"):
            evidence["decompile_error"] = decomp_row.get("error")
        next_static = None
        next_decompile = None
        if view != "decompiled":
            evidence["instructions"] = instructions[ins_start : ins_start + n_ins]
            evidence["instruction_total"] = total_ins
            if ins_start + n_ins < total_ins:
                next_static = ins_start + n_ins
        if view != "static":
            evidence["pseudocode"] = pseudocode[text_start : text_start + n_chars]
            evidence["pseudocode_total"] = total_chars
            if text_start + n_chars < total_chars:
                next_decompile = text_start + n_chars
        truncated = next_static is not None or next_decompile is not None
        return _document(
            "function",
            "ok",
            identity=identity,
            provenance=provenance,
            evidence=evidence,
            truncated=truncated,
            next_static_offset=next_static,
            next_decompile_offset=next_decompile,
        )

    return _fit(build, ins_budget, text_budget, budget)


def _fit(
    build: Callable[[int, int], dict[str, Any]],
    n_ins: int,
    n_chars: int,
    budget: int,
) -> dict[str, Any]:
    if _nbytes(build(n_ins, n_chars)) <= budget:
        return build(n_ins, n_chars)
    chars = n_chars
    ins = n_ins
    if chars:
        low, high, best = 0, chars, 0
        while low <= high:
            mid = (low + high) // 2
            if _nbytes(build(ins, mid)) <= budget:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        chars = best
    if _nbytes(build(ins, chars)) > budget:
        chars = 0
        low, high, best = 0, ins, 0
        while low <= high:
            mid = (low + high) // 2
            if _nbytes(build(mid, 0)) <= budget:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        ins = best
    return build(ins, chars)


def _name_matches(
    conn: sqlite3.Connection,
    query: str,
    limit: int,
    ctx: ResearchContext,
) -> tuple[list[dict[str, Any]], str, bool]:
    exact = list(
        conn.execute(
            """
            SELECT entry_rva, name_mangled, name_demangled, kind
            FROM functions
            WHERE name_mangled = ? OR name_demangled = ?
            ORDER BY entry_rva
            LIMIT ?
            """,
            (query, query, limit + 1),
        )
    )
    if exact:
        truncated = len(exact) > limit
        return [_match_record(row, "exact", ctx) for row in exact[:limit]], "exact", truncated
    pattern = _literal_like(query)
    partial = list(
        conn.execute(
            """
            SELECT entry_rva, name_mangled, name_demangled, kind
            FROM functions
            WHERE (name_mangled LIKE ? ESCAPE '\\' OR name_demangled LIKE ? ESCAPE '\\')
            ORDER BY entry_rva
            LIMIT ?
            """,
            (pattern, pattern, limit + 1),
        )
    )
    truncated = len(partial) > limit
    return [_match_record(row, "partial", ctx) for row in partial[:limit]], "partial", truncated


def _open_index(ctx: ResearchContext) -> tuple[str, sqlite3.Connection | None]:
    path = ctx.exp / "index.sqlite"
    if not path.is_file():
        return "unavailable", None
    try:
        conn = index_db.connect_readonly(path)
    except (sqlite3.DatabaseError, FileNotFoundError):
        return "identity_error", None
    try:
        rows = conn.execute("SELECT key, value FROM provenance")
        stored = {row["key"]: row["value"] for row in rows}
    except sqlite3.DatabaseError:
        conn.close()
        return "identity_error", None
    expected = {
        "binary_sha256": ctx.summary.get("binary_sha256"),
        "baseline_revision": ctx.revision,
        "inventory_bundle_digest": ctx.bundle_digest,
    }
    if any(not value or stored.get(key) != value for key, value in expected.items()):
        conn.close()
        return "identity_error", None
    return "present", conn


def _stream_callsites(path: Path, callers: set[str]) -> dict[str, list[dict[str, Any]]]:
    """Keep rows for the requested callers. Do not build the full census."""
    grouped: dict[str, list[dict[str, Any]]] = {caller: [] for caller in callers}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or not any(caller in line for caller in callers):
                continue
            row = json.loads(line)
            caller = row.get("caller_rva")
            if caller in grouped:
                grouped[caller].append(row)
    return grouped


def _index_blocked(operation: str, ctx: ResearchContext, state: str) -> dict[str, Any]:
    if state == "identity_error":
        return _document(
            operation,
            "identity_error",
            provenance=_provenance(ctx, state),
            reason="identity",
            detail="stale index provenance",
        )
    return _document(
        operation,
        "unavailable",
        provenance=_provenance(ctx, state),
        reason="missing_index",
        detail="index.sqlite",
    )


def _failure(
    operation: str,
    exc: Exception,
    *,
    provenance: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(exc)
    lowered = text.lower()
    if isinstance(exc, FileNotFoundError) or "missing" in lowered or "no exports" in lowered or "no export revisions" in lowered:
        status, reason = "unavailable", "missing_source"
    elif any(marker in text for marker in _PACKAGE_MARKERS):
        status, reason = "identity_error", "package"
    else:
        status, reason = "identity_error", "identity"
    return _document(operation, status, identity=identity, provenance=provenance, reason=reason, detail=text)


def _match_record(row: sqlite3.Row, match: str, ctx: ResearchContext) -> dict[str, Any]:
    rva = _normalize_rva(row["entry_rva"])
    return {
        "entry_rva": rva,
        "name_mangled": row["name_mangled"],
        "name_demangled": row["name_demangled"],
        "kind": row["kind"],
        "match": match,
        "in_snapshot": rva in ctx.rva_to_batch,
    }


def _edge(row: sqlite3.Row) -> dict[str, Any]:
    resolution = row["resolution"]
    callee = row["callee_internal_rva"]
    external = row["callee_external_symbol"]
    unresolved = resolution in ("unresolved_indirect", "unresolved_other") or (not callee and not external)
    return {
        "callsite_rva": row["callsite_rva"],
        "caller_rva": row["caller_rva"],
        "callee_rva": _normalize_rva(callee) if callee else None,
        "external_symbol": external,
        "resolution": resolution,
        "unresolved": unresolved,
    }


def _callsite(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "callsite_rva": row.get("callsite_rva"),
        "caller_rva": row.get("caller_rva"),
        "instruction_class": row.get("instruction_class"),
        "flow_instruction_class": row.get("flow_instruction_class"),
        "classifier_status": row.get("classifier_status"),
        "pcode_kind": row.get("pcode_kind"),
        "unresolved": row.get("instruction_class") == "indirect" or row.get("flow_instruction_class") == "indirect",
    }


def _public_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema",
        "batch_id",
        "baseline_revision",
        "selection_manifest_sha256",
        "inventory_bundle_digest",
        "batch_rva_digest",
        "batch_size",
        "static_sha256",
        "decompiled_sha256",
        "binary_sha256",
        "ghidra_version",
        "program_name",
        "image_base",
        "executable_sha256",
    )
    return {key: receipt.get(key) for key in keys if key in receipt}


def _provenance(ctx: ResearchContext, index_state: str) -> dict[str, Any]:
    return {
        "binary_sha256": ctx.summary.get("binary_sha256"),
        "baseline_revision": ctx.revision,
        "inventory_bundle_digest": ctx.bundle_digest,
        "sources": {
            "index": index_state,
            "snapshot": "renderer_snapshot/v1",
        },
    }


def _document(
    operation: str,
    status: str,
    *,
    identity: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    evidence: Any = None,
    truncated: bool = False,
    reason: str | None = None,
    detail: str | None = None,
    next_static_offset: int | None = None,
    next_decompile_offset: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "operation": operation,
        "status": status,
        "identity": identity,
        "provenance": provenance or {},
        "evidence": evidence,
        "truncated": truncated,
    }
    if reason:
        body["reason"] = reason
    if detail:
        body["detail"] = _redact(detail)
    if next_static_offset is not None:
        body["next_static_offset"] = next_static_offset
    if next_decompile_offset is not None:
        body["next_decompile_offset"] = next_decompile_offset
    return body


def _shrink_matches(document: dict[str, Any], budget: int) -> dict[str, Any]:
    matches = document["evidence"]["matches"]
    while matches and _nbytes(document) > budget:
        matches.pop()
        document["truncated"] = True
        document["reason"] = "truncated"
        if len(matches) != 1:
            document["status"] = "ambiguous"
            document["identity"] = {"query": document["identity"].get("query")}
    return document


def _trim_lists(document: dict[str, Any], budget: int, keys: tuple[str, ...]) -> dict[str, Any]:
    evidence = document["evidence"]
    while _nbytes(document) > budget and any(evidence[key] for key in keys):
        for key in keys:
            if evidence[key]:
                evidence[key].pop()
                document["truncated"] = True
                break
    return document


def _nbytes(document: dict[str, Any]) -> int:
    return len(_serialize(document).encode()) + 1


def _serialize(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _emit(document: dict[str, Any]) -> None:
    sys.stdout.write(_serialize(document) + "\n")


def _literal_like(needle: str) -> str:
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _normalize_rva(value: str) -> str:
    return format_rva(parse_rva(value))


def _redact(text: str) -> str:
    return re.sub(r"(?:/[^\s'\"]+)+", lambda match: Path(match.group(0)).name, text)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--baseline-revision", default=None)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reverse_engineering.research")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status")
    _add_common(status)
    resolve = sub.add_parser("resolve")
    _add_common(resolve)
    resolve.add_argument("query")
    resolve.add_argument("--limit", type=int, default=DEFAULT_RESOLVE_LIMIT)
    function = sub.add_parser("function")
    _add_common(function)
    function.add_argument("rva")
    function.add_argument("--view", choices=("static", "decompiled", "all"), default="all")
    function.add_argument("--static-offset", type=int, default=0)
    function.add_argument("--decompile-offset", type=int, default=0)
    function.add_argument("--limit", type=int, default=None)
    xrefs = sub.add_parser("xrefs")
    _add_common(xrefs)
    xrefs.add_argument("rva")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
