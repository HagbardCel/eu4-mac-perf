"""Qualify an original-API GL control before it can blame Metal.

A one-draw GL control is only admissible causal evidence when it reproduces
live GL exactly on all four BGRA channels **and** it is provably the control for
the same sealed observation.  Those are different questions:

* :func:`compare` answers only whether two BGRA images are byte-identical.
* :func:`authenticate` binds the live image, control image and declared
  original-API execution to one sealed frame, event stream, draw, resource and
  checkpoint.
* :func:`qualify` requires both answers; an exact image from another seal is
  not qualified.
* :func:`attribution` is the only permitted Metal-blame projection, and it
  requires the qualified result rather than a caller-supplied flag.

This module is deliberately independent of the renderer.  It reports the exact
statistics, keeps a locator for both images, and names the smallest missing
observation when the control is not qualified.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import m6_compare

SCHEMA = "gfx-gl-control-qualification/v2"
COMPARISON_SCHEMA = "gfx-gl-control-comparison/v1"
AUTHENTICATION_SCHEMA = "gfx-gl-control-authentication/v1"
MANIFEST_SCHEMA = "gfx-gl-control-manifest/v2"
DEFAULT_RESOURCE = "fb:0"
ORIGINAL_API = "opengl"


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


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value))


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


def compare(live_path: Path | str, control_path: Path | str,
            width: int, height: int) -> dict:
    """Compare two BGRA images exactly; no provenance decision is made here."""
    live_path = Path(live_path)
    control_path = Path(control_path)
    if width <= 0 or height <= 0:
        raise QualificationError("invalid_extent", f"{width}x{height}")
    try:
        live = live_path.read_bytes()
    except OSError as exc:
        raise QualificationError("unreadable_live", f"{live_path}: {exc}") from exc
    try:
        control = control_path.read_bytes()
    except OSError as exc:
        raise QualificationError(
            "unreadable_control", f"{control_path}: {exc}") from exc
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
        "live": {"path": str(live_path), "sha256": _sha(live_path)},
        "control": {"path": str(control_path),
                    "sha256": _sha(control_path)},
        "statistics": statistics,
    }


def _find_checkpoint_request(frame_dir: Path,
                             checkpoint_request: Path | str | None) -> Path:
    candidates = []
    if checkpoint_request is not None:
        candidates.append(Path(checkpoint_request))
    candidates.extend([
        frame_dir / "checkpoint-request.json",
        frame_dir.parent / "shadow" / "checkpoint-request.json",
        frame_dir.parent / "checkpoint-request.json",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise QualificationError(
        "missing_checkpoint_request",
        "no checkpoint request binds the checkpoint event to the sealed frame")


def _checkpoint_row(request: dict, event: int,
                    resource: str) -> dict | None:
    rows = request.get("checkpoints")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get("event")
        if type(value) is int and value == event \
                and str(row.get("resource") or "") == resource:
            return row
    return None


def _authenticate(frame_dir: Path | str,
                  live_path: Path | str,
                  control_path: Path | str,
                  width: int, height: int, *,
                  checkpoint_request: Path | str | None,
                  checkpoint_event: int,
                  draw_event: int,
                  manifest: Path | str,
                  resource: str = DEFAULT_RESOURCE) -> dict:
    """Bind the images and control execution to one sealed observation.

    Every required fact is read from a file and checked, not trusted from a
    caller-supplied boolean: the seal must validate, the live image must be the
    checkpoint's declared reference, the event stream digest must equal the
    seal's digest, the manifest must describe a completed original-API render
    of the selected draw from that same stream, and the control artifact digest
    must match the manifest.
    """
    from automation import m6_frame

    frame_dir = Path(frame_dir).expanduser().resolve()
    live_path = Path(live_path).expanduser().resolve()
    control_path = Path(control_path).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()

    reasons: list[str] = []
    missing: list[str] = []
    identity: dict = {}

    def fail(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    seal = None
    try:
        seal = m6_frame.load_seal(
            frame_dir, verify_counts=False, verify_blobs=False)
    except Exception as exc:  # noqa: BLE001 - report as explicit unqualified
        fail(f"frame seal is not authentic: {exc}")
    if seal is not None:
        for field in ("session_id", "capture_frame_id", "present_index",
                      "event_stream_sha256"):
            identity[field] = seal.get(field)
        if int(seal.get("width") or 0) != width \
                or int(seal.get("height") or 0) != height:
            fail(
                f"comparison extent {width}x{height} does not match the seal "
                f"{seal.get('width')}x{seal.get('height')}")

    request_path = None
    try:
        request_path = _find_checkpoint_request(frame_dir, checkpoint_request)
        request = _read_json(request_path, "checkpoint_request")
        if request.get("schema") != "gfx-frame-checkpoint-request/v1":
            fail("checkpoint request has an unexpected schema")
        row = _checkpoint_row(request, checkpoint_event, resource)
        if row is None:
            fail(
                f"checkpoint request has no {resource} checkpoint at event "
                f"{checkpoint_event}")
        else:
            identity["checkpoint_event"] = checkpoint_event
            identity["checkpoint_resource"] = resource
            identity["checkpoint_reference_sha256"] = row.get(
                "reference_sha256")
            reference_sha = row.get("reference_sha256")
            if not _is_sha256(reference_sha):
                fail("checkpoint row has no valid reference sha256")
            else:
                live_sha = _sha_or_none(live_path)
                if live_sha is None:
                    fail(f"live image is unreadable: {live_path}")
                elif live_sha != reference_sha:
                    fail(
                        "live image is not the checkpoint's declared "
                        "reference (different seal or different checkpoint)")
            reference_file = row.get("reference_file")
            if isinstance(reference_file, str) and reference_file:
                expected_path = (frame_dir / "blobs" / reference_file).resolve()
                if live_path != expected_path:
                    fail(
                        "live image path is not the sealed checkpoint blob "
                        f"{reference_file}")
            checkpoint_session = row.get("session_id")
            checkpoint_frame = row.get("capture_frame_id")
            if checkpoint_session is not None \
                    and seal is not None \
                    and checkpoint_session != seal.get("session_id"):
                fail("checkpoint request names a different session")
            if checkpoint_frame is not None \
                    and seal is not None \
                    and checkpoint_frame != seal.get("capture_frame_id"):
                fail("checkpoint request names a different capture frame")
    except QualificationError as exc:
        fail(exc.detail or str(exc))

    try:
        control_sha = _sha(control_path)
    except OSError as exc:
        fail(f"control image is unreadable: {exc}")
        control_sha = None

    manifest_value = None
    try:
        manifest_value = _read_json(manifest_path, "control_manifest")
    except QualificationError as exc:
        fail(exc.detail or str(exc))

    if manifest_value is not None:
        if manifest_value.get("schema") != MANIFEST_SCHEMA:
            fail(
                f"control manifest schema is {manifest_value.get('schema')!r}, "
                f"expected {MANIFEST_SCHEMA!r}")
        if str(manifest_value.get("api") or "").lower() != ORIGINAL_API:
            fail(
                "control was not rendered through the original OpenGL API "
                f"(api={manifest_value.get('api')!r})")
        if manifest_value.get("execution") != "completed":
            fail(
                "control execution is not declared complete "
                f"(execution={manifest_value.get('execution')!r})")
        if seal is not None:
            for field in ("session_id", "capture_frame_id", "present_index"):
                if manifest_value.get(field) != seal.get(field):
                    fail(
                        f"control manifest {field} does not match the sealed "
                        "frame")
            if manifest_value.get("event_stream_sha256") \
                    != seal.get("event_stream_sha256"):
                fail(
                    "control manifest event stream does not match the sealed "
                    "frame")
        if type(manifest_value.get("draw_event")) is not int \
                or manifest_value.get("draw_event") != draw_event:
            fail(
                "control manifest draw event does not match the selected draw")
        if type(manifest_value.get("checkpoint_event")) is not int \
                or manifest_value.get("checkpoint_event") != checkpoint_event:
            fail(
                "control manifest checkpoint event does not match the sealed "
                "checkpoint")
        if str(manifest_value.get("resource") or "") != resource:
            fail("control manifest resource does not match the checkpoint")
        if manifest_value.get("artifact_sha256") != control_sha:
            fail("control artifact digest does not match the control manifest")
        if int(manifest_value.get("width") or 0) != width \
                or int(manifest_value.get("height") or 0) != height:
            fail("control manifest extent does not match the comparison")
        if str(manifest_value.get("pixel_format") or "").upper() != "BGRA8":
            fail("control manifest pixel format is not BGRA8")
        if not manifest_value.get("renderer") \
                or not isinstance(manifest_value.get("renderer"), dict):
            fail("control manifest has no original-API renderer identity")
        if not _is_sha256(manifest_value.get("executable_sha256")):
            fail("control manifest has no executable digest")
        identity["draw_event"] = draw_event
        identity["resource"] = resource
        identity["control_artifact_sha256"] = control_sha
        identity["control_manifest_sha256"] = _sha(manifest_path)

    if seal is not None:
        try:
            trace = m6_frame.load_trace(frame_dir, verify_blobs=False)
        except Exception as exc:  # noqa: BLE001
            fail(f"sealed event stream is unreadable: {exc}")
            trace = None
        if trace is not None:
            events = trace.get("_events") or []
            draw = next(
                (event for event in events
                 if type(event.get("index")) is int
                 and event.get("index") == draw_event), None)
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
            if not any(
                    type(event.get("index")) is int
                    and event.get("index") == checkpoint_event
                    for event in events):
                fail(
                    f"checkpoint event {checkpoint_event} is absent from the "
                    "sealed event stream")
            if draw_event >= checkpoint_event:
                fail("selected draw does not precede the checkpoint")

    if identity.get("checkpoint_event") is None:
        missing.append(
            "sealed checkpoint identity (event, resource, reference digest) "
            "was not established")
    if identity.get("draw_event") is None:
        missing.append("selected draw identity was not established")
    if identity.get("control_manifest_sha256") is None:
        missing.append(
            "authenticated original-API control manifest was not established")

    return {
        "schema": AUTHENTICATION_SCHEMA,
        "status": "authenticated" if not reasons else "unauthenticated",
        "authenticated": not reasons,
        "reasons": reasons,
        "missing": missing,
        "identity": identity,
        "seal": ({"path": str(frame_dir / "frame-seal.json"),
                  "sha256": _sha_or_none(frame_dir / "frame-seal.json")}
                 if seal is not None else None),
        "checkpoint_request": (
            {"path": str(request_path), "sha256": _sha_or_none(request_path)}
            if request_path is not None and request_path.is_file() else None),
        "control_manifest": (
            {"path": str(manifest_path), "sha256": _sha_or_none(manifest_path)}
            if manifest_path.is_file() else None),
    }


def _revalidate(authentication: dict | None, live_path: Path,
                control_path: Path, width: int, height: int) -> dict:
    """Re-check an authentication record against the actual evidence files.

    ``authentication`` is an output document, not a capability token.  A caller
    can pass a hand-written dict, so its booleans and reasons are ignored: the
    recorded seal/checkpoint/manifest paths are re-read and the identity is
    recomputed.  This makes a forged ``authenticated: true`` record useless and
    keeps the public :func:`qualify` entry point honest.
    """
    if not isinstance(authentication, dict):
        return {}
    seal = authentication.get("seal")
    manifest = authentication.get("control_manifest")
    request = authentication.get("checkpoint_request")
    identity = authentication.get("identity") or {}
    if not isinstance(seal, dict) or not isinstance(seal.get("path"), str) \
            or not isinstance(manifest, dict) \
            or not isinstance(manifest.get("path"), str) \
            or not isinstance(request, dict) \
            or not isinstance(request.get("path"), str):
        return {}
    try:
        return _authenticate(
            Path(seal["path"]).parent, live_path, control_path, width, height,
            checkpoint_request=Path(request["path"]),
            checkpoint_event=identity.get("checkpoint_event"),
            draw_event=identity.get("draw_event"),
            manifest=Path(manifest["path"]),
            resource=str(identity.get("resource") or DEFAULT_RESOURCE))
    except (OSError, ValueError, QualificationError):
        return {}


def _assemble(comparison: dict, authentication: dict | None, *,
              drawable: dict | None, prestate: dict | None) -> dict:
    authenticated = bool(
        isinstance(authentication, dict)
        and authentication.get("authenticated") is True
        and authentication.get("status") == "authenticated")
    reasons = list((authentication or {}).get("reasons") or [])
    missing = list((authentication or {}).get("missing") or [])
    if not isinstance(authentication, dict) or not authentication:
        missing.insert(
            0,
            "authenticated frame/stream/resource/checkpoint identity and "
            "original-API execution provenance were not supplied")
    if not comparison["exact"]:
        missing.extend(_missing(drawable, prestate))
    qualified = bool(
        comparison["exact"] and authenticated and not reasons and not missing)
    if not qualified and not missing:
        missing.append(
            "reduce the residual by varying one captured input at a time")
    return {
        "schema": SCHEMA,
        "status": "qualified" if qualified else "unqualified",
        "qualified": qualified,
        "width": comparison["width"],
        "height": comparison["height"],
        "comparison": comparison,
        "qualification": {
            "status": "qualified" if qualified else "unqualified",
            "qualified": qualified,
            "authenticated": authenticated,
            "reasons": reasons,
            "identity": (authentication or {}).get("identity") or {},
            "seal": (authentication or {}).get("seal"),
            "checkpoint_request": (authentication or {}).get(
                "checkpoint_request"),
            "control_manifest": (authentication or {}).get("control_manifest"),
        },
        "statistics": comparison["statistics"],
        "live": comparison["live"],
        "control": comparison["control"],
        "missing_observation": missing,
        "next_discriminator": (
            None if qualified else (missing[0] if missing else None)),
        "metal_blame_allowed": qualified,
    }


def qualify(live_path: Path | str, control_path: Path | str,
            width: int, height: int, *, drawable: dict | None = None,
            prestate: dict | None = None,
            authentication: dict | None = None) -> dict:
    """Return exact comparison plus re-validated control qualification.

    ``qualified`` is true only when the comparison is exact **and** the
    recorded sealed identity re-validates against the actual files.  A caller
    that supplies only two equal images, or a hand-written authentication
    dict, gets an explicit ``unqualified`` result and never a blame permission.
    """
    live_path = Path(live_path)
    control_path = Path(control_path)
    comparison = compare(live_path, control_path, width, height)
    if authentication is None:
        checked = None
    else:
        checked = _revalidate(
            authentication, live_path, control_path, width, height)
        if not checked:
            checked = {
                "authenticated": False,
                "status": "unauthenticated",
                "reasons": [
                    "the supplied authentication record did not re-validate "
                    "against the evidence files"],
                "missing": list(authentication.get("missing") or []),
            }
    return _assemble(
        comparison, checked, drawable=drawable, prestate=prestate)


def qualify_sealed(live_path: Path | str, control_path: Path | str,
                   width: int, height: int, *, frame_dir: Path | str,
                   checkpoint_request: Path | str | None = None,
                   checkpoint_event: int, draw_event: int,
                   manifest: Path | str,
                   resource: str = DEFAULT_RESOURCE,
                   drawable: dict | None = None,
                   prestate: dict | None = None) -> dict:
    """Authenticate the sealed observation, then qualify the control."""
    authentication = _authenticate(
        frame_dir, live_path, control_path, width, height,
        checkpoint_request=checkpoint_request,
        checkpoint_event=checkpoint_event, draw_event=draw_event,
        manifest=manifest, resource=resource)
    comparison = compare(live_path, control_path, width, height)
    return _assemble(
        comparison, authentication, drawable=drawable, prestate=prestate)


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
        and checked.get("authenticated") is True)
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
    parser.add_argument("control")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--frame-dir", type=Path, default=None,
                        help="sealed frame directory")
    parser.add_argument("--checkpoint-request", type=Path, default=None)
    parser.add_argument("--checkpoint-event", type=int, default=None)
    parser.add_argument("--draw-event", type=int, default=None)
    parser.add_argument("--resource", default=DEFAULT_RESOURCE)
    parser.add_argument("--manifest", type=Path, default=None,
                        help="authenticated original-API control manifest")
    parser.add_argument("--drawable", help="JSON drawable record")
    parser.add_argument("--prestate", help="JSON pre-draw state record")
    parser.add_argument("--comparison-only", action="store_true",
                        help="report pixel equality only; never qualifies")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if args.comparison_only:
        value = compare(args.live, args.control, args.width, args.height)
        code = 0 if value["exact"] else 1
    elif args.frame_dir is not None and args.manifest is not None \
            and args.checkpoint_event is not None \
            and args.draw_event is not None:
        drawable = (json.loads(Path(args.drawable).read_text())
                    if args.drawable else None)
        prestate = (json.loads(Path(args.prestate).read_text())
                    if args.prestate else None)
        value = qualify_sealed(
            args.live, args.control, args.width, args.height,
            frame_dir=args.frame_dir,
            checkpoint_request=args.checkpoint_request,
            checkpoint_event=args.checkpoint_event,
            draw_event=args.draw_event, manifest=args.manifest,
            resource=args.resource, drawable=drawable, prestate=prestate)
        code = 0 if value["qualified"] else 1
    else:
        value = {
            "schema": SCHEMA,
            "status": "unqualified",
            "qualified": False,
            "metal_blame_allowed": False,
            "missing_observation": [
                "qualification requires --frame-dir, --checkpoint-event, "
                "--draw-event and --manifest; pixel equality alone is not a "
                "control"],
            "comparison": compare(
                args.live, args.control, args.width, args.height),
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
