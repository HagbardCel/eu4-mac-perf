"""Read-only projection of one captured rendering event.

The projection reuses the sealed frame trace, the existing resource graph, the
translated-shader report and the existing checkpoint reconciler.  It never
captures, replays, launches EU4 or writes a cache.  Every value is one of:
``original_gl_observed``, ``metal_observed``, ``implementation_derived`` or
``unresolved``.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from automation import (gfx_frame_graph, gfx_independent_replay,
                        gfx_observation, gfx_prefix_check, gfx_replay_case,
                        m6_frame)

SCHEMA = "gfx-event-evidence/v1"
UNIFORM_LIMIT = 1
COMPONENT_LIMIT = 2
TEXTURE_LIMIT = 8
ATTRIBUTE_LIMIT = 4
OBSERVATION_LIMIT = 2


class EventEvidenceError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _optional_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_frame(frame_dir: Path):
    try:
        trusted = gfx_prefix_check.verify_adjacent_trusted_evidence(frame_dir)
    except gfx_prefix_check.PrefixCheckError as exc:
        raise EventEvidenceError("invalid_trusted_evidence", str(exc)) from exc
    trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    trace["_trusted_evidence"] = trusted
    try:
        seal = m6_frame.load_seal(frame_dir, trace=trace,
                                  trusted_evidence=trusted)
    except m6_frame.FrameError as exc:
        if trusted is not None or (frame_dir / "frame-seal.json").is_file():
            raise EventEvidenceError("invalid_seal",
                                     f"{frame_dir}: {exc}") from exc
        seal = None
    return trace, seal


def _source(case_path: Path, *, frame_index: int | None,
            metal: str | Path | None, shaders: str | Path | None,
            verification: dict | None) -> dict:
    case_path = Path(case_path).expanduser()
    verified = bool(verification and verification.get("ok"))
    if (case_path / "case.json").is_file():
        manifest = _optional_json(case_path / "case.json")
        schema = manifest.get("schema")
        if schema not in gfx_replay_case.CASE_SCHEMAS:
            raise EventEvidenceError("unsupported_case",
                                     f"unsupported case schema: {schema!r}")
        target = True
        if schema == gfx_replay_case.SEQUENCE_SCHEMA:
            sequence = _optional_json(case_path / (manifest.get("sequence")
                                                  or "sequence.json"))
            frames = [item for item in sequence.get("frames") or []
                      if isinstance(item, dict) and item.get("dir")]
            wanted = sequence.get("target_index") if frame_index is None \
                else frame_index
            if wanted is None:
                raise EventEvidenceError(
                    "frame_required",
                    "the sequence has no single target frame; pass --frame")
            selected = [item for item in frames if item.get("index") == wanted]
            if len(selected) != 1:
                raise EventEvidenceError(
                    "frame_not_found", f"frame {wanted} is not recorded")
            entry = selected[0]
            target = wanted == sequence.get("target_index")
            frame_dir = case_path / str(entry["dir"])
        else:
            if frame_index not in (None, 1):
                raise EventEvidenceError("frame_not_found",
                                         "a single-frame case has only frame 1")
            frame_dir = case_path / str(manifest.get("capture") or "capture")
        shared = target and verified
        packaged_metal = (case_path / str(manifest["replay"])
                          if target and manifest.get("replay") else None)
        source = {
            "kind": "case", "case_path": case_path, "manifest": manifest,
            "frame_dir": frame_dir, "target_frame": target,
            "graph_path": (case_path / str(manifest.get("dependency_graph")
                                           or "dependency-graph.json"))
            if target else None,
            "metal_dir": packaged_metal if shared else None,
            "packaged_metal": packaged_metal,
            "shader_dir": (case_path / str(manifest["shaders"]))
            if shared and manifest.get("shaders") else None,
        }
    elif (case_path / "frame-trace.json").is_file():
        if frame_index not in (None, 1):
            raise EventEvidenceError("frame_not_found",
                                     "a sealed frame has only frame 1")
        source = {
            "kind": "frame", "frame_dir": case_path, "target_frame": True,
            "graph_path": None,
            "metal_dir": None, "shader_dir": None,
        }
    else:
        raise EventEvidenceError("not_found",
                                 f"no case or sealed frame at {case_path}")
    if metal is not None:
        if source["kind"] == "case" and not verified:
            source["metal_refused"] = ("case verification did not pass; "
                                       "--metal cannot bypass it")
        else:
            source["metal_dir"] = Path(metal).expanduser().resolve()
    if shaders is not None:
        source["shader_dir"] = Path(shaders).expanduser().resolve()
    return source


def _uniform_values(frame_dir: Path, uniform: dict, full: bool) -> dict:
    data = (Path(frame_dir) / "blobs" / Path(str(uniform["file"])).name
            ).read_bytes()
    kind = str(uniform.get("type"))
    is_float = kind in ("float", "mat2", "mat3", "mat4")
    components = int(uniform["components"])
    elements = int(uniform.get("elements") or 1)
    count = components * elements
    if len(data) != count * 4 or (uniform.get("bytes")
                                  and int(uniform["bytes"]) != len(data)):
        raise EventEvidenceError("malformed_uniform", str(uniform.get("name")))
    values = []
    limit = count if full else min(count, COMPONENT_LIMIT)
    for index in range(limit):
        chunk = data[index * 4:(index + 1) * 4]
        bits = "0x%08x" % int.from_bytes(chunk, "little")
        if is_float:
            value = struct.unpack("<f", chunk)[0]
            row = {"index": index, "bits": bits}
            if math.isfinite(value):
                row["value"] = value
            else:
                row["value"] = None
        else:
            row = {"index": index,
                   "value": int.from_bytes(chunk, "little", signed=True),
                   "bits": bits}
        values.append(row)
    return {
        "name": uniform.get("name"), "type": kind,
        "components": components, "elements": elements,
        "file": uniform.get("file"),
        "values": values,
        "omitted_components": max(0, count - limit),
    }


def _inputs(frame_dir: Path, event: dict, graph: dict, *,
            full: bool) -> dict:
    program = event.get("program") or {}
    uniforms = []
    for uniform in (program.get("uniforms") or []):
        if not isinstance(uniform, dict) or uniform.get("type") == "sampler":
            continue
        if len(uniforms) >= (999 if full else UNIFORM_LIMIT):
            continue
        try:
            uniforms.append(_uniform_values(frame_dir, uniform, full))
        except (OSError, KeyError, TypeError, ValueError,
                EventEvidenceError) as exc:
            uniforms.append({"name": uniform.get("name"),
                             "error": str(exc)})
    generated = set(graph.get("renderer_generated") or [])
    external = set(graph.get("external_inputs") or [])
    unproven = set(graph.get("unproven") or [])
    textures = [{
        "unit": item.get("unit"), "sampler": item.get("sampler_name"),
        "name": str(item.get("name")), "format": item.get("internal_format"),
        "level0": ((item.get("levels") or [{}])[0]).get("file"),
        "provenance": ("renderer_generated" if f"tex:{item.get('name')}"
                       in generated else "external_input"
                       if f"tex:{item.get('name')}" in external else
                       "unproven" if f"tex:{item.get('name')}" in unproven
                       else "unresolved"),
    } for item in (event.get("textures") or [])[:TEXTURE_LIMIT]
        if isinstance(item, dict)]
    return {
        "program": {
            "name": program.get("name"),
            "shaders": [{
                "stage": item.get("stage"), "sha256": item.get("sha256"),
            } for item in program.get("shaders") or []],
            "uniforms": uniforms,
        },
        "attributes": [{
            "location": item.get("location"), "name": item.get("name"),
            "components": item.get("components"), "file": item.get("data"),
        } for item in (event.get("attributes") or [])[:ATTRIBUTE_LIMIT]
            if isinstance(item, dict)],
        "indices": {key: (event.get("indices") or {}).get(key) for key in
                    ("file", "count")
                    if (event.get("indices") or {}).get(key) is not None},
        "textures": textures,
        "state": event.get("state") or {},
    }


def _metal(source: dict, graph: dict, trace: dict, seal: dict | None,
           event_index: int, target_name: str,
           observation: dict | None) -> dict:
    if not source.get("target_frame"):
        return {"correspondence": "unavailable",
                "reason": "the packaged replay belongs to the case target "
                          "frame, not the selected frame",
                }
    if source.get("metal_refused"):
        return {"correspondence": "unverified",
                "reason": source["metal_refused"]}
    if source.get("kind") == "case" and not source.get("metal_dir"):
        return {"correspondence": "unverified",
                "reason": "the case verification does not bind this capture "
                          "to an executed replay",
                }
    metal_dir = source.get("metal_dir")
    replay_path = Path(metal_dir) / "replay.json" if metal_dir else None
    if replay_path is None or not replay_path.is_file():
        return {"correspondence": "unavailable", "reason": "no replay.json",
                }
    replay = _optional_json(replay_path)
    if replay.get("status") != "ok" or replay.get("failed") != 0 \
            or replay.get("events") != len(trace.get("_events") or []) \
            or replay.get("encoded") != replay.get("events"):
        return {"correspondence": "unverified",
                "reason": "the replay did not complete every captured event",
                }
    request, _, request_problems, request_binding = (
        gfx_independent_replay.checkpoint_request_evidence(
            graph, Path(metal_dir), seal=seal))
    if request_binding.get("status") != "bound" or request_problems:
        return {"correspondence": "unverified",
                "reason": "the checkpoint request is not bound to the selected "
                          "capture"}
    key = gfx_independent_replay._checkpoint_key({
        "event": event_index, "target": target_name, "timing": "post_event"})
    requested = [item for item in request.get("checkpoints") or []
                 if isinstance(item, dict)
                 and gfx_independent_replay._checkpoint_key(item) == key]
    if len(requested) != 1:
        return {"correspondence": "unverified",
                "reason": "the selected post-event checkpoint is not uniquely "
                          "requested by the validated checkpoint request"}
    matches = [item for item in replay.get("checkpoints") or []
               if isinstance(item, dict)
               and gfx_independent_replay._checkpoint_key(item) == key]
    if len(matches) != 1:
        return {"correspondence": "unverified",
                "reason": "the replay does not contain exactly one requested "
                          "post-event checkpoint"}
    checkpoint = matches[0]
    if str(checkpoint.get("file")) != str(requested[0].get("file")):
        return {"correspondence": "unverified",
                "reason": "the replay checkpoint is not the one bound by the "
                          "validated request"}
    if observation is None:
        return {"correspondence": "unverified",
                "reason": "the selected event has no captured observation to "
                          "bind the replay checkpoint",
                }
    colour = observation.get("color") or {}
    if not colour.get("sha256") or checkpoint.get(
            "reference_sha256") != colour.get("sha256"):
        return {"correspondence": "unverified",
                "reason": "the replay checkpoint does not bind the selected "
                          "capture observation",
                }
    metal_path = Path(metal_dir) / Path(str(checkpoint.get("file"))).name
    if not metal_path.is_file():
        return {"correspondence": "unavailable",
                "reason": "the replay checkpoint payload is absent"}
    execution = _optional_json(Path(metal_dir) / "trusted-execution.json")
    provenance = False
    provenance_reason = "no trusted execution or producer receipt"
    if execution:
        problems = gfx_prefix_check.verify_execution_outputs(
            execution, Path(metal_dir))
        outputs = {item.get("file") for item in execution.get("outputs") or []
                   if isinstance(item, dict)}
        provenance = (execution.get("schema") == "gfx-trusted-execution/v1"
                      and not problems and metal_path.name in outputs
                      and execution.get("request_sha256")
                      == request_binding.get("sha256")
                      and str(Path(str(execution.get("frame_dir"))).resolve())
                      == str(Path(source["frame_dir"]).resolve()))
        provenance_reason = (problems[0] if problems else
                             "trusted execution does not bind this request/"
                             "frame/checkpoint")
    elif (source.get("kind") == "case"
          and source.get("packaged_metal")
          and Path(source["packaged_metal"]).resolve()
          == Path(metal_dir).resolve()):
        receipt = gfx_replay_case.verify_producer_receipt(
            Path(source["case_path"]), source.get("manifest") or {})
        provenance = receipt.get("status") == "verified"
        provenance_reason = "; ".join(receipt.get("problems") or [])
    if not provenance:
        return {"correspondence": "unverified",
                "reason": provenance_reason,
                "checkpoint_file": metal_path.name,
                "reference_file": colour.get("file"),
                "artifacts": {"frame_dir": str(source["frame_dir"]),
                              "metal_dir": str(metal_dir)},
                "source": "unresolved"}
    try:
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            graph, {"checkpoints": [requested[0]]},
            {"checkpoints": [checkpoint]}, Path(source["frame_dir"]),
            Path(metal_dir),
            capture=gfx_independent_replay.capture_identity(seal, trace, replay))
    except (gfx_independent_replay.ReplayError, OSError, ValueError) as exc:
        return {"correspondence": "unverified",
                "reason": f"checkpoint reconciliation failed: {exc}",
                }
    if validation.get("status") != "ok" or not results \
            or results[0].get("status") == "invalid":
        return {"correspondence": "unverified",
                "reason": "checkpoint reconciliation did not establish a valid "
                          "observation"}
    result = results[0]
    statistics = {key: (result.get("statistics") or {}).get(key) for key in
                  ("mismatched_pixels", "max_channel_error",
                   "first_differing_pixel", "differing_bounds")
                  if key in (result.get("statistics") or {})}
    metal_path = Path(str(result.get("metal_file")))
    return {
        "correspondence": "verified",
        "checkpoint_file": Path(metal_path).name,
        "checkpoint_sha256": result.get("metal_sha256"),
        "status": result.get("status"),
        "statistics": statistics,
    }


def _read_pixel(path: Path, x: int, y: int, width: int, height: int,
                pixel_format: str) -> dict:
    row = y
    with Path(path).open("rb") as handle:
        handle.seek((row * width + x) * 4)
        body = handle.read(4)
    if len(body) != 4:
        raise EventEvidenceError("pixel_read", f"{path}: short read")
    if pixel_format == "R32F":
        raw = int.from_bytes(body, "little")
        value = struct.unpack("<f", body)[0]
        return {"raw": raw, "value": value, "channels": {"R": value}}
    if pixel_format == "BGRA8":
        b, g, r, a = body
    elif pixel_format == "RGBA8":
        r, g, b, a = body
    else:
        raise EventEvidenceError("pixel_format", str(pixel_format))
    return {"channels": {"R": r, "G": g, "B": b, "A": a}}


def _pixel(observation: dict | None, metal: dict,
           coordinate: tuple[int, int],
           metal_dir: Path | None = None) -> dict | None:
    if observation is None:
        return {"status": "unavailable",
                "reason": "the selected event has no captured observation"}
    scope = gfx_observation.format_scope(observation)
    if not scope.get("supported"):
        return {"status": "not_comparable",
                "reason": "; ".join(scope.get("problems") or [])}
    x, y = coordinate
    width, height = observation.get("width"), observation.get("height")
    if not isinstance(width, int) or not isinstance(height, int) \
            or not (0 <= x < width and 0 <= y < height):
        raise EventEvidenceError(
            "pixel_out_of_range",
            f"pixel {x},{y} is outside {width}x{height}")
    colour = observation.get("color") or {}
    reference = (Path(observation.get("_frame_dir", "")) / "blobs"
                 / Path(str(colour.get("file"))).name)
    if not reference.is_file():
        return {"status": "unavailable", "reason": "observation payload absent"}
    left = _read_pixel(reference, x, y, width, height,
                       scope["pixel_format"])
    channels = (["R"] if scope["compared_channels"] == "R32F"
                else list(scope["compared_channels"]))
    result = {
        "status": "metal_unavailable",
        "coordinate": {"x": x, "y": y, "origin": observation.get("origin")},
        "compared_channels": channels,
        "original_gl": {name: left["channels"][name] for name in channels},
        "metal": None, "signed_difference": None,
    }
    if scope["pixel_format"] == "R32F":
        result["original_gl"] = {"R": left["value"]
                                 if math.isfinite(left["value"]) else None}
        result["original_bits"] = {"R": "0x%08x" % left["raw"]}
    if metal.get("correspondence") != "verified" or not metal.get(
            "checkpoint_file"):
        return result
    candidate = (Path(metal_dir) / Path(str(metal.get("checkpoint_file"))).name
                 if metal_dir is not None and metal.get("checkpoint_file")
                 else None)
    if candidate is None or not candidate.is_file():
        return result
    right = _read_pixel(candidate, x, y, width, height,
                        scope["pixel_format"])
    if scope["pixel_format"] == "R32F":
        equal = left["raw"] == right["raw"]
        result.update({
            "status": "exact" if equal else "different",
            "metal": {"R": right["value"]
                      if math.isfinite(right["value"]) else None},
            "metal_bits": {"R": "0x%08x" % right["raw"]},
            "bitwise_equal": equal,
        })
        return result
    gl = result["original_gl"]
    mt = {name: right["channels"][name] for name in channels}
    signed = {name: gl[name] - mt[name] for name in channels}
    result.update({"status": "exact" if all(v == 0 for v in signed.values())
                   else "different", "metal": mt,
                   "signed_difference": signed})
    return result


def inspect_event(case_path: Path | str, event: int, *,
                  frame_index: int | None = None,
                  metal: str | Path | None = None,
                  shaders: str | Path | None = None,
                  pixel: tuple[int, int] | None = None,
                  full: bool = False,
                  verification: dict | None = None) -> dict:
    if isinstance(event, bool) or not isinstance(event, int) or event < 0:
        raise EventEvidenceError("invalid_event",
                                 "the event index must be a non-negative integer")
    source = _source(case_path, frame_index=frame_index, metal=metal,
            shaders=shaders, verification=verification)
    result = {
        "schema": SCHEMA,
        "artifacts": {
            "frame_dir": str(source["frame_dir"]),
            "metal_dir": str(source["metal_dir"])
            if source.get("metal_dir") else None,
        },
        "missing_evidence": [],
    }
    frame_dir = Path(source["frame_dir"])
    if not frame_dir.is_dir():
        result["capture"] = {"authority": "unverified", "sealed": False}
        result["event"] = {"index": event}
        result["missing_evidence"] = [{
            "field": "captured_event_stream",
            "reason": "the case preserves diagnostics but not the captured "
                      "event stream",
        }]
        return result
    trace, seal = _load_frame(frame_dir)
    graph_path = source.get("graph_path")
    graph = (_optional_json(Path(graph_path))
             if graph_path is not None and Path(graph_path).is_file() else {})
    if not (graph.get("resources") and graph.get("events")):
        graph = gfx_frame_graph.build(
            frame_dir, trace=trace,
            trusted_evidence=trace.get("_trusted_evidence"))
    events = trace.get("_events") or []
    if event >= len(events):
        raise EventEvidenceError(
            "event_not_found",
            f"event {event} is outside 0..{len(events) - 1}")
    raw = events[event]
    row = next((item for item in graph.get("events") or []
                if item.get("event") == event), {})
    if raw.get("kind") in ("resolve", "copy") and raw.get("destination") \
            not in (None, "", "0"):
        target_key = f"tex:{raw['destination']}"
        attachment = {"internal_format": raw.get("internal_format"),
                      "width": raw.get("destination_width") or raw.get("width"),
                      "height": raw.get("destination_height") or raw.get("height")}
    else:
        target_key, attachment = gfx_frame_graph._target_resource(
            raw.get("target") or {})
        target_key = row.get("target") or target_key
    target_name = target_key.split(":", 1)[1] if ":" in target_key else "0"
    observation = next((record for record in
                        (graph.get("prefix") or {}).get("post_events") or []
                        if isinstance(record, dict)
                        and record.get("event") == event
                        and str(record.get("target_name")) == target_name), None)
    if observation is not None:
        observation["_frame_dir"] = str(frame_dir)
    metal_view = _metal(source, graph, trace, seal, event, target_name,
                        observation)
    shader_dir = next((Path(candidate) for candidate in (
        source.get("shader_dir"), frame_dir / "shaders",
        Path(source["metal_dir"]) / "shaders" if source.get("metal_dir") else None,
        Path(source["metal_dir"]).parent / "shaders"
        if source.get("metal_dir") else None)
        if candidate is not None
        and (Path(candidate) / "shaders.json").is_file()), None)
    translated = None
    if shader_dir is not None:
        report = _optional_json(shader_dir / "shaders.json")
        translated = next((item for item in report.get("programs") or []
                           if isinstance(item, dict)
                           and item.get("program")
                           == (raw.get("program") or {}).get("name")), None)
    draw = raw.get("draw") or {}
    result["capture"] = {
        "authority": (gfx_observation.authority(trace, seal) if seal
                      else "synthetic" if trace.get("test_only")
                      else "unverified"),
        "sealed": seal is not None,
        "session_id": (seal or {}).get("session_id"),
        "capture_frame_id": (seal or {}).get("capture_frame_id"),
        "event_stream_sha256": (seal or {}).get("event_stream_sha256"),
        "reference_sha256": (seal or {}).get("reference_sha256"),
    }
    result["event"] = {
        "index": event,
        "kind": raw.get("kind"),
        "entry": draw.get("entry") or row.get("entry"),
        "caller_rva": draw.get("caller_rva") or row.get("caller_rva"),
        "target": target_key, "format": attachment.get("internal_format"),
        "width": attachment.get("width") or raw.get("target", {}).get("width"),
        "height": attachment.get("height") or raw.get("target", {}).get("height"),
    }
    result["original_gl"] = _inputs(frame_dir, raw, graph, full=full)
    observed = [record for record in
                (graph.get("prefix") or {}).get("post_events") or []
                if isinstance(record, dict)
                and str(record.get("target_name")) == target_name]
    result["observations"] = [{
        "event": record.get("event"), "timing": record.get("timing"),
        "kind": record.get("kind"),
        "file": (record.get("color") or {}).get("file"),
    } for record in observed[:999 if full else OBSERVATION_LIMIT]]
    raw_uniforms = [item for item in
                    (raw.get("program") or {}).get("uniforms") or []
                    if isinstance(item, dict) and item.get("type") != "sampler"]
    result["omitted"] = {
        "uniforms": max(0, len(raw_uniforms) - len(
            result["original_gl"]["program"]["uniforms"])),
        "attributes": max(0, len(raw.get("attributes") or []) - len(
            result["original_gl"]["attributes"])),
        "textures": max(0, len(raw.get("textures") or []) - len(
            result["original_gl"]["textures"])),
        "observations": max(0, len(observed) - len(result["observations"])),
    }
    if translated is not None:
        captured = {item.get("stage"): item.get("sha256")
                    for item in (raw.get("program") or {}).get("shaders") or []}
        metal_view["translated_shader"] = {
            "program": translated.get("program"),
            "stages": {name: "exact" if stage.get("source_sha256")
                       and stage.get("source_sha256") == captured.get(name)
                       else "different" if captured.get(name) else "unavailable"
                       for name, stage in (translated.get("stages")
                                           or {}).items()},
        }
    result["metal"] = metal_view
    divergence = {
        "status": metal_view.get("status") if metal_view.get(
            "correspondence") == "verified" else "unverified",
        "statistics": metal_view.get("statistics") or {},
        "first_divergent_pixel": None,
        "requested_pixel": None,
    }
    first = (metal_view.get("statistics") or {}).get("first_differing_pixel")
    if metal_view.get("correspondence") == "verified" and isinstance(first, dict):
        divergence["first_divergent_pixel"] = _pixel(
            observation, metal_view, (first["x"], first["y"]),
            source.get("metal_dir"))
    if pixel is not None:
        divergence["requested_pixel"] = _pixel(
            observation, metal_view, pixel, source.get("metal_dir"))
    first_view = divergence.get("first_divergent_pixel")
    requested_view = divergence.get("requested_pixel")
    if first_view and requested_view and first_view.get(
            "coordinate") == requested_view.get("coordinate"):
        divergence["requested_pixel"] = {
            "same_as": "first_divergent_pixel",
            "coordinate": requested_view.get("coordinate"),
        }
    result["divergence"] = divergence
    missing = []
    if seal is None:
        missing.append({"field": "frame_seal", "reason": "not sealed"})
    if graph.get("status") not in ("complete", "partial"):
        missing.append({"field": "resource_graph",
                        "reason": "; ".join(graph.get("reasons") or
                                            ["unavailable"])})
    if metal_view.get("correspondence") != "verified":
        missing.append({"field": "metal_correspondence",
                        "reason": metal_view.get("reason") or "not established"})
    if observation is None:
        missing.append({"field": "original_gl_observation",
                        "reason": "not captured for this event"})
    if (raw.get("program") or {}).get("name") is not None:
        if translated is None:
            missing.append({"field": "translated_shader",
                            "reason": "not captured for the program"})
        missing += [
            {"field": "metal_uniform_values", "reason": "not recorded by the replay"},
            {"field": "metal_effective_state", "reason": "not recorded by the replay"},
            {"field": "shader_stage_outputs", "reason": "not captured"},
        ]
    result["missing_evidence"] = missing
    if full:
        result["captured_event"] = raw
    return result
