#!/usr/bin/env python3
"""Independent M7 exact-prefix acceptance checker.

    python3 accept.py [--target-end N] <workspace>

The default target is event 20.  A campaign may select a smaller successive
prefix (for example ``--target-end 3``); later observations remain diagnostics
and do not expand the acceptance requirement.

The checker lives in the trusted infrastructure checkout and imports the
trusted ``automation`` modules from there, never from the candidate.  It reads
the sealed original-EU4 frame and the in-process Metal replay under

    <workspace>/.local/m7/event20/live/frame
    <workspace>/.local/m7/event20/live/shadow

and recomputes every exact-prefix comparison from the original pixel bytes.
A model statement, a matching final image, or a missing intermediate checkpoint
cannot pass.

Supported verdicts: ``pass`` only for an independently verified exact prefix
through the requested target from an authentic sealed frame; ``fail`` for a
verified first divergence; ``inconclusive`` when the evidence is missing,
incomplete, unsupported or unproven.  ``EVENT20_EXACT`` is a campaign name
derived here from the generic ``EXACT_PREFIX`` result; the common checker has no
event-20 constant.
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

DEFAULT_TARGET_END = 20


def _target_end(argv: list[str]) -> tuple[int, list[str]]:
    """Read the optional ``--target-end`` before the workspace argument."""
    target = DEFAULT_TARGET_END
    rest = list(argv[1:])
    if rest[:1] == ["--target-end"]:
        if len(rest) < 2:
            raise ValueError("--target-end needs an event number")
        target = int(rest[1])
        rest = rest[2:]
    if not 0 <= target < 4096:
        raise ValueError("target event is out of range")
    return target, rest


def _selected_sidecar() -> Path | None:
    """The campaign sidecar explicitly bound by the trusted caller, if any."""
    raw = os.environ.get("EU4_M7_CANDIDATE_SIDECAR", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _trusted_root(workspace: Path) -> Path:
    selected = _selected_sidecar()
    if selected is not None:
        # An explicitly selected campaign owns its reference.  An ambient
        # trusted-root override must not redirect it.
        sidecar = json.loads(selected.read_text())
        configured = sidecar.get("trusted_reference_root")
    else:
        declared = os.environ.get("EU4_M7_EVENT20_TRUSTED_ROOT", "").strip()
        if declared:
            configured = declared
        else:
            sidecar_path = ROOT / "dsh" / "campaigns" / "m7-event20.json"
            sidecar = json.loads(sidecar_path.read_text())
            configured = sidecar.get("trusted_reference_root")
    return gfx_prefix_check.resolve_trusted_reference(
        configured, workspace, root=ROOT)


def _emit(verdict: str, diagnostics: str, result: dict | None = None) -> int:
    payload = {
        "schema": "m7-event20-acceptance/v1",
        "verdict": verdict,
        "diagnostics": diagnostics,
    }
    if result is not None:
        payload["result"] = result
    print(json.dumps(payload, indent=2))
    return 0 if verdict == "pass" else 1


def main(argv: list[str]) -> int:
    try:
        target_end, positional = _target_end(argv)
    except ValueError as exc:
        return _emit("inconclusive", f"invalid target: {exc}")
    if not positional:
        return _emit("inconclusive",
                     "usage: accept.py [--target-end N] <workspace>")
    workspace = Path(positional[0]).expanduser().resolve()
    try:
        trusted_root = _trusted_root(workspace)
    except (gfx_prefix_check.PrefixCheckError, OSError, ValueError,
            json.JSONDecodeError) as exc:
        return _emit(
            "inconclusive",
            f"trusted reference root is not usable: {exc}")
    try:
        trusted_root.relative_to(workspace)
    except ValueError:
        pass
    else:
        return _emit(
            "inconclusive",
            "trusted evidence root is inside the candidate workspace: "
            f"{trusted_root}")
    record_path = trusted_root / "trusted-evidence.json"
    try:
        record = json.loads(record_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return _emit(
            "inconclusive",
            f"digest-bound trusted evidence record is unreadable: {exc}")
    frame_dir = Path(str(record.get("frame_path") or
                           (trusted_root / "frame"))).expanduser().resolve()
    if not frame_dir.is_dir():
        return _emit(
            "inconclusive",
            "digest-bound trusted evidence root has no sealed frame: "
            f"{frame_dir}")
    sidecar = _selected_sidecar() or (
        ROOT / "dsh" / "campaigns" / "m7-event20.json").resolve()
    run_dir = os.environ.get("EU4_M7_CHECKER_RUN_DIR", "").strip() or None
    try:
        result = gfx_prefix_check.evaluate(
            frame_dir, None, require_end=target_end, trusted_execution=True,
            trusted_root=trusted_root, candidate_workspace=workspace,
            candidate_sidecar=sidecar, trusted_run_dir=run_dir)
    except (gfx_prefix_check.PrefixCheckError, OSError, ValueError) as exc:
        return _emit("inconclusive", f"prefix checker could not evaluate: {exc}")
    generic = result.get("verdict")
    authority = (result.get("evidence") or {}).get("authority")
    exact_prefix = result.get("verified_prefix") or {}
    if (result.get("qualified") is True
            and authority == "authentic"
            and int(exact_prefix.get("end", -1)) >= target_end):
        name = ("EVENT20_EXACT" if target_end == DEFAULT_TARGET_END
                else f"EXACT_PREFIX_{target_end}")
        result = dict(result, campaign_verdict=name)
        return _emit(
            "pass",
            f"independent exact-prefix comparison verified events 0 through "
            f"{target_end} from the authentic sealed frame; every compared "
            "output is independently attributable",
            result)
    if generic == "UNSUPPORTED":
        return _emit(
            "inconclusive",
            "the prefix contains unsupported auxiliary observations: "
            + ", ".join(str(event) for event in
                        result.get("excluded_events") or []),
            result)
    if generic == "EXACT_PREFIX" and result.get("qualified") is not True:
        return _emit(
            "inconclusive",
            "the exact pixel match is not independently attributable: "
            + "; ".join(result.get("evidence_limitations") or
                        ["shared renderer input/output payload"]),
            result)
    if generic == "EXACT_PREFIX" and authority != "authentic":
        return _emit(
            "inconclusive",
            f"the exact prefix is {authority}, not an authentic sealed frame; "
            "it qualifies the mechanism but not original-frame equivalence",
            result)
    if generic == "FIRST_DIVERGENCE":
        event = result.get("first_divergence")
        return _emit(
            "fail",
            f"earliest verified divergence is event {event}; "
            "the verified exact prefix stops at the event before it, and "
            "remediation continues from that divergence",
            result)
    return _emit(
        "inconclusive",
        f"exact event-{target_end} qualification is not established: "
        + "; ".join(result.get("problems") or [generic or "unknown"]),
        result)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
