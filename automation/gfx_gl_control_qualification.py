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
The shipped authority registry is therefore empty until a renderer is
implemented, independently reviewed and pinned.
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

from automation import gfx_ed25519
from automation import gfx_gl_replay_input as replay_input
from automation import m6_compare

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


def _sha(path: Path) -> str:
    return replay_input.sha256_bytes(Path(path).read_bytes())


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
    ``default_checkpoint`` record, not a mutable request sidecar.  The live
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
        before = replay_input.verify_package(package["path"])
        if before["package_sha256"] != package["package_sha256"]:
            return fail("input package changed before execution")
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
        after = replay_input.verify_package(package["path"])
        if after["package_sha256"] != package["package_sha256"]:
            fail("input package changed during execution")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return fail(
                f"control renderer failed with exit {completed.returncode}"
                + (f": {detail[-500:]}" if detail else ""))
        artifact = run_dir / "result.bgra"
        receipt_path = run_dir / "receipt.json"
        import os as os_module
        import stat as stat_module
        for path, label in ((receipt_path, "receipt"), (artifact, "artifact")):
            if path.is_symlink():
                return fail(f"control {label} is a symlink, not a regular file")
            if path.exists():
                try:
                    info = os_module.lstat(path)
                except OSError:
                    return fail(f"control {label} cannot be inspected")
                if not stat_module.S_ISREG(info.st_mode):
                    return fail(
                        f"control {label} is not a regular file")
                if path.resolve().parent != run_dir.resolve():
                    return fail(
                        f"control {label} escaped the renderer run directory")
        if artifact.exists():
            try:
                if os_module.stat(artifact).st_ino == os_module.stat(
                        live_path).st_ino and os_module.stat(
                            artifact).st_dev == os_module.stat(
                                live_path).st_dev:
                    return fail(
                        "control artifact is the sealed live image (hardlink)")
            except OSError:
                return fail("control artifact or live image cannot be inspected")
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
    except (OSError, ValueError, QualificationError,
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
    qualified = bool(
        comparison is not None and comparison["exact"] and authenticated
        and evidence_proven and authority.get("qualified") and receipt_verified
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
            "receipt_verified": receipt_verified,
            "reasons": reasons,
            "identity": identity,
            "frame_dir": (authentication or {}).get("frame_dir"),
            "seal": (authentication or {}).get("seal_document"),
            "evidence": evidence,
            "evidence_sha256": replay_input.sha256_bytes(
                _canonical({
                    "status": evidence.get("status"),
                    "drawable": evidence.get("drawable"),
                    "prestate": evidence.get("prestate"),
                    "missing": evidence.get("missing"),
                    "unsupported": evidence.get("unsupported"),
                    "identity": evidence.get("identity"),
                })),
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

    def _within(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    # The renderer may walk its working directory and the package ancestors.
    # Keeping the control output outside the sealed capture tree (and outside
    # every ancestor of it) is what makes reference isolation structural rather
    # than a promise about the request document.
    capture_root = frame_resolved.parent
    if _within(out_resolved, capture_root) or _within(
            capture_root, out_resolved):
        return _assemble(
            None, None,
            evidence=_refused_evidence(
                "unsafe_output_directory",
                f"control output {out_resolved} overlaps the sealed capture "
                f"tree {capture_root}; choose a directory outside it"))
    for sub in ("replay-input", "renderer-run"):
        candidate = out_resolved / sub
        if candidate.is_symlink():
            return _assemble(
                None, None,
                evidence=_refused_evidence(
                    "unsafe_output_directory",
                    f"control subdirectory {candidate} is a symlink"))
        if candidate.exists() and _within(candidate.resolve(), capture_root):
            return _assemble(
                None, None,
                evidence=_refused_evidence(
                    "unsafe_output_directory",
                    f"control subdirectory {candidate} resolves inside the "
                    "sealed capture tree"))
    authentication = _authenticate(
        frame_dir, live_path, width, height,
        checkpoint_event=checkpoint_event, draw_event=draw_event,
        resource=resource)
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
    except replay_input.ReplayInputError as exc:
        return _assemble(
            None, _public_authentication(authentication),
            evidence=_refused_evidence(exc.kind, exc.detail))
    public_authentication = _public_authentication(authentication)
    if evidence.get("status") != "proven":
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    try:
        live_bytes = live_path.read_bytes()
    except OSError as exc:
        public_authentication["reasons"].append(
            f"live image is unreadable: {exc}")
        public_authentication["authenticated"] = False
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    live_sha256 = hashlib.sha256(live_bytes).hexdigest()
    if live_sha256 != authentication["identity"].get(
            "checkpoint_reference_sha256"):
        public_authentication["reasons"].append(
            "live image changed after authentication")
        public_authentication["authenticated"] = False
        return _assemble(
            None, public_authentication, evidence=evidence,
            authority=_load_authority(None))
    expected = width * height * 4
    if len(live_bytes) != expected:
        public_authentication["reasons"].append(
            f"live image has {len(live_bytes)} bytes, expected {expected}")
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
    except (OSError, replay_input.ReplayInputError) as exc:
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
    checked = qualification.get("qualification") or {}
    identity = checked.get("identity") or {}
    comparison = qualification.get("comparison") or {}
    authority = _load_authority(checked.get("renderer_sha256"))
    if authority.get("qualified") is not True:
        return False
    embedded = checked.get("renderer_authority") or {}
    if embedded.get("renderer_sha256") != checked.get("renderer_sha256"):
        return False
    if embedded.get("name") != authority.get("name"):
        return False
    if embedded.get("public_key") != authority.get("public_key"):
        return False
    renderer_path = checked.get("renderer_path")
    renderer_sha = checked.get("renderer_sha256")
    if not isinstance(renderer_path, str) or not renderer_path:
        return False
    renderer_file = Path(renderer_path)
    if renderer_file.is_symlink() or not renderer_file.is_file():
        return False
    if _sha_or_none(renderer_file) != renderer_sha:
        return False
    request_ref = checked.get("request") or {}
    request_path = Path(str(request_ref.get("path") or ""))
    if not request_path.is_file() or _sha_or_none(request_path) != \
            request_ref.get("sha256"):
        return False
    try:
        request_document = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(request_document, dict):
        return False
    receipt_ref = checked.get("receipt") or {}
    control_ref = checked.get("control") or {}
    receipt_path = Path(str(receipt_ref.get("path") or ""))
    control_path = Path(str(control_ref.get("path") or ""))
    live_path = Path(str((comparison.get("live") or {}).get("path") or ""))
    frame_dir = checked.get("frame_dir")
    if not receipt_path.is_file() or not control_path.is_file() \
            or not live_path.is_file() or not isinstance(frame_dir, str) \
            or not frame_dir:
        return False
    if _sha_or_none(receipt_path) != receipt_ref.get("sha256"):
        return False
    control_sha = _sha_or_none(control_path)
    if control_sha != control_ref.get("sha256"):
        return False
    if control_sha != (comparison.get("control") or {}).get("sha256"):
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
    evidence = derive_evidence(
        authentication, checkpoint_event=checkpoint_event,
        draw_event=draw_event, width=width, height=height, resource=resource)
    if evidence.get("status") != "proven":
        return False
    embedded_evidence = checked.get("evidence")
    if not isinstance(embedded_evidence, dict):
        return False
    embedded_digest = replay_input.sha256_bytes(_canonical({
        "status": embedded_evidence.get("status"),
        "drawable": embedded_evidence.get("drawable"),
        "prestate": embedded_evidence.get("prestate"),
        "missing": embedded_evidence.get("missing"),
        "unsupported": embedded_evidence.get("unsupported"),
        "identity": embedded_evidence.get("identity"),
    }))
    if embedded_digest != checked.get("evidence_sha256"):
        return False
    derived_digest = replay_input.sha256_bytes(_canonical({
        "status": evidence.get("status"),
        "drawable": evidence.get("drawable"),
        "prestate": evidence.get("prestate"),
        "missing": evidence.get("missing"),
        "unsupported": evidence.get("unsupported"),
        "identity": evidence.get("identity"),
    }))
    if derived_digest != embedded_digest:
        return False
    package_ref = checked.get("input_package") or {}
    package_path = Path(str(package_ref.get("path") or ""))
    if not package_path.is_dir():
        return False
    try:
        package = replay_input.verify_package(package_path)
    except (OSError, replay_input.ReplayInputError):
        return False
    if package["package_sha256"] != package_ref.get("package_sha256"):
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
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(receipt, dict):
        return False
    reasons, _unsupported, execution = _validate_receipt(
        receipt, nonce=str(checked.get("nonce") or ""),
        renderer_sha=str(checked.get("renderer_sha256") or ""),
        authority=authority, identity=derived,
        package_sha256=package["package_sha256"], width=width,
        height=height, draw_event=draw_event,
        checkpoint_event=checkpoint_event, resource=resource,
        artifact=control_path, evidence=evidence)
    if reasons or execution != "completed":
        return False
    if not (
            receipt.get("nonce") == checked.get("nonce")
            and (receipt.get("renderer") or {}).get("sha256")
            == checked.get("renderer_sha256")
            and receipt.get("execution") == "completed"
            and str(receipt.get("api") or "").lower() == ORIGINAL_API):
        return False
    try:
        live_bytes = live_path.read_bytes()
        control_bytes = control_path.read_bytes()
    except OSError:
        return False
    if hashlib.sha256(live_bytes).hexdigest() != derived.get(
            "checkpoint_reference_sha256"):
        return False
    if hashlib.sha256(control_bytes).hexdigest() != control_sha:
        return False
    try:
        recomputed = compare_payloads(
            live_bytes, control_bytes, width, height,
            live_path=live_path, control_path=control_path)
    except QualificationError:
        return False
    return recomputed.get("exact") is True


def attribution(qualification: dict) -> dict:
    """Project qualification into the only permitted blame decision."""
    if not isinstance(qualification, dict):
        raise QualificationError("invalid_qualification")
    comparison = qualification.get("comparison")
    checked = qualification.get("qualification")
    try:
        evidence_ok = _evidence_recheck(qualification)
    except (AttributeError, TypeError, ValueError, OSError):
        # A malformed projection is not evidence. Attribution is a decision
        # boundary, so it refuses instead of raising into its caller.
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
