"""Trusted acceptance v2 for agent-driven draw-input campaigns.

This is an immutable successor to ``automation/gfx_draw_accept.py``. The v1
module stays byte-identical because the completed L3.2b-I campaign's
verification manifest pins it. New campaigns use this revision.

v2 adds fail-closed field dispatch, base validators for the four established
inputs, durable provisional checkpoints, a workflow-level launch ledger and a
trusted qualification receipt. It never launches EU4 and never trusts a
candidate ``packet_ready`` claim.
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
from automation import launch_ledger  # noqa: E402
from automation.gfx_checkpoint import (  # noqa: E402
    CheckpointError,
    source_tree_digest,
    validate_checkpoint,
    write_checkpoint,
    write_qualified_bundle,
)
from automation.gfx_contracts import (  # noqa: E402
    ContractContext,
    ContractError,
    load_contract,
    merge_validators,
)
from automation.gfx_evidence_policy import evidence_relevant  # noqa: E402
from automation.gfx_contracts.api import (  # noqa: E402
    record_for,
    require_precedes,
    require_record,
    resolve_packet_payload,
    sha256_bytes,
    verify_static_span,
)
from automation.state import repo_root, require_worktree  # noqa: E402
from automation.manifest_revision import (  # noqa: E402
    is_ancestor,
    verify_manifest_at_commit,
)
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
    "draw_invocation": "selected draw invocation and master identity",
    "primitive_semantics": "engine primitive enum mapped to the GL mode",
    "viewport": "effective viewport words at the draw",
    "render_target": "observed target identity and declared replay assumptions",
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
    template = campaign_dir
    if isinstance(campaign.get("template"), str) and campaign["template"]:
        candidate = Path(campaign["template"])
        if not candidate.is_absolute():
            candidate = (repo_root(campaign_dir) / candidate).resolve()
        template = candidate
    config = _json(template / "l32b.json")
    required = ("milestone", "pinned_commit", "required_inputs", "allowed_paths",
                "test_modules", "manifest")
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError("l32b.json missing " + ", ".join(missing))
    campaign["_template_dir"] = str(template)
    return campaign, config


def campaign_template(campaign: dict, campaign_dir: Path) -> Path:
    raw = campaign.get("_template_dir")
    return Path(raw).resolve() if raw else Path(campaign_dir).resolve()


def trusted_contract(campaign: dict, config: dict) -> dict:
    raw = campaign.get("trusted_contract")
    if isinstance(raw, dict):
        return {
            "commit": str(raw.get("commit") or campaign["workspace"]["commit"]),
            "version": str(raw.get("contract_version")
                           or config.get("contract_version", "v0")),
            "sha256": raw.get("contract_sha256") or config.get("contract_sha256"),
        }
    return {
        "commit": str(campaign["workspace"]["commit"]),
        "version": str(config.get("contract_version", "v0")),
        "sha256": config.get("contract_sha256"),
    }


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


def manifest_problem(
    campaign_dir: Path,
    config: dict,
    pin: str,
    campaign: dict | None = None,
) -> str | None:
    template = campaign_template(campaign, campaign_dir) if campaign else Path(campaign_dir)
    path = template / str(config["manifest"])
    if not path.is_file():
        return f"verification manifest missing: {path.name}"
    try:
        manifest = load_manifest(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"verification manifest unreadable: {exc}"
    new_identity = bool(campaign and isinstance(campaign.get("trusted_contract"), dict))
    trusted = trusted_contract(campaign, config) if campaign else {
        "commit": pin, "version": "v0", "sha256": None,
    }
    if new_identity:
        errors = verify_manifest_at_commit(ROOT, manifest, trusted["commit"])
        if errors:
            return "verification manifest: " + "; ".join(errors[:6])
        baseline = manifest.get("repo_baseline_commit")
        if not isinstance(baseline, str) or not is_ancestor(ROOT, baseline, pin):
            return "verification manifest baseline is not an ancestor of the campaign pin"
    else:
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


def _ledger_path(run_root: Path, config: dict | None) -> tuple[Path | None, str | None]:
    if config and isinstance(config.get("workflow_ledger"), str) \
            and config["workflow_ledger"]:
        return Path(config["workflow_ledger"]), None
    try:
        pointer = launch_ledger.read_pointer(run_root)
    except launch_ledger.LedgerError as exc:
        return None, str(exc)
    if pointer is None:
        return None, None
    return Path(pointer["ledger"]), None


def read_launch(
    run_root: Path, limit: int, config: dict | None = None
) -> tuple[dict | None, dict | None]:
    ledger_path, error = _ledger_path(run_root, config)
    if error:
        return None, emit("unavailable", f"workflow ledger pointer is malformed: {error}")
    if ledger_path is not None:
        try:
            ledger = launch_ledger.load_ledger(ledger_path)
        except launch_ledger.LedgerError as exc:
            return None, emit("unavailable", f"workflow ledger is malformed: {exc}")
        receipts = launch_ledger.campaign_receipts(ledger, run_root.name)
        if len(receipts) > limit:
            return None, emit(
                "unavailable",
                "campaign receipts exceed the declared per-campaign launch limit",
            )
        return {
            "schema": launch_ledger.LEDGER_SCHEMA,
            "campaign_id": run_root.name,
            "used": len(receipts),
            "limit": limit,
            "launches": receipts,
        }, None
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


def _select_receipt(
    receipt: dict,
    run_root: Path,
    captures_root: Path,
    plans_root: Path,
    candidate_sha: str,
) -> tuple[Path | None, dict | None, dict | None, bool]:
    """Return (capture, metadata, failure, stale)."""
    output = Path(str(receipt.get("output", ""))).expanduser()
    if not output.is_absolute():
        output = (run_root / output).resolve()
    else:
        output = output.resolve()
    if not _under(output, captures_root):
        return None, None, emit(
            "fail",
            _diagnostic(reason="capture_outside_accounted_root", output=str(output)),
        ), False
    if not output.is_dir():
        return None, None, emit(
            "unavailable",
            _diagnostic(reason="accounted_capture_missing", output=str(output)),
        ), False
    plan = Path(str(receipt.get("capture_plan") or ""))
    if not plan.is_file():
        plan = plans_root / f"{output.name}.json"
    if not plan.is_file():
        return None, None, emit(
            "fail",
            _diagnostic(reason="capture_plan_missing", output=str(output)),
        ), False
    request = _capture_request(output)
    if request is None:
        return None, None, emit(
            "unavailable",
            _diagnostic(reason="capture_request_missing", output=str(output)),
        ), False
    library = str(request.get("library_sha256") or "")
    if library != candidate_sha:
        return None, None, None, True
    try:
        plan_value = _json(plan)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, None, emit(
            "fail", _diagnostic(reason="capture_plan_invalid", detail=str(exc))
        ), False
    if not isinstance(plan_value, dict) or not plan_value.get("question") \
            or not plan_value.get("expected_observation"):
        return None, None, emit(
            "fail", _diagnostic(reason="capture_plan_incomplete", output=str(output))
        ), False
    return output, {
        "receipt": receipt,
        "plan": plan_value,
        "plan_path": str(plan),
        "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
    }, None, False


def select_capture(
    run_root: Path,
    config: dict,
    candidate_sha: str,
    launch: dict | None = None,
) -> tuple[Path | None, dict | None, dict | None]:
    captures_root = run_root / str(config.get("capture_root", "captures"))
    plans_root = run_root / str(config.get("capture_plans", "capture-plans"))
    ledger_path, error = _ledger_path(run_root, config)
    if error:
        return None, None, emit("unavailable", f"workflow ledger pointer is malformed: {error}")
    if ledger_path is not None:
        try:
            ledger = launch_ledger.load_ledger(ledger_path)
        except launch_ledger.LedgerError as exc:
            return None, None, emit("unavailable", f"workflow ledger is malformed: {exc}")
        receipts = launch_ledger.campaign_receipts(
            ledger, run_root.name, status="completed"
        )
        stale: list[str] = []
        for receipt in reversed(receipts):
            capture, metadata, failure, was_stale = _select_receipt(
                receipt, run_root, captures_root, plans_root, candidate_sha
            )
            if failure:
                return None, None, failure
            if was_stale:
                stale.append(Path(str(receipt.get("output", ""))).name)
                continue
            if capture is not None:
                return capture, metadata, None
        if stale:
            return None, {"stale": stale}, emit(
                "inconclusive",
                _diagnostic(
                    reason="no_capture_from_current_observer_build",
                    next_investigation="rebuild the capture with the current observer or revert the post-capture edits",
                ),
            )
        return None, None, None
    launch_path = run_root / "live-launch.json"
    if not launch_path.is_file():
        return None, None, None
    try:
        legacy = _json(launch_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None, None
    receipts = legacy.get("launches") or []
    stale: list[str] = []
    for receipt in reversed(receipts):
        if not isinstance(receipt, dict):
            continue
        capture, metadata, failure, was_stale = _select_receipt(
            receipt, run_root, captures_root, plans_root, candidate_sha
        )
        if failure:
            return None, None, failure
        if was_stale:
            stale.append(Path(str(receipt.get("output", ""))).name)
            continue
        if capture is not None:
            return capture, metadata, None
    if stale:
        return None, {"stale": stale}, emit(
            "inconclusive",
            _diagnostic(
                reason="no_capture_from_current_observer_build",
                captures_remaining=int(legacy.get("limit", 0)) - int(legacy.get("used", 0)),
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


def _evidence_bytes(evidence: list, ctx: dict, packet_dir: Path) -> tuple[list[Path], str | None]:
    artifact = ctx["artifact"]
    target = ctx["target"]
    capture_dir = artifact.root
    payloads: list[Path] = []
    target_payload = target.payload_bytes
    resolved = ctx.setdefault("evidence_paths", {})
    for item in evidence:
        if not isinstance(item, dict):
            return [], "evidence entry is not an object"
        kind = item.get("type")
        if kind == "draw_snapshot":
            if item.get("call_id") != target.call_id:
                return [], "draw snapshot evidence is not the selected draw"
            if target_payload is None:
                return [], "selected draw has no captured master payload"
            offset = item.get("offset", 0)
            length = item.get("length", len(target_payload))
            if type(offset) is not int or type(length) is not int \
                    or offset < 0 or length < 0:
                return [], "draw snapshot offset/length is invalid"
            if offset + length > len(target_payload):
                return [], "draw snapshot slice is outside the selected draw payload"
            payload = _resolve_payload(str(item.get("payload", "")), [capture_dir, packet_dir])
            if payload is None:
                return [], "draw snapshot payload is missing"
            data = payload.read_bytes()
            if hashlib.sha256(data).hexdigest() != item.get("sha256") \
                    or len(data) != item.get("size"):
                return [], "draw snapshot hash/size mismatch"
            if data != target_payload[offset:offset + length]:
                return [], "draw snapshot bytes differ from the selected draw record"
            resolved[str(item.get("payload"))] = payload
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
            if record.payload_bytes is None:
                return [], "resource observation has no original payload"
            payload = _resolve_payload(str(item.get("payload", "")), [capture_dir, packet_dir])
            if payload is None:
                return [], "resource observation payload is missing"
            data = payload.read_bytes()
            if hashlib.sha256(data).hexdigest() != item.get("sha256") \
                    or len(data) != item.get("size"):
                return [], "resource observation payload hash/size mismatch"
            if data != record.payload_bytes:
                return [], "resource observation payload differs from the original operation"
            resolved[str(item.get("payload"))] = payload
            payloads.append(payload)
        elif kind == "draw_record":
            record = next(
                (row for row in artifact.operations
                 if row.call_id == item.get("call_id")),
                None,
            )
            if record is None or record.symbol != "_GfxDraw":
                return [], "draw record evidence is not the selected draw"
            if item.get("call_id") != target.call_id:
                return [], "draw record evidence is not the selected draw"
            if item.get("caller_rva") is not None \
                    and int(item["caller_rva"]) != int(record.caller_rva):
                return [], "draw record caller differs from the capture"
            if item.get("thread_id") is not None \
                    and int(item["thread_id"]) != int(record.thread_id):
                return [], "draw record thread differs from the capture"
            if item.get("completion_seq") is not None \
                    and int(item["completion_seq"]) != int(record.completion_seq):
                return [], "draw record completion order differs from the capture"
        elif kind == "primitive_set":
            record = next(
                (row for row in artifact.operations
                 if row.call_id == item.get("call_id")),
                None,
            )
            if record is None or record.symbol != "_GfxSetPrimitiveType":
                return [], "primitive evidence is not a _GfxSetPrimitiveType record"
            if record.call_id >= target.call_id:
                return [], "primitive evidence does not precede the draw"
            observed = record.meta.get("primitive_enum")
            if type(observed) is int and observed != item.get("primitive_enum"):
                return [], "primitive enum differs from the original record"
        elif kind == "static_table":
            rel = item.get("path")
            if not isinstance(rel, str) or not rel:
                return [], "static table evidence has no path"
            candidate = Path(rel)
            if candidate.is_absolute() or ".." in candidate.parts:
                return [], "static table evidence path is invalid"
            path = (ROOT / candidate).resolve()
            try:
                path.relative_to(ROOT.resolve())
            except ValueError:
                return [], "static table evidence path escapes the repository"
            if not path.is_file():
                return [], "static table evidence file is missing"
            expected = ctx.get("manifest_members", {}).get(candidate.as_posix())
            if expected is None:
                return [], "static table evidence is not in the pinned manifest"
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return [], "static table evidence differs from the manifest"
            ctx.setdefault("frozen_evidence", {})[candidate.as_posix()] = path
        elif kind == "static_reconstruction":
            error = verify_static_span(item, ContractContext(
                artifact=artifact,
                target=target,
                master=ctx["master"],
                packet_dir=packet_dir,
                capture_dir=capture_dir,
                config=ctx.get("config") or {},
                repo=ROOT,
                target_metadata=ctx.get("target_metadata") or {},
                manifest_members=ctx.get("manifest_members") or {},
            ))
            if error:
                return [], error
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


def _create_for_header(artifact, header: int, draw_call_id: int):
    found = None
    for record in artifact.operations:
        if record.call_id >= draw_call_id:
            continue
        if record.symbol != "_GfxCreateVertexBuffer":
            continue
        if record.meta.get("header") == header:
            found = record
    return found


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
        create = _create_for_header(ctx["artifact"], master_slot["header"], ctx["target"].call_id)
        if create is None or not create.resource_id:
            return f"slot {slot} header has no preceding create record"
        if resource_id != create.resource_id:
            return f"slot {slot} resource_id differs from the header's create record"
        resource_call_id = item.get("resource_call_id", item.get("first_call_id"))
        if resource_call_id is not None and resource_call_id != create.call_id:
            return f"slot {slot} resource call id differs from the create record"
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
        if method != "resource_lifecycle":
            return f"slot {slot} uses unsupported contents method {method!r}"
        expected = _effective_buffer(ctx["artifact"], resource_id, ctx["target"].call_id)
        if expected is None or expected != data:
            return f"slot {slot} resource bytes do not match the capture lifecycle"
    if 0 not in seen:
        return "slot 0 vertex contents are required"
    return None


def _validate_matrix(value: dict, ctx: dict, packet_dir: Path) -> str | None:
    if value.get("method") != "resource_observation":
        return "matrix_constant must use a resource_observation"
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
    record = next(
        (row for row in ctx["artifact"].operations
         if row.call_id == value.get("last_call_id")),
        None,
    )
    if record is None or record.symbol != "_GfxSetConstantBuffer":
        return "matrix_constant has no original constant-buffer record"
    if not record.fields_ok or not record.contents_known \
            or record.call_id >= ctx["target"].call_id:
        return "matrix_constant record is not a usable preceding observation"
    if record.meta.get("slot") != 0:
        return "matrix_constant record is not slot 0"
    if record.meta.get("header") != constants[0].get("pointer"):
        return "matrix_constant record header differs from the draw master"
    if ctx["target"].meta.get("constant_slot0_match") is not True:
        return "draw did not cross-check constant slot 0 at draw time"
    if ctx["target"].meta.get("constant_slot0_call_id") != record.call_id:
        return "matrix_constant record is not the draw-time-matched call"
    if record.payload_bytes is None or len(record.payload_bytes) < 64 \
            or record.payload_bytes[:64] != raw:
        return "matrix_constant bytes differ from the original constant record"
    return None


def _validate_bindings(value: dict, ctx: dict) -> str | None:
    slots = value.get("slots")
    if not isinstance(slots, list):
        return "vertex_buffer_bindings has no slots"
    master_slots = {item["slot"]: item for item in ctx["master"]["vertex_buffers"]}
    claimed = {}
    for item in slots:
        if not isinstance(item, dict):
            return "vertex_buffer_bindings slot is not an object"
        slot = item.get("slot")
        if type(slot) is not int or slot < 0 or slot > 3 or slot in claimed:
            return "vertex_buffer_bindings slot is invalid or duplicated"
        master_slot = master_slots.get(slot)
        if master_slot is None:
            return f"vertex_buffer_bindings slot {slot} is not bound in the draw master"
        create = _create_for_header(ctx["artifact"], master_slot["header"], ctx["target"].call_id)
        if create is None or not create.resource_id:
            return f"vertex_buffer_bindings slot {slot} header has no create record"
        if item.get("resource_id") != create.resource_id:
            return f"vertex_buffer_bindings slot {slot} resource_id differs from its header"
        if item.get("resource_call_id") != create.call_id:
            return f"vertex_buffer_bindings slot {slot} resource_call_id differs from its header"
        if item.get("stride") != master_slot["stride"] or item.get("offset") != master_slot["offset"]:
            return f"vertex_buffer_bindings slot {slot} differs from the draw master"
        if slot == 0 and item.get("count") != ctx["effective_count"]:
            return "vertex_buffer_bindings slot 0 count differs from the effective count"
        claimed[slot] = item
    if 0 not in claimed:
        return "vertex_buffer_bindings slot 0 is missing"
    for slot in master_slots:
        if slot not in claimed:
            return f"vertex_buffer_bindings omits bound slot {slot}"
    return None


def _validate_draw_invocation(value: dict, ctx: dict) -> str | None:
    target = ctx["target"]
    master = ctx["master"]
    if value.get("call_id") != target.call_id:
        return "draw_invocation call id differs from the selected draw"
    caller = value.get("caller_rva")
    if isinstance(caller, str):
        try:
            caller = int(caller, 0)
        except ValueError:
            return "draw_invocation caller is not an RVA"
    if caller != CALLER_RVA:
        return "draw_invocation caller differs from 0xedfcd1"
    if value.get("mode") != 5 or master.get("mode") != 5:
        return "draw_invocation mode is not GL_TRIANGLE_STRIP"
    if value.get("effective_count") != ctx["effective_count"]:
        return "draw_invocation effective count differs from the capture"
    expected_sha = master.get("sha256")
    if value.get("master_sha256") not in (None, expected_sha):
        return "draw_invocation master hash differs from the capture"
    if value.get("thread_id") is not None and int(value["thread_id"]) != int(target.thread_id):
        return "draw_invocation thread differs from the capture"
    if value.get("completion_seq") is not None \
            and int(value["completion_seq"]) != int(target.completion_seq):
        return "draw_invocation completion order differs from the capture"
    return None


def _validate_primitive_semantics(value: dict, ctx: dict) -> str | None:
    engine = value.get("engine_enum")
    if type(engine) is not int or engine < 0:
        return "primitive_semantics engine enum is not an integer"
    if value.get("gl_mode") != 5 or value.get("draw_mode", 5) != 5:
        return "primitive_semantics does not resolve to GL_TRIANGLE_STRIP"
    call_id = value.get("primitive_call_id", value.get("call_id"))
    record = next(
        (row for row in ctx["artifact"].operations
         if row.call_id == call_id and row.symbol == "_GfxSetPrimitiveType"),
        None,
    )
    if record is None:
        return "primitive_semantics has no preceding _GfxSetPrimitiveType record"
    if record.call_id >= ctx["target"].call_id:
        return "primitive_semantics record does not precede the draw"
    observed = record.meta.get("primitive_enum")
    if type(observed) is int and observed != engine:
        return "primitive_semantics enum differs from the original record"
    table_path = value.get("table_path") or "analysis/qualification/v0/primitive-table.evidence.json"
    table = ctx.get("frozen_evidence", {}).get(table_path)
    if table is None:
        return "primitive_semantics static table is not in the pinned manifest"
    try:
        payload = json.loads(Path(table).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"primitive_semantics static table is unreadable: {exc}"
    mapping = (payload.get("output") or {}).get("mapping") or {}
    expected = mapping.get(str(engine))
    if expected != 5:
        return "primitive_semantics static table does not map the enum to GL mode 5"
    return None


def _validate_viewport(value: dict, ctx: dict) -> str | None:
    master = ctx["master"]
    if value.get("words") != master.get("viewport_words"):
        return "viewport words differ from the draw master"
    if value.get("gl_viewport") != master.get("gl_viewport"):
        return "viewport GL rectangle differs from the draw master"
    return None


def _validate_render_target(value: dict, ctx: dict) -> str | None:
    master = ctx["master"]
    observed = value.get("observed")
    if not isinstance(observed, dict):
        return "render_target has no observed properties"
    if observed.get("master_render_target") != master.get("render_target"):
        return "render_target observed target differs from the draw master"
    if observed.get("window") != master.get("window"):
        return "render_target window differs from the draw master"
    if master.get("render_target") == 0 \
            and observed.get("kind") != "default_backbuffer":
        return "render_target does not identify the default backbuffer"
    assumptions = value.get("assumptions")
    if not isinstance(assumptions, list) or not assumptions:
        return "render_target has no declared replay assumptions"
    properties = {
        item.get("property")
        for item in assumptions
        if isinstance(item, dict)
    }
    for required in ("format", "initial_contents"):
        if required not in properties:
            return f"render_target is missing the {required} replay assumption"
    return None


BASE_VALIDATORS = {
    "draw_invocation": _validate_draw_invocation,
    "primitive_semantics": _validate_primitive_semantics,
    "viewport": _validate_viewport,
    "render_target": _validate_render_target,
    "vertex_buffer_bindings": lambda value, ctx: _validate_bindings(value, ctx),
    "vertex_buffer_contents": lambda value, ctx: _validate_vertex_contents(
        value, ctx, ctx["packet_dir"]
    ),
    "matrix_constant": lambda value, ctx: _validate_matrix(
        value, ctx, ctx["packet_dir"]
    ),
}


def _field_digest(item: dict) -> str | None:
    if not isinstance(item, dict):
        return None
    body = {"value": item.get("value"), "evidence": item.get("evidence")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _contract_context(
    ctx: dict,
    packet_dir: Path,
    config: dict,
    *,
    field_name: str | None = None,
    field_evidence: list | None = None,
) -> ContractContext:
    paths = ctx.setdefault("evidence_paths", {})
    return ContractContext(
        artifact=ctx["artifact"],
        target=ctx["target"],
        master=ctx["master"],
        packet_dir=Path(packet_dir),
        capture_dir=Path(ctx["artifact"].root),
        config=config,
        repo=ROOT,
        target_metadata=ctx.get("target_metadata") or {},
        evidence_paths=paths,
        frozen_evidence=ctx.get("frozen_evidence") or {},
        manifest_members=ctx.get("manifest_members") or {},
        field_name=field_name,
        field_evidence=list(field_evidence or []),
        field_evidence_paths=paths,
    )


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
    contract = ctx.get("contract")
    try:
        validators = merge_validators(BASE_VALIDATORS, contract) if contract else dict(BASE_VALIDATORS)
    except ContractError as exc:
        return None, f"contract registry: {exc}"
    ctx["packet_dir"] = Path(packet_dir)
    ctx.setdefault("evidence_paths", {})
    states: dict[str, str] = {}
    established: list[str] = []
    missing: list[str] = []
    provisional: list[str] = []
    invalid: list[str] = []
    for name in required:
        item = inputs.get(name)
        if not isinstance(item, dict) or item.get("status") != "established":
            states[name] = "missing"
            missing.append(name)
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            return None, f"{name} has no value"
        evidence = item.get("evidence") or []
        _, error = _evidence_bytes(evidence, ctx, packet_dir)
        if error:
            return None, f"{name}: {error}"
        relevant, basis = evidence_relevant(name, evidence, ctx)
        if not relevant:
            return None, f"{name}: established without field-relevant evidence: {basis}"
        validator = validators.get(name)
        if validator is None:
            states[name] = "provisional"
            provisional.append(name)
            continue
        try:
            if name in BASE_VALIDATORS:
                error = validator(value, ctx)
            else:
                field_ctx = _contract_context(
                    ctx,
                    packet_dir,
                    config,
                    field_name=name,
                    field_evidence=evidence,
                )
                error = validator(value, field_ctx)
        except Exception as exc:
            return None, f"{name}: validator_error: {exc}"
        if error:
            return None, f"{name}: {error}"
        states[name] = "validated"
        established.append(name)
    bindings_item = inputs.get("vertex_buffer_bindings") or {}
    contents_item = inputs.get("vertex_buffer_contents") or {}
    if bindings_item.get("status") == "established" \
            and contents_item.get("status") == "established":
        binding_slots = {
            item.get("slot"): item
            for item in (bindings_item.get("value") or {}).get("slots", [])
            if isinstance(item, dict)
        }
        content_slots = {
            item.get("slot"): item
            for item in (contents_item.get("value") or {}).get("buffers", [])
            if isinstance(item, dict)
        }
        if set(binding_slots) != set(content_slots):
            return None, "vertex buffer bindings and contents cover different slots"
        for slot, binding in binding_slots.items():
            content = content_slots[slot]
            if binding.get("resource_id") != content.get("resource_id"):
                return None, f"slot {slot} binding and contents resource ids differ"
            if content.get("resource_call_id", content.get("first_call_id")) \
                    != binding.get("resource_call_id"):
                return None, f"slot {slot} binding and contents creation calls differ"
    if packet.get("packet_ready") is True and (missing or provisional):
        return None, "packet_ready=true while required inputs remain unresolved"
    if config.get("require_packet_ready") and not missing and not provisional \
            and packet.get("packet_ready") is not True:
        return None, "packet_ready must be true when every required input is validated"
    return {
        "established": established,
        "missing": missing,
        "provisional": provisional,
        "invalid": invalid,
        "states": states,
    }, None


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


def _load_trusted_contract(campaign: dict, config: dict):
    trusted = trusted_contract(campaign, config)
    version = trusted["version"]
    try:
        contract = load_contract(version)
    except ContractError as exc:
        return None, None, emit("unavailable", f"contract unavailable: {exc}")
    for current in contract.versions:
        module_path = ROOT / "automation" / "gfx_contracts" / f"{current}.py"
        if not module_path.is_file():
            return None, None, emit(
                "unavailable", f"contract module {current} is missing from the checkout"
            )
        actual = _sha(module_path)
        if current == version and trusted.get("sha256") \
                and actual != trusted["sha256"]:
            return None, None, emit(
                "unavailable", "contract module hash differs from the campaign pin"
            )
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "show",
             f"{trusted['commit']}:automation/gfx_contracts/{current}.py"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return None, None, emit(
                "unavailable",
                f"contract module {current} is not present at the trusted revision",
            )
        if hashlib.sha256(completed.stdout).hexdigest() != actual:
            return None, None, emit(
                "unavailable",
                f"contract module {current} differs from the trusted revision",
            )
    return contract, trusted, None


def _schema_summary(value: dict) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): type(item).__name__ for key, item in value.items()}


def _evidence_types(item: dict) -> list[str]:
    types = set()
    for evidence in item.get("evidence") or []:
        if isinstance(evidence, dict) and isinstance(evidence.get("type"), str):
            types.add(evidence["type"])
    return sorted(types)


def _checkpoint_fields(
    inputs: dict, required: list[str], states: dict, ctx: dict
) -> list[dict]:
    rows = []
    for name in required:
        item = inputs.get(name)
        if not isinstance(item, dict):
            item = {}
        relevant, basis = evidence_relevant(
            name, item.get("evidence") or [], ctx
        )
        rows.append(
            {
                "name": name,
                "status": item.get("status"),
                "state": states.get(name),
                "value_sha256": _field_digest(item),
                "evidence_types": _evidence_types(item),
                "evidence_relevant": bool(relevant),
                "evidence_basis": basis,
            }
        )
    return rows


def _write_receipt(
    run_root: Path,
    *,
    campaign_id: str,
    config: dict,
    trusted: dict,
    candidate_sha: str,
    candidate_digest: str,
    artifact,
    target,
    master: dict,
    packet: dict,
    packet_dir: Path,
    capture: dict,
    fields: list[dict],
) -> Path:
    receipt_fields = {
        "campaign_id": campaign_id,
        "milestone": config.get("milestone"),
        "contract_version": trusted.get("version"),
        "contract_sha256": trusted.get("sha256"),
        "trusted_contract_commit": trusted.get("commit"),
        "checker_sha256": _sha(Path(__file__)),
        "candidate_digest": candidate_digest,
        "observer_library_sha256": candidate_sha,
        "packet_ready": bool(packet.get("packet_ready")),
        "scope": "L3.2b source-input qualification under the declared replay contract",
        "not_claimed": [
            "original framebuffer contents",
            "GPU execution or pixel equivalence",
            "complete Metal frame",
            "L3.3 completion",
        ],
        "assumptions": packet.get("assumptions") or [],
    }
    bundle = write_qualified_bundle(
        run_root,
        campaign_id=campaign_id,
        candidate_digest=candidate_digest,
        capture=capture,
        packet=packet,
        packet_dir=packet_dir,
        capture_dir=Path(artifact.root),
        target={
            "call_id": target.call_id,
            "caller_rva": f"0x{target.caller_rva:x}",
            "mode": master.get("mode"),
            "effective_count": master.get("effective_count"),
        },
        fields=fields,
        receipt_fields=receipt_fields,
    )
    return Path(bundle["receipt"])


def check(workspace: Path, campaign_dir: Path, *, requalification=None) -> dict:
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
    manifest_error = manifest_problem(campaign_dir, config, pin, campaign)
    if manifest_error:
        return emit("unavailable", manifest_error)
    if requalification is None and isinstance(campaign, dict):
        requalification = campaign.get("requalification")
    if requalification:
        paths = [str(item) for item in requalification.get("changed_paths") or []]
        if not paths:
            return emit("fail", "requalification has no approved changed paths")
    else:
        try:
            paths = changed_paths(workspace)
        except OSError as exc:
            return emit("unavailable", f"worktree status unavailable: {exc}")
    allowed = [str(item) for item in config["allowed_paths"]]
    if requalification and requalification.get("allowed_paths"):
        allowed = [str(item) for item in requalification["allowed_paths"]]
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

    if isinstance(campaign.get("trusted_contract"), dict):
        contract, trusted, failure = _load_trusted_contract(campaign, config)
        if failure:
            return failure
    else:
        # Legacy campaigns (L3.2b-I) predate versioned contracts. They keep
        # the old checker behavior and have no contract module at their pin.
        contract = None
        trusted = {"version": "v0", "sha256": None, "commit": pin}
    template = campaign_template(campaign, campaign_dir)
    try:
        manifest = load_manifest(template / str(config["manifest"]))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"verification manifest unreadable: {exc}")
    manifest_members = {
        str(member.get("path")): str(member.get("sha256"))
        for member in manifest.get("members", [])
        if isinstance(member, dict) and isinstance(member.get("path"), str)
    }
    try:
        target_metadata = _json(ROOT / "reverse_engineering" / "target.json")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"pinned target metadata unreadable: {exc}")

    candidate_sha, failure = build_candidate(workspace)
    if failure:
        return failure
    failure = run_frozen_host(workspace, template)
    if failure:
        return failure
    failure = run_candidate_tests(workspace, [str(item) for item in config["test_modules"]])
    if failure:
        return failure

    run_root = repo_root(campaign_dir) / ".local" / "automation" / campaign_id
    limit = int(config.get("launch_limit", 3))
    if requalification:
        capture_dir = Path(str(requalification.get("capture") or ""))
        selection = requalification.get("selection") or {}
        launch = requalification.get("launch") or {"used": 0}
        used = int(launch.get("used", 0))
        remaining = max(0, limit - used)
        request = _capture_request(capture_dir) if capture_dir.is_dir() else None
    else:
        launch, failure = read_launch(run_root, limit, config)
        if failure and failure["verdict"] != "inconclusive":
            return failure
        used = int(launch.get("used", 0)) if launch else 0
        remaining = max(0, limit - used)
        if launch is None:
            return emit("inconclusive", _diagnostic(reason="no_campaign_launch"))
        capture_dir, selection, failure = select_capture(
            run_root, config, candidate_sha, launch
        )
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
    ctx["contract"] = contract
    ctx["config"] = config
    ctx["target_metadata"] = target_metadata
    ctx["manifest_members"] = manifest_members
    packet, packet_dir, failure = run_candidate_packet(workspace, capture_dir)
    if failure:
        return failure
    temporary = packet_dir.parent
    try:
        validated, error = validate_packet(packet, packet_dir, ctx, config)
        if error:
            return emit("fail", error)
        required = [str(item) for item in config["required_inputs"]]
        states = validated.get("states") or {}
        provisional = validated.get("provisional") or []
        missing = validated.get("missing") or []
        candidate_digest = (
            str(requalification.get("candidate_digest") or "")
            if requalification else source_tree_digest(workspace, paths)
        )
        receipt = selection.get("receipt") if selection else {}
        capture = {
            "capture_dir": str(capture_dir),
            "session": ctx["artifact"].session,
            "pid": ctx["artifact"].pid,
            "executable_sha256": ctx["artifact"].executable_sha256,
            "library_sha256": str(request.get("library_sha256") or ""),
            "receipt_token": receipt.get("token"),
            "capture_plan": str(selection.get("plan_path") or "") if selection else "",
            "capture_plan_sha256": selection.get("plan_sha256") if selection else None,
        }
        if provisional:
            fields = _checkpoint_fields(packet.get("inputs") or {}, required, states, ctx)
            gaps = []
            for name in provisional:
                item = (packet.get("inputs") or {}).get(name) or {}
                gaps.append(
                    {
                        "name": name,
                        "observed_evidence_types": _evidence_types(item),
                        "required_semantics": (
                            "an independently qualified field-specific validator "
                            "for the original evidence"
                        ),
                        "proposed_value_schema": _schema_summary(
                            item.get("value") or {}
                        ),
                    }
                )
            kind = "partial" if missing else "complete_provisional"
            try:
                checkpoint = write_checkpoint(
                    run_root,
                    campaign_id=campaign_id,
                    candidate_base_commit=pin,
                    trusted_contract_commit=trusted["commit"],
                    checker_sha256=_sha(Path(__file__)),
                    contract_version=trusted["version"],
                    contract_sha256=trusted.get("sha256"),
                    manifest_sha256=hashlib.sha256(
                        (template / str(config["manifest"])).read_bytes()
                    ).hexdigest(),
                    candidate_digest=candidate_digest,
                    observer_library_sha256=candidate_sha,
                    capture=capture,
                    packet=packet,
                    packet_dir=packet_dir,
                    capture_dir=capture_dir,
                    target={
                        "call_id": ctx["target"].call_id,
                        "caller_rva": f"0x{ctx['target'].caller_rva:x}",
                        "mode": ctx["master"].get("mode"),
                        "effective_count": ctx["master"].get("effective_count"),
                    },
                    fields=fields,
                    contract_gaps=gaps,
                    checkpoint_kind=kind,
                    blocked_reason="unsupported_evidence_contract",
                )
            except CheckpointError as exc:
                return emit("unavailable", f"checkpoint could not be written: {exc}")
            return emit(
                "checkpoint",
                _diagnostic(
                    reason="unsupported_evidence_contract",
                    provisional=provisional,
                    missing=missing,
                    established=validated.get("established") or [],
                    checkpoint=checkpoint["path"],
                    checkpoint_sha256=checkpoint["sha256"],
                    checkpoint_kind=kind,
                    captures_used=used,
                    captures_remaining=remaining,
                ),
            )
        if missing:
            blocker = _blocker(workspace, config)
            if blocker is not None:
                return emit(
                    "blocked",
                    _diagnostic(
                        reason="semantic_blocker",
                        dependency=blocker.get("dependency"),
                        missing=missing,
                        captures_remaining=remaining,
                    ),
                )
            if used >= limit:
                return emit(
                    "blocked",
                    _diagnostic(
                        reason="campaign_budget_exhausted",
                        missing=missing,
                        captures_used=used,
                        captures_remaining=0,
                    ),
                )
            return emit(
                "inconclusive",
                _diagnostic(
                    reason="required_inputs_missing",
                    established=validated.get("established") or [],
                    missing=missing,
                    states=states,
                    captures_used=used,
                    captures_remaining=remaining,
                    next_investigation=INPUT_HINTS.get(
                        missing[0], "investigate the missing dependency"
                    ),
                ),
            )
        fields = _checkpoint_fields(packet.get("inputs") or {}, required, states, ctx)
        try:
            receipt_path = _write_receipt(
                run_root,
                campaign_id=campaign_id,
                config=config,
                trusted=trusted,
                candidate_sha=candidate_sha,
                candidate_digest=candidate_digest,
                artifact=ctx["artifact"],
                target=ctx["target"],
                master=ctx["master"],
                packet=packet,
                packet_dir=packet_dir,
                capture=capture,
                fields=fields,
            )
        except OSError as exc:
            return emit("unavailable", f"qualification receipt could not be written: {exc}")
        return emit(
            "pass",
            _diagnostic(
                milestone=config["milestone"],
                established=validated.get("established") or [],
                captures_used=used,
                qualification_receipt=str(receipt_path),
                packet_ready=bool(packet.get("packet_ready")),
            ),
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    return emit(
        "inconclusive",
        _diagnostic(
            reason="no_campaign_launch_counter",
            captures_used=0,
            captures_remaining=remaining,
            next_investigation="implement and test offline evidence first",
        ),
    )
