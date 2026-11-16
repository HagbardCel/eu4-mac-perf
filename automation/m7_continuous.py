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
import shutil
import statistics
import subprocess
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
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1,
                max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


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
                 timeout: float = 3600.0) -> dict:
    """Execute, diagnose and supervise one bounded ordered frame sequence."""
    if max_pending < 1:
        raise ContinuousError("max_pending", "must be positive")
    started = time.monotonic()
    frames = [Path(frame).expanduser().resolve() for frame in frames]
    out_dir = prepare_output(Path(out_dir), frames)
    validation = validate_sequence(frames, max_frames=max_frames,
                                   max_bytes=max_bytes)
    host_path = Path(host).expanduser() if host else m6_frame.build_replay_host()
    control_dir = out_dir / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(host_path), "--sequence", ",".join(map(str, frames)),
        "--shaders", str(Path(shaders).expanduser().resolve()),
        "--out", str(out_dir), "--control-dir", str(control_dir),
        "--control-timeout-ms", "120000",
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
                shaders=Path(shaders).expanduser().resolve())
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
            "diagnosis_seconds": diagnosis_seconds,
        }
        result["frames"].append(entry)
        hard_stop = (not row.get("ok")
                     or row.get("history_status") not in (1, None))
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
                manifest = gfx_replay_case.package_diagnosis(
                    frame, output, case, diagnosis,
                    shaders=Path(shaders).expanduser().resolve(),
                    case_id=f"m7-continuous-frame-{sequence:06d}")
                entry["case"] = {
                    "case_id": manifest.get("case_id"),
                    "files": len(manifest.get("files") or []),
                    "bytes": sum(item["bytes"]
                                 for item in manifest.get("files") or []),
                }
            except (gfx_replay_case.CaseError, OSError) as exc:
                entry["case_error"] = str(exc)
            stopped_during_execution = True
            return False
        return True

    try:
        for line in process.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            keep_going = process_row(row)
            decide(keep_going, result.get("stop_reason") or "continue",
                   int(row.get("sequence") or 0))
            if not keep_going:
                break
    finally:
        if stopped_during_execution and process.poll() is None:
            try:
                process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        else:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                result["status"] = "failed"
                result["stop_reason"] = "timeout"
        result["returncode"] = process.returncode
        result["stderr"] = (process.stderr.read() or "")[-4000:] if process.stderr else ""
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
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
