#!/usr/bin/env python3
"""Salvage frozen control-plane counters from an incomplete border submission run."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

SNAPSHOT_KEYS = (
    "border_loop_head_entries",
    "border_runtime_context_checked",
    "border_runtime_context_multidraw_supported",
    "border_semantic_evaluations",
    "border_semantic_decisions",
    "border_semantic_eliminations",
    "border_implementable_evaluations",
    "border_implementable_decisions",
    "border_implementable_eliminations",
    "border_structural_draws",
    "border_structural_eliminations",
    "border_loop_decode_failures",
    "border_loop_geometry_failures",
    "border_loop_resolution_failures",
    "border_roi_epoch_invalid",
    "border_thread_mismatch_count",
    "border_roi_freeze_partial_walk",
    "border_roi_freeze_semantic_suppress",
    "border_roi_freeze_implementable_suppress",
    "candidate_swaps",
    "border_multidraw_calls",
    "border_draw_calls_eliminated",
)


def salvage_partial_live(run_dir: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "benchmark"))
    sys.path.insert(0, str(ROOT / "analysis" / "tools"))
    import submission_control as sc
    import submission_border_validation as bv
    from border_loop_roi import eliminations_per_frame

    run_dir = run_dir.resolve()
    control_path = run_dir / "submission_control.bin"
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    snap = sc.read_snapshot(control_path)
    gates = bv.border_phase_roi_authoritative_gates(snap)
    swaps = int(snap.get("candidate_swaps", 0))
    excerpt = {key: int(snap.get(key, 0)) for key in SNAPSHOT_KEYS}
    provisional_roi = {
        "semantic_eliminations_per_frame": eliminations_per_frame(
            int(snap.get("border_semantic_eliminations", 0)), swaps
        ),
        "implementable_eliminations_per_frame": eliminations_per_frame(
            int(snap.get("border_implementable_eliminations", 0)), swaps
        ),
        "structural_eliminations_per_frame": eliminations_per_frame(
            int(snap.get("border_structural_eliminations", 0)), swaps
        ),
        "candidate_swaps": swaps,
        "note": "PARTIAL_LIVE_A2_DIAGNOSTIC — not registered primary Venice ROI estimate",
    }
    return {
        "evidence_kind": "PARTIAL_LIVE_A2_DIAGNOSTIC",
        "run": run_dir.name,
        "verified_code_commit": "3ae26023acb5f0e255c8d1182ab2438b47f7b74c",
        "derivation": {
            "control_source": str(control_path.relative_to(ROOT)) if control_path.is_relative_to(ROOT) else str(control_path),
            "manifest_source": str(manifest_path.relative_to(ROOT)) if manifest_path.is_relative_to(ROOT) else str(manifest_path),
        },
        "readiness": manifest.get("readiness"),
        "error": manifest.get("error"),
        "failure_stage": {"phase": "b2", "stage": "settle"},
        "snapshot_excerpt": excerpt,
        "border_roi_authoritative_gates": gates,
        "provisional_roi": provisional_roi,
        "interpretation": (
            "Frozen control mmap after incomplete run; authoritative gates on final bank. "
            "Live loop-head observer executed (detour, gateway, Gate 0, classifier path) even if eliminations are zero."
        ),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: partial_live_salvage.py <run_dir>", file=sys.stderr)
        return 2
    payload = salvage_partial_live(Path(sys.argv[1]))
    run_id = Path(sys.argv[1]).name.removesuffix("-submission-experiment")
    out = ROOT / "analysis" / "evidence" / f"partial-live-{run_id}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
