#!/usr/bin/env python3
"""Build a compact reviewable capsule from a local submission-experiment run directory."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmark"))

import submission_border_validation as bv  # noqa: E402

SCHEMA_V6_CENSUS_KEYS = (
    "border_mode0_entries",
    "border_mode1_entries",
    "border_mode_other_entries",
    "border_mode0_visible_entries",
    "border_mode1_visible_entries",
    "border_mode_other_visible_entries",
    "border_mode0_visible_nonzero_triangle_entries",
    "border_mode1_visible_nonzero_triangle_entries",
    "border_mode_other_visible_nonzero_triangle_entries",
)

COUNTER_KEYS = (
    "border_loop_head_entries",
    "candidate_swaps",
    *SCHEMA_V6_CENSUS_KEYS,
    "border_semantic_evaluations",
    "border_semantic_decisions",
    "border_semantic_eliminations",
    "border_implementable_evaluations",
    "border_implementable_decisions",
    "border_implementable_eliminations",
    "border_structural_draws",
    "border_structural_eliminations",
    "border_structural_walks",
    "border_runtime_context_checked",
    "border_runtime_context_multidraw_supported",
    "border_loop_decode_failures",
    "border_loop_geometry_failures",
    "border_loop_resolution_failures",
    "border_roi_epoch_invalid",
    "border_thread_mismatch_count",
    "border_roi_freeze_partial_walk",
)

HISTOGRAM_KEYS = bv.SEMANTIC_RUN_LEN_HISTOGRAM_KEYS + bv.IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _phase_capsule(
    phase: dict,
    *,
    offline_verified_code_commit: str | None,
    run_git_commit: str | None,
) -> dict:
    end = (phase.get("control_validation") or {}).get("end") or {}
    schema = int(end.get("counter_schema_version", 0))
    counters = {k: int(end[k]) for k in COUNTER_KEYS if k in end}
    hist = {k: int(end.get(k, 0)) for k in HISTOGRAM_KEYS if k in end}
    out: dict[str, Any] = {
        "phase": phase.get("name"),
        "counter_schema_version": schema,
        "run_git_commit": run_git_commit,
        "offline_verified_code_commit": offline_verified_code_commit,
        "counters": counters,
        "histogram_sums": {
            "semantic": sum(hist.get(k, 0) for k in bv.SEMANTIC_RUN_LEN_HISTOGRAM_KEYS),
            "implementable": sum(hist.get(k, 0) for k in bv.IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS),
        },
        "border_gate0": bv.border_gate0_from_snapshot(end),
        "border_roi_validity_gate": bv.border_roi_validity_gate(end),
        "border_roi_authoritative_gates": bv.border_phase_roi_authoritative_gates(end),
    }
    if schema == 6:
        out["border_roi_domain_coverage_gate"] = bv.border_roi_domain_coverage_gate(end)
    if phase.get("passive_detour_liveness"):
        out["passive_detour_liveness"] = phase["passive_detour_liveness"]
    return out


def build_capsule(
    run_dir: Path,
    *,
    evidence_kind: str,
    registered_provenance_commit: str | None = None,
    extra: dict | None = None,
) -> dict:
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    git = manifest.get("git")
    run_git_commit = (git or {}).get("commit") if git else None
    offline_verified = registered_provenance_commit
    if offline_verified is None and git:
        offline_verified = git.get("commit")

    phases_out = []
    for phase in manifest.get("phases") or []:
        phases_out.append(
            _phase_capsule(
                phase,
                offline_verified_code_commit=offline_verified,
                run_git_commit=run_git_commit,
            )
        )

    capsule: dict[str, Any] = {
        "evidence_kind": evidence_kind,
        "run": run_dir.name,
        "experiment": manifest.get("experiment"),
        "manifest_status": manifest.get("status"),
        "run_git_commit": run_git_commit,
        "offline_verified_code_commit": offline_verified,
        "offline_verified_code_commit_source": (
            "registered_provenance" if registered_provenance_commit else ("manifest_git" if git else None)
        ),
        "source_hashes": {
            "manifest.json": _sha256(manifest_path),
            "events.jsonl": _sha256(run_dir / "events.jsonl") if (run_dir / "events.jsonl").is_file() else None,
            "submission_control.bin": _sha256(run_dir / "submission_control.bin")
            if (run_dir / "submission_control.bin").is_file()
            else None,
        },
        "phases": phases_out,
    }
    if manifest.get("border_domain_coverage_gate"):
        capsule["border_domain_coverage_gate"] = manifest["border_domain_coverage_gate"]
    if extra:
        capsule.update(extra)
    return capsule


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence-kind", default="border_run_capsule")
    parser.add_argument("--registered-commit", default=None)
    args = parser.parse_args()
    run_dir = args.run_dir.expanduser().resolve()
    capsule = build_capsule(
        run_dir,
        evidence_kind=args.evidence_kind,
        registered_provenance_commit=args.registered_commit,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(capsule, indent=2) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
