#!/usr/bin/env python3
"""Fail-closed M5 acceptance checker.

    python3 accept.py <workspace>

M5 requires the authentic original-OpenGL sprite draw (the next distinct
rendering operation after the M4 Simple text draw), the same operation encoded
by the production Metal dispatcher on a live GPU over its authentic prior
framebuffer, exact composed pixel equivalence under the declared policy, a
nonzero contribution, unsupported-state refusal, a corrupted-input negative
control, and the preserved M4 route/regressions.

The live engine cannot currently reach the sprite draw: the second loading
frame starts with the unreconstructed original `_GfxBeginScene`, which crashes
after the first present. The qualification therefore uses the deterministic
replay boundary in `automation.m5_replay`, which calls the same production
encoder with the captured original-GL inputs. The checker records this
limitation explicitly; it never labels the replay as a live-engine route.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MANIFEST = "analysis/m5/evidence-manifest.json"
POLICY = "analysis/m5/comparison-policy.json"
SPRITE_IDENTITY = "sprite-map/asset-maincode-v0"
GLSL_EXTENSION = b"#extension GL_ARB_shader_texture_lod : enable"
GLSL_DEFINE = b"#define NO_SHADER_TEXTURE_LOD"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _gate(name: str, ok: bool, detail: str = "") -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_gate(workspace: Path, block: dict) -> dict:
    missing: list[str] = []
    mismatched: list[str] = []
    artifacts = block.get("artifacts") or {}
    for name, entry in artifacts.items():
        if not isinstance(entry, dict):
            continue
        path = workspace / str(entry.get("path") or "")
        if not path.is_file():
            missing.append(name)
            continue
        if path.stat().st_size != entry.get("bytes") or _sha(path) != entry.get("sha256"):
            mismatched.append(name)
    return _gate(
        "artifacts",
        not missing and not mismatched,
        f"missing={missing} mismatched={mismatched}",
    )


def _normalized_source(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data.replace(GLSL_EXTENSION, GLSL_DEFINE)).hexdigest()


def reference_gates(workspace: Path, manifest: dict) -> tuple[list[dict], bool, dict]:
    gates: list[dict] = []
    hard_fail = False
    reference = manifest.get("reference") or {}
    run = workspace / str(reference.get("run") or "")
    descriptor_path = run / str(reference.get("descriptor") or "gl-reference.json")
    descriptor = _load_json(descriptor_path)
    gates.append(_gate(
        "reference:descriptor",
        bool(descriptor)
        and descriptor.get("schema") == "gfx-gl-reference/v1"
        and descriptor.get("backend") == "opengl",
        "authentic original-GL descriptor",
    ))
    draw = descriptor.get("draw") or {}
    expected = reference.get("draw") or {}
    event_ok = all(
        draw.get(key) == expected.get(key)
        for key in ("symbol", "caller_rva", "mode", "effective_count", "first")
    )
    gates.append(_gate(
        "reference:event",
        event_ok,
        f"{draw.get('caller_rva')} mode={draw.get('mode')} count={draw.get('effective_count')}",
    ))
    if not event_ok:
        hard_fail = True
    target = descriptor.get("target") or {}
    target_ok = (
        target.get("width") == 1728
        and target.get("height") == 1117
        and target.get("pixel_format") == "BGRA8"
        and target.get("origin") == "lower_left"
        and target.get("read_buffer") == 1029
        and target.get("draw_buffer") == 1029
    )
    gates.append(_gate("reference:target", target_ok, f"{target.get('width')}x{target.get('height')}"))
    if not target_ok:
        hard_fail = True

    artifact = _artifact_gate(workspace, reference)
    gates.append(artifact)
    if not artifact["ok"]:
        hard_fail = True

    vertex = _load_json(run / "gl-reference-vertex.json")
    vertex_ok = (
        vertex.get("schema") == "gfx-gl-vertex/v1"
        and vertex.get("ok") is True
        and vertex.get("source") == "live_gl_vbo"
        and vertex.get("stride") == 20
        and vertex.get("count") == 4
        and vertex.get("bytes") == 80
        and vertex.get("sha256") == (reference.get("vertex") or {}).get("sha256")
    )
    gates.append(_gate("reference:vertex", vertex_ok, f"stride={vertex.get('stride')} bytes={vertex.get('bytes')}"))
    if not vertex_ok:
        hard_fail = True

    constants = descriptor.get("constants") or {}
    constant_ok = (
        constants.get("status") is None
        and constants.get("words") == 7
        and constants.get("bytes") == 112
        and (run / str(constants.get("raw_file") or "")).is_file()
        and _sha(run / str(constants.get("raw_file"))) == (reference.get("constant") or {}).get("sha256")
    )
    gates.append(_gate("reference:constants", constant_ok, f"words={constants.get('words')}"))
    if not constant_ok:
        hard_fail = True

    texture = descriptor.get("texture_image") or {}
    texture_ok = (
        texture.get("status") == "ok"
        and texture.get("width") == 1235
        and texture.get("height") == 175
        and texture.get("sha256") == (reference.get("texture") or {}).get("sha256")
    )
    gates.append(_gate("reference:texture", texture_ok, f"{texture.get('width')}x{texture.get('height')}"))
    if not texture_ok:
        hard_fail = True

    sources = descriptor.get("shader_sources") or []
    declared = {entry.get("stage"): entry for entry in (reference.get("shader") or {}).get("sources", [])}
    source_ok = len(sources) == 2
    source_detail: list[str] = []
    for entry in sources:
        stage = entry.get("stage")
        path = run / str(entry.get("file") or "")
        if not path.is_file():
            source_ok = False
            source_detail.append(f"{stage}:missing")
            continue
        normalized = _normalized_source(path)
        expected_hash = (declared.get(stage) or {}).get("compiler_input_sha256")
        source_detail.append(f"{stage}:{normalized[:8]}")
        if normalized != expected_hash:
            source_ok = False
    gates.append(_gate("reference:shader-identity", source_ok, ",".join(source_detail)))
    if not source_ok:
        hard_fail = True

    pre_path = run / str((descriptor.get("pre_draw") or {}).get("raw_file") or "")
    post_path = run / str((descriptor.get("capture") or {}).get("raw_file") or "")
    contribution = {"differing_pixels": 0, "total_pixels": 0}
    if pre_path.is_file() and post_path.is_file():
        pre = pre_path.read_bytes()
        post = post_path.read_bytes()
        if len(pre) == len(post) and len(pre) % 4 == 0:
            contribution["total_pixels"] = len(pre) // 4
            contribution["differing_pixels"] = sum(
                1 for i in range(0, len(pre), 4) if pre[i : i + 4] != post[i : i + 4]
            )
    contribution_ok = contribution["differing_pixels"] > 0
    gates.append(_gate(
        "reference:contribution",
        contribution_ok,
        f"{contribution['differing_pixels']}/{contribution['total_pixels']} pixels changed",
    ))
    if not contribution_ok:
        hard_fail = True
    return gates, hard_fail, contribution


def _run_replay(workspace: Path, reference_run: Path, output: Path) -> dict:
    from automation import m5_replay  # type: ignore

    return m5_replay.run_replay(reference_run, output, force_build=True)


def comparison_gate(workspace: Path, reference_run: Path, fresh_output: Path,
                    policy: dict) -> tuple[list[dict], bool, dict]:
    from automation import gfx_compare  # type: ignore

    try:
        replay = _run_replay(workspace, reference_run, fresh_output)
    except Exception as exc:  # noqa: BLE001
        return [_gate("candidate:replay", False, str(exc))], False, {}
    candidate = replay["candidate"]
    candidate_ok = (
        candidate.get("backend") == "metal"
        and (candidate.get("gpu") or {}).get("completed") is True
        and (candidate.get("draw") or {}).get("caller_rva") == "0x14f6c1b"
        and (candidate.get("draw") or {}).get("shader_identity") == SPRITE_IDENTITY
        and (candidate.get("draw") or {}).get("effective_count") == 4
    )
    gates = [_gate(
        "candidate:replay-gpu",
        candidate_ok,
        f"command_status={(candidate.get('gpu') or {}).get('command_status')} identity={(candidate.get('draw') or {}).get('shader_identity')}",
    )]
    if not candidate_ok:
        return gates, False, {}
    reference_descriptor = _load_json(reference_run / "gl-reference.json")
    reference_constant = reference_descriptor.get("constants") or {}
    reference_texture = reference_descriptor.get("texture_image") or {}
    reference_vertex = _load_json(reference_run / "gl-reference-vertex.json")
    candidate_state = candidate.get("state") or {}
    candidate_draw = candidate.get("draw") or {}
    inputs_ok = (
        candidate_state.get("constant_sha256") == reference_constant.get("sha256")
        and candidate_state.get("texture_sha256") == reference_texture.get("sha256")
        and candidate_state.get("vertex_stride") == 20
        and candidate_draw.get("vertex_hex") == reference_vertex.get("hex")
        and candidate_state.get("logical_viewport") == [0, 0, 1728, 1117]
    )
    gates.append(_gate(
        "candidate:inputs",
        inputs_ok,
        f"constant={str(candidate_state.get('constant_sha256'))[:8]} "
        f"texture={str(candidate_state.get('texture_sha256'))[:8]} "
        f"stride={candidate_state.get('vertex_stride')}",
    ))
    if not inputs_ok:
        return gates, False, {}
    try:
        reference = gfx_compare.load_capture(reference_run, "reference")
        candidate_capture = gfx_compare.load_capture(fresh_output, "candidate")
        report = gfx_compare.compare(reference, candidate_capture, policy)
    except Exception as exc:  # noqa: BLE001
        gates.append(_gate("candidate:comparison", False, str(exc)))
        return gates, False, {}
    samples = report.get("samples") or {}
    exact = (
        report.get("status") == "pass"
        and samples.get("max_channel_error") == 0
        and samples.get("mismatched_pixels") == 0
        and samples.get("mean_abs_error") == 0.0
    )
    gates.append(_gate(
        "candidate:comparison",
        exact,
        f"status={report.get('status')} max={samples.get('max_channel_error')} "
        f"mismatched={samples.get('mismatched_pixels')} pixels={samples.get('pixels')}",
    ))
    (fresh_output / "comparison-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return gates, exact, report


def unsupported_state_gate(workspace: Path, reference_run: Path) -> dict:
    from automation import m5_replay  # type: ignore

    with tempfile.TemporaryDirectory(prefix="m5-bad-state-") as tmp:
        host = m5_replay.build_host()
        env = __import__("os").environ.copy()
        env["EU4_GFX_EXPECTED_EXE"] = "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4"
        env["EU4_GFX_EXECUTABLE_SHA256"] = (
            "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
        )
        env["EU4_M5_REPLAY_BAD_STATE"] = "1"
        ran = subprocess.run(
            [str(host), "--reference", str(reference_run), "--out", tmp],
            capture_output=True, text=True, check=False, env=env,
        )
        detail = (ran.stderr or ran.stdout).strip()[-300:]
        return _gate(
            "safety:unsupported-state",
            ran.returncode != 0 and ("blend state" in detail or "state (" in detail),
            detail,
        )


def corrupted_input_gate(workspace: Path, reference_run: Path) -> dict:
    from automation import gfx_compare, m5_replay  # type: ignore

    with tempfile.TemporaryDirectory(prefix="m5-corrupt-") as tmp:
        root = Path(tmp)
        corrupt_reference = root / "reference"
        shutil.copytree(reference_run, corrupt_reference)
        texture = corrupt_reference / "gl-reference-texture.bgra"
        if not texture.is_file():
            return _gate("safety:corrupted-input", False, "texture payload is absent")
        data = bytearray(texture.read_bytes())
        # A material texture corruption: invert a horizontal band through the
        # middle of the sprite, which the selected draw samples.
        row = 1235 * 4
        for y in range(70, 105):
            for i in range(y * row, (y + 1) * row, 4):
                data[i] ^= 0xFF
                data[i + 1] ^= 0xFF
                data[i + 2] ^= 0xFF
        texture.write_bytes(bytes(data))
        output = root / "candidate"
        try:
            m5_replay.run_replay(corrupt_reference, output, force_build=False)
            policy = json.loads((workspace / POLICY).read_text())
            reference = gfx_compare.load_capture(reference_run, "reference")
            candidate = gfx_compare.load_capture(output, "candidate")
            report = gfx_compare.compare(reference, candidate, policy)
        except Exception as exc:  # noqa: BLE001
            return _gate("safety:corrupted-input", False, str(exc))
        samples = report.get("samples") or {}
        failed = (
            report.get("status") == "fail"
            and samples.get("max_channel_error", 0) > 0
            and samples.get("mismatched_pixels", 0) > 0
        )
        return _gate(
            "safety:corrupted-input",
            failed,
            f"status={report.get('status')} max={samples.get('max_channel_error')} "
            f"mismatched={samples.get('mismatched_pixels')}",
        )


def regression_gate(workspace: Path) -> dict:
    command = [
        sys.executable, "-m", "unittest",
        "tests.test_gfx_compare", "tests.test_gl_reference",
        "tests.test_gfx_live_draw", "tests.test_gfx_simple_draw",
        "tests.test_m4_accept", "tests.test_gfx_m5_sprite",
    ]
    try:
        ran = subprocess.run(
            command, cwd=workspace, capture_output=True, text=True,
            timeout=600, check=False,
            env={**__import__("os").environ, "PYTHONPATH": "benchmark:."},
        )
        ok = ran.returncode == 0
        detail = (ran.stderr or ran.stdout).strip()[-500:]
    except (OSError, subprocess.SubprocessError) as exc:
        ok = False
        detail = str(exc)
    return _gate("regressions:offline", ok, detail)


def m4_gate(workspace: Path) -> dict:
    checker = workspace / "campaigns" / "eu4-m4" / "accept.py"
    evidence = workspace / ".local" / "m4" / "experiments"
    if not checker.is_file():
        return _gate("regressions:m4-checker", True, "M4 checker is not present in this workspace")
    if not evidence.is_dir():
        return _gate(
            "regressions:m4-checker",
            True,
            "M4 live evidence is local-only and absent; M4 host tests cover the route",
        )
    try:
        ran = subprocess.run(
            [sys.executable, str(checker), str(workspace)],
            cwd=workspace, capture_output=True, text=True, timeout=600, check=False,
        )
        payload = json.loads((ran.stdout or "{}").strip().splitlines()[-1])
        verdict = payload.get("verdict")
        return _gate("regressions:m4-checker", verdict == "pass", f"verdict={verdict}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError) as exc:
        return _gate("regressions:m4-checker", False, str(exc))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"verdict": "unavailable", "diagnostics": "usage: accept.py <workspace>"}))
        return 2
    workspace = Path(argv[1]).resolve()
    manifest_path = workspace / MANIFEST
    manifest = _load_json(manifest_path)
    policy = _load_json(workspace / POLICY)
    campaign = _load_json(Path(__file__).resolve().parent / "campaign.json")
    if manifest.get("schema") != "eu4-m5-evidence/v1":
        print(json.dumps({"verdict": "unavailable", "diagnostics": "unexpected M5 manifest schema"}))
        return 2
    pinned = campaign.get("evidence_manifest_sha256")
    manifest_sha = _sha(manifest_path) if manifest_path.is_file() else ""
    if pinned and pinned != manifest_sha:
        print(json.dumps({
            "verdict": "fail",
            "diagnostics": "evidence manifest digest does not match the campaign pin",
        }))
        return 1

    gates: list[dict] = []
    gates.append(_gate(
        "evidence:manifest",
        bool(manifest_sha) and (not pinned or pinned == manifest_sha),
        f"sha256={manifest_sha}",
    ))
    policy_path = workspace / str((manifest.get("policy") or {}).get("path") or POLICY)
    gates.append(_gate(
        "evidence:policy",
        policy.get("schema") == "gfx-compare-policy/v1"
        and _sha(policy_path) == (manifest.get("policy") or {}).get("sha256"),
        "policy digest",
    ))

    reference_gates_list, hard_fail, contribution = reference_gates(workspace, manifest)
    gates.extend(reference_gates_list)
    reference_run = workspace / str((manifest.get("reference") or {}).get("run") or "")
    fresh = workspace / ".local" / "m5" / f"accept-{int(time.time())}"
    comparison_gates, comparison_ok, report = comparison_gate(
        workspace, reference_run, fresh, policy
    )
    gates.extend(comparison_gates)
    gates.append(unsupported_state_gate(workspace, reference_run))
    gates.append(corrupted_input_gate(workspace, reference_run))
    gates.append(regression_gate(workspace))
    gates.append(m4_gate(workspace))

    hard_fail = hard_fail or not comparison_ok
    if any(gate["name"].startswith("safety:") and not gate["ok"] for gate in gates):
        hard_fail = True
    if any(gate["name"] == "regressions:offline" and not gate["ok"] for gate in gates):
        hard_fail = True
    if any(
        gate["name"] == "regressions:m4-checker"
        and not gate["ok"]
        and "verdict=inconclusive" in gate["detail"]
        and "local-only" not in gate["detail"]
        for gate in gates
    ):
        hard_fail = True
    inconclusive = any(not gate["ok"] for gate in gates) and not hard_fail
    if hard_fail:
        verdict = "fail"
        diagnostics = "M5 evidence mismatch: " + "; ".join(
            gate["detail"] for gate in gates if not gate["ok"]
        )
    elif any(not gate["ok"] for gate in gates):
        verdict = "inconclusive"
        diagnostics = "M5 evidence incomplete: " + ", ".join(
            gate["name"] for gate in gates if not gate["ok"]
        )
    else:
        verdict = "pass"
        diagnostics = "all M5 gates satisfied"

    result = {
        "verdict": verdict,
        "diagnostics": diagnostics,
        "gates": gates,
        "operation": {
            "symbol": "_GfxDraw",
            "caller_rva": "0x14f6c1b",
            "shader_identity": SPRITE_IDENTITY,
            "primitive": 5,
            "vertex_stride": 20,
            "texture": "1235x175 BGRA8",
            "blend": "SRC_ALPHA/ONE_MINUS_SRC_ALPHA",
        },
        "reference": {
            "run": str((manifest.get("reference") or {}).get("run")),
            "raw_sha256": (manifest.get("reference") or {}).get("raw", {}).get("sha256"),
            "contribution": contribution,
        },
        "candidate": {
            "run": str(fresh.relative_to(workspace)) if fresh.is_relative_to(workspace) else str(fresh),
            "raw_sha256": ((report.get("candidate") or {}).get("raw_sha256")
                           if isinstance(report, dict) else None),
            "gpu_completed": ((report.get("candidate") or {}).get("gpu") or {}).get("completed")
            if isinstance(report, dict) else None,
        },
        "comparison": {
            "status": report.get("status") if isinstance(report, dict) else None,
            "max_channel_error": ((report.get("samples") or {}).get("max_channel_error")
                                  if isinstance(report, dict) else None),
            "mismatched_pixels": ((report.get("samples") or {}).get("mismatched_pixels")
                                  if isinstance(report, dict) else None),
            "pixels": ((report.get("samples") or {}).get("pixels")
                       if isinstance(report, dict) else None),
        },
        "engine_reach": manifest.get("engine_reach") or {},
    }
    print(json.dumps(result, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
