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
        seal = m6_frame.load_seal(
            frame_dir, verify_counts=False, verify_blobs=False)
    except Exception as exc:  # noqa: BLE001 - report as explicit unqualified
        fail(f"frame seal is not authentic: {exc}")
    if seal is not None:
        for field in ("session_id", "capture_frame_id", "present_index",
                      "event_stream_sha256", "reference_sha256"):
            identity[field] = seal.get(field)
        if int(seal.get("width") or 0) != width \
                or int(seal.get("height") or 0) != height:
            fail(
                f"comparison extent {width}x{height} does not match the seal "
                f"{seal.get('width')}x{seal.get('height')}")
        try:
            trace = m6_frame.load_trace(frame_dir, verify_blobs=False)
        except Exception as exc:  # noqa: BLE001
            fail(f"sealed event stream is unreadable: {exc}")
            trace = None

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
                if int(record.get("width") or 0) != width \
                        or int(record.get("height") or 0) != height:
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
        if int(artifact_record.get("width") or 0) != width \
                or int(artifact_record.get("height") or 0) != height:
            fail("control artifact extent does not match the comparison")
        if str(artifact_record.get("pixel_format") or "").upper() != "BGRA8":
            fail("control artifact pixel format is not BGRA8")
    if not isinstance(receipt.get("renderer"), dict) \
            or not receipt.get("renderer"):
        fail("control receipt has no original-API renderer identity")
    return reasons


def _run_renderer(*, renderer: Path, frame_dir: Path, live_path: Path,
                  identity: dict, width: int, height: int,
                  out_dir: Path, timeout: float) -> tuple[
                      Path | None, dict | None, list[str]]:
    """Execute the declared renderer and validate its receipt."""
    renderer = Path(renderer).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    if not renderer.is_file():
        return None, None, [f"control renderer is not a file: {renderer}"]
    renderer_sha = _sha_or_none(renderer)
    if renderer_sha is None:
        return None, None, [f"control renderer is unreadable: {renderer}"]
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if any(out_dir.iterdir()):
            return None, None, [
                f"control output directory is not empty: {out_dir}"]
        nonce = secrets.token_hex(16)
        request = {
            "schema": REQUEST_SCHEMA,
            "nonce": nonce,
            "frame_dir": str(frame_dir),
            "live_image": str(live_path),
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
            return None, None, [
                f"control renderer failed with exit {completed.returncode}"
                + (f": {detail[-500:]}" if detail else "")]
        artifact = out_dir / "result.bgra"
        receipt_path = out_dir / "receipt.json"
        if not artifact.is_file() or not receipt_path.is_file():
            return None, None, [
                "control renderer did not produce result.bgra and receipt.json"]
        receipt = _read_json(receipt_path, "control_receipt")
        reasons = _validate_receipt(
            receipt, nonce=nonce, renderer_sha=renderer_sha,
            identity=identity, width=width, height=height,
            artifact=artifact)
        return artifact, receipt, reasons
    except subprocess.TimeoutExpired:
        return None, None, [f"control renderer timed out after {timeout}s"]
    except (OSError, ValueError, QualificationError) as exc:
        return None, None, [f"control renderer could not be authenticated: {exc}"]


def _assemble(comparison: dict | None, authentication: dict | None, *,
              receipt: dict | None = None, control_path: Path | None = None,
              drawable: dict | None = None, prestate: dict | None = None,
              run_reasons: list[str] | None = None) -> dict:
    authenticated = bool(
        isinstance(authentication, dict)
        and authentication.get("authenticated") is True
        and authentication.get("status") == "authenticated")
    reasons = list((authentication or {}).get("reasons") or [])
    reasons.extend(run_reasons or [])
    missing = list((authentication or {}).get("missing") or [])
    if not isinstance(authentication, dict) or not authentication:
        missing.insert(
            0,
            "authenticated frame/stream/resource/checkpoint identity was not "
            "supplied")
    if comparison is None:
        missing.insert(0, "no authenticated control image was produced")
    elif not comparison["exact"]:
        missing.extend(_missing(drawable, prestate))
    qualified = bool(
        comparison is not None and comparison["exact"] and authenticated
        and not reasons and not missing)
    if not qualified and not missing:
        missing.append(
            "reduce the residual by varying one captured input at a time")
    statistics = comparison["statistics"] if comparison is not None else None
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
            "identity": (authentication or {}).get("identity") or {},
            "frame_dir": (authentication or {}).get("frame_dir"),
            "seal": (authentication or {}).get("seal"),
            "receipt": ({"path": str(control_path.parent / "receipt.json"),
                         "sha256": _sha_or_none(
                             control_path.parent / "receipt.json")}
                        if control_path is not None else None),
            "control": ({"path": str(control_path),
                         "sha256": _sha_or_none(control_path)}
                        if control_path is not None else None),
            "renderer": (receipt or {}).get("renderer"),
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
                   drawable: dict | None = None,
                   prestate: dict | None = None) -> dict:
    """Authenticate the seal, execute the renderer, then qualify the control."""
    live_path = Path(live_path)
    frame_dir = Path(frame_dir)
    authentication = _authenticate(
        frame_dir, live_path, width, height,
        checkpoint_event=checkpoint_event, draw_event=draw_event,
        resource=resource)
    if not authentication["authenticated"]:
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate)
    control_path, receipt, reasons = _run_renderer(
        renderer=Path(renderer), frame_dir=frame_dir, live_path=live_path,
        identity=authentication["identity"], width=width, height=height,
        out_dir=Path(out_dir), timeout=timeout)
    if control_path is None:
        return _assemble(
            None, authentication, drawable=drawable, prestate=prestate,
            run_reasons=reasons)
    comparison = compare(live_path, control_path, width, height)
    return _assemble(
        comparison, authentication, receipt=receipt,
        control_path=control_path, drawable=drawable, prestate=prestate,
        run_reasons=reasons)


def _evidence_recheck(qualification: dict) -> bool:
    checked = qualification.get("qualification") or {}
    receipt_ref = checked.get("receipt") or {}
    control_ref = checked.get("control") or {}
    receipt_path = Path(str(receipt_ref.get("path") or ""))
    control_path = Path(str(control_ref.get("path") or ""))
    if not receipt_path.is_file() or not control_path.is_file():
        return False
    if _sha_or_none(receipt_path) != receipt_ref.get("sha256"):
        return False
    if _sha_or_none(control_path) != control_ref.get("sha256"):
        return False
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    artifact = receipt.get("artifact")
    return bool(
        isinstance(artifact, dict)
        and artifact.get("sha256") == control_ref.get("sha256")
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
