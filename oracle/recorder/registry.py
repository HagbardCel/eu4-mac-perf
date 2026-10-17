"""Offline resource registry and closure verifier for the P4 recorder.

An observation is only replayable if every resource it addressed is one the capture
actually holds. The recorder core is function-agnostic - it does not know what a
resource is - so the hook layer declares, per observation, an opaque *observation
context* naming the resource the call addressed and the slot state that resolves it.
This module reads that context and decides closure.

A resource is named by its **lifetime** - ``slot:<slot>@<generation>`` - never by a
bare handle. A handle whose generation no longer lives in its slot names a resource
that is *gone*, so the observation yields ``INCOMPLETE_ORACLE`` instead of being
silently attributed to whatever now occupies the slot. That is why a reused slot
never leaks into the observation that used the old handle, and it is the "missing
resource yields INCOMPLETE_ORACLE" rule.

Closure is then simply: an observation is closed when its reference resolves. The
lifetimes it could not resolve become its ``unavailable_dependencies`` - the field
P4.1 already uses to disqualify a record from semantic comparison.

It touches no process and never opens EU IV.
"""
from __future__ import annotations

from typing import Any, Iterable, NamedTuple, Sequence

#: The marker a missing resource yields, named exactly as the P4 gate names it.
INCOMPLETE_ORACLE = "incomplete-oracle"

#: The hook-declared context layout: ref_valid, ref_slot, ref_gen, live, slot_gen,
#: version.
CONTEXT_FIELDS = 6


class RegistryError(ValueError):
    """A capture's resource closure could not be verified."""


class Reference(NamedTuple):
    """The resource an observation addressed, and the slot state that resolves it."""

    valid: bool          # a resource reference was declared at all
    slot: int
    generation: int
    live: bool           # the referenced generation was live in that slot
    slot_generation: int # the slot's generation at that moment
    version: int         # the referenced lifetime's content version

    @property
    def resolved(self) -> bool:
        """Whether the reference names a resource the capture holds."""
        return self.valid and self.live and self.slot_generation == self.generation

    @property
    def named(self) -> str:
        """The lifetime the reference names, whether or not it resolves."""
        return f"slot:{self.slot}@{self.generation}"


def reference_from_context(context: Sequence[int]) -> Reference:
    """Decode the hook-declared observation context into a reference."""
    if len(context) != CONTEXT_FIELDS:
        raise RegistryError(
            f"an observation context is {CONTEXT_FIELDS} words, got {len(context)}")
    valid, slot, generation, live, slot_generation, version = (int(word) for word in context)
    return Reference(bool(valid), slot, generation, bool(live), slot_generation, version)


def lifetime_of(reference: Reference) -> str:
    """The lifetime a reference resolves to, or ``INCOMPLETE_ORACLE``."""
    return reference.named if reference.resolved else INCOMPLETE_ORACLE


def verdicts(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """One closure verdict per observation: its lifetime and any named dependency."""
    resolved: list[dict[str, Any]] = []
    for event in events:
        reference = reference_from_context(event["context"])
        resolved.append({
            "lifetime": lifetime_of(reference),
            "resolved": reference.resolved,
            # A reference that names a lifetime and cannot resolve it is an
            # unavailable dependency; an invalid reference names nothing.
            "unavailable_dependency": (reference.named
                                       if reference.valid and not reference.resolved else None),
        })
    return resolved


def verify_closure(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Verify the capture's resource closure and summarize it.

    Raises when an observation claims to be complete while addressing no live
    resource - the one direction that must never hold, since a complete observation
    is exactly one that operated on a resource the capture can replay.
    """
    lifetimes: set[str] = set()
    unavailable: set[str] = set()
    incomplete = 0
    unreferenced = 0

    for index, event in enumerate(events):
        reference = reference_from_context(event["context"])
        complete = bool(event["complete"])
        if complete and not reference.resolved:
            raise RegistryError(
                f"observation {index} is complete but its resource reference "
                f"({reference.named}) does not resolve")
        if reference.resolved:
            lifetimes.add(reference.named)
        elif reference.valid:
            unavailable.add(reference.named)
        else:
            unreferenced += 1
        if not complete:
            incomplete += 1

    return {
        "observations": len(events),
        "complete": len(events) - incomplete,
        "incomplete": incomplete,
        "unreferenced": unreferenced,
        "lifetimes": sorted(lifetimes),
        "unavailable_dependencies": sorted(unavailable),
        "incomplete_oracle": sorted(unavailable),
    }
