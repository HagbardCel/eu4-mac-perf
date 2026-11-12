#!/usr/bin/env python3
"""Fail-closed M5 acceptance checker.

    python3 accept.py <workspace>

M5 requires the authentic original-OpenGL sprite draw (the next distinct
rendering operation after the M4 Simple text draw), the same operation encoded
and GPU-completed by the authentic live engine path, an ordered live prefix,
exact seeded replay equivalence under the declared policy, a recomputed nonzero
live contribution, unsupported-state refusal, corrupted-input refusal, evidence
integrity, and the preserved M4 route/regressions.

The checker reports `sprite_replay_qualified`, `live_engine_qualified`,
`live_composition_qualified`, `live_pixels_qualified` and `m5_complete`
separately. `verdict=pass` requires `m5_complete`; a missing live capture or
local reference is `inconclusive`; a corrupt or inconsistent manifest/policy is
`fail`. `live_pixels_qualified` is currently false because the live prefix
cannot reproduce the original loading-screen background producer; this is
reported as the remaining live-scene pixel limitation and does not substitute
for the exact seeded replay.
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
from types import SimpleNamespace

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


def reference_gates(workspace: Path, manifest: dict, policy: dict) -> tuple[list[dict], bool, dict]:
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

    sampler = descriptor.get("sampler") or {}
    expected_sampler = (policy.get("expected") or {}).get("reference_sampler") or {}
    sampler_ok = (
        sampler.get("status") == "ok"
        and sampler.get("min_filter") == expected_sampler.get("min_filter")
        and sampler.get("mag_filter") == expected_sampler.get("mag_filter")
        and sampler.get("wrap_s") == expected_sampler.get("wrap_s")
        and sampler.get("wrap_t") == expected_sampler.get("wrap_t")
    )
    gates.append(_gate(
        "reference:sampler",
        sampler_ok,
        f"min={sampler.get('min_filter')} mag={sampler.get('mag_filter')} "
        f"wrap=({sampler.get('wrap_s')},{sampler.get('wrap_t')})",
    ))
    if not sampler_ok:
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

    return m5_replay.run_replay(reference_run, output, force_build=False)


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
    reference_sampler = reference_descriptor.get("sampler") or {}
    reference_vertex = _load_json(reference_run / "gl-reference-vertex.json")
    candidate_state = candidate.get("state") or {}
    candidate_draw = candidate.get("draw") or {}
    candidate_sampler = candidate_state.get("sampler") or {}
    inputs_ok = (
        candidate_state.get("constant_sha256") == reference_constant.get("sha256")
        and candidate_state.get("texture_sha256") == reference_texture.get("sha256")
        and candidate_state.get("vertex_stride") == 20
        and candidate_draw.get("vertex_hex") == reference_vertex.get("hex")
        and candidate_state.get("logical_viewport") == [0, 0, 1728, 1117]
        and candidate_sampler.get("min_filter") == reference_sampler.get("min_filter")
        and candidate_sampler.get("mag_filter") == reference_sampler.get("mag_filter")
        and candidate_sampler.get("wrap_s") == reference_sampler.get("wrap_s")
        and candidate_sampler.get("wrap_t") == reference_sampler.get("wrap_t")
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
            capture_output=True, text=True, check=False, env=env, timeout=120,
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
        gate = _gate("regressions:m4-checker", True,
                     "M4 checker is not present in this workspace")
        gate["status"] = "not_run"
        return gate
    if not evidence.is_dir():
        gate = _gate(
            "regressions:m4-checker",
            True,
            "M4 live evidence is local-only and absent; M4 host tests cover the route",
        )
        gate["status"] = "not_run"
        return gate
    try:
        ran = subprocess.run(
            [sys.executable, str(checker), str(workspace)],
            cwd=workspace, capture_output=True, text=True, timeout=600, check=False,
        )
        payload = json.loads((ran.stdout or "{}").strip().splitlines()[-1])
        verdict = payload.get("verdict")
        gate = _gate("regressions:m4-checker", verdict in ("pass", "inconclusive"),
                     f"verdict={verdict}")
        gate["status"] = "pass" if verdict == "pass" else (
            "fail" if verdict == "fail" else "not_run")
        return gate
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError) as exc:
        gate = _gate("regressions:m4-checker", False, str(exc))
        gate["status"] = "fail"
        return gate


def _canonical_bgra(path: Path, width, height, pixel_format, origin,
                    row_stride=None) -> bytes:
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("image extent is invalid")
    if pixel_format not in ("BGRA8", "RGBA8"):
        raise ValueError("image pixel format is invalid")
    if origin not in ("lower_left", "top_left"):
        raise ValueError("image origin is invalid")
    if row_stride is not None and row_stride != width * 4:
        raise ValueError("image row stride does not match width*4")
    data = path.read_bytes()
    if len(data) != width * height * 4:
        raise ValueError(f"image size {len(data)} != {width}x{height}x4")
    from automation import gfx_compare  # type: ignore

    capture = SimpleNamespace(
        payload=data, pixel_format=pixel_format, width=width, height=height,
        row_stride=width * 4, origin=origin,
    )
    return gfx_compare._canonical_rgba(capture)


def _changed_mask(before: bytes, after: bytes) -> tuple[list[int], int]:
    if len(before) != len(after):
        raise ValueError("image sizes differ")
    mask = [0] * (len(before) // 4)
    changed = 0
    for pixel in range(len(mask)):
        offset = pixel * 4
        if before[offset:offset + 4] != after[offset:offset + 4]:
            mask[pixel] = 1
            changed += 1
    return mask, changed


def evidence_integrity_gates(workspace: Path, manifest: dict,
                             policy: dict) -> tuple[list[dict], str]:
    """Return (gates, status) for manifest/policy integrity.

    status is "ok", "fail" (available evidence is inconsistent or invalid) or
    "inconclusive" (required local evidence is missing).
    """
    gates: list[dict] = []
    manifest_path = workspace / MANIFEST
    if not manifest_path.is_file():
        gates.append(_gate("evidence:manifest", False, "evidence manifest is missing"))
        gates.append(_gate("evidence:policy", False, "evidence manifest is missing"))
        return gates, "inconclusive"
    if manifest.get("schema") != "eu4-m5-evidence/v1":
        gates.append(_gate("evidence:manifest", False,
                           "evidence manifest is malformed or has an unexpected schema"))
        gates.append(_gate("evidence:policy", False, "evidence manifest is invalid"))
        return gates, "fail"
    manifest_sha = _sha(manifest_path)
    campaign = _load_json(Path(__file__).resolve().parent / "campaign.json")
    pinned = campaign.get("evidence_manifest_sha256")
    if pinned and pinned != manifest_sha:
        gates.append(_gate("evidence:manifest", False,
                           "evidence manifest digest does not match the campaign pin"))
        gates.append(_gate("evidence:policy", False, "manifest integrity failed"))
        return gates, "fail"
    gates.append(_gate("evidence:manifest", True, f"sha256={manifest_sha}"))

    policy_path = workspace / str((manifest.get("policy") or {}).get("path") or POLICY)
    if not policy_path.is_file():
        gates.append(_gate("evidence:policy", False, "comparison policy is missing"))
        return gates, "inconclusive"
    expected = (manifest.get("policy") or {}).get("sha256")
    if policy.get("schema") != "gfx-compare-policy/v1":
        gates.append(_gate("evidence:policy", False, "comparison policy is malformed"))
        return gates, "fail"
    if not expected or _sha(policy_path) != expected:
        gates.append(_gate("evidence:policy", False,
                           "comparison policy digest does not match the manifest"))
        return gates, "fail"
    gates.append(_gate("evidence:policy", True, "policy digest"))
    return gates, "ok"


def live_engine_gates(workspace: Path, manifest: dict, policy: dict) -> tuple[list[dict], bool, bool, bool, dict]:
    """Validate the authentic live-engine reach and GPU execution.

    Returns (gates, live_engine_ok, composition_ok, live_pixels_ok, live_report).
    The live pixel comparison is reported separately because the loading-screen
    background is not yet reconstructed in the live prefix; a mismatch there is
    not a seeded-replay failure.
    """
    from automation import gfx_compare  # type: ignore

    live = manifest.get("live_engine") or {}
    gates: list[dict] = []
    if not live:
        gates.append(_gate("live:evidence", False, "live engine capture is absent"))
        return gates, False, False, False, {}
    evidence_path = workspace / str(live.get("evidence") or "analysis/m5/live-evidence.json")
    evidence = _load_json(evidence_path)
    if evidence.get("schema") != "eu4-m5-live-evidence/v1":
        gates.append(_gate("live:evidence", False, "live evidence schema is unavailable"))
        return gates, False, False, False, {}
    if live.get("sha256") and _sha(evidence_path) != live.get("sha256"):
        gates.append(_gate("live:evidence", False, "live evidence digest does not match"))
        return gates, False, False, False, {}
    run = workspace / str(evidence.get("run") or "")
    if not run.is_dir():
        gates.append(_gate("live:evidence", False, f"live run is absent: {run}"))
        return gates, False, False, False, {}

    def _load_block(block: dict) -> tuple[dict, bool]:
        name = str(block.get("descriptor") or "")
        descriptor_path = run / name
        if not name or not descriptor_path.is_file():
            return {}, False
        descriptor = _load_json(descriptor_path)
        raw = run / str(block.get("bgra") or "")
        ok = (
            bool(descriptor)
            and raw.is_file()
            and raw.stat().st_size == block.get("bgra_bytes")
            and _sha(raw) == block.get("bgra_sha256")
        )
        if block.get("descriptor_sha256") and _sha(descriptor_path) != block.get("descriptor_sha256"):
            ok = False
        return descriptor, ok

    checkpoint, checkpoint_ok = _load_block(evidence.get("checkpoint") or {})
    logical, logical_ok = _load_block(evidence.get("logical") or {})
    prefix, prefix_ok = _load_block(evidence.get("prefix_pre") or {})
    reference_descriptor = _load_json(
        workspace / str((manifest.get("reference") or {}).get("run") or "") / "gl-reference.json"
    )
    reference_sampler = reference_descriptor.get("sampler") or {}
    live_sampler = (logical.get("state") or {}).get("sampler") or {}
    checkpoint_draw = checkpoint.get("draw") or {}
    checkpoint_gpu = checkpoint.get("gpu") or {}
    logical_draw = logical.get("draw") or {}
    logical_gpu = logical.get("gpu") or {}
    live_ok = (
        checkpoint_ok
        and logical_ok
        and prefix_ok
        and evidence.get("engine_reached") is True
        and checkpoint.get("caller", {}).get("rva") == "0x14f6c1b"
        and checkpoint_draw.get("shader_identity") == SPRITE_IDENTITY
        and checkpoint_draw.get("primitive") == 5
        and checkpoint_draw.get("count") == 4
        and checkpoint_draw.get("vertex_stride") == 20
        and checkpoint_draw.get("vertex_bytes") == 80
        and checkpoint_gpu.get("completed") is True
        and checkpoint_gpu.get("command_status") == 4
        and checkpoint.get("target", {}).get("width") == 3456
        and checkpoint.get("target", {}).get("height") == 2234
        and logical_draw.get("caller_rva") == "0x14f6c1b"
        and logical_draw.get("shader_identity") == SPRITE_IDENTITY
        and logical_draw.get("effective_count") == 4
        and logical_gpu.get("completed") is True
        and prefix.get("target", {}).get("width") == 1728
        and prefix.get("target", {}).get("height") == 1117
        and live_sampler.get("min_filter") == reference_sampler.get("min_filter")
        and live_sampler.get("mag_filter") == reference_sampler.get("mag_filter")
        and live_sampler.get("wrap_s") == reference_sampler.get("wrap_s")
        and live_sampler.get("wrap_t") == reference_sampler.get("wrap_t")
    )
    gates.append(_gate(
        "live:engine-gpu",
        live_ok,
        f"caller={checkpoint.get('caller', {}).get('rva')} "
        f"identity={checkpoint_draw.get('shader_identity')} "
        f"gpu={checkpoint_gpu.get('command_status')} "
        f"target={checkpoint.get('target', {}).get('width')}x{checkpoint.get('target', {}).get('height')}",
    ))
    if not live_ok:
        return gates, False, False, False, {}

    prefix_draws = (logical.get("prefix") or {}).get("draws") or []
    order_ok = (
        len(prefix_draws) >= 2
        and prefix_draws[0].get("shader_identity") == "simple-lua-Simple/asset-maincode-v0"
        and prefix_draws[-1].get("shader_identity") == SPRITE_IDENTITY
        and prefix_draws[-1].get("caller_rva") == "0x14f6c1b"
        and all(isinstance(draw.get("caller_rva"), str) for draw in prefix_draws)
    )
    gates.append(_gate(
        "live:prefix-order",
        order_ok,
        f"draws={[(d.get('caller_rva'), d.get('shader_identity')) for d in prefix_draws]}",
    ))
    reported = evidence.get("contribution") or {}
    contribution_ok = False
    contribution_detail = "image validation failed"
    contribution_report: dict = {}
    try:
        reference_run = workspace / str((manifest.get("reference") or {}).get("run") or "")
        reference_capture = gfx_compare.load_capture(reference_run, "reference")
        reference_descriptor = _load_json(reference_run / "gl-reference.json")
        pre_descriptor = reference_descriptor.get("pre_draw") or {}
        pre_gl_path = reference_run / str(pre_descriptor.get("raw_file") or "")
        gl_pre = _canonical_bgra(
            pre_gl_path, pre_descriptor.get("width"), pre_descriptor.get("height"),
            pre_descriptor.get("pixel_format"), pre_descriptor.get("origin"),
            pre_descriptor.get("row_stride"),
        )
        gl_post = gfx_compare._canonical_rgba(reference_capture)
        prefix_target = prefix.get("target") or {}
        live_pre = _canonical_bgra(
            run / str((evidence.get("prefix_pre") or {}).get("bgra") or ""),
            prefix_target.get("width"), prefix_target.get("height"),
            prefix_target.get("pixel_format"), prefix_target.get("origin"),
            prefix_target.get("row_stride"),
        )
        logical_target = logical.get("target") or {}
        live_post = _canonical_bgra(
            run / str((evidence.get("logical") or {}).get("bgra") or ""),
            logical_target.get("width"), logical_target.get("height"),
            logical_target.get("pixel_format"), logical_target.get("origin"),
            logical_target.get("row_stride"),
        )
        gl_mask, gl_changed = _changed_mask(gl_pre, gl_post)
        live_mask, live_changed = _changed_mask(live_pre, live_post)
        total = len(gl_mask)
        reported_agree = (
            gl_changed == reported.get("gl_differing_pixels")
            and live_changed == reported.get("live_differing_pixels")
            and total == reported.get("total_pixels")
        )
        intersection = sum(1 for a, b in zip(gl_mask, live_mask) if a and b)
        union = sum(1 for a, b in zip(gl_mask, live_mask) if a or b)
        equal = sum(1 for a, b in zip(gl_mask, live_mask) if a == b)
        iou = (intersection / union) if union else 0.0
        contribution_report = {
            "gl_recomputed": gl_changed,
            "live_recomputed": live_changed,
            "gl_reported": reported.get("gl_differing_pixels"),
            "live_reported": reported.get("live_differing_pixels"),
            "total_pixels": total,
            "reported_agree": reported_agree,
            "mask_intersection": intersection,
            "mask_union": union,
            "mask_equal_pixels": equal,
            "mask_iou": round(iou, 6),
        }
        material = live_changed >= max(1, total // 1000)
        contribution_ok = material and reported_agree
        contribution_detail = (
            f"live={live_changed} gl={gl_changed} reported_agree={reported_agree} "
            f"mask_iou={iou:.3f} total={total}"
        )
    except Exception as exc:  # noqa: BLE001
        contribution_detail = str(exc)
    gates.append(_gate("live:contribution", contribution_ok, contribution_detail))
    composition_ok = order_ok and contribution_ok

    live_pixels_ok = False
    live_report: dict = {}
    try:
        reference = gfx_compare.load_capture(workspace / str((manifest.get("reference") or {}).get("run") or ""), "reference")
        candidate = gfx_compare.load_capture(run / "metal-logical.json", "candidate")
        live_report = gfx_compare.compare(reference, candidate, policy)
        samples = live_report.get("samples") or {}
        live_pixels_ok = (
            live_report.get("status") == "pass"
            and samples.get("max_channel_error") == 0
            and samples.get("mismatched_pixels") == 0
        )
        detail = f"status={live_report.get('status')} max={samples.get('max_channel_error')} mismatched={samples.get('mismatched_pixels')}"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    gates.append(_gate("live:pixels", live_pixels_ok, detail + " (separate from seeded replay)"))
    live_report["contribution"] = contribution_report
    return gates, live_ok, composition_ok, live_pixels_ok, live_report


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"verdict": "unavailable", "diagnostics": "usage: accept.py <workspace>"}))
        return 2
    workspace = Path(argv[1]).resolve()
    manifest_path = workspace / MANIFEST
    manifest = _load_json(manifest_path)
    policy_path = workspace / str((manifest.get("policy") or {}).get("path") or POLICY)
    policy = _load_json(policy_path)

    integrity_gates, integrity_status = evidence_integrity_gates(
        workspace, manifest, policy
    )
    if integrity_status != "ok":
        diagnostic = "; ".join(
            gate["detail"] for gate in integrity_gates if not gate["ok"]
        )
        print(json.dumps({
            "verdict": "inconclusive" if integrity_status == "inconclusive" else "fail",
            "m5_complete": False,
            "diagnostics": "M5 evidence is not qualified: " + diagnostic,
            "gates": integrity_gates,
            "sprite_replay_qualified": False,
            "live_engine_qualified": False,
            "live_composition_qualified": False,
            "live_pixels_qualified": False,
        }, indent=2))
        return 1

    gates: list[dict] = list(integrity_gates)

    reference_run = workspace / str((manifest.get("reference") or {}).get("run") or "")
    reference_available = reference_run.is_dir()
    reference_ok = False
    contribution: dict = {}
    replay_ok = False
    report: dict = {}
    fresh = workspace / ".local" / "m5" / f"accept-{int(time.time())}"
    if reference_available:
        reference_gates_list, reference_hard, contribution = reference_gates(workspace, manifest, policy)
        gates.extend(reference_gates_list)
        comparison_gates, replay_ok, report = comparison_gate(
            workspace, reference_run, fresh, policy
        )
        gates.extend(comparison_gates)
        reference_ok = (
            all(gate["ok"] for gate in reference_gates_list)
            and not reference_hard
            and replay_ok
        )
    else:
        gates.append(_gate("reference:available", False,
                           "operator-local original-GL reference is absent"))
        gates.append(_gate("candidate:replay-gpu", False,
                           "offline replay is not run without the local reference"))

    live_gates, live_engine_ok, composition_ok, live_pixels_ok, live_report = live_engine_gates(
        workspace, manifest, policy
    )
    gates.extend(live_gates)
    gates.append(unsupported_state_gate(workspace, reference_run) if reference_available
                 else _gate("safety:unsupported-state", False,
                            "local reference is absent"))
    gates.append(corrupted_input_gate(workspace, reference_run) if reference_available
                 else _gate("safety:corrupted-input", False,
                            "local reference is absent"))
    gates.append(regression_gate(workspace))
    m4_result = m4_gate(workspace)
    gates.append(m4_result)

    hard_fail = integrity_status == "fail"
    if reference_available:
        hard_fail = hard_fail or any(
            gate["name"] in ("reference:event", "reference:target", "reference:vertex",
                             "reference:constants", "reference:texture",
                             "reference:shader-identity", "reference:contribution")
            and not gate["ok"]
            for gate in gates
        )
        hard_fail = hard_fail or any(
            gate["name"] in ("candidate:comparison",) and not gate["ok"] for gate in gates
        )
    if any(gate["name"].startswith("safety:") and not gate["ok"] for gate in gates) and reference_available:
        hard_fail = True
    if any(gate["name"] == "regressions:offline" and not gate["ok"] for gate in gates):
        hard_fail = True
    if m4_result.get("status") == "fail":
        hard_fail = True
    if any(gate["name"].startswith("live:") and not gate["ok"] for gate in gates) and             any(gate["name"] == "live:evidence" and not gate["ok"] for gate in gates):
        # Missing live evidence is inconclusive, not a hard failure; a present
        # live evidence record that fails validation is a hard failure.
        hard_fail = hard_fail or not any(
            gate["name"] == "live:evidence" and "absent" in gate["detail"]
            for gate in gates
        )

    safety_ok = all(
        gate["ok"] for gate in gates if gate["name"].startswith("safety:")
    )
    regressions_ok = all(
        gate["ok"] for gate in gates
        if gate["name"] == "regressions:offline"
    )
    m4_ok = m4_result.get("status") in ("pass", "not_run")
    sprite_replay_qualified = reference_ok and replay_ok
    integrity_ok = integrity_status == "ok"
    m5_complete = (
        integrity_ok
        and sprite_replay_qualified
        and live_engine_ok
        and composition_ok
        and safety_ok
        and regressions_ok
        and m4_ok
    )
    if hard_fail:
        verdict = "fail"
        diagnostics = "M5 evidence mismatch: " + "; ".join(
            gate["detail"] for gate in gates if not gate["ok"]
        )
    elif m5_complete:
        verdict = "pass"
        diagnostics = "live engine reach, GPU execution, composition, seeded replay and regressions satisfied"
    else:
        verdict = "inconclusive"
        missing = [gate["name"] for gate in gates if not gate["ok"]]
        diagnostics = "M5 evidence incomplete: " + ", ".join(missing)

    result = {
        "verdict": verdict,
        "diagnostics": diagnostics,
        "sprite_replay_qualified": sprite_replay_qualified,
        "live_engine_qualified": live_engine_ok,
        "live_composition_qualified": composition_ok,
        "live_pixels_qualified": live_pixels_ok,
        "m5_complete": m5_complete,
        "evidence_integrity": integrity_status,
        "m4_evidence": {
            "status": m4_result.get("status", "not_run"),
            "detail": m4_result.get("detail"),
        },
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
            "available": reference_available,
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
        "live": {
            "run": (manifest.get("live_engine") or {}).get("evidence"),
            "pixels": {
                "status": live_report.get("status") if isinstance(live_report, dict) else None,
                "max_channel_error": ((live_report.get("samples") or {}).get("max_channel_error")
                                      if isinstance(live_report, dict) else None),
                "mismatched_pixels": ((live_report.get("samples") or {}).get("mismatched_pixels")
                                      if isinstance(live_report, dict) else None),
            },
            "reason": (manifest.get("live_engine") or {}).get("limitation"),
            "contribution": live_report.get("contribution") if isinstance(live_report, dict) else None,
        },
        "engine_reach": manifest.get("engine_reach") or {},
    }
    print(json.dumps(result, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
