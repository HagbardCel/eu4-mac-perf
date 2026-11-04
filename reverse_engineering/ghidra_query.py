#!/usr/bin/env python3
"""One-shot read-only queries against the Ghidra project recorded at import.

Each command starts analyzeHeadless and waits for it to exit. The project
directory is taken from ghidra_project.json. EU4_RE_EXPORT only selects the
corpus root that holds that file. -readOnly discards in-process program
changes; it is not a filesystem sandbox.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import analyze_headless, ghidra_install, load_ghidra_meta
from reverse_engineering.export_identity import read_ghidra_version
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.macho_rva import assert_image_base_matches_target
from reverse_engineering.paths import (
    ReError,
    ghidra_project_meta_path,
    load_target,
    local_binary_root,
)

SCHEMA = "eu4.ghidra_query.v1"
DEFAULT_MAX_BYTES = 24 * 1024
MIN_MAX_BYTES = 1024
HARD_MAX_BYTES = 256 * 1024
DEFAULT_PROCESS_TIMEOUT = 180
DEFAULT_DECOMPILE_TIMEOUT = 60
_RVA_RE = re.compile(r"0x[0-9a-fA-F]+$")
_SCRIPT = "QueryProgram.java"
_INSTRUCTION_LIMIT = 128
_DECOMPILE_LIMIT = 16000
_EDGE_LIMIT = 64
_REF_LIMIT = 64
_PCODE_LIMIT = 96


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else 2
    if args.max_bytes < MIN_MAX_BYTES or args.max_bytes > HARD_MAX_BYTES:
        print(f"max-bytes must be from {MIN_MAX_BYTES} to {HARD_MAX_BYTES}", file=sys.stderr)
        return 2
    offsets = _offsets(args)
    if any(value < 0 for value in offsets.values()):
        print("offsets must be zero or positive", file=sys.stderr)
        return 2
    try:
        document = execute(args.command, args.query, offsets, args.max_bytes, args.process_timeout)
    except (ReError, OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        document = _unavailable(args.command, exc)
    _emit(document, args.max_bytes)
    return 0


def execute(operation: str, query: str, offsets: dict[str, int], budget: int, process_timeout: int) -> dict[str, Any]:
    kind, normalized = _classify(query)
    if operation == "address" and kind != "rva":
        raise ValueError("address queries require an RVA")
    meta, project_dir = _recorded_project()
    image_base = int(meta["image_base"])
    assert_image_base_matches_target(image_base)
    identity = _identity(meta, image_base, kind, normalized)
    request: dict[str, Any] = {"op": operation, "decompile_timeout": DEFAULT_DECOMPILE_TIMEOUT}
    if kind == "rva":
        request["rva"] = normalized
        request["address"] = image_base + parse_rva(normalized)
    else:
        request["name"] = normalized
    request.update(_window(operation, offsets))
    raw = _invoke(project_dir, meta, request, process_timeout)
    problem = _identity_problem(raw, meta)
    if problem:
        return _document(operation, "identity_mismatch", identity=identity, reason=problem)
    return assemble(operation, raw, identity=identity, offsets=offsets, budget=budget)


def assemble(
    operation: str,
    raw: dict[str, Any],
    *,
    identity: dict[str, Any],
    offsets: dict[str, int],
    budget: int,
) -> dict[str, Any]:
    presence = raw.get("presence")
    if operation == "function" and presence == "absent":
        return _document(operation, "absent", identity=identity, evidence={"presence": "absent"})
    if operation == "function" and presence == "ambiguous":
        matches = list(raw.get("matches") or [])
        total = int(raw.get("match_total") or len(matches))
        return _page_matches(identity, matches, total, offsets.get("match", 0), budget)
    if operation == "address" and presence == "unmapped":
        return _document(operation, "unmapped", identity=identity, evidence={"presence": "unmapped"})
    if presence != "present":
        raise ValueError("malformed response")
    if operation == "function":
        return _page_function(raw, identity, offsets, budget)
    return _page_address(raw, identity, offsets, budget)


def _recorded_project() -> tuple[dict[str, Any], Path]:
    root = local_binary_root()
    meta_path = ghidra_project_meta_path(root)
    if not meta_path.is_file():
        raise ReError(f"missing {meta_path.name}")
    meta = load_ghidra_meta(root)
    for key in ("project_dir", "project_name", "program_name", "image_base"):
        if meta.get(key) in (None, ""):
            raise ReError(f"ghidra_project.json missing {key}")
    project_dir = Path(meta["project_dir"])
    if not project_dir.is_dir() or not any(path.suffix == ".gpr" or path.name.endswith(".rep") for path in project_dir.iterdir()):
        raise ReError(f"recorded ghidra project is missing: {project_dir}")
    return meta, project_dir


def _invoke(project_dir: Path, meta: dict[str, Any], request: dict[str, Any], process_timeout: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="eu4-ghidra-query-") as tmp:
        folder = Path(tmp)
        request_path = folder / "request.json"
        response_path = folder / "response.json"
        request_path.write_text(json.dumps(request), encoding="utf-8")
        analyze_headless(
            project_dir,
            str(meta["project_name"]),
            "-process",
            str(meta["program_name"]),
            "-noanalysis",
            "-readOnly",
            "-postScript",
            _SCRIPT,
            str(request_path),
            str(response_path),
            process_timeout=process_timeout,
        )
        if not response_path.is_file():
            raise ReError("query response missing")
        try:
            payload = json.loads(response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("malformed response") from exc
    if not isinstance(payload, dict):
        raise ValueError("malformed response")
    return payload


def _identity_problem(raw: dict[str, Any], meta: dict[str, Any]) -> str | None:
    target = load_target()
    got = str(raw.get("executable_sha256") or "").lower()
    if got != str(target["sha256"]).lower():
        return "executable_sha256 mismatch"
    try:
        image_base = int(raw.get("image_base"))
    except (TypeError, ValueError):
        return "image_base mismatch"
    if image_base != int(meta["image_base"]) or image_base != int(target["image_base"]):
        return "image_base mismatch"
    if raw.get("program_name") != meta.get("program_name"):
        return "program_name mismatch"
    return None


def _page_function(raw: dict[str, Any], identity: dict[str, Any], offsets: dict[str, int], budget: int) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "presence": "present",
        "entry_rva": raw.get("entry_rva"),
        "name": raw.get("name"),
        "body": raw.get("body") or [],
        "thunk": bool(raw.get("thunk")),
        "no_body": bool(raw.get("no_body")),
        "decompile_status": raw.get("decompile_status"),
        "hypothesis": _hypothesis(raw.get("hypothesis")),
        "instructions": list(raw.get("instructions") or []),
        "pseudocode": str(raw.get("pseudocode") or ""),
        "callers": list(raw.get("callers") or []),
        "callees": list(raw.get("callees") or []),
    }
    if raw.get("decompile_error"):
        evidence["decompile_error"] = _redact(str(raw.get("decompile_error")))
    totals = {
        "instructions": int(raw.get("instruction_total") or 0),
        "pseudocode": int(raw.get("pseudocode_total") or 0),
        "callers": int(raw.get("caller_total") or 0),
        "callees": int(raw.get("callee_total") or 0),
    }
    starts = {
        "instructions": offsets.get("instruction", 0),
        "pseudocode": offsets.get("decompile", 0),
        "callers": offsets.get("caller", 0),
        "callees": offsets.get("callee", 0),
    }
    document = _document("function", "ok", identity=identity, evidence=evidence)
    text = evidence["pseudocode"]

    def fits() -> bool:
        return _fits_function(document, evidence, starts, totals, budget)

    _drop_until(evidence, ("callees", "callers"), fits)
    if text and not fits():
        evidence["pseudocode"] = _fit_prefix(evidence, "pseudocode", text, fits)
    if not fits():
        _drop_until(evidence, ("instructions",), fits)
    if not fits() or _pending_without_progress(starts, totals, evidence, text_key="pseudocode"):
        return _budget("function", identity)
    return document


def _page_address(raw: dict[str, Any], identity: dict[str, Any], offsets: dict[str, int], budget: int) -> dict[str, Any]:
    value_kind = raw.get("value_kind")
    if value_kind not in ("static_image", "unavailable"):
        raise ValueError("malformed response")
    evidence: dict[str, Any] = {
        "presence": "present",
        "initialized": bool(raw.get("initialized")),
        "defined": bool(raw.get("defined")),
        "defined_kind": raw.get("defined_kind"),
        "value_kind": value_kind,
        "code_refs": list(raw.get("code_refs") or []),
        "data_refs": list(raw.get("data_refs") or []),
        "lifting": {"pcode": list(raw.get("pcode") or [])},
    }
    for key in ("instruction_start_rva", "defined_at_rva", "data_type", "symbol", "static_bytes"):
        if key in raw and raw.get(key) is not None:
            evidence[key] = raw.get(key)
    if raw.get("machine") is not None:
        evidence["machine"] = raw.get("machine")
    totals = {
        "code_refs": int(raw.get("code_ref_total") or 0),
        "data_refs": int(raw.get("data_ref_total") or 0),
        "pcode": int(raw.get("pcode_total") or 0),
    }
    starts = {
        "code_refs": offsets.get("code_ref", 0),
        "data_refs": offsets.get("data_ref", 0),
        "pcode": offsets.get("pcode", 0),
    }
    document = _document("address", "ok", identity=identity, evidence=evidence)

    def fits() -> bool:
        return _fits_address(document, evidence, starts, totals, budget)

    _drop_until(evidence["lifting"], ("pcode",), fits)
    _drop_until(evidence, ("data_refs", "code_refs"), fits)
    pcode = evidence["lifting"]["pcode"]
    if not fits() or _pending_without_progress(
        starts,
        totals,
        {"code_refs": evidence["code_refs"], "data_refs": evidence["data_refs"], "pcode": pcode},
    ):
        return _budget("address", identity)
    return document


def _page_matches(
    identity: dict[str, Any],
    matches: list[dict[str, Any]],
    total: int,
    start: int,
    budget: int,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {"presence": "ambiguous", "matches": list(matches)[start:]}
    document = _document("function", "ambiguous", identity=identity, evidence=evidence)

    def fits() -> bool:
        document.pop("next_match_offset", None)
        evidence.pop("match_blocked", None)
        returned = len(evidence["matches"])
        evidence["match_total"] = total
        evidence["match_returned"] = returned
        document["truncated"] = start + returned < total
        if document["truncated"]:
            if returned > 0:
                document["next_match_offset"] = start + returned
            else:
                evidence["match_blocked"] = True
        return _nbytes(document) <= budget

    _drop_until(evidence, ("matches",), fits)
    if not fits() or (start < total and not evidence["matches"]):
        return _budget("function", identity)
    return document


def _pending_without_progress(starts: dict[str, int], totals: dict[str, int], evidence: dict[str, Any], text_key: str | None = None) -> bool:
    pending = any(starts[key] < totals[key] for key in totals)
    if not pending:
        return False
    if text_key and evidence.get(text_key):
        return False
    return not any(evidence.get(key) for key in totals if key != text_key)


def _annotate_list(document: dict[str, Any], key: str, prefix: str, start: int, total: int, next_key: str) -> None:
    evidence = document["evidence"]
    returned = len(evidence[key])
    evidence[f"{prefix}_total"] = total
    evidence[f"{prefix}_returned"] = returned
    if start + returned < total:
        document["truncated"] = True
        if returned > 0:
            document[next_key] = start + returned
        else:
            evidence[f"{prefix}_blocked"] = True


def _annotate_text(document: dict[str, Any], start: int, total: int) -> None:
    evidence = document["evidence"]
    returned = len(evidence["pseudocode"])
    evidence["pseudocode_total"] = total
    evidence["pseudocode_returned"] = returned
    if start + returned < total:
        document["truncated"] = True
        if returned > 0:
            document["next_decompile_offset"] = start + returned
        else:
            evidence["decompile_blocked"] = True


def _drop_until(holder: dict[str, Any], keys: tuple[str, ...], fits) -> None:
    if fits():
        return
    for key in keys:
        while holder.get(key) and not fits():
            holder[key].pop()
            if fits():
                return


def _fit_prefix(evidence: dict[str, Any], key: str, text: str, fits) -> str:
    low, high, best = 0, len(text), 0
    while low <= high:
        mid = (low + high) // 2
        evidence[key] = text[:mid]
        if fits():
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    evidence[key] = text[:best]
    return evidence[key]


def _fits_function(document, evidence, starts, totals, budget: int) -> bool:
    for key in ("next_instruction_offset", "next_decompile_offset", "next_caller_offset", "next_callee_offset"):
        document.pop(key, None)
    for key in (
        "instruction_total", "instruction_returned", "instruction_blocked",
        "pseudocode_total", "pseudocode_returned", "decompile_blocked",
        "caller_total", "caller_returned", "caller_blocked",
        "callee_total", "callee_returned", "callee_blocked",
    ):
        evidence.pop(key, None)
    document["truncated"] = False
    _annotate_list(document, "instructions", "instruction", starts["instructions"], totals["instructions"], "next_instruction_offset")
    _annotate_text(document, starts["pseudocode"], totals["pseudocode"])
    _annotate_list(document, "callers", "caller", starts["callers"], totals["callers"], "next_caller_offset")
    _annotate_list(document, "callees", "callee", starts["callees"], totals["callees"], "next_callee_offset")
    return _nbytes(document) <= budget


def _fits_address(document, evidence, starts, totals, budget: int) -> bool:
    for key in ("next_code_ref_offset", "next_data_ref_offset", "next_pcode_offset"):
        document.pop(key, None)
    for key in (
        "code_ref_total", "code_ref_returned", "code_ref_blocked",
        "data_ref_total", "data_ref_returned", "data_ref_blocked",
    ):
        evidence.pop(key, None)
    lifting = evidence["lifting"]
    lifting.pop("pcode_blocked", None)
    document["truncated"] = False
    _annotate_list(document, "code_refs", "code_ref", starts["code_refs"], totals["code_refs"], "next_code_ref_offset")
    _annotate_list(document, "data_refs", "data_ref", starts["data_refs"], totals["data_refs"], "next_data_ref_offset")
    returned = len(lifting["pcode"])
    lifting["pcode_total"] = totals["pcode"]
    lifting["pcode_returned"] = returned
    if starts["pcode"] + returned < totals["pcode"]:
        document["truncated"] = True
        if returned > 0:
            document["next_pcode_offset"] = starts["pcode"] + returned
        else:
            lifting["pcode_blocked"] = True
    return _nbytes(document) <= budget


def _hypothesis(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "source": "ghidra",
        "calling_convention": source.get("calling_convention"),
        "return_type": source.get("return_type"),
        "parameters": list(source.get("parameters") or []),
    }


def _window(operation: str, offsets: dict[str, int]) -> dict[str, int]:
    if operation == "function":
        return {
            "instruction_offset": offsets.get("instruction", 0),
            "instruction_limit": _INSTRUCTION_LIMIT,
            "decompile_offset": offsets.get("decompile", 0),
            "decompile_limit": _DECOMPILE_LIMIT,
            "caller_offset": offsets.get("caller", 0),
            "caller_limit": _EDGE_LIMIT,
            "callee_offset": offsets.get("callee", 0),
            "callee_limit": _EDGE_LIMIT,
        }
    return {
        "code_ref_offset": offsets.get("code_ref", 0),
        "code_ref_limit": _REF_LIMIT,
        "data_ref_offset": offsets.get("data_ref", 0),
        "data_ref_limit": _REF_LIMIT,
        "pcode_offset": offsets.get("pcode", 0),
        "pcode_limit": _PCODE_LIMIT,
    }


def _classify(query: str) -> tuple[str, str]:
    text = query.strip()
    if not text:
        raise ValueError("empty query")
    if _RVA_RE.match(text):
        return "rva", format_rva(parse_rva(text))
    return "name", text


def _ghidra_version() -> str:
    try:
        return read_ghidra_version(ghidra_install())
    except ReError:
        return "unknown"


def _identity(meta: dict[str, Any], image_base: int, kind: str, normalized: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ghidra_version": _ghidra_version(),
        "program_name": meta.get("program_name"),
        "executable_sha256": load_target()["sha256"],
        "image_base": image_base,
        "query": normalized,
    }
    if kind == "rva":
        body["queried_rva"] = normalized
        body["queried_address"] = format_rva(image_base + parse_rva(normalized))
    return body


def _document(
    operation: str,
    status: str,
    *,
    identity: dict[str, Any] | None = None,
    evidence: Any = None,
    truncated: bool = False,
    reason: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": SCHEMA,
        "operation": operation,
        "status": status,
        "identity": identity,
        "evidence": evidence,
        "truncated": truncated,
    }
    if reason:
        body["reason"] = reason
    if detail:
        body["detail"] = detail
    return body


def _budget(operation: str, identity: dict[str, Any]) -> dict[str, Any]:
    return _document(operation, "unavailable", identity={"query": identity.get("query")}, reason="response_budget")


def _unavailable(operation: str, exc: Exception) -> dict[str, Any]:
    text = _redact(str(exc))[:500]
    reason = "malformed_response" if str(exc) == "malformed response" else "query_failed"
    return _document(operation, "unavailable", reason=reason, detail=text or None)


def _emit(document: dict[str, Any], budget: int) -> None:
    if _nbytes(document) > budget:
        document = _budget(str(document.get("operation") or "query"), {"query": None})
    sys.stdout.write(_serialize(document) + "\n")


def _nbytes(document: dict[str, Any]) -> int:
    return len(_serialize(document).encode()) + 1


def _serialize(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _redact(text: str) -> str:
    return re.sub(r"(?:/[^\s'\"]+)+", lambda match: Path(match.group(0)).name, text)


def _offsets(args: argparse.Namespace) -> dict[str, int]:
    if args.command == "function":
        return {
            "instruction": args.instruction_offset,
            "decompile": args.decompile_offset,
            "caller": args.caller_offset,
            "callee": args.callee_offset,
            "match": args.match_offset,
        }
    return {
        "code_ref": args.code_ref_offset,
        "data_ref": args.data_ref_offset,
        "pcode": args.pcode_offset,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reverse_engineering.ghidra_query")
    sub = parser.add_subparsers(dest="command", required=True)
    function = sub.add_parser("function")
    _common(function)
    function.add_argument("query")
    function.add_argument("--instruction-offset", type=int, default=0)
    function.add_argument("--decompile-offset", type=int, default=0)
    function.add_argument("--caller-offset", type=int, default=0)
    function.add_argument("--callee-offset", type=int, default=0)
    function.add_argument("--match-offset", type=int, default=0)
    address = sub.add_parser("address")
    _common(address)
    address.add_argument("query")
    address.add_argument("--code-ref-offset", type=int, default=0)
    address.add_argument("--data-ref-offset", type=int, default=0)
    address.add_argument("--pcode-offset", type=int, default=0)
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--process-timeout", type=int, default=DEFAULT_PROCESS_TIMEOUT)


if __name__ == "__main__":
    raise SystemExit(main())
