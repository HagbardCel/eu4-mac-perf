"""Qualify an original-API GL control before it can blame Metal.

A one-draw GL control is only admissible causal evidence when it reproduces
live GL exactly on all four BGRA channels **and** the control's production is
provably tied to the same sealed observation. Those are different questions:

* :func:`compare` answers only whether two BGRA images are byte-identical.
* :func:`qualify_sealed` authenticates the sealed frame, event stream,
  checkpoint and draw; executes the declared control renderer with a
  challenge nonce; validates the renderer's receipt and artifact digest; and
  only then admits the comparison.
* :func:`attribution` is the only permitted Metal-blame projection. It
  re-checks the recorded receipt and artifact instead of trusting a boolean.

The qualifier never accepts a hand-written control image plus manifest as
proof of execution. The renderer is executed by this process, its executable
digest is recorded, and its receipt must echo the challenge. The trust
boundary is explicit: a renderer that lies about its own GL calls is outside
what a receipt chain can prove, but a copied live image cannot qualify without
running the declared renderer and producing the matching receipt.

This module is deliberately independent of the renderer implementation. It
reports exact statistics, keeps locators for both images, and names the missing
observation when the control is not qualified.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from automation import m6_compare

SCHEMA = "gfx-gl-control-qualification/v3"
COMPARISON_SCHEMA = "gfx-gl-control-comparison/v1"
AUTHENTICATION_SCHEMA = "gfx-gl-control-authentication/v2"
RECEIPT_SCHEMA = "gfx-gl-control-receipt/v1"
REQUEST_SCHEMA = "gfx-gl-control-request/v1"
DEFAULT_RESOURCE = "fb:0"
ORIGINAL_API = "opengl"
DEFAULT_TIMEOUT = 300.0


class QualificationError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sha_or_none(path: Path) -> str | None:
    try:
        return _sha(path)
    except OSError:
        return None


def _int_or_none(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


def _intervening_writer(events: list[dict], draw_event: int,
                        checkpoint_event: int, framebuffer: int) -> dict | None:
    """Return a same-target draw/clear between the pair, if one exists."""
    for event in events:
        index = event.get("index")
        if type(index) is not int or not (draw_event < index < checkpoint_event):
            continue
        if event.get("kind") not in ("draw", "clear"):
            continue
        target = event.get("target") or {}
        if target.get("framebuffer") == framebuffer:
            return event
    return None


def _load_authority(path: Path | str | None, renderer_sha: str) -> dict:
    """Load the operator-pinned renderer authority.

    Without a pinned authority the renderer is self-attested and cannot
    authorize Metal blame, however exact its pixels are. The authority is an
    explicit operator trust decision, not a self-declaration by the renderer.
    """
    if path is None:
        return {"qualified": False, "self_attested": True,
                "reason": ("no pinned renderer authority was supplied; the "
                           "renderer is self-attested")}
    try:
        value = _read_json(Path(path), "renderer_authority")
    except QualificationError as exc:
        return {"qualified": False, "self_attested": True,
                "reason": exc.detail or str(exc)}
    reasons = []
    if value.get("schema") != "gfx-gl-control-renderer-authority/v1":
        reasons.append("renderer authority has an unexpected schema")
    if value.get("qualified") is not True:
        reasons.append("renderer authority is not qualified")
    if value.get("renderer_sha256") != renderer_sha:
        reasons.append("renderer authority digest does not match the executed renderer")
    if str(value.get("api") or "").lower() != ORIGINAL_API:
        reasons.append("renderer authority does not name the original OpenGL API")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        reasons.append("renderer authority has no renderer name")
    return {
        "qualified": not reasons,
        "self_attested": False,
        "path": str(Path(path)),
        "sha256": _sha_or_none(Path(path)),
        "name": value.get("name"),
        "renderer_sha256": value.get("renderer_sha256"),
        "qualified_by": value.get("qualified_by"),
        "reasons": reasons,
    }


def _read_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QualificationError(
            "unreadable_" + label, f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise QualificationError(
            "invalid_" + label, f"{path}: expected a JSON object")
    return value


def _missing(drawable: dict | None, prestate: dict | None) -> list[str]:
    missing: list[str] = []
    if not isinstance(drawable, dict) or not drawable.get("observed"):
        missing.append(
            "default-framebuffer drawable properties (GL_SAMPLES, "
            "GL_SAMPLE_BUFFERS, depth/stencil/color bit depths) were not "
            "captured for this seal")
    else:
        for field in ("samples", "sample_buffers", "depth_bits",
                      "stencil_bits", "red_bits", "green_bits", "blue_bits",
                      "alpha_bits"):
            if drawable.get(field) is None:
                missing.append(f"drawable {field} is not recorded")
    if not isinstance(prestate, dict) or not prestate:
        missing.append(
            "authentic pre-draw depth/stencil/color prestate is not declared")
    else:
        for field in ("depth_clear", "stencil_clear", "color"):
            if field not in prestate:
                missing.append(f"pre-draw {field} is not declared")
    return missing


def compare_payloads(live: bytes, control: bytes, width: int, height: int,
                     *, live_path: Path | None = None,
                     control_path: Path | None = None) -> dict:
    """Compare already-loaded BGRA payloads without re-reading a path."""
    if width <= 0 or height <= 0:
        raise QualificationError("invalid_extent", f"{width}x{height}")
    expected = width * height * 4
    if len(live) != expected or len(control) != expected:
        raise QualificationError(
            "invalid_payload",
            f"expected {expected} BGRA bytes, live={len(live)} "
            f"control={len(control)}")
    statistics = m6_compare.compare_images(live, control, width, height)
    exact = bool(
        statistics["mismatched_pixels"] == 0
        and statistics["max_channel_error"] == 0
        and statistics["alpha_different_pixels"] == 0)
    return {
        "schema": COMPARISON_SCHEMA,
        "status": "exact" if exact else "different",
        "exact": exact,
        "width": width,
        "height": height,
        "live": {"path": str(live_path or ""),
                 "sha256": hashlib.sha256(live).hexdigest()},
        "control": {"path": str(control_path or ""),
                    "sha256": hashlib.sha256(control).hexdigest()},
        "statistics": statistics,
    }


def compare(live_path: Path | str, control_path: Path | str,
            width: int, height: int) -> dict:
    """Compare two BGRA images exactly; no provenance decision is made here."""
    live_path = Path(live_path)
    control_path = Path(control_path)
    try:
        live = live_path.read_bytes()
    except OSError as exc:
        raise QualificationError("unreadable_live", f"{live_path}: {exc}") from exc
    try:
        control = control_path.read_bytes()
    except OSError as exc:
        raise QualificationError(
            "unreadable_control", f"{control_path}: {exc}") from exc
    return compare_payloads(
        live, control, width, height, live_path=live_path,
        control_path=control_path)


def _event(events: list[dict], index: int) -> dict | None:
    return next(
        (event for event in events
         if type(event.get("index")) is int and event.get("index") == index),
        None)


def _authenticate(frame_dir: Path, live_path: Path, width: int, height: int,
                  *, checkpoint_event: int, draw_event: int,
                  resource: str) -> dict:
    """Authenticate the sealed observation and the selected live image.

    The authoritative checkpoint is the seal-verified event stream's own
    ``default_checkpoint`` record, not a mutable request sidecar. The live
    image must be the exact sealed blob at that path and digest.
    """
    from automation import m6_frame

    frame_dir = Path(frame_dir).expanduser().resolve()
    live_path = Path(live_path).expanduser().resolve()

    reasons: list[str] = []
    missing: list[str] = []
    identity: dict = {}
    seal = None
    trace = None

    def fail(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    try:
        trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    except Exception as exc:  # noqa: BLE001 - explicit unqualified
        fail(f"sealed event stream or referenced blobs are unreadable: {exc}")
        trace = None
    if trace is not None:
        try:
            seal = m6_frame.load_seal(
                frame_dir, trace=trace, verify_counts=False, verify_blobs=True)
        except Exception as exc:  # noqa: BLE001 - explicit unqualified
            fail(f"frame seal is not authentic: {exc}")
        identity["blobs_verified"] = trace.get("_blob_count_verified")
    if seal is not None:
        for field in ("session_id", "capture_frame_id", "present_index",
                      "event_stream_sha256", "reference_sha256"):
            identity[field] = seal.get(field)
        seal_width = _int_or_none(seal.get("width"))
        seal_height = _int_or_none(seal.get("height"))
        if seal_width is None or seal_height is None:
            fail("frame seal extent is not integral")
        elif seal_width != width or seal_height != height:
            fail(
                f"comparison extent {width}x{height} does not match the seal "
                f"{seal.get('width')}x{seal.get('height')}")
    if trace is not None:
        events = trace.get("_events") or []
        checkpoint = _event(events, checkpoint_event)
        draw = _event(events, draw_event)
        if checkpoint is None:
            fail(
                f"checkpoint event {checkpoint_event} is absent from the "
                "sealed event stream")
        else:
            record = checkpoint.get("default_checkpoint")
            if not isinstance(record, dict):
                fail(
                    f"checkpoint event {checkpoint_event} has no sealed "
                    "default_checkpoint record")
            else:
                if record.get("status") != "captured":
                    fail("checkpoint record is not a captured observation")
                if record.get("event") != checkpoint_event:
                    fail("checkpoint record event does not match the selection")
                if record.get("timing") != "pre_event":
                    fail("checkpoint record is not the pre-event observation")
                if str(record.get("resource") or "") != resource:
                    fail(
                        f"checkpoint record resource {record.get('resource')!r} "
                        f"is not {resource}")
                if str(record.get("target") or "") != resource.split(":", 1)[-1]:
                    fail("checkpoint record target does not match the resource")
                reference_sha = record.get("sha256")
                reference_file = record.get("file")
                if not _is_sha256(reference_sha):
                    fail("checkpoint record has no valid reference sha256")
                if not isinstance(reference_file, str) or not reference_file:
                    fail("checkpoint record has no reference file")
                if isinstance(reference_file, str) and reference_file:
                    expected_path = (frame_dir / "blobs" / reference_file).resolve()
                    if live_path != expected_path:
                        fail(
                            "live image is not the sealed checkpoint blob "
                            f"{reference_file}")
                    live_sha = _sha_or_none(live_path)
                    if live_sha is None:
                        fail(f"live image is unreadable: {live_path}")
                    elif _is_sha256(reference_sha) and live_sha != reference_sha:
                        fail(
                            "live image digest is not the sealed checkpoint "
                            "reference (swapped or different seal)")
                record_width = _int_or_none(record.get("width"))
                record_height = _int_or_none(record.get("height"))
                if record_width is None or record_height is None:
                    fail("checkpoint record extent is not integral")
                elif record_width != width or record_height != height:
                    fail("checkpoint record extent does not match the comparison")
                if seal is not None:
                    if record.get("session_id") not in (None, seal.get("session_id")):
                        fail("checkpoint record names a different session")
                    if record.get("capture_frame_id") not in (
                            None, seal.get("capture_frame_id")):
                        fail("checkpoint record names a different capture frame")
                identity["checkpoint_event"] = checkpoint_event
                identity["checkpoint_resource"] = resource
                identity["checkpoint_reference_file"] = reference_file
                identity["checkpoint_reference_sha256"] = reference_sha
        if draw is None or draw.get("kind") != "draw":
            fail(f"draw event {draw_event} is not a draw in the seal")
        else:
            framebuffer = (draw.get("target") or {}).get("framebuffer")
            expected = None
            if resource.startswith("fb:"):
                try:
                    expected = int(resource.split(":", 1)[1])
                except ValueError:
                    expected = None
            if expected is None:
                fail(f"unsupported resource identity {resource!r}")
            elif framebuffer != expected:
                fail(
                    f"draw event {draw_event} targets framebuffer "
                    f"{framebuffer!r}, not {resource}")
        if draw_event >= checkpoint_event:
            fail("selected draw does not precede the checkpoint")
        expected_fb = None
        if resource.startswith("fb:"):
            expected_fb = _int_or_none(resource.split(":", 1)[1])
        if expected_fb is not None:
            intervening = _intervening_writer(
                events, draw_event, checkpoint_event, expected_fb)
            if intervening is not None:
                fail(
                    f"{intervening.get('kind')} event {intervening.get('index')} "
                    "targets the same framebuffer between the draw and the "
                    "checkpoint")
        identity["draw_event"] = draw_event
        identity["resource"] = resource

    if identity.get("checkpoint_event") is None:
        missing.append(
            "sealed checkpoint identity (event, resource, reference digest) "
            "was not established")
    if identity.get("draw_event") is None:
        missing.append("selected draw identity was not established")

    return {
        "schema": AUTHENTICATION_SCHEMA,
        "status": "authenticated" if not reasons else "unauthenticated",
        "authenticated": not reasons,
        "reasons": reasons,
        "missing": missing,
        "identity": identity,
        "frame_dir": str(frame_dir),
        "seal": ({"path": str(frame_dir / "frame-seal.json"),
                  "sha256": _sha_or_none(frame_dir / "frame-seal.json")}
                 if seal is not None else None),
    }


def _validate_receipt(receipt: dict, *, nonce: str, renderer_sha: str,
                      identity: dict, width: int, height: int,
                      artifact: Path) -> list[str]:
    reasons: list[str] = []

    def fail(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if receipt.get("schema") != RECEIPT_SCHEMA:
        fail(
            f"control receipt schema is {receipt.get('schema')!r}, expected "
            f"{RECEIPT_SCHEMA!r}")
    if receipt.get("nonce") != nonce:
        fail("control receipt did not echo the execution challenge")
    if receipt.get("renderer_sha256") != renderer_sha:
        fail("control receipt renderer digest does not match the executed file")
    if str(receipt.get("api") or "").lower() != ORIGINAL_API:
        fail(
            "control was not rendered through the original OpenGL API "
            f"(api={receipt.get('api')!r})")
    if receipt.get("execution") != "completed":
        fail(
            "control execution is not declared complete "
            f"(execution={receipt.get('execution')!r})")
    frame = receipt.get("frame")
    if not isinstance(frame, dict):
        fail("control receipt has no frame identity")
    else:
        for field in ("session_id", "capture_frame_id", "present_index"):
            if frame.get(field) != identity.get(field):
                fail(f"control receipt frame {field} does not match the seal")
        if frame.get("event_stream_sha256") != identity.get("event_stream_sha256"):
            fail("control receipt event stream does not match the seal")
    checkpoint = receipt.get("checkpoint")
    if not isinstance(checkpoint, dict):
        fail("control receipt has no checkpoint identity")
    else:
        if checkpoint.get("event") != identity.get("checkpoint_event"):
            fail("control receipt checkpoint event does not match")
        if str(checkpoint.get("resource") or "") != str(
                identity.get("checkpoint_resource")):
            fail("control receipt checkpoint resource does not match")
        if checkpoint.get("reference_sha256") != identity.get(
                "checkpoint_reference_sha256"):
            fail("control receipt checkpoint reference digest does not match")
    draw = receipt.get("draw")
    if not isinstance(draw, dict) or draw.get("event") != identity.get("draw_event"):
        fail("control receipt draw event does not match")
    artifact_record = receipt.get("artifact")
    if not isinstance(artifact_record, dict):
        fail("control receipt has no artifact record")
    else:
        if artifact_record.get("sha256") != _sha_or_none(artifact):
            fail("control artifact digest does not match the receipt")
        if artifact_record.get("file") not in (None, artifact.name):
            fail("control artifact path does not match the receipt")
        artifact_width = _int_or_none(artifact_record.get("width"))
        artifact_height = _int_or_none(artifact_record.get("height"))
        if artifact_width is None or artifact_height is None:
            fail("control artifact extent is not integral")
        elif artifact_width != width or artifact_height != height:
            fail("control artifact extent does not match the comparison")
        if str(artifact_record.get("pixel_format") or "").upper() != "BGRA8":
            fail("control artifact pixel format is not BGRA8")
        try:
            actual_bytes = artifact.stat().st_size
        except OSError:
            actual_bytes = None
        if actual_bytes is None:
            fail("control artifact is unreadable")
        elif actual_bytes != width * height * 4:
            fail(
                f"control artifact has {actual_bytes} bytes, expected "
                f"{width * height * 4}")
    if not isinstance(receipt.get("renderer"), dict) \
            or not receipt.get("renderer"):
        fail("control receipt has no original-API renderer identity")
    return reasons


def _run_renderer(*, renderer: Path, frame_dir: Path, live_input: Path,
                  identity: dict, width: int, height: int,
                  out_dir: Path, timeout: float) -> dict:
    """Execute the declared renderer and validate its receipt.

    The renderer reads a copy of the authenticated live bytes, never the
    sealed blob, so it cannot change the image the qualifier later compares.
    """
    renderer = Path(renderer).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    result = {
        "artifact": None,
        "receipt": None,
        "reasons": [],
        "nonce": None,
        "renderer_sha256": None,
        "request_sha256": None,
    }

    def fail(reason: str) -> dict:
        result["reasons"].append(reason)
        return result

    if not renderer.is_file():
        return fail(f"control renderer is not a file: {renderer}")
    renderer_sha = _sha_or_none(renderer)
    if renderer_sha is None:
        return fail(f"control renderer is unreadable: {renderer}")
    result["renderer_sha256"] = renderer_sha
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if (out_dir / "result.bgra").exists() \
                or (out_dir / "receipt.json").exists():
            return fail(f"control output directory already has a result: {out_dir}")
        nonce = secrets.token_hex(16)
        result["nonce"] = nonce
        request = {
            "schema": REQUEST_SCHEMA,
            "nonce": nonce,
            "frame_dir": str(frame_dir),
            "live_image": str(live_input),
            "event_stream_sha256": identity.get("event_stream_sha256"),
            "session_id": identity.get("session_id"),
            "capture_frame_id": identity.get("capture_frame_id"),
            "present_index": identity.get("present_index"),
            "checkpoint": {
                "event": identity.get("checkpoint_event"),
                "resource": identity.get("checkpoint_resource"),
                "reference_file": identity.get("checkpoint_reference_file"),
                "reference_sha256": identity.get("checkpoint_reference_sha256"),
            },
            "draw": {"event": identity.get("draw_event")},
            "width": width,
            "height": height,
            "out": str(out_dir),
        }
        request_path = out_dir / "request.json"
        request_path.write_text(json.dumps(request, indent=2) + "\n",
                                encoding="utf-8")
        result["request_sha256"] = _sha_or_none(request_path)
        if renderer.suffix == ".py":
            command = [sys.executable, str(renderer)]
        else:
            command = [str(renderer)]
        command.extend(["--request", str(request_path), "--out", str(out_dir)])
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            check=False)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return fail(
                f"control renderer failed with exit {completed.returncode}"
                + (f": {detail[-500:]}" if detail else ""))
        artifact = out_dir / "result.bgra"
        receipt_path = out_dir / "receipt.json"
        if not artifact.is_file() or not receipt_path.is_file():
            return fail(
                "control renderer did not produce result.bgra and receipt.json")
        receipt = _read_json(receipt_path, "control_receipt")
        reasons = _validate_receipt(
            receipt, nonce=nonce, renderer_sha=renderer_sha,
            identity=identity, width=width, height=height,
            artifact=artifact)
        result["artifact"] = artifact
        result["receipt"] = receipt
        result["reasons"].extend(reasons)
        return result
    except subprocess.TimeoutExpired:
        return fail(f"control renderer timed out after {timeout}s")
    except (OSError, ValueError, QualificationError) as exc:
        return fail(f"control renderer could not be authenticated: {exc}")


def _assemble(comparison: dict | None, authentication: dict | None, *,
              run: dict | None = None, live_sha256: str | None = None,
              authority: dict | None = None,
              drawable: dict | None = None,
              prestate: dict | None = None) -> dict:
    authenticated = bool(
        isinstance(authentication, dict)
        and authentication.get("authenticated") is True
        and authentication.get("status") == "authenticated")
    reasons = list((authentication or {}).get("reasons") or [])
    reasons.extend((run or {}).get("reasons") or [])
    authority = authority or {"qualified": False, "self_attested": True,
                             "reason": "no renderer authority was supplied",
                             "reasons": ["no renderer authority was supplied"]}
    reasons.extend(authority.get("reasons") or [])
    missing = list((authentication or {}).get("missing") or [])
    identity = (authentication or {}).get("identity") or {}
    if not isinstance(authentication, dict) or not authentication:
        missing.insert(
            0,
            "authenticated frame/stream/resource/checkpoint identity was not "
            "supplied")
    if comparison is None:
        missing.insert(0, "no authenticated control image was produced")
    elif not comparison["exact"]:
        missing.extend(_missing(drawable, prestate))
    if not authority.get("qualified"):
        missing.insert(
            0,
            "control renderer is self-attested; a pinned qualified renderer "
            "authority is required for Metal attribution")
    live_bound = bool(
        comparison is not None
        and live_sha256 is not None
        and live_sha256 == identity.get("checkpoint_reference_sha256")
        and comparison["live"].get("sha256") == live_sha256)
    if comparison is not None and not live_bound:
        reasons.append(
            "the compared live image is not the authenticated checkpoint bytes")
    qualified = bool(
        comparison is not None and comparison["exact"] and authenticated
        and authority.get("qualified") and live_bound
        and not reasons and not missing)
    if not qualified and not missing:
        missing.append(
            "reduce the residual by varying one captured input at a time")
    statistics = comparison["statistics"] if comparison is not None else None
    control_path = (run or {}).get("artifact")
    return {
        "schema": SCHEMA,
        "status": "qualified" if qualified else "unqualified",
        "qualified": qualified,
        "width": (comparison or {}).get("width"),
        "height": (comparison or {}).get("height"),
        "comparison": comparison or {
            "schema": COMPARISON_SCHEMA,
            "status": "unavailable",
            "exact": False,
            "statistics": None,
        },
        "qualification": {
            "status": "qualified" if qualified else "unqualified",
            "qualified": qualified,
            "authenticated": authenticated,
            "reasons": reasons,
            "identity": identity,
            "frame_dir": (authentication or {}).get("frame_dir"),
            "seal": (authentication or {}).get("seal"),
            "receipt": ({"path": str(control_path.parent / "receipt.json"),
                         "sha256": _sha_or_none(
                             control_path.parent / "receipt.json")}
                        if control_path is not None else None),
            "control": ({"path": str(control_path),
                         "sha256": _sha_or_none(control_path)}
                        if control_path is not None else None),
            "renderer": (run or {}).get("receipt", {}).get("renderer")
                        if run and run.get("receipt") else None,
            "renderer_sha256": (run or {}).get("renderer_sha256"),
            "nonce": (run or {}).get("nonce"),
            "request_sha256": (run or {}).get("request_sha256"),
            "renderer_authority": authority,
            "live_sha256": live_sha256,
            "checkpoint_reference_sha256": identity.get(
                "checkpoint_reference_sha256"),
            "blobs_verified": identity.get("blobs_verified"),
        },
        "statistics": statistics,
        "live": (comparison or {}).get("live"),
        "control": (comparison or {}).get("control"),
        "missing_observation": missing,
        "next_discriminator": (
            None if qualified else (missing[0] if missing else None)),
        "metal_blame_allowed": qualified,
    }


def qualify_sealed(live_path: Path | str, width: int, height: int, *,
                   frame_dir: Path | str, checkpoint_event: int,
                   draw_event: int, renderer: Path | str,
                   out_dir: Path | str, resource: str = DEFAULT_RESOURCE,
                   timeout: float = DEFAULT_TIMEOUT,
                   renderer_authority: Path | str | None = None,
                   drawable: dict | None = None,
                   prestate: dict | None = None) -> dict:
    """Authenticate the seal, execute the renderer, then qualify the control.

    The live bytes are read once, copied to the renderer's work directory, and
    compared directly. The sealed blob is never handed to the renderer, so a
    renderer cannot change the image that is being qualified.
    """
    live_path = Path(live_path)
    frame_dir = Path(frame_dir)
    out_dir = Path(out_dir)
    authentication = _authenticate(
        frame_dir, live_path, width, height,
        checkpoint_event=checkpoint_event, draw_event=draw_event,
        resource=resource)
    if not authentication["authenticated"]:
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    try:
        live_bytes = live_path.read_bytes()
    except OSError as exc:
        authentication["reasons"].append(f"live image is unreadable: {exc}")
        authentication["authenticated"] = False
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    live_sha256 = hashlib.sha256(live_bytes).hexdigest()
    if live_sha256 != authentication["identity"].get(
            "checkpoint_reference_sha256"):
        authentication["reasons"].append(
            "live image changed after authentication")
        authentication["authenticated"] = False
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    expected = width * height * 4
    if len(live_bytes) != expected:
        authentication["reasons"].append(
            f"live image has {len(live_bytes)} bytes, expected {expected}")
        authentication["authenticated"] = False
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        authentication["reasons"].append(
            f"control output directory is unusable: {exc}")
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    live_input = out_dir / "live-authenticated.bgra"
    try:
        live_input.write_bytes(live_bytes)
    except OSError as exc:
        authentication["reasons"].append(
            f"authenticated live copy could not be written: {exc}")
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    run = _run_renderer(
        renderer=Path(renderer), frame_dir=frame_dir, live_input=live_input,
        identity=authentication["identity"], width=width, height=height,
        out_dir=out_dir, timeout=timeout)
    authority = _load_authority(
        renderer_authority, run.get("renderer_sha256") or "")
    control_path = run.get("artifact")
    comparison = None
    if control_path is not None:
        try:
            control_bytes = control_path.read_bytes()
        except OSError as exc:
            run["reasons"].append(f"control artifact is unreadable: {exc}")
        else:
            if len(control_bytes) != expected:
                run["reasons"].append(
                    f"control artifact has {len(control_bytes)} bytes, "
                    f"expected {expected}")
            else:
                comparison = compare_payloads(
                    live_bytes, control_bytes, width, height,
                    live_path=live_path, control_path=control_path)
        if _sha_or_none(live_path) != live_sha256:
            run["reasons"].append(
                "sealed live blob changed during the renderer run")
    return _assemble(
        comparison, authentication, run=run, live_sha256=live_sha256,
        authority=authority, drawable=drawable, prestate=prestate)


def _evidence_recheck(qualification: dict) -> bool:
    checked = qualification.get("qualification") or {}
    identity = checked.get("identity") or {}
    comparison = qualification.get("comparison") or {}
    authority = checked.get("renderer_authority") or {}
    if not isinstance(authority, dict) or authority.get("qualified") is not True:
        return False
    receipt_ref = checked.get("receipt") or {}
    control_ref = checked.get("control") or {}
    receipt_path = Path(str(receipt_ref.get("path") or ""))
    control_path = Path(str(control_ref.get("path") or ""))
    if not receipt_path.is_file() or not control_path.is_file():
        return False
    if _sha_or_none(receipt_path) != receipt_ref.get("sha256"):
        return False
    control_sha = _sha_or_none(control_path)
    if control_sha != control_ref.get("sha256"):
        return False
    if control_sha != (comparison.get("control") or {}).get("sha256"):
        return False
    if checked.get("live_sha256") != identity.get("checkpoint_reference_sha256"):
        return False
    if (comparison.get("live") or {}).get("sha256") != checked.get(
            "live_sha256"):
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    if receipt.get("nonce") != checked.get("nonce"):
        return False
    if receipt.get("renderer_sha256") != checked.get("renderer_sha256"):
        return False
    if authority.get("renderer_sha256") != checked.get("renderer_sha256"):
        return False
    frame = receipt.get("frame") or {}
    if frame.get("session_id") != identity.get("session_id") \
            or frame.get("capture_frame_id") != identity.get("capture_frame_id") \
            or frame.get("event_stream_sha256") != identity.get(
                "event_stream_sha256"):
        return False
    checkpoint = receipt.get("checkpoint") or {}
    if checkpoint.get("event") != identity.get("checkpoint_event") \
            or checkpoint.get("reference_sha256") != identity.get(
                "checkpoint_reference_sha256"):
        return False
    artifact = receipt.get("artifact")
    return bool(
        isinstance(artifact, dict)
        and artifact.get("sha256") == control_sha
        and receipt.get("execution") == "completed"
        and str(receipt.get("api") or "").lower() == ORIGINAL_API)


def attribution(qualification: dict) -> dict:
    """Project qualification into the only permitted blame decision."""
    if not isinstance(qualification, dict):
        raise QualificationError("invalid_qualification")
    comparison = qualification.get("comparison")
    checked = qualification.get("qualification")
    allowed = bool(
        qualification.get("status") == "qualified"
        and qualification.get("qualified") is True
        and qualification.get("metal_blame_allowed") is True
        and isinstance(comparison, dict)
        and comparison.get("exact") is True
        and isinstance(checked, dict)
        and checked.get("authenticated") is True
        and _evidence_recheck(qualification))
    return {
        "schema": "gfx-metal-blame/v1",
        "allowed": allowed,
        "reason": (
            "the authenticated original-API control reproduces live GL exactly"
            if allowed else
            "the control is not an authenticated exact reproduction; do not "
            "attribute the residual to Metal"),
        "qualification": qualification,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare and authenticate an original-API GL control "
                    "before any Metal blame.")
    parser.add_argument("live")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frame-dir", type=Path, default=None)
    parser.add_argument("--checkpoint-event", type=int, default=None)
    parser.add_argument("--draw-event", type=int, default=None)
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    parser.add_argument("--renderer", type=Path, default=None,
                        help="executable that renders the control and writes "
                             "result.bgra plus receipt.json")
    parser.add_argument("--control-out", type=Path, default=None)
    parser.add_argument("--renderer-authority", type=Path, default=None,
                        help="operator-pinned renderer authority JSON; without "
                             "it the renderer is self-attested and cannot blame")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--drawable", help="JSON drawable record")
    parser.add_argument("--prestate", help="JSON pre-draw state record")
    parser.add_argument("--comparison-only", nargs=2,
                        metavar=("LIVE", "CONTROL"),
                        help="report pixel equality only; never qualifies")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.comparison_only:
        value = compare(
            args.comparison_only[0], args.comparison_only[1],
            args.width, args.height)
        code = 0 if value["exact"] else 1
    elif args.frame_dir is not None and args.renderer is not None \
            and args.checkpoint_event is not None \
            and args.draw_event is not None:
        drawable = (json.loads(Path(args.drawable).read_text())
                    if args.drawable else None)
        prestate = (json.loads(Path(args.prestate).read_text())
                    if args.prestate else None)
        out_dir = args.control_out
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="gl-control-"))
        value = qualify_sealed(
            args.live, args.width, args.height, frame_dir=args.frame_dir,
            checkpoint_event=args.checkpoint_event,
            draw_event=args.draw_event, renderer=args.renderer,
            out_dir=out_dir, resource=args.resource, timeout=args.timeout,
            renderer_authority=args.renderer_authority,
            drawable=drawable, prestate=prestate)
        code = 0 if value["qualified"] else 1
    else:
        value = {
            "schema": SCHEMA,
            "status": "refused",
            "qualified": False,
            "metal_blame_allowed": False,
            "missing_observation": [
                "qualification requires --frame-dir, --checkpoint-event, "
                "--draw-event, --renderer and --control-out; pixel equality "
                "alone is not a control"],
        }
        code = 2
    text = json.dumps(value, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
