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


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _payload_ok(path: Path, declared_size, declared_sha) -> tuple[bool, str]:
    if not path.is_file():
        return False, f"payload is absent: {path.name}"
    if declared_size != path.stat().st_size:
        return False, f"payload size {path.stat().st_size} != {declared_size}"
    if declared_sha != _sha(path):
        return False, "payload sha256 does not match its declaration"
    return True, ""


def presentation_gates(workspace: Path, manifest: dict, policy: dict,
                       candidate_json: dict,
                       expected_extent: tuple[int, int] = (3456, 2234)
                       ) -> tuple[list[dict], bool, bool, dict]:
    """Validate the after-present checkpoint, drawable, and window evidence.

    Returns (gates, hard_fail, missing, scene_metrics). Similarity is recomputed
    from the recorded screenshot and drawable; a manifest score is not trusted.
    """
    from automation.live_gfx import similarity_to_drawable

    presentation = manifest.get("presentation") or {}
    candidate = manifest.get("candidate") or {}
    run = workspace / str(presentation.get("run") or "")
    occurrence = presentation.get("occurrence", 1)
    descriptor_path = run / f"checkpoint-after-present-{occurrence}.json"
    descriptor = _load_json(descriptor_path)
    gates: list[dict] = []
    hard_fail = False
    missing = False

    target = descriptor.get("target") or {}
    capture = descriptor.get("capture") or {}
    shown = descriptor.get("presentation") or {}
    gpu = descriptor.get("gpu") or {}
    width = target.get("width")
    height = target.get("height")
    raw = run / str(capture.get("raw_file") or "")
    payload_ok, payload_detail = _payload_ok(
        raw, capture.get("raw_bytes"), capture.get("raw_sha256")
    )
    identity_ok = (
        descriptor.get("schema") == "gfx-checkpoint/v1"
        and descriptor.get("checkpoint") == "after-present"
        and descriptor.get("occurrence") == occurrence
        and descriptor.get("executable_sha256") == manifest.get("executable_sha256")
        and descriptor.get("library_sha256")
        == (candidate_json.get("provenance") or {}).get("library_sha256")
        and descriptor.get("session")
        == (candidate_json.get("provenance") or {}).get("session")
        and (descriptor.get("caller") or {}).get("pid")
        == (candidate_json.get("provenance") or {}).get("pid")
    )
    if not descriptor:
        missing = True
    elif not identity_ok or not payload_ok:
        hard_fail = True
    gates.append(_gate(
        "presentation:checkpoint",
        bool(descriptor) and identity_ok and payload_ok,
        "identity/payload: " + (payload_detail or "ok"),
    ))

    drawable_ok = (
        bool(descriptor)
        and gpu.get("completed") is True
        and gpu.get("command_status") == 4
        and shown.get("shown") is True
        and shown.get("drawable_readback") is True
        and isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0
        and payload_ok
    )
    if not drawable_ok and not missing:
        hard_fail = hard_fail or (bool(descriptor) and not payload_ok)
    gates.append(_gate("presentation:drawable", drawable_ok,
                       f"extent={width}x{height} sha={capture.get('raw_sha256')}"))
    extent_ok = (width, height) == expected_extent
    gates.append(_gate("presentation:m4-extent", extent_ok,
                       f"M4 fixture extent {width}x{height} expected {expected_extent[0]}x{expected_extent[1]}"))
    if not extent_ok and bool(descriptor):
        hard_fail = True

    window = run / str(presentation.get("window_file") or "")
    window_ok = (
        window.is_file()
        and _sha(window) == presentation.get("window_sha256")
        and presentation.get("window_width") == width
        and presentation.get("window_height") == height
    )
    similarity = {"status": "unavailable", "reason": "not evaluated"}
    if window_ok and isinstance(width, int) and isinstance(height, int):
        similarity = similarity_to_drawable(window, raw, width, height)
    threshold = float(policy.get("presentation_similarity_max_mean_error", 2.0))
    similar_ok = (
        similarity.get("status") == "compared"
        and float(similarity.get("mean_abs_error", 999.0)) <= threshold
    )
    if window_ok and not similar_ok:
        if similarity.get("status") == "compared":
            hard_fail = True
        else:
            missing = True
    if not window_ok:
        missing = True
    gates.append(_gate(
        "presentation:visible",
        window_ok and similar_ok,
        f"recomputed mean_abs_error={similarity.get('mean_abs_error')} "
        f"threshold={threshold} similarity={similarity.get('status')}",
    ))

    scene: dict = {}
    try:
        from automation import gfx_compare  # type: ignore

        reference_json = json.loads(
            (workspace / str((manifest.get("reference") or {}).get(
                "artifacts", {}).get("gl-reference.json", {}).get("path") or ""))
            .read_text()
        )
        reference_path = workspace / str((manifest.get("reference") or {}).get(
            "artifacts", {}).get("gl-reference.json", {}).get("path") or "")
        gl_capture = gfx_compare.load_capture(reference_path.parent, "reference")
        scale_x = width // gl_capture.width if isinstance(width, int) else 0
        scale_y = height // gl_capture.height if isinstance(height, int) else 0
        if scale_x != scale_y or not 1 <= scale_x <= 4:
            raise ValueError("physical/logical extent is not an integer scale")
        physical = raw.read_bytes()
        rgba = bytearray()
        for y in range(gl_capture.height):
            for x in range(gl_capture.width):
                acc = [0, 0, 0, 0]
                for dy in range(scale_x):
                    base = ((y * scale_x + dy) * width + x * scale_x) * 4
                    for dx in range(scale_x):
                        i = base + dx * 4
                        for c in range(4):
                            acc[c] += physical[i + c]
                divisor = scale_x * scale_x
                rgba += bytes((acc[2] // divisor, acc[1] // divisor,
                               acc[0] // divisor, acc[3] // divisor))
        scene = gfx_compare._stats(
            gfx_compare._canonical_rgba(gl_capture), bytes(rgba),
            gl_capture.width, gl_capture.height, 0,
        )
        scene_ok = (
            scene["mean_abs_error"] <= float(policy.get("presentation_scene_max_mean_error", 2.0))
            and scene["max_channel_error"] <= int(
                policy.get("presentation_scene_max_channel_error", 64)
            )
        )
    except Exception as exc:  # noqa: BLE001
        scene = {"reason": str(exc)}
        scene_ok = False
    gates.append(_gate(
        "presentation:drawable-scene",
        scene_ok,
        f"reduced mean_abs_error={scene.get('mean_abs_error')} "
        f"max={scene.get('max_channel_error')}",
    ))
    if not scene_ok:
        missing = True
    return gates, hard_fail, missing, scene


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
        and (reference_json.get("pack") or {}).get("restored") is True
    )
    gates.append(_gate("reference:authentic", authentic, "schema/backend/provenance/draw/caps"))

    repeat_json_path = workspace / str(
        (repeat.get("artifacts") or {}).get("gl-reference.json", {}).get("path") or ""
    )
    repeat_json = _load_json(repeat_json_path)
    deterministic = (
        reference.get("raw_sha256") == repeat.get("raw_sha256")
        and reference.get("caller_rva") == repeat.get("caller_rva")
        and repeat_json.get("schema") == "gfx-gl-reference/v1"
        and repeat_json.get("backend") == "opengl"
        and (repeat_json.get("capture") or {}).get("raw_sha256") == repeat.get("raw_sha256")
        and (repeat_json.get("provenance") or {}).get("executable_sha256") == expected_exe
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

    candidate_capture = candidate_json.get("capture") or {}
    candidate_raw = candidate_json_path.parent / str(candidate_capture.get("raw_file") or "")
    candidate_payload_ok, candidate_payload_detail = _payload_ok(
        candidate_raw, candidate_capture.get("raw_bytes"), candidate_capture.get("raw_sha256")
    )
    metal = (
        candidate_json.get("schema") == "gfx-metal-logical/v1"
        and candidate_json.get("backend") == "metal"
        and (candidate_json.get("gpu") or {}).get("completed") is True
        and (candidate_json.get("provenance") or {}).get("executable_sha256") == expected_exe
        and (candidate_json.get("provenance") or {}).get("library_sha256") == manifest.get("library_sha256")
        and candidate_capture.get("raw_sha256") == candidate.get("raw_sha256")
        and (candidate_json.get("draw") or {}).get("effective_count") == candidate.get("effective_count")
        and candidate_payload_ok
    )
    gates.append(_gate("candidate:gpu", metal,
                       "Metal logical capture, payload and GPU completion: " + candidate_payload_detail))

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

    policy_path = workspace / str(expected_policy.get("path") or POLICY)
    policy_doc: dict = {}
    try:
        policy_doc = json.loads(policy_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        policy_doc = {}
    try:
        pres_gates, pres_hard, pres_missing, scene = presentation_gates(
            workspace, manifest, policy_doc, candidate_json
        )
    except Exception as exc:  # noqa: BLE001
        pres_gates = [_gate("presentation:checkpoint", False, str(exc))]
        pres_hard = False
        pres_missing = True
        scene = {}
    gates.extend(pres_gates)
    hard_fail = hard_fail or pres_hard
    inconclusive = inconclusive or pres_missing

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
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=workspace, capture_output=True, text=True, check=False,
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
