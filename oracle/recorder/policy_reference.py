"""Differential reference model for the P4 capture-policy descriptor.

The native declaration in ``oracle/recorder/native/driver.c`` is authoritative:
that is what actually runs. This module is a *reference*, not a second source of
truth. It re-derives the canonical descriptor bytes from a small declared
descriptor so a test can prove the native encoder and the reference agree. If the
two ever drift, the test fails rather than a digest silently changing meaning.

Hashing lives here, offline, off the recorder's hot path: the native core emits
canonical bytes only.
"""
from __future__ import annotations

import hashlib
import struct
from typing import Any

# Hook ids and rule codes mirror the native enums.
HOOK_IDS = {"create": 1, "update": 2, "read": 3, "share": 4, "destroy": 5}
RULE_CODES = {"count-only": 0, "deep": 1}

# The two policies declared in the native driver, in the order they are printed.
DECLARED_POLICIES: dict[str, dict[str, Any]] = {
    "observe-data": {
        "version": 1,
        "default": "count-only",
        "rules": [{"hook": "read", "rule": "deep"}, {"hook": "update", "rule": "deep"}],
    },
    "observe-flow": {
        "version": 1,
        "default": "deep",
        "rules": [{"hook": "create", "rule": "count-only"},
                  {"hook": "destroy", "rule": "count-only"}],
    },
}


def canonical_bytes(descriptor: dict[str, Any]) -> bytes:
    """The canonical descriptor encoding: big-endian fields, rules sorted by hook id."""
    rules = sorted((HOOK_IDS[r["hook"]], RULE_CODES[r["rule"]]) for r in descriptor["rules"])
    out = struct.pack(">III", int(descriptor["version"]), RULE_CODES[descriptor["default"]],
                      len(rules))
    for hook_id, rule in rules:
        out += struct.pack(">II", hook_id, rule)
    return out


def digest(descriptor: dict[str, Any]) -> str:
    """The policy digest a record names: sha256 over the canonical descriptor bytes."""
    return hashlib.sha256(canonical_bytes(descriptor)).hexdigest()


def rule_for(descriptor: dict[str, Any], hook: str) -> str:
    """The rule that applies to a hook: the first declared override, else the default."""
    hook_id = HOOK_IDS[hook]
    for rule in descriptor["rules"]:
        if HOOK_IDS[rule["hook"]] == hook_id:
            return rule["rule"]
    return descriptor["default"]
