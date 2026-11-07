#!/usr/bin/env python3
"""A0.1 trusted-validation checker: frozen oracle vs workspace candidate."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO_CANDIDATES = list(Path(__file__).resolve().parents)


def trusted_root() -> Path:
    for parent in _REPO_CANDIDATES:
        if (parent / ".git").exists() and (parent / "automation").is_dir():
            return parent
    raise SystemExit("trusted repository not found")


ROOT = trusted_root()
sys.path.insert(0, str(ROOT))

from automation.evidence import extract_primitive_table, lookup_from_table  # noqa: E402
from automation.replay_primitive import (  # noqa: E402
    REL_CANDIDATE,
    evaluate_candidate_against_evidence,
    evaluate_promoted_regressions,
    resolve_regression_root,
)
from automation.verification_manifest import (  # noqa: E402
    EU4_EXECUTABLE_SHA256,
    digest_manifest,
    load_manifest,
    verify_manifest,
)

DEFAULT_ORACLE = ROOT / "analysis" / "qualification" / "v0"
DEFAULT_EXE = Path(
    "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4"
)


def emit(verdict: str, diagnostics: str) -> None:
    print(json.dumps({"verdict": verdict, "diagnostics": diagnostics}))


def resolve_oracle_and_manifest() -> tuple[Path, Path]:
    """Prefer campaign-pinned version manifest; else tip under oracle dir."""
    pinned = os.environ.get("EU4_QUALIFICATION_MANIFEST")
    oracle_env = os.environ.get("EU4_QUALIFICATION_ORACLE")
    oracle = Path(oracle_env) if oracle_env else DEFAULT_ORACLE
    if not oracle.is_absolute():
        oracle = ROOT / oracle
    if pinned:
        path = Path(pinned)
        if not path.is_absolute():
            path = ROOT / path
        # version-dir pin: .../oracle_versions/<id>/verification-manifest.json
        if path.parent.parent.name == "oracle_versions":
            oracle = path.parent.parent.parent
        return oracle, path
    return oracle, oracle / "verification-manifest.json"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        emit("unavailable", "usage: accept.py <workspace>")
        return 0
    workspace = Path(argv[1]).resolve()
    fixture_mode = os.environ.get("EU4_A01_FIXTURE_MODE") == "1"

    oracle, manifest_path = resolve_oracle_and_manifest()
    evidence_path = oracle / "primitive-table.evidence.json"

    if not manifest_path.is_file() or not evidence_path.is_file():
        emit(
            "unavailable",
            "frozen oracle missing; run: python3 -m automation.promote_evidence",
        )
        return 0
    if not fixture_mode and not DEFAULT_EXE.is_file():
        emit(
            "unavailable",
            f"EU4 executable not found at {DEFAULT_EXE}; "
            "instruction_static promotion requires the pinned install",
        )
        return 0

    manifest = load_manifest(manifest_path)
    # Fixture/temp oracles may use absolute member paths; try oracle then ROOT.
    errors = verify_manifest(oracle, manifest)
    if errors:
        errors = verify_manifest(ROOT, manifest)
    if errors:
        emit(
            "unavailable",
            "verification manifest integrity failure: " + "; ".join(errors),
        )
        return 0
    if manifest.get("eu4_executable_sha256") != EU4_EXECUTABLE_SHA256:
        emit("unavailable", "manifest eu4_executable_sha256 does not match pin")
        return 0
    if "repo_baseline_commit" not in manifest:
        emit("unavailable", "manifest missing repo_baseline_commit")
        return 0

    version_name = manifest.get("oracle_version_name")
    if version_name and version_name not in (None, "v0"):
        reg_root = resolve_regression_root(oracle, manifest)
        listed = [
            m for m in (manifest.get("members") or []) if m.get("role") == "regression"
        ]
        if listed and reg_root is None:
            emit(
                "unavailable",
                f"oracle version {version_name} regressions directory missing",
            )
            return 0

    frozen = json.loads(evidence_path.read_text(encoding="utf-8"))
    if frozen.get("provenance") != "instruction_static":
        emit("fail", "frozen evidence provenance must be instruction_static")
        return 0

    if not fixture_mode:
        try:
            live = extract_primitive_table(DEFAULT_EXE)
        except ValueError as exc:
            emit("unavailable", f"live extraction failed: {exc}")
            return 0
        if live["output_hash"] != frozen.get("output_hash"):
            emit(
                "unavailable",
                "live extraction output_hash differs from frozen evidence "
                "(oracle drift or wrong executable)",
            )
            return 0

    candidate_path = workspace / REL_CANDIDATE
    if not candidate_path.is_file():
        emit("fail", f"missing workspace candidate: {REL_CANDIDATE}")
        return 0
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

    err = evaluate_candidate_against_evidence(
        candidate, frozen, check_out_of_range_field=False
    )
    if err:
        emit(
            "fail",
            f"{err}; oracle_manifest_sha256={digest_manifest(manifest)}",
        )
        return 0

    reg_err = evaluate_promoted_regressions(
        ROOT, oracle, candidate, frozen, manifest=manifest
    )
    if reg_err:
        if reg_err.startswith("unavailable:"):
            emit("unavailable", reg_err.removeprefix("unavailable:").strip())
            return 0
        emit(
            "fail",
            f"{reg_err}; oracle_manifest_sha256={digest_manifest(manifest)}",
        )
        return 0

    table = candidate["table"]
    if lookup_from_table(table, 99) != 4:
        emit("fail", "candidate out-of-range mapping via table length must return 4")
        return 0

    emit(
        "pass",
        "workspace candidate matches frozen instruction_static oracle (controlled suite); "
        f"manifest_sha256={digest_manifest(manifest)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
