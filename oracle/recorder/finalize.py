"""Offline finalization of a capture: resource identity, aliasing, semantic dedup.

The recorder (P4.3/P4.4) counts and captures; it does **not** decide equality.
This module closes the capture/classification split offline. Given the sealed
observation records a run produced and the recorder's native counters, it

* resolves resource identity by lifetime - ``resource_id@generation`` - not by the
  handle an observation happened to use, so two observations of one resource
  through two handles share an identity, while a reused slot in a new generation
  is a different resource;
* classifies fully qualified records into ``unique`` and ``verified_repeat`` by
  their ``semantic_digest`` - canonical semantic equality, never a hot-path byte
  compare;
* asserts the accounting identity

      intercepted = unique + verified_repeat + incomplete + counted_only + lost

  so a run can never report "nothing new" while silently dropping observations.

It touches no process and never opens EU IV.
"""
from __future__ import annotations

from typing import Any

from oracle.recorder import schema

LEDGER_SCHEMA_VERSION = "eu4-oracle-ledger/v1"

#: The native recorder counters the identity is asserted against.
REQUIRED_COUNTERS = ("intercepted", "unselected", "captured", "incomplete", "lost")

class FinalizeError(ValueError):
    """A capture could not be finalized consistently."""


def resource_identity(resource: dict[str, Any]) -> str:
    """The lifetime identity of a resource: contents version is deliberately excluded."""
    return f"{resource['resource_id']}@{int(resource['generation'])}"


def observed_resources(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each observed resource lifetime to its observed aliases and content versions.

    Two handles onto one resource collapse to one key; a reused slot in a new
    generation does not, because the generation is part of the key.
    """
    observed: dict[str, dict[str, Any]] = {}
    for sealed in records:
        schema.verify(sealed)
        for resource in sealed["payload"]["resources"]:
            key = resource_identity(resource)
            entry = observed.setdefault(key, {"aliases": set(), "versions": set()})
            entry["versions"].add(int(resource["version"]))
            for alias in resource["aliases"]:
                entry["aliases"].add(f"{alias['resource_id']}@{int(alias['generation'])}")
    return {key: {"aliases": sorted(entry["aliases"]), "versions": sorted(entry["versions"])}
            for key, entry in sorted(observed.items())}


def _counters(counters: dict[str, Any]) -> dict[str, int]:
    clean: dict[str, int] = {}
    for name in REQUIRED_COUNTERS:
        if name not in counters:
            raise FinalizeError(f"counters are missing {name!r}")
        value = counters[name]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise FinalizeError(f"counter {name!r} must be a non-negative integer")
        clean[name] = value
    return clean


def classify(records: list[dict[str, Any]],
             counters: dict[str, Any]) -> dict[str, Any]:
    """Classify sealed records and assert the capture accounting identity.

    ``records`` are the materialized observations - the ``captured`` and
    ``incomplete`` ones. ``counted-only`` observations are never materialized: they
    exist only as the ``unselected`` counter.

    Native capture completeness and semantic qualification are two different
    statements and are reconciled, never conflated. A record the native recorder
    counted as ``captured`` is stored and accounted for even when its declared
    semantic domain is incomplete; it simply receives no semantic digest and never
    becomes ``verified_repeat``. The identity is therefore

        intercepted = unique + verified_repeat + domain_incomplete
                      + incomplete + counted_only + lost

    where ``domain_incomplete`` is the part of the native ``captured`` count that is
    not semantically comparable. Native counters are never rewritten to make this
    pass.
    """
    clean = _counters(counters)

    captured: list[dict[str, Any]] = []
    qualified: list[dict[str, Any]] = []
    incomplete = 0
    gap_counts: dict[str, int] = {}
    for sealed in records:
        schema.verify(sealed)
        payload = sealed["payload"]
        state = payload["completeness"]["state"]
        if state == "captured":
            captured.append(sealed)
            gaps = schema.qualification_gaps(payload)
            if gaps:
                for gap in gaps:
                    category = gap.split(":", 1)[0]
                    gap_counts[category] = gap_counts.get(category, 0) + 1
            else:
                qualified.append(sealed)
        elif state == "incomplete":
            incomplete += 1
        else:
            raise FinalizeError(f"a {state!r} record must not be materialized")
        if payload["completeness"]["losses"] != 0:
            raise FinalizeError("a materialized record must not carry sub-observation loss")

    if len(captured) != clean["captured"]:
        raise FinalizeError(
            f"native counted {clean['captured']} captured observations but {len(captured)} "
            "records were materialized")
    if incomplete != clean["incomplete"]:
        raise FinalizeError(
            f"native counted {clean['incomplete']} incomplete observations but {incomplete} "
            "records were materialized")

    groups: dict[str, list[str]] = {}
    projections: dict[str, bytes] = {}
    for sealed in qualified:
        payload = sealed["payload"]
        digest = schema.semantic_digest(payload)
        projection = schema.canonical_bytes(schema.semantic_projection(payload))
        seen = projections.get(digest)
        if seen is None:
            projections[digest] = projection
        elif seen != projection:
            # A digest is an index, never the decision: equal hashes over unequal
            # canonical projections are a collision, and the bucket fails closed
            # rather than merging two different behaviours.
            raise FinalizeError(
                f"semantic digest collision: unequal canonical projections share {digest}")
        groups.setdefault(digest, []).append(payload["invocation"]["call_id"])
    unique = len(groups)
    verified_repeat = len(qualified) - unique
    domain_incomplete = len(captured) - len(qualified)

    total = (unique + verified_repeat + domain_incomplete + clean["incomplete"]
             + clean["unselected"] + clean["lost"])
    if total != clean["intercepted"]:
        raise FinalizeError(
            f"capture accounting violated: {total} observations accounted for against "
            f"{clean['intercepted']} intercepted")

    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "intercepted": clean["intercepted"],
        "unique": unique,
        "verified_repeat": verified_repeat,
        "domain_incomplete": domain_incomplete,
        "domain_incomplete_gaps": dict(sorted(gap_counts.items())),
        "incomplete": clean["incomplete"],
        "counted_only": clean["unselected"],
        "lost": clean["lost"],
        "groups": {digest: sorted(call_ids) for digest, call_ids in sorted(groups.items())},
    }


# --- concurrent capture order ---------------------------------------------------

def partial_order(events: list[dict[str, Any]],
                  syncs: list[dict[str, Any]]) -> dict[int, set[int]]:
    """The happens-before relation that per-thread sequences and sync edges induce.

    ``events`` are ``{"thread_id", "sequence", "phase"}`` records. Within a thread,
    consecutive sequences are ordered. Across threads, a declared barrier orders
    every participating thread's ``phase_before`` events before every participating
    thread's ``phase_after`` events. The result is a map from event index to the set
    of its predecessors - a partial order, deliberately not a total one, because two
    events in the same phase on different threads are genuinely unordered.
    """
    preds: dict[int, set[int]] = {index: set() for index in range(len(events))}
    by_thread: dict[int, list[int]] = {}
    for index, event in enumerate(events):
        by_thread.setdefault(event["thread_id"], []).append(index)
    for indices in by_thread.values():
        ordered = sorted(indices, key=lambda index: events[index]["sequence"])
        for earlier, later in zip(ordered, ordered[1:]):
            preds[later].add(earlier)
    for sync in syncs:
        participants = set(sync["participants"])
        before = [index for index, event in enumerate(events)
                  if event["thread_id"] in participants
                  and event["phase"] == sync["phase_before"]]
        after = [index for index, event in enumerate(events)
                 if event["thread_id"] in participants
                 and event["phase"] == sync["phase_after"]]
        for index in before:
            for later in after:
                preds[later].add(index)
    return preds


def is_acyclic(preds: dict[int, set[int]]) -> bool:
    """Whether the relation admits a topological order, i.e. is a partial order."""
    parents = {node: set(predecessors) for node, predecessors in preds.items()}
    successors: dict[int, set[int]] = {node: set() for node in parents}
    for node, node_parents in parents.items():
        for parent in node_parents:
            successors.setdefault(parent, set()).add(node)
    ready = [node for node, node_parents in parents.items() if not node_parents]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for child in sorted(successors[node]):
            parents[child].discard(node)
            if not parents[child]:
                ready.append(child)
    return seen == len(parents)
