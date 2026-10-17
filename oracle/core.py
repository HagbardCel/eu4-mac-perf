"""Small OFFLINE contract prototype. Not a runtime recorder or secure sandbox.

Demonstrates count reconciliation, collision-safe deep dedup, and first-divergence
reporting. JSON inputs are already canonical; real project must implement verified
pointer relocation, ABI, memory validity and resource semantics separately.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf8")


@dataclass(frozen=True)
class Duplicate:
    digest: str
    is_new: bool
    bucket_collisions: int


class ObservationLedger:
    """Increment encounter count for every successful hook call, including uncaptured.

    Deep captures are deduplicated by canonical observable payload equality, not
    by a hash or input fingerprint alone. Real recorder increments separately
    on the hot path and does expensive work offline.
    """

    def __init__(self):
        self.entries: Counter[str] = Counter()
        self.deep: Counter[str] = Counter()
        self.proven_duplicate: Counter[str] = Counter()
        self.observed_unique: dict[str, list[bytes]] = defaultdict(list)
        self.hash_buckets: dict[tuple[str, str], list[bytes]] = defaultdict(list)
        self.indeterminate: Counter[str] = Counter()
        self.loss: Counter[str] = Counter()

    def encountered(self, function_id: str, *, loss: bool = False) -> None:
        self.entries[function_id] += 1
        if loss:
            self.loss[function_id] += 1

    def observed(self, function_id: str, payload: Any, *, complete: bool) -> Duplicate | None:
        if self.deep[function_id] >= self.entries[function_id]:
            raise ValueError("deep capture without matching intercepted entry")
        self.deep[function_id] += 1
        if not complete:
            self.indeterminate[function_id] += 1
            return None
        raw = canonical_bytes(payload)
        digest = sha256(raw).hexdigest()
        bucket = self.hash_buckets[(function_id, digest)]
        for other in bucket:
            if raw == other:  # validates equality after digest match
                self.proven_duplicate[function_id] += 1
                return Duplicate(digest, False, len(bucket) - 1)
        bucket.append(raw)
        self.observed_unique[function_id].append(raw)
        return Duplicate(digest, True, len(bucket) - 1)

    def summary(self, function_id: str) -> dict[str, int]:
        n = self.entries[function_id]
        deep = self.deep[function_id]
        distinct = len(self.observed_unique[function_id])
        dup = self.proven_duplicate[function_id]
        unknown = self.indeterminate[function_id]
        assert n >= deep and deep == distinct + dup + unknown
        return {"encountered": n, "deep_captured": deep, "unique_complete": distinct,
                "verified_repeats": dup, "incomplete_deep": unknown,
                "counted_only": n - deep, "lost_events": self.loss[function_id]}


def compare_fixture(expected: dict, actual: dict) -> dict:
    """Minimal deterministic comparator: ordered effects and raw state bytes.

    Strictly refuses a pass when original evidence is incomplete. Never claims
    universal equivalence. Real comparator additionally verifies ABI, alias
    relocation, memory masks, environment and complete effect coverage.
    """
    meta = expected.get("capture", {})
    if not meta.get("complete", False) or meta.get("lost_events", 0) or meta.get("unknown_dependencies"):
        return {"status": "INCOMPLETE_ORACLE", "first_divergence": None}
    for key in ("return_hex", "exit"):
        if expected.get(key) != actual.get(key):
            return {"status": "MISMATCH", "first_divergence": {"domain": "return" if key == "return_hex" else "exception", "field": key, "expected": expected.get(key), "actual": actual.get(key)}}
    a = expected.get("post_memory", {})
    b = actual.get("post_memory", {})
    for obj in sorted(set(a) | set(b)):
        v1, v2 = a.get(obj), b.get(obj)
        if v1 == v2:
            continue
        if v1 is None or v2 is None:
            return {"status": "MISMATCH", "first_divergence": {"domain": "memory", "object": obj, "field": "presence", "expected": v1, "actual": v2}}
        x1, x2 = bytes.fromhex(v1), bytes.fromhex(v2)
        first = next((i for i in range(min(len(x1), len(x2))) if x1[i] != x2[i]), min(len(x1), len(x2)))
        return {"status": "MISMATCH", "first_divergence": {"domain": "memory", "object": obj, "byte_offset": first, "expected": x1[first:first+1].hex(), "actual": x2[first:first+1].hex()}}
    ev1, ev2 = expected.get("effects", []), actual.get("effects", [])
    for i in range(max(len(ev1), len(ev2))):
        x, y = (ev1[i] if i < len(ev1) else None), (ev2[i] if i < len(ev2) else None)
        if x != y:
            return {"status": "MISMATCH", "first_divergence": {"domain": "effects", "event_index": i, "expected": x, "actual": y}}
    return {"status": "PASS", "scope": "captured fixture only", "first_divergence": None}
