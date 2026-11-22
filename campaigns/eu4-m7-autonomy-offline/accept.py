#!/usr/bin/env python3
"""Independent offline autonomy-fixture acceptance.

    python3 accept.py <candidate-workspace>

The fixture is a derived, explicitly labelled event subset (original events
10, 15, 17) with a trusted copied reference outside the candidate workspace.
The production trusted candidate-execution path builds and runs the candidate
renderer and recomputes every comparison.  A pass requires the exact prefix
0..2 with no excluded or invalid event.

The canonical event-20 checker is not relaxed: this checker only owns the
offline smoke fixture and reports its narrower scope explicitly.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_prefix_check  # noqa: E402

TRUSTED_ROOT = ROOT / ".local" / "m7" / "autonomy-offline" / "trusted" / "offline-b"
EXPECTED_END = 2
SIDECAR = ROOT / "dsh" / "campaigns" / "m7-autonomy-offline.json"


def _emit(verdict: str, diagnostics: str, result: dict | None = None) -> int:
    payload = {
        "schema": "m7-autonomy-offline-acceptance/v1",
        "verdict": verdict,
        "diagnostics": diagnostics,
        "scope": {"start": 0, "end": EXPECTED_END,
                  "fixture": "derived original events 10,15,17"},
    }
    if result is not None:
        payload["result"] = result
    print(json.dumps(payload, indent=2))
    return 0 if verdict == "pass" else 1


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return _emit("inconclusive", "usage: accept.py <workspace>")
    workspace = Path(argv[1]).expanduser().resolve()
    frame = TRUSTED_ROOT / "frame"
    if not frame.is_dir():
        return _emit("inconclusive",
                     f"trusted offline fixture is absent: {frame}")
    run_dir = os.environ.get("EU4_M7_AUTONOMY_RUN_DIR", "").strip() or None
    try:
        result = gfx_prefix_check.evaluate(
            frame, None, require_end=EXPECTED_END, trusted_execution=True,
            trusted_root=TRUSTED_ROOT, candidate_workspace=workspace,
            candidate_sidecar=SIDECAR, trusted_run_dir=run_dir)
    except (gfx_prefix_check.PrefixCheckError, OSError, ValueError) as exc:
        return _emit("inconclusive",
                     f"offline checker could not evaluate: {exc}")
    events = result.get("events") or []
    authority = (result.get("evidence") or {}).get("authority")
    exact = (
        result.get("qualified") is True
        and result.get("verified_prefix") == {
            "start": 0, "end": EXPECTED_END}
        and not result.get("excluded_events")
        and len(events) == EXPECTED_END + 1
        and all(entry.get("status") == "match" for entry in events)
        and authority in ("derived", "synthetic")
    )
    if exact:
        return _emit(
            "pass",
            f"independent trusted candidate execution reproduced the exact "
            f"{authority} offline prefix 0..2 (mechanism qualification only; "
            "not original-frame equivalence)",
            result)
    if (result.get("verdict") == "EXACT_PREFIX"
            and result.get("qualified") is not True):
        return _emit(
            "inconclusive",
            "the offline pixel match is not independently attributable: "
            + "; ".join(result.get("evidence_limitations") or
                        ["shared renderer input/output payload"]),
            result)
    first = result.get("first_divergence")
    if first is not None:
        return _emit(
            "fail",
            f"offline fixture diverges first at event {first}; correct the "
            "candidate renderer and revalidate",
            result)
    return _emit(
        "inconclusive",
        "offline fixture exactness is not established: "
        + "; ".join(result.get("problems") or ["unknown"]),
        result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
