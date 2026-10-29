#!/usr/bin/env python3
"""Readiness shortlist of the graphics-anchor closure (work order INV).

Inputs are the committed bounded static graph and the frozen renderer corpus. The corpus is
joined by pinned address only: a node's absolute `address` minus the image base must equal the
row's `entry_rva`. Names are metadata. A node without a row at its address is unresolved, never
name-matched. Only rows joined to the closure are read for evidence; the corpus is not scored.

Labels: `ready-leaf` (no internal callees, nothing unresolved or unvisited anywhere below it),
`blocked` (some evidence is unresolved or unvisited, locally or in a callee), and
`not-this-stage` (an existing qualified RE package, or a resolved composite that needs callee
packages first). An unresolved or unvisited edge is not evidence of no dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from reverse_engineering.intervals import parse_rva
from reverse_engineering.paths import ROOT, ReError, load_target

SCHEMA = "eu4-trace-readiness-shortlist/v1"
STATIC_GRAPH = ROOT / "analysis" / "frame-model-static.json"
CORPUS = ROOT / "analysis" / "renderer-corpus" / "scope" / "v1" / "renderer-functions.jsonl"
RECONSTRUCTION = ROOT / "analysis" / "reconstruction"
OUTPUT = ROOT / "analysis" / "trace-readiness" / "shortlist-v1.json"
# llvm-objdump symbolizes unnamed __stubs entries against the undefined `dyld_stub_binder` (value 0),
# so the offset is the absolute stub address.
STUB_PREFIX = "dyld_stub_binder+"


def _rva(value: int) -> str:
    return f"0x{value:x}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tarjan(nodes: list[str], succ: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components, callees before callers; deterministic for sorted input."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    out: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on_stack.add(v)
        for w in succ.get(v, []):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            component = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                component.append(w)
                if w == v:
                    break
            out.append(sorted(component))

    for v in nodes:
        if v not in index:
            visit(v)
    return out


def load_packages(reconstruction_dir: Path, binary_sha: str) -> dict[int, dict]:
    """Existing RE packages keyed by the entry RVA in their instruction inventory (read-only)."""
    packages: dict[int, dict] = {}
    for inventory in sorted(reconstruction_dir.glob("*/instructions.json")):
        meta = json.loads(inventory.read_text())
        if meta.get("binary_sha256") != binary_sha:
            continue
        pkg = inventory.parent
        level = "RE2" if (pkg / "differential").is_dir() else "RE1" if (pkg / "reconstruction").is_dir() else "RE0"
        packages[parse_rva(meta["entry_rva"])] = {
            "path": pkg.relative_to(ROOT).as_posix() if pkg.is_relative_to(ROOT) else pkg.name,
            "level": level,
        }
    return packages


def _classify(name: str, alias_to_node: dict[str, str], unvisited: set[str], image_base: int) -> str:
    if name in alias_to_node:
        return "visited"
    if name in unvisited:
        return "unvisited_at_bound"
    if name.startswith(STUB_PREFIX):
        try:
            if int(name[len(STUB_PREFIX):], 16) >= image_base:
                return "external_stub"
        except ValueError:
            pass
    return "unresolved"


def build_shortlist(
    static: dict,
    corpus_rows: list[dict],
    packages: dict[int, dict],
    target: dict,
    inputs: dict | None = None,
) -> dict:
    binary_sha = target["sha256"]
    image_base = int(target["image_base"])
    if static.get("executable_sha256") != binary_sha:
        raise ReError("contract conflict: static graph executable_sha256 differs from target.json")
    graph = static["graph"]
    nodes: dict[str, dict] = graph["nodes"]
    unvisited = set(graph["unvisited_at_bound"])

    corpus: dict[int, dict] = {}
    for row in corpus_rows:
        rva = parse_rva(row["entry_rva"])
        if rva in corpus:
            raise ReError(f"duplicate corpus entry_rva {row['entry_rva']}")
        corpus[rva] = row

    alias_to_node: dict[str, str] = {}
    by_address: dict[int, str] = {}
    for key, node in nodes.items():
        if not isinstance(node.get("address"), int):
            raise ReError(f"node {key} has no integer address")
        if node["address"] in by_address:
            raise ReError(f"nodes {by_address[node['address']]} and {key} share an address")
        by_address[node["address"]] = key
        for alias in {key, *node["aliases"]}:
            alias_to_node[alias] = key

    def callee_nodes(key: str) -> list[str]:
        return sorted(
            {alias_to_node[e["target"]] for e in nodes[key]["edges"] if e["target"] in alias_to_node}
        )

    anchors = sorted(graph["roots"])
    closure: set[str] = set()
    pending = sorted({alias_to_node[r] for r in anchors if r in alias_to_node})
    while pending:
        key = pending.pop()
        if key not in closure:
            closure.add(key)
            pending.extend(callee_nodes(key))
    order = sorted(closure, key=lambda k: nodes[k]["address"])
    succ = {k: callee_nodes(k) for k in order}

    records: dict[str, dict] = {}
    for key in order:
        node = nodes[key]
        rva = node["address"] - image_base
        row = corpus.get(rva)
        callees: dict[tuple, dict] = {}
        stubs: dict[int, int] = {}
        for edge in node["edges"]:
            status = _classify(edge["target"], alias_to_node, unvisited, image_base)
            if status == "external_stub":
                stub = int(edge["target"][len(STUB_PREFIX):], 16)
                stubs[stub] = stubs.get(stub, 0) + 1
                continue
            callee_rva = nodes[alias_to_node[edge["target"]]]["address"] - image_base if status == "visited" else None
            entry_key = (status, callee_rva if callee_rva is not None else edge["target"])
            entry = callees.setdefault(
                entry_key, {"status": status, "rva": _rva(callee_rva) if callee_rva is not None else None,
                            "symbol": edge["target"], "sites": 0}
            )
            entry["sites"] += 1
        external = [
            {"kind": "stub_call", "stub_va": _rva(stub), "symbol": None, "sites": n}
            for stub, n in sorted(stubs.items())
        ] + [
            {"kind": "gl_pointer_reference", "stub_va": None, "symbol": name, "sites": n}
            for name, n in sorted(_count(t for r in node["gl_pointer_references"] for t in r["targets"]).items())
        ]
        blocking = []
        if node["indirect_edges"]:
            blocking.append("unresolved_indirect_site")
        statuses = {c["status"] for c in callees.values()}
        if "unvisited_at_bound" in statuses:
            blocking.append("edge_to_unvisited_function")
        if "unresolved" in statuses:
            blocking.append("unresolved_call_target")
        if row is None:
            blocking.append("corpus_join_unresolved")
        else:
            if row["missing_indirect_sites"]:
                blocking.append("corpus_missing_indirect_sites")
            if row["pcode_callind_sites"]:
                blocking.append("corpus_pcode_callind_sites")
            # The static graph records `callq` only; direct tail `jmp`s are flow-only sites in the corpus.
            if row["flow_only_sites"] or row["census_callsites"] > len(node["edges"]):
                blocking.append("corpus_call_sites_not_in_static_graph")
        records[key] = {
            "rva": _rva(rva),
            "symbol": key,
            "aliases": sorted(node["aliases"]),
            "depth": node["depth"],
            "binary_sha256": binary_sha,
            "corpus_join": "address" if row is not None else "unresolved",
            "corpus": None if row is None else {
                "entry_rva": row["entry_rva"],
                "name_demangled": row["name_demangled"],
                "kind": row["kind"],
                "tier": row["tier"],
                "instruction_count": row["instruction_count"],
                "census_callsites": row["census_callsites"],
                "flow_only_sites": row["flow_only_sites"],
                "missing_indirect_sites": len(row["missing_indirect_sites"]),
                "pcode_callind_sites": row["pcode_callind_sites"],
            },
            "direct_callees": sorted(callees.values(), key=lambda c: (c["status"], c["rva"] or "", c["symbol"])),
            "indirect_sites": [
                {"site_rva": _rva(e["address"] - image_base), "operand": e["operand"], "status": e["status"]}
                for e in sorted(node["indirect_edges"], key=lambda e: e["address"])
            ],
            "external_api_calls": external,
            "re_package": packages.get(rva),
            "blocking": blocking,
        }

    cycles = []
    blocked_component: dict[str, bool] = {}
    for component in tarjan(order, succ):
        members = set(component)
        cyclic = len(component) > 1 or component[0] in succ[component[0]]
        if cyclic:
            cycles.append(sorted(records[k]["rva"] for k in component))
        outward = sorted({w for k in component for w in succ[k] if w not in members})
        blocked = any(records[k]["blocking"] for k in component) or any(blocked_component[w] for w in outward)
        for k in component:
            blocked_component[k] = blocked
        for k in component:
            rec = records[k]
            if any(blocked_component[w] for w in succ[k] if w not in members):
                rec["blocking"].append("callee_blocked")
            if blocked and not rec["blocking"]:
                rec["blocking"].append("cycle_member_blocked")
            if rec["re_package"] is not None:
                rec["label"], rec["label_reason"] = "not-this-stage", "existing_qualified_re_package"
            elif rec["blocking"]:
                rec["label"], rec["label_reason"] = "blocked", rec["blocking"][0]
            elif succ[k]:
                rec["label"], rec["label_reason"] = "not-this-stage", "composite_needs_callee_packages"
            else:
                rec["label"], rec["label_reason"] = "ready-leaf", "no_internal_callees_all_evidence_resolved"

    functions = [records[k] for k in order]
    counts = _count(f["label"] for f in functions)
    return {
        "schema": SCHEMA,
        "binary_sha256": binary_sha,
        "image_base": image_base,
        "inputs": inputs or {},
        "anchors": anchors,
        "unresolved_anchors": [r for r in anchors if r not in alias_to_node],
        "bounds": {
            "max_depth": graph["max_depth"],
            "capacity": graph["capacity"],
            "unvisited_at_bound": len(unvisited),
            "limits": graph["limits"],
        },
        "counts": {label: counts.get(label, 0) for label in ("blocked", "not-this-stage", "ready-leaf")},
        "ready_leaves": [
            {"rva": f["rva"], "symbol": f["symbol"], "external_api_calls": len(f["external_api_calls"])}
            for f in functions
            if f["label"] == "ready-leaf"
        ],
        "cycles": sorted(cycles),
        "functions": functions,
    }


def _count(items) -> dict:
    out: dict = {}
    for item in items:
        out[item] = out.get(item, 0) + 1
    return out


def build_committed() -> dict:
    target = load_target()
    corpus_rows = [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]
    inputs = {
        path.relative_to(ROOT).as_posix(): _sha256(path)
        for path in (STATIC_GRAPH, CORPUS)
    }
    return build_shortlist(
        json.loads(STATIC_GRAPH.read_text()),
        corpus_rows,
        load_packages(RECONSTRUCTION, target["sha256"]),
        target,
        inputs,
    )


def render(shortlist: dict) -> bytes:
    return (json.dumps(shortlist, indent=1, sort_keys=True) + "\n").encode()


def cmd_build(args: argparse.Namespace) -> int:
    shortlist = build_committed()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(render(shortlist))
    print(json.dumps({"written": str(args.output), "counts": shortlist["counts"]}, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    if not args.output.is_file() or args.output.read_bytes() != render(build_committed()):
        print(f"error: {args.output} is stale; run `build`", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "path": str(args.output)}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Anchor-closure readiness shortlist (INV)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("build", cmd_build), ("check", cmd_check)):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, default=OUTPUT)
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
