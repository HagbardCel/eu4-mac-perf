"""Qualify an original-API GL control before it can blame Metal.

A one-draw GL control is admissible causal evidence only when it reproduces
live GL exactly on all four BGRA channels **and** its production is provably
tied to the same sealed observation from independent pre-draw inputs.  Those
are different questions:

* :func:`compare` answers only whether two BGRA images are byte-identical.
* :func:`qualify_sealed` authenticates the sealed frame, event stream,
  checkpoint and draw; derives the mandatory drawable/prestate evidence from
  that seal (never from caller JSON); materialises a restricted, content-
  addressed replay-input package that excludes the post-draw reference;
  executes the declared renderer with a challenge nonce; verifies a signed
  receipt bound to the renderer authority, the input package and the output
  image; and only then admits the comparison.
* :func:`attribution` is the only permitted Metal-blame projection.  It
  re-derives the seal identity, the input evidence, the executed receipt and
  exact equality from disk instead of trusting a serialized boolean.

Reference isolation is structural, not cosmetic.  The renderer never receives
the frame directory or a copy of the target checkpoint image; it receives a
separate package containing only the authorised pre-draw state and draw inputs.
The reference stays a comparator-side object.

Trust boundary.  A signature authenticates execution identity: the holder of
the registered private key ran a renderer whose reviewed digest is pinned.  It
is not, by itself, proof that the renderer made OpenGL calls; that property
follows from the reviewed implementation and the digest-pinned execution path.
The shipped authority registry pins the reviewed M7 original-OpenGL renderer;
an unlisted renderer remains unqualified.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

from automation import dsh_receipts, gfx_ed25519, state
from automation import gfx_gl_replay_input as replay_input
from automation import gfx_frame_pixels
from automation.gfx_artifacts import sha256_file as _sha

SCHEMA = "gfx-gl-control-qualification/v4"
COMPARISON_SCHEMA = "gfx-gl-control-comparison/v1"
AUTHENTICATION_SCHEMA = "gfx-gl-control-authentication/v3"
RECEIPT_SCHEMA = "gfx-gl-control-receipt/v2"
REQUEST_SCHEMA = "gfx-gl-control-request/v2"
EVIDENCE_SCHEMA = replay_input.EVIDENCE_SCHEMA
DEFAULT_RESOURCE = "fb:0"
ORIGINAL_API = "opengl"
DEFAULT_TIMEOUT = 300.0
SIGNATURE_ALGORITHM = "ed25519"
RECEIPT_CANONICALIZATION = "json-sorted-compact-utf8-excluding-signature"

_DRAWABLE_FIELDS = ("samples", "sample_buffers", "depth_bits", "stencil_bits",
                    "red_bits", "green_bits", "blue_bits", "alpha_bits")


class QualificationError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


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


def _canonical(value) -> bytes:
    return replay_input.canonical_json(value)


def _receipt_body(receipt: dict) -> dict:
    body = dict(receipt)
    body.pop("signature", None)
    return body


def _intervening_writer(events: list[dict], draw_position: int,
                        checkpoint_position: int,
                        framebuffer: int) -> dict | None:
    """Return a same-target draw/clear between two event-stream positions.

    Positions, not recorded ``index`` values, are authoritative: the recorder
    emits some clears with a null index, and an unindexed clear still changes
    the framebuffer.
    """
    from automation import gfx_frame_graph
    from automation.gfx_gl_replay_input import SUPPORTED_EVENT_KINDS
    for position in range(draw_position + 1, checkpoint_position):
        event = events[position]
        kind = event.get("kind")
        if kind not in SUPPORTED_EVENT_KINDS:
            return event
        if kind in ("draw", "clear"):
            target = event.get("target") or {}
            recorded = target.get("framebuffer")
            if type(recorded) is not int or recorded == framebuffer:
                # An unknown target cannot be excluded as a writer.
                return event
        elif kind in ("copy", "resolve"):
            problems = gfx_frame_graph.copy_contract_problems(event, position)
            if problems or str(event.get("destination")) == "0":
                return event
    return None


def _load_authority(renderer_sha: str | None) -> dict:
    """Resolve the executed renderer in the checked-in authority registry.

    A caller-supplied JSON file is not a pin: any caller can write it.  The
    authority is a reviewed entry in
    :mod:`automation.gfx_gl_control_authorities`; adding one is a code change.
    The entry binds the renderer digest, its independent components, its
    original-API identity and the Ed25519 public key that signs receipts.
    """
    from automation import gfx_gl_control_authorities as registry_module

    if not renderer_sha:
        return {"qualified": False, "self_attested": True,
                "reasons": ["the renderer was not executed"]}
    entries = registry_module.AUTHORITIES
    entry = entries.get(renderer_sha) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {
            "qualified": False,
            "self_attested": False,
            "registry": registry_module.SCHEMA,
            "renderer_sha256": renderer_sha,
            "reasons": [
                "renderer digest is not in the checked-in authority registry"],
        }
    reasons = []
    if entry.get("qualified") is not True:
        reasons.append("renderer registry entry is not qualified")
    if str(entry.get("api") or "").lower() != ORIGINAL_API:
        reasons.append(
            "renderer registry entry does not name the original OpenGL API")
    if not isinstance(entry.get("name"), str) or not entry.get("name"):
        reasons.append("renderer registry entry has no renderer name")
    public_key = entry.get("public_key")
    if not isinstance(public_key, str) or len(public_key) != 64 \
            or any(character not in "0123456789abcdef" for character in public_key):
        reasons.append(
            "renderer registry entry has no Ed25519 public key")
    components = entry.get("components", [])
    if not isinstance(components, list):
        reasons.append("renderer registry entry components are malformed")
        components = []
    for component in components:
        if not isinstance(component, dict) \
                or not isinstance(component.get("role"), str) \
                or not _is_sha256(component.get("sha256")):
            reasons.append(
                "renderer registry entry has a malformed component binding")
            break
    return {
        "qualified": not reasons,
        "self_attested": False,
        "registry": registry_module.SCHEMA,
        "source": "checked-in-registry",
        "renderer_sha256": renderer_sha,
        "name": entry.get("name"),
        "qualified_by": entry.get("qualified_by"),
        "public_key": public_key if isinstance(public_key, str) else None,
        "components": [
            {"role": item.get("role"), "sha256": item.get("sha256")}
            for item in components if isinstance(item, dict)
        ],
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


def _reference_pixels(path: Path, digest: str, width: int, height: int) -> bytes:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise QualificationError("reference_identity", "reference image changed after authentication")
    expected = width * height * 4
    if width <= 0 or height <= 0 or len(payload) != expected:
        raise QualificationError("reference_extent", f"reference image has {len(payload)} bytes, expected {expected}")
    return payload


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
    statistics = gfx_frame_pixels.compare_images(live, control, width, height)
    exact, _ = gfx_frame_pixels.exact_comparison_statistics(statistics, width, height)
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
    ``default_checkpoint`` record, not a mutable request sidecar.  The live
    image must be the exact sealed blob at that path and digest.
    """
    from automation import gfx_frame

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
        trace = gfx_frame.load_trace(frame_dir, verify_blobs=True)
    except Exception as exc:  # noqa: BLE001 - explicit unqualified
        fail(f"sealed event stream or referenced blobs are unreadable: {exc}")
        trace = None
    if trace is not None:
        try:
            seal = gfx_frame.load_seal(
                frame_dir, trace=trace, verify_counts=True, verify_blobs=True)
        except Exception as exc:  # noqa: BLE001 - explicit unqualified
            fail(f"frame seal is not authentic: {exc}")
        identity["blobs_verified"] = trace.get("_blob_count_verified")
    if seal is not None:
        for field in ("session_id", "capture_frame_id", "present_index",
                      "event_stream_sha256", "reference_sha256"):
            identity[field] = seal.get(field)
        identity["seal_sha256"] = _sha_or_none(frame_dir / "frame-seal.json")
        identity["manifest_sha256"] = seal.get("manifest_sha256")
        identity["reference_file"] = seal.get("reference_file")
        identity["capture_dir"] = str(frame_dir)
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
        from automation.gfx_gl_replay_input import SUPPORTED_EVENT_KINDS
        for position, event in enumerate(events):
            if event.get("kind") not in SUPPORTED_EVENT_KINDS:
                fail(
                    f"event at position {position} has unclassified kind "
                    f"{event.get('kind')!r}")
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
                from automation import gfx_frame_graph
                for problem in gfx_frame_graph.default_checkpoint_problems(
                        record, checkpoint_event, frame_dir):
                    fail(f"checkpoint record is invalid: {problem}")
                if str(record.get("resource") or "") != resource:
                    fail(
                        f"checkpoint record resource {record.get('resource')!r} "
                        f"is not {resource}")
                if str(record.get("target") or "") != resource.split(":", 1)[-1]:
                    fail("checkpoint record target does not match the resource")
                reference_sha = record.get("sha256")
                reference_file = record.get("file")
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
                if (record.get("width"), record.get("height")) != (width, height):
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
        if expected_fb is not None and draw is not None and checkpoint is not None:
            draw_position = events.index(draw)
            checkpoint_position = events.index(checkpoint)
            if draw_position >= checkpoint_position:
                fail("selected draw appears after the checkpoint in the stream")
            else:
                intervening = _intervening_writer(
                    events, draw_position, checkpoint_position, expected_fb)
                if intervening is not None:
                    fail(
                        f"{intervening.get('kind')} event with recorded index "
                        f"{intervening.get('index')!r} targets the same "
                        "framebuffer between the draw and the checkpoint")
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
        "trace": trace,
        "seal": seal,
        "frame_dir": str(frame_dir),
        "seal_document": ({"path": str(frame_dir / "frame-seal.json"),
                           "sha256": _sha_or_none(frame_dir / "frame-seal.json")}
                          if seal is not None else None),
    }


def _public_authentication(authentication: dict) -> dict:
    """Drop the in-memory trace/seal documents from a transport projection."""
    return {key: value for key, value in authentication.items()
            if key not in ("trace", "seal")}


def derive_evidence(authentication: dict, *, checkpoint_event: int,
                    draw_event: int, width: int, height: int,
                    resource: str) -> dict:
    """Derive mandatory input evidence from the authenticated seal."""
    trace = authentication.get("trace")
    seal = authentication.get("seal")
    if not isinstance(trace, dict) or not isinstance(seal, dict):
        return {
            "schema": EVIDENCE_SCHEMA,
            "status": "missing",
            "proven": False,
            "drawable": None,
            "prestate": None,
            "missing": ["authenticated trace and seal are unavailable"],
            "unsupported": [],
            "identity": {},
        }
    return replay_input.derive_evidence(
        trace, seal, checkpoint_event=checkpoint_event, draw_event=draw_event,
        resource=resource, width=width, height=height)


def _request_document(*, nonce: str, package: dict, identity: dict,
                      width: int, height: int, draw_event: int,
                      checkpoint_event: int, resource: str) -> dict:
    """The renderer's complete input surface.

    Deliberately absent: the frame directory, the live image path, the live
    image digest, and any copy of the post-draw reference.  The renderer sees
    provenance labels and the restricted package only.
    """
    return {
        "schema": REQUEST_SCHEMA,
        "nonce": nonce,
        "input_package": package["path"],
        "input_package_sha256": package["package_sha256"],
        "expected": {
            "session_id": identity.get("session_id"),
            "capture_frame_id": identity.get("capture_frame_id"),
            "present_index": identity.get("present_index"),
            "event_stream_sha256": identity.get("event_stream_sha256"),
            "checkpoint_event": checkpoint_event,
            "checkpoint_resource": resource,
            "draw_event": draw_event,
        },
        "width": width,
        "height": height,
    }


def _sanitized_env(repo_root: Path, run_dir: Path) -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("EU4_")
           and key not in ("PWD", "OLDPWD")}
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (str(repo_root) if not existing
                         else str(repo_root) + os.pathsep + existing)
    # A renderer that is confined to its working directory should not learn the
    # operator's shell or evidence paths from inherited variables.
    env["HOME"] = str(run_dir)
    env["TMPDIR"] = str(run_dir)
    return env


def _verified_input_package(path: Path, expected: str) -> dict:
    package = replay_input.verify_package(path)
    if package["package_sha256"] != expected:
        raise QualificationError("package_identity", "restricted replay inputs changed")
    return package


def _run_renderer(*, renderer: Path, package: dict, identity: dict,
                  evidence: dict, width: int, height: int, draw_event: int,
                  checkpoint_event: int, resource: str, out_dir: Path,
                  live_path: Path, timeout: float) -> dict:
    """Execute the declared renderer against the restricted input package."""
    renderer = Path(renderer).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    run_dir = out_dir / "renderer-run"
    result = {
        "artifact": None,
        "receipt": None,
        "receipt_path": None,
        "reasons": [],
        "nonce": None,
        "renderer_sha256": None,
        "renderer_path": str(renderer),
        "request_path": None,
        "request_sha256": None,
        "package": package,
        "execution": None,
        "unsupported": [],
    }

    def fail(reason: str) -> dict:
        if reason not in result["reasons"]:
            result["reasons"].append(reason)
        return result

    if not renderer.is_file():
        return fail(f"control renderer is not a file: {renderer}")
    renderer_sha = _sha_or_none(renderer)
    if renderer_sha is None:
        return fail(f"control renderer is unreadable: {renderer}")
    result["renderer_sha256"] = renderer_sha
    try:
        if run_dir.is_symlink():
            return fail("control run directory is a symlink")
        if run_dir.exists():
            if any(run_dir.iterdir()):
                return fail(
                    f"control output directory already has contents: {run_dir}")
        run_dir.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_hex(16)
        result["nonce"] = nonce
        request = _request_document(
            nonce=nonce, package=package, identity=identity, width=width,
            height=height, draw_event=draw_event,
            checkpoint_event=checkpoint_event, resource=resource)
        request["out"] = str(run_dir)
        request_path = run_dir / "request.json"
        request_path.write_text(json.dumps(request, indent=2) + "\n",
                                encoding="utf-8")
        result["request_path"] = str(request_path)
        result["request_sha256"] = _sha_or_none(request_path)
        _verified_input_package(Path(package["path"]), package["package_sha256"])
        if renderer.suffix == ".py":
            command = [sys.executable, str(renderer)]
        else:
            command = [str(renderer)]
        command.extend(["--request", str(request_path), "--out", str(run_dir)])
        repo_root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            check=False, cwd=str(run_dir),
            env=_sanitized_env(repo_root, run_dir))
        _verified_input_package(Path(package["path"]), package["package_sha256"])
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return fail(
                f"control renderer failed with exit {completed.returncode}"
                + (f": {detail[-500:]}" if detail else ""))
        artifact = run_dir / "result.bgra"
        receipt_path = run_dir / "receipt.json"
        for path in (receipt_path, artifact):
            if path.is_symlink():
                return fail(f"control output is a symlink: {path.name}")
        try:
            dsh_receipts.artifact_records(run_dir, (path.name for path in (receipt_path, artifact)
                                                    if path.exists()))
        except ValueError as exc:
            return fail(str(exc))
        if not receipt_path.is_file():
            return fail("control renderer did not produce receipt.json")
        receipt = _read_json(receipt_path, "control_receipt")
        reasons, unsupported, execution = _validate_receipt(
            receipt, nonce=nonce, renderer_sha=renderer_sha,
            authority=_load_authority(renderer_sha), identity=identity,
            package_sha256=package["package_sha256"], width=width,
            height=height, draw_event=draw_event,
            checkpoint_event=checkpoint_event, resource=resource,
            artifact=artifact, evidence=evidence)
        result["artifact"] = artifact if artifact.is_file() else None
        result["receipt"] = receipt
        result["receipt_path"] = receipt_path
        result["execution"] = execution
        result["unsupported"] = unsupported
        result["reasons"].extend(reasons)
        return result
    except subprocess.TimeoutExpired:
        return fail(f"control renderer timed out after {timeout}s")
    except (OSError, ValueError, TypeError, AttributeError, KeyError,
            IndexError, QualificationError,
            replay_input.ReplayInputError) as exc:
        return fail(f"control renderer could not be authenticated: {exc}")


def _same_drawable(recorded: dict | None, reported: dict | None) -> list[str]:
    reasons: list[str] = []
    if not isinstance(recorded, dict) or not isinstance(reported, dict):
        return ["the renderer did not report its drawable properties"]
    for field in _DRAWABLE_FIELDS:
        if reported.get(field) != recorded.get(field):
            reasons.append(
                f"renderer drawable {field} {reported.get(field)!r} does not "
                f"match the captured drawable {recorded.get(field)!r}")
    for field in ("width", "height"):
        if reported.get(field) != recorded.get(field):
            reasons.append(
                f"renderer drawable {field} {reported.get(field)!r} does not "
                f"match the captured drawable {recorded.get(field)!r}")
    recorded_identity = recorded.get("format_identity")
    reported_identity = reported.get("format_identity")
    if not isinstance(recorded_identity, dict):
        reasons.append(
            "the captured drawable has no proven internal-format identity; "
            "matching bit depths is not live-drawable equivalence")
    elif reported_identity != recorded_identity:
        reasons.append(
            "renderer drawable internal-format identity does not match the "
            f"captured identity ({reported_identity!r} != "
            f"{recorded_identity!r})")
    return reasons


def _validate_receipt(receipt: dict, *, nonce: str, renderer_sha: str,
                      authority: dict, identity: dict, package_sha256: str,
                      width: int, height: int, draw_event: int,
                      checkpoint_event: int, resource: str,
                      artifact: Path, evidence: dict | None = None
                      ) -> tuple[list[str], list[str], str | None]:
    reasons: list[str] = []
    unsupported: list[str] = []

    def fail(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    if receipt.get("schema") != RECEIPT_SCHEMA:
        fail(
            f"control receipt schema is {receipt.get('schema')!r}, expected "
            f"{RECEIPT_SCHEMA!r}")
    if receipt.get("nonce") != nonce:
        fail("control receipt did not echo the execution challenge")
    renderer = receipt.get("renderer")
    if not isinstance(renderer, dict) or not renderer:
        fail("control receipt has no original-API renderer identity")
        renderer = {}
    else:
        if renderer.get("sha256") != renderer_sha:
            fail("control receipt renderer digest does not match the executed file")
        if authority.get("name") is not None \
                and renderer.get("name") != authority.get("name"):
            fail("control receipt renderer name does not match the authority")
        receipt_components = renderer.get("components")
        expected_components = authority.get("components") or []
        if receipt_components is None:
            receipt_components = []
        if not isinstance(receipt_components, list) or not all(
                isinstance(item, dict) for item in receipt_components):
            fail("control receipt renderer components are malformed")
        elif not all(isinstance(item.get("role"), str)
                     and isinstance(item.get("sha256"), str)
                     for item in receipt_components):
            fail("control receipt renderer component types are malformed")
        else:
            actual = sorted(
                (item.get("role"), item.get("sha256"))
                for item in receipt_components)
            expected = sorted(
                (item.get("role"), item.get("sha256"))
                for item in expected_components)
            if actual != expected:
                fail(
                    "control receipt component digests do not match the "
                    "registered renderer authority")
            for item in receipt_components:
                path = item.get("path")
                if not isinstance(path, str) or not path:
                    fail(
                        "control receipt component path is not recorded; a "
                        "component digest cannot be independently verified")
                    continue
                component = Path(path)
                if component.is_symlink() or not component.is_file():
                    fail(
                        f"control receipt component is not a regular file: "
                        f"{path}")
                elif _sha_or_none(component) != item.get("sha256"):
                    fail(
                        f"control receipt component digest does not match the "
                        f"file on disk: {path}")
    if str(receipt.get("api") or "").lower() != ORIGINAL_API:
        fail(
            "control was not rendered through the original OpenGL API "
            f"(api={receipt.get('api')!r})")
    execution = receipt.get("execution")
    if execution == "unsupported_drawable":
        recorded = receipt.get("unsupported")
        if isinstance(recorded, list):
            unsupported = [str(item) for item in recorded if item]
        if not unsupported:
            unsupported = ["the renderer reported an unsupported drawable"]
    elif execution != "completed":
        fail(
            "control execution is not declared complete "
            f"(execution={execution!r})")
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
        if checkpoint.get("event") != checkpoint_event:
            fail("control receipt checkpoint event does not match")
        if str(checkpoint.get("resource") or "") != resource:
            fail("control receipt checkpoint resource does not match")
    draw = receipt.get("draw")
    if not isinstance(draw, dict) or draw.get("event") != draw_event:
        fail("control receipt draw event does not match")
    consumed = receipt.get("input")
    if not isinstance(consumed, dict):
        fail("control receipt has no input-package identity")
    elif consumed.get("package_sha256") != package_sha256:
        fail("control receipt input package does not match the executed package")
    signature = receipt.get("signature")
    if not isinstance(signature, dict) \
            or signature.get("algorithm") != SIGNATURE_ALGORITHM \
            or not isinstance(signature.get("value"), str):
        fail("control receipt is not signed by a registered Ed25519 key")
    elif signature.get("canonicalization") not in (
            None, RECEIPT_CANONICALIZATION):
        fail("control receipt names an unknown signing canonicalisation")
    else:
        public_key = authority.get("public_key")
        try:
            signature_bytes = bytes.fromhex(signature["value"])
        except (TypeError, ValueError):
            signature_bytes = b""
        if not public_key or not gfx_ed25519.verify(
                bytes.fromhex(str(public_key)),
                _canonical(_receipt_body(receipt)), signature_bytes):
            fail(
                "control receipt signature does not verify against the "
                "registered renderer authority")
    if execution == "unsupported_drawable":
        return reasons, unsupported, execution
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
        if str(artifact_record.get("origin") or "") != "lower_left":
            fail("control artifact origin is not the captured lower_left")
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
    gl = receipt.get("gl")
    if not isinstance(gl, dict):
        fail("control receipt has no OpenGL execution record")
    else:
        for field in ("vendor", "renderer", "version"):
            if not isinstance(gl.get(field), str) or not gl.get(field):
                fail(f"control receipt OpenGL {field} identity is empty")
        if _int_or_none(gl.get("draw_calls")) != 1:
            fail("control receipt does not report exactly one executed draw")
        if _int_or_none(gl.get("error")) != 0:
            fail("control receipt reports a GL error during execution")
        if evidence is not None:
            reasons.extend(_same_drawable(evidence.get("drawable"),
                                          gl.get("target")))
    return reasons, unsupported, execution


def _evidence_digest(evidence: dict) -> str:
    return replay_input.sha256_bytes(_canonical({key: evidence.get(key) for key in (
        "status", "drawable", "drawable_equivalence", "prestate", "missing", "unsupported", "identity",
    )}))


def _assemble(comparison: dict | None, authentication: dict | None, *,
              evidence: dict | None = None, run: dict | None = None,
              live_sha256: str | None = None,
              authority: dict | None = None) -> dict:
    authenticated = bool(
        isinstance(authentication, dict)
        and authentication.get("authenticated") is True
        and authentication.get("status") == "authenticated")
    reasons = list((authentication or {}).get("reasons") or [])
    reasons.extend((run or {}).get("reasons") or [])
    authority = authority or {"qualified": False, "self_attested": True,
                              "reasons": ["no renderer authority was supplied"]}
    reasons.extend(authority.get("reasons") or [])
    missing = list((authentication or {}).get("missing") or [])
    evidence = evidence or {
        "schema": EVIDENCE_SCHEMA, "status": "missing", "proven": False,
        "drawable": None, "prestate": None,
        "missing": ["mandatory input evidence was not derived"],
        "unsupported": [], "identity": {},
    }
    missing.extend(evidence.get("missing") or [])
    unsupported = list(evidence.get("unsupported") or [])
    unsupported.extend((run or {}).get("unsupported") or [])
    identity = (authentication or {}).get("identity") or {}
    if not isinstance(authentication, dict) or not authentication:
        missing.insert(
            0,
            "authenticated frame/stream/resource/checkpoint identity was not "
            "supplied")
    if comparison is None:
        missing.insert(0, "no authenticated control image was produced")
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
    receipt_verified = bool(
        run is not None and run.get("receipt") is not None
        and not (run.get("reasons") or []))
    evidence_proven = evidence.get("proven") is True \
        and evidence.get("status") == "proven"
    drawable_equivalence = (
        evidence.get("drawable_equivalence")
        or (evidence.get("drawable") or {}).get("equivalence")
        or "unproven")
    if drawable_equivalence != "proven" and not missing and not unsupported:
        missing.append(
            "the live default drawable's internal pixel/depth/stencil formats "
            "and render-target path are not proven identical to the control "
            "target")
    qualified = bool(
        comparison is not None and comparison["exact"] and authenticated
        and evidence_proven and drawable_equivalence == "proven"
        and authority.get("qualified") and receipt_verified
        and live_bound and not reasons and not missing and not unsupported)
    if not qualified and not missing and not unsupported \
            and comparison is not None and not comparison["exact"]:
        missing.append(
            "reduce the residual by varying one captured input at a time")
    if unsupported:
        status = "unsupported_state"
    elif qualified:
        status = "qualified"
    else:
        status = "unqualified"
    statistics = comparison["statistics"] if comparison is not None else None
    control_path = (run or {}).get("artifact")
    receipt_path = (run or {}).get("receipt_path")
    return {
        "schema": SCHEMA,
        "status": status,
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
            "status": status,
            "qualified": qualified,
            "authenticated": authenticated,
            "evidence_proven": evidence_proven,
            "drawable_equivalence": drawable_equivalence,
            "receipt_verified": receipt_verified,
            "reasons": reasons,
            "identity": identity,
            "frame_dir": (authentication or {}).get("frame_dir"),
            "seal": (authentication or {}).get("seal_document"),
            "evidence": evidence,
            "evidence_sha256": _evidence_digest(evidence),
            "receipt": ({"path": str(receipt_path),
                         "sha256": _sha_or_none(receipt_path)}
                        if receipt_path is not None else None),
            "control": ({"path": str(control_path),
                         "sha256": _sha_or_none(control_path)}
                        if control_path is not None else None),
            "renderer": (run or {}).get("receipt", {}).get("renderer")
                        if run and run.get("receipt") else None,
            "renderer_sha256": (run or {}).get("renderer_sha256"),
            "renderer_path": (run or {}).get("renderer_path"),
            "nonce": (run or {}).get("nonce"),
            "request": ({"path": str((run or {}).get("request_path")),
                         "sha256": (run or {}).get("request_sha256")}
                        if (run or {}).get("request_path") is not None else None),
            "input_package": (run or {}).get("package"),
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
        "unsupported_state": unsupported,
        "next_discriminator": (
            None if qualified else
            (unsupported[0] if unsupported
             else (missing[0] if missing else None))),
        "metal_blame_allowed": qualified,
    }


def _refused_evidence(kind: str, detail: str) -> dict:
    return {
        "schema": EVIDENCE_SCHEMA,
        "status": "unsupported_state",
        "proven": False,
        "drawable": None,
        "prestate": None,
        "missing": [],
        "unsupported": [f"{kind}: {detail}"],
        "identity": {},
    }


def qualify_sealed(live_path: Path | str, width: int, height: int, *,
                   frame_dir: Path | str, checkpoint_event: int,
                   draw_event: int, renderer: Path | str,
                   out_dir: Path | str, resource: str = DEFAULT_RESOURCE,
                   timeout: float = DEFAULT_TIMEOUT,
                   caller_drawable: dict | None = None,
                   caller_prestate: dict | None = None) -> dict:
    """Authenticate the seal, derive inputs, execute the renderer, compare.

    ``caller_drawable`` and ``caller_prestate`` exist only to refuse them: the
    mandatory evidence is derived from the sealed event graph, never from
    caller JSON.
    """
    if caller_drawable is not None or caller_prestate is not None:
        return _assemble(
            None, None,
            evidence=_refused_evidence(
                "caller_supplied_evidence",
                "drawable/prestate JSON is not accepted; the qualifier derives "
                "both from the sealed frame"))
    live_path = Path(live_path)
    frame_dir = Path(frame_dir)
    out_dir = Path(out_dir)
    frame_resolved = frame_dir.expanduser().resolve()
    out_resolved = out_dir.expanduser().resolve()

    capture_root = frame_resolved.parent
    try:
        state.outside_workspace(out_resolved, capture_root)
    except RuntimeError:
        return _assemble(
            None, None,
            evidence=_refused_evidence(
                "unsafe_output_directory",
                f"control output {out_resolved} overlaps the sealed capture tree {capture_root}"))
    for sub in ("replay-input", "renderer-run"):
        candidate = out_resolved / sub
        if candidate.is_symlink():
            return _assemble(
                None, None,
                evidence=_refused_evidence(
                    "unsafe_output_directory",
                    f"control subdirectory {candidate} is a symlink"))
        if candidate.exists() and candidate.resolve().is_relative_to(capture_root):
            return _assemble(
                None, None,
                evidence=_refused_evidence(
                    "unsafe_output_directory",
                    f"control subdirectory {candidate} resolves inside the "
                    "sealed capture tree"))
    try:
        authentication = _authenticate(
            frame_dir, live_path, width, height,
            checkpoint_event=checkpoint_event, draw_event=draw_event,
            resource=resource)
    except Exception as exc:  # noqa: BLE001 - public boundary must refuse
        return _assemble(
            None, None,
            evidence=_refused_evidence(
                "malformed_seal", f"the sealed observation could not be read: {exc}"))
    if not authentication["authenticated"]:
        return _assemble(
            None, _public_authentication(authentication),
            evidence={
                "schema": EVIDENCE_SCHEMA, "status": "missing",
                "proven": False, "drawable": None, "prestate": None,
                "missing": list(authentication.get("missing") or []),
                "unsupported": [], "identity": {},
            })
    try:
        evidence = derive_evidence(
            authentication, checkpoint_event=checkpoint_event,
            draw_event=draw_event, width=width, height=height,
            resource=resource)
    except (replay_input.ReplayInputError, KeyError, IndexError, TypeError,
            ValueError) as exc:
        kind = getattr(exc, "kind", "malformed_evidence")
        detail = getattr(exc, "detail", str(exc))
        return _assemble(
            None, _public_authentication(authentication),
            evidence=_refused_evidence(kind, detail))
    public_authentication = _public_authentication(authentication)
    if evidence.get("status") != "proven":
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    live_sha256 = authentication["identity"].get("checkpoint_reference_sha256")
    try:
        live_bytes = _reference_pixels(live_path, live_sha256, width, height)
    except (OSError, QualificationError) as exc:
        public_authentication["reasons"].append(f"live image is invalid: {exc}")
        public_authentication["authenticated"] = False
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        package = replay_input.build_package(
            frame_dir=frame_dir, trace=authentication["trace"],
            seal=authentication["seal"], evidence=evidence,
            checkpoint_event=checkpoint_event, draw_event=draw_event,
            width=width, height=height, resource=resource,
            out_dir=out_dir / "replay-input")
    except (OSError, replay_input.ReplayInputError, AttributeError, KeyError,
            IndexError, TypeError, ValueError, OverflowError) as exc:
        public_authentication["reasons"].append(
            f"restricted replay-input package could not be built: {exc}")
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    identity = dict(authentication["identity"])
    run = _run_renderer(
        renderer=Path(renderer), package=package, identity=identity,
        evidence=evidence, width=width, height=height, draw_event=draw_event,
        checkpoint_event=checkpoint_event, resource=resource,
        out_dir=out_dir, live_path=live_path, timeout=timeout)
    authority = _load_authority(run.get("renderer_sha256") or "")
    control_path = run.get("artifact")
    comparison = None
    if control_path is not None:
        try:
            comparison = compare_payloads(live_bytes, control_path.read_bytes(), width, height,
                                          live_path=live_path, control_path=control_path)
        except (OSError, QualificationError) as exc:
            run["reasons"].append(f"control artifact could not be compared: {exc}")
        if _sha_or_none(live_path) != live_sha256:
            run["reasons"].append(
                "sealed live blob changed during the renderer run")
    return _assemble(
        comparison, public_authentication, evidence=evidence, run=run,
        live_sha256=live_sha256, authority=authority)


def _evidence_recheck(qualification: dict) -> bool:
    """Re-derive the evidence instead of trusting the returned projection.

    A qualification document is a transport object: a caller can edit its
    ``status``, ``exact`` or ``authenticated`` booleans without touching the
    images.  Causal attribution therefore re-authenticates the seal, re-derives
    the drawable/prestate evidence, re-reads and re-verifies the input package
    and signed receipt, and recomputes exact four-channel equality.
    """
    if not isinstance(qualification, dict):
        return False
    checked = qualification.get("qualification") or {}
    if not isinstance(checked, dict):
        return False
    identity = checked.get("identity") or {}
    comparison = qualification.get("comparison") or {}
    if not isinstance(identity, dict) or not isinstance(comparison, dict):
        return False
    authority = _load_authority(checked.get("renderer_sha256"))
    if authority.get("qualified") is not True:
        return False
    embedded = checked.get("renderer_authority") or {}
    if not isinstance(embedded, dict):
        return False
    if embedded.get("renderer_sha256") != checked.get("renderer_sha256"):
        return False
    if embedded.get("name") != authority.get("name"):
        return False
    if embedded.get("public_key") != authority.get("public_key"):
        return False
    references = {
        "renderer": {"path": checked.get("renderer_path"), "sha256": checked.get("renderer_sha256")},
        **{key: checked.get(key) for key in ("request", "receipt", "control")},
    }
    paths = {}
    for name, reference in references.items():
        if not isinstance(reference, dict) or not isinstance(reference.get("path"), str):
            return False
        path = Path(reference["path"])
        digest = reference.get("sha256")
        if not _is_sha256(digest) or _sha_or_none(path) != digest:
            return False
        paths[name] = path
    if paths["renderer"].is_symlink():
        return False
    request_document = _read_json(paths["request"], "control_request")
    live_ref, control_comparison = comparison.get("live"), comparison.get("control")
    if not isinstance(live_ref, dict) or not isinstance(control_comparison, dict):
        return False
    live_path = Path(str(live_ref.get("path") or ""))
    frame_dir = checked.get("frame_dir")
    control_sha = references["control"]["sha256"]
    if (not live_path.is_file() or not isinstance(frame_dir, str) or not frame_dir
            or control_sha != control_comparison.get("sha256")):
        return False
    width = _int_or_none(comparison.get("width"))
    height = _int_or_none(comparison.get("height"))
    if width is None or height is None:
        return False
    checkpoint_event = _int_or_none(identity.get("checkpoint_event"))
    draw_event = _int_or_none(identity.get("draw_event"))
    resource = str(identity.get("checkpoint_resource") or DEFAULT_RESOURCE)
    if checkpoint_event is None or draw_event is None:
        return False
    authentication = _authenticate(
        Path(frame_dir), live_path, width, height,
        checkpoint_event=checkpoint_event, draw_event=draw_event,
        resource=resource)
    if authentication.get("authenticated") is not True:
        return False
    derived = authentication.get("identity") or {}
    for field in ("session_id", "capture_frame_id", "present_index",
                  "event_stream_sha256", "checkpoint_event",
                  "checkpoint_resource", "checkpoint_reference_sha256",
                  "draw_event", "seal_sha256"):
        if derived.get(field) != identity.get(field):
            return False
    try:
        evidence = derive_evidence(
            authentication, checkpoint_event=checkpoint_event,
            draw_event=draw_event, width=width, height=height,
            resource=resource)
    except (replay_input.ReplayInputError, QualificationError):
        return False
    if evidence.get("status") != "proven":
        return False
    if evidence.get("drawable_equivalence") != "proven":
        return False
    embedded_evidence = checked.get("evidence")
    if not isinstance(embedded_evidence, dict):
        return False
    if embedded_evidence.get("drawable_equivalence") != "proven":
        return False
    if _evidence_digest(embedded_evidence) != checked.get("evidence_sha256") \
            or _evidence_digest(evidence) != checked.get("evidence_sha256"):
        return False
    package_ref = checked.get("input_package") or {}
    if not isinstance(package_ref, dict):
        return False
    package_path = Path(str(package_ref.get("path") or ""))
    if not package_path.is_dir():
        return False
    try:
        package = _verified_input_package(package_path, package_ref.get("package_sha256"))
    except (OSError, replay_input.ReplayInputError, QualificationError):
        return False
    expected = request_document.get("expected") or {}
    if request_document.get("nonce") != checked.get("nonce") \
            or request_document.get("input_package_sha256") != package[
                "package_sha256"]:
        return False
    for field in ("session_id", "capture_frame_id", "present_index",
                  "event_stream_sha256"):
        if expected.get(field) != derived.get(field):
            return False
    if expected.get("checkpoint_event") != checkpoint_event \
            or expected.get("draw_event") != draw_event \
            or expected.get("checkpoint_resource") != resource:
        return False
    receipt = _read_json(paths["receipt"], "control_receipt")
    reasons, _unsupported, execution = _validate_receipt(
        receipt, nonce=str(checked.get("nonce") or ""),
        renderer_sha=str(checked.get("renderer_sha256") or ""),
        authority=authority, identity=derived,
        package_sha256=package["package_sha256"], width=width,
        height=height, draw_event=draw_event,
        checkpoint_event=checkpoint_event, resource=resource,
        artifact=paths["control"], evidence=evidence)
    if reasons or execution != "completed":
        return False
    recomputed = compare(live_path, paths["control"], width, height)
    return (recomputed.get("exact") is True
            and recomputed["live"]["sha256"] == derived.get("checkpoint_reference_sha256")
            and recomputed["control"]["sha256"] == control_sha)


def attribution(qualification: dict) -> dict:
    """Project qualification into the only permitted blame decision.

    This is a decision boundary.  Malformed input, an unsupported state or an
    unreadable artifact must produce a structured refusal; it must never raise
    into a caller or fall through to an authorized verdict.
    """
    if not isinstance(qualification, dict):
        return {
            "schema": "gfx-metal-blame/v1",
            "allowed": False,
            "reason": (
                "the qualification document is malformed; an absent or "
                "non-object projection cannot authorize Metal attribution"),
            "qualification": None,
        }
    comparison = qualification.get("comparison")
    checked = qualification.get("qualification")
    try:
        evidence_ok = _evidence_recheck(qualification)
    except Exception:  # noqa: BLE001 - decision boundary must refuse
        # A malformed projection is not evidence. Attribution refuses instead
        # of raising a ReplayInputError, KeyError or AttributeError into its
        # caller.
        evidence_ok = False
    allowed = bool(
        qualification.get("status") == "qualified"
        and qualification.get("qualified") is True
        and qualification.get("metal_blame_allowed") is True
        and isinstance(comparison, dict)
        and comparison.get("exact") is True
        and isinstance(checked, dict)
        and checked.get("authenticated") is True
        and checked.get("evidence_proven") is True
        and checked.get("drawable_equivalence") == "proven"
        and evidence_ok)
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


def preflight_reference(frame_dir: Path, trace: dict, seal: dict, trusted: dict,
                        selection: dict) -> dict:
    """Classify original evidence before a renderer is built or executed."""
    from automation import gfx_frame_graph, gfx_observation

    if gfx_observation.authority(trace, seal) not in ("authentic", "synthetic"):
        return {"verdict": "invalid_provenance", "problems": ["reference has no verified execution tier"]}
    events = trace["_events"]
    draw_id = selection.get("draw")
    resource = selection.get("resource")
    workload = selection.get("workload")
    if workload is not None:
        matches = []
        for event in events:
            draw = event.get("draw") or {}
            color = (event.get("target") or {}).get("color_attachment") or {}
            shaders = (event.get("program") or {}).get("shaders") or []
            if (event.get("kind") == "draw"
                    and draw.get("caller_rva") == workload["caller_rva"]
                    and len(shaders) == len(workload["shader_sha256"])
                    and {row["stage"]: row["sha256"] for row in shaders}
                    == workload["shader_sha256"]
                    and all(color.get(key) == value for key, value in
                            workload["color_attachment"].items())):
                matches.append(event)
        if len(matches) != 1:
            return {"verdict": "inapplicable_reference",
                    "problems": ["semantic workload does not identify a unique original draw"]}
        selected = matches[0]
        if draw_id is not None and selected.get("index") != draw_id:
            return {"verdict": "invalid_provenance",
                    "problems": ["selected event disagrees with the workload identity"]}
        draw_id = selected.get("index")
        resource = resource or f"tex:{selected['target']['color_attachment']['name']}"
    draw = _event(events, draw_id)
    if not draw or draw.get("kind") != "draw":
        return {"verdict": "invalid_provenance", "problems": ["selected draw is absent"]}
    checkpoint_id = selection.get("checkpoint", draw_id)
    scope = {"draw": draw_id, "resource": resource, "checkpoint": checkpoint_id}
    if resource == DEFAULT_RESOURCE:
        facts = replay_input.derive_evidence(
            trace, seal, draw_event=draw_id, checkpoint_event=checkpoint_id,
            resource=resource, width=seal["width"], height=seal["height"])
        missing = list(facts.get("missing") or [])
        unsupported = list(facts.get("unsupported") or [])
        if (facts.get("drawable") or {}).get("depth_bits") == 32:
            missing.append("lossless original D32 depth observation")
        if not missing and not unsupported:
            unsupported.append("selected-draw candidate scope requires a color-only offscreen target")
        verdict = ("missing_original_observation" if missing else
                   "inapplicable_reference" if unsupported else "eligible")
        return {"verdict": verdict, "scope": scope, "problems": missing + unsupported}

    graph = gfx_frame_graph.build(frame_dir, trace, trusted_evidence=trusted)
    target = draw.get("target") or {}
    attachment = target.get("color_attachment") or {}
    if (resource != f"tex:{attachment.get('name')}" or attachment.get("type") != "texture"
            or attachment.get("level") != 0 or attachment.get("internal_format") != "RGBA8"):
        return {"verdict": "unsupported_execution_scope", "scope": scope,
                "problems": ["selected target is outside the color-only RGBA8 level-0 scope"]}
    if graph.get("status") == "invalid":
        return {"verdict": "invalid_provenance", "scope": scope,
                "problems": graph.get("reasons") or ["sealed graph is invalid"]}
    producers = (graph.get("resources", {}).get(resource) or {}).get("producers") or []
    earlier = [item for item in producers if item["event"] < draw_id]
    missing = []
    clear = None
    clear_event = None
    if earlier and (earlier[-1].get("coverage") or {}).get("color_full"):
        clear_event = _event(events, earlier[-1]["event"])
        if clear_event is not None and clear_event.get("kind") == "clear":
            clear = clear_event.get("color")
    missing.extend(replay_input._state_problems(draw_id, draw.get("state") or {}))
    direct = next((row for row in (graph.get("prefix") or {}).get("post_events") or []
                   if row.get("event") == draw_id and row.get("resource") == resource), None)
    if direct is None:
        history = gfx_frame_graph.load_frame_history(frame_dir, trace)
        if history.get("status") == "invalid":
            return {"verdict": "invalid_provenance", "scope": scope,
                    "problems": history.get("reasons") or ["invalid texture history"]}
        reads = ([_event(events, checkpoint_id) or {}] if "checkpoint" in selection else
                 [event for event in events
                  if type(event.get("index")) is int and event["index"] > draw_id])
        fetched = [
            (read["index"], level) for read in reads for texture in read.get("textures") or []
            if texture.get("name") == attachment["name"]
            for level in texture.get("levels") or [] if level.get("level") == 0]
        if len(fetched) != 1:
            missing.append("unique later readback of the selected original subresource")
        elif history.get("status") != "verified":
            scope["later_readback"] = fetched[0][0]
            missing.append("sealed texture lifecycle between production and later readback")
        else:
            scope["later_readback"] = fetched[0][0]
            name = str(attachment["name"])
            changes = [*history.get("delete_events", {}).get(name, []),
                       *history.get("mutation_events", {}).get(name, []),
                       *(producer["event"] for producer in producers)]
            if any(draw_id < event <= fetched[0][0] for event in changes):
                missing.append("later readback crosses an attachment write or lifetime change")
            # A later sampled texture inventory lacks an authenticated
            # immediate observation and freshness witness for this contract.
            missing.append("authenticated freshness and lifetime of the later readback")
        missing.append("direct original attachment observation including depth/stencil applicability")
    else:
        semantics = draw.get("draw") or {}
        submission = draw.get("submission") or {}
        required_submission = {
            "api": "glDrawArrays", "target_continuity": "entry_submission_exit",
            "call_id": semantics.get("call_id"),
            "framebuffer": target["framebuffer"], "read_framebuffer": target["framebuffer"],
            "attachment": attachment["name"], "level": 0, "samples": 0,
            "draw_buffer": 0x8ce0, "read_buffer": 0x8ce0,
            "depth_present": False, "stencil_present": False,
            **{key: semantics.get(key) for key in ("mode", "first", "count")},
        }
        if (semantics.get("indexed") is not False or semantics.get("instance_count") != 1
                or semantics.get("base_vertex") != 0
                or semantics.get("mode") not in (4, 5)
                or type(semantics.get("first")) is not int or semantics["first"] < 0
                or type(semantics.get("count")) is not int or semantics["count"] < 3):
            missing.append("supported nonindexed glDrawArrays semantics")
        if (type(submission.get("call_id")) is not int or submission["call_id"] <= 0
                or any(submission.get(key) != value for key, value in required_submission.items())):
            missing.append("effective GL submission and entry-to-exit attachment continuity")
        program = draw.get("program") or {}
        linkage = draw.get("program_diagnostics") or {}
        shaders = program.get("shaders") or []
        if (linkage.get("link") is not True
                or len(shaders) != 2
                or {shader.get("stage") for shader in shaders} != {"vertex", "pixel"}
                or any(shader.get("compile") is not True for shader in shaders)
                or linkage.get("active_attributes") != len(draw.get("attributes") or [])
                or linkage.get("active_uniforms") != len(program.get("uniforms") or [])):
            missing.append("effective linked shader and complete active program inputs")
        observed_target = direct.get("target") or {}
        observed_attachment = observed_target.get("color_attachment") or {}
        if (observed_target.get("framebuffer") != target.get("framebuffer")
                or any(observed_attachment.get(key) != attachment.get(key)
                       for key in ("name", "level", "type", "internal_format"))):
            return {"verdict": "invalid_provenance", "scope": scope,
                    "problems": ["post-draw attachment identity differs from the selected draw"]}
        if any(texture.get("name") == attachment["name"]
               for texture in draw.get("textures") or []):
            missing.append("draw samples its own color attachment")
        pre = draw.get("pre_image") or {}
        initial_color = None
        if pre:
            initial_identity = {"framebuffer": target["framebuffer"], "attachment": attachment["name"],
                                "level": attachment["level"], "width": attachment["width"],
                                "height": attachment["height"], "origin": "lower_left",
                                "internal_format": 0x8058, "pixel_format": "BGRA8"}
            name = pre.get("file")
            if (not isinstance(name, str) or Path(name).name != name
                    or any(pre.get(key) != value for key, value in initial_identity.items())):
                return {"verdict": "invalid_provenance", "scope": scope,
                        "problems": ["direct pre-draw attachment identity differs"]}
            seed_path = frame_dir / "blobs" / name
            if (_sha(seed_path) != pre.get("sha256")
                    or seed_path.stat().st_size != attachment["width"] * attachment["height"] * 4):
                return {"verdict": "invalid_provenance", "scope": scope,
                        "problems": ["direct pre-draw attachment payload differs"]}
            initial_color = {**pre, "source": "direct_pre_draw", "event": draw_id}
        elif isinstance(clear, list) and len(clear) == 4 and all(v in (0, 1) for v in clear):
            history = gfx_frame_graph.load_frame_history(frame_dir, trace)
            name = str(attachment["name"])
            changes = [*history.get("delete_events", {}).get(name, []),
                       *history.get("mutation_events", {}).get(name, [])]
            if history.get("status") == "verified" and not any(
                    clear_event["index"] <= change <= draw_id for change in changes):
                pixel = [int(clear[index]) * 255 for index in (2, 1, 0, 3)]
                initial_color = {"source": "verified_full_clear", "event": clear_event["index"],
                                 "bgra": pixel}
        if initial_color is None:
            missing.append("direct pre-draw attachment or proved full clear for this lifetime")
        fmt = gfx_observation.format_scope(direct)
        missing.extend(fmt.get("problems") or [])
        if (fmt.get("compared_channels") != "RGBA" or direct.get("pixel_format") != "RGBA8"
                or direct.get("origin") != "lower_left" or not (direct.get("color") or {}).get("file")
                or (direct.get("width"), direct.get("height")) !=
                (attachment.get("width"), attachment.get("height"))):
            missing.append("lossless original RGBA observation")
        if any(replay_input._gl_enum(target.get(field)) != 0x8ce0
               for field in ("draw_buffer", "read_buffer")):
            missing.append("observed COLOR_ATTACHMENT0 draw and read bindings")
        if draw["state"].get("framebuffer_srgb") is not False:
            missing.append("observed linear framebuffer state for the RGBA8 attachment")
        state = draw["state"]
        if (state.get("blend", {}).get("enabled") or state.get("depth", {}).get("test")
                or state.get("stencil", {}).get("test") or state.get("alpha_test", {}).get("enabled")
                or state.get("polygon", {}).get("offset_fill")):
            missing.append("enabled raster state extends beyond the observed offscreen contract")
        for field in ("depth", "stencil"):
            if (direct.get(field) or {}).get("status") != "not_present":
                missing.append(f"verified absence of original {field} attachment")
        if not missing:
            expected = direct["color"]
            seed = _offscreen_seed(frame_dir, initial_color, direct["width"], direct["height"])
            if seed == _offscreen_bgra(frame_dir, expected):
                return {"verdict": "inapplicable_reference", "scope": scope,
                        "problems": ["original RGBA output is identical to its initialization"]}
    result = {"verdict": "missing_original_observation" if missing else "eligible",
              "scope": scope, "problems": missing}
    if not missing:
        result["initial_color"] = initial_color
        result["post_color"] = direct["color"]
    return result


def _offscreen_seed(frame: Path, initial: dict, width: int, height: int) -> bytes:
    if initial.get("source") == "direct_pre_draw":
        return (frame / "blobs" / initial["file"]).read_bytes()
    return bytes(initial["bgra"]) * (width * height)


def _offscreen_bgra(frame: Path, observation: dict) -> bytes:
    original = (frame / "blobs" / observation["file"]).read_bytes()
    if replay_input.sha256_bytes(original) != observation["sha256"]:
        raise replay_input.ReplayInputError("invalid_original_output", observation["file"])
    if observation.get("pixel_format") != "RGBA8":
        return original
    return gfx_frame_pixels.swap_red_blue(original)


def _offscreen_package(frame: Path, trace: dict, seal: dict, facts: dict, directory: Path) -> dict:
    """Adapt the verified selected transaction to the legacy package vocabulary."""
    from automation import gfx_frame

    scope = facts["scope"]
    draw = _event(trace["_events"], scope["draw"])
    attachment = draw["target"]["color_attachment"]
    width, height = (attachment[key] for key in ("width", "height"))
    expected = facts["post_color"]
    forbidden = {expected["sha256"], replay_input.sha256_bytes(_offscreen_bgra(frame, expected))}
    # The staged view belongs to the verifier. Only the checked, content-addressed
    # package produced from it is mounted in the candidate's profile.
    view = directory / "selected-input"
    blobs = view / "blobs"
    blobs.mkdir(parents=True)
    names = gfx_frame.event_input_blob_names([draw])
    names.update(item["file"] for item in draw["program"]["shaders"])
    for name in names:
        if Path(name).name != name:
            raise replay_input.ReplayInputError("invalid_input_path", name)
        source = frame / "blobs" / name
        if _sha(source) in forbidden:
            raise replay_input.ReplayInputError("reference_leak", name)
        (blobs / name).symlink_to(source)
    seed = _offscreen_seed(frame, facts["initial_color"], width, height)
    seed_path = blobs / "derived-clear.bgra"
    seed_path.write_bytes(seed)
    drawable = {"samples": 0, "sample_buffers": 0, "depth_bits": 0, "stencil_bits": 0,
                "red_bits": 8, "green_bits": 8, "blue_bits": 8, "alpha_bits": 8,
                "width": width, "height": height}
    evidence = {
        "status": "proven", "identity": {"width": width, "height": height},
        "drawable": drawable,
        "prestate": {"color": {"source": "checkpoint", "file": seed_path.name,
                               "sha256": replay_input.sha256_bytes(seed)},
                     "depth": {"source": "not_required"}, "stencil": {"source": "not_required"}},
    }
    package = replay_input.build_package(
        frame_dir=view, trace={"_events": [draw]}, seal=seal, evidence=evidence,
        checkpoint_event=scope["draw"], draw_event=scope["draw"], width=width, height=height,
        resource=scope["resource"], out_dir=directory / "inputs")
    packaged = json.loads((Path(package["path"]) / "replay-input.json").read_text())
    if forbidden.intersection(item["sha256"] for item in packaged["files"]):
        raise replay_input.ReplayInputError("reference_leak", "offscreen reference payload")
    return package


def qualify_published_candidate(repo: Path, commit: str, directory: Path,
                                source: tuple, case: dict) -> dict:
    from automation import gfx_observation
    from automation.verification_manifest import EU4_EXECUTABLE_SHA256

    frame, trace, seal, trusted = source
    if seal.get("_verified") is not True:
        return {"verdict": "invalid_provenance", "problems": ["selected frame seal is unverified"]}
    tier = gfx_observation.authority(trace, seal)
    facts = preflight_reference(frame, trace, seal, trusted, case["selection"])
    original = trace.get("gl") or {}
    gl_identity = {key: original.get(key) for key in ("vendor", "renderer", "version")}
    architecture = trace.get("process_architecture")
    context = (original.get("cgl") or {}).get("profile")
    os_build = trace.get("macos_build")
    executable_sha = trace.get("executable_sha256")
    if facts["verdict"] == "eligible" and tier == "authentic":
        if not (isinstance(os_build, str) and os_build) or not executable_sha:
            facts["problems"].append("original executable or macOS build is unobserved")
        elif executable_sha != EU4_EXECUTABLE_SHA256:
            facts.update(verdict="invalid_provenance", problems=["original executable identity differs"])
        elif os_build != subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "kern.osversion"], text=True, timeout=5).strip():
            facts.update(verdict="inapplicable_environment", problems=["original macOS build differs"])
    if facts["verdict"] == "eligible":
        if architecture not in ("arm64", "x86_64"):
            facts["problems"].append("original process architecture is unobserved")
        if not all(gl_identity.values()) or context != "legacy":
            facts["problems"].append("original legacy GL driver or context is unobserved")
        if facts["problems"]:
            facts["verdict"] = "missing_original_observation"
    provenance = {"reference": case["reference"],
                  "evidence_tier": ({"authentic": "original-eu4", "synthetic": "synthetic"}.get(tier)
                                    if facts["verdict"] != "invalid_provenance" else None),
                  "original_environment": {"architecture": architecture, **gl_identity, "profile": context,
                                           "executable_sha256": executable_sha, "macos_build": os_build},
                  "original_evidence_verified": facts["verdict"] != "invalid_provenance"}
    if facts["verdict"] != "eligible":
        return {**facts, **provenance, "execution": "not_started"}
    package = _offscreen_package(frame, trace, seal, facts, directory)
    expected = facts["post_color"]
    comparison = directory / "trusted-post.bgra"
    comparison.write_bytes(_offscreen_bgra(frame, expected))
    result = qualify_candidate(
        repo, commit, directory, Path(package["path"]), comparison,
        {"package_sha256": package["package_sha256"], "expected_sha256": _sha(comparison),
         "architecture": architecture, "gl": {**gl_identity, "context_profile": "4096"}})
    result["scope"] = {**result.get("scope", {}), **facts["scope"],
                       "initial_color": facts["initial_color"], "post_color": expected}
    return {**result, **provenance}


def _required_observations(manifest: dict) -> dict:
    """The selected-draw contract comes from the verified input, not a fixture's assertions."""
    target = manifest["drawable"]
    state = manifest["state"]
    attributes = manifest["attributes"]
    draw = manifest["draw"]
    textures = {texture["unit"]: texture for texture in manifest["textures"]}
    uniforms = {}
    for item in manifest["program"]["uniforms"]:
        if item["type"] == "sampler":
            tex = textures[item["unit"]]
            levels = tex["levels"]
            sampler = tex["sampler"]
            if (tex["target"] != "2D" or len(levels) != 1 or levels[0]["level"] != 0
                    or levels[0]["internal_format"] != "RGBA8" or levels[0]["compressed"]
                    or replay_input._gl_enum(sampler.get("min")) not in (0x2600, 0x2601)):
                raise QualificationError("unsupported_observation", "sampler requires unsupported mip or format observation")
            params = [sampler[key] for key in (
                "min", "mag", "wrap_s", "wrap_t", "base_level", "max_level",
                "compare_mode", "compare_func", "min_lod", "max_lod", "lod_bias", "anisotropy")]
            uniforms[item["name"]] = {
                "unit": item["unit"], "format": 0x8058, "width": levels[0]["width"],
                "height": levels[0]["height"], "sha256": levels[0]["sha256"],
                "sampler": [int(value, 0) if isinstance(value, str) else value for value in params],
            }
        elif item["type"] == "float":
            uniforms[item["name"]] = {key: item[key] for key in ("components", "elements", "sha256")}
        else:
            raise QualificationError("unsupported_observation", f"uniform {item['name']} has unsupported type")
    if any(item.get("divisor") or item.get("integer") or not item.get("enabled")
           for item in attributes):
        raise QualificationError("unsupported_observation", "unsupported vertex attribute binding")
    restored = {
        "mode": draw["mode"], "count": draw["count"], "linked": 1,
        "gl_error": 0, "framebuffer_status": 0x8cd5, "draw_buffer": 0x8ce0,
        "viewport": state["viewport"], "scissor_enabled": int(state["scissor"]["enabled"]),
        "blend": int(state["blend"]["enabled"]), "depth_test": int(state["depth"]["test"]),
        "stencil_test": int(state["stencil"]["test"]), "cull": int(state["cull"]["enabled"]),
        "dither": int(state["dither"]), "color_mask": state["color_mask"],
        "active_uniforms": len(uniforms), "active_attributes": len(attributes),
        "uniforms": uniforms,
        "attributes": {
            item["name"]: {"location": item["index"], "components": item["components"],
                           "type": replay_input._gl_enum(item["gl_type"]), "stride": item["stride"],
                           "enabled": 1, "normalized": int(item["normalized"]),
                           "offset": max(0, item["offset"] - item["range_begin"]),
                           "sha256": item["data_sha256"]}
            for item in attributes
        },
    }
    if not draw["indexed"]:
        restored["first"] = draw["first"] - attributes[0]["first_vertex"]
    if state["scissor"]["enabled"]:
        restored["scissor"] = state["scissor"]["box"]
    restored.update({
        "polygon_mode": [replay_input._gl_enum(value) for value in state["polygon"]["mode"]],
        "polygon_offset": int(state["polygon"]["offset_fill"]),
        "logic_enabled": int(state["logic_op"]["enabled"]),
        "multisample": int(state["multisample"]),
        "framebuffer_srgb": int(state["framebuffer_srgb"]),
        "alpha_test": int(state["alpha_test"]["enabled"]),
    })
    if state["cull"]["enabled"]:
        restored["cull_mode"] = replay_input._gl_enum(state["cull"]["mode"])
        restored["front_face"] = replay_input._gl_enum(state["cull"]["front"])
    if state["logic_op"]["enabled"]:
        restored["logic_mode"] = replay_input._gl_enum(state["logic_op"]["mode"])
    if target["depth_bits"] == target["stencil_bits"] == 0:
        restored["read_buffer"] = 0x8ce0
        restored["attachments"] = {"color_type": 0x1702, "color_level": 0,
                                   "depth_type": 0, "stencil_type": 0,
                                   "format": 0x8c43 if state["framebuffer_srgb"] else 0x8058,
                                   "width": target["width"], "height": target["height"],
                                   "samples": target["samples"]}
    return {"restore": {**restored, "submitted": 0}, "post": {**restored, "submitted": 1}}


def qualify_candidate(repo: Path, commit: str, directory: Path, package: Path, expected: Path,
                      expectation: dict) -> dict:
    """Execute the compiled candidate with trusted GL draw/readback hooks."""
    from automation import gfx_gl_control_renderer as driver
    from automation import candidate_snapshot, dsh_receipts, launch_lease, native_build, qualification_sandbox

    try:
        package_record = _verified_input_package(package, expectation["package_sha256"])
        width, height = (package_record["manifest_document"]["identity"][name]
                         for name in ("width", "height"))
        reference_sha = expectation["expected_sha256"]
        reference = _reference_pixels(expected, reference_sha, width, height)
    except (OSError, QualificationError) as exc:
        return {"verdict": "invalid_provenance", "problems": [str(exc)]}
    manifest = package_record["manifest_document"]
    try:
        observed_state = _required_observations(manifest)
    except (KeyError, IndexError, TypeError, ValueError, QualificationError) as exc:
        return {"verdict": "missing_required_expectation", "problems": [str(exc)]}
    # Apple GL reports a trailing newline for shader strings lacking one.
    shaders = {}
    for shader in manifest["program"]["shaders"]:
        body = (package / shader["path"]).read_bytes()
        if not body.endswith(b"\n"):
            body += b"\n"
        shaders[shader["stage"]] = replay_input.sha256_bytes(body)
    for phase in observed_state.values():
        phase["shader_sha256"] = shaders
    if any(item["sha256"] == reference_sha for item in manifest["files"]):
        return {"verdict": "invalid_provenance", "problems": ["GL reference bytes overlap input"]}
    seed = (package / manifest["prestate"]["color"]["path"]).read_bytes()
    seed_sha = hashlib.sha256(seed).hexdigest()
    if seed_sha == reference_sha:
        return {"verdict": "inapplicable_reference", "problems": ["draw has no attachment effect"]}
    control = directory / "control.ctl"
    control.write_text("\n".join(driver._ctl_lines(manifest, package)) + "\n")

    input_source = "renderer/gl/eu4_gfx_draw_control.c"
    staged = directory / "candidate"
    digests = candidate_snapshot.materialize_commit(repo, commit, staged, (input_source,))
    observer = repo / "renderer/gl/eu4_gfx_draw_observer.c"
    digests[str(observer.relative_to(repo))] = _sha(observer)
    clang, sdk = native_build.qualification_toolchain("clang")
    build = directory / "build"
    build.mkdir()
    executable = build / "gl-candidate"
    target = expectation["architecture"]
    if target not in ("arm64", "x86_64"):
        raise QualificationError("unsupported OpenGL build architecture")
    flags = ("-arch", target, "-O2", "-Wno-deprecated-declarations")
    redirects = ("-Dmain=eu4_candidate_draw_main",
                 "-DglDrawArrays=eu4_observed_draw_arrays",
                 "-DglDrawElements=eu4_observed_draw_elements")
    candidate_object = build / "renderer.o"
    for arguments in (
            [*redirects, "-c", staged / input_source, "-o", candidate_object],
            [observer, candidate_object, "-framework", "OpenGL", "-o", executable]):
        built = native_build.qualification_compile(
            clang, sdk, [*flags, *arguments], build=build, readable=(staged, observer))
        if built.returncode:
            return {"verdict": "execution_failure", "problems": [built.stderr[-2500:]]}

    results = {}
    executable_digest = _sha(executable)
    transitions = expectation.get("repeat_count", 1)
    if transitions not in (1, 2):
        raise QualificationError("unsupported_observation", "only bounded one- or two-draw fixtures are supported")
    order = [(n, phase) for n in range(transitions) for phase in ("restore", "post")]
    for mode in ("draw", "omit"):
        work = directory / mode
        work.mkdir()
        with launch_lease.gpu_execution():
            observed = qualification_sandbox.run(
                [executable, mode, width, height, "--control", control, "--out", work],
                writable=work, readable=(package, control), graphics="gl",
                translated=target == "x86_64")
        allowed = {"execution.json", "result.bgra", "observed-context.txt",
                   "depth-after.f32", "stencil-after.u8"}
        allowed.update(f"observed-{n}-{phase}.bgra" for n, phase in order)
        if observed.returncode:
            return {"verdict": "execution_failure", "problems": [
                f"GL worker exit {observed.returncode}: {observed.stderr[-2500:]}"]}
        outputs = dsh_receipts.worker_outputs(work, allowed)
        if _sha(executable) != executable_digest:
            return {"verdict": "execution_failure", "problems": ["GL candidate executable changed"]}
        execution = json.loads((work / "execution.json").read_text())
        if execution.get("execution") != "completed":
            return {"verdict": "unsupported_candidate",
                    "problems": execution.get("missing") or [str(execution)]}
        rows = [json.loads(line) for line in observed.stdout.splitlines()]
        if [(row.get("transition"), row.get("phase")) for row in rows] != order:
            return {"verdict": "execution_failure", "problems": [
                "trusted observer did not report the declared ordered draw boundaries"]}
        steps = []
        for n in range(transitions):
            actual = {phase: rows[n * 2 + offset] for offset, phase in enumerate(("restore", "post"))}
            restored = (work / f"observed-{n}-restore.bgra").read_bytes()
            final = (work / f"observed-{n}-post.bgra").read_bytes()
            initial = seed if n == 0 or mode == "omit" else reference
            steps.append({"observation": actual,
                          "restore": compare_payloads(initial, restored, width, height),
                          "post": compare_payloads(reference, final, width, height),
                          "post_equals_seed": replay_input.sha256_bytes(final) == seed_sha})
        observations = work / "trusted-observations.json"
        observations.write_text(json.dumps(rows) + "\n")
        receipt, execution_receipt = dsh_receipts.write_receipt(work, {
            "schema": dsh_receipts.REVISION_SCHEMA, "execution": "completed", "execution_revision": commit,
            "scope": "selected-draw", "control": mode, "architecture": target,
            "source_digests": digests, "executable_sha256": _sha(executable),
            "recipe": native_build.qualification_recipe(clang, sdk, [*flags, *redirects]),
            "package_sha256": package_record["package_sha256"],
            "outputs": outputs + [{"file": observations.name, "bytes": observations.stat().st_size,
                                   "sha256": _sha(observations)}],
        })
        environment = dict(zip(("vendor", "renderer", "version", "context_profile"),
                               (work / "observed-context.txt").read_text().splitlines(), strict=True))
        results[mode] = {**steps[-1], "steps": steps, "environment": environment,
                         "receipt": str(receipt),
                         "receipt_digest": execution_receipt["receipt_digest"],
                         "outputs": execution_receipt["outputs"],
                         "renderer_artifact_sha256": _sha(work / "result.bgra")}

    for result in results.values():
        if any(result["environment"].get(key) != value
               for key, value in expectation["gl"].items()):
            return {"verdict": "candidate_environment_mismatch", "problems": ["observed GL environment differs"]}
    differences = []
    for n, (draw, omission) in enumerate(zip(results["draw"]["steps"], results["omit"]["steps"], strict=True)):
        for phase in ("restore", "post"):
            additional = expectation.get("observed_state", {}).get(phase, {})
            for field, expected_value in (additional | observed_state[phase]).items():
                actual = draw["observation"][phase].get(field)
                if expected_value != actual:
                    differences.append({"transition": n, "phase": phase, "field": field,
                                        "expected": expected_value, "actual": actual})
            if not draw[phase]["exact"]:
                differences.append({"transition": n, "phase": phase, "field": "attachment",
                                    "statistics": draw[phase]["statistics"]})
        if (not omission["restore"]["exact"] or not omission["post_equals_seed"]
                or omission["post"]["exact"]
                or any(
                    draw["observation"][phase] !=
                    (omission["observation"][phase] | {"submitted": int(phase == "post")})
                    or omission["observation"][phase]["submitted"] != 0
                    for phase in ("restore", "post"))):
            differences.append({"transition": n, "phase": "omit", "field": "omission_control",
                                "statistics": omission["post"]["statistics"]})
    earliest = differences[0] if differences else None
    problems = [f"transition {row['transition']} {row['phase']}.{row['field']} differs"
                for row in differences]
    output_names = [
        (Path(control["receipt"]).parent / item["file"]).relative_to(directory).as_posix()
        for control in results.values() for item in control["outputs"]
    ] + [Path(control["receipt"]).relative_to(directory).as_posix() for control in results.values()]
    return {
        "verdict": "different" if problems else "exact", "evidence_tier": "synthetic",
        "outputs": dsh_receipts.artifact_records(directory, output_names),
        "recipe": native_build.qualification_recipe(clang, sdk, [*flags, *redirects]),
        "environment": results["draw"]["environment"],
        "validation_contract": {"gl_observer_sha256": _sha(repo / "renderer/gl/eu4_gfx_draw_observer.c")},
        "execution": "completed", "independently_verified": True, "candidate": {
            "executable_sha256": _sha(executable), "source_digests": digests,
            "architecture": target,
        },
        "scope": {"observed_state": sorted(observed_state["restore"]), "attachment": "BGRA8",
                  "transitions": transitions},
        "coverage": ["restoration", "submission", "post-attachment", "draw-omission"],
        "earliest_observed_divergence": earliest, "controls": results, "problems": problems,
        "reference": {"package_sha256": expectation["package_sha256"],
                      "expected_sha256": reference_sha},
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
                        help="executable that renders the control from the "
                             "restricted replay-input package and writes "
                             "result.bgra plus a signed receipt.json")
    parser.add_argument("--control-out", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--drawable",
                        help="refused: mandatory evidence is derived from the "
                             "sealed frame, never from caller JSON")
    parser.add_argument("--prestate",
                        help="refused: mandatory evidence is derived from the "
                             "sealed frame, never from caller JSON")
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
    elif args.drawable or args.prestate:
        value = _assemble(
            None, None,
            evidence=_refused_evidence(
                "caller_supplied_evidence",
                "caller-supplied --drawable/--prestate JSON does not authorize "
                "qualification; the evidence is derived from the sealed frame"))
        code = 2
    elif args.frame_dir is not None and args.renderer is not None \
            and args.checkpoint_event is not None \
            and args.draw_event is not None:
        out_dir = args.control_out
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="gl-control-"))
        value = qualify_sealed(
            args.live, args.width, args.height, frame_dir=args.frame_dir,
            checkpoint_event=args.checkpoint_event,
            draw_event=args.draw_event, renderer=args.renderer,
            out_dir=out_dir, resource=args.resource, timeout=args.timeout)
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
