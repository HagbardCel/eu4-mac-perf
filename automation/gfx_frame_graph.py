"""Dependency-aware frame capture contract (Strategy B, Phase 2).

A frame trace is only *replay-ready* when every sampled resource resolves to a
known content version, every renderer-generated version has an in-frame
producer, and no required operation is missing.  This module derives that
resource graph from an existing ``gfx-frame-trace/v1`` capture and classifies
the capture as ``complete``, ``partial`` or ``invalid``.

The graph deliberately separates:

* **external inputs** — assets and CPU-uploaded data legitimately seeded from
  the OpenGL capture;
* **renderer-generated outputs** — targets and copies that Metal must produce
  itself; an OpenGL image of one of these may be a reference checkpoint but
  must never be bound to a downstream Metal pass;
* **unproven content** — a sampled resource with no captured producer (for
  example an uncaptured resolve).  The capture is partial until that operation
  is recorded.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from automation import gfx_observation

SCHEMA = "gfx-frame-graph/v1"
COVERAGE_SCHEMA = "gfx-frame-coverage-report/v1"

EXTERNAL = "external_input"
GENERATED = "renderer_generated"
UNPROVEN = "unproven"
BOUNDARY = "pre_frame_renderer_output"

SUPPORTED_KINDS = ("draw", "clear", "resolve", "copy")


class GraphError(RuntimeError):
    pass


def _name(value) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, str)):
        return str(value)
    return ""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _target_resource(target: dict) -> tuple[str, dict]:
    if not isinstance(target, dict):
        target = {}
    attachment = target.get("color_attachment")
    if isinstance(attachment, dict) and _name(attachment.get("name")):
        # Renderbuffers and textures occupy different GL namespaces.  A
        # renderbuffer colour attachment is part of the framebuffer, not a
        # sampleable texture; keying it as ``tex:N`` would alias a texture
        # that happens to share the numeric name.
        if str(attachment.get("type") or "") == "renderbuffer":
            framebuffer = target.get("framebuffer", 0)
            return f"fb:{framebuffer}", {}
        return f"tex:{_name(attachment.get('name'))}", attachment
    framebuffer = target.get("framebuffer", 0)
    return f"fb:{framebuffer}", {}


def _resource(resources: dict, key: str, *, kind: str, width=None, height=None,
              internal_format=None) -> dict:
    value = resources.get(key)
    if value is None:
        value = {
            "id": key,
            "kind": kind,
            "dimensions": [width, height],
            "format": internal_format,
            "producers": [],
            "consumers": [],
            "has_clear": False,
            "origin": None,
            "boundary": None,
            "captured_versions": [],
        }
        resources[key] = value
    else:
        if width and not value["dimensions"][0]:
            value["dimensions"] = [width, height]
        if internal_format and not value["format"]:
            value["format"] = internal_format
    return value


DEFAULT_CHECKPOINT_SCHEMA = "gfx-default-framebuffer-checkpoint/v1"
DEFAULT_CHECKPOINT_SCHEMAS = (
    DEFAULT_CHECKPOINT_SCHEMA,
    "gfx-default-framebuffer-checkpoint/v2",
)


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def default_checkpoint_problems(value, index: int,
                                frame_dir: Path | None = None) -> list[str]:
    """Validate one additive default-framebuffer checkpoint record.

    A checkpoint is authoritative reference evidence only when it names the
    exact pre-execution event, the ``fb:0`` resource, the full BGRA8 image and
    a readable blob.  Anything else is an instrumentation failure, never a
    match.
    """
    if not isinstance(value, dict):
        return [f"default checkpoint at event {index} is not an object"]
    problems: list[str] = []
    if value.get("schema") not in DEFAULT_CHECKPOINT_SCHEMAS:
        problems.append(
            f"default checkpoint at event {index} has an unsupported schema")
    if value.get("status") != "captured":
        problems.append(
            f"default checkpoint at event {index} was not captured")
    if value.get("event") != index:
        problems.append(
            f"default checkpoint at event {index} names event "
            f"{value.get('event')!r}")
    if str(value.get("target")) != "0":
        problems.append(
            f"default checkpoint at event {index} targets "
            f"{value.get('target')!r}, not the default framebuffer")
    if value.get("resource") != "fb:0":
        problems.append(
            f"default checkpoint at event {index} has resource "
            f"{value.get('resource')!r}, not fb:0")
    if value.get("pixel_format") != "BGRA8":
        problems.append(
            f"default checkpoint at event {index} is not BGRA8")
    if value.get("origin") != "lower_left":
        problems.append(
            f"default checkpoint at event {index} is not lower-left origin")
    if value.get("timing") != "pre_event":
        problems.append(
            f"default checkpoint at event {index} is not a pre-event "
            "observation")
    width = value.get("width")
    height = value.get("height")
    if (not isinstance(width, int) or isinstance(width, bool)
            or not 0 < width <= 8192):
        problems.append(
            f"default checkpoint at event {index} has invalid width")
        width = None
    if (not isinstance(height, int) or isinstance(height, bool)
            or not 0 < height <= 8192):
        problems.append(
            f"default checkpoint at event {index} has invalid height")
        height = None
    declared_bytes = value.get("bytes")
    if (width is not None and height is not None
            and declared_bytes != width * height * 4):
        problems.append(
            f"default checkpoint at event {index} byte count does not match "
            "its dimensions")
    name = value.get("file")
    if (not isinstance(name, str) or not name
            or Path(name).name != name or "/" in name):
        problems.append(
            f"default checkpoint at event {index} has an invalid blob name")
    elif frame_dir is not None:
        path = Path(frame_dir) / "blobs" / name
        if not path.is_file():
            problems.append(
                f"default checkpoint at event {index} blob is absent")
        elif (isinstance(declared_bytes, int)
              and path.stat().st_size != declared_bytes):
            problems.append(
                f"default checkpoint at event {index} blob size does not "
                "match its declaration")
    if not _is_sha256(value.get("sha256")):
        problems.append(
            f"default checkpoint at event {index} has an invalid sha256")
    # Capture identity is carried on the record when the tracer knows it.  An
    # absent pair is tolerated for older captures; a malformed or foreign pair
    # is not.
    session = value.get("session_id")
    if session is not None and (not isinstance(session, str) or not session):
        problems.append(
            f"default checkpoint at event {index} has an invalid session_id")
    capture_frame = value.get("capture_frame_id")
    if capture_frame is not None and (
            not isinstance(capture_frame, int) or isinstance(capture_frame, bool)
            or capture_frame <= 0):
        problems.append(
            f"default checkpoint at event {index} has an invalid "
            "capture_frame_id")
    return problems


COPY_OPERATION = "glCopyTexSubImage2D"
COPY_REQUIRED_FIELDS = (
    "operation",
    "source_framebuffer",
    "read_buffer",
    "source",
    "destination",
    "source_level",
    "destination_level",
    "source_rect",
    "destination_offset",
    "source_width",
    "source_height",
    "source_internal_format",
    "destination_width",
    "destination_height",
    "internal_format",
    "source_resolved",
    "destination_resolved",
)


def copy_contract_problems(event: dict, index: int) -> list[str]:
    """One supported-transfer contract shared by coverage and the graph.

    The replay host implements exactly one variant.  Anything else is invalid
    evidence, never a silently ignored event.
    """
    problems: list[str] = []
    missing = [field for field in COPY_REQUIRED_FIELDS
               if field not in event or event.get(field) is None]
    if missing:
        return [f"copy at event {index} is missing: {', '.join(missing)}"]
    if event.get("operation") != COPY_OPERATION:
        problems.append(
            f"unsupported copy operation at event {index}: "
            f"{event.get('operation')!r}")
    if event.get("is_blit") not in (None, False, 0):
        problems.append(f"unsupported blit at event {index}")
    if event.get("source_level") != 0 or event.get("destination_level") != 0:
        problems.append(f"unsupported mip level at event {index}")
    if event.get("source_resolved") is not True or event.get(
            "destination_resolved") is not True:
        problems.append(f"unresolved copy source or destination at event {index}")
    source_fbo = event.get("source_framebuffer")
    read_buffer = event.get("read_buffer")
    if not isinstance(source_fbo, int) or source_fbo < 0:
        problems.append(f"invalid source framebuffer at event {index}")
    if not isinstance(read_buffer, int) or read_buffer < 0:
        problems.append(f"invalid read buffer at event {index}")
    source = str(event.get("source") or "")
    destination = str(event.get("destination") or "")
    if not destination or destination == "0":
        problems.append(f"missing copy destination at event {index}")
    if source_fbo == 0:
        if read_buffer != 0x0405:
            problems.append(
                f"default-framebuffer copy does not read GL_BACK at "
                f"event {index}")
        if source not in ("", "0"):
            problems.append(
                f"default-framebuffer copy names an attachment at event {index}")
    else:
        if not source or source == "0":
            problems.append(
                f"non-default copy has no source attachment at event {index}")
        attachment = event.get("source_attachment")
        if attachment is None:
            problems.append(
                f"non-default copy has no attachment selector at event {index}")
        elif attachment != read_buffer:
            problems.append(
                f"copy attachment {attachment!r} does not match read buffer "
                f"{read_buffer!r} at event {index}")
    rect = event.get("source_rect")
    offset = event.get("destination_offset")
    if not isinstance(rect, list) or len(rect) != 4 or not all(
            isinstance(value, int) for value in rect):
        problems.append(f"malformed source rectangle at event {index}")
        return problems
    if not isinstance(offset, list) or len(offset) != 2 or not all(
            isinstance(value, int) for value in offset):
        problems.append(f"malformed destination offset at event {index}")
        return problems
    x, y, width, height = rect
    dx, dy = offset
    if width <= 0 or height <= 0 or x < 0 or y < 0 or dx < 0 or dy < 0:
        problems.append(f"invalid copy rectangle at event {index}")
    if event.get("width") != width or event.get("height") != height:
        problems.append(f"copy width/height disagree with rectangle at event {index}")
    source_width = event.get("source_width")
    source_height = event.get("source_height")
    destination_width = event.get("destination_width")
    destination_height = event.get("destination_height")
    for label, value in (("source_width", source_width),
                         ("source_height", source_height),
                         ("destination_width", destination_width),
                         ("destination_height", destination_height)):
        if not isinstance(value, int) or value <= 0:
            problems.append(f"invalid {label} at event {index}")
    if isinstance(source_width, int) and isinstance(source_height, int) and (
            x + width > source_width or y + height > source_height):
        problems.append(f"source rectangle exceeds source at event {index}")
    if isinstance(destination_width, int) and isinstance(
            destination_height, int) and (
            dx + width > destination_width or dy + height > destination_height):
        problems.append(
            f"destination rectangle exceeds destination at event {index}")
    for label in ("source_internal_format", "internal_format"):
        value = event.get(label)
        if label == "source_internal_format" and source_fbo == 0:
            # The default drawable format is not GL-queryable; the read buffer
            # and BGRA8 reference convention identify it.
            if value not in (0, None) and not isinstance(value, int):
                problems.append(f"invalid {label} at event {index}")
            continue
        if not isinstance(value, int) or value <= 0:
            problems.append(f"invalid {label} at event {index}")
    return problems


HISTORY_SCHEMA = "gfx-frame-sequence/v1"


def _file_sha256(path: Path) -> str | None:
    try:
        import hashlib
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _optional_document(path: Path) -> dict | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_frame_history(frame_dir: Path | str, trace: dict) -> dict:
    """Load and verify one continuous frame's ``frame-sequence.json`` record.

    A declared carried set is not evidence by itself.  The record must belong
    to this exact captured frame (manifest/reference digests), and its
    ``previous`` identity must resolve to the immediately preceding sequence
    record whose renderer history contains every declared carried name.  The
    result is ``absent`` for one-shot frames, ``verified`` for a bound record,
    and ``invalid`` when any identity or resource-history link disagrees.
    """
    frame_dir = Path(frame_dir)
    path = frame_dir / "frame-sequence.json"
    if not path.is_file():
        return {
            "schema": HISTORY_SCHEMA,
            "status": "absent",
            "reasons": [],
            "sequence_index": None,
            "previous": None,
            "render_targets": [],
            "carried_renderer_outputs": [],
            "delete_events": {},
            "mutation_events": {},
            "session_id": None,
            "pid": None,
            "capture_frame_id": None,
        }
    document = _optional_document(path)
    if document is None:
        return {
            "schema": HISTORY_SCHEMA,
            "status": "invalid",
            "reasons": ["frame-sequence.json is not a JSON object"],
            "sequence_index": None, "previous": None,
            "render_targets": [], "carried_renderer_outputs": [],
            "delete_events": {}, "mutation_events": {},
            "session_id": None, "pid": None,
            "capture_frame_id": None,
        }
    problems: list[str] = []
    if document.get("schema") != HISTORY_SCHEMA:
        problems.append("frame sequence schema is invalid")
    frame = trace.get("frame") or {}
    manifest_sha = _file_sha256(frame_dir / "frame-trace.json")
    seal = _optional_document(frame_dir / "frame-seal.json")
    if manifest_sha and document.get("manifest_sha256") != manifest_sha:
        problems.append(
            "frame sequence manifest_sha256 does not match the captured frame")
    if (frame.get("reference_sha256")
            and document.get("reference_sha256") != frame.get("reference_sha256")):
        problems.append(
            "frame sequence reference_sha256 does not match the captured "
            "reference")
    if seal is not None:
        if document.get("capture_frame_id") != seal.get("capture_frame_id"):
            problems.append(
                "frame sequence capture_frame_id does not match the seal")
        if document.get("session_id") != seal.get("session_id"):
            problems.append("frame sequence session_id does not match the seal")
        if document.get("pid") != seal.get("pid"):
            problems.append("frame sequence pid does not match the seal")
        if (seal.get("event_stream_sha256")
                and document.get("event_stream_sha256")
                != seal.get("event_stream_sha256")):
            problems.append(
                "frame sequence event_stream_sha256 does not match the seal")
    try:
        sequence_index = int(document.get("sequence_index") or 0)
    except (TypeError, ValueError):
        sequence_index = 0
    if sequence_index <= 0:
        problems.append("frame sequence index is invalid")

    def names(value, label: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            problems.append(f"frame sequence {label} is not an array")
            return []
        result = []
        for item in value:
            if not isinstance(item, str) or not item:
                problems.append(f"frame sequence {label} contains an invalid name")
                continue
            if item in result:
                problems.append(f"frame sequence {label} repeats {item}")
                continue
            result.append(item)
        return result

    render_targets = names(document.get("render_targets"), "render_targets")
    carried = names(document.get("carried_renderer_outputs"),
                    "carried_renderer_outputs")
    delete_events: dict[str, list[int]] = {}
    mutation_events: dict[str, list[int]] = {}
    lifecycle = document.get("lifecycle")
    if lifecycle is not None and not isinstance(lifecycle, list):
        problems.append("frame sequence lifecycle is not an array")
        lifecycle = []
    for item in lifecycle or []:
        if not isinstance(item, dict):
            problems.append("frame sequence lifecycle entry is invalid")
            continue
        kind = item.get("kind")
        if kind not in ("delete", "create", "reset", "mutation"):
            problems.append(f"unsupported lifecycle kind {kind!r}")
            continue
        try:
            event = int(item.get("event"))
        except (TypeError, ValueError):
            problems.append("lifecycle entry has no integer event index")
            continue
        entry_names = item.get("names")
        if not isinstance(entry_names, list):
            problems.append("lifecycle entry names are invalid")
            continue
        if kind == "delete":
            for name in entry_names:
                if isinstance(name, str) and name:
                    delete_events.setdefault(name, []).append(event)
        elif kind == "mutation":
            for name in entry_names:
                if isinstance(name, str) and name:
                    mutation_events.setdefault(name, []).append(event)

    previous = document.get("previous")
    previous_info = None
    if sequence_index > 1:
        if not isinstance(previous, dict):
            problems.append(
                "continuous frame has no previous-frame identity")
        else:
            previous_info = previous
            previous_dir = Path(str(previous.get("dir") or ""))
            if not previous_dir.is_absolute():
                # Portable cases keep the predecessor link relative to the
                # owning frame, never to the process working directory.
                previous_dir = (frame_dir / previous_dir).resolve()
            previous_path = previous_dir / "frame-sequence.json"
            previous_document = (
                _optional_document(previous_path)
                if previous_path.is_file() else None)
            if previous_document is None:
                problems.append(
                    "previous frame sequence record is absent or unreadable")
            else:
                if previous_document.get("schema") != HISTORY_SCHEMA:
                    problems.append("previous frame sequence schema is invalid")
                if int(previous_document.get("sequence_index") or 0) != (
                        sequence_index - 1):
                    problems.append(
                        "previous frame sequence index is not contiguous")
                if previous_document.get("capture_frame_id") != previous.get(
                        "capture_frame_id"):
                    problems.append(
                        "previous-frame capture id does not match its record")
                if previous_document.get("manifest_sha256") != previous.get(
                        "manifest_sha256"):
                    problems.append(
                        "previous-frame manifest digest does not match its "
                        "record")
                if previous_document.get("event_stream_sha256") != previous.get(
                        "event_stream_sha256"):
                    problems.append(
                        "previous-frame event stream digest does not match "
                        "its record")
                if (previous_document.get("session_id")
                        != document.get("session_id")):
                    problems.append(
                        "previous frame belongs to another session")
                previous_manifest = _file_sha256(
                    previous_dir / "frame-trace.json")
                if (previous_manifest
                        and previous_document.get("manifest_sha256")
                        != previous_manifest):
                    problems.append(
                        "previous frame manifest digest does not match the "
                        "captured file")
                previous_owned: set[str] = set()
                for field in ("render_targets",
                              "carried_renderer_outputs"):
                    value = previous_document.get(field) or []
                    if not isinstance(value, list) or any(
                            not isinstance(name, str) for name in value):
                        problems.append(
                            f"previous frame {field} is not a string array")
                        continue
                    previous_owned.update(value)
                for name in carried:
                    if name not in previous_owned:
                        problems.append(
                            f"carried resource {name} has no previous-frame "
                            "renderer identity")
    elif previous is not None:
        problems.append("first frame declares a previous-frame identity")
    elif carried:
        problems.append(
            "first frame declares carried renderer output without history")

    return {
        "schema": HISTORY_SCHEMA,
        "status": "invalid" if problems else "verified",
        "reasons": problems,
        "sequence_index": sequence_index or None,
        "previous": previous_info,
        "render_targets": render_targets,
        "carried_renderer_outputs": carried,
        "delete_events": delete_events,
        "mutation_events": mutation_events,
        "session_id": document.get("session_id"),
        "pid": document.get("pid"),
        "capture_frame_id": document.get("capture_frame_id"),
    }


CAPTURE_IDENTITY_FIELDS = (
    "session_id", "capture_frame_id", "present_index", "event_stream_sha256",
    "reference_sha256", "manifest_sha256", "pid",
)


def _graph_capture_identity(frame_dir: Path, trace: dict,
                            history: dict | None = None,
                            checkpoints: list[dict] | None = None) -> dict:
    """Frame identity from the graph's own records, never an invented schema.

    The authoritative source is the same-process frame seal.  A fixture or
    legacy frame without a seal may still carry identity on its default
    checkpoints or its frame history; those are used only when they are
    internally consistent.  Missing fields stay ``None`` rather than being
    guessed from a neighbouring frame or a present index.
    """
    from automation import m6_frame

    seal = None
    seal_path = Path(frame_dir) / "frame-seal.json"
    try:
        seal = m6_frame.load_seal(
            frame_dir, trace=trace, verify_counts=False, verify_blobs=False)
    except m6_frame.FrameError as exc:
        if seal_path.exists():
            # A present but invalid seal is not the same as an unsealed
            # fixture.  Never fall back to weaker records and call it valid.
            identity = {key: None for key in CAPTURE_IDENTITY_FIELDS}
            identity["source"] = "invalid_seal"
            identity["problem"] = str(exc)
            return identity
        seal = None
    if seal is not None:
        identity = {key: seal.get(key) for key in CAPTURE_IDENTITY_FIELDS}
        identity["source"] = "seal"
        return identity
    identity = {key: None for key in CAPTURE_IDENTITY_FIELDS}
    identity["source"] = "absent"
    if checkpoints is None:
        checkpoints = [
            event.get("default_checkpoint")
            for event in trace.get("_events") or []
            if isinstance(event.get("default_checkpoint"), dict)]
    sessions = {item.get("session_id") for item in checkpoints
                if item.get("session_id") is not None}
    frames = {item.get("capture_frame_id") for item in checkpoints
              if item.get("capture_frame_id") is not None}
    if len(sessions) == 1:
        identity["session_id"] = next(iter(sessions))
        identity["source"] = "checkpoints"
    if len(frames) == 1:
        identity["capture_frame_id"] = next(iter(frames))
        identity["source"] = "checkpoints"
    if identity["session_id"] is None and (history or {}).get("session_id"):
        identity["session_id"] = history["session_id"]
        identity["source"] = "history"
    if identity["capture_frame_id"] is None and (
            history or {}).get("capture_frame_id") is not None:
        identity["capture_frame_id"] = history["capture_frame_id"]
        identity["source"] = "history"
    identity["present_index"] = trace.get("present_index")
    frame = trace.get("frame") or {}
    if identity["reference_sha256"] is None:
        identity["reference_sha256"] = frame.get("reference_sha256")
    return identity


def _clear_coverage(event: dict, writes: list[str], width,
                    height) -> dict:
    """Which buffers a clear provably initialises over the full target.

    A clear only counts as initialisation when its own write mask enables the
    buffer and its scissor does not clip the target.  Older captures without a
    clear ``state`` record are explicitly unproven rather than assumed.
    """
    state = event.get("state") if isinstance(event.get("state"), dict) else None
    scissor = (state or {}).get("scissor")
    full_extent = False
    if isinstance(scissor, dict) and isinstance(scissor.get("enabled"), bool):
        if scissor["enabled"]:
            box = scissor.get("box")
            if isinstance(box, (list, tuple)) and len(box) == 4 \
                    and all(isinstance(value, int)
                            and not isinstance(value, bool) for value in box):
                x, y, w, h = box
                extent_width = int(width or 0)
                extent_height = int(height or 0)
                # Endpoint coverage: a negative origin moves the far edge
                # left/up, so the box's width alone cannot prove full extent.
                full_extent = (
                    extent_width > 0 and extent_height > 0
                    and x <= 0 and y <= 0
                    and x + w >= extent_width
                    and y + h >= extent_height)
            else:
                full_extent = False
        else:
            full_extent = True
    color_mask = (state or {}).get("color_mask")
    depth_mask = (state or {}).get("depth_mask")
    stencil_mask = (state or {}).get("stencil_mask")
    color_full = bool(
        "color" in writes and full_extent
        and isinstance(color_mask, (list, tuple))
        and list(color_mask) == [1, 1, 1, 1])
    depth_full = bool(
        "depth" in writes and full_extent and depth_mask is True)
    stencil_full = bool(
        "stencil" in writes and full_extent
        and stencil_mask in (-1, 0xFFFFFFFF))
    return {
        "full_extent": full_extent,
        "state_recorded": state is not None,
        "scissor_recorded": isinstance(scissor, dict),
        "scissor": scissor,
        "color_mask": color_mask,
        "depth_mask": depth_mask,
        "stencil_mask": stencil_mask,
        "color_full": color_full,
        "depth_full": depth_full,
        "stencil_full": stencil_full,
    }


def build(frame_dir: Path | str, trace: dict | None = None) -> dict:
    """Derive the resource graph and coverage classification for one frame.

    ``trace`` may be an already verified ``m6_frame.load_trace`` result.  The
    diagnostic path passes it so one capture is not hashed twice; callers that
    only have a directory keep the original signature.
    """
    from automation import m6_frame

    frame_dir = Path(frame_dir)
    try:
        if trace is None:
            trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
        elif Path(str(trace.get("_frame_dir") or "")).resolve() != frame_dir.resolve():
            raise m6_frame.FrameError(
                "supplied trace belongs to another frame directory")
    except m6_frame.FrameError as exc:
        return {
            "schema": SCHEMA,
            "frame_dir": str(frame_dir),
            "status": "invalid",
            "reasons": [str(exc)],
            "resources": {},
            "events": [],
        }
    events = trace.get("_events") or []
    manifest = trace
    history = load_frame_history(frame_dir, trace)
    logical = [
        int((manifest.get("frame") or {}).get("width") or 0),
        int((manifest.get("frame") or {}).get("height") or 0),
    ]
    resources: dict[str, dict] = {}
    target_names: set[str] = set()
    event_rows: list[dict] = []
    unknown_events: list[dict] = []
    contract_problems: list[str] = []
    default_checkpoints: list[dict] = []

    # Additive selected-event metadata.  It stays part of the event record, so
    # the operation count and replay stream are unchanged.  Several bounded
    # checkpoints may be attached to one frame; each must name its own
    # pre-execution event and the declared selection must match what was
    # actually attached.
    for index, event in enumerate(events):
        value = event.get("default_checkpoint")
        if value is None:
            continue
        problems = default_checkpoint_problems(value, index, frame_dir)
        if problems:
            contract_problems.extend(problems)
            continue
        default_checkpoints.append({
            "event": index,
            "target": "0",
            "resource": "fb:0",
            "reference_file": value["file"],
            "reference_sha256": value["sha256"],
            "reference_width": value["width"],
            "reference_height": value["height"],
            "session_id": value.get("session_id"),
            "capture_frame_id": value.get("capture_frame_id"),
        })
    attached_events = [item["event"] for item in default_checkpoints]
    if attached_events != sorted(attached_events) or len(
            set(attached_events)) != len(attached_events):
        # Each event carries at most one checkpoint, so this is only reachable
        # with a malformed stream; never derive an interval from it.
        contract_problems.append(
            "default checkpoints are not strictly increasing per event")
    declared_events = manifest.get("default_checkpoint_events")
    if declared_events is not None:
        if (not isinstance(declared_events, list)
                or any(not isinstance(item, int) or isinstance(item, bool)
                       for item in declared_events)):
            contract_problems.append(
                "frame declares a malformed default checkpoint event list")
        elif declared_events != attached_events:
            contract_problems.append(
                "frame declares default checkpoints "
                f"{declared_events!r} but attached {attached_events!r}")
        declared_count = manifest.get("default_checkpoint_count")
        if (isinstance(declared_count, int)
                and not isinstance(declared_count, bool)
                and isinstance(declared_events, list)
                and declared_count != len(declared_events)):
            contract_problems.append(
                "frame declares "
                f"{declared_count} default checkpoints but lists "
                f"{len(declared_events)}")
    # Every checkpoint in one frame must belong to the same execution.  The
    # authoritative seal binding is re-checked during reconciliation; here the
    # records only have to be internally consistent.
    checkpoint_sessions = {
        item["session_id"] for item in default_checkpoints
        if item.get("session_id") is not None}
    if len(checkpoint_sessions) > 1:
        contract_problems.append(
            "default checkpoints name more than one capture session")
    checkpoint_frames = {
        item["capture_frame_id"] for item in default_checkpoints
        if item.get("capture_frame_id") is not None}
    if len(checkpoint_frames) > 1:
        contract_problems.append(
            "default checkpoints name more than one capture frame")

    # First pass: every logical texture name that is ever written.
    for event in events:
        key, _ = _target_resource(event.get("target") or {})
        if key.startswith("tex:"):
            target_names.add(key[4:])
        if event.get("kind") in ("resolve", "copy"):
            destination = _name(event.get("destination"))
            if destination:
                target_names.add(destination)

    for index, event in enumerate(events):
        kind = event.get("kind")
        target = event.get("target") or {}
        key, attachment = _target_resource(target)
        width = target.get("width") or attachment.get("width")
        height = target.get("height") or attachment.get("height")
        internal = attachment.get("internal_format")
        draw = event.get("draw") or {}
        program = event.get("program") or {}
        state = event.get("state") or {}
        row = {
            "index": index,
            "event": index,
            "kind": kind,
            "target": key,
            "samples": [],
            "writes": [],
            "entry": draw.get("entry"),
            "caller_rva": draw.get("caller_rva"),
            "program": program.get("name"),
            "operation": event.get("operation"),
            "state": {
                name: state.get(name)
                for name in (
                    "blend", "depth", "stencil", "cull", "polygon",
                    "alpha_test", "scissor", "viewport", "color_mask",
                    "dither", "multisample",
                )
                if name in state
            },
        }
        if kind == "clear":
            resource = _resource(resources, key, kind="framebuffer"
                                 if key.startswith("fb:") else "texture",
                                 width=width, height=height,
                                 internal_format=internal)
            flags = int(event.get("flags") or 0)
            writes = []
            if flags & 1:
                writes.append("color")
            if flags & 2:
                writes.append("depth")
            if flags & 4:
                writes.append("stencil")
            coverage = _clear_coverage(event, writes, width, height)
            if writes:
                producer = {"event": index, "kind": "clear", "writes": writes,
                            "cleared": True, "coverage": coverage}
                resource["producers"].append(producer)
                row["writes"].extend(writes)
                row["clear_coverage"] = coverage
                # ``has_clear`` retains its historical meaning (a colour clear
                # happened).  The strict, dimension-checked claim is
                # ``coverage.color_full``; only fully covered depth/stencil
                # clears are recorded as initialisation.
                if "color" in writes:
                    resource["has_clear"] = True
                if coverage["depth_full"]:
                    resource["has_depth_clear"] = True
                if coverage["stencil_full"]:
                    resource["has_stencil_clear"] = True
        elif kind == "draw":
            resource = _resource(resources, key, kind="framebuffer"
                                 if key.startswith("fb:") else "texture",
                                 width=width, height=height,
                                 internal_format=internal)
            draw = event.get("draw") or {}
            producer = {
                "event": index,
                "kind": "draw",
                "entry": draw.get("entry"),
                "caller_rva": draw.get("caller_rva"),
                "program": (event.get("program") or {}).get("name"),
            }
            resource["producers"].append(producer)
            row["writes"].append("color")
            for texture in event.get("textures") or []:
                name = _name(texture.get("name"))
                if not name:
                    continue
                sampled_key = f"tex:{name}"
                level = (texture.get("levels") or [{}])
                first = level[0] if level else {}
                sampled = _resource(
                    resources, sampled_key, kind="texture",
                    width=texture.get("width") or first.get("width"),
                    height=texture.get("height") or first.get("height"),
                    internal_format=texture.get("internal_format"),
                )
                consumer = {
                    "event": index,
                    "unit": texture.get("unit"),
                    "sampler": texture.get("sampler_name"),
                }
                sampled["consumers"].append(consumer)
                row["samples"].append({
                    "resource": sampled_key,
                    "unit": texture.get("unit"),
                    "sampler": texture.get("sampler_name"),
                })
                if first.get("sha256") and first.get("file"):
                    sampled["captured_versions"].append({
                        "event": index,
                        "sha256": first.get("sha256"),
                        "file": first.get("file"),
                        "width": first.get("width"),
                        "height": first.get("height"),
                        "pixel_format": first.get("pixel_format"),
                    })
        elif kind in ("resolve", "copy"):
            source = _name(event.get("source") or event.get("source_texture"))
            destination = _name(event.get("destination"))
            source_fbo = event.get("source_framebuffer")
            operation = event.get("operation")
            contract_problems.extend(copy_contract_problems(event, index))
            if source in ("", "0") or source_fbo == 0:
                source_key = "fb:0"
            else:
                source_key = f"tex:{source}"
            sampled = _resource(resources, source_key, kind="framebuffer"
                                if source_key.startswith("fb:") else "texture")
            sampled["consumers"].append(
                {"event": index, "unit": None, "sampler": kind,
                 "operation": operation,
                 "destination": f"tex:{destination}" if destination else None,
                 "source_framebuffer": source_fbo,
                 "source_attachment": event.get("source_attachment")})
            row["samples"].append({"resource": source_key,
                                   "unit": None, "sampler": kind})
            if destination:
                dest_key = f"tex:{destination}"
                resource = _resource(
                    resources, dest_key, kind="texture",
                    width=event.get("destination_width")
                    or event.get("width"),
                    height=event.get("destination_height")
                    or event.get("height"),
                    internal_format=event.get("internal_format"))
                resource["producers"].append(
                    {"event": index, "kind": kind,
                     "operation": operation,
                     "rect": event.get("source_rect") or event.get("rect"),
                     "destination_offset": event.get(
                         "destination_offset"),
                     "source": source,
                     "source_framebuffer": source_fbo,
                     "read_buffer": event.get("read_buffer"),
                     "source_level": event.get("source_level"),
                     "destination_level": event.get("destination_level"),
                     "source_image": event.get("source_image")})
                row["writes"].append("color")
        else:
            unknown_events.append({"event": index, "kind": kind})
        event_rows.append(row)

    # Frame-boundary seed: the first draw's seed is the only pre-frame image
    # the legacy trace records.  It is a declared prior-frame renderer output.
    seed = None
    seed_key = None
    seed_event = None
    for index, event in enumerate(events):
        if event.get("kind") == "draw" and event.get("seed"):
            seed = event["seed"]
            seed_key, _ = _target_resource(event.get("target") or {})
            seed_event = index
            break
    if seed:
        value = resources.get(seed_key)
        if value is not None:
            value["boundary"] = {
                "kind": BOUNDARY,
                "event": seed_event,
                "sha256": seed.get("sha256"),
                "file": seed.get("file"),
                "width": seed.get("width"),
                "height": seed.get("height"),
            }

    # Classify every resource from its producer/consumer ordering.
    for key, resource in resources.items():
        consumers = sorted(resource["consumers"], key=lambda item: item["event"])
        producers = sorted(resource["producers"], key=lambda item: item["event"])
        for consumer in consumers:
            consumer["required_generation"] = sum(
                1 for producer in producers
                if producer["event"] < consumer["event"]
            )
        resource["consumer_generations"] = [
            {"event": consumer["event"],
             "required_generation": consumer["required_generation"]}
            for consumer in consumers
        ]
        first_consumer = consumers[0]["event"] if consumers else None
        writes_before = [item for item in producers
                         if first_consumer is None or item["event"] < first_consumer]
        writes_after = [item for item in producers
                        if first_consumer is not None and item["event"] > first_consumer]
        resource["classification_basis"] = []
        resource["possible_missing_producer"] = False
        if writes_before:
            resource["origin"] = GENERATED
            resource["initialized_by"] = (
                "clear" if writes_before[0]["kind"] == "clear" else writes_before[0]["kind"]
            )
            if first_consumer is not None:
                resource["consumed_version_event"] = writes_before[-1]["event"]
                resource["expected_generation"] = len(writes_before)
            resource["classification_basis"] = [
                "captured in-frame producer before first consumer"]
        elif key.startswith("fb:"):
            # The default framebuffer always exists before the frame; it is a
            # pre-frame output unless a clear initializes it in-frame.
            resource["origin"] = BOUNDARY if resource["boundary"] else UNPROVEN
            resource["classification_basis"] = [
                "default framebuffer without an in-frame initialization"]
        elif first_consumer is not None:
            width, height = resource["dimensions"]
            full_extent = (
                width in logical and height in logical
            )
            if writes_after:
                resource["origin"] = UNPROVEN
                resource["unproven_reason"] = (
                    "first consumer precedes every captured producer")
                resource["classification_basis"] = [
                    "consumer precedes the only captured producer"]
            elif full_extent or key[4:] in target_names:
                resource["origin"] = UNPROVEN
                resource["possible_missing_producer"] = True
                resource["unproven_reason"] = (
                    "sampled full-extent texture has no captured producer; a "
                    "resolve/copy or earlier pass may be missing from the capture")
                resource["classification_basis"] = [
                    "no in-frame producer",
                    "full-extent or in-frame target: missing producer is possible"]
            else:
                resource["origin"] = EXTERNAL
                resource["classification_basis"] = [
                    "no in-frame producer",
                    "captured content at every consumer",
                    "not an in-frame target",
                ]
        else:
            resource["origin"] = GENERATED if producers else UNPROVEN
            resource["classification_basis"] = (
                ["captured in-frame producer"] if producers
                else ["no producer and no consumer"])
            if not producers:
                resource["unproven_reason"] = "written resource has no producer"

        name = key.split(":", 1)[1] if key.startswith("tex:") else None
        carried_names = set(history.get("carried_renderer_outputs") or [])
        if name and name in carried_names:
            resource["history"] = {
                "carried": True,
                "status": history.get("status"),
                "previous": history.get("previous"),
                "sequence_index": history.get("sequence_index"),
                "capture_frame_id": history.get("capture_frame_id"),
            }
            if resource["origin"] in (EXTERNAL, UNPROVEN) and not producers:
                first_consumer = (min(
                    (int(item["event"]) for item in consumers
                     if item.get("event") is not None),
                    default=None))
                deletes = [int(event) for event in
                           (history.get("delete_events") or {}).get(name, [])
                           if isinstance(event, int)]
                blocking = [event for event in deletes
                            if first_consumer is None or event <= first_consumer]
                mutations = [int(event) for event in
                             (history.get("mutation_events") or {}).get(
                                 name, []) if isinstance(event, int)]
                if mutations:
                    resource["unproven_reason"] = (
                        "carried renderer output has an ordered CPU texture "
                        "mutation that the replay does not reproduce")
                    resource["classification_basis"] = [
                        "carried declaration invalidated by glTex* mutation"]
                elif history.get("status") != "verified":
                    resource["unproven_reason"] = (
                        "carried renderer output declares unverified "
                        "frame-sequence history")
                    resource["classification_basis"] = [
                        "carried declaration without verified history"]
                elif blocking:
                    resource["unproven_reason"] = (
                        "carried renderer output was deleted before its "
                        "first consumer")
                    resource["classification_basis"] = [
                        "carried declaration invalidated by lifecycle delete"]
                else:
                    resource["origin"] = BOUNDARY
                    resource["classification_basis"] = [
                        "authorized prior-frame Metal renderer output",
                        "previous-frame identity and resource link verified"]
                    resource["history"]["verified"] = True

    # Coverage reasons.
    reasons: list[str] = []
    if history.get("status") == "invalid":
        reasons.append(
            "frame-sequence history is invalid: "
            + "; ".join(history.get("reasons") or ["unknown"]))
    carried_names = set(history.get("carried_renderer_outputs") or [])
    for name in sorted(history.get("mutation_events") or {}):
        if name in carried_names:
            # A carried renderer output mutated by a CPU texture write has no
            # reproducible Metal history for later frames, whether or not this
            # frame samples it.  The frame cannot qualify as a verified carry.
            reasons.append(
                f"tex:{name} is carried but has an unreproduced CPU texture "
                "mutation; its persistent Metal history is not proven")
    if not manifest.get("complete"):
        reasons.append("frame manifest is not complete")
    if unknown_events:
        reasons.append(
            "unsupported event kinds: "
            + ", ".join(sorted({str(item["kind"]) for item in unknown_events}))
        )
    reasons.extend(contract_problems)
    unproven = sorted(key for key, value in resources.items()
                      if value["origin"] == UNPROVEN and value["consumers"])
    for key in unproven:
        value = resources[key]
        reasons.append(
            f"{key} is sampled but its content version is unproven: "
            f"{value.get('unproven_reason', 'unknown producer')}"
        )
    sampled_full_extent_missing = [
        key for key in unproven
        if resources[key]["dimensions"][0] in logical
        and resources[key]["dimensions"][1] in logical
    ]
    has_operation_capture = any(
        event.get("kind") in ("resolve", "copy") for event in events)
    if sampled_full_extent_missing and not has_operation_capture:
        reasons.append(
            "the capture records draws and clears only; a sampled full-extent "
            "texture without a producer indicates an uncaptured resolve/copy "
            "operation (for example _GfxResolve)")
    prefix = prefix_coverage(trace, events, resources)
    if prefix["status"] == "invalid":
        contract_problems.extend(prefix["problems"])
    coverage = m6_frame.coverage_report(trace)
    for problem in coverage.get("missing") or []:
        issue = problem.get("issue")
        if issue in ("missing_uniform_bytes", "draw_without_vertex_attributes",
                     "texture_without_levels", "shader_not_compiled",
                     "missing_shader_stage", "texture_capture_error"):
            reasons.append(f"capture coverage: {issue} at event "
                           f"{problem.get('event')}")

    external = sorted(key for key, value in resources.items()
                      if value["origin"] == EXTERNAL and value["consumers"])
    generated = sorted(key for key, value in resources.items()
                       if value["origin"] == GENERATED)
    generated_sampled = sorted(
        key for key in generated
        if resources[key]["consumers"] and not key.startswith("fb:")
    )
    capture = _graph_capture_identity(
        frame_dir, trace, history, checkpoints=default_checkpoints)
    if capture.get("source") == "invalid_seal":
        problem = ("frame seal is present but invalid: "
                   + str(capture.get("problem") or "unknown seal problem"))
        contract_problems.append(problem)
        reasons.append(problem)
    status = "invalid" if (
        not manifest.get("complete") or unknown_events or contract_problems
    ) else ("partial" if reasons else "complete")
    return {
        "schema": SCHEMA,
        "frame_dir": str(frame_dir),
        "status": status,
        "reasons": reasons,
        "capture": capture,
        "boundary": {
            "logical_extent": logical,
            "seed": seed,
            "seed_resource": seed_key if seed else None,
            "seed_superseded_by_in_frame_clear": bool(
                seed
                and resources.get(seed_key, {}).get("has_clear")
                and resources[seed_key]["producers"]
                and seed_event is not None
                and resources[seed_key]["producers"][0]["event"] < seed_event),
            "note": (
                "The first-draw seed is prior-frame renderer output. If an "
                "in-frame clear precedes the first draw it is superseded and "
                "does not narrow coverage."
            ),
        },
        "resources": resources,
        "events": event_rows,
        "default_checkpoints": default_checkpoints,
        "prefix": prefix,
        "counts": {
            "events": len(events),
            "draws": sum(1 for event in events if event.get("kind") == "draw"),
            "clears": sum(1 for event in events if event.get("kind") == "clear"),
            "resolves": sum(1 for event in events
                            if event.get("kind") in ("resolve", "copy")),
            "resources": len(resources),
            "external_inputs": len(external),
            "renderer_generated": len(generated),
            "renderer_generated_sampled": len(generated_sampled),
            "unproven": len(unproven),
        },
        "external_inputs": external,
        "renderer_generated": generated,
        "renderer_generated_sampled": generated_sampled,
        "unproven": unproven,
        "history": {
            "status": history.get("status"),
            "sequence_index": history.get("sequence_index"),
            "previous": history.get("previous"),
            "carried_renderer_outputs": history.get(
                "carried_renderer_outputs") or [],
            "reasons": history.get("reasons") or [],
            "mutation_events": history.get("mutation_events") or {},
        },
        "carried_resources": sorted(
            key for key, value in resources.items()
            if value.get("origin") == BOUNDARY
            and (value.get("history") or {}).get("carried")),
    }


def _event_output_resource(event: dict) -> str | None:
    """Resource written by one executed event, or ``None`` if it has none."""
    kind = event.get("kind")
    if kind in ("resolve", "copy"):
        destination = _name(event.get("destination"))
        return f"tex:{destination}" if destination else None
    if kind in ("draw", "clear"):
        return _target_resource(event.get("target") or {})[0]
    return None


def prefix_coverage(trace: dict, events: list[dict],
                    resources: dict) -> dict:
    """Validate opt-in exact-prefix coverage against the captured event stream.

    Every executed event from 0 through the declared end must have exactly one
    post-event observation naming the event's own written resource, and every
    resource consumed inside the prefix must have an in-prefix producer whose
    write was observed (or be an explicitly external/boundary input).  A
    missing, misattributed or unobserved operation is a coverage failure, never
    an implicit match.
    """
    prefix = trace.get("_prefix")
    if prefix is None:
        return {"status": "absent", "end": None, "post_events": [],
                "initial": None, "excluded_events": [], "problems": []}
    problems: list[str] = []
    end = prefix["end"]
    post = prefix["post_events"]
    if len(events) <= end:
        return {"status": "invalid", "end": end, "post_events": [],
                "initial": prefix["initial"],
                "problems": [f"prefix end {end} exceeds the "
                             f"{len(events)}-event stream"]}
    by_event: dict[int, dict] = {}
    excluded_events: list[int] = []
    for record in post:
        event_index = record.get("event")
        if not isinstance(event_index, int) or isinstance(event_index, bool):
            problems.append("prefix observation has an invalid event index")
            continue
        if event_index in by_event:
            problems.append(f"prefix event {event_index} is observed twice")
            continue
        by_event[event_index] = record
    for event_index in range(end + 1):
        event = events[event_index]
        record = by_event.get(event_index)
        if record is None:
            problems.append(
                f"prefix event {event_index} has no post-event observation")
            continue
        expected = _event_output_resource(event)
        if expected is None:
            problems.append(
                f"prefix event {event_index} has no identified output "
                "resource")
            continue
        if record.get("resource") != expected:
            problems.append(
                f"prefix event {event_index} observes "
                f"{record.get('resource')!r}, not {expected!r}")
        if record.get("kind") != event.get("kind"):
            problems.append(
                f"prefix event {event_index} kind disagrees with the stream")
        colour = record.get("color") or {}
        if colour.get("status") != "unsupported_format":
            problems.extend(
                f"prefix event {event_index}: {problem}"
                for problem in format_scope(record)["problems"])
        if colour.get("status") == "unsupported_format":
            if event.get("kind") != "clear":
                problems.append(
                    f"prefix event {event_index} has an unsupported colour "
                    "format for a non-clear operation")
                continue
            resource = resources.get(expected) or {}
            consumers = [
                int(item["event"]) for item in
                resource.get("consumers") or []
                if item.get("event") is not None
                and int(item["event"]) <= end]
            if consumers:
                problems.append(
                    f"prefix event {event_index} writes an unsupported colour "
                    f"format to {expected}, which is consumed at "
                    f"{consumers[0]}")
                continue
            later = [
                item for item in resource.get("producers") or []
                if item.get("event") is not None
                and event_index < int(item["event"]) <= end]
            unsafe = [
                item for item in later
                if not item.get("cleared")
                or not (item.get("coverage") or {}).get("color_full")]
            if unsafe:
                problems.append(
                    f"prefix event {event_index} writes an unsupported colour "
                    f"format to {expected}, which is rewritten without a full "
                    "clear inside the prefix")
                continue
            excluded_events.append(event_index)
            continue
        producers = [
            int(item["event"]) for item in
            (resources.get(expected) or {}).get("producers") or []
            if item.get("event") is not None]
        if event_index not in producers:
            problems.append(
                f"prefix event {event_index} is not a producer of {expected}")
    initial = prefix.get("initial") or {}
    if initial.get("resource") != "fb:0":
        # The initial observation is the default framebuffer state; a
        # non-default initial target is allowed only when it is the frame's
        # first written resource and is covered by its own producer.
        expected_initial = _event_output_resource(events[0])
        if initial.get("resource") != expected_initial:
            problems.append(
                "prefix initial observation does not name the first event's "
                "render target")
    for key, resource in resources.items():
        consumers = [
            int(item["event"]) for item in resource.get("consumers") or []
            if item.get("event") is not None and int(item["event"]) <= end]
        if not consumers:
            continue
        producers = [
            int(item["event"]) for item in resource.get("producers") or []
            if item.get("event") is not None and int(item["event"]) <= end]
        if producers:
            last = max(producers)
            if last not in by_event:
                problems.append(
                    f"{key} is consumed inside the prefix but its last "
                    f"in-prefix write at event {last} is not observed")
                continue
        elif resource.get("origin") not in (EXTERNAL, BOUNDARY):
            problems.append(
                f"{key} is consumed inside the prefix with no in-prefix "
                "producer and no initial observation")
    initial_state: list[dict] = []
    state_coverage: list[dict] = []

    def full_clear_before(resource_key: str, event_index: int,
                          component: str) -> bool:
        resource = resources.get(resource_key) or {}
        for producer in resource.get("producers") or []:
            producer_event = producer.get("event")
            if (producer_event is None
                    or int(producer_event) >= event_index):
                continue
            if not producer.get("cleared"):
                continue
            coverage = producer.get("coverage") or {}
            if coverage.get(f"{component}_full") is True:
                return True
        return False

    for event_index, event in enumerate(events[:end + 1]):
        kind = event.get("kind")
        if kind not in ("draw", "resolve", "copy"):
            continue
        target = event.get("target") or {}
        target_key = _event_output_resource(event)
        if kind in ("resolve", "copy"):
            source = _name(event.get("source") or event.get("source_texture"))
            source_key = ("fb:0" if source in ("", "0")
                          else f"tex:{source}")
            if not full_clear_before(source_key, event_index, "color"):
                problems.append(
                    f"prefix event {event_index} reads {source_key} before a "
                    "full colour clear")
            continue
        if target_key is None:
            problems.append(
                f"prefix event {event_index} has no identified output target")
            continue
        state = event.get("state") or {}
        depth = state.get("depth") or {}
        stencil = state.get("stencil") or {}
        depth_used = bool(depth.get("test") or depth.get("write"))
        stencil_used = bool(stencil.get("test") or stencil.get("write"))
        depth_present = target_key == "fb:0" or bool(
            target.get("depth_attachment"))
        stencil_present = target_key == "fb:0" or bool(
            target.get("depth_attachment"))
        if not full_clear_before(target_key, event_index, "color"):
            if target_key == "fb:0" and prefix.get("initial"):
                # The initial default-framebuffer observation is captured, but
                # it is only a verified input when a later full clear does not
                # overwrite it before consumption.
                problems.append(
                    f"prefix event {event_index} reads {target_key} before a "
                    "full colour clear")
            else:
                problems.append(
                    f"prefix event {event_index} reads {target_key} before a "
                    "full colour clear")
        if depth_used and depth_present and not full_clear_before(
                target_key, event_index, "depth"):
            problems.append(
                f"prefix event {event_index} uses depth on {target_key} "
                "before a full depth clear")
        if stencil_used and stencil_present and not full_clear_before(
                target_key, event_index, "stencil"):
            problems.append(
                f"prefix event {event_index} uses stencil on {target_key} "
                "before a full stencil clear")
        state_coverage.append({
            "event": event_index,
            "resource": target_key,
            "color_initialized": full_clear_before(
                target_key, event_index, "color"),
            "depth_used": depth_used,
            "depth_present": depth_present,
            "depth_initialized": (full_clear_before(
                target_key, event_index, "depth") if depth_present else None),
            "stencil_used": stencil_used,
            "stencil_present": stencil_present,
            "stencil_initialized": (full_clear_before(
                target_key, event_index, "stencil") if stencil_present else None),
        })
    for key, resource in resources.items():
        consumers = [
            int(item["event"]) for item in resource.get("consumers") or []
            if item.get("event") is not None and int(item["event"]) <= end]
        if not consumers:
            continue
        producers = [
            item for item in resource.get("producers") or []
            if item.get("event") is not None
            and int(item["event"]) < min(consumers)]
        first = min(consumers)
        if producers:
            producer = min(producers, key=lambda item: int(item["event"]))
            full_clear = any(
                item.get("cleared")
                and (item.get("coverage") or {}).get("color_full") is True
                for item in producers)
            initial_state.append({
                "resource": key,
                "first_consumer": first,
                "first_producer": int(producer["event"]),
                "status": "overwritten_by_producer",
                "proof": ("the first in-prefix writer is an observed full clear"
                          if (producer.get("cleared")
                              and (producer.get("coverage") or {}).get(
                                  "color_full") is True)
                          else ("an observed full clear precedes the first "
                                "consumer" if full_clear
                                else "the first in-prefix writer is observed")),
            })
        elif key == "fb:0" and prefix.get("initial"):
            initial_state.append({
                "resource": key,
                "first_consumer": first,
                "first_producer": None,
                "status": "initial_observation",
                "proof": "the initial pre-event-0 default-framebuffer state is "
                         "captured; no unverified pre-frame input is assumed",
            })
        elif resource.get("origin") in (EXTERNAL, BOUNDARY):
            initial_state.append({
                "resource": key,
                "first_consumer": first,
                "first_producer": None,
                "status": "declared_external_input",
                "proof": "the resource is an explicit external/boundary input, "
                         "not renderer output",
            })
        else:
            problems.append(
                f"{key} is consumed inside the prefix with no observed producer "
                "and no initial-state proof")
    if prefix.get("initial") and not any(
            row["resource"] == "fb:0" for row in initial_state):
        fb0 = resources.get("fb:0") or {}
        consumers = [
            int(item["event"]) for item in fb0.get("consumers") or []
            if item.get("event") is not None]
        first_consumer = min(consumers) if consumers else None
        producers = [
            item for item in fb0.get("producers") or []
            if item.get("event") is not None
            and (first_consumer is None or int(item["event"]) < first_consumer)]
        overwritten = any(
            item.get("cleared")
            and (item.get("coverage") or {}).get("color_full") is True
            for item in producers)
        if first_consumer is None or first_consumer > end:
            status = "not_consumed_inside_prefix"
            proof = ("the initial default-framebuffer state is captured and no "
                     "default-framebuffer consumer exists inside the prefix")
        elif overwritten:
            status = "overwritten_before_consumption"
            proof = ("an observed full default-framebuffer clear precedes the "
                     "first default-framebuffer consumer")
        else:
            status = "unverified_initial_consumption"
            proof = ("the initial default-framebuffer state is captured but no "
                     "full clear proves that it is irrelevant")
            problems.append(proof)
        initial_state.append({
            "resource": "fb:0",
            "first_consumer": first_consumer,
            "first_producer": (int(min(producers, key=lambda item: int(
                item["event"]))["event"]) if producers else None),
            "status": status,
            "proof": proof,
        })
    return {
        "status": "complete" if not problems else "invalid",
        "end": end,
        "initial": prefix.get("initial"),
        "post_events": post,
        "excluded_events": excluded_events,
        "initial_state": initial_state,
        "state_coverage": state_coverage,
        "problems": problems,
    }


def _producer_events(resources: dict, resource_key: str,
                     event: int) -> list[int]:
    """Graph-derived same-resource writers strictly before an observation.

    The list is intentionally explicit: an empty result means the graph found
    no writer before the observation, not that the previous event is assumed
    to be the writer.
    """
    resource = resources.get(resource_key) or {}
    return [
        int(item["event"]) for item in resource.get("producers") or []
        if item.get("event") is not None and int(item["event"]) < int(event)
    ]


def _writer_authority(events: list[int]) -> str:
    return "graph" if events else "unknown"


def checkpoint_plan(graph: dict) -> list[dict]:
    """First-consumer checkpoints for every sampled in-frame target.

    The OpenGL texture captured at the consumer is the reference for the
    state *before* that operation executes.  These are reference checkpoints
    only: the replay never seeds them into Metal.
    """
    plan: list[dict] = []
    resources = graph.get("resources") or {}
    for key in graph.get("renderer_generated_sampled") or []:
        resource = resources.get(key)
        if not resource:
            continue
        consumers = sorted(resource.get("consumers") or [],
                           key=lambda item: item["event"])
        if not consumers:
            continue
        first = consumers[0]
        version = next(
            (item for item in resource.get("captured_versions") or []
             if item["event"] == first["event"]),
            None,
        )
        if version is None:
            continue
        plan.append({
            "event": first["event"],
            "target": key.split(":", 1)[1],
            "resource": key,
            "reference_file": version["file"],
            "reference_sha256": version["sha256"],
            "reference_width": version["width"],
            "reference_height": version["height"],
            "producer_events": _producer_events(
                resources, key, first["event"]),
            "writer_authority": _writer_authority(
                _producer_events(resources, key, first["event"])),
        })
    # A captured in-frame copy may include the effective default-framebuffer
    # source pixels.  That is the authoritative checkpoint for the copy input,
    # distinct from the copied destination.
    seen_sources: set[tuple[int, str]] = set()
    for resource in resources.values():
        for producer in resource.get("producers") or []:
            image = producer.get("source_image")
            if not image:
                continue
            if int(producer.get("source_framebuffer") or 0) != 0:
                continue
            event = int(producer["event"])
            key = (event, "0")
            if key in seen_sources:
                continue
            seen_sources.add(key)
            producers = _producer_events(resources, "fb:0", event)
            plan.append({
                "event": event,
                "target": "0",
                "resource": "fb:0",
                "reference_file": image.get("file"),
                "reference_sha256": image.get("sha256"),
                "reference_width": image.get("width"),
                "reference_height": image.get("height"),
                "producer_events": producers,
                "writer_authority": _writer_authority(producers),
            })
    # The opt-in capture-side checkpoint is authoritative reference evidence for
    # the default framebuffer immediately before its event.  It is never a
    # texture uploaded into Metal.
    existing = {(int(item["event"]), str(item["target"])) for item in plan}
    for item in graph.get("default_checkpoints") or []:
        key = (int(item["event"]), str(item["target"]))
        if key in existing:
            continue
        existing.add(key)
        producers = _producer_events(resources, "fb:0", int(item["event"]))
        plan.append({
            "event": int(item["event"]),
            "target": "0",
            "resource": "fb:0",
            "reference_file": item["reference_file"],
            "reference_sha256": item["reference_sha256"],
            "reference_width": item["reference_width"],
            "reference_height": item["reference_height"],
            "producer_events": producers,
            "writer_authority": _writer_authority(producers),
            "session_id": item.get("session_id"),
            "capture_frame_id": item.get("capture_frame_id"),
        })
    plan.sort(key=lambda item: (item["event"], item["target"]))
    return plan


def format_scope(observation: dict) -> dict:
    """Derive the applicable comparison channels from sealed attachment identity.

    The definition lives in :mod:`automation.gfx_observation` so the native
    writer, the graph, the portable case builder and the reconciler cannot
    disagree.  The declared ``alpha_applicable`` field is evidence to be
    checked, never an authority.
    """
    return gfx_observation.format_scope(observation)


def _alpha_applicable(observation: dict) -> bool:
    return format_scope(observation)["alpha_applicable"]


def prefix_checkpoint_plan(graph: dict) -> list[dict]:
    """Post-event Metal checkpoints for the opt-in exact-prefix mode.

    The reference is the original EU4 post-event readback captured natively.
    Each entry names ``timing="post_event"`` so the replay reads the target
    after the event executed, never before it.
    """
    prefix = graph.get("prefix") or {}
    if prefix.get("status") != "complete":
        return []
    resources = graph.get("resources") or {}
    plan: list[dict] = []
    for observation in prefix.get("post_events") or []:
        if ((observation.get("color") or {}).get("status")
                == "unsupported_format"):
            continue
        scope = format_scope(observation)
        event = int(observation["event"])
        resource = str(observation["resource"])
        target = str(observation["target_name"])
        colour = observation.get("color") or {}
        plan.append({
            "event": event,
            "target": target,
            "resource": resource,
            "timing": "post_event",
            "file": f"prefix-event{event}-{target}.bgra",
            "reference_file": colour.get("file"),
            "reference_sha256": colour.get("sha256"),
            "reference_width": observation.get("width"),
            "reference_height": observation.get("height"),
            "reference_pixel_format": colour.get("pixel_format")
            or observation.get("pixel_format"),
            "alpha_applicable": scope["alpha_applicable"],
            "compared_channels": scope["compared_channels"],
            "producer_events": [event],
            "writer_authority": "prefix_observation",
            "session_id": _observation_identity(observation, "session_id"),
            "capture_frame_id": _observation_identity(
                observation, "capture_frame_id"),
        })
    return plan


def _observation_identity(observation: dict, field: str):
    value = observation.get(field)
    return value if value is not None else None


def checkpoint_request(graph: dict) -> dict:
    """Build the deterministic checkpoint request for one graph.

    The request is derived only from the immutable captured frame.  The
    in-process coordinator publishes this document before the Metal bridge is
    released, and the bridge binds its digest into the shader receipt.
    """
    plan = checkpoint_plan(graph)
    checkpoints = []
    for item in plan:
        entry = {
            "event": item["event"],
            "target": item["target"],
            "resource": item["resource"],
            "file": f"intermediate-event{item['event']}-tex{item['target']}.bgra",
            "reference_file": item["reference_file"],
            "reference_sha256": item["reference_sha256"],
            "reference_width": item["reference_width"],
            "reference_height": item["reference_height"],
            "producer_events": item["producer_events"],
            "writer_authority": item["writer_authority"],
        }
        # Capture identity and writer_authority are additive.  A request
        # derived before them therefore has a different digest; legacy
        # published requests remain valid through the explicit legacy path,
        # but their exact source digest is not preserved by re-derivation.
        if item.get("session_id") is not None:
            entry["session_id"] = item["session_id"]
        if item.get("capture_frame_id") is not None:
            entry["capture_frame_id"] = item["capture_frame_id"]
        checkpoints.append(entry)
    for item in prefix_checkpoint_plan(graph):
        entry = {
            "event": item["event"],
            "target": item["target"],
            "resource": item["resource"],
            "timing": "post_event",
            "file": item["file"],
            "reference_file": item["reference_file"],
            "reference_sha256": item["reference_sha256"],
            "reference_width": item["reference_width"],
            "reference_height": item["reference_height"],
            "reference_pixel_format": item.get("reference_pixel_format"),
            "alpha_applicable": item.get("alpha_applicable", True),
            "compared_channels": item.get("compared_channels"),
            "producer_events": item["producer_events"],
            "writer_authority": item["writer_authority"],
        }
        if item.get("session_id") is not None:
            entry["session_id"] = item["session_id"]
        if item.get("capture_frame_id") is not None:
            entry["capture_frame_id"] = item["capture_frame_id"]
        checkpoints.append(entry)
    return {
        "schema": "gfx-frame-checkpoint-request/v1",
        "checkpoints": checkpoints,
    }


def _graph_capture_identity_from_records(graph: dict) -> dict:
    """Best-effort identity from a serialized graph's checkpoint records."""
    identity = {}
    for item in field_checkpoints(graph):
        for key in ("session_id", "capture_frame_id"):
            if item.get(key) is not None:
                identity[key] = item[key]
    if identity:
        identity["source"] = "checkpoints"
    return identity


def field_checkpoints(graph: dict) -> list[dict]:
    rows = []
    for item in graph.get("default_checkpoints") or []:
        if isinstance(item, dict):
            rows.append(item)
    return rows


def provenance_matrix(graph: dict, checkpoints: list[dict] | None = None,
                      frame_identity: dict | None = None,
                      writer_limit: int = 8) -> list[dict]:
    """Render observation/writer rows from the graph, not by arithmetic.

    Every requested or reconciled observation is projected with:

    * its own event, target, resource and ``phase="pre_event"`` scope;
    * the graph's same-resource writers strictly before that event, newest
      last, with ``writer_authority="unknown"`` when the graph records none;
    * the operation *at* the observation event separately, explicitly marked
      as not the writer of the observed pre-event content;
    * the frame/capture identity, and checkpoint statistics when supplied.

    Callers must not infer a writer as ``event - 1`` or by subtracting one
    producer from a list.  A graph with no prior same-resource writer yields an
    explicit unknown rather than a misleading adjacency.
    """
    resources = graph.get("resources") or {}
    events = {int(row.get("event")): row
              for row in graph.get("events") or []
              if row.get("event") is not None}
    observations = checkpoints
    if observations is None:
        observations = checkpoint_plan(graph)
    identity = dict(frame_identity or graph.get("capture") or {})
    if not identity:
        # A legacy caller may pass a graph serialized before the captured
        # identity projection existed; recover only records actually present.
        identity = _graph_capture_identity_from_records(graph)
    rows: list[dict] = []
    for item in observations:
        if not isinstance(item, dict) or item.get("event") is None:
            continue
        event = int(item["event"])
        resource_key = str(item.get("resource") or (
            "fb:0" if str(item.get("target")) == "0"
            else f"tex:{item.get('target')}"))
        producer_events = _producer_events(resources, resource_key, event)
        writers = []
        for writer_event in producer_events[-writer_limit:]:
            writer = events.get(writer_event) or {}
            writers.append({
                "event": writer_event,
                "kind": writer.get("kind"),
                "operation": writer.get("operation"),
                "entry": writer.get("entry"),
                "caller_rva": writer.get("caller_rva"),
                "program": writer.get("program"),
            })
        upcoming = events.get(event) or {}
        resource = resources.get(resource_key) or {}
        generations = [
            row for row in resource.get("consumer_generations") or []
            if int(row.get("event", -1)) == event
        ]
        row = {
            "observation": {
                "event": event,
                "target": item.get("target"),
                "resource": resource_key,
                "phase": "pre_event",
                "authority": ("authoritative" if item.get("authoritative")
                              else item.get("authority") or "requested"),
                "status": item.get("status"),
            },
            "writers": writers,
            "writer_events": producer_events[-writer_limit:],
            "writer_authority": _writer_authority(producer_events),
            "producer_event": (producer_events[-1]
                               if producer_events else None),
            "generation": {
                "required": (generations[0].get("required_generation")
                             if generations else None),
                "producers_before": len(producer_events),
                "captured_writes": len(resource.get("producers") or []),
            },
            "upcoming_event": {
                "event": event,
                "kind": upcoming.get("kind"),
                "operation": upcoming.get("operation"),
                "entry": upcoming.get("entry"),
                "program": upcoming.get("program"),
                "note": "the operation at the observation event; it has not "
                        "written the observed pre-event content",
            },
            "frame": identity,
            "statistics": item.get("statistics"),
        }
        rows.append(row)
    return rows


def write_checkpoint_request(graph: dict, path: Path | str) -> dict:
    """Atomically publish a checkpoint request.

    A reader either sees the complete document or no document, so a stale or
    partially written request can never be bound to a replay.
    """
    import os

    value = checkpoint_request(graph)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)
    return value


def evaluate_replay(graph: dict, replay: dict) -> dict:
    """Validate replay provenance against the capture contract.

    A pass cannot hide a captured OpenGL intermediate in the Metal dependency
    chain: every sampled renderer-generated resource must have been produced by
    Metal with a nonzero generation.  Pixel equality alone is never sufficient.
    """
    reasons: list[str] = []
    status = "complete"
    if replay.get("status") != "ok":
        reasons.append("replay did not complete")
        status = "invalid"
    if type(replay.get("events")) is int and replay.get("encoded") != replay["events"]:
        reasons.append(
            f"encoded {replay.get('encoded')} of {replay.get('events')} events")
        status = "invalid"
    if replay.get("failed"):
        reasons.append(f"{replay['failed']} event(s) failed")
        status = "invalid"
    if replay.get("command_status") != 4:
        reasons.append("Metal command buffer did not complete")
        status = "invalid"
    if not replay.get("readback"):
        reasons.append("final Metal readback is missing")
        status = "invalid"
    provenance = replay.get("provenance") or {}
    history = replay.get("history") or {}
    samples = replay.get("_provenance_samples")
    if samples is None:
        samples = []

    def event_index(value) -> int:
        """Preserve event 0; only a missing value becomes -1."""
        return -1 if value is None else int(value)

    by_texture: dict[str, list[dict]] = {}
    by_consumer: dict[tuple, list[dict]] = {}
    for sample in samples:
        by_texture.setdefault(str(sample.get("texture") or ""), []).append(sample)
        by_consumer.setdefault(
            (event_index(sample.get("event")),
             str(sample.get("sampler") or "")),
            []).append(sample)

    history_resources: dict[str, dict] = {}
    if (isinstance(history, dict) and history.get("status") == "ok"
            and history.get("sequence")):
        for entry in history.get("resources") or []:
            if isinstance(entry, dict) and entry.get("name") is not None:
                history_resources[str(entry["name"])] = entry

    def generation_problem(resource_key: str, resource: dict,
                           event: int, required: int | None,
                           actual: int) -> dict | None:
        """Exact expected generation, derived from the session's own history.

        A newer generation is not acceptable merely because it exceeds the
        frame-local producer count: the base generation retained by the
        persistent session is subtracted first, so an unrelated recreation or
        a stale carry cannot pass.
        """
        if required is None:
            return None
        name = (resource_key.split(":", 1)[1]
                if resource_key.startswith("tex:") else resource_key)
        entry = history_resources.get(name)
        if entry is None or not resource_key.startswith("tex:"):
            expected = required
        else:
            try:
                final = int(entry.get("generation") or 0)
            except (TypeError, ValueError):
                final = 0
            writes = len(resource.get("producers") or [])
            base = final - writes
            if base < 0:
                return {"kind": "history_generation_inconsistent",
                        "expected_generation": required,
                        "actual_generation": actual,
                        "history_generation": final,
                        "frame_writes": writes}
            expected = base + required
        if actual != expected:
            return {"kind": "stale_generation",
                    "expected_generation": expected,
                    "actual_generation": actual,
                    "local_required_generation": required}
        return None

    # Every captured consumer must be represented exactly once per
    # (event, sampler), with the captured resource and required generation.
    consumer_problems: list[dict] = []
    resources = graph.get("resources") or {}
    for key, resource in resources.items():
        name = key.split(":", 1)[1] if key.startswith("tex:") else key
        required_by_event = {
            int(row["event"]): int(row["required_generation"])
            for row in resource.get("consumer_generations") or []
        }
        for consumer in resource.get("consumers") or []:
            event = event_index(consumer.get("event"))
            sampler = str(consumer.get("sampler") or "")
            if sampler in ("resolve", "copy"):
                # Resolve/copy execution is validated by the ordered event
                # stream, not by a texture-unit binding.
                continue
            rows = by_consumer.get((event, sampler), [])
            if not rows:
                consumer_problems.append({
                    "event": event, "sampler": sampler, "resource": key,
                    "kind": "missing_consumer"})
                continue
            if len(rows) != 1:
                # Two records for the same expected (event, sampler) are two
                # contested bindings, not one confirmation.
                consumer_problems.append({
                    "event": event, "sampler": sampler, "resource": key,
                    "kind": "duplicate_consumer",
                    "records": len(rows)})
                continue
            textures = {str(row.get("texture") or "") for row in rows}
            if len(textures) != 1:
                consumer_problems.append({
                    "event": event, "sampler": sampler, "resource": key,
                    "kind": "ambiguous_consumer", "textures": sorted(textures)})
                continue
            if textures != {name}:
                consumer_problems.append({
                    "event": event, "sampler": sampler, "resource": key,
                    "kind": "wrong_resource",
                    "actual_texture": next(iter(textures))})
                continue
            for row in rows:
                source = row.get("source")
                if key.startswith("fb:"):
                    continue
                if resource.get("origin") == GENERATED:
                    if source != "metal_render_target":
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key,
                            "kind": "captured_intermediate_substitution"})
                    required = required_by_event.get(event)
                    actual_generation = int(row.get("generation") or 0)
                    if (row.get("carried")
                            and row.get("history_generation") is not None):
                        # A carried sample must equal the exact generation
                        # retained by the session: the final history
                        # generation minus every write this frame.
                        name = key.split(":", 1)[1]
                        entry = history_resources.get(name) or {}
                        try:
                            final_generation = int(
                                entry.get("generation") or 0)
                        except (TypeError, ValueError):
                            final_generation = 0
                        base_generation = final_generation - len(
                            resource.get("producers") or [])
                        if (actual_generation != int(row["history_generation"])
                                or actual_generation <= 0
                                or actual_generation != base_generation
                                or not row.get("carried")):
                            consumer_problems.append({
                                "event": event, "sampler": sampler,
                                "resource": key, "kind": "stale_generation",
                                "expected_generation":
                                    int(row["history_generation"]),
                                "actual_generation": actual_generation})
                    else:
                        problem = generation_problem(
                            key, resource, event, required, actual_generation)
                        if problem is not None:
                            consumer_problems.append(dict(
                                problem, event=event, sampler=sampler,
                                resource=key))
                elif resource.get("origin") == BOUNDARY:
                    actual_generation = int(row.get("generation") or 0)
                    history_generation = row.get("history_generation")
                    name = key.split(":", 1)[1]
                    entry = history_resources.get(name) or {}
                    try:
                        expected_carried = int(entry.get("generation") or 0)
                    except (TypeError, ValueError):
                        expected_carried = 0
                    if source != "metal_render_target":
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key,
                            "kind": "carried_resource_external_substitution"})
                    elif not row.get("carried"):
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key,
                            "kind": "carried_resource_not_declared"})
                    elif (history_generation is None
                          or actual_generation != int(history_generation)
                          or expected_carried <= 0
                          or actual_generation != expected_carried):
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key, "kind": "stale_carried_generation",
                            "expected_generation": expected_carried,
                            "history_generation": history_generation,
                            "actual_generation": actual_generation})
                elif resource.get("origin") == EXTERNAL:
                    if source != "external_capture":
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key, "kind": "external_not_captured"})
    if consumer_problems:
        reasons.append(
            f"{len(consumer_problems)} expected consumer binding(s) failed")
        status = "invalid"
    # Copy/resolve sources are not texture-unit bindings.  Validate them
    # against the replay's ordered transfer records and generation changes.
    transfer_problems: list[dict] = []
    transfers = replay.get("transfers")
    if transfers is None:
        transfers = []
    transfers_by_event: dict[int, list[dict]] = {}
    for transfer in transfers:
        if isinstance(transfer, dict):
            transfers_by_event.setdefault(
                event_index(transfer.get("event")), []).append(transfer)
    for key, resource in resources.items():
        for consumer in resource.get("consumers") or []:
            sampler = str(consumer.get("sampler") or "")
            if sampler not in ("resolve", "copy"):
                continue
            event = event_index(consumer.get("event"))
            rows = transfers_by_event.get(event, [])
            if len(rows) == 0:
                transfer_problems.append({
                    "event": event, "resource": key, "kind": "missing_transfer"})
                continue
            if len(rows) > 1:
                transfer_problems.append({
                    "event": event, "resource": key,
                    "kind": "duplicate_transfer", "records": len(rows)})
                continue
            transfer = rows[0]
            if transfer.get("status") != "ok":
                transfer_problems.append({
                    "event": event, "resource": key,
                    "kind": "transfer_not_executed"})
            expected_source = (key.split(":", 1)[1]
                               if key.startswith("tex:") else "0")
            if str(transfer.get("source") or "") != expected_source:
                transfer_problems.append({
                    "event": event, "resource": key,
                    "kind": "wrong_source",
                    "expected": expected_source,
                    "actual": transfer.get("source")})
            destination = consumer.get("destination")
            if destination and str(transfer.get("destination") or "") != str(
                    destination).split(":", 1)[-1]:
                transfer_problems.append({
                    "event": event, "resource": key,
                    "kind": "wrong_destination",
                    "expected": destination,
                    "actual": transfer.get("destination")})
            # Every transfer must carry the source category implied by the
            # captured resource.  A name of "0" alone does not prove the
            # independently rendered Metal default target was used.
            source_kind = str(transfer.get("source_kind") or "")
            if key == "fb:0":
                if resource.get("origin") == GENERATED:
                    allowed_kinds = {"metal_default"}
                else:
                    allowed_kinds = {"boundary_capture"}
            elif key.startswith("tex:") and resource.get("origin") in (
                    GENERATED, BOUNDARY):
                allowed_kinds = {"metal_render_target"}
            else:
                allowed_kinds = {"boundary_capture"}
            if source_kind not in allowed_kinds:
                transfer_problems.append({
                    "event": event, "resource": key,
                    "kind": "source_category",
                    "expected": sorted(allowed_kinds),
                    "source_kind": source_kind})
            if key.startswith("tex:"):
                required = next(
                    (int(row["required_generation"])
                     for row in resource.get("consumer_generations") or []
                     if event_index(row.get("event")) == event), None)
                actual_generation = int(transfer.get("source_generation") or 0)
                if resource.get("origin") == BOUNDARY:
                    name = key.split(":", 1)[1]
                    entry = history_resources.get(name) or {}
                    expected = int(entry.get("generation") or 0)
                    if actual_generation != expected or expected <= 0:
                        transfer_problems.append({
                            "event": event, "resource": key,
                            "kind": "stale_carried_source_generation",
                            "expected": expected,
                            "actual": actual_generation})
                else:
                    problem = generation_problem(
                        key, resource, event, required, actual_generation)
                    if problem is not None:
                        if problem.get("kind") == "stale_generation":
                            problem["kind"] = "stale_source_generation"
                        transfer_problems.append(dict(
                            problem, event=event, resource=key))
            if destination:
                destination_resource = resources.get(str(destination)) or {}
                producers = destination_resource.get("producers") or []
                expected_after = sum(
                    1 for producer in producers
                    if event_index(producer.get("event")) <= event)
                destination_entry = history_resources.get(
                    str(destination).split(":", 1)[-1])
                if destination_entry is not None:
                    try:
                        final = int(destination_entry.get("generation") or 0)
                    except (TypeError, ValueError):
                        final = 0
                    base = final - len(producers)
                    if base >= 0:
                        expected_after = base + expected_after
                before = int(transfer.get("destination_generation_before") or 0)
                after = int(transfer.get("destination_generation_after") or 0)
                if after != before + 1 or after != expected_after:
                    transfer_problems.append({
                        "event": event, "resource": destination,
                        "kind": "destination_generation",
                        "expected_after": expected_after,
                        "before": before, "after": after})
    if transfer_problems:
        reasons.append(
            f"{len(transfer_problems)} copy/resolve provenance check(s) failed")
        status = "invalid"

    seeded: list[dict] = []
    sampled_keys = list(graph.get("renderer_generated_sampled") or [])
    for key in graph.get("carried_resources") or []:
        if key not in sampled_keys:
            sampled_keys.append(key)
    for key in sampled_keys:
        resource = (graph.get("resources") or {}).get(key, {})
        draw_consumers = [
            consumer for consumer in resource.get("consumers") or []
            if str(consumer.get("sampler") or "") not in ("resolve", "copy")
        ]
        if not draw_consumers:
            # Copy-only resources are validated by transfer records above;
            # they never create texture-unit bindings.
            continue
        name = key.split(":", 1)[1]
        bindings = by_texture.get(name)
        if not bindings:
            reasons.append(
                f"{key} is sampled in the capture but the replay reports no "
                "binding; provenance is incomplete")
            status = "invalid"
            continue
        external = [item for item in bindings
                    if item.get("source") != "metal_render_target"]
        zero = [item for item in bindings
                if item.get("source") == "metal_render_target"
                and int(item.get("generation") or 0) == 0]
        resource = (graph.get("resources") or {}).get(key, {})
        required_by_event = {
            row["event"]: row["required_generation"]
            for row in resource.get("consumer_generations") or []
        }
        stale = []
        for item in bindings:
            if item.get("source") != "metal_render_target":
                continue
            expected = required_by_event.get(event_index(item.get("event")))
            if expected is None:
                continue
            generation = int(item.get("generation") or 0)
            if resource.get("origin") == BOUNDARY:
                history_generation = item.get("history_generation")
                if (item.get("carried")
                        and history_generation is not None
                        and generation == int(history_generation)
                        and generation > 0):
                    continue
                stale.append({
                    "event": item.get("event"),
                    "expected_generation": history_generation,
                    "actual_generation": generation,
                })
                continue
            problem = generation_problem(
                key, resource, event_index(item.get("event")), expected,
                generation)
            if problem is not None:
                stale.append(dict(problem, event=item.get("event")))
        if external or zero or stale:
            seeded.append({
                "resource": key,
                "external_bindings": len(external),
                "generation_zero_bindings": len(zero),
                "stale_generation_bindings": len(stale),
                "stale": stale[:8],
            })
    for item in provenance.get("first_sample_before_any_write") or []:
        seeded.append({
            "resource": f"tex:{item.get('texture')}",
            "first_sample_before_any_write": True,
            "event": item.get("event"),
        })
    if seeded:
        reasons.append(
            "a renderer-generated resource was bound from captured content or "
            "before Metal produced it")
        if status == "invalid" or any(
                item.get("external_bindings") for item in seeded):
            status = "invalid"
        else:
            status = "partial"
    if graph.get("status") == "invalid":
        status = "invalid"
        reasons.extend(graph.get("reasons") or [])
    elif graph.get("status") == "partial" and status == "complete":
        status = "partial"
        reasons.extend(graph.get("reasons") or [])
    return {
        "schema": "gfx-independent-replay-evaluation/v1",
        "status": status,
        "reasons": reasons,
        "seeded_intermediates": seeded,
        "consumer_validation": {
            "expected": sum(len(value.get("consumers") or [])
                            for value in resources.values()),
            "problems": consumer_problems[:100],
            "problems_count": len(consumer_problems),
        },
        "transfer_validation": {
            "executed": len(transfers),
            "problems": transfer_problems[:100],
            "problems_count": len(transfer_problems),
        },
        "provenance": {
            "bindings": provenance.get("bindings"),
            "metal_render_target_bindings": provenance.get(
                "metal_render_target_bindings"),
            "external_capture_bindings": provenance.get(
                "external_capture_bindings"),
            "render_target_textures": provenance.get("render_target_textures"),
            "external_textures": provenance.get("external_textures"),
        },
        "coverage_boundary": graph.get("boundary"),
    }
