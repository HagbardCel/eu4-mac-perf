#!/usr/bin/env python3
"""Fail-closed M4 acceptance checker.

    python3 accept.py <workspace>

Prints one JSON object. `pass` requires the authentic original-OpenGL
reference, the corresponding live Metal logical capture, an exact fixed-policy
comparison, GPU-completed drawable presentation, independent visible-window
evidence, offline regressions, and provenance consistency. Any missing gate is
`inconclusive`; a mismatched gate is `fail`. Neither is M4.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

MANIFEST = "analysis/m4/evidence-manifest.json"
POLICY = "analysis/m4/comparison-policy.json"
PIPELINE_FILES = (
    "automation/gfx_compare.py",
    "benchmark/eu4_gfx_observe_framebuffer.c",
    "benchmark/eu4_gfx_observe_draw.c",
    "benchmark/eu4_gfx_observe_framebuffer.h",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gate(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _load_manifest(workspace: Path) -> dict:
    path = workspace / MANIFEST
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "verdict": "unavailable",
            "diagnostics": f"evidence manifest is unreadable: {exc}",
        }))
        raise SystemExit(0) from exc
    if value.get("schema") != "eu4-m4-evidence/v1":
        print(json.dumps({"verdict": "unavailable",
                          "diagnostics": "unexpected manifest schema"}))
        raise SystemExit(0)
    return value


def _artifact_gate(workspace: Path, block: dict) -> dict:
    missing: list[str] = []
    mismatched: list[str] = []
    artifacts = block.get("artifacts") or {}
    for name, entry in artifacts.items():
        if not isinstance(entry, dict) or not entry.get("sha256"):
            continue
        path = workspace / str(entry.get("path") or "")
        if not path.is_file():
            missing.append(name)
            continue
        if path.stat().st_size != entry.get("bytes") or _sha(path) != entry.get("sha256"):
            mismatched.append(name)
        if missing or mismatched:
            break
    return _gate(
        "artifacts",
        not missing and not mismatched,
        f"missing={missing} mismatched={mismatched}",
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"verdict": "unavailable", "diagnostics": "usage: accept.py <workspace>"}))
        return 2
    workspace = Path(argv[1]).resolve()
    manifest = _load_manifest(workspace)
    campaign_path = Path(__file__).resolve().parent / "campaign.json"
    try:
        campaign = json.loads(campaign_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        campaign = {}
    pinned = campaign.get("evidence_manifest_sha256")
    actual = _sha(workspace / MANIFEST)
    if not pinned or pinned != actual:
        print(json.dumps({
            "verdict": "unavailable",
            "diagnostics": "evidence manifest digest does not match the campaign pin",
            "expected": pinned,
            "actual": actual,
        }))
        return 0
    gates: list[dict] = []
    hard_fail = False
    inconclusive = False

    for role in ("reference", "reference_repeat", "candidate", "comparison"):
        block = manifest.get(role) or {}
        gate = _artifact_gate(workspace, block)
        gate["name"] = f"artifacts:{role}"
        gates.append(gate)
        if not gate["ok"]:
            inconclusive = True

    reference = manifest.get("reference") or {}
    repeat = manifest.get("reference_repeat") or {}
    candidate = manifest.get("candidate") or {}
    comparison = manifest.get("comparison") or {}
    presentation = manifest.get("presentation") or {}
    expected_exe = manifest.get("executable_sha256")
    expected_policy = manifest.get("policy") or {}

    reference_json_path = workspace / str((reference.get("artifacts") or {}).get("gl-reference.json", {}).get("path") or "")
    candidate_json_path = workspace / str((candidate.get("artifacts") or {}).get("metal-logical.json", {}).get("path") or "")
    try:
        reference_json = json.loads(reference_json_path.read_text())
        candidate_json = json.loads(candidate_json_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        reference_json = {}
        candidate_json = {}

    authentic = (
        reference_json.get("schema") == "gfx-gl-reference/v1"
        and reference_json.get("backend") == "opengl"
        and (reference_json.get("provenance") or {}).get("executable_sha256") == expected_exe
        and (reference_json.get("draw") or {}).get("caller_rva") == reference.get("caller_rva")
        and (reference_json.get("draw") or {}).get("mode") == 5
        and (reference_json.get("draw") or {}).get("effective_count", 0) >= 3
        and (reference_json.get("gpu") or {}).get("gl_error") == 0
        and (reference_json.get("gpu") or {}).get("read_pixels") is True
        and (reference_json.get("caps") or {}).get("byte4") == reference.get("caps_byte4")
        and (reference_json.get("capture") or {}).get("raw_sha256") == reference.get("raw_sha256")
        and (reference_json.get("shader") or {}).get("glsl_sha256") == reference.get("glsl_sha256")
    )
    gates.append(_gate("reference:authentic", authentic, "schema/backend/provenance/draw/caps"))

    deterministic = (
        reference.get("raw_sha256") == repeat.get("raw_sha256")
        and reference.get("caller_rva") == repeat.get("caller_rva")
    )
    gates.append(_gate("reference:deterministic", deterministic, "independent launches agree"))

    operations_path = workspace / str(
        (reference.get("artifacts") or {}).get("operations-summary.json", {}).get("path") or ""
    )
    try:
        operations = json.loads(operations_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        operations = {}
    envelope = (
        operations.get("integrity_ok") is True
        and operations.get("target_draw_qualified") is True
        and operations.get("target_draw_caller") is not None
    )
    gates.append(_gate("reference:authentic-envelope", envelope, "observer integrity and target draw"))

    metal = (
        candidate_json.get("schema") == "gfx-metal-logical/v1"
        and candidate_json.get("backend") == "metal"
        and (candidate_json.get("gpu") or {}).get("completed") is True
        and (candidate_json.get("provenance") or {}).get("executable_sha256") == expected_exe
        and (candidate_json.get("capture") or {}).get("raw_sha256") == candidate.get("raw_sha256")
        and (candidate_json.get("draw") or {}).get("effective_count") == candidate.get("effective_count")
    )
    gates.append(_gate("candidate:gpu", metal, "Metal logical capture and GPU completion"))

    policy_path = workspace / str(expected_policy.get("path") or POLICY)
    if policy_path.is_file() and _sha(policy_path) != expected_policy.get("sha256"):
        hard_fail = True
        gates.append(_gate("policy:identity", False, "policy digest drifted"))
    else:
        gates.append(_gate("policy:identity", policy_path.is_file(), "fixed policy present"))

    sys.path.insert(0, str(workspace))
    try:
        from automation import gfx_compare  # type: ignore

        policy = gfx_compare.load_policy(policy_path)
        ref_capture = gfx_compare.load_capture(
            reference_json_path.parent, "reference"
        )
        cand_capture = gfx_compare.load_capture(candidate_json_path, "candidate")
        report = gfx_compare.compare(ref_capture, cand_capture, policy)
    except Exception as exc:  # noqa: BLE001
        report = {"status": "unavailable", "reasons": [str(exc)]}
    compare_ok = report.get("status") == "pass"
    if report.get("status") in ("fail", "invalid"):
        hard_fail = True
    elif not compare_ok:
        inconclusive = True
    gates.append(
        _gate(
            "comparison:fixed-policy",
            compare_ok,
            f"status={report.get('status')} reasons={report.get('reasons')}",
        )
    )
    samples = report.get("samples") or {}
    if compare_ok and (
        samples.get("max_channel_error") != comparison.get("max_channel_error")
        or samples.get("mismatched_pixels") != comparison.get("mismatched_pixels")
    ):
        gates.append(
            _gate(
                "comparison:report-identity",
                False,
                "fresh comparison does not reproduce the recorded statistics",
            )
        )
        hard_fail = True
    else:
        gates.append(_gate("comparison:report-identity", compare_ok, "recorded and fresh statistics agree"))

    presentation_ok = (
        presentation.get("shown") is True
        and presentation.get("drawable_readback") is True
        and bool(presentation.get("drawable_sha256"))
    )
    gates.append(_gate("presentation:drawable", presentation_ok, "drawable readback and completion"))
    if not presentation_ok:
        inconclusive = True

    visible = presentation.get("visible") is True and presentation.get("window_captured") == "captured"
    gates.append(
        _gate(
            "presentation:visible",
            visible,
            f"window_captured={presentation.get('window_captured')} reason={presentation.get('reason')}",
        )
    )
    if not visible:
        inconclusive = True

    try:
        tests = subprocess.run(
            [
                sys.executable, "-m", "unittest",
                "tests.test_gfx_compare", "tests.test_gl_reference", "tests.test_live_gfx_probe",
            ],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        regressions_ok = tests.returncode == 0
        regression_detail = (tests.stderr or tests.stdout).strip()[-400:]
    except (OSError, subprocess.SubprocessError) as exc:
        regressions_ok = False
        regression_detail = str(exc)
    gates.append(_gate("regressions:offline", regressions_ok, regression_detail))
    if not regressions_ok:
        inconclusive = True

    tree = subprocess.run(
        ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True, check=False
    ).stdout.strip()
    provenance_ok = bool(expected_exe) and bool(candidate.get("vertex_hex_sha256")) and not tree
    gates.append(_gate("provenance:source", provenance_ok, "clean tree and recorded identities"))
    if not provenance_ok:
        inconclusive = True

    if hard_fail:
        verdict = "fail"
        diagnostics = "M4 evidence mismatch: " + "; ".join(
            gate["detail"] for gate in gates if not gate["ok"]
        )
    elif inconclusive or not all(gate["ok"] for gate in gates):
        verdict = "inconclusive"
        missing = [gate["name"] for gate in gates if not gate["ok"]]
        diagnostics = "M4 evidence incomplete: " + ", ".join(missing)
    else:
        verdict = "pass"
        diagnostics = "all M4 gates satisfied"

    result = {
        "verdict": verdict,
        "diagnostics": diagnostics,
        "gates": gates,
        "policy": expected_policy.get("path"),
        "reference": {
            "raw_sha256": reference.get("raw_sha256"),
            "vertex_sha256": reference.get("vertex_sha256"),
            "caller_rva": reference.get("caller_rva"),
        },
        "candidate": {
            "raw_sha256": candidate.get("raw_sha256"),
            "vertex_hex_sha256": candidate.get("vertex_hex_sha256"),
        },
        "comparison": {
            "status": report.get("status"),
            "max_channel_error": samples.get("max_channel_error"),
            "mismatched_pixels": samples.get("mismatched_pixels"),
            "pixels": samples.get("pixels"),
        },
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
