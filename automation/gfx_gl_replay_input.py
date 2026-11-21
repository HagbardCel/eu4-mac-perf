"""Restricted, content-addressed replay inputs for an original-API GL control.

A control renderer must reproduce a selected draw from the *inputs* of that
draw.  It must never receive the post-draw reference image it is supposed to
reproduce.  This module derives the mandatory input evidence from a sealed
frame's own event graph and materialises only the authorised pre-draw inputs
into a separate directory:

* colour, depth and stencil pre-draw state (a captured seed, or a provably
  complete clear history);
* the selected draw's type, buffers, attributes and indices;
* the program's shaders and uniform values;
* captured texture contents (the original compressed blocks when present) and
  sampler state;
* the effective render state and the default drawable's properties.

The target checkpoint blob is never copied.  The comparison reference stays a
qualification-side object; the renderer consumes only the package produced
here.

Evidence provenance is separate from package transport.  :func:`derive_evidence`
answers "is every mandatory pre-draw observable proven by this seal?" and
:func:`build_package` only runs after that answer is ``proven``.  A missing
depth/stencil seed with an incomplete clear history, an unobserved drawable, or
a missing sRGB/dither state is an explicit ``missing`` result, never an assumed
default.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EVIDENCE_SCHEMA = "gfx-gl-control-input-evidence/v1"
PACKAGE_SCHEMA = "gfx-gl-replay-input/v1"

GL_COLOR_BUFFER_BIT = 0x0001
GL_DEPTH_BUFFER_BIT = 0x0002
GL_STENCIL_BUFFER_BIT = 0x0004
GL_BACK = 0x0405
GL_FRONT = 0x0404

DEFAULT_RESOURCE = "fb:0"

_UNKNOWN_VALUES = (None, "", "unknown")


class ReplayInputError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def canonical_json(value) -> bytes:
    """The one canonical byte representation used for every digest."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _int_or_none(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _gl_enum(value) -> int | None:
    """Parse a recorded GL enum (``"0x405"``, ``"1029"`` or int)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 0) if text else None
        except ValueError:
            return None
    return None


def _event_by_index(events: list[dict], index: int) -> dict | None:
    return next(
        (event for event in events
         if type(event.get("index")) is int and event.get("index") == index),
        None)


def _event_position(events: list[dict], event: dict) -> int:
    for position, candidate in enumerate(events):
        if candidate is event:
            return position
    return events.index(event)


def _same_target(event: dict, framebuffer: int) -> bool:
    target = event.get("target") or {}
    return target.get("framebuffer") == framebuffer


def _is_writer(event: dict) -> bool:
    """A same-target operation that can change colour/depth/stencil."""
    kind = event.get("kind")
    if kind == "draw":
        return True
    if kind == "clear":
        return True
    if kind == "copy":
        return bool(event.get("writes_target", False))
    return False


def _last_writer(events: list[dict], position: int,
                 framebuffer: int) -> dict | None:
    for event in reversed(events[:position]):
        if _is_writer(event) and _same_target(event, framebuffer):
            return event
    return None


def _box_covers_full_framebuffer(box, width: int, height: int) -> bool:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return False
    try:
        x, y, w, h = (int(item) for item in box)
    except (TypeError, ValueError):
        return False
    return x <= 0 and y <= 0 and w >= width and h >= height


def _clear_state(clear: dict) -> dict | None:
    state = clear.get("state")
    return state if isinstance(state, dict) else None


def _clear_covers(clear: dict, bit: int, width: int, height: int,
                  mask_check) -> tuple[bool, str]:
    """Whether one clear fully initialises the selected buffer."""
    flags = _int_or_none(clear.get("flags"))
    if flags is None or not (flags & bit):
        return False, "clear does not include this buffer"
    target = clear.get("target") or {}
    if _int_or_none(target.get("width")) != width \
            or _int_or_none(target.get("height")) != height:
        return False, "clear target extent differs from the drawable"
    state = _clear_state(clear)
    if state is None:
        return False, "clear coverage/mask state was not recorded"
    scissor = state.get("scissor") or {}
    if scissor.get("enabled"):
        if not _box_covers_full_framebuffer(
                scissor.get("box"), width, height):
            return False, "scissor-clipped clear does not cover the drawable"
    mask_ok, reason = mask_check(state)
    if not mask_ok:
        return False, reason
    return True, ""


def _colour_mask_ok(state: dict) -> tuple[bool, str]:
    mask = state.get("color_mask")
    if mask is not None and list(mask) != [1, 1, 1, 1]:
        return False, "colour write mask is not fully enabled"
    return True, ""


def _depth_mask_ok(state: dict) -> tuple[bool, str]:
    mask = state.get("depth_mask")
    if mask is False:
        return False, "depth write mask is disabled"
    return True, ""


def _stencil_mask_ok(state: dict) -> tuple[bool, str]:
    mask = _int_or_none(state.get("stencil_mask"))
    if mask is not None and mask != -1 and mask != 0xFFFFFFFF:
        return False, "stencil write mask is not fully enabled"
    return True, ""


def _drawable_evidence(trace: dict, width: int, height: int) -> tuple[dict | None, list[str], list[str]]:
    """Derive the default drawable contract from the seal's own record."""
    missing: list[str] = []
    unsupported: list[str] = []
    gl = trace.get("gl") if isinstance(trace.get("gl"), dict) else {}
    record = gl.get("drawable")
    if gl.get("drawable_observed") is not True or not isinstance(record, dict):
        missing.append(
            "default-framebuffer drawable properties (GL_SAMPLES, "
            "GL_SAMPLE_BUFFERS, depth/stencil/colour bit depths) were not "
            "captured for this seal")
        return None, missing, unsupported
    drawable = {
        "samples": _int_or_none(record.get("samples")),
        "sample_buffers": _int_or_none(record.get("sample_buffers")),
        "depth_bits": _int_or_none(record.get("depth_bits")),
        "stencil_bits": _int_or_none(record.get("stencil_bits")),
        "red_bits": _int_or_none(record.get("red_bits")),
        "green_bits": _int_or_none(record.get("green_bits")),
        "blue_bits": _int_or_none(record.get("blue_bits")),
        "alpha_bits": _int_or_none(record.get("alpha_bits")),
        "drawn_framebuffer_binding": _int_or_none(
            record.get("drawn_framebuffer_binding")),
        "double_buffer": record.get("double_buffer"),
        "stereo": record.get("stereo"),
        "width": width,
        "height": height,
        "pixel_format": "BGRA8",
        "origin": "lower_left",
    }
    for field in ("samples", "sample_buffers", "depth_bits", "stencil_bits",
                  "red_bits", "green_bits", "blue_bits", "alpha_bits"):
        if drawable[field] is None:
            missing.append(f"drawable {field} is not recorded")
    if drawable["drawn_framebuffer_binding"] not in (None, 0):
        missing.append(
            "the drawable was observed while a non-default framebuffer was "
            "bound; it does not describe the live window drawable")
    if drawable["stereo"] is True:
        unsupported.append("a stereo default drawable cannot be reproduced")
    if drawable["red_bits"] not in (None, 8) \
            or drawable["green_bits"] not in (None, 8) \
            or drawable["blue_bits"] not in (None, 8) \
            or drawable["alpha_bits"] not in (None, 8):
        unsupported.append(
            "the default drawable colour precision is not RGBA8 "
            f"({drawable['red_bits']}/{drawable['green_bits']}/"
            f"{drawable['blue_bits']}/{drawable['alpha_bits']} bits)")
    return drawable, missing, unsupported


def _checkpoint_seed(draw: dict, draw_event: int, resource: str,
                     width: int, height: int,
                     missing: list[str]) -> dict | None:
    """The pre-draw colour seed captured immediately before the draw."""
    record = draw.get("default_checkpoint")
    if not isinstance(record, dict):
        missing.append(
            f"pre-draw colour seed at draw event {draw_event} was not captured")
        return None
    problems = []
    if record.get("status") != "captured":
        problems.append("checkpoint is not a captured observation")
    if record.get("event") != draw_event:
        problems.append("checkpoint event does not match the selected draw")
    if record.get("timing") != "pre_event":
        problems.append("checkpoint is not the pre-event observation")
    if str(record.get("resource") or "") != resource:
        problems.append("checkpoint resource does not match the draw target")
    if isinstance(record.get("session_id"), str) is False:
        problems.append("checkpoint has no session identity")
    if _int_or_none(record.get("width")) != width \
            or _int_or_none(record.get("height")) != height:
        problems.append("checkpoint extent differs from the drawable")
    digest = record.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        problems.append("checkpoint has no valid content digest")
    if not isinstance(record.get("file"), str) or not record.get("file"):
        problems.append("checkpoint has no blob file")
    if problems:
        missing.extend(
            f"pre-draw colour seed is unproven: {problem}"
            for problem in problems)
        return None
    return {
        "source": "checkpoint",
        "event": draw_event,
        "file": record["file"],
        "sha256": digest,
        "bytes": _int_or_none(record.get("bytes")),
        "width": width,
        "height": height,
        "pixel_format": str(record.get("pixel_format") or "BGRA8"),
        "origin": str(record.get("origin") or "lower_left"),
    }


def _seed_record(record: dict, label: str, width: int, height: int,
                 missing: list[str]) -> dict | None:
    """Validate a captured depth/stencil seed sub-record."""
    problems = []
    if not isinstance(record, dict):
        return None
    digest = record.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        problems.append("no valid content digest")
    if not isinstance(record.get("file"), str) or not record.get("file"):
        problems.append("no blob file")
    if _int_or_none(record.get("width")) != width \
            or _int_or_none(record.get("height")) != height:
        problems.append("extent differs from the drawable")
    if problems:
        missing.extend(
            f"pre-draw {label} seed is unproven: {problem}"
            for problem in problems)
        return None
    return {
        "source": "checkpoint",
        "file": record["file"],
        "sha256": digest,
        "bytes": _int_or_none(record.get("bytes")),
        "format": str(record.get("format") or ""),
        "width": width,
        "height": height,
    }


def _clear_history_value(clear: dict, label: str) -> dict:
    field = "depth" if label == "depth" else "stencil"
    value = clear.get(field)
    return {
        "source": "clear_history",
        "event": clear.get("index"),
        "value": value,
        "coverage": "full",
    }


def _prestate_evidence(draw: dict, events: list[dict], position: int,
                       framebuffer: int, width: int, height: int,
                       *, draw_event: int, resource: str,
                       drawable: dict | None) -> tuple[dict | None, list[str], list[str]]:
    missing: list[str] = []
    unsupported: list[str] = []
    colour = _checkpoint_seed(
        draw, draw_event, resource, width, height, missing)
    state = draw.get("state") if isinstance(draw.get("state"), dict) else {}
    last = _last_writer(events, position, framebuffer)
    checkpoint = draw.get("default_checkpoint")
    checkpoint = checkpoint if isinstance(checkpoint, dict) else {}

    prestate = {"color": colour, "depth": None, "stencil": None,
                "last_writer": {
                    "kind": (last or {}).get("kind"),
                    "index": (last or {}).get("index"),
                }}
    if colour is None:
        # A fully covering colour clear with no later writer is the only
        # admissible non-seeded colour provenance.
        if isinstance(last, dict) and last.get("kind") == "clear":
            ok, reason = _clear_covers(
                last, GL_COLOR_BUFFER_BIT, width, height, _colour_mask_ok)
            if ok:
                prestate["color"] = {
                    "source": "clear_history",
                    "event": last.get("index"),
                    "value": last.get("color"),
                    "coverage": "full",
                }
            else:
                missing.append(
                    f"pre-draw colour is unproven: {reason}")
        else:
            missing.append(
                "pre-draw colour seed and a complete colour clear history "
                "were both unavailable")
    elif not isinstance(last, dict) or last.get("kind") != "clear":
        # The captured seed is authoritative; no history proof is needed for
        # colour because the image itself is the complete pre-draw contents.
        pass

    depth_used = bool((state.get("depth") or {}).get("test")) \
        or bool((state.get("depth") or {}).get("write"))
    stencil_used = bool((state.get("stencil") or {}).get("test"))

    for label, used, bit, masks in (
            ("depth", depth_used, GL_DEPTH_BUFFER_BIT, _depth_mask_ok),
            ("stencil", stencil_used, GL_STENCIL_BUFFER_BIT,
             _stencil_mask_ok)):
        if not used:
            prestate[label] = {"source": "not_required"}
            continue
        seed = _seed_record(checkpoint.get(label), label, width, height, missing)
        if seed is not None:
            prestate[label] = seed
            continue
        if isinstance(checkpoint.get(label), (dict, list, str)):
            # Present but malformed: the missing reason is already recorded.
            continue
        if isinstance(last, dict) and last.get("kind") == "clear":
            ok, reason = _clear_covers(
                last, bit, width, height, masks)
            if ok:
                prestate[label] = _clear_history_value(last, label)
            else:
                missing.append(
                    f"pre-draw {label} contents are unproven: {reason}")
        else:
            missing.append(
                f"pre-draw {label} contents are unproven: the last "
                f"same-target writer is a {((last or {}).get('kind') or 'none')}, "
                "not a fully covering clear, and no captured seed exists")

    # A resolved multisample seed cannot be used as per-sample depth/stencil
    # state: it would silently average the samples the draw tests against.
    samples = _int_or_none((drawable or {}).get("samples"))
    for label in ("depth", "stencil"):
        record = prestate.get(label) or {}
        if samples and record.get("source") == "checkpoint":
            unsupported.append(
                f"a multisampled default drawable ({samples} samples) with a "
                f"resolved {label} seed cannot be reproduced exactly; only a "
                "uniform clear history is provable")
    return prestate, missing, unsupported


def derive_evidence(trace: dict, seal: dict, *, checkpoint_event: int,
                    draw_event: int, resource: str = DEFAULT_RESOURCE,
                    width: int, height: int) -> dict:
    """Derive mandatory pre-draw evidence from the sealed event graph.

    Nothing here trusts caller-supplied JSON.  ``trace`` must be a verified
    frame trace (``m6_frame.load_trace``) and ``seal`` its verified seal.
    """
    events = trace.get("_events") or []
    draw = _event_by_index(events, draw_event)
    checkpoint = _event_by_index(events, checkpoint_event)
    if draw is None or draw.get("kind") != "draw":
        raise ReplayInputError(
            "invalid_draw", f"event {draw_event} is not a draw in the seal")
    if checkpoint is None:
        raise ReplayInputError(
            "invalid_checkpoint", f"event {checkpoint_event} is absent")
    if draw_event >= checkpoint_event:
        raise ReplayInputError(
            "invalid_selection", "the selected draw does not precede the checkpoint")
    expected_fb = _int_or_none(resource.split(":", 1)[1]) \
        if resource.startswith("fb:") else None
    if expected_fb is None:
        raise ReplayInputError("unsupported_resource", resource)
    if (draw.get("target") or {}).get("framebuffer") != expected_fb:
        raise ReplayInputError(
            "wrong_target",
            f"draw event {draw_event} does not target {resource}")
    position = _event_position(events, draw)

    missing: list[str] = []
    unsupported: list[str] = []
    drawable, drawable_missing, drawable_unsupported = _drawable_evidence(
        trace, width, height)
    missing.extend(drawable_missing)
    unsupported.extend(drawable_unsupported)

    target = draw.get("target") if isinstance(draw.get("target"), dict) else {}
    if _int_or_none(target.get("width")) not in (None, width) \
            or _int_or_none(target.get("height")) not in (None, height):
        missing.append(
            "recorded draw target extent differs from the checkpoint extent")
    draw_buffer = _gl_enum(target.get("draw_buffer"))
    if draw_buffer is None:
        missing.append("the draw buffer bound for the selected draw is not recorded")
    elif draw_buffer not in (GL_BACK, GL_FRONT) and drawable \
            and drawable.get("double_buffer"):
        unsupported.append(
            f"the selected draw targets GL draw buffer 0x{draw_buffer:x}, "
            "which is not the live back/front colour buffer")

    state = draw.get("state") if isinstance(draw.get("state"), dict) else {}
    for field, label in (
            ("framebuffer_srgb", "GL_FRAMEBUFFER_SRGB enable state"),
            ("dither", "GL_DITHER enable state"),
            ("multisample", "GL_MULTISAMPLE enable state")):
        if field not in state:
            missing.append(f"{label} was not captured for the selected draw")

    prestate, prestate_missing, prestate_unsupported = _prestate_evidence(
        draw, events, position, expected_fb, width, height,
        draw_event=draw_event, resource=resource, drawable=drawable)
    missing.extend(prestate_missing)
    unsupported.extend(prestate_unsupported)

    # The target checkpoint must not be the source of any input.
    reference_digest = (checkpoint.get("default_checkpoint") or {}).get("sha256") \
        if isinstance(checkpoint.get("default_checkpoint"), dict) else None
    for record in (
            (prestate.get("color") or {}),
            (prestate.get("depth") or {}),
            (prestate.get("stencil") or {})):
        if reference_digest and record.get("sha256") == reference_digest:
            unsupported.append(
                "the pre-draw state names the target checkpoint's own blob; "
                "the reference output cannot be an input")
    colour_record = prestate.get("color") or {}
    if reference_digest and colour_record.get("source") == "checkpoint" \
            and colour_record.get("sha256") == reference_digest:
        unsupported.append(
            "the selected draw has no observable effect: the captured "
            "pre-draw colour seed is byte-identical to the target checkpoint, "
            "so an exact result cannot be distinguished from copying the "
            "reference output")

    status = "proven"
    if unsupported:
        status = "unsupported_state"
    elif missing:
        status = "missing"
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": status,
        "proven": status == "proven",
        "drawable": drawable,
        "prestate": prestate,
        "missing": missing,
        "unsupported": unsupported,
        "identity": {
            "session_id": seal.get("session_id"),
            "capture_frame_id": seal.get("capture_frame_id"),
            "present_index": seal.get("present_index"),
            "event_stream_sha256": seal.get("event_stream_sha256"),
            "checkpoint_event": checkpoint_event,
            "draw_event": draw_event,
            "resource": resource,
            "width": width,
            "height": height,
            "pixel_format": "BGRA8",
            "origin": "lower_left",
        },
        "witness": {
            "draw_target_framebuffer": (draw.get("target") or {}).get("framebuffer"),
            "last_writer": prestate.get("last_writer"),
            "color_checkpoint": {
                "event": ((draw.get("default_checkpoint") or {}).get("event")
                          if isinstance(draw.get("default_checkpoint"), dict)
                          else None),
                "status": ((draw.get("default_checkpoint") or {}).get("status")
                           if isinstance(draw.get("default_checkpoint"), dict)
                           else None),
            },
        },
    }


class _PackageWriter:
    def __init__(self, root: Path, forbidden_digests: set[str]):
        self.root = root
        self.blobs = root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=False)
        self.files: list[dict] = []
        self._by_digest: dict[str, dict] = {}
        self.forbidden = forbidden_digests

    def add(self, source: Path, *, role: str, digest: str | None = None,
            extension: str = "bin") -> dict:
        source = Path(source)
        body = source.read_bytes()
        actual = sha256_bytes(body)
        if digest is not None and digest != actual:
            raise ReplayInputError(
                "blob_digest_mismatch",
                f"{source.name}: declared {digest}, measured {actual}")
        if actual in self.forbidden:
            raise ReplayInputError(
                "reference_output_equals_input",
                f"{source.name} is byte-identical to the target checkpoint's "
                "output; the selected draw has no observable effect, so "
                "copying the reference cannot be disproved")
        existing = self._by_digest.get(actual)
        if existing is not None:
            return existing
        name = f"{actual}.{extension}"
        path = self.blobs / name
        path.write_bytes(body)
        record = {
            "role": role,
            "path": f"blobs/{name}",
            "sha256": actual,
            "bytes": len(body),
        }
        self.files.append(record)
        self._by_digest[actual] = record
        return record


def _record_path(record: dict) -> str:
    return record["path"]


def build_package(*, frame_dir: Path, trace: dict, seal: dict, evidence: dict,
                  checkpoint_event: int, draw_event: int, width: int,
                  height: int, resource: str, out_dir: Path) -> dict:
    """Materialise the verified pre-draw inputs for the control renderer.

    Raises :class:`ReplayInputError` when the evidence is not proven or when
    the target checkpoint blob would enter the package by any route.
    """
    if evidence.get("status") != "proven":
        raise ReplayInputError(
            "unproven_evidence",
            "the input package requires proven drawable and prestate evidence")
    frame_dir = Path(frame_dir).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ReplayInputError(
            "package_dir_not_empty", f"{out_dir} already has contents")
    out_dir.mkdir(parents=True, exist_ok=True)

    events = trace.get("_events") or []
    draw = _event_by_index(events, draw_event)
    checkpoint = _event_by_index(events, checkpoint_event)
    if draw is None or checkpoint is None:
        raise ReplayInputError("invalid_selection", "event selection is missing")
    blob_root = frame_dir / "blobs"
    reference_digest = (checkpoint.get("default_checkpoint") or {}).get("sha256")
    forbidden = {reference_digest} if isinstance(reference_digest, str) else set()
    reference_file = (checkpoint.get("default_checkpoint") or {}).get("file") \
        if isinstance(checkpoint.get("default_checkpoint"), dict) else None

    writer = _PackageWriter(out_dir, forbidden)
    manifest: dict = {
        "schema": PACKAGE_SCHEMA,
        "identity": dict(evidence.get("identity") or {}),
        "drawable": evidence.get("drawable"),
        "prestate": {},
        "draw": {},
        "program": {},
        "attributes": [],
        "indices": None,
        "textures": [],
        "state": draw.get("state") or {},
        "files": writer.files,
    }

    prestate = evidence.get("prestate") or {}
    resolved_prestate: dict = {}
    for label, extension in (("color", "bgra"), ("depth", "f32"),
                             ("stencil", "u8")):
        record = prestate.get(label)
        if not isinstance(record, dict):
            resolved_prestate[label] = record
            continue
        if record.get("source") == "checkpoint":
            source = blob_root / str(record.get("file") or "")
            if not source.is_file():
                raise ReplayInputError(
                    "missing_input_blob",
                    f"pre-draw {label} seed {record.get('file')} is absent")
            copied = writer.add(
                source, role=f"prestate-{label}",
                digest=record.get("sha256"), extension=extension)
            resolved_prestate[label] = {
                **record, "path": copied["path"], "bytes": copied["bytes"],
            }
        else:
            resolved_prestate[label] = dict(record)
    manifest["prestate"] = resolved_prestate

    # The colour seed is mandatory for a full-frame comparison; the evidence
    # derivation refuses it only for a provably complete colour clear.
    if (resolved_prestate.get("color") or {}).get("source") == "checkpoint" \
            and (resolved_prestate["color"].get("path") or "") == "":
        raise ReplayInputError("missing_input_blob", "colour seed was not copied")

    draw_record = {
        "kind": draw.get("kind"),
        "mode": draw.get("draw", {}).get("mode"),
        "first": draw.get("draw", {}).get("first"),
        "count": draw.get("draw", {}).get("count"),
        "instance_count": draw.get("draw", {}).get("instance_count"),
        "indexed": draw.get("draw", {}).get("indexed"),
        "base_vertex": draw.get("draw", {}).get("base_vertex"),
        "caller_rva": draw.get("draw", {}).get("caller_rva"),
    }
    manifest["draw"] = draw_record
    if not draw_record["count"] or draw_record["mode"] is None:
        raise ReplayInputError("incomplete_draw", "draw mode/count is absent")

    program = draw.get("program") if isinstance(draw.get("program"), dict) else {}
    shaders = []
    for shader in program.get("shaders") or []:
        if not isinstance(shader, dict) or not shader.get("file"):
            continue
        extension = "vert" if shader.get("stage") == "vertex" else "frag"
        copied = writer.add(
            blob_root / str(shader["file"]), role=f"shader-{shader.get('stage')}",
            digest=shader.get("sha256"), extension=extension)
        shaders.append({
            "stage": shader.get("stage"),
            "sha256": shader.get("sha256"),
            "path": copied["path"],
            "bytes": copied["bytes"],
        })
    uniforms = []
    for uniform in program.get("uniforms") or []:
        if not isinstance(uniform, dict):
            continue
        entry = {
            "name": uniform.get("name"),
            "type": uniform.get("type"),
            "unit": uniform.get("unit"),
            "components": uniform.get("components"),
            "elements": uniform.get("elements"),
        }
        if uniform.get("file"):
            copied = writer.add(
                blob_root / str(uniform["file"]),
                role="uniform", digest=uniform.get("sha256"))
            entry.update({"path": copied["path"], "sha256": copied["sha256"],
                          "bytes": copied["bytes"]})
        uniforms.append(entry)
    if not shaders:
        raise ReplayInputError(
            "missing_shader", "the selected draw has no captured shader sources")
    manifest["program"] = {
        "name": program.get("name"),
        "shaders": shaders,
        "uniforms": uniforms,
    }

    attributes = []
    for attribute in draw.get("attributes") or []:
        if not isinstance(attribute, dict) or not attribute.get("data"):
            continue
        copied = writer.add(
            blob_root / str(attribute["data"]), role="vertex-buffer",
            digest=attribute.get("sha256"))
        attributes.append({
            "index": attribute.get("index"),
            "name": attribute.get("name"),
            "location": attribute.get("location"),
            "enabled": attribute.get("enabled"),
            "components": attribute.get("components"),
            "gl_type": attribute.get("gl_type"),
            "normalized": attribute.get("normalized"),
            "integer": attribute.get("integer"),
            "stride": attribute.get("stride"),
            "offset": attribute.get("offset"),
            "divisor": attribute.get("divisor"),
            "range_begin": attribute.get("range_begin"),
            "range_bytes": attribute.get("range_bytes"),
            "first_vertex": attribute.get("first_vertex"),
            "data_path": copied["path"],
            "data_sha256": copied["sha256"],
            "data_bytes": copied["bytes"],
        })
    if not attributes:
        raise ReplayInputError(
            "missing_attributes", "the selected draw has no captured vertex data")
    manifest["attributes"] = attributes

    indices = draw.get("indices") if isinstance(draw.get("indices"), dict) else None
    if indices is not None and indices.get("file"):
        copied = writer.add(
            blob_root / str(indices["file"]), role="index-buffer",
            digest=indices.get("sha256"))
        manifest["indices"] = {
            "kind": indices.get("kind"),
            "count": indices.get("count"),
            "first_index": indices.get("first_index"),
            "base_vertex": indices.get("base_vertex"),
            "vertex_origin": indices.get("vertex_origin"),
            "path": copied["path"],
            "sha256": copied["sha256"],
            "bytes": copied["bytes"],
        }
    if draw_record.get("indexed") and manifest["indices"] is None:
        raise ReplayInputError(
            "missing_indices", "the selected indexed draw has no captured indices")

    textures = []
    for texture in draw.get("textures") or []:
        if not isinstance(texture, dict):
            continue
        levels = []
        for level in texture.get("levels") or []:
            if not isinstance(level, dict):
                continue
            compressed = level.get("compressed") \
                if isinstance(level.get("compressed"), dict) else None
            source = compressed if compressed and compressed.get("file") \
                else level
            if not source.get("file"):
                continue
            extension = "dxt" if source is compressed else "tex"
            copied = writer.add(
                blob_root / str(source["file"]),
                role="texture-compressed" if source is compressed else "texture",
                digest=source.get("sha256"), extension=extension)
            levels.append({
                "level": level.get("level"),
                "width": level.get("width"),
                "height": level.get("height"),
                "internal_format": level.get("internal_format"),
                "pixel_format": level.get("pixel_format"),
                "pixel_type": level.get("pixel_type"),
                "row_stride": level.get("row_stride"),
                "compressed": bool(source is compressed),
                "path": copied["path"],
                "sha256": copied["sha256"],
                "bytes": copied["bytes"],
            })
        textures.append({
            "unit": texture.get("unit"),
            "sampler_name": texture.get("sampler_name"),
            "target": texture.get("target"),
            "name": texture.get("name"),
            "width": texture.get("width"),
            "height": texture.get("height"),
            "internal_format": texture.get("internal_format"),
            "sampler": texture.get("sampler") or {},
            "levels": levels,
        })
    manifest["textures"] = textures

    # No file may be the target checkpoint's output blob under another name.
    for record in writer.files:
        if reference_file and Path(record["path"]).name == reference_file:
            raise ReplayInputError(
                "reference_leak", "the target checkpoint blob entered the package")

    # Guard against a caller handing us a directory that already has the
    # sealed reference in it; the package is a fresh, closed set.
    manifest["files"] = writer.files
    digest = sha256_bytes(canonical_json(manifest))
    manifest["package_sha256"] = digest
    (out_dir / "replay-input.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "schema": PACKAGE_SCHEMA,
        "path": str(out_dir),
        "manifest": str(out_dir / "replay-input.json"),
        "package_sha256": digest,
        "file_count": len(writer.files),
        "total_bytes": sum(record["bytes"] for record in writer.files),
    }


def verify_package(package_dir: Path | str) -> dict:
    """Re-verify a package from disk; never trust its transported manifest."""
    package_dir = Path(package_dir).expanduser().resolve()
    try:
        manifest = json.loads(
            (package_dir / "replay-input.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayInputError("unreadable_package", str(exc)) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != PACKAGE_SCHEMA:
        raise ReplayInputError("invalid_package", "unexpected package schema")
    declared = manifest.get("package_sha256")
    body = dict(manifest)
    body.pop("package_sha256", None)
    actual = sha256_bytes(canonical_json(body))
    if declared != actual:
        raise ReplayInputError(
            "package_digest_mismatch",
            f"declared {declared}, recomputed {actual}")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ReplayInputError("invalid_package", "no files are declared")
    seen = set()
    for record in files:
        if not isinstance(record, dict):
            raise ReplayInputError("invalid_package", "file record is not an object")
        relative = str(record.get("path") or "")
        path = (package_dir / relative).resolve()
        try:
            path.relative_to(package_dir)
        except ValueError as exc:
            raise ReplayInputError(
                "invalid_package", f"file escapes the package: {relative}") from exc
        if not path.is_file():
            raise ReplayInputError("missing_package_blob", relative)
        measured = _sha256_file(path)
        if measured != record.get("sha256"):
            raise ReplayInputError(
                "package_blob_digest_mismatch", relative)
        if path.stat().st_size != record.get("bytes"):
            raise ReplayInputError("package_blob_size_mismatch", relative)
        if relative in seen:
            raise ReplayInputError("duplicate_package_record", relative)
        seen.add(relative)
    return {
        "schema": PACKAGE_SCHEMA,
        "path": str(package_dir),
        "manifest": str(package_dir / "replay-input.json"),
        "package_sha256": actual,
        "file_count": len(files),
        "total_bytes": sum(int(record.get("bytes") or 0) for record in files),
        "manifest_document": manifest,
    }
