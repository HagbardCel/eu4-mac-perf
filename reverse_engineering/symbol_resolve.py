"""Resolve seed / hook symbols against Ghidra inventory naming conventions."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from functools import lru_cache
from typing import Any

ITANIUM_RE = re.compile(r"^__?Z")


class SeedResolutionError(Exception):
    pass


def normalize_demangled(name: str | None) -> str:
    if not name:
        return ""
    text = name.strip()
    if text.endswith("()"):
        text = text[:-2]
    if "(" in text:
        text = text[: text.index("(")]
    return text.strip()


def inventory_aliases(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    short = row.get("name_mangled")
    if short:
        keys.add(short)
    dem = normalize_demangled(row.get("name_demangled"))
    if dem:
        keys.add(dem)
    namespace = row.get("namespace") or ""
    if short and namespace and namespace != "Global":
        keys.add(f"{namespace}::{short}")
    return keys


def build_symbol_index(functions: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    buckets: dict[str, set[str]] = defaultdict(set)
    for rva, row in functions.items():
        for key in inventory_aliases(row):
            buckets[key].add(rva)
    return {key: sorted(rvas) for key, rvas in buckets.items()}


def mach_o_demangle_input(symbol: str) -> str:
    return symbol[1:] if symbol.startswith("__Z") else symbol


@lru_cache(maxsize=256)
def demangle_itanium(symbol: str) -> str | None:
    if not ITANIUM_RE.match(symbol):
        return None
    filt_input = mach_o_demangle_input(symbol)
    result = subprocess.run(
        ["c++filt", filt_input],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    out = result.stdout.strip()
    if out == filt_input:
        return None
    return out


def _filter_matches(
    matches: list[str],
    functions: dict[str, dict[str, Any]],
    require_internal: bool,
) -> list[str]:
    if not require_internal:
        return matches
    return [
        rva
        for rva in matches
        if functions[rva].get("kind") in ("internal", "thunk")
    ]


def _lookup_index(
    index: dict[str, list[str]],
    key: str,
    functions: dict[str, dict[str, Any]],
    require_internal: bool,
) -> list[str]:
    return _filter_matches(index.get(key, []), functions, require_internal)


def resolve_symbol_to_rva(
    functions: dict[str, dict[str, Any]],
    symbol: str,
    require_internal: bool = False,
    index: dict[str, list[str]] | None = None,
) -> str:
    if index is None:
        index = build_symbol_index(functions)

    matches: list[str] = []
    for rva, row in functions.items():
        if row.get("name_mangled") == symbol:
            matches.append(rva)
    matches = _filter_matches(matches, functions, require_internal)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SeedResolutionError(f"ambiguous symbol {symbol}: {matches}")

    for key in (normalize_demangled(symbol), symbol):
        if not key:
            continue
        matches = _lookup_index(index, key, functions, require_internal)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SeedResolutionError(f"ambiguous symbol {symbol}: {matches}")

    demangled = demangle_itanium(symbol)
    if demangled:
        key = normalize_demangled(demangled)
        matches = _lookup_index(index, key, functions, require_internal)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SeedResolutionError(f"ambiguous symbol {symbol}: {matches}")

    raise SeedResolutionError(f"no match for symbol {symbol}")
