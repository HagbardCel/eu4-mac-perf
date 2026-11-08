"""Trusted acceptance for the L3.2b agent-driven draw-input campaigns.

The checker never launches EU4. It builds/tests the candidate worktree, verifies
the frozen campaign/oracle artifacts, validates the campaign launch receipts,
and independently checks the candidate packet against the original capture.
A candidate packet's ``packet_ready`` claim is not trusted.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_draw_packet as trusted_packet  # noqa: E402
import eu4_gfx_op_capture as trusted_capture  # noqa: E402
from automation.state import repo_root, require_worktree  # noqa: E402
from automation.verification_manifest import load_manifest, verify_manifest  # noqa: E402

CALLER_RVA = 0xEDFCD1
CAPTURE_MASTER_SIZE = 0x171
FROZEN_HOST_SCENARIOS = (
    "observe-draw-qualified",
    "observe-draw-two-qualified",
    "observe-draw-retention",
    "observe-buffers",
    "observe-invalid-output",
)
INPUT_HINTS = {
    "vertex_buffer_bindings": "selected vertex-buffer identity/stride/offset",
    "vertex_buffer_contents": "complete selected vertex-buffer contents",
    "matrix_constant": "64-byte constant slot 0 used by the draw",
    "vertex_attribute_layout": "selected shader object attribute table",
    "render_state_blend": "decoded blend snapshot",
    "render_state_depth_stencil": "decoded depth/stencil snapshot",
    "render_state_rasterizer": "decoded rasterizer snapshot",
    "scissor_state": "effective scissor state",
    "shader_identity": "selected shader object identity",
    "shader_stage_sources": "exact stage source bytes tied to the selected object",
    "texture_slot_0_identity": "bound texture identity",
    "texture_slot_0_contents": "texture contents and mutation closure",
    "sampler_state": "effective sampler fields",
}


def emit(verdict: str, diagnostics: str, **extra: object) -> dict:
    value = {"verdict": verdict, "diagnostics": diagnostics}
    value.update(extra)
    return value


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diagnostic(**value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _tail(text: str, limit: int = 2500) -> str:
    text = (text or "").strip()
    return text[-limit:]


def load_config(campaign_dir: Path) -> tuple[dict, dict]:
    campaign_dir = Path(campaign_dir).resolve()
    campaign = _json(campaign_dir / "campaign.json")
    config = _json(campaign_dir / "l32b.json")
    required = ("milestone", "pinned_commit", "required_inputs", "allowed_paths",
                "test_modules", "manifest")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError("l32b.json missing " + ", ".join(missing))
    return campaign, config


def path_allowed(rel: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
        elif fnmatch.fnmatch(rel, pattern):
            return True
    return False


def changed_paths(workspace: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1", "-z",
         "--untracked-files=all"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.decode() or "git status failed")
    parts = completed.stdout.split(b"\0")
    found: list[str] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        if not entry:
            break
        status = entry[:2].decode()
        found.append(entry[3:].decode())
        index += 1
        if "R" in status or "C" in status:
            if index < len(parts) and parts[index]:
                found.append(parts[index].decode())
                index += 1
    return found


def worktree_problem(workspace: Path, pin: str) -> tuple[str, str] | None:
    try:
        require_worktree(workspace, repo_root(Path(__file__).resolve().parent), pin)
    except RuntimeError as exc:
        return ("unavailable", f"worktree unavailable: {exc}")
    detached = subprocess.run(
        ["git", "-C", str(workspace), "symbolic-ref", "-q", "HEAD"],
        capture_output=True,
        check=False,
    )
    if detached.returncode == 0:
        return ("unavailable", "worktree is not detached")
    return None


def manifest_problem(campaign_dir: Path, config: dict, pin: str) -> str | None:
    path = campaign_dir / str(config["manifest"])
    if not path.is_file():
        return f"verification manifest missing: {path.name}"
    try:
        manifest = load_manifest(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"verification manifest unreadable: {exc}"
    errors = verify_manifest(ROOT, manifest)
    if errors:
        return "verification manifest: " + "; ".join(errors[:6])
    if manifest.get("repo_baseline_commit") != pin:
        return "verification manifest baseline does not match the campaign pin"
    try:
        target = _json(ROOT / "reverse_engineering" / "target.json")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"pinned target metadata unreadable: {exc}"
    if manifest.get("eu4_executable_sha256") != target.get("sha256"):
        return "verification manifest executable identity differs from target.json"
    for member in manifest.get("members", []):
        if isinstance(member, dict) and Path(str(member.get("path", ""))).name == path.name:
            return "verification manifest must not include itself"
    return None


def _candidate_env(workspace: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(workspace / "benchmark") + os.pathsep + str(workspace)
    )
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    return env


def _candidate_run(workspace: Path, arguments: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        arguments,
        cwd=str(workspace),
        env=_candidate_env(workspace),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def build_candidate(workspace: Path) -> tuple[str | None, dict | None]:
    if sys.platform != "darwin":
        return None, emit("unavailable", "L3.2b observer builds require macOS")
    if shutil.which("clang") is None:
        return None, emit("unavailable", "clang is not on PATH")
    code = (
        "import json, eu4_gfx_startup_trace as s;"
        "print(json.dumps({'library': str(s.build_library()),"
        "'host': str(s.build_host())}))"
    )
    try:
        completed = _candidate_run(workspace, [sys.executable, "-c", code], 900)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, emit("unavailable", f"candidate build could not run: {exc}")
    if completed.returncode != 0:
        detail = _tail(completed.stderr or completed.stdout)
        return None, emit("fail", f"candidate observer build failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
        library = Path(payload["library"]).resolve()
    except (json.JSONDecodeError, KeyError, OSError, TypeError) as exc:
        return None, emit("fail", f"candidate build result unreadable: {exc}")
    if not library.is_file():
        return None, emit("fail", "candidate observer library was not produced")
    return _sha(library), None


def run_candidate_tests(workspace: Path, modules: list[str]) -> dict | None:
    try:
        completed = _candidate_run(
            workspace, [sys.executable, "-m", "unittest", *modules], 900
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return emit("unavailable", f"candidate tests could not run: {exc}")
    if completed.returncode != 0:
        detail = _tail(completed.stderr or completed.stdout)
        return emit("fail", f"candidate tests failed: {detail}")
    output = (completed.stderr or "") + (completed.stdout or "")
    if "Ran 0 tests" in output:
        return emit("fail", "candidate tests ran no tests")
    return None


def _arch_prefix() -> list[str]:
    return [] if platform.machine() == "x86_64" else ["arch", "-x86_64"]


def run_frozen_host(workspace: Path, campaign_dir: Path) -> dict | None:
    if sys.platform != "darwin":
        return emit("unavailable", "frozen observer host requires macOS")
    if shutil.which("clang") is None:
        return emit("unavailable", "clang is not on PATH")
    frozen = campaign_dir / "frozen"
    host_source = frozen / "gfx_startup_trace_host.c"
    stubs_source = frozen / "gfx_startup_trace_stubs.S"
    if not host_source.is_file() or not stubs_source.is_file():
        return emit("unavailable", "frozen host fixtures are missing")
    bench = workspace / "benchmark"
    sources = [
        str(host_source),
        str(stubs_source),
        str(bench / "eu4_gfx_site_patch.c"),
        str(bench / "eu4_gfx_observe.c"),
        *[str(path) for path in sorted(bench.glob("eu4_gfx_observe_*.c"))],
        str(bench / "eu4_gfx_observe_gateway.S"),
        str(bench / "eu4_gfx_startup_trace.c"),
        str(bench / "eu4_gfx_startup_trace_detour.c"),
        str(bench / "eu4_gfx_startup_trace_gateway.S"),
    ]
    temporary = Path(tempfile.mkdtemp(prefix="eu4-l32b-frozen-"))
    try:
        host = temporary / "gfx_host"
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
            "-I", str(bench), "-o", str(host), *sources,
        ]
        try:
            built = subprocess.run(
                command, capture_output=True, text=True, timeout=300, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return emit("unavailable", f"frozen host build could not run: {exc}")
        if built.returncode != 0:
            return emit(
                "fail",
                "frozen observer host failed to compile: "
                + _tail(built.stderr or built.stdout),
            )
        for scenario in FROZEN_HOST_SCENARIOS:
            directory = temporary / scenario
            directory.mkdir()
            ran = subprocess.run(
                [*_arch_prefix(), str(host), scenario, str(directory)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if ran.returncode != 0:
                detail = _tail(ran.stderr or ran.stdout or "scenario failed")
                return emit("fail", f"frozen host scenario {scenario}: {detail}")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return None


def read_launch(run_root: Path, limit: int) -> tuple[dict | None, dict | None]:
    path = run_root / "live-launch.json"
    if not path.is_file():
        return None, emit(
            "inconclusive",
            _diagnostic(
                reason="no_campaign_launch_counter",
                captures_remaining=limit,
                next_investigation="implement offline evidence first",
            ),
        )
    try:
        data = _json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, emit("unavailable", f"launch counter unreadable: {exc}")
    if data.get("schema") != "live-launch/v0":
        return None, emit("unavailable", "unexpected launch counter schema")
    if data.get("campaign_id") and data["campaign_id"] != run_root.name:
        return None, emit("unavailable", "launch counter campaign mismatch")
    used = data.get("used")
    counter_limit = data.get("limit")
    receipts = data.get("launches")
    if type(used) is not int or type(counter_limit) is not int or not isinstance(receipts, list):
        return None, emit("unavailable", "launch counter is malformed")
    if used < 0 or counter_limit != limit or used > counter_limit or len(receipts) != used:
        return None, emit("unavailable", "launch counter does not match campaign limits")
    return data, None


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _capture_request(capture_dir: Path) -> dict | None:
    path = capture_dir / "capture-request.json"
    if not path.is_file():
        return None
    try:
        value = _json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def select_capture(
    run_root: Path, config: dict, candidate_sha: str
) -> tuple[Path | None, dict | None, dict | None]:
    captures_root = run_root / str(config.get("capture_root", "captures"))
    plans_root = run_root / str(config.get("capture_plans", "capture-plans"))
    launch_path = run_root / "live-launch.json"
    if not launch_path.is_file():
        return None, None, None
    try:
        launch = _json(launch_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None, None
    receipts = launch.get("launches") or []
    stale: list[str] = []
    for receipt in reversed(receipts):
        if not isinstance(receipt, dict):
            continue
        output = Path(str(receipt.get("output", ""))).expanduser()
        if not output.is_absolute():
            output = (run_root / output).resolve()
        else:
            output = output.resolve()
        if not _under(output, captures_root):
            return None, None, emit(
                "fail",
                _diagnostic(reason="capture_outside_accounted_root", output=str(output)),
            )
        if not output.is_dir():
            return None, None, emit(
                "unavailable",
                _diagnostic(reason="accounted_capture_missing", output=str(output)),
            )
        plan = plans_root / f"{output.name}.json"
        if not plan.is_file():
            return None, None, emit(
                "fail",
                _diagnostic(reason="capture_plan_missing", output=str(output)),
            )
        request = _capture_request(output)
        if request is None:
            return None, None, emit(
                "unavailable",
                _diagnostic(reason="capture_request_missing", output=str(output)),
            )
        library = str(request.get("library_sha256") or "")
        if library != candidate_sha:
            stale.append(output.name)
            continue
        try:
            plan_value = _json(plan)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, None, emit(
                "fail", _diagnostic(reason="capture_plan_invalid", detail=str(exc))
            )
        if not isinstance(plan_value, dict) or not plan_value.get("question") \
                or not plan_value.get("expected_observation"):
            return None, None, emit(
                "fail", _diagnostic(reason="capture_plan_incomplete", output=str(output))
            )
        return output, {"receipt": receipt, "plan": plan_value}, None
    if stale:
        return None, {"stale": stale}, emit(
            "inconclusive",
            _diagnostic(
                reason="no_capture_from_current_observer_build",
                captures_remaining=int(launch.get("limit", 0)) - int(launch.get("used", 0)),
                next_investigation="rebuild the capture with the current observer or revert the post-capture edits",
            ),
        )
    return None, None, None


def _selected_draw(artifact: trusted_capture.CaptureArtifact):
    draws = [record for record in artifact.operations if record.symbol == "_GfxDraw"]
    qualifying = [record for record in draws if record.meta.get("qualified") is True]
    return draws, qualifying


def validate_capture(capture_dir: Path, candidate_sha: str, request: dict) -> tuple[dict | None, dict | None]:
    try:
        artifact = trusted_capture.load_capture(capture_dir, require_events=False)
    except trusted_capture.CaptureIncomplete as exc:
        return None, emit("unavailable", _diagnostic(reason=exc.reason, detail=exc.detail))
    summary = artifact.op_summary
    if summary.get("stop_policy") != "target_draw" or summary.get("retention") != "state":
        return None, emit("fail", "capture is not a target-draw state capture")
    if int(summary.get("target_draw_caller", 0)) != CALLER_RVA:
        return None, emit("fail", "capture target caller is not 0xedfcd1")
    if summary.get("target_draw_qualified") is not True:
        draws, qualifying = _selected_draw(artifact)
        return None, emit(
            "inconclusive",
            _diagnostic(
                reason="draw_not_qualified",
                observed=summary.get("draw_observed", len(draws)),
                at_caller=summary.get("draw_at_caller", 0),
                modes=summary.get("draw_modes", []),
                counts=summary.get("draw_counts", {}),
            ),
        )
    draws, qualifying = _selected_draw(artifact)
    if len(qualifying) != 1:
        return None, emit("fail", "capture does not contain exactly one qualifying draw")
    target = qualifying[0]
    if target.call_id != summary.get("target_draw_call_id"):
        return None, emit("fail", "capture target call id disagrees with the summary")
    if target.payload_bytes is None or target.payload_size != CAPTURE_MASTER_SIZE:
        return None, emit("fail", "qualifying draw has no 0x171-byte master payload")
    try:
        master = trusted_packet.parse_master(target.payload_bytes)
    except trusted_packet.PacketError as exc:
        return None, emit("fail", f"draw master payload invalid: {exc}")
    raw_count = target.meta.get("raw_count")
    expected_effective = raw_count if isinstance(raw_count, int) and raw_count > 0 else master["effective_count"]
    if type(raw_count) is not int or target.meta.get("effective_count") != expected_effective:
        return None, emit("fail", "capture effective-count metadata disagrees with the draw rule")
    if target.caller_rva != CALLER_RVA or master["mode"] != 5 \
            or master["shader"] == 0 or expected_effective < 3:
        return None, emit("fail", "capture draw does not satisfy Decision 0003")
    if str(request.get("library_sha256") or "") != candidate_sha:
        return None, emit("inconclusive", _diagnostic(reason="stale_capture_build"))
    return {
        "artifact": artifact,
        "target": target,
        "master": master,
        "raw_count": raw_count,
        "effective_count": expected_effective,
        "request": request,
    }, None


def run_candidate_packet(
    workspace: Path, capture_dir: Path
) -> tuple[dict | None, Path | None, dict | None]:
    temporary = Path(tempfile.mkdtemp(prefix="eu4-l32b-packet-"))
    output = temporary / "packet"
    try:
        completed = _candidate_run(
            workspace,
            [sys.executable, "-m", "automation.live_gfx", "packet",
             "--capture", str(capture_dir), "--output", str(output)],
            600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        return None, None, emit("unavailable", f"candidate packet command failed to run: {exc}")
    packet_path = output / "draw-packet.json"
    if not packet_path.is_file():
        shutil.rmtree(temporary, ignore_errors=True)
        return None, None, emit(
            "fail", "candidate packet was not produced: " + _tail(completed.stderr or completed.stdout)
        )
    try:
        packet = _json(packet_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        shutil.rmtree(temporary, ignore_errors=True)
        return None, None, emit("fail", f"candidate packet unreadable: {exc}")
    return packet, temporary / "packet", None


def _resolve_payload(path_text: str, bases: list[Path]) -> Path | None:
    candidate = Path(path_text)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    for base in bases:
        path = (base / candidate).resolve()
        if path.is_file() and _under(path, base):
            return path
    return None


def _evidence_bytes(evidence: list, capture_dir: Path, packet_dir: Path, artifact, target_call_id: int) -> tuple[list[Path], str | None]:
    payloads: list[Path] = []
    for item in evidence:
        if not isinstance(item, dict):
            return [], "evidence entry is not an object"
        kind = item.get("type")
        if kind == "draw_snapshot":
            if item.get("call_id") != target_call_id:
                return [], "draw snapshot evidence is not the selected draw"
            payload = _resolve_payload(str(item.get("payload", "")), [capture_dir, packet_dir])
            if payload is None:
                return [], "draw snapshot payload is missing"
            if _sha(payload) != item.get("sha256") or payload.stat().st_size != item.get("size"):
                return [], "draw snapshot hash/size mismatch"
            payloads.append(payload)
        elif kind == "resource_observation":
            call_id = item.get("call_id")
            record = next((row for row in artifact.operations if row.call_id == call_id), None)
            if record is None:
                return [], "resource observation call is not in the capture"
            if record.symbol != item.get("symbol") or record.resource_id != item.get("resource_id"):
                return [], "resource observation identity mismatch"
            if not record.fields_ok:
                return [], "resource observation is not structurally valid"
            payload = _resolve_payload(str(item.get("payload", "")), [capture_dir, packet_dir])
            if payload is None or _sha(payload) != item.get("sha256") \
                    or payload.stat().st_size != item.get("size"):
                return [], "resource observation payload hash/size mismatch"
            payloads.append(payload)
        elif kind == "static_reconstruction":
            digest = item.get("span_sha256")
            span = item.get("instruction_span")
            if not isinstance(digest, str) or not isinstance(span, str):
                return [], "static reconstruction span is incomplete"
            try:
                raw = bytes.fromhex(span)
            except ValueError:
                return [], "static reconstruction span is not hex"
            if hashlib.sha256(raw).hexdigest() != digest:
                return [], "static reconstruction span digest mismatch"
        elif kind == "unresolved":
            continue
        else:
            return [], f"unsupported evidence type: {kind}"
    return payloads, None


def _effective_buffer(artifact, resource_id: str, draw_call_id: int) -> bytes | None:
    data: bytes | None = None
    for record in artifact.operations:
        if record.call_id >= draw_call_id or record.resource_id != resource_id:
            continue
        if record.symbol == "_GfxCreateVertexBuffer":
            data = record.payload_bytes if record.contents_known else None
        elif record.symbol == "_GfxUpdateVertexBuffer":
            payload = record.payload_bytes
            if payload is None:
                data = None
                continue
            if record.meta.get("partial") == 1:
                offset = record.meta.get("offset")
                if data is None or type(offset) is not int or offset < 0:
                    data = None
                else:
                    data = data[:offset] + payload + data[offset + len(payload):]
            else:
                data = payload
    return data


def _validate_vertex_contents(value: dict, ctx: dict, packet_dir: Path) -> str | None:
    buffers = value.get("buffers")
    if not isinstance(buffers, list) or not buffers:
        return "vertex_buffer_contents has no buffers"
    master = ctx["master"]
    master_slots = {item["slot"]: item for item in master["vertex_buffers"]}
    seen = set()
    for item in buffers:
        if not isinstance(item, dict):
            return "vertex buffer entry is not an object"
        slot = item.get("slot")
        resource_id = item.get("resource_id")
        if type(slot) is not int or slot < 0 or slot > 3 or slot in seen:
            return "vertex buffer slot is invalid or duplicated"
        seen.add(slot)
        if not isinstance(resource_id, str) or not resource_id:
            return "vertex buffer resource_id is missing"
        master_slot = master_slots.get(slot)
        if master_slot is None:
            return f"slot {slot} is not bound in the draw master"
        stride = item.get("stride")
        offset = item.get("offset")
        count = item.get("count")
        if type(stride) is not int or type(offset) is not int or type(count) is not int:
            return f"slot {slot} stride/offset/count is not an integer"
        if stride != master_slot["stride"] or offset != master_slot["offset"]:
            return f"slot {slot} stride/offset differs from the draw master"
        if slot == 0 and count != ctx["effective_count"]:
            return "slot 0 count differs from the effective draw count"
        size = item.get("contents_size")
        sha = item.get("contents_sha256")
        sidecar = item.get("sidecar")
        method = item.get("method")
        if type(size) is not int or size <= 0 or not isinstance(sha, str) \
                or len(sha) != 64 or not isinstance(sidecar, str):
            return f"slot {slot} contents metadata is incomplete"
        path = _resolve_payload(sidecar, [packet_dir])
        if path is None:
            return f"slot {slot} sidecar is missing from the packet"
        data = path.read_bytes()
        if len(data) != size or _sha(path) != sha:
            return f"slot {slot} sidecar hash/size mismatch"
        if size < offset + count * stride:
            return f"slot {slot} contents do not cover the bound range"
        if method == "resource_lifecycle":
            expected = _effective_buffer(ctx["artifact"], resource_id, ctx["target"].call_id)
            if expected is None or expected != data:
                return f"slot {slot} resource bytes do not match the capture lifecycle"
        elif method != "draw_time":
            return f"slot {slot} has an unsupported contents method"
    if 0 not in seen:
        return "slot 0 vertex contents are required"
    return None


def _validate_matrix(value: dict, ctx: dict, packet_dir: Path) -> str | None:
    if value.get("slot") != 0 or value.get("size") != 64:
        return "matrix_constant must be slot 0 and 64 bytes"
    hex_text = value.get("hex")
    sha = value.get("sha256")
    sidecar = value.get("sidecar")
    if not isinstance(hex_text, str) or len(hex_text) != 128 or not isinstance(sha, str):
        return "matrix_constant hex/hash is incomplete"
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError:
        return "matrix_constant hex is invalid"
    if len(raw) != 64 or hashlib.sha256(raw).hexdigest() != sha:
        return "matrix_constant bytes do not match the declared hash"
    path = _resolve_payload(str(sidecar or ""), [packet_dir])
    if path is None or path.read_bytes() != raw:
        return "matrix_constant sidecar is missing or differs"
    constants = [item for item in ctx["master"]["constants"] if item.get("slot") == 0]
    if not constants or int(constants[0].get("vec4_count", 0)) < 4:
        return "draw master has no 64-byte constant slot 0"
    return None


def _validate_bindings(value: dict, ctx: dict) -> str | None:
    slots = value.get("slots")
    if not isinstance(slots, list):
        return "vertex_buffer_bindings has no slots"
    slot0 = next((item for item in slots if isinstance(item, dict) and item.get("slot") == 0), None)
    if slot0 is None or not isinstance(slot0.get("resource_id"), str) or not slot0["resource_id"]:
        return "vertex_buffer_bindings slot 0 has no resource_id"
    master_slot = next(
        (item for item in ctx["master"]["vertex_buffers"] if item["slot"] == 0), None
    )
    if master_slot is None:
        return "draw master has no slot 0 vertex buffer"
    if slot0.get("stride") != master_slot["stride"] or slot0.get("offset") != master_slot["offset"]:
        return "vertex_buffer_bindings slot 0 differs from the draw master"
    if slot0.get("count") != ctx["effective_count"]:
        return "vertex_buffer_bindings slot 0 count differs from the effective count"
    return None


def validate_packet(
    packet: dict, packet_dir: Path, ctx: dict, config: dict
) -> tuple[dict | None, str | None]:
    if packet.get("schema") != trusted_packet.SCHEMA:
        return None, "packet schema mismatch"
    target = packet.get("target")
    target_call = None
    if isinstance(target, dict):
        target_call = target.get("call_id")
        if target_call is None and isinstance(target.get("invocation"), dict):
            target_call = target["invocation"].get("call_id")
    if not isinstance(target, dict) or target_call != ctx["target"].call_id:
        return None, "packet target call_id differs from the capture"
    if target.get("caller_rva") != "0xedfcd1":
        return None, "packet target caller differs from 0xedfcd1"
    if target.get("effective_count") != ctx["effective_count"] or target.get("mode") != 5:
        return None, "packet target draw parameters differ from the capture"
    provenance = packet.get("provenance") or {}
    for key, value in (
        ("session", ctx["artifact"].session),
        ("pid", ctx["artifact"].pid),
        ("executable_sha256", ctx["artifact"].executable_sha256),
        ("image_base", f"0x{ctx['artifact'].image_base:x}"),
    ):
        if provenance.get(key) != value:
            return None, f"packet provenance {key} differs from the capture"
    inputs = packet.get("inputs")
    if not isinstance(inputs, dict):
        return None, "packet inputs are missing"
    required = [str(item) for item in config["required_inputs"]]
    established = []
    missing = []
    for name in required:
        item = inputs.get(name)
        if not isinstance(item, dict) or item.get("status") != "established":
            missing.append(name)
            continue
        established.append(name)
        value = item.get("value")
        if not isinstance(value, dict):
            return None, f"{name} has no value"
        payloads, error = _evidence_bytes(
            item.get("evidence") or [], ctx["artifact"].root, packet_dir,
            ctx["artifact"], ctx["target"].call_id,
        )
        if error:
            return None, f"{name}: {error}"
        if name == "vertex_buffer_contents":
            error = _validate_vertex_contents(value, ctx, packet_dir)
        elif name == "matrix_constant":
            error = _validate_matrix(value, ctx, packet_dir)
        elif name == "vertex_buffer_bindings":
            error = _validate_bindings(value, ctx)
        else:
            error = None
        if error:
            return None, error
    if missing:
        return {"established": established, "missing": missing}, None
    if packet.get("packet_ready") is True:
        all_inputs = packet.get("inputs") or {}
        unresolved = [
            name for name in trusted_packet.REQUIRED_INPUTS
            if (all_inputs.get(name) or {}).get("status") != "established"
        ]
        if unresolved:
            return None, "packet_ready=true while required inputs remain unresolved"
    return {"established": established, "missing": []}, None


def _blocker(workspace: Path, config: dict) -> dict | None:
    path = workspace / str(config.get(
        "blocker_path", "analysis/gfx-draw/v0/l32b/effective-state/blockers.json"
    ))
    if not path.is_file():
        return None
    try:
        value = _json(path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("reason_code") != "semantic_blocker":
        return None
    if not value.get("dependency") or not isinstance(value.get("evidence"), list) \
            or not value["evidence"]:
        return None
    return value


def check(workspace: Path, campaign_dir: Path) -> dict:
    workspace = Path(workspace).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    try:
        campaign, config = load_config(campaign_dir)
        pin = str(campaign["workspace"]["commit"])
        campaign_id = str(campaign["id"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"campaign configuration unreadable: {exc}")
    problem = worktree_problem(workspace, pin)
    if problem:
        return emit(problem[0], problem[1])
    manifest_error = manifest_problem(campaign_dir, config, pin)
    if manifest_error:
        return emit("unavailable", manifest_error)
    try:
        paths = changed_paths(workspace)
    except OSError as exc:
        return emit("unavailable", f"worktree status unavailable: {exc}")
    allowed = [str(item) for item in config["allowed_paths"]]
    illegal = [path for path in paths if not path_allowed(path, allowed)]
    if illegal:
        return emit("fail", "source scope violated: " + ", ".join(illegal[:8]))
    frozen = [str(item) for item in config.get("forbidden_paths", [])]
    touched = [path for path in paths if any(
        path == pattern or path.startswith(pattern.rstrip("/") + "/")
        for pattern in frozen
    )]
    if touched:
        return emit("fail", "frozen path modified: " + ", ".join(touched[:8]))

    candidate_sha, failure = build_candidate(workspace)
    if failure:
        return failure
    failure = run_frozen_host(workspace, campaign_dir)
    if failure:
        return failure
    failure = run_candidate_tests(workspace, [str(item) for item in config["test_modules"]])
    if failure:
        return failure

    run_root = repo_root(campaign_dir) / ".local" / "automation" / campaign_id
    limit = int(config.get("launch_limit", 3))
    launch, failure = read_launch(run_root, limit)
    if failure and failure["verdict"] != "inconclusive":
        return failure
    used = int(launch.get("used", 0)) if launch else 0
    remaining = max(0, limit - used)
    if launch is not None:
        capture_dir, selection, failure = select_capture(run_root, config, candidate_sha)
        if failure:
            return failure
        if capture_dir is None:
            return emit(
                "inconclusive",
                _diagnostic(
                    reason="no_candidate_capture",
                    captures_used=used,
                    captures_remaining=remaining,
                    next_investigation="run one campaign capture when runtime evidence is required",
                ),
            )
        request = _capture_request(capture_dir)
        if request is None:
            return emit("unavailable", "capture request is missing")
        ctx, failure = validate_capture(capture_dir, candidate_sha, request)
        if failure:
            return failure
        packet, packet_dir, failure = run_candidate_packet(workspace, capture_dir)
        if failure:
            return failure
        try:
            validated, error = validate_packet(packet, packet_dir, ctx, config)
        finally:
            shutil.rmtree(packet_dir.parent, ignore_errors=True)
        if error:
            return emit("fail", error)
        if validated["missing"]:
            blocker = _blocker(workspace, config)
            if blocker is not None:
                return emit(
                    "blocked",
                    _diagnostic(
                        reason="semantic_blocker",
                        dependency=blocker.get("dependency"),
                        missing=validated["missing"],
                        captures_remaining=remaining,
                    ),
                )
            if used >= limit:
                return emit(
                    "blocked",
                    _diagnostic(
                        reason="campaign_budget_exhausted",
                        missing=validated["missing"],
                        captures_used=used,
                        captures_remaining=0,
                    ),
                )
            return emit(
                "inconclusive",
                _diagnostic(
                    reason="required_inputs_missing",
                    established=validated["established"],
                    missing=validated["missing"],
                    captures_used=used,
                    captures_remaining=remaining,
                    next_investigation=INPUT_HINTS.get(
                        validated["missing"][0], "investigate the missing dependency"
                    ),
                ),
            )
        return emit(
            "pass",
            _diagnostic(
                milestone=config["milestone"],
                established=validated["established"],
                captures_used=used,
            ),
        )

    return emit(
        "inconclusive",
        _diagnostic(
            reason="no_campaign_launch_counter",
            captures_used=0,
            captures_remaining=remaining,
            next_investigation="implement and test offline evidence first",
        ),
    )
