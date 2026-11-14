"""Dual-process differential campaign coordinator.

Starts the original-OpenGL reference and the reconstructed-Metal candidate
concurrently, streams each process's logical-frame observations, aligns them,
applies the GPU health gate, preserves bounded evidence on the first terminal
condition, and stops only the processes this session owns.

PR 2 scope: the runner, observation channel, alignment and safe stop.  Pixel
comparison is the PR 3 layer and is reported as ``not_run`` here rather than
being faked from command-buffer completion.
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from automation import gfx_align, gfx_artifacts, gfx_diagnostics, gfx_flight, gfx_frame_compare
from automation import gfx_launch, gfx_scenario
from automation import gfx_session, gfx_wait, launch_ledger, launch_lease

import eu4_benchmark as base_benchmark

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "gfx-dual-run/v1"
ALIGNED = "aligned"
AMBIGUOUS = "ambiguous"
NOT_ALIGNED = "not_aligned"
MISSING = "missing"


@dataclass
class FrameRecord:
    backend: str
    path: Path
    value: dict

    @property
    def index(self) -> int:
        return int(self.value.get("frame_index", 0))

    @property
    def digest(self) -> str:
        return str(self.value.get("sha256") or "")


class FrameStream:
    """Incremental reader for one process's ``frame-observe.jsonl``."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.pending = b""
        self.records: list[FrameRecord] = []
        self.malformed = 0
        self.backend = ""

    def poll(self) -> list[FrameRecord]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            # A rotated/truncated stream is an instrumentation failure, not a pass.
            self.offset = 0
            self.pending = b""
        if size == self.offset:
            return []
        try:
            with self.path.open("rb") as source:
                source.seek(self.offset)
                chunk = source.read(size - self.offset)
        except OSError:
            return []
        self.offset = size
        data = self.pending + chunk
        lines = data.split(b"\n")
        self.pending = lines.pop() if lines else b""
        found: list[FrameRecord] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.malformed += 1
                continue
            if not isinstance(value, dict) or "frame_index" not in value:
                self.malformed += 1
                continue
            backend = str(value.get("backend") or self.backend)
            self.backend = backend
            record = FrameRecord(backend, self.path, value)
            self.records.append(record)
            found.append(record)
        return found


@dataclass
class CampaignState:
    ref_stream: FrameStream
    cand_stream: FrameStream
    ref_by_index: dict[int, FrameRecord] = field(default_factory=dict)
    cand_by_index: dict[int, FrameRecord] = field(default_factory=dict)
    pairs: list[dict] = field(default_factory=list)
    misaligned: int = 0
    duplicate: int = 0
    dropped: int = 0
    first_pair: dict | None = None
    last_pair: dict | None = None
    last_ref: int = 0
    last_cand: int = 0
    ref_base: int | None = None
    cand_base: int | None = None
    pairing: bool = False
    lockstep_stops: int = 0
    released_ref: set[int] = field(default_factory=set)
    released_cand: set[int] = field(default_factory=set)
    phase_entry_ref: int | None = None
    phase_entry_cand: int | None = None

    def begin_pairing(self) -> None:
        """Anchor correspondence at the current observation point.

        The two processes start at different times; pairing absolute frame
        ordinals from process start would compare a loading frame with a title
        frame.  After both are ready, the next frames from each side are
        treated as the same logical interval.  PR 3 replaces this anchor with
        semantic scene/update evidence.
        """
        ref_indices = [record.index for record in self.ref_stream.records]
        cand_indices = [record.index for record in self.cand_stream.records]
        if not ref_indices or not cand_indices:
            return
        self.ref_base = max(ref_indices) + 1
        self.cand_base = max(cand_indices) + 1
        self.phase_entry_ref = self.ref_base - 1
        self.phase_entry_cand = self.cand_base - 1
        self.pairing = True


def _record_health(record: FrameRecord, health: gfx_diagnostics.GpuHealth) -> None:
    value = record.value
    if value.get("capture_valid") is False:
        health.add(
            "capture",
            f"{record.backend} frame {record.index} capture is invalid",
            source=str(record.path),
            symbol="frame",
            draw=str(record.index),
            detail={"error": value.get("error")},
        )
    if value.get("gpu_completed") is False:
        health.add(
            "command_buffer",
            f"{record.backend} frame {record.index} command buffer did not complete",
            source=str(record.path),
            symbol="frame",
            draw=str(record.index),
        )
    skipped = value.get("skipped_before")
    if type(skipped) is int and skipped > 0:
        health.add(
            "capture",
            f"{record.backend} skipped {skipped} logical frames before {record.index}",
            severity="warning",
            source=str(record.path),
            draw=str(record.index),
            signature=f"capture:skipped_frames:{record.backend}",
        )


def align_streams(state: CampaignState) -> list[dict]:
    """Pair observations by logical frame index.

    PR 2 uses the frame index as the correspondence key.  A pair is only
    ``aligned`` when both sides emitted the same index and both are valid; a
    gap or a missing side is reported as an alignment outcome, never as a pixel
    difference.  PR 3 adds semantic keys (scene/draw composition) on top.
    """
    new_pairs: list[dict] = []
    if not state.pairing:
        return new_pairs
    ref_base = state.ref_base or 0
    cand_base = state.cand_base or 0
    offsets = sorted(
        {index - ref_base for index in state.ref_by_index}
        & {index - cand_base for index in state.cand_by_index}
    )
    for offset in offsets:
        if offset < 0:
            continue
        ref_index = ref_base + offset
        cand_index = cand_base + offset
        ref = state.ref_by_index.pop(ref_index)
        cand = state.cand_by_index.pop(cand_index)
        ref_valid = ref.value.get("capture_valid") is True
        cand_valid = cand.value.get("capture_valid") is True
        extent = _extent_relation(ref, cand)
        reference_signature = gfx_align._signature(ref.value)
        candidate_signature = gfx_align._signature(cand.value)
        same_draws = (
            type(ref.value.get("draws")) is int
            and ref.value.get("draws") == cand.value.get("draws")
            and ref.value.get("draws") > 0
        )
        strong = (
            extent["normalized"]
            and same_draws
            and reference_signature
            and reference_signature == candidate_signature
        )
        if not ref_valid or not cand_valid:
            status = NOT_ALIGNED
        elif not strong:
            status = AMBIGUOUS
        elif ref.digest and ref.digest == cand.digest:
            status = ALIGNED
        else:
            status = ALIGNED
        pair = {
            "frame_index": ref_index,
            "candidate_frame_index": cand_index,
            "status": status,
            "reference": {
                "sha256": ref.digest,
                "width": ref.value.get("width"),
                "height": ref.value.get("height"),
                "draws": ref.value.get("draws"),
                "caller_signature": ref.value.get("caller_signature"),
            },
            "candidate": {
                "sha256": cand.digest,
                "width": cand.value.get("width"),
                "height": cand.value.get("height"),
                "draws": cand.value.get("draws"),
                "caller_signature": cand.value.get("caller_signature"),
            },
            "exact_equal": bool(ref.digest and ref.digest == cand.digest),
            "extent": _extent_relation(ref, cand),
            "preview": _preview_stats(ref, cand),
            "alignment_key": "frame_index",
        }
        state.pairs.append(pair)
        new_pairs.append(pair)
        state.first_pair = state.first_pair or pair
        state.last_pair = pair
        state.last_ref = max(state.last_ref, ref_index)
        state.last_cand = max(state.last_cand, cand_index)
    # Stale unpaired offsets indicate a gap on the other side.  The two sides
    # have different absolute ordinals, so compare relative offsets only.
    if offsets:
        latest = max(offsets)
        for table, base in ((state.ref_by_index, ref_base),
                            (state.cand_by_index, cand_base)):
            stale = [index for index in table if index - base < latest - 2]
            for index in stale:
                table.pop(index, None)
                state.misaligned += 1
    return new_pairs


def _tail(run_dir: Path) -> dict:
    return {
        "runs": str(run_dir),
        "health": gfx_diagnostics.scan_run(run_dir),
    }


def _request_dumps(launched: dict, indices: list[int]) -> Path | None:
    if not indices:
        return None
    path = launched["observe_dir"] / "capture-request.json"
    path.write_text(json.dumps({"schema": "gfx-frame-request/v1",
                                "frames": sorted(set(indices))}) + "\n",
                    encoding="utf-8")
    return path



def _prune_dumps(launched: dict, keep_indices: list[int]) -> None:
    """Keep the run directory bounded to the recent paired raws.

    A preserved failure pair has already been copied into the artifact store, so
    deleting older live dumps does not discard established evidence.
    """
    keep = {str(index) for index in keep_indices}
    captured = Path(launched["observe_dir"]) / "captured"
    if not captured.is_dir():
        return
    for path in captured.glob("frame-*"):
        stem = path.stem.split("-", 1)[-1]
        if stem not in keep:
            try:
                path.unlink()
            except OSError:
                pass


def _release(launched: dict, indices: list[int]) -> None:
    release = launched["observe_dir"] / "release"
    release.mkdir(parents=True, exist_ok=True)
    for index in indices:
        (release / f"frame-{index}").write_text("1\n", encoding="utf-8")


def _await_dump_meta(launched: dict, indices: list[int], timeout: float = 4.0) -> dict:
    captured = Path(launched["observe_dir"]) / "captured"
    wanted = set(indices)
    found: dict[int, dict] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and wanted:
        for index in sorted(wanted):
            path = captured / f"frame-{index}.json"
            if not path.is_file():
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            found[index] = value
            wanted.discard(index)
        if wanted:
            time.sleep(0.02)
    return {"found": found, "missing": sorted(wanted)}


def _collect_dumps(launched: dict, indices: list[int], store: gfx_artifacts.ArtifactStore,
                   timeout: float = 3.0) -> dict:
    captured = launched["run_dir"] / "observe" / "captured"
    wanted = set(indices)
    found: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for index in sorted(wanted):
            meta = captured / f"frame-{index}.json"
            if not meta.is_file():
                continue
            try:
                value = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw = Path(value.get("raw_file", ""))
            if not raw.is_file():
                continue
            stored = store.put_file(raw, kind="frame", preserved=True,
                                    logical_name=f"{launched['role']}-frame-{index}")
            found.append({
                "frame_index": index,
                "meta": value,
                "sha256": stored.sha256 if stored else value.get("sha256"),
                "preserved": bool(stored),
            })
            wanted.discard(index)
        if not wanted:
            break
        time.sleep(0.05)
    return {"captured": found, "missing": sorted(wanted)}


def _extent_relation(reference: FrameRecord, candidate: FrameRecord) -> dict:
    """Record the logical/physical extent relation and origin convention.

    A HiDPI candidate is expected to be an integer multiple of the reference.
    A non-integer relation is reported rather than silently treated as aligned.
    """
    ref_w = reference.value.get("width")
    ref_h = reference.value.get("height")
    cand_w = candidate.value.get("width")
    cand_h = candidate.value.get("height")
    relation = {
        "reference": [ref_w, ref_h],
        "candidate": [cand_w, cand_h],
        "reference_origin": reference.value.get("origin"),
        "candidate_origin": candidate.value.get("origin"),
        "scale": None,
        "normalized": False,
        "reason": "",
    }
    if not all(type(value) is int and value > 0 for value in
               (ref_w, ref_h, cand_w, cand_h)):
        relation["reason"] = "extent is missing"
        return relation
    if cand_w == ref_w and cand_h == ref_h:
        relation["scale"] = 1
        relation["normalized"] = True
        return relation
    if cand_w % ref_w == 0 and cand_h % ref_h == 0:
        scale_x = cand_w // ref_w
        scale_y = cand_h // ref_h
        if scale_x == scale_y and scale_x in (2, 3, 4):
            relation["scale"] = scale_x
            relation["normalized"] = True
            return relation
    relation["reason"] = "extents are not an integer logical/physical pair"
    return relation


def _preview_stats(reference: FrameRecord, candidate: FrameRecord) -> dict:
    """Small, bounded preview comparison for the PR 2 report.

    This is not the authoritative pixel comparator (PR 3).  It reports exact
    hash equality and a coarse preview difference so a runner defect is visible
    without claiming a rendering verdict.
    """
    ref_path = reference.path.parent / str(reference.value.get("preview_file") or "")
    cand_path = candidate.path.parent / str(candidate.value.get("preview_file") or "")
    try:
        ref = ref_path.read_bytes()
        cand = cand_path.read_bytes()
    except OSError:
        return {"status": "unavailable", "reason": "preview payload is absent"}
    if len(ref) != len(cand) or len(ref) % 4:
        return {"status": "unavailable", "reason": "preview extents differ"}
    pixels = len(ref) // 4
    different = 0
    max_error = 0
    total = 0
    for offset in range(0, len(ref), 4):
        delta = max(abs(ref[offset] - cand[offset]),
                    abs(ref[offset + 1] - cand[offset + 1]),
                    abs(ref[offset + 2] - cand[offset + 2]))
        if delta:
            different += 1
            max_error = max(max_error, delta)
        total += delta
    return {
        "status": "compared",
        "pixels": pixels,
        "different_pixels": different,
        "mismatched_fraction": round(different / pixels, 6) if pixels else 0.0,
        "max_channel_error": max_error,
        "mean_abs_error": round(total / (pixels * 3), 6) if pixels else 0.0,
    }



def _compare_pair(pair: dict, reference_meta: dict | None,
                  candidate_meta: dict | None, scenario: dict) -> dict:
    """Compare one lockstep pair from its preserved raw dumps.

    A pair whose alignment evidence is too weak is not compared; a pixel
    difference is only a rendering verdict for a strong alignment.
    """
    if not reference_meta or not candidate_meta:
        return {
            "status": "not_compared",
            "reason": "one or both paired raw frames are unavailable",
        }
    reference_record = {
        "frame_index": pair["frame_index"],
        "width": reference_meta.get("width"),
        "height": reference_meta.get("height"),
        "origin": reference_meta.get("origin"),
        "caller_signature": reference_meta.get("caller_signature"),
        "draws": reference_meta.get("draws"),
    }
    candidate_record = {
        "frame_index": pair.get("candidate_frame_index"),
        "width": candidate_meta.get("width"),
        "height": candidate_meta.get("height"),
        "origin": candidate_meta.get("origin"),
        "caller_signature": candidate_meta.get("caller_signature"),
        "draws": candidate_meta.get("draws"),
    }
    score, evidence = gfx_align.match_score(reference_record, candidate_record)
    relation = gfx_align.extent_relation(reference_record, candidate_record)
    reference_signature = gfx_align._signature(reference_record)
    candidate_signature = gfx_align._signature(candidate_record)
    same_draws = (
        type(reference_record.get("draws")) is int
        and reference_record.get("draws") == candidate_record.get("draws")
        and reference_record.get("draws") > 0
    )
    strong = (
        relation["normalized"]
        and "scene_mismatch" not in evidence
        and same_draws
        and (
            (reference_signature and reference_signature == candidate_signature)
            or "scene" in evidence
        )
    )
    alignment = {
        "score": score,
        "evidence": evidence,
        "extent": relation,
        "draw_counts": [reference_record.get("draws"),
                        candidate_record.get("draws")],
        "signatures": [reference_signature, candidate_signature],
        "status": gfx_align.ALIGNED if strong else gfx_align.AMBIGUOUS,
    }
    if alignment["status"] != gfx_align.ALIGNED:
        return {"status": "not_compared", "reason": "alignment is ambiguous",
                "alignment": alignment}
    policy = scenario["comparison"]
    minimum_draws = int((scenario.get("settle") or {}).get("min_draws", 0))
    if minimum_draws > 0 and min(
        reference_record.get("draws") or 0,
        candidate_record.get("draws") or 0,
    ) < minimum_draws:
        return {
            "status": "not_compared",
            "reason": "scene has not reached the settle draw threshold "
                      f"({reference_record.get('draws')}/"
                      f"{candidate_record.get('draws')} < {minimum_draws})",
            "alignment": alignment,
        }
    try:
        content = _content_corroboration(
            Path(reference_meta["raw_file"]), reference_meta,
            Path(candidate_meta["raw_file"]), candidate_meta,
            policy,
        )
    except Exception as exc:  # noqa: BLE001
        content = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    if content.get("veto"):
        alignment["status"] = gfx_align.AMBIGUOUS
        alignment["content"] = content
        return {
            "status": "not_compared",
            "reason": "coarse content does not corroborate the semantic alignment",
            "alignment": alignment,
        }
    alignment["content"] = content
    compare_policy = {
        "policy": policy["policy"],
        "pixel_tolerance": policy["max_channel_error"],
        "max_mismatched_fraction": policy["max_mismatched_fraction"],
        "max_mean_abs_error": policy["max_mean_abs_error"],
        "allow_scale_normalization": True,
    }
    try:
        reference_payload = Path(reference_meta["raw_file"]).read_bytes()
        candidate_payload = Path(candidate_meta["raw_file"]).read_bytes()
        result = gfx_frame_compare.compare_payloads(
            reference_payload, reference_meta, candidate_payload, candidate_meta,
            policy=compare_policy, regions=policy["regions"],
        )
    except (OSError, gfx_frame_compare.CompareError) as exc:
        return {"status": gfx_frame_compare.UNVERIFIED,
                "reason": f"{type(exc).__name__}: {exc}",
                "alignment": alignment}
    result["alignment"] = alignment
    return result



def _content_corroboration(reference_path: Path, reference_meta: dict,
                           candidate_path: Path, candidate_meta: dict,
                           policy: dict) -> dict:
    """Use coarse content as a *veto* on a semantic alignment, never as proof.

    Caller signatures can repeat across loading/menu frames.  If the normalized
    images correlate far too weakly to be the same scene, the pair is an
    alignment problem rather than a rendering verdict.  A strongly mirrored
    image is not vetoed: that is exactly the orientation failure the comparator
    must detect.
    """
    try:
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("numpy is required") from exc
    reference = gfx_frame_compare.decode_frame(
        reference_path.read_bytes(), int(reference_meta["width"]),
        int(reference_meta["height"]),
        origin=str(reference_meta.get("origin", "top_left")),
        pixel_format=str(reference_meta.get("pixel_format", "BGRA8")),
    )
    candidate = gfx_frame_compare.decode_frame(
        candidate_path.read_bytes(), int(candidate_meta["width"]),
        int(candidate_meta["height"]),
        origin=str(candidate_meta.get("origin", "top_left")),
        pixel_format=str(candidate_meta.get("pixel_format", "BGRA8")),
    )
    candidate, _normalization = gfx_frame_compare.normalize_candidate(
        reference, candidate, policy={"allow_scale_normalization": True},
        candidate_meta=candidate_meta,
    )
    height, width = reference.shape[:2]

    def coarse(image):
        gray = image[:, :, :3].astype(np.float32).mean(axis=2)
        rows = 41
        columns = 64
        y_edges = np.linspace(0, height, rows + 1).astype(int)
        x_edges = np.linspace(0, width, columns + 1).astype(int)
        out = np.zeros((rows, columns), dtype=np.float32)
        for row in range(rows):
            for column in range(columns):
                block = gray[y_edges[row]:max(y_edges[row] + 1, y_edges[row + 1]),
                             x_edges[column]:max(x_edges[column] + 1, x_edges[column + 1])]
                out[row, column] = float(block.mean()) if block.size else 0.0
        return out

    left = coarse(reference).reshape(-1)
    right = coarse(candidate).reshape(-1)
    flipped = coarse(candidate[:, ::-1, :]).reshape(-1)
    normal_correlation = gfx_frame_compare._correlation(left, right) or 0.0
    flipped_correlation = gfx_frame_compare._correlation(left, flipped) or 0.0
    threshold = float(policy.get("alignment_min_correlation", 0.35))
    mirrored_signal = (
        flipped_correlation > normal_correlation + 0.10
        and flipped_correlation >= threshold
    )
    veto = normal_correlation < threshold and not mirrored_signal
    return {
        "status": "compared",
        "normal_correlation": round(normal_correlation, 6),
        "flipped_correlation": round(flipped_correlation, 6),
        "threshold": threshold,
        "mirrored_signal": bool(mirrored_signal),
        "veto": bool(veto),
    }


def _preserve_pair(store: gfx_artifacts.ArtifactStore, launched: dict,
                   pair: dict) -> None:
    for role, key in (("reference", "reference"), ("candidate", "candidate")):
        meta = (pair.get("dumps") or {}).get(key, {}).get("found", {})
        for index, value in meta.items():
            raw = Path(value.get("raw_file", ""))
            if raw.is_file():
                store.put_file(raw, kind="frame", preserved=True,
                               logical_name=f"{role}-frame-{index}")



def _comparison_summary(pairs: list[dict], scenario: dict) -> dict:
    compared = [pair for pair in pairs if isinstance(pair.get("comparison"), dict)
                and pair["comparison"].get("status") in
                (gfx_frame_compare.PASS, gfx_frame_compare.FAIL,
                 gfx_frame_compare.DIAGNOSTIC, gfx_frame_compare.UNVERIFIED)]
    failures = [pair for pair in compared
                if pair["comparison"].get("status") == gfx_frame_compare.FAIL]
    unverified = [pair for pair in compared
                  if pair["comparison"].get("status") == gfx_frame_compare.UNVERIFIED]
    return {
        "status": (
            gfx_frame_compare.FAIL if failures
            else (gfx_frame_compare.UNVERIFIED if unverified
                  else (gfx_frame_compare.PASS if compared else "not_run"))
        ),
        "policy": scenario["comparison"]["policy"],
        "compared": len(compared),
        "passed": sum(1 for pair in compared
                      if pair["comparison"].get("status") == gfx_frame_compare.PASS),
        "failed": len(failures),
        "unverified": len(unverified),
        "first_failure": failures[0] if failures else None,
        "not_compared": sum(
            1 for pair in pairs
            if isinstance(pair.get("comparison"), dict)
            and pair["comparison"].get("status") == "not_compared"
        ),
    }


def _stop_processes(session: gfx_session.Session, *, resume_first: bool = False) -> list[dict]:
    if resume_first:
        for identity in session.processes.values():
            try:
                if identity.matches():
                    os.killpg(identity.pgid, signal.SIGCONT)
            except OSError:
                pass
    return [item.as_dict() for item in session.terminate_all(grace_seconds=6.0)]



def _latest(stream: FrameStream) -> FrameRecord | None:
    return stream.records[-1] if stream.records else None


def _release_once(launched: dict, state: CampaignState, role: str,
                  index: int) -> None:
    released = state.released_ref if role == "reference" else state.released_cand
    if index in released:
        return
    _release({"observe_dir": launched[role].observe_dir}, [index])
    released.add(index)


def run_campaign(
    scenario: dict,
    *,
    run_root: Path,
    ledger_path: Path,
    shader_cache: Path | None,
    qualified_sources: Path | None,
    mode: str | None = None,
    max_frames: int | None = None,
    profile: str | None = None,
    keep_evidence: bool = True,
    runner: Callable | None = None,
    session_root: Path | None = None,
    staggered: bool = False,
    stagger_wait_seconds: float = 600.0,
) -> dict:
    run_root = Path(run_root).expanduser().resolve()
    ledger_path = Path(ledger_path).expanduser().resolve()
    shader_cache = Path(shader_cache).expanduser().resolve() if shader_cache else None
    qualified_sources = (
        Path(qualified_sources).expanduser().resolve() if qualified_sources else None
    )
    mode = mode or scenario["mode"]
    max_frames = max_frames or scenario["observation"]["max_frames"]
    wait_profile = gfx_wait.profile(profile or scenario["profile"])
    started = time.time()
    identity = base_benchmark.gog_identity()
    if identity["executable_sha256"] != scenario["executable_sha256"]:
        raise gfx_launch.LaunchError(
            "identity",
            "scenario executable_sha256 does not match the installed pinned build",
        )
    executable = Path(scenario.get("executable") or identity["executable"])
    preflight = gfx_launch.preflight_user_data(executable)

    session = gfx_session.Session.create(
        Path(run_root),
        workflow_id=str(Path(ledger_path).stem),
        profile=wait_profile.name,
        scenario_digest=scenario["digest"],
        session_root_path=session_root,
    )
    result: dict = {
        "schema": SCHEMA,
        "session_id": session.session_id,
        "run_dir": str(session.run_dir),
        "scenario": {
            "name": scenario["name"],
            "digest": scenario["digest"],
            "mode": mode,
            "profile": wait_profile.name,
            "logical_extent": scenario["logical_extent"],
            "comparison_policy": scenario["comparison"]["policy"],
            "comparison": scenario["comparison"],
        },
        "build": {},
        "processes": {},
        "preflight": preflight,
        "frames": {},
        "health": {},
        "dumps": {},
        "stop": {},
        "ledger": {},
        "terminations": [],
        "result": "incomplete",
    }
    tokens: list[str] = []
    lease = None
    launched: dict = {}
    store = gfx_artifacts.ArtifactStore(
        session.run_dir / "artifacts", max_bytes=128 * 1024 * 1024
    )
    flight = gfx_flight.FlightRecorder(max_frames=min(256, max(16, max_frames)),
                                       max_events=512)
    try:
        tokens = launch_ledger.claim_many(
            ledger_path,
            campaign_id="dual-run",
            run_root=session.run_dir,
            outputs=[session.run_dir / "reference", session.run_dir / "candidate"],
            targets=["frame"],
            requested_timeout=float(scenario["observation"]["max_runtime_seconds"]),
            executable_sha256=identity["executable_sha256"],
            executable_path=identity["executable"],
            require_pinned=True,
            roles=["reference", "candidate"],
        )
        result["ledger"] = {"tokens": tokens, "claimed": 2}
        libraries = gfx_launch.build_libraries()
        result["build"] = {
            "reference_library": str(libraries["reference"]),
            "reference_library_sha256": gfx_artifacts.sha256_file(libraries["reference"]),
            "candidate_library": str(libraries["candidate"]),
            "candidate_library_sha256": gfx_artifacts.sha256_file(libraries["candidate"]),
            "executable_sha256": identity["executable_sha256"],
        }
        config = gfx_launch.LaunchConfig(
            executable=executable,
            args=list(scenario["args"]),
            executable_sha256=identity["executable_sha256"],
            logical_extent=tuple(scenario["logical_extent"]),
            qualified_sources=Path(qualified_sources) if qualified_sources else None,
            shader_cache=Path(shader_cache) if shader_cache else None,
            ring=int(scenario["observation"]["ring"]),
            deadline_seconds=float(scenario["observation"]["max_runtime_seconds"]),
            lockstep=mode == "lockstep",
            metadata_only=True,
            session_id=session.session_id,
        )
        lease = launch_lease.acquire(executable, output=session.run_dir)
        # Start both backends before waiting on either one's readiness.
        health = gfx_diagnostics.GpuHealth()
        prelaunched_ref_stream = None
        launched["reference"] = gfx_launch.spawn(
            "reference", config, session.run_dir / "reference",
            lease=lease, session=session, token=tokens[0], libraries=libraries,
            backend=scenario.get("reference_backend", "opengl"),
        )
        launch_ledger.start(ledger_path, launched["reference"].token,
                            launched["reference"].process.pid)
        result["processes"]["reference"] = launched["reference"].as_dict()
        if staggered:
            # Let the (slower) reference reach the target scene before starting
            # the candidate.  Both processes still overlap for the whole
            # differential interval; this only removes dead time where the
            # candidate rendered an empty screen while the reference loaded.
            minimum_draws = int((scenario.get("settle") or {}).get("min_draws", 0))
            prelaunched_ref_stream = FrameStream(
                launched["reference"].observe_dir / "frame-observe.jsonl")
            wait_deadline = time.monotonic() + max(30.0, stagger_wait_seconds)
            while time.monotonic() < wait_deadline:
                for record in prelaunched_ref_stream.poll():
                    _record_health(record, health)
                latest = _latest(prelaunched_ref_stream)
                draws = (latest.value.get("draws") if latest else 0) or 0
                if minimum_draws == 0 or draws >= minimum_draws:
                    result["stagger"] = {
                        "reference_ready_seconds": round(
                            max(0.0, stagger_wait_seconds - (wait_deadline - time.monotonic())), 3),
                        "reference_frame": latest.index if latest else None,
                        "reference_draws": draws,
                    }
                    break
                if launched["reference"].process.poll() is not None:
                    result["stagger"] = {"status": "reference_exited_before_phase",
                                         "reference_draws": draws}
                    raise _Finished()
                time.sleep(0.1)
            else:
                result["stagger"] = {"status": "phase_timeout",
                                     "reference_draws": draws}
                result["stop"] = {"reason": "reference_phase_timeout",
                                  "trigger": f"{stagger_wait_seconds}s"}
                result["result"] = "incomplete"
                raise _Finished()
        launched["candidate"] = gfx_launch.spawn(
            "candidate", config, session.run_dir / "candidate",
            lease=lease, session=session, token=tokens[1], libraries=libraries,
            backend=scenario.get("candidate_backend", "metal"),
        )
        launch_ledger.start(ledger_path, launched["candidate"].token,
                            launched["candidate"].process.pid)
        result["processes"]["candidate"] = launched["candidate"].as_dict()

        def abort_for_exit() -> bool:
            return any(item.process.poll() is not None for item in launched.values())

        def role_ready(role: str) -> bool:
            item = launched[role]
            if item.backend == "metal":
                return gfx_launch.candidate_initialized(
                    item.run_dir, item.process.pid)
            return gfx_launch.reference_installed(item.run_dir, item.process.pid)

        def both_ready() -> bool:
            return (
                role_ready("reference") and role_ready("candidate")
                and gfx_launch.observation_lines(launched["reference"].run_dir) > 0
                and gfx_launch.observation_lines(launched["candidate"].run_dir) > 0
            )

        readiness = gfx_wait.wait_for(
            {"both_backends_ready": both_ready},
            wait_profile=wait_profile.stage(
                base_seconds=float(scenario["settle"]["timeout_seconds"]),
                hard_seconds=float(scenario["settle"]["timeout_seconds"]) * 2,
                idle_seconds=min(30.0, float(scenario["settle"]["timeout_seconds"]) / 2),
            ),
            progress=lambda: gfx_wait.Progress(
                "startup",
                "observations",
                0.0,
                counter=float(
                    gfx_launch.observation_lines(launched["reference"].run_dir)
                    + gfx_launch.observation_lines(launched["candidate"].run_dir)
                ),
            ),
            aborts={"process_exit": abort_for_exit},
            is_alive=lambda: not abort_for_exit(),
            stage="startup",
        )
        result["readiness"] = readiness.as_dict()
        if not readiness.ok:
            result["stop"] = {
                "reason": f"readiness_{readiness.status}",
                "detail": readiness.as_dict(),
                "trigger": "startup",
            }
            result["result"] = "incomplete"
            raise _Finished()

        state = CampaignState(
            ref_stream=prelaunched_ref_stream or FrameStream(
                launched["reference"].observe_dir / "frame-observe.jsonl"),
            cand_stream=FrameStream(launched["candidate"].observe_dir / "frame-observe.jsonl"),
        )
        deadline = time.monotonic() + float(scenario["observation"]["max_runtime_seconds"])
        settle_frames = int(scenario["settle"]["min_frames"])
        while time.monotonic() < deadline:
            for record in state.ref_stream.poll():
                state.ref_by_index[record.index] = record
                _record_health(record, health)
            for record in state.cand_stream.poll():
                state.cand_by_index[record.index] = record
                _record_health(record, health)
            if (len(state.ref_stream.records) >= settle_frames
                    and len(state.cand_stream.records) >= settle_frames):
                break
            if any(item.process.poll() is not None for item in launched.values()):
                break
            time.sleep(0.02)
        minimum_draws = int((scenario.get("settle") or {}).get("min_draws", 0))
        if mode == "streaming" and minimum_draws > 0:
            phase_deadline = time.monotonic() + float(scenario["settle"]["timeout_seconds"])
            while time.monotonic() < phase_deadline:
                for record in state.ref_stream.poll():
                    state.ref_by_index[record.index] = record
                    _record_health(record, health)
                for record in state.cand_stream.poll():
                    state.cand_by_index[record.index] = record
                    _record_health(record, health)
                latest_ref = _latest(state.ref_stream)
                latest_cand = _latest(state.cand_stream)
                ref_draws = (latest_ref.value.get("draws") if latest_ref else 0) or 0
                cand_draws = (latest_cand.value.get("draws") if latest_cand else 0) or 0
                if (ref_draws >= minimum_draws and cand_draws >= minimum_draws):
                    break
                if any(item.process.poll() is not None for item in launched.values()):
                    break
                time.sleep(0.02)
        if mode == "streaming" and minimum_draws > 0:
            # Pre-request a small full-resolution window at the start of the
            # target phase.  Metadata-only observation is cheap enough to reach
            # the scene; these frames carry the raw payloads for comparison.
            latest_ref = _latest(state.ref_stream)
            latest_cand = _latest(state.cand_stream)
            if latest_ref is not None and latest_cand is not None:
                window = 32
                _request_dumps(
                    {"observe_dir": launched["reference"].observe_dir},
                    list(range(latest_ref.index + 1, latest_ref.index + 1 + window)),
                )
                _request_dumps(
                    {"observe_dir": launched["candidate"].observe_dir},
                    list(range(latest_cand.index + 1, latest_cand.index + 1 + window)),
                )
        if mode == "lockstep":
            # Do not pair until both sides have reached the target scene.  The
            # side that arrives first stays paused while the other finishes
            # loading; loading progress is never compared.
            state.pairing = False
            state.ref_base = None
            state.cand_base = None
        else:
            state.begin_pairing()
        result["alignment"] = {
            "anchor": "post-readiness relative frame ordinal in the target phase",
            "reference_base": state.ref_base,
            "candidate_base": state.cand_base,
            "semantic": False,
            "phase": {
                "name": "target" if minimum_draws else "all",
                "min_draws": minimum_draws,
                "reference_entry_index": state.phase_entry_ref,
                "candidate_entry_index": state.phase_entry_cand,
            },
        }
        stop_reason = ""
        stop_trigger = ""
        while time.monotonic() < deadline:
            new_ref = state.ref_stream.poll()
            new_cand = state.cand_stream.poll()
            for record in new_ref:
                state.ref_by_index[record.index] = record
                _record_health(record, health)
            for record in new_cand:
                state.cand_by_index[record.index] = record
                _record_health(record, health)
            if not state.pairing and mode == "lockstep":
                latest_ref = _latest(state.ref_stream)
                latest_cand = _latest(state.cand_stream)
                ref_draws = (latest_ref.value.get("draws") if latest_ref else 0) or 0
                cand_draws = (latest_cand.value.get("draws") if latest_cand else 0) or 0
                if latest_ref is not None and latest_cand is not None and (
                    minimum_draws == 0
                    or (ref_draws >= minimum_draws and cand_draws >= minimum_draws)
                ):
                    state.begin_pairing()
                    state.released_ref.add(latest_ref.index)
                    state.released_cand.add(latest_cand.index)
                    _release({"observe_dir": launched["reference"].observe_dir},
                             [latest_ref.index])
                    _release({"observe_dir": launched["candidate"].observe_dir},
                             [latest_cand.index])
                    window = min(48, max_frames)
                    _request_dumps(
                        {"observe_dir": launched["reference"].observe_dir},
                        list(range(state.ref_base, state.ref_base + window)),
                    )
                    _request_dumps(
                        {"observe_dir": launched["candidate"].observe_dir},
                        list(range(state.cand_base, state.cand_base + window)),
                    )
                    result["alignment"] = {
                        "anchor": "relative frame ordinal after both reached the target phase",
                        "reference_base": state.ref_base,
                        "candidate_base": state.cand_base,
                        "semantic": True,
                        "phase": {
                            "name": "target",
                            "min_draws": minimum_draws,
                            "reference_entry_index": state.phase_entry_ref,
                            "candidate_entry_index": state.phase_entry_cand,
                            "reference_draws": ref_draws,
                            "candidate_draws": cand_draws,
                        },
                    }
                else:
                    # Advance only the side(s) still below the phase threshold.
                    if latest_ref is not None and ref_draws < minimum_draws:
                        _release_once(launched, state, "reference", latest_ref.index)
                    if latest_cand is not None and cand_draws < minimum_draws:
                        _release_once(launched, state, "candidate", latest_cand.index)
                    pairs = []
            pairs = align_streams(state)
            for pair in pairs:
                flight.frame({
                    "frame_index": pair.get("frame_index"),
                    "candidate_frame_index": pair.get("candidate_frame_index"),
                    "status": pair.get("status"),
                    "exact_equal": pair.get("exact_equal"),
                    "preview": pair.get("preview"),
                    "comparison": (
                        {key: pair["comparison"].get(key)
                         for key in ("status", "reasons", "mirrored_regions")}
                        if isinstance(pair.get("comparison"), dict) else None
                    ),
                })
            if pairs:
                for pair in pairs:
                    ref_index = pair["frame_index"]
                    cand_index = pair.get("candidate_frame_index", ref_index)
                    settle_draws = int((scenario.get("settle") or {}).get("min_draws", 0))
                    pair_draws = (
                        (pair.get("reference") or {}).get("draws") or 0,
                        (pair.get("candidate") or {}).get("draws") or 0,
                    )
                    if settle_draws and min(pair_draws) < settle_draws:
                        # Loading progress is not comparable; do not pay for a
                        # full-resolution dump.
                        pair["comparison"] = {
                            "status": "not_compared",
                            "reason": f"below settle draw threshold {pair_draws} < {settle_draws}",
                        }
                        if mode == "lockstep":
                            _release({"observe_dir": launched["reference"].observe_dir},
                                     [ref_index])
                            _release({"observe_dir": launched["candidate"].observe_dir},
                                     [cand_index])
                            state.lockstep_stops += 1
                        continue
                    _request_dumps({"observe_dir": launched["reference"].observe_dir},
                                   [ref_index])
                    _request_dumps({"observe_dir": launched["candidate"].observe_dir},
                                   [cand_index])
                    if ref_index % 32 == 0:
                        _request_dumps(
                            {"observe_dir": launched["reference"].observe_dir},
                            list(range(ref_index, ref_index + 32)),
                        )
                        _request_dumps(
                            {"observe_dir": launched["candidate"].observe_dir},
                            list(range(cand_index, cand_index + 32)),
                        )
                    dump_timeout = 4.0 if mode == "lockstep" else 1.5
                    reference_dump = _await_dump_meta(
                        {"observe_dir": launched["reference"].observe_dir}, [ref_index],
                        timeout=dump_timeout)
                    candidate_dump = _await_dump_meta(
                        {"observe_dir": launched["candidate"].observe_dir}, [cand_index],
                        timeout=dump_timeout)
                    pair["dumps"] = {
                        "reference": reference_dump,
                        "candidate": candidate_dump,
                    }
                    comparison = _compare_pair(
                        pair, reference_dump.get("found", {}).get(ref_index),
                        candidate_dump.get("found", {}).get(cand_index),
                        scenario,
                    )
                    pair["comparison"] = comparison
                    if comparison.get("status") in (gfx_frame_compare.FAIL,
                                                    gfx_frame_compare.UNVERIFIED):
                        stop_reason = "frame_difference" if comparison.get("status") == gfx_frame_compare.FAIL else "comparison_unverified"
                        stop_trigger = (
                            f"frame {ref_index}/{cand_index}: "
                            + "; ".join(comparison.get("reasons", [])[:3])
                        )
                        if comparison.get("status") == gfx_frame_compare.FAIL:
                            _preserve_pair(store, launched, pair)
                        if mode == "lockstep":
                            _release({"observe_dir": launched["reference"].observe_dir},
                                     [ref_index])
                            _release({"observe_dir": launched["candidate"].observe_dir},
                                     [cand_index])
                        break
                    if mode == "lockstep":
                        _release({"observe_dir": launched["reference"].observe_dir},
                                 [ref_index])
                        _release({"observe_dir": launched["candidate"].observe_dir},
                                 [cand_index])
                    state.released_ref.add(ref_index)
                    state.released_cand.add(cand_index)
                    recent = state.pairs[-2:]
                    _prune_dumps(
                        {"observe_dir": launched["reference"].observe_dir},
                        [pair["frame_index"] for pair in recent],
                    )
                    _prune_dumps(
                        {"observe_dir": launched["candidate"].observe_dir},
                        [pair.get("candidate_frame_index", pair["frame_index"])
                         for pair in recent],
                    )
                    if mode == "lockstep":
                        state.lockstep_stops += 1
                    if stop_reason:
                        break
            if stop_reason:
                break
            if results := [pair for pair in pairs if pair["status"] == NOT_ALIGNED]:
                stop_reason = "alignment_failure"
                stop_trigger = f"frame {results[0]['frame_index']} is not aligned"
                break
            gate = gfx_diagnostics.health_gate(health.summary())
            if not gate["allow"]:
                stop_reason = "gpu_execution_failure"
                stop_trigger = gate["reason"]
                break
            exited = [role for role, item in launched.items() if item.process.poll() is not None]
            if exited:
                stop_reason = "process_exit"
                stop_trigger = ", ".join(exited)
                break
            if len(state.pairs) >= max_frames:
                stop_reason = "frame_limit"
                stop_trigger = f"{len(state.pairs)} aligned frames"
                break
            if not new_ref and not new_cand:
                time.sleep(0.02)
        if not stop_reason:
            stop_reason = "deadline"
            stop_trigger = f"{scenario['observation']['max_runtime_seconds']}s"

        result["frames"] = {
            "observed_reference": len(state.ref_stream.records),
            "observed_candidate": len(state.cand_stream.records),
            "paired": len(state.pairs),
            "aligned": sum(1 for pair in state.pairs if pair["status"] == ALIGNED),
            "not_aligned": sum(1 for pair in state.pairs if pair["status"] != ALIGNED),
            "misaligned_gaps": state.misaligned,
            "malformed_reference": state.ref_stream.malformed,
            "malformed_candidate": state.cand_stream.malformed,
            "lockstep_stops": state.lockstep_stops,
            "first_pair": state.first_pair,
            "last_pair": state.last_pair,
            "policy": scenario["comparison"]["policy"],
            "comparison": _comparison_summary(state.pairs, scenario),
        }
        stop = {
            "reason": stop_reason,
            "trigger": stop_trigger,
            "observed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        result["stop"] = stop
        flight.freeze(stop_reason)
        flight_path = flight.write(session.run_dir / "flight-recorder.json")
        result["flight_recorder"] = str(flight_path)
        store.put_file(flight_path, kind="flight", preserved=True,
                       logical_name="flight-recorder")

        dump_count = int(scenario["observation"]["dump_on_stop"])
        selected = state.pairs[-dump_count:] if dump_count and state.pairs else []
        wanted_by_role = {
            "reference": [pair["frame_index"] for pair in selected],
            "candidate": [pair.get("candidate_frame_index", pair["frame_index"])
                          for pair in selected],
        }
        if selected:
            for role, item in launched.items():
                _request_dumps({"observe_dir": item.observe_dir},
                               wanted_by_role[role])
            # The observers answer from their bounded recent-frame ring.
            result["dumps"] = {
                role: _collect_dumps(
                    {"role": role, "run_dir": item.run_dir,
                     "observe_dir": item.observe_dir},
                    wanted_by_role[role], store,
                )
                for role, item in launched.items()
            }
        else:
            result["dumps"] = {"requested": [], "note": "no aligned frame to preserve"}

        reference_health = gfx_diagnostics.scan_run(launched["reference"].run_dir)
        candidate_health = gfx_diagnostics.scan_run(launched["candidate"].run_dir)
        merged = gfx_diagnostics.GpuHealth()
        for summary in (health.summary(), reference_health, candidate_health):
            for row in summary.get("aggregated", []):
                merged.add(
                    row.get("kind", "other"),
                    row.get("message", ""),
                    severity=row.get("severity", "error") if row.get("severity") in ("error", "warning", "info") else "error",
                    source=row.get("source", ""),
                    detail=row.get("detail") or {},
                    signature=row.get("signature"),
                )
        result["health"] = merged.summary()
        result["health"]["gate"] = gfx_diagnostics.health_gate(
            result["health"],
            result_requires_pixels=scenario["comparison"]["policy"] == "exact",
        )
        result["result"] = "completed" if stop_reason == "frame_limit" else "stopped"
        result["artifacts"] = store.summary()
        (session.run_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except _Finished:
        pass
    except BaseException as exc:  # noqa: BLE001 - every launch must be accounted for
        flight.freeze("exception")
        try:
            flight.write(session.run_dir / "flight-recorder.json")
        except Exception:  # noqa: BLE001
            pass
        result["result"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result.setdefault("stop", {"reason": "exception", "trigger": result["error"]})
        (session.run_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    finally:
        for role, item in launched.items():
            result.setdefault("processes", {}).setdefault(role, {})["exit_code"] = item.process.poll()
        if mode == "lockstep":
            for role, item in launched.items():
                pending = [record.index for record in
                           (state.ref_stream.records if role == "reference"
                            else state.cand_stream.records)]
                if pending:
                    _release({"observe_dir": item.observe_dir}, pending[-4:])
        result["terminations"] = _stop_processes(session, resume_first=mode == "lockstep")
        if lease is not None:
            lease.release()
            lease = None
        for role, item in launched.items():
            ledger_status = "completed" if item.process.returncode == 0 else "failed"
            launch_ledger.finish(ledger_path, item.token, pid=item.process.pid,
                                 status=ledger_status)
        try:
            manifest = store.write_manifest()
            result["artifacts"] = store.summary()
            result["artifact_manifest"] = str(manifest)
        except Exception as exc:  # noqa: BLE001
            result["artifact_error"] = str(exc)
        session.close(
            status=gfx_session.CLOSED if result.get("result") in ("completed", "stopped")
            else gfx_session.FAILED,
            reason=result.get("stop", {}).get("reason", ""),
            extra={"result": result.get("result"), "frames": result.get("frames", {})},
        )
        result["terminal"] = gfx_session.Session.load(session.run_dir).get("terminal")
        if not keep_evidence:
            session.cleanup_temp()
        result["finished_unix"] = time.time()
        result["duration_seconds"] = round(result["finished_unix"] - started, 3)
        (session.run_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return result


class _Finished(Exception):
    """Internal control flow: a readiness failure already recorded a result."""


def _parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run one concurrent OpenGL-vs-Metal differential campaign",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="start and supervise a dual-process campaign")
    run.add_argument("--scenario", required=True, type=Path)
    run.add_argument("--run-root", required=True, type=Path)
    run.add_argument("--ledger", required=True, type=Path)
    run.add_argument("--shader-cache", type=Path, default=None)
    run.add_argument("--qualified-sources", type=Path,
                     default=ROOT / "analysis" / "gfx-shader" / "v0" / "qualified-sources.json")
    run.add_argument("--mode", choices=("streaming", "lockstep"), default=None)
    run.add_argument("--max-frames", type=int, default=None)
    run.add_argument("--profile", choices=("early", "targeted", "qualification"),
                     default=None)
    run.add_argument("--session-root", type=Path, default=None)
    run.add_argument("--staggered", action="store_true",
                     help="start the reference and let it reach the target scene before the candidate")
    run.add_argument("--stagger-wait", type=float, default=600.0)
    run.add_argument("--out", type=Path, default=None)

    template = sub.add_parser("template", help="write a validated scenario template")
    template.add_argument("--out", required=True, type=Path)
    template.add_argument("--executable-sha256", required=True)
    template.add_argument("--width", type=int, default=1728)
    template.add_argument("--height", type=int, default=1117)
    template.add_argument("--max-frames", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        value = gfx_scenario.write_template(
            args.out, executable_sha256=args.executable_sha256,
            extent=(args.width, args.height), max_frames=args.max_frames,
        )
        print(json.dumps(value, indent=2))
        return 0
    try:
        scenario = gfx_scenario.load(args.scenario)
        result = run_campaign(
            scenario,
            run_root=args.run_root,
            ledger_path=args.ledger,
            shader_cache=args.shader_cache,
            qualified_sources=args.qualified_sources,
            mode=args.mode,
            max_frames=args.max_frames,
            profile=args.profile,
            session_root=args.session_root,
            staggered=args.staggered,
            stagger_wait_seconds=args.stagger_wait,
        )
    except (gfx_scenario.ScenarioError, gfx_launch.LaunchError,
            launch_ledger.LedgerError, launch_lease.LeaseError) as exc:
        print(json.dumps({"schema": SCHEMA, "result": "refused",
                          "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 3
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    print(json.dumps({
        "schema": result.get("schema"),
        "session_id": result.get("session_id"),
        "result": result.get("result"),
        "stop": result.get("stop"),
        "frames": result.get("frames", {}).get("paired"),
        "health": result.get("health", {}).get("verdict"),
        "run_dir": result.get("run_dir"),
    }, indent=2))
    return 0 if result.get("result") in ("completed", "stopped") else 2


if __name__ == "__main__":
    raise SystemExit(main())
