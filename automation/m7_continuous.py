"""Bounded continuous single-process dual-render campaign supervisor.

The capture side writes one immutable ``gfx-frame-trace/v1`` directory per
successive frame plus a ``gfx-frame-sequence/v1`` record.  This module validates
that ordered sequence, executes it through one persistent Metal session
(``gfx_metal_replay_cli --sequence``), diagnoses every produced frame with the
PR3 contract, and stops on the first non-qualified frame while preserving a
portable case.

This is the synchronous stage: all captured frames are already on disk before
the Metal worker starts.  It deliberately does not claim live asynchronous
capture; the queue/bounds here are the exact limits the live bridge must obey.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import queue
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path

from automation import gfx_frame_graph, gfx_independent_replay, gfx_replay_case, m6_frame

SCHEMA = "m7-continuous-campaign/v1"
OUTPUT_MARKER = ".m7-continuous-output.json"
STOP_CLASSIFICATIONS = {
    "qualified_difference",
    "difference_with_partial_state_coverage",
    "incomplete_capture",
    "invalid_provenance",
    "unsupported_state",
    "gpu_execution_failure",
    "incomplete_diagnostics",
}


class ContinuousError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContinuousError("unreadable", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContinuousError("unreadable", f"{path} is not an object")
    return value


def prepare_output(out_dir: Path, inputs: list[Path]) -> Path:
    out_dir = out_dir.expanduser().resolve()
    if gfx_independent_replay._protected_output(out_dir):
        raise ContinuousError("output_protected", str(out_dir))
    for source in inputs:
        if gfx_independent_replay._path_overlap(
                out_dir, source.expanduser().resolve()):
            raise ContinuousError("output_overlap",
                                  f"{out_dir} overlaps {source}")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise ContinuousError("output_conflict", str(out_dir))
        if any(out_dir.iterdir()):
            marker = out_dir / OUTPUT_MARKER
            owned = False
            try:
                value = json.loads(marker.read_text(encoding="utf-8"))
                owned = value.get("schema") == "m7-continuous-output/v1"
            except (OSError, UnicodeError, json.JSONDecodeError):
                owned = False
            if not owned:
                raise ContinuousError(
                    "output_not_owned",
                    f"{out_dir} is nonempty and not a prior campaign output")
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / OUTPUT_MARKER).write_text(json.dumps({
        "schema": "m7-continuous-output/v1",
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }) + "\n")
    return out_dir


def validate_sequence(frames: list[Path], *, max_frames: int = 64,
                      max_bytes: int = 0) -> dict:
    """Validate ordered identity without executing Metal."""
    if not frames:
        raise ContinuousError("empty_sequence")
    if len(frames) > max_frames:
        raise ContinuousError(
            "too_many_frames", f"{len(frames)} > {max_frames}")
    records = []
    sealed_frames: list[bool] = []
    previous_identity = None
    session_id = None
    pid = None
    total_bytes = 0
    for index, frame in enumerate(frames, start=1):
        frame = frame.expanduser().resolve()
        if not (frame / "frame-trace.json").is_file():
            raise ContinuousError("missing_frame", str(frame))
        if not (frame / "frame-events.jsonl").is_file():
            raise ContinuousError("missing_event_stream", str(frame))
        sequence_path = frame / "frame-sequence.json"
        if not sequence_path.is_file():
            raise ContinuousError("missing_sequence", str(frame))
        sequence = _read_json(sequence_path)
        if sequence.get("schema") != "gfx-frame-sequence/v1":
            raise ContinuousError("sequence_schema", str(sequence_path))
        if index == 1 and sequence.get("previous") is not None:
            raise ContinuousError(
                "first_frame_previous",
                f"the first frame declares a previous identity: {frame}")
        if int(sequence.get("sequence_index") or 0) != index:
            raise ContinuousError(
                "sequence_index",
                f"{frame} declares {sequence.get('sequence_index')} not {index}")
        if sequence.get("complete") is not True:
            raise ContinuousError("incomplete_frame", str(frame))
        if session_id is None:
            session_id = sequence.get("session_id")
            pid = sequence.get("pid")
        elif sequence.get("session_id") != session_id:
            raise ContinuousError("session_identity", str(frame))
        if not session_id or not pid:
            raise ContinuousError("session_identity_missing", str(frame))
        manifest_sha = _sha(frame / "frame-trace.json")
        events_sha = _sha(frame / "frame-events.jsonl")
        if sequence.get("manifest_sha256") != manifest_sha:
            raise ContinuousError("manifest_digest", str(frame))
        if sequence.get("event_stream_sha256") != events_sha:
            raise ContinuousError("event_digest", str(frame))
        capture_id = sequence.get("capture_frame_id")
        if not isinstance(capture_id, int) or capture_id <= 0:
            raise ContinuousError("capture_id", str(frame))
        reference = frame / "frame-reference.bgra"
        if not reference.is_file():
            raise ContinuousError("missing_reference", str(frame))
        if sequence.get("reference_sha256") != _sha(reference):
            raise ContinuousError("reference_digest", str(frame))
        seal_path = frame / "frame-seal.json"
        sealed_frames.append(seal_path.is_file())
        if seal_path.is_file():
            try:
                seal = m6_frame.load_seal(frame)
            except m6_frame.FrameError as exc:
                raise ContinuousError(
                    "invalid_seal", f"{frame}: {exc}") from exc
            if seal.get("capture_frame_id") != capture_id:
                raise ContinuousError("seal_capture_id", str(frame))
            if seal.get("manifest_sha256") != manifest_sha:
                raise ContinuousError("seal_manifest", str(frame))
            if seal.get("reference_sha256") != sequence.get(
                    "reference_sha256"):
                raise ContinuousError("seal_reference", str(frame))
        if previous_identity is not None:
            previous = sequence.get("previous")
            if not isinstance(previous, dict):
                raise ContinuousError("previous_identity", str(frame))
            if previous.get("capture_frame_id") != previous_identity[
                    "capture_frame_id"]:
                raise ContinuousError("previous_capture_id", str(frame))
            if previous.get("manifest_sha256") != previous_identity[
                    "manifest_sha256"]:
                raise ContinuousError("previous_manifest", str(frame))
            if previous.get("event_stream_sha256") != previous_identity[
                    "event_stream_sha256"]:
                raise ContinuousError("previous_events", str(frame))
        size = sum(path.stat().st_size for path in frame.rglob("*")
                   if path.is_file())
        total_bytes += size
        records.append({
            "sequence": index,
            "frame_dir": str(frame),
            "capture_frame_id": capture_id,
            "manifest_sha256": manifest_sha,
            "event_stream_sha256": events_sha,
            "reference_sha256": sequence.get("reference_sha256"),
            "carried_renderer_outputs":
                sequence.get("carried_renderer_outputs") or [],
            "bytes": size,
        })
        previous_identity = {
            "capture_frame_id": capture_id,
            "manifest_sha256": manifest_sha,
            "event_stream_sha256": events_sha,
        }
    if any(sealed_frames) and not all(sealed_frames):
        raise ContinuousError(
            "mixed_seals",
            "some frames are sealed and others are not; a continuous "
            "campaign must be uniformly sealed or explicitly synthetic")
    if max_bytes > 0 and total_bytes > max_bytes:
        raise ContinuousError(
            "byte_budget", f"{total_bytes} > {max_bytes}")
    return {
        "schema": "gfx-frame-sequence-validation/v1",
        "status": "ok",
        "session_id": session_id,
        "pid": pid,
        "frames": records,
        "bytes": total_bytes,
        "sealed": bool(sealed_frames) and all(sealed_frames),
    }


def prepare_sequence_checkpoints(frames: list[Path], out_dir: Path,
                                  validation: dict) -> dict:
    """Publish one bound checkpoint request and receipt per sequence frame.

    The request is derived from the validated capture graph (not from the
    replay result), and the receipt binds its digest to the exact sealed
    frame/session identity.  The native sequence CLI copies both into the
    frame's output directory and passes the request to the Metal core, so the
    same PR3 reconciliation contract applies to offline sequence replay.
    """
    base = out_dir / "checkpoints"
    base.mkdir(parents=True, exist_ok=True)
    records = []
    validated = {int(row["sequence"]): row
                 for row in validation.get("frames") or []}
    for index, frame in enumerate(frames, start=1):
        row = validated.get(index)
        if row is None:
            raise ContinuousError("sequence_validation",
                                  f"frame {index} has no validation record")
        try:
            graph = gfx_frame_graph.build(frame)
        except Exception as exc:  # noqa: BLE001 - explicit campaign failure
            raise ContinuousError(
                "checkpoint_graph", f"{frame}: {exc}") from exc
        if graph.get("status") == "invalid":
            raise ContinuousError(
                "checkpoint_graph",
                f"{frame}: " + "; ".join(graph.get("reasons") or ["invalid"]))
        directory = base / f"frame-{index:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        request_path = directory / "checkpoint-request.json"
        request = gfx_frame_graph.write_checkpoint_request(graph, request_path)
        request_sha = _sha(request_path)
        seal = None
        if (frame / "frame-seal.json").is_file():
            try:
                seal = m6_frame.load_seal(frame)
            except m6_frame.FrameError as exc:
                raise ContinuousError(
                    "invalid_seal", f"{frame}: {exc}") from exc
        sequence = _read_json(frame / "frame-sequence.json")
        identity = {
            "capture_frame_id": sequence.get("capture_frame_id"),
            "pid": sequence.get("pid"),
            "session_id": sequence.get("session_id"),
            "trace_manifest_sha256": sequence.get("manifest_sha256"),
            "event_stream_sha256": sequence.get("event_stream_sha256"),
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
        }
        if seal is not None:
            identity.update({
                "capture_frame_id": seal.get("capture_frame_id"),
                "pid": seal.get("pid"),
                "session_id": seal.get("session_id"),
                "trace_manifest_sha256": seal.get("manifest_sha256"),
                "event_stream_sha256": seal.get("event_stream_sha256"),
                "event_stream_bytes": seal.get("event_stream_bytes"),
            })
        receipt = {
            "schema": "m7-shadow-shader-receipt/v1",
            **identity,
            "artifacts_sha256": "",
            "artifact_count": 0,
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": request_sha,
            "checkpoint_count": len(request.get("checkpoints") or []),
            "sequence": index,
            "graph_status": graph.get("status"),
        }
        (directory / "shader-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        records.append({
            "sequence": index,
            "frame_dir": str(frame),
            "directory": str(directory),
            "request_file": str(request_path),
            "request_sha256": request_sha,
            "checkpoints": len(request.get("checkpoints") or []),
            "graph_status": graph.get("status"),
        })
    return {
        "schema": "m7-sequence-checkpoints/v1",
        "directory": str(base),
        "frames": records,
    }


def _terminate_process(process: subprocess.Popen, grace: float = 5.0) -> None:
    """Bounded termination: terminate, then kill, then stop waiting."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except OSError:
        pass
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _effective_frame_shaders(frame: Path, fallback: Path | str) -> Path:
    """Resolve the translation directory the sequence CLI uses for a frame.

    The native sequence host prefers ``<frame>/shaders`` and falls back to its
    single ``--shaders`` argument.  Diagnosis must inspect the same translation
    that produced the frame, not the target's directory for every frame.
    """
    local = frame / "shaders"
    if (local / "shaders.json").is_file():
        return local.resolve()
    return Path(fallback).expanduser().resolve()


def run_campaign(frames: list[Path], out_dir: Path, *,
                 shaders: Path | str,
                 policy: Path | str | None = None,
                 max_pending: int = 4,
                 max_frames: int = 64,
                 max_bytes: int = 0,
                 max_seconds: float = 0.0,
                 host: Path | str | None = None,
                 stop_on_difference: bool = True,
                 package_dir: Path | str | None = None,
                 cancel=None,
                 timeout: float = 3600.0,
                 frame_timeout: float = 120.0,
                 case_max_bytes: int = 0,
                 stderr_limit: int = 1_000_000) -> dict:
    """Execute, diagnose and supervise one bounded ordered frame sequence."""
    if max_pending < 1:
        raise ContinuousError("max_pending", "must be positive")
    if frame_timeout <= 0 or timeout <= 0:
        raise ContinuousError(
            "timeout", "frame_timeout and timeout must be positive")
    started = time.monotonic()
    frames = [Path(frame).expanduser().resolve() for frame in frames]
    try:
        _policy_value, policy_evidence = gfx_independent_replay.load_policy(
            policy)
    except gfx_independent_replay.ReplayError as exc:
        raise ContinuousError(exc.kind, exc.detail) from exc
    out_dir = prepare_output(Path(out_dir), frames)
    validation = validate_sequence(frames, max_frames=max_frames,
                                   max_bytes=max_bytes)
    host_path = Path(host).expanduser() if host else m6_frame.build_replay_host()
    control_dir = out_dir / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_setup = prepare_sequence_checkpoints(frames, out_dir, validation)
    command = [
        str(host_path), "--sequence", ",".join(map(str, frames)),
        "--shaders", str(Path(shaders).expanduser().resolve()),
        "--out", str(out_dir), "--control-dir", str(control_dir),
        "--control-timeout-ms", "120000",
        "--sequence-checkpoints", checkpoint_setup["directory"],
        "--frame-timeout-ms", str(int(frame_timeout * 1000)),
    ]
    result: dict = {
        "schema": SCHEMA,
        "status": "running",
        "host": str(host_path),
        "out_dir": str(out_dir),
        "shaders": str(Path(shaders).expanduser().resolve()),
        "max_pending": max_pending,
        "max_frames": max_frames,
        "max_bytes": max_bytes,
        "stop_on_difference": stop_on_difference,
        "policy": policy_evidence,
        "validation": validation,
        "frames": [],
        "stop_reason": None,
        "returncode": None,
        "stderr": "",
    }
    replay_times = []
    diagnosis_times = []
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stopped_during_execution = False

    def decide(continue_running: bool, reason: str, sequence: int) -> None:
        (control_dir / "decision.tmp").write_text(json.dumps({
            "continue": continue_running,
            "reason": reason,
            "sequence": sequence,
        }) + "\n")
        os.replace(control_dir / "decision.tmp", control_dir / "decision")

    def process_row(row: dict) -> bool:
        nonlocal stopped_during_execution
        if cancel is not None and cancel.is_set():
            result["status"] = "cancelled"
            result["stop_reason"] = "cancelled"
            stopped_during_execution = True
            return False
        if max_seconds > 0 and time.monotonic() - started > max_seconds:
            result["status"] = "incomplete"
            result["stop_reason"] = "time_budget"
            stopped_during_execution = True
            return False
        sequence = int(row.get("sequence") or 0)
        if sequence < 1 or sequence > len(frames):
            result["status"] = "failed"
            result["stop_reason"] = "frame_identity"
            stopped_during_execution = True
            return False
        frame = frames[sequence - 1]
        output = Path(str(row.get("output_dir") or
                          out_dir / f"out-{sequence:03d}")).resolve()
        diagnosis_started = time.monotonic()
        effective_shaders = _effective_frame_shaders(frame, shaders)
        if not row.get("ok"):
            diagnosis = {
                "classification": "gpu_execution_failure",
                "qualification": {"axes": {"gpu_execution": "failed"}},
                "first_divergence": {
                    "kind": "incomplete",
                    "reason": row.get("error") or "Metal execution failed",
                },
                "pixels": {"statistics": None},
                "checkpoint_validation": {"status": "incomplete"},
            }
        else:
            diagnosis = gfx_independent_replay.diagnose_frame(
                frame, output, output, policy,
                shaders=effective_shaders,
                require_seal=bool(validation.get("sealed")))
            diagnosis["campaign_policy"] = policy_evidence
            diagnosis["policy"] = policy_evidence
        diagnosis_seconds = time.monotonic() - diagnosis_started
        diagnosis_times.append(diagnosis_seconds)
        replay_times.append(float(row.get("duration_ns") or 0) / 1e9)
        classification = diagnosis.get("classification")
        entry = {
            "sequence": sequence,
            "frame_dir": str(frame),
            "output_dir": str(output),
            "ok": bool(row.get("ok")),
            "events": row.get("events"),
            "encoded": row.get("encoded"),
            "failed": row.get("failed"),
            "history_status": row.get("history_status"),
            "carried_bindings": row.get("carried_bindings"),
            "missing_history": row.get("missing_history"),
            "stale_history": row.get("stale_history"),
            "stage": row.get("stage"),
            "error": row.get("error"),
            "classification": classification,
            "axes": (diagnosis.get("qualification") or {}).get("axes"),
            "first_divergence": diagnosis.get("first_divergence"),
            "pixels": (diagnosis.get("pixels") or {}).get("statistics"),
            "checkpoint_validation": diagnosis.get("checkpoint_validation"),
            "diagnostic_report": str(output / "diagnostic-report.json"),
            "shader_dir": str(effective_shaders),
            "diagnosis_seconds": diagnosis_seconds,
        }
        result["frames"].append(entry)
        terminal_classification = classification in (
            "gpu_execution_failure", "invalid_provenance",
            "incomplete_capture")
        hard_stop = (not row.get("ok")
                     or row.get("history_status") not in (1, None)
                     or terminal_classification)
        soft_stop = (classification in STOP_CLASSIFICATIONS
                     or classification != "qualified_match")
        stop = hard_stop or (stop_on_difference and soft_stop)
        if stop:
            result["status"] = "stopped"
            result["stop_reason"] = (
                "gpu_execution_failure" if not row.get("ok")
                else "history_invalid"
                if row.get("history_status") not in (1, None)
                else classification)
            result["stopped_at"] = sequence
            try:
                case = Path(package_dir) if package_dir else (out_dir / "case")
                package_kwargs = {
                    "shaders": Path(shaders).expanduser().resolve(),
                    "case_id": f"m7-continuous-frame-{sequence:06d}",
                    "checkpoint_sources": [
                        out_dir / "checkpoints" / f"frame-{index:03d}"
                        for index in range(1, sequence + 1)],
                }
                if case_max_bytes > 0:
                    package_kwargs["max_bytes"] = case_max_bytes
                manifest = gfx_replay_case.package_sequence_diagnosis(
                    frames[:sequence], output, case, diagnosis,
                    **package_kwargs)
                reproduction = manifest.get("reproduction") or {}
                entry["case"] = {
                    "case_id": manifest.get("case_id"),
                    "files": len(manifest.get("files") or []),
                    "bytes": sum(item["bytes"]
                                 for item in manifest.get("files") or []),
                    "reproduction": reproduction,
                    "independently_reproducible": reproduction.get(
                        "independently_reproducible"),
                }
            except (gfx_replay_case.CaseError, OSError) as exc:
                entry["case_error"] = str(exc)
            stopped_during_execution = True
            return False
        return True

    stderr_chunks: list[str] = []
    stderr_total = 0
    events: "queue.Queue[tuple[str, str | None]]" = queue.Queue()

    def reader(stream, name: str) -> None:
        try:
            for line in stream:
                events.put((name, line))
        except (OSError, ValueError):
            pass
        finally:
            events.put((name, None))

    threads: list[threading.Thread] = []
    stdout_open = process.stdout is not None
    stderr_open = process.stderr is not None
    for stream, name in ((process.stdout, "stdout"),
                         (process.stderr, "stderr")):
        if stream is None:
            continue
        thread = threading.Thread(target=reader, args=(stream, name),
                                  daemon=True)
        thread.start()
        threads.append(thread)
    overall_deadline = (started + max_seconds
                        if max_seconds > 0 else started + timeout)
    frame_deadline = time.monotonic() + frame_timeout
    stop_loop = False

    def consume_row(line: str) -> bool | None:
        """Process one JSON result line; return False to stop the campaign."""
        nonlocal frame_deadline
        line = line.strip()
        if not line.startswith("{"):
            return None
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        keep_going = process_row(row)
        decide(keep_going, result.get("stop_reason") or "continue",
               int(row.get("sequence") or 0))
        frame_deadline = time.monotonic() + frame_timeout
        return keep_going

    try:
        while not stop_loop:
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                result["status"] = "cancelled"
                result["stop_reason"] = "cancelled"
                break
            if now >= overall_deadline:
                result["status"] = "failed"
                result["stop_reason"] = "overall_timeout"
                result["error"] = (
                    f"campaign exceeded its {timeout:.1f}s overall deadline")
                break
            if now >= frame_deadline:
                result["status"] = "failed"
                result["stop_reason"] = "frame_timeout"
                result["error"] = (
                    f"worker published no frame result within "
                    f"{frame_timeout:.1f}s")
                break
            wait = min(0.2, overall_deadline - now, frame_deadline - now)
            try:
                name, line = events.get(timeout=max(0.0, wait))
            except queue.Empty:
                if (not stdout_open and not stderr_open
                        and process.poll() is not None):
                    break
                continue
            if line is None:
                if name == "stdout":
                    stdout_open = False
                else:
                    stderr_open = False
                if (not stdout_open and not stderr_open
                        and process.poll() is not None):
                    break
                continue
            if name == "stderr":
                # Bounded concurrent drain: a full pipe can never deadlock
                # the worker, and diagnostics stay available.
                if stderr_total < stderr_limit:
                    keep = line[:stderr_limit - stderr_total]
                    stderr_chunks.append(keep)
                    stderr_total += len(keep)
                continue
            if consume_row(line) is False:
                stop_loop = True
                break
    finally:
        # Mark readers done, then bound process teardown.  Stop decisions ask
        # the worker to exit; a worker that ignores them is terminated.
        if result.get("status") in ("stopped", "cancelled"):
            try:
                process.wait(timeout=min(10.0, frame_timeout))
            except subprocess.TimeoutExpired:
                _terminate_process(process)
        else:
            try:
                process.wait(timeout=min(10.0, frame_timeout))
            except subprocess.TimeoutExpired:
                _terminate_process(process)
        if process.poll() is None:
            _terminate_process(process)
        # Never diagnose a frame after a stop: queued output is reported as
        # unexecuted evidence, not promoted into the campaign result.
        while True:
            try:
                name, line = events.get_nowait()
            except queue.Empty:
                break
            if line is not None and name == "stderr" and (
                    stderr_total < stderr_limit):
                keep = line[:stderr_limit - stderr_total]
                stderr_chunks.append(keep)
                stderr_total += len(keep)
        for thread in threads:
            thread.join(timeout=2.0)
        result["returncode"] = process.returncode
        result["stderr"] = "".join(stderr_chunks)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        unexecuted = max(0, len(frames) - len(result["frames"]))
        if unexecuted:
            pending = [
                {"sequence": index + 1, "frame_dir": str(frames[index])}
                for index in range(len(result["frames"]), len(frames))
            ]
            result["unexecuted_frames"] = pending
            result["queued_frames"] = pending
        result["queue"] = {
            "depth": 0,
            "high_water": 1,
            "unexecuted": unexecuted,
            "max_pending": max_pending,
        }
    if result["status"] == "running":
        if result["returncode"] not in (0, None):
            result["status"] = "failed"
            result["stop_reason"] = "host_failure"
        elif len(result["frames"]) != len(frames):
            result["status"] = "incomplete"
            result["stop_reason"] = "frame_count"
            result["missing_frames"] = len(frames) - len(result["frames"])
        else:
            result["status"] = "complete"
            result["stop_reason"] = "frame_limit"
    result["qualified"] = bool(
        result["status"] == "complete"
        and all(item.get("classification") == "qualified_match"
                for item in result["frames"]))
    result["metrics"] = {
        "frames": len(result["frames"]),
        "replay_seconds": {
            "average": statistics.mean(replay_times) if replay_times else 0.0,
            "median": statistics.median(replay_times) if replay_times else 0.0,
            "p95": _percentile(replay_times, 0.95),
            "worst": max(replay_times) if replay_times else 0.0,
        },
        "diagnosis_seconds": {
            "average": statistics.mean(diagnosis_times)
            if diagnosis_times else 0.0,
            "median": statistics.median(diagnosis_times)
            if diagnosis_times else 0.0,
            "p95": _percentile(diagnosis_times, 0.95),
            "worst": max(diagnosis_times) if diagnosis_times else 0.0,
        },
        "queue_high_water": 1,
        "max_pending": max_pending,
        "bytes_written": sum(
            path.stat().st_size for path in out_dir.rglob("*")
            if path.is_file()),
        "wall_seconds": time.monotonic() - started,
    }
    (out_dir / "campaign-report.json").write_text(
        json.dumps(result, indent=2) + "\n")
    (out_dir / "metrics.json").write_text(
        json.dumps(result["metrics"], indent=2) + "\n")
    result["metrics"]["bytes_written"] = sum(
        path.stat().st_size for path in out_dir.rglob("*")
        if path.is_file())
    (out_dir / "campaign-report.json").write_text(
        json.dumps(result, indent=2) + "\n")
    (out_dir / "metrics.json").write_text(
        json.dumps(result["metrics"], indent=2) + "\n")
    return result
