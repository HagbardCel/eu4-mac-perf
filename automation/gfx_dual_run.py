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

import itertools
import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from automation import gfx_align, gfx_artifacts, gfx_diagnostics, gfx_flight, gfx_frame_compare
from automation import gfx_display, gfx_launch, gfx_scenario
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
    stability: dict = field(default_factory=lambda: {
        "reference": {"last_digest": None, "streak": 0, "max_streak": 0},
        "candidate": {"last_digest": None, "streak": 0, "max_streak": 0},
    })
    identity_streak: int = 0
    identity_max_streak: int = 0
    last_identity: int = 0
    samples: list[dict] = field(default_factory=list)

    def note_digest(self, role: str, digest: str | None) -> None:
        if not digest:
            return
        item = self.stability[role]
        if digest == item["last_digest"]:
            item["streak"] += 1
        else:
            item["last_digest"] = digest
            item["streak"] = 1
        item["max_streak"] = max(item["max_streak"], item["streak"])

    def note_identity(self, reference: dict, candidate: dict) -> None:
        ref = _identity_value(reference)
        cand = _identity_value(candidate)
        if not ref or ref != cand:
            self.identity_streak = 0
            return
        if ref == self.last_identity:
            self.identity_streak += 1
        else:
            self.last_identity = ref
            self.identity_streak = 1
        self.identity_max_streak = max(self.identity_max_streak,
                                       self.identity_streak)

    def stability_summary(self, required: int) -> dict:
        if required <= 1:
            required = 2
        verified = (
            all(item["max_streak"] >= required for item in self.stability.values())
            or self.identity_max_streak >= required
        )
        return {
            "status": "verified" if verified else "weak",
            "required_frames": required,
            "identity_max_streak": self.identity_max_streak,
            "roles": {key: dict(value) for key, value in self.stability.items()},
        }

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


def _identity_value(record) -> int:
    if record is None:
        return 0
    value = record.value.get("update_identity") if hasattr(record, "value") else record.get("update_identity")
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return 0
    return int(value or 0)


def _pause_failures(launched: dict) -> list[str]:
    failures = []
    for role, item in launched.items():
        path = Path(item.observe_dir) / "pause-requested.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            failures.append(f"{role}: pause request was not recorded")
            continue
        if value.get("status") != "requested":
            failures.append(f"{role}: pause status {value.get('status')!r}")
    return failures


def _health_stop(summary: dict) -> tuple[str, str] | None:
    gate = gfx_diagnostics.health_gate(summary)
    if gate["allow"]:
        return None
    if summary.get("verdict") == gfx_diagnostics.FAILED:
        return "gpu_execution_failure", str(gate.get("reason") or "GPU failure")
    return "infrastructure_failure", str(gate.get("reason") or "health gate denied")


def _phase_ready(reference_draws: int, candidate_draws: int,
                 minimum: int, tolerance: float,
                 reference_update: int = 0,
                 candidate_update: int = 0) -> bool:
    if reference_update and candidate_update:
        if reference_update != candidate_update:
            return False
        # A shared engine update is the strong criterion.  Draw counts are
        # backend-dependent (GL batches differently from the reconstruction),
        # so they must not veto an identity-matched scene.
        if minimum > 0 and (reference_draws < minimum or candidate_draws < minimum):
            return False
        return reference_draws > 0 and candidate_draws > 0
    if minimum > 0 and (reference_draws < minimum or candidate_draws < minimum):
        return False
    if reference_draws <= 0 or candidate_draws <= 0:
        return False
    larger = max(reference_draws, candidate_draws)
    return abs(reference_draws - candidate_draws) / larger <= tolerance


def align_streams(state: CampaignState, *, limit: int | None = None,
                  draw_tolerance: float = 0.30) -> list[dict]:
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
        if limit is not None and len(new_pairs) >= limit:
            break
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
        ref_draws = ref.value.get("draws") or 0
        cand_draws = cand.value.get("draws") or 0
        draw_relation = (
            abs(ref_draws - cand_draws) / max(ref_draws, cand_draws)
            if ref_draws and cand_draws else 1.0
        )
        ref_identity = _identity_value(ref)
        cand_identity = _identity_value(cand)
        identity_equal = bool(ref_identity and ref_identity == cand_identity)
        strong = extent["normalized"] and (
            (identity_equal and draw_relation <= draw_tolerance)
            or (same_draws and reference_signature
                and reference_signature == candidate_signature)
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
                "game_day": ref.value.get("game_day"),
                "update_identity": ref.value.get("update_identity"),
            },
            "candidate": {
                "sha256": cand.digest,
                "width": cand.value.get("width"),
                "height": cand.value.get("height"),
                "draws": cand.value.get("draws"),
                "caller_signature": cand.value.get("caller_signature"),
                "game_day": cand.value.get("game_day"),
                "update_identity": cand.value.get("update_identity"),
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


_REQUEST_IDS = itertools.count(time.time_ns())

# Must match ``OBS_MAX_DUMPS`` in benchmark/eu4_gfx_frame_observe.c.  A request
# larger than this is refused before it reaches the native observer; the
# observer also rejects an oversized request instead of silently truncating it.
NATIVE_REQUEST_CAPACITY = 256


class RequestError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _request_dumps(launched: dict, indices: list[int]) -> int | None:
    if not indices:
        return None
    unique = sorted(set(indices))
    if len(unique) > NATIVE_REQUEST_CAPACITY:
        raise RequestError(
            "request_too_large",
            f"{len(unique)} frames requested; native capacity is "
            f"{NATIVE_REQUEST_CAPACITY}. Split the request into bounded windows "
            "instead of relying on truncation.",
        )
    request_id = next(_REQUEST_IDS)
    path = Path(launched["observe_dir"]) / "capture-request.json"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({
        "schema": "gfx-frame-request/v1",
        "request_id": request_id,
        "frames": unique,
        "requested": len(unique),
        "capacity": NATIVE_REQUEST_CAPACITY,
    }) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return request_id



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


def _await_dump_meta(launched: dict, indices: list[int], timeout: float = 4.0,
                     request_id: int | None = None) -> dict:
    captured = Path(launched["observe_dir"]) / "captured"
    wanted = set(indices)
    found: dict[int, dict] = {}
    explicit_missing: set[int] = set()
    deadline = time.monotonic() + timeout
    ack_path = Path(launched["observe_dir"]) / "capture-ack.json"
    while time.monotonic() < deadline and wanted:
        if request_id is not None and ack_path.is_file():
            try:
                ack = json.loads(ack_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                ack = {}
            if (isinstance(ack, dict) and ack.get("request_id") == request_id
                    and ack.get("status") in (
                        "captured", "expired", "missing", "invalid"
                    )):
                if ack.get("status") == "invalid":
                    return {
                        "found": {},
                        "missing": sorted(wanted),
                        "ack": ack,
                        "reason": "observer rejected the capture request",
                    }
                for value in ack.get("missing", []):
                    if type(value) is int and value in wanted:
                        wanted.discard(value)
                        explicit_missing.add(value)
                # Still scan captured/: an expired ack may coexist with a
                # successfully written payload for another requested index.
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
    return {
        "found": found,
        "missing": sorted((wanted | explicit_missing) - set(found)),
        "ack": None,
    }


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
            problems = _verify_dump_payload(
                value, index,
                expected_backend=launched.get("backend"),
                expected_session=launched.get("session_id"),
            )
            if problems:
                # Preserve malformed evidence for diagnosis but never present
                # it as a valid comparison frame.
                stored = store.put_file(raw, kind="frame-invalid", preserved=False,
                                        logical_name=f"{launched['role']}-frame-{index}-invalid")
                found.append({
                    "frame_index": index,
                    "meta": value,
                    "sha256": stored.sha256 if stored else value.get("sha256"),
                    "preserved": False,
                    "valid": False,
                    "reasons": problems,
                })
                wanted.discard(index)
                continue
            stored = store.put_file(raw, kind="frame", preserved=True,
                                    logical_name=f"{launched['role']}-frame-{index}")
            found.append({
                "frame_index": index,
                "meta": value,
                "sha256": stored.sha256 if stored else value.get("sha256"),
                "preserved": bool(stored),
                "valid": True,
                "reasons": [],
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



DUMP_SCHEMA = "gfx-frame-dump/v1"


def _verify_dump_payload(meta: dict, index: int, *,
                         expected_backend: str | None = None,
                         expected_session: str | None = None) -> list[str]:
    """Verify a native frame dump before it is used as comparison evidence.

    Declared dimensions, row stride, origin, pixel format, byte count and
    SHA-256 must all agree with the payload.  A dump that fails any check is
    rejected, not compared and not silently repaired.
    """
    reasons: list[str] = []
    if not isinstance(meta, dict):
        return ["dump metadata is not an object"]
    if meta.get("schema") not in (None, DUMP_SCHEMA):
        reasons.append(f"unexpected dump schema {meta.get('schema')!r}")
    if type(meta.get("frame_index")) is int and meta["frame_index"] != index:
        reasons.append(
            f"dump frame_index {meta['frame_index']} != requested {index}"
        )
    width, height = meta.get("width"), meta.get("height")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        reasons.append("dump extent is missing or invalid")
        width = height = 0
    if meta.get("row_stride") is not None and type(meta.get("row_stride")) is int:
        if meta["row_stride"] != width * 4:
            reasons.append(
                f"row stride {meta['row_stride']} != tightly packed {width * 4}"
            )
    elif meta.get("pixel_format") == "BGRA8":
        reasons.append("BGRA8 dump does not declare its row stride")
    if meta.get("pixel_format") not in (None, "BGRA8"):
        reasons.append(f"unexpected pixel format {meta.get('pixel_format')!r}")
    origin = meta.get("origin")
    if origin is not None and origin not in ("lower_left", "top_left"):
        reasons.append(f"unexpected origin {origin!r}")
    if expected_backend is not None and meta.get("backend") not in (
        None, expected_backend
    ):
        reasons.append(
            f"dump backend {meta.get('backend')!r} != {expected_backend!r}"
        )
    if expected_session is not None and meta.get("session") not in (
        None, expected_session
    ):
        reasons.append("dump session does not match the campaign session")
    raw = meta.get("raw_file")
    if not isinstance(raw, str) or not raw:
        reasons.append("raw_file is absent")
        return reasons
    path = Path(raw)
    if not path.is_absolute():
        reasons.append("raw_file is not absolute")
    try:
        payload = path.read_bytes()
    except OSError:
        reasons.append("raw capture file is unreadable")
        return reasons
    raw_bytes = meta.get("raw_bytes")
    expected_bytes = width * height * 4
    if raw_bytes is not None and type(raw_bytes) is int and raw_bytes != expected_bytes:
        reasons.append(
            f"raw_bytes {raw_bytes} != {expected_bytes} for {width}x{height} BGRA8"
        )
    if len(payload) != expected_bytes:
        reasons.append(
            f"payload is {len(payload)} bytes, expected {expected_bytes}"
        )
    digest = meta.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        reasons.append("sha256 declaration is absent or malformed")
    elif gfx_artifacts.sha256_file(path) != digest:
        reasons.append("payload digest does not match its declaration")
    return reasons


def _compare_pair(pair: dict, reference_meta: dict | None,
                  candidate_meta: dict | None, scenario: dict, *,
                  expected: dict | None = None) -> dict:
    """Compare one lockstep pair from its preserved raw dumps.

    A pair whose alignment evidence is too weak is not compared; a pixel
    difference is only a rendering verdict for a strong alignment.
    """
    if not reference_meta or not candidate_meta:
        return {
            "status": "not_compared",
            "reason": "one or both paired raw frames are unavailable",
        }
    expected = expected or {}
    reference_problems = _verify_dump_payload(
        reference_meta, pair["frame_index"],
        expected_backend=expected.get("reference_backend"),
        expected_session=expected.get("session"),
    )
    candidate_problems = _verify_dump_payload(
        candidate_meta, pair.get("candidate_frame_index", pair["frame_index"]),
        expected_backend=expected.get("candidate_backend"),
        expected_session=expected.get("session"),
    )
    if reference_problems or candidate_problems:
        return {
            "status": "not_compared",
            "reason": "capture integrity failed; malformed evidence is not compared",
            "integrity": {
                "reference": reference_problems,
                "candidate": candidate_problems,
            },
        }
    reference_record = {
        "frame_index": pair["frame_index"],
        "width": reference_meta.get("width"),
        "height": reference_meta.get("height"),
        "origin": reference_meta.get("origin"),
        "caller_signature": reference_meta.get("caller_signature"),
        "draws": reference_meta.get("draws"),
        "game_day": reference_meta.get("game_day"),
        "update_identity": reference_meta.get("update_identity"),
    }
    candidate_record = {
        "frame_index": pair.get("candidate_frame_index"),
        "width": candidate_meta.get("width"),
        "height": candidate_meta.get("height"),
        "origin": candidate_meta.get("origin"),
        "caller_signature": candidate_meta.get("caller_signature"),
        "draws": candidate_meta.get("draws"),
        "game_day": candidate_meta.get("game_day"),
        "update_identity": candidate_meta.get("update_identity"),
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
    reference_identity = _identity_value(reference_record)
    candidate_identity = _identity_value(candidate_record)
    identity_equal = bool(
        reference_identity and reference_identity == candidate_identity
    )
    draw_tolerance = float((scenario.get("settle") or {}).get(
        "draw_tolerance", 0.25))
    ref_draws = reference_record.get("draws") or 0
    cand_draws = candidate_record.get("draws") or 0
    draw_relation = (
        abs(ref_draws - cand_draws) / max(ref_draws, cand_draws)
        if ref_draws and cand_draws else 1.0
    )
    strong = (
        relation["normalized"]
        and "scene_mismatch" not in evidence
        and "game_update_mismatch" not in evidence
        and (
            # A shared engine day plus a comparable render workload is strong
            # across backends even when GL/Metal batch draws differently.
            (identity_equal and draw_relation <= draw_tolerance)
            or (
                same_draws
                and (
                    (reference_signature
                     and reference_signature == candidate_signature)
                    or "scene" in evidence
                )
            )
        )
    )
    alignment = {
        "score": score,
        "evidence": evidence,
        "extent": relation,
        "draw_counts": [reference_record.get("draws"),
                        candidate_record.get("draws")],
        "signatures": [reference_signature, candidate_signature],
        "identity_equal": identity_equal,
        "draw_relation": round(draw_relation, 6),
        "status": gfx_align.ALIGNED if strong else gfx_align.AMBIGUOUS,
    }
    if alignment["status"] != gfx_align.ALIGNED:
        if identity_equal and draw_relation > draw_tolerance:
            reason = (
                "same engine day but render workload differs "
                f"({draw_relation:.1%} > {draw_tolerance:.0%}); "
                "intra-day state is not established"
            )
        else:
            reason = "alignment is ambiguous"
        return {"status": "not_compared", "reason": reason,
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
    # A day-only identity is weaker than a tick identity.  In that case coarse
    # content corroboration prevents two different loading/scene phases from
    # being reported as a rendering difference.  A strong tick identity would
    # not need this guard.
    alignment["content"] = content
    # Image correlation is diagnostic only.  Correspondence is established by
    # engine identity, draw workload and scene stability; a coarse image
    # correlation is not allowed to veto (or authorize) a rendering verdict.
    alignment["correspondence"] = {
        "basis": "engine_day+draw_workload+scene_stability",
        "render_stage_proven": bool(
            (scenario.get("start") or {}).get("pause")
            and (scenario.get("settle") or {}).get("stable_frames", 1) > 1
        ),
        "image_correlation_is_diagnostic": True,
    }
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




def _process_failure(result: dict) -> bool:
    """True only for an abnormal exit that was observed before cleanup.

    SIGKILL/SIGTERM produced by our own bounded termination is not a renderer
    process failure.  Older results that lack ``exit_code_before_stop`` are
    interpreted using the stop reason.
    """
    stop_reason = (result.get("stop") or {}).get("reason")
    for process in (result.get("processes") or {}).values():
        if not isinstance(process, dict):
            continue
        if "exit_code_before_stop" in process:
            code = process.get("exit_code_before_stop")
        else:
            code = process.get("exit_code")
            if code in (-9, -15) and stop_reason != "process_exit":
                continue
        if type(code) is int and code != 0:
            return True
    return False


def classify_campaign(result: dict, scenario: dict, *, max_frames: int) -> dict:
    """Separate execution outcome from rendering qualification.

    A campaign is ``match`` only when the requested frame count was actually
    compared, every required pair was valid and passed, health is clean, and no
    dropped/skipped/invalid required frame remains.
    """
    frames = result.get("frames") or {}
    comparison = frames.get("comparison") or {}
    health = result.get("health") or {}
    stop = result.get("stop") or {}
    policy = (scenario.get("comparison") or {}).get("policy", "exact")
    paired = int(frames.get("paired") or 0)
    aligned = int(frames.get("aligned") or 0)
    not_aligned = int(frames.get("not_aligned") or 0)
    passed = int(comparison.get("passed") or 0)
    failed = int(comparison.get("failed") or 0)
    unverified = int(comparison.get("unverified") or 0)
    not_compared = int(comparison.get("not_compared") or 0)
    valid_compared = passed + failed
    required = int(max_frames)
    coverage_complete = (
        paired >= required
        and valid_compared >= required
        and unverified == 0
        and not_compared == 0
        and not_aligned == 0
    )
    reasons = []
    if result.get("error") and "exception" in str(stop.get("reason") or ""):
        classification = "infrastructure_failure"
        reasons.append("campaign raised an internal error")
    elif _process_failure(result):
        classification = "process_failure"
        reasons.append("an owned game process exited abnormally")
    elif (result.get("display") or {}).get("monitor_failed"):
        classification = "incomplete"
        reasons.append("background window/focus verification failed during the run")
    elif health.get("verdict") == "failed":
        classification = "gpu_execution_failure"
        reasons.append((health.get("first_failure") or {}).get("message")
                       or "GPU/API execution failure")
    elif health.get("instrumentation_failures"):
        classification = "infrastructure_failure"
        reasons.append("instrumentation failure")
    elif stop.get("reason") in ("lockstep_timeout", "observation_stall",
                                "alignment_failure"):
        classification = "alignment_failure"
        reasons.append(stop.get("reason"))
    elif not_aligned:
        classification = "alignment_failure"
        reasons.append(f"{not_aligned} pair(s) not aligned")
    elif failed:
        classification = "rendering_difference"
        reasons.extend((comparison.get("first_failure") or {}).get("comparison", {}).get("reasons", [])[:3])
    elif valid_compared < required:
        classification = "incomplete"
        reasons.append(
            f"requested {required} compared frames, got {valid_compared}; "
            f"paired={paired}, aligned={aligned}, unverified={unverified}, "
            f"not_compared={not_compared}"
        )
    elif not coverage_complete:
        classification = "incomplete"
        reasons.append("required coverage is not complete")
    elif policy == "diagnostic":
        classification = "incomplete"
        reasons.append("diagnostic policy reports differences but does not qualify")
    elif (result.get("stability") or {}).get("status") != "verified":
        classification = "incomplete"
        reasons.append("no verified static scene; correspondence is weak")
    elif health.get("verdict") != "healthy":
        classification = "incomplete"
        reasons.append(
            f"GPU health is {health.get('verdict', 'unknown')}, not healthy")
    else:
        classification = "match"
    return {
        "schema": "gfx-campaign-qualification/v1",
        "status": classification,
        "qualified": classification == "match",
        "policy": policy,
        "requested_frames": required,
        "frames": {
            "observed": int(frames.get("observed_reference") or 0)
                        + int(frames.get("observed_candidate") or 0),
            "paired": paired,
            "aligned": aligned,
            "validly_compared": valid_compared,
            "passed": passed,
            "failed": failed,
            "unverified": unverified,
            "not_compared": not_compared,
            "not_aligned": not_aligned,
        },
        "coverage_complete": coverage_complete,
        "reasons": [reason for reason in reasons if reason],
    }


def _effective_scenario(scenario: dict) -> tuple[dict, dict]:
    """Return an immutable effective launch specification and its provenance.

    ``--continuelastsave`` is part of the arguments that are actually
    executed, so it must be part of the digest recorded for the campaign.  The
    caller's normalized scenario is never mutated.
    """
    declared_args = list(scenario.get("args") or [])
    effective = {key: value for key, value in scenario.items()}
    effective["args"] = list(declared_args)
    if (scenario.get("start") or {}).get("kind") == "save":
        if "--continuelastsave" not in effective["args"]:
            effective["args"].append("--continuelastsave")
    provenance = {
        "declared_digest": scenario.get("digest"),
        "declared_args": declared_args,
        "effective_args": list(effective["args"]),
        "args_changed": effective["args"] != declared_args,
    }
    provenance["args_sha256"] = gfx_scenario.scenario_digest(
        {"args": provenance["effective_args"]}
    )
    if effective["args"] != declared_args:
        effective["digest"] = gfx_scenario.scenario_digest(effective)
    provenance["effective_digest"] = effective.get("digest")
    return effective, provenance


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
    background: bool = False,
    display_name: str = gfx_display.DEFAULT_NAME,
    display_window: tuple[int, int] | None = None,
    display_timeout: float = 60.0,
    display_focus_guard: bool = False,
) -> dict:
    scenario, launch_provenance = _effective_scenario(scenario)
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
    window_size = display_window or tuple(scenario["logical_extent"])
    display_qualified = None
    display_plan = None
    if background:
        # Validation only: no virtual-screen creation and no shared settings
        # write before launch authorization and the host-wide lease.
        try:
            display_plan = gfx_display.plan_background(
                preflight["user_data"], name=display_name,
                window_size=window_size, focus_guard=display_focus_guard,
            )
        except gfx_display.DisplayError as exc:
            session.fail(f"display_{exc.kind}", extra={"display_error": str(exc)})
            raise gfx_launch.LaunchError("display", f"{exc.kind}: {exc}") from exc
    result: dict = {
        "schema": SCHEMA,
        "session_id": session.session_id,
        "run_dir": str(session.run_dir),
        "scenario": {
            "name": scenario["name"],
            "digest": scenario["digest"],
            "declared_digest": launch_provenance["declared_digest"],
            "launch": launch_provenance,
            "mode": mode,
            "profile": wait_profile.name,
            "logical_extent": scenario["logical_extent"],
            "comparison_policy": scenario["comparison"]["policy"],
            "comparison": scenario["comparison"],
        },
        "build": {},
        "processes": {},
        "preflight": preflight,
        "display_plan": display_plan.as_dict() if display_plan else None,
        "frames": {},
        "health": {},
        "dumps": {},
        "stop": {},
        "ledger": {},
        "terminations": [],
        "result": "incomplete",
    }
    display_monitors: dict = {}
    campaign_start = time.monotonic()
    tokens: list[str] = []
    lease = None
    launched: dict = {}
    state: CampaignState | None = None
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
            pause_at_draws=(
                int((scenario.get("start") or {}).get("pause_at_draws")
                    or scenario["settle"]["min_draws"])
                if (scenario.get("start") or {}).get("pause")
                and not (scenario.get("start") or {}).get("pause_at_first_game_day")
                and int(scenario["settle"]["min_draws"]) > 0
                else 0
            ),
            pause_at_first_game_day=bool(
                (scenario.get("start") or {}).get("pause")
                and (scenario.get("start") or {}).get("pause_at_first_game_day")
            ),
            pause_by_game_speed_zero=bool(
                (scenario.get("start") or {}).get("pause")
                and (scenario.get("start") or {}).get("pause_mode") == "game_speed_zero"
            ),
            pause_signature=int((scenario.get("start") or {}).get(
                "pause_signature") or "0", 0),
            pause_max_draws=int((scenario.get("start") or {}).get(
                "pause_max_draws") or 0),
        )
        result["launch_spec"] = {
            "argv": list(config.args),
            "args_sha256": gfx_scenario.scenario_digest({"args": list(config.args)}),
            "cwd": str(config.executable.parent),
            "executable_sha256": config.executable_sha256,
        }
        lease = launch_lease.acquire(executable, output=session.run_dir)
        if background and display_plan is not None:
            # The lease is held: the virtual display may now be prepared and
            # the shared settings changed.  Any failure here restores first.
            try:
                display_qualified = gfx_display.activate_background(
                    display_plan, session.run_dir)
                session.attach_display(display_qualified)
            except BaseException:
                if display_qualified is not None:
                    display_qualified.restore()
                session.fail("display_activation_failed")
                raise
        # Start both backends before waiting on either one's readiness.
        health = gfx_diagnostics.GpuHealth()
        prelaunched_ref_stream = None
        launched["reference"] = gfx_launch.spawn(
            "reference", config, session.run_dir / "reference",
            lease=lease, session=session, token=tokens[0], libraries=libraries,
            backend=scenario.get("reference_backend", "opengl"),
        )
        if background and display_qualified is not None:
            display_monitors["reference"] = gfx_display.WindowMonitor(
                launched["reference"].process.pid, display_qualified.display,
                window_size,
            ).start()
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
        if background and display_qualified is not None:
            display_monitors["candidate"] = gfx_display.WindowMonitor(
                launched["candidate"].process.pid, display_qualified.display,
                window_size,
            ).start()
        launch_ledger.start(ledger_path, launched["candidate"].token,
                            launched["candidate"].process.pid)
        result["processes"]["candidate"] = launched["candidate"].as_dict()
        incremental = {
            role: gfx_diagnostics.IncrementalHealth(item.run_dir, health)
            for role, item in launched.items()
        }
        health.add("health_check",
                   "observer startup and owned processes healthy at readiness",
                   severity="info", signature="health_check:readiness")

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
        display_monitors = {}
        if background and display_qualified is not None:
            window_checks = {}
            for role, item in launched.items():
                window_checks[role] = gfx_display.verify_window(
                    item.process.pid, display_qualified.display, window_size,
                    timeout=display_timeout,
                )
            result["display"] = {
                "display": display_qualified.as_dict(),
                "windows": window_checks,
            }
            failed = {
                role: check for role, check in window_checks.items()
                if not check.get("ok")
            }
            if failed:
                result["stop"] = {
                    "reason": "display_window_failed",
                    "detail": failed,
                    "trigger": "startup",
                }
                result["result"] = "incomplete"
                raise _Finished()
        if not readiness.ok:
            result["stop"] = {
                "reason": f"readiness_{readiness.status}",
                "detail": readiness.as_dict(),
                "trigger": "startup",
            }
            result["result"] = "incomplete"
            raise _Finished()

        # Startup/settling health: a refused draw, failed pipeline or failed
        # resolve during startup must stop the campaign before it spends the
        # observation budget on an already-invalid process.
        startup_health = {
            role: gfx_diagnostics.scan_run(item.run_dir)
            for role, item in launched.items()
        }
        result["startup_health"] = startup_health
        startup_failure = {
            role: summary for role, summary in startup_health.items()
            if summary.get("verdict") == "failed"
        }
        if startup_failure:
            first = next(iter(startup_failure.values()))
            result["stop"] = {
                "reason": "startup_gpu_failure",
                "detail": {
                    role: {
                        "verdict": summary.get("verdict"),
                        "first_failure": summary.get("first_failure"),
                    }
                    for role, summary in startup_failure.items()
                },
                "trigger": "startup",
            }
            result["health"] = next(iter(startup_failure.values()))
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
                tolerance = float((scenario.get("settle") or {}).get(
                    "draw_tolerance", 0.25))
                if _phase_ready(
                    ref_draws, cand_draws, minimum_draws, tolerance,
                    _identity_value(latest_ref),
                    _identity_value(latest_cand),
                ):
                    break
                if any(item.process.poll() is not None for item in launched.values()):
                    break
                time.sleep(0.02)
        if (scenario.get("start") or {}).get("pause"):
            pause_failures = _pause_failures(launched)
            if not pause_failures:
                baseline = {
                    "reference": (lambda item: item.value.get("game_day") if item else None)(
                        _latest(state.ref_stream)),
                    "candidate": (lambda item: item.value.get("game_day") if item else None)(
                        _latest(state.cand_stream)),
                }
                confirm_deadline = time.monotonic() + float(
                    (scenario.get("settle") or {}).get("pause_confirm_seconds", 2.0))
                while time.monotonic() < confirm_deadline:
                    for record in state.ref_stream.poll():
                        state.ref_by_index[record.index] = record
                    for record in state.cand_stream.poll():
                        state.cand_by_index[record.index] = record
                    for role, stream in (("reference", state.ref_stream),
                                         ("candidate", state.cand_stream)):
                        latest = _latest(stream)
                        day = latest.value.get("game_day") if latest else None
                        if day is not None and day != baseline[role]:
                            pause_failures.append(
                                f"{role}: game day advanced after pause "
                                f"({baseline[role]} -> {day})")
                            break
                    if pause_failures:
                        break
                    time.sleep(0.05)
            if pause_failures:
                result["stop"] = {"reason": "pause_failed",
                                  "trigger": "; ".join(pause_failures)}
                result["result"] = "incomplete"
                raise _Finished()
        streaming_requests: dict[str, int | None] = {}
        if mode == "streaming" and minimum_draws > 0:
            # Pre-request a small full-resolution window at the start of the
            # target phase.  Metadata-only observation is cheap enough to reach
            # the scene; these frames carry the raw payloads for comparison.
            latest_ref = _latest(state.ref_stream)
            latest_cand = _latest(state.cand_stream)
            if latest_ref is not None and latest_cand is not None:
                window = max(32, max_frames + 8)
                streaming_requests["reference"] = _request_dumps(
                    {"observe_dir": launched["reference"].observe_dir},
                    list(range(latest_ref.index + 1, latest_ref.index + 1 + window)),
                )
                streaming_requests["candidate"] = _request_dumps(
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
        last_progress_at = time.monotonic()
        last_counts = (0, 0)
        stall_seconds = float((scenario.get("observation") or {}).get(
            "stall_seconds", 45.0))
        while time.monotonic() < deadline:
            new_ref = state.ref_stream.poll()
            new_cand = state.cand_stream.poll()
            for record in new_ref:
                state.ref_by_index[record.index] = record
                _record_health(record, health)
            for record in new_cand:
                state.cand_by_index[record.index] = record
                _record_health(record, health)
            for role, scanner in incremental.items():
                summary = scanner.poll()
                health_stop = _health_stop(summary)
                if health_stop is not None:
                    stop_reason, detail = health_stop
                    stop_trigger = f"{role}: {detail}"
                    break
            if stop_reason:
                break
            counts = (len(state.ref_stream.records), len(state.cand_stream.records))
            if counts != last_counts:
                last_counts = counts
                last_progress_at = time.monotonic()
            if (not stop_reason and time.monotonic() - last_progress_at >= stall_seconds
                    and all(item.process.poll() is None for item in launched.values())):
                stop_reason = "observation_stall"
                stop_trigger = (
                    f"no new frame for {stall_seconds:g}s; "
                    f"reference={counts[0]} candidate={counts[1]} observations"
                )
                break
            if mode == "lockstep" and state is not None:
                timeout_files = [
                    Path(item.observe_dir) / "lockstep-timeout.jsonl"
                    for item in launched.values()
                ]
                timed_out = [path for path in timeout_files
                             if path.is_file() and path.stat().st_size > 0]
                if timed_out:
                    stop_reason = "lockstep_timeout"
                    stop_trigger = (
                        "lockstep wait expired; synchronization is lost at "
                        + ", ".join(str(path.parent) for path in timed_out)
                    )
                    result["synchronization"] = {
                        "lost": True,
                        "reason": "lockstep_timeout",
                        "evidence": [str(path) for path in timed_out],
                    }
                    break
            if not state.pairing and mode == "lockstep":
                latest_ref = _latest(state.ref_stream)
                latest_cand = _latest(state.cand_stream)
                ref_draws = (latest_ref.value.get("draws") if latest_ref else 0) or 0
                cand_draws = (latest_cand.value.get("draws") if latest_cand else 0) or 0
                tolerance = float((scenario.get("settle") or {}).get(
                    "draw_tolerance", 0.25))
                if latest_ref is not None and latest_cand is not None and _phase_ready(
                    ref_draws, cand_draws, minimum_draws, tolerance,
                    _identity_value(latest_ref),
                    _identity_value(latest_cand),
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
            remaining = max_frames - len(state.pairs)
            pairs = align_streams(
                state, limit=max(0, remaining),
                draw_tolerance=float((scenario.get("settle") or {}).get(
                    "draw_tolerance", 0.30)))
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
                    if mode == "lockstep":
                        # The paused frame cannot advance on its own; request
                        # exactly this pair's payloads before releasing it.
                        _request_dumps(
                            {"observe_dir": launched["reference"].observe_dir},
                            [ref_index])
                        _request_dumps(
                            {"observe_dir": launched["candidate"].observe_dir},
                            [cand_index])
                    # In streaming mode the phase-entry window was requested
                    # once.  Issuing another request here would overwrite that
                    # pending window and lose already-declared frames.
                    dump_timeout = 4.0 if mode == "lockstep" else 2.0
                    reference_dump = _await_dump_meta(
                        {"observe_dir": launched["reference"].observe_dir}, [ref_index],
                        timeout=dump_timeout,
                        request_id=streaming_requests.get("reference")
                        if mode == "streaming" else None)
                    candidate_dump = _await_dump_meta(
                        {"observe_dir": launched["candidate"].observe_dir}, [cand_index],
                        timeout=dump_timeout,
                        request_id=streaming_requests.get("candidate")
                        if mode == "streaming" else None)
                    pair["dumps"] = {
                        "reference": reference_dump,
                        "candidate": candidate_dump,
                    }
                    state.note_identity(pair["reference"], pair["candidate"])
                    for role, dump in (("reference", reference_dump),
                                       ("candidate", candidate_dump)):
                        for value in (dump.get("found") or {}).values():
                            state.note_digest(role, value.get("sha256"))
                    comparison = _compare_pair(
                        pair, reference_dump.get("found", {}).get(ref_index),
                        candidate_dump.get("found", {}).get(cand_index),
                        scenario,
                        expected={
                            "session": session.session_id,
                            "reference_backend": launched["reference"].backend,
                            "candidate_backend": launched["candidate"].backend,
                        },
                    )
                    pair["comparison"] = comparison
                    if len(state.samples) < 256:
                        stats = comparison.get("statistics") or {}
                        state.samples.append({
                            "reference_index": ref_index,
                            "candidate_index": cand_index,
                            "status": comparison.get("status"),
                            "alignment": (comparison.get("alignment") or {}).get("status"),
                            "mismatched_fraction": stats.get("mismatched_fraction"),
                            "max_channel_error": stats.get("max_channel_error"),
                            "mean_abs_error": stats.get("mean_abs_error"),
                        })
                    if comparison.get("status") in (gfx_frame_compare.FAIL,
                                                    gfx_frame_compare.UNVERIFIED):
                        if comparison.get("status") == gfx_frame_compare.FAIL:
                            _preserve_pair(store, launched, pair)
                        if mode == "lockstep":
                            _release({"observe_dir": launched["reference"].observe_dir},
                                     [ref_index])
                            _release({"observe_dir": launched["candidate"].observe_dir},
                                     [cand_index])
                        if bool((scenario.get("comparison") or {}).get(
                                "stop_on_first", True)):
                            stop_reason = (
                                "frame_difference"
                                if comparison.get("status") == gfx_frame_compare.FAIL
                                else "comparison_unverified"
                            )
                            stop_trigger = (
                                f"frame {ref_index}/{cand_index}: "
                                + "; ".join(comparison.get("reasons", [])[:3])
                            )
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
            "samples": state.samples,
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
                     "observe_dir": item.observe_dir, "backend": item.backend,
                     "session_id": session.session_id},
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
        cleanup_errors: list[str] = []

        def attempt(label: str, action):
            try:
                return action()
            except BaseException as exc:  # noqa: BLE001 - one cleanup failure must not skip others
                cleanup_errors.append(f"{label}: {type(exc).__name__}: {exc}")
                return None

        for role, item in list(launched.items()):
            attempt(f"record-exit-{role}",
                    lambda item=item, role=role: result.setdefault(
                        "processes", {}).setdefault(role, {}).update(
                            {"exit_code_before_stop": item.process.poll(),
                             "exit_code": item.process.poll()}))
        if mode == "lockstep" and state is not None:
            for role, item in list(launched.items()):
                def release_pending(item=item, role=role):
                    pending = [record.index for record in
                               (state.ref_stream.records if role == "reference"
                                else state.cand_stream.records)]
                    if pending:
                        _release({"observe_dir": item.observe_dir}, pending[-4:])
                attempt(f"release-paused-{role}", release_pending)
        attempt("terminate-owned", lambda: result.__setitem__(
            "terminations", _stop_processes(session, resume_first=mode == "lockstep")))
        for role, item in list(launched.items()):
            attempt(f"record-final-exit-{role}",
                    lambda item=item, role=role: result.setdefault(
                        "processes", {}).setdefault(role, {}).update(
                            {"exit_code": item.process.poll()}))
        # Shared settings are restored after all owned processes are gone and
        # before the host-wide lease is released.
        if display_monitors:
            monitor_summary = {
                role: monitor.stop() for role, monitor in display_monitors.items()
            }
            result.setdefault("display", {})["monitor"] = monitor_summary
            if any(item.get("unexpected_activation") or
                   item.get("display_disconnected") or
                   item.get("placement_failed")
                   for item in monitor_summary.values()):
                result.setdefault("display", {})["monitor_failed"] = True
        display_restore = attempt("restore-display", session.restore_display)
        if display_restore is not None:
            result["display_restore"] = display_restore
            if display_restore.get("conflict"):
                cleanup_errors.append(
                    "display settings changed by another actor; backup preserved")
        if lease is not None:
            attempt("release-lease", lambda: lease.release())
            lease = None
        finished_tokens = set()
        for role, item in list(launched.items()):
            def finish_item(item=item):
                # A campaign that stopped on a confirmed difference still ran
                # to its configured termination; its launch is not a failure.
                ledger_status = (
                    "completed"
                    if result.get("result") in ("completed", "stopped")
                    else "failed"
                )
                launch_ledger.finish(ledger_path, item.token, pid=item.process.pid,
                                     status=ledger_status)
                finished_tokens.add(item.token)
            attempt(f"finish-ledger-{role}", finish_item)
        for token in tokens:
            if token not in finished_tokens:
                attempt(f"finish-unstarted-{token}",
                        lambda token=token: launch_ledger.finish(
                            ledger_path, token, pid=None, status="failed"))
        attempt("write-artifact-manifest", lambda: result.update({
            "artifact_manifest": str(store.write_manifest()),
            "artifacts": store.summary(),
        }))
        if result.get("stop"):
            result["stop"]["elapsed_seconds"] = round(
                time.monotonic() - campaign_start, 3)
        if state is not None:
            result["stability"] = state.stability_summary(
                int((scenario.get("settle") or {}).get("stable_frames", 1)))
        else:
            result.setdefault("stability", {"status": "weak", "required_frames": 1})
        if "qualification" not in result:
            result["qualification"] = classify_campaign(result, scenario,
                                                        max_frames=max_frames)
            result["classification"] = result["qualification"]["status"]
        attempt("close-session", lambda: session.close(
            status=gfx_session.CLOSED if result.get("result") in ("completed", "stopped")
            else gfx_session.FAILED,
            reason=result.get("stop", {}).get("reason", ""),
            extra={"result": result.get("result"),
                   "classification": result.get("classification"),
                   "frames": result.get("frames", {})},
        ))
        attempt("load-terminal", lambda: result.__setitem__(
            "terminal", gfx_session.Session.load(session.run_dir).get("terminal")))
        if not keep_evidence:
            attempt("cleanup-temp", session.cleanup_temp)
        result["cleanup_errors"] = cleanup_errors
        result["finished_unix"] = time.time()
        result["duration_seconds"] = round(result["finished_unix"] - started, 3)
        attempt("write-final-result", lambda: (
            session.run_dir / "result.json"
        ).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8"))
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
    run.add_argument("--background", action="store_true",
                     help="run on the reusable BetterDisplay virtual screen")
    run.add_argument("--display-name", default=gfx_display.DEFAULT_NAME)
    run.add_argument("--display-timeout", type=float, default=60.0)
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
            background=args.background,
            display_name=args.display_name,
            display_timeout=args.display_timeout,
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
