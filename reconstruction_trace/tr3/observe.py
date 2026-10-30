"""One-shot selection and dropped-event accounting for one `_GfxSetConstantBuffer` call.

A native QBDI producer is a later step. This module does not intercept the
function or read an instruction stream. Synthetic observations stay labeled
`handwritten-synthetic`.
"""
from __future__ import annotations

import copy


def select_one(candidates):
    """Return the first candidate. Later candidates are not examined."""
    return candidates[0]


def apply_accounting(trace, *, dropped_instructions=0, dropped_accesses=0,
                     dropped_transfers=0):
    """Derive incompleteness from dropped-event counts.

    The caller passes a trace whose completeness is still ``complete``. A
    nonzero count is the failure. A pre-labeled incomplete trace is not the
    evidence.
    """
    updated = copy.deepcopy(trace)
    if dropped_instructions or dropped_accesses or dropped_transfers:
        updated["completeness"] = "incomplete"
        updated["incomplete_reasons"] = [
            "events dropped: "
            f"{dropped_instructions} instructions, "
            f"{dropped_accesses} accesses, "
            f"{dropped_transfers} transfers"]
    return updated
