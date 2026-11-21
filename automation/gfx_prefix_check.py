"""Independent exact-prefix qualification for events 0 through 20.

This module is the acceptance authority for the M7 event-20 campaign.  It does
not trust the renderer, the replay report, or the model:

* the sealed frame, its event stream and its opt-in prefix observations are
  verified from disk;
* the checkpoint request is bound to the frame by the shader receipt;
* every prefix comparison is recomputed byte-for-byte from the original-GL
  reference blob and the Metal output file with the exact four-channel
  comparator;
* a missing, stale, foreign or normalized observation is a failure.

It returns a structured verdict.  ``EVENT20_EXACT`` is only possible when every
event from 0 through 20 has a matching, identity-bound, full BGRA observation.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from automation import (
    campaign_guard, dsh_campaign, gfx_frame_graph, gfx_independent_replay,
    m6_compare, m6_frame, replay_host_dependencies, state,
)

ROOT = Path(__file__).resolve().parents[1]


class PrefixCheckError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrefixCheckError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PrefixCheckError(f"not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, problems: list[str], message: str) -> None:
    if not condition:
        problems.append(message)


def _opaque_alpha(payload: bytes) -> bytes:
    data = bytearray(payload)
    for offset in range(3, len(data), 4):
        data[offset] = 255
    return bytes(data)


def classify_prefix(events_out: list[dict], *, problems: list[str],
                    end: int, require_end: int) -> dict:
    """Walk the prefix sequentially and classify only authenticated evidence.

    A missing, invalid or excluded event stops the walk.  A later mismatch may
    not be promoted to ``FIRST_DIVERGENCE`` when an earlier event could not be
    verified, and no exact verdict is possible with an excluded operation.
    """
    rows = {int(item["event"]): item for item in events_out
            if isinstance(item.get("event"), int)}
    verified_end = -1
    first_divergence = None
    unverified_event = None
    for event in range(end + 1):
        row = rows.get(event)
        if row is None:
            unverified_event = event
            break
        status = row.get("status")
        if status == "match":
            verified_end = event
            continue
        if status == "divergent":
            first_divergence = event
            break
        unverified_event = event
        break
    invalid_events = sorted(
        event for event, row in rows.items()
        if row.get("status") not in ("match", "divergent"))
    excluded_events = sorted(
        event for event, row in rows.items()
        if row.get("status") == "unsupported_auxiliary")
    verified_color_events = sorted(
        event for event, row in rows.items() if row.get("status") == "match")
    if problems or unverified_event is not None:
        verdict = "requires_additional_evidence"
        status = "incomplete"
        verified_prefix = ({"start": 0, "end": verified_end}
                           if verified_end >= 0 else None)
        first_divergence = None
    elif first_divergence is not None:
        verdict = "FIRST_DIVERGENCE"
        status = "divergent"
        verified_prefix = ({"start": 0, "end": first_divergence - 1}
                           if first_divergence > 0 else None)
    elif verified_end == end and end >= require_end:
        verdict = "EVENT20_EXACT"
        status = "qualified"
        verified_prefix = {"start": 0, "end": require_end}
    elif verified_end == end:
        verdict = "partial_prefix"
        status = "partial"
        verified_prefix = {"start": 0, "end": end}
    else:
        verdict = "requires_additional_evidence"
        status = "incomplete"
        verified_prefix = None
    return {
        "first_divergence": first_divergence,
        "unverified_event": unverified_event,
        "invalid_events": invalid_events,
        "excluded_events": excluded_events,
        "verified_color_events": verified_color_events,
        "verified_prefix": verified_prefix,
        "verdict": verdict,
        "status": status,
    }


def _tree_manifest(root: Path) -> list[dict]:
    files = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            files.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return files


def _root_digest(files: list[dict]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode())
    return digest.hexdigest()


def resolve_trusted_reference(configured: str | Path | None,
                              candidate_workspace: Path | str | None,
                              *, root: Path | str = ROOT) -> Path:
    """Resolve the one explicitly configured trusted reference root.

    The value comes from trusted campaign configuration.  A missing value, a
    directory without a trusted-evidence record, a directory containing more
    than one seal, or a root inside the candidate workspace is refused.  The
    newest directory is never selected implicitly.
    """
    if configured is None or str(configured).strip() == "":
        raise PrefixCheckError("trusted_reference_root is not configured")
    path = Path(str(configured)).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    if candidate_workspace is not None:
        try:
            path.relative_to(Path(candidate_workspace).expanduser().resolve())
        except ValueError:
            pass
        else:
            raise PrefixCheckError(
                "trusted reference root is inside the candidate workspace")
    if not path.is_dir():
        raise PrefixCheckError(
            f"trusted reference root does not exist: {path}")
    direct = path / "trusted-evidence.json"
    if direct.is_file():
        return path
    nested = sorted(path.glob("*/trusted-evidence.json"))
    if len(nested) == 1:
        return nested[0].parent.resolve()
    if not nested:
        raise PrefixCheckError(
            f"trusted reference root has no trusted-evidence record: {path}")
    raise PrefixCheckError(
        "trusted reference root is ambiguous: more than one sealed evidence "
        "record exists; configure the exact seal directory")


def prepare_trusted_evidence(frame_dir: Path | str, destination: Path | str,
                             *, source_note: str = "",
                             read_only: bool = True, copy: bool = True) -> dict:
    """Copy a sealed frame outside the candidate workspace and bind its bytes.

    The destination is a read-only, digest-bound qualification root.  The
    trusted checker loads only this copy, so candidate rewrites of the original
    worktree cannot change the reference after publication.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if copy:
        target = destination / "frame"
        shutil.copytree(frame_dir, target)
        frame_relative = "frame"
        recorded_frame = None
    else:
        target = frame_dir
        frame_relative = None
        recorded_frame = str(frame_dir)
    files = _tree_manifest(target)
    seal_path = frame_dir / "frame-seal.json"
    original_capture_dir = None
    if seal_path.is_file():
        try:
            original_capture_dir = _read_json(seal_path).get("capture_dir")
        except PrefixCheckError:
            original_capture_dir = None
    record = {
        "schema": "m7-trusted-evidence/v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_note": source_note,
        "original_capture_dir": original_capture_dir,
        "frame_relative": frame_relative,
        "frame_path": recorded_frame,
        "files": files,
        "root_sha256": _root_digest(files),
    }
    (destination / "trusted-evidence.json").write_text(
        json.dumps(record, indent=2) + "\n")
    if read_only:
        try:
            destination.chmod(0o555)
        except OSError:
            pass
    return record


def verify_trusted_evidence(trusted_root: Path | str,
                            frame_dir: Path | str) -> dict:
    """Verify a digest-bound trusted root and return its record."""
    trusted_root = Path(trusted_root).expanduser().resolve()
    record_path = trusted_root / "trusted-evidence.json"
    record = _read_json(record_path)
    if record.get("schema") != "m7-trusted-evidence/v1":
        raise PrefixCheckError("unexpected trusted-evidence schema")
    if record.get("frame_path"):
        expected_frame = Path(str(record["frame_path"])).expanduser().resolve()
    else:
        expected_frame = (trusted_root /
                          str(record.get("frame_relative") or "")).resolve()
    if Path(frame_dir).expanduser().resolve() != expected_frame:
        raise PrefixCheckError(
            "frame directory is not the trusted-evidence frame")
    files = _tree_manifest(expected_frame)
    if files != record.get("files"):
        raise PrefixCheckError("trusted-evidence file manifest changed")
    if _root_digest(files) != record.get("root_sha256"):
        raise PrefixCheckError("trusted-evidence root digest changed")
    record = dict(record)
    record["_root"] = str(trusted_root)
    return record


def _git(workspace: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workspace), *args], capture_output=True, text=True,
        check=False, timeout=60)
    if completed.returncode != 0:
        raise PrefixCheckError(
            f"git {' '.join(args)} failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def verify_execution_outputs(execution: dict, metal_dir: Path) -> list[str]:
    """Return provenance problems for a trusted execution's outputs."""
    problems: list[str] = []
    metal_dir = Path(metal_dir)
    for item in execution.get("outputs") or []:
        path = metal_dir / str(item.get("file"))
        if (not path.is_file()
                or path.stat().st_size != item.get("bytes")
                or _sha256(path) != item.get("sha256")):
            problems.append(
                "trusted checkpoint output changed after execution: "
                f"{item.get('file')}")
    replay_path = metal_dir / "replay.json"
    if (not replay_path.is_file()
            or _sha256(replay_path) != execution.get("replay_json_sha256")):
        problems.append("trusted replay result changed after execution")
    receipt_path = metal_dir / "shader-receipt.json"
    if (not receipt_path.is_file()
            or _sha256(receipt_path) != execution.get("receipt_sha256")):
        problems.append("trusted execution receipt changed after execution")
    return problems


def verify_candidate(workspace: Path | str,
                     sidecar_path: Path | str) -> dict:
    """Verify the candidate worktree, revision and allowed-path policy."""
    workspace = Path(workspace).expanduser().resolve()
    sidecar = dsh_campaign.load_sidecar(Path(sidecar_path))
    if sidecar.workspace.resolve() != workspace:
        raise PrefixCheckError(
            "candidate workspace is not the campaign sidecar workspace")
    try:
        state.require_worktree(
            workspace, ROOT, sidecar.candidate_baseline_commit or "")
    except RuntimeError as exc:
        raise PrefixCheckError(f"candidate worktree is not authorized: {exc}") from exc
    revision = _git(workspace, "rev-parse", "HEAD").strip()
    changed: set[str] = set()
    status = _git(workspace, "status", "--porcelain=v1", "-z")
    for entry in status.split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    committed = _git(
        workspace, "diff", "--name-only",
        sidecar.candidate_baseline_commit or "HEAD").splitlines()
    changed.update(path for path in committed if path)
    for path in sorted(changed):
        if not dsh_campaign.path_allowed(path, sidecar.allowed_paths):
            raise PrefixCheckError(
                f"candidate change is outside the allowed paths: {path}")
        if dsh_campaign.path_allowed(path, sidecar.forbidden_paths):
            raise PrefixCheckError(
                f"candidate change is in a forbidden path: {path}")
    return {
        "workspace": str(workspace),
        "sidecar": str(Path(sidecar_path).resolve()),
        "baseline_commit": sidecar.candidate_baseline_commit,
        "revision": revision,
        "changed_paths": sorted(changed),
        "policy_digest": sidecar.policy_digest,
    }


def _candidate_source_digests(workspace: Path) -> dict[str, str]:
    return replay_host_dependencies.relative_dependencies(
        workspace, replay_host_dependencies.REPLAY_SOURCES)


def _run_trusted_replay(frame_dir: Path, request: dict,
                        run_dir: Path | str | None = None, *,
                        candidate_workspace: Path | str | None = None,
                        candidate_sidecar: Path | str | None = None) -> dict:
    """Execute the production Metal replay from the infrastructure checkout.

    The checker does not trust a candidate-authored replay.json, receipt or
    output files.  It derives the request, translates the captured shaders with
    the trusted infrastructure, runs the trusted replay host and compares the
    files that host just wrote.
    """
    if run_dir is None:
        run = Path(tempfile.mkdtemp(prefix="m7-prefix-check-"))
    else:
        run = Path(run_dir).expanduser().resolve()
        run.mkdir(parents=True, exist_ok=True)
    request_path = run / "checkpoint-request.json"
    request_path.write_text(json.dumps(request, indent=2) + "\n")
    shaders = run / "shaders"
    command = [
        sys.executable, "-m", "automation.m6_frame", "translate",
        "--frame", str(frame_dir), "--out", str(shaders), "--force",
    ]
    translated = subprocess.run(
        command, cwd=str(ROOT), capture_output=True, text=True,
        timeout=300, check=False)
    if translated.returncode != 0:
        raise PrefixCheckError(
            "trusted shader translation failed: "
            + (translated.stderr.strip() or translated.stdout.strip())[-400:])
    candidate = None
    if candidate_workspace is not None:
        candidate = verify_candidate(
            candidate_workspace,
            candidate_sidecar or (ROOT / "dsh" / "campaigns" / "m7-event20.json"))
        before = _candidate_source_digests(Path(candidate["workspace"]))
        snapshot_root = run / "candidate-snapshot"
        snapshot = replay_host_dependencies.snapshot_sources(
            candidate["workspace"], snapshot_root)
        host = run / "candidate-gfx-frame-replay"
        try:
            candidate_build = replay_host_dependencies.build_snapshot_host(
                snapshot_root, host)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrefixCheckError(
                f"candidate replay host build failed: {exc}") from exc
        after = _candidate_source_digests(Path(candidate["workspace"]))
        if before != after:
            raise PrefixCheckError(
                "candidate renderer source changed between snapshot and build")
        if candidate_build.get("source_digests") != after:
            raise PrefixCheckError(
                "candidate source digest does not match the built snapshot")
        candidate["source_digests"] = after
        candidate["source_digest"] = hashlib.sha256(json.dumps(
            after, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        candidate["snapshot_files"] = snapshot["files"]
        candidate["build"] = candidate_build
    else:
        try:
            host = m6_frame.build_replay_host()
        except (m6_frame.FrameError, OSError, RuntimeError) as exc:
            raise PrefixCheckError(
                f"trusted replay host build failed: {exc}") from exc
    replay_dir = run / "metal"
    replay_dir.mkdir()
    replay_command = [
        str(host), "--frame", str(frame_dir), "--shaders", str(shaders),
        "--out", str(replay_dir), "--checkpoints", str(request_path),
    ]
    started = time.time()
    completed = subprocess.run(
        replay_command, cwd=str(ROOT), capture_output=True, text=True,
        timeout=600, check=False)
    if completed.returncode != 0:
        raise PrefixCheckError(
            "trusted Metal replay failed: "
            + (completed.stderr.strip() or completed.stdout.strip())[-400:])
    if candidate is not None:
        if _sha256(host) != candidate["build"]["host_sha256"]:
            raise PrefixCheckError(
                "candidate replay host changed after execution")
        if _candidate_source_digests(Path(candidate["workspace"])) != \
                candidate["source_digests"]:
            raise PrefixCheckError(
                "candidate renderer source changed after the trusted build")
    replay_path = replay_dir / "replay.json"
    replay = _read_json(replay_path)
    if replay.get("status") != "ok" or replay.get("failed") != 0:
        raise PrefixCheckError("trusted Metal replay did not complete cleanly")
    receipt = {
        "schema": "m7-shadow-shader-receipt/v1",
        "capture_frame_id": None,
        "pid": None,
        "session_id": None,
        "trace_manifest_sha256": None,
        "event_stream_sha256": None,
        "checkpoint_request_file": request_path.name,
        "checkpoint_request_sha256": _sha256(request_path),
        "checkpoint_count": len(request.get("checkpoints") or []),
    }
    seal_path = frame_dir / "frame-seal.json"
    if seal_path.is_file():
        seal = _read_json(seal_path)
        receipt.update({
            "capture_frame_id": seal.get("capture_frame_id"),
            "pid": seal.get("pid"),
            "session_id": seal.get("session_id"),
            "trace_manifest_sha256": seal.get("manifest_sha256"),
            "event_stream_sha256": seal.get("event_stream_sha256"),
        })
    (replay_dir / "checkpoint-request.json").write_text(
        json.dumps(request, indent=2) + "\n")
    (replay_dir / "shader-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n")
    outputs = []
    for path in sorted(replay_dir.iterdir()):
        if path.is_file() and path.name not in (
                "replay.json", "shader-receipt.json",
                "checkpoint-request.json", "trusted-execution.json"):
            outputs.append({"file": path.name, "bytes": path.stat().st_size,
                            "sha256": _sha256(path)})
    execution = {
        "schema": "m7-trusted-execution/v1",
        "mode": ("candidate-build-and-replay" if candidate is not None
                 else "checker-native-replay"),
        "candidate": candidate,
        "outputs": outputs,
        "started_epoch": started,
        "finished_epoch": time.time(),
        "frame_dir": str(frame_dir),
        "request_sha256": _sha256(replay_dir / "checkpoint-request.json"),
        "replay_host": str(host),
        "replay_host_sha256": _sha256(host),
        "shaders_json_sha256": _sha256(shaders / "shaders.json")
        if (shaders / "shaders.json").is_file() else None,
        "replay_json_sha256": _sha256(replay_path),
        "receipt_sha256": _sha256(replay_dir / "shader-receipt.json"),
        "request_sha256": _sha256(replay_dir / "checkpoint-request.json"),
        "status": replay.get("status"),
        "events": replay.get("events"),
        "encoded": replay.get("encoded"),
        "failed": replay.get("failed"),
        "command": replay_command,
    }
    (replay_dir / "trusted-execution.json").write_text(
        json.dumps(execution, indent=2) + "\n")
    return {
        "metal_dir": replay_dir,
        "request_path": replay_dir / "checkpoint-request.json",
        "execution": execution,
    }


def evaluate(frame_dir: Path | str, metal_dir: Path | str | None = None,
             *, request_path: Path | str | None = None,
             require_end: int = 20,
             trusted_execution: bool = False,
             trusted_root: Path | str | None = None,
             trusted_run_dir: Path | str | None = None,
             candidate_workspace: Path | str | None = None,
             candidate_sidecar: Path | str | None = None) -> dict:
    """Verify one sealed frame's exact prefix against its Metal replay.

    ``frame_dir`` contains frame-seal.json, frame-trace.json,
    frame-events.jsonl, blobs/ and prefix-observations.jsonl.  ``metal_dir``
    contains replay.json plus the Metal checkpoint outputs.  The trusted
    checker must run this function outside the candidate workspace.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = (Path(metal_dir).expanduser().resolve()
                 if metal_dir is not None else None)
    problems: list[str] = []
    result: dict = {
        "schema": "m7-event20-prefix-check/v1",
        "frame_dir": str(frame_dir),
        "metal_dir": str(metal_dir),
        "required_through": require_end,
        "problems": problems,
    }
    if trusted_root is not None:
        try:
            record = verify_trusted_evidence(trusted_root, frame_dir)
            result["trusted_evidence"] = {
                "root": str(Path(trusted_root).expanduser().resolve()),
                "root_sha256": record.get("root_sha256"),
                "file_count": len(record.get("files") or []),
            }
        except (PrefixCheckError, OSError) as exc:
            problems.append(f"trusted evidence is not usable: {exc}")
            return dict(result, verdict="requires_additional_evidence",
                        status="invalid", first_divergence=None,
                        verified_prefix=None, events=[])
    trusted_record = None
    if trusted_root is not None:
        try:
            trusted_record = verify_trusted_evidence(trusted_root, frame_dir)
        except (PrefixCheckError, OSError):
            trusted_record = None
    try:
        seal = m6_frame.load_seal(
            frame_dir,
            trusted_evidence=trusted_record if trusted_root is not None else None)
        trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    except (m6_frame.FrameError, OSError, ValueError) as exc:
        problems.append(f"sealed frame is not usable: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    graph = gfx_frame_graph.build(frame_dir)
    result["capture"] = gfx_independent_replay.capture_identity(
        seal, trace)
    result["graph_status"] = graph.get("status")
    result["graph_reasons"] = graph.get("reasons") or []
    prefix = graph.get("prefix") or {}
    result["prefix_status"] = prefix.get("status")
    result["prefix_end"] = prefix.get("end")
    if prefix.get("status") != "complete":
        problems.append("exact-prefix coverage is not complete")
        for problem in prefix.get("problems") or []:
            problems.append(str(problem))
        return dict(result, verdict="requires_additional_evidence",
                    status="incomplete", first_divergence=None,
                    verified_prefix=None, events=[])
    end = int(prefix["end"])
    result["initial_state"] = prefix.get("initial_state") or []
    result["state_coverage"] = prefix.get("state_coverage") or []
    result["prefix_coverage"] = {
        "status": prefix.get("status"),
        "end": end,
        "post_events": len(prefix.get("post_events") or []),
        "excluded_events": prefix.get("excluded_events") or [],
    }
    if end < require_end:
        problems.append(
            f"capture prefix ends at event {end}, not {require_end}")
    expected_request = gfx_frame_graph.checkpoint_request(graph)
    expected_canonical = json.dumps(
        expected_request, sort_keys=True, separators=(",", ":"))
    result["expected_request_sha256"] = hashlib.sha256(
        expected_canonical.encode()).hexdigest()
    if trusted_execution:
        evidence = {
            "entry_point": "automation.gfx_prefix_check.evaluate",
            "frame_dir": str(frame_dir),
            "candidate_workspace": (str(Path(candidate_workspace).expanduser()
                                       .resolve())
                                    if candidate_workspace is not None else None),
            "candidate_sidecar": (str(Path(candidate_sidecar).expanduser()
                                      .resolve())
                                  if candidate_sidecar is not None else None),
        }
        try:
            with campaign_guard.attempt("replays", evidence=evidence) as attempt:
                try:
                    trusted = _run_trusted_replay(
                        frame_dir, expected_request, trusted_run_dir,
                        candidate_workspace=candidate_workspace,
                        candidate_sidecar=candidate_sidecar)
                except (PrefixCheckError, OSError,
                        subprocess.TimeoutExpired) as exc:
                    if attempt is not None:
                        attempt.fail(str(exc), evidence=evidence)
                    raise
        except campaign_guard.CampaignRefusal as exc:
            problems.append(f"trusted replay budget refused: {exc}")
            return dict(result, verdict="requires_additional_evidence",
                        status="invalid", first_divergence=None,
                        verified_prefix=None, events=[], execution={
                            "mode": "candidate-build-and-replay",
                            "status": "refused",
                            "error": str(exc),
                        })
        except (PrefixCheckError, OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"trusted Metal execution failed: {exc}")
            return dict(result, verdict="requires_additional_evidence",
                        status="invalid", first_divergence=None,
                        verified_prefix=None, events=[], execution={
                            "mode": "candidate-build-and-replay",
                            "status": "failed",
                            "error": str(exc),
                        })
        result["execution"] = trusted["execution"]
        metal_dir = trusted["metal_dir"]
        request_file = trusted["request_path"]
        execution = trusted["execution"]
        problems.extend(verify_execution_outputs(execution, metal_dir))
    else:
        if metal_dir is None:
            problems.append("Metal result directory is required without "
                            "trusted execution")
            return dict(result, verdict="requires_additional_evidence",
                        status="invalid", first_divergence=None,
                        verified_prefix=None, events=[])
        request_file = (Path(request_path).expanduser().resolve()
                        if request_path is not None
                        else metal_dir / "checkpoint-request.json")
    try:
        request = _read_json(request_file)
    except PrefixCheckError as exc:
        problems.append(str(exc))
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    request_sha = _sha256(request_file)
    result["request"] = {"path": str(request_file),
                         "sha256": request_sha}
    published_canonical = json.dumps(
        request, sort_keys=True, separators=(",", ":"))
    if published_canonical != expected_canonical:
        problems.append(
            "published checkpoint request does not agree with the independent "
            "derivation from the sealed frame")
        result["request"]["agrees_with_derivation"] = False
    else:
        result["request"]["agrees_with_derivation"] = True
    receipt_path = metal_dir / "shader-receipt.json"
    try:
        receipt = _read_json(receipt_path)
    except PrefixCheckError as exc:
        problems.append(f"shader receipt is not usable: {exc}")
        receipt = {}
    result["receipt"] = {
        "path": str(receipt_path),
        "sha256": _sha256(receipt_path) if receipt_path.is_file() else None,
    }
    _require(receipt.get("schema") == "m7-shadow-shader-receipt/v1",
             problems, "shader receipt schema is not recognized")
    _require(receipt.get("checkpoint_request_sha256") == request_sha,
             problems,
             "shader receipt does not bind the published checkpoint request")
    _require(receipt.get("checkpoint_request_file")
             == request_file.name,
             problems, "shader receipt names a different request file")
    requested = request.get("checkpoints")
    if not isinstance(requested, list):
        problems.append("checkpoint request has no checkpoint array")
        requested = []
    _require(receipt.get("checkpoint_count") == len(requested),
             problems, "shader receipt checkpoint count disagrees with the "
                       "published request")
    for field, seal_field in (
            ("capture_frame_id", "capture_frame_id"),
            ("pid", "pid"),
            ("session_id", "session_id"),
            ("trace_manifest_sha256", "manifest_sha256"),
            ("event_stream_sha256", "event_stream_sha256")):
        if seal.get(seal_field) is not None:
            _require(receipt.get(field) == seal.get(seal_field), problems,
                     f"shader receipt {field} does not match the sealed frame")
    replay_path = metal_dir / "replay.json"
    try:
        replay = _read_json(replay_path)
    except PrefixCheckError as exc:
        problems.append(f"Metal replay result is not usable: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    result["replay"] = {
        "path": str(replay_path),
        "sha256": _sha256(replay_path),
        "status": replay.get("status"),
        "events": replay.get("events"),
        "encoded": replay.get("encoded"),
        "failed": replay.get("failed"),
    }
    _require(replay.get("status") == "ok", problems,
             "Metal replay status is not ok")
    _require(replay.get("failed") == 0, problems,
             "Metal replay reported failed events")
    _require(replay.get("events") == len(trace.get("_events") or []),
             problems, "Metal replay event count does not match the frame")
    _require(replay.get("encoded") == replay.get("events"), problems,
             "Metal replay did not encode every event")
    for field, value in (
            ("checkpoint_request_sha256", request_sha),
            ("checkpoint_request_file", request_file.name)):
        if replay.get(field) is not None:
            _require(replay.get(field) == value, problems,
                     f"Metal replay {field} does not bind the request")
    capture = result["capture"]
    try:
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            graph, request, replay, frame_dir, metal_dir, capture=capture)
    except (gfx_independent_replay.ReplayError, OSError, ValueError) as exc:
        problems.append(f"checkpoint reconciliation failed: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    result["checkpoint_validation"] = validation
    _require(validation.get("status") == "ok", problems,
             "checkpoint reconciliation reported invalid evidence")

    events_out: list[dict] = []
    expected_post = {int(record["event"]): record
                     for record in prefix.get("post_events") or []}
    excluded = {int(event) for event in prefix.get("excluded_events") or []}
    result["excluded_events"] = sorted(excluded)
    seen: set[int] = set()
    for entry in results:
        if entry.get("timing") != "post_event":
            continue
        event = int(entry.get("event"))
        seen.add(event)
        observation = expected_post.get(event)
        row = {
            "event": event,
            "target": entry.get("target"),
            "resource": entry.get("resource"),
            "timing": entry.get("timing"),
            "status": entry.get("status"),
            "reference_file": entry.get("reference_file"),
            "metal_file": entry.get("metal_file"),
            "reference_sha256": entry.get("reference_sha256"),
            "metal_sha256": entry.get("metal_sha256"),
            "dimensions": [entry.get("width"), entry.get("height")],
            "reference_pixel_format": entry.get("reference_pixel_format"),
            "candidate_pixel_format": entry.get("candidate_pixel_format"),
            "alpha_applicable": entry.get("alpha_applicable", True) is not False,
            "compared_channels": entry.get("compared_channels"),
        }
        statistics = entry.get("statistics") or {}
        row["statistics"] = statistics
        if event in excluded:
            problems.append(
                f"prefix event {event} is excluded but has a Metal result")
            row["status"] = "invalid"
            row["problem"] = "unexpected result for an excluded event"
            events_out.append(row)
            continue
        if observation is None:
            problems.append(f"prefix event {event} was not requested")
            row["status"] = "invalid"
            row["problem"] = "unexpected prefix observation"
            events_out.append(row)
            continue
        if (entry.get("resource") != observation.get("resource")
                or str(entry.get("target"))
                != str(observation.get("target_name"))):
            problems.append(
                f"prefix event {event} resource or target disagrees with the "
                "sealed observation")
            row["status"] = "invalid"
            row["problem"] = "resource identity mismatch"
        if entry.get("reference_pixel_format") not in (None, "BGRA8", "RGBA8"):
            problems.append(
                f"prefix event {event} has an unsupported reference pixel "
                "format")
            row["status"] = "invalid"
            row["problem"] = "unsupported pixel format"
        # Recompute exact four-channel equality from the original bytes.  The
        # reconciled statistics are not trusted for the verdict.
        try:
            width = int(entry.get("width") or 0)
            height = int(entry.get("height") or 0)
            reference_path = Path(entry.get("reference_file") or "")
            metal_path = Path(entry.get("metal_file") or "")
            reference = reference_path.read_bytes()
            candidate = metal_path.read_bytes()
            if (len(reference) != width * height * 4
                    or len(candidate) != width * height * 4):
                raise PrefixCheckError("pixel payload size mismatch")
            alpha_applicable = entry.get("alpha_applicable", True) is not False
            channels = entry.get("compared_channels") or (
                "RGBA" if alpha_applicable else "RGB")
            if channels == "R32F":
                exact = gfx_independent_replay.compare_r32f(
                    reference, candidate, width, height)
            else:
                if not alpha_applicable:
                    reference = _opaque_alpha(reference)
                    candidate = _opaque_alpha(candidate)
                exact = m6_compare.compare_images(
                    reference, candidate, width, height)
            row["independent"] = {
                "mismatched_pixels": exact["mismatched_pixels"],
                "max_channel_error": exact["max_channel_error"],
                "alpha_different_pixels": exact["alpha_different_pixels"],
                "pixels": exact["pixels"],
            }
            if (exact["mismatched_pixels"] != 0
                    or exact["max_channel_error"] != 0
                    or exact["alpha_different_pixels"] != 0):
                row["status"] = "divergent"
                row["problem"] = "independent exact comparison failed"
            elif row["status"] == "match":
                row["status"] = "match"
            else:
                row["status"] = "invalid"
                row["problem"] = (row.get("problem")
                                  or entry.get("reason")
                                  or "reconciled status is not match")
        except (OSError, ValueError, PrefixCheckError) as exc:
            row["status"] = "invalid"
            row["problem"] = f"independent comparison failed: {exc}"
            problems.append(f"prefix event {event}: {row['problem']}")
        events_out.append(row)
    for event in range(end + 1):
        if event in seen:
            continue
        if event in excluded:
            events_out.append({
                "event": event,
                "status": "unsupported_auxiliary",
                "excluded": True,
                "problem": "isolated auxiliary target with an unsupported "
                           "non-BGRA colour format; the clear is accounted "
                           "for but not compared",
            })
            continue
        problems.append(f"prefix event {event} has no reconciled result")
        events_out.append({
            "event": event,
            "status": "missing",
            "problem": "no reconciled Metal result",
        })
    events_out.sort(key=lambda item: item["event"])
    result["events"] = events_out
    result["observed_events"] = len(events_out)

    classification = classify_prefix(
        events_out, problems=problems, end=end, require_end=require_end)
    result.update(classification)
    result["verdict"] = classification["verdict"]
    result["problems_count"] = len(problems)
    return result
