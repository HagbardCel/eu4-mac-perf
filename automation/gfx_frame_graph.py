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
    logical = [
        int((manifest.get("frame") or {}).get("width") or 0),
        int((manifest.get("frame") or {}).get("height") or 0),
    ]
    resources: dict[str, dict] = {}
    target_names: set[str] = set()
    event_rows: list[dict] = []
    unknown_events: list[dict] = []
    contract_problems: list[str] = []

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
            if "color" in writes:
                resource["has_clear"] = True
                resource["producers"].append(
                    {"event": index, "kind": "clear", "writes": writes,
                     "cleared": True}
                )
                row["writes"].extend(writes)
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

    # Coverage reasons.
    reasons: list[str] = []
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
    status = "invalid" if (
        not manifest.get("complete") or unknown_events or contract_problems
    ) else ("partial" if reasons else "complete")
    return {
        "schema": SCHEMA,
        "frame_dir": str(frame_dir),
        "status": status,
        "reasons": reasons,
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
    }


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
            "producer_events": [item["event"] for item in
                                resource.get("producers") or []
                                if item["event"] < first["event"]],
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
            plan.append({
                "event": event,
                "target": "0",
                "resource": "fb:0",
                "reference_file": image.get("file"),
                "reference_sha256": image.get("sha256"),
                "reference_width": image.get("width"),
                "reference_height": image.get("height"),
                "producer_events": [],
            })
    plan.sort(key=lambda item: (item["event"], item["target"]))
    return plan


def checkpoint_request(graph: dict) -> dict:
    """Build the deterministic checkpoint request for one graph.

    The request is derived only from the immutable captured frame.  The
    in-process coordinator publishes this document before the Metal bridge is
    released, and the bridge binds its digest into the shader receipt.
    """
    plan = checkpoint_plan(graph)
    checkpoints = []
    for item in plan:
        checkpoints.append({
            "event": item["event"],
            "target": item["target"],
            "resource": item["resource"],
            "file": f"intermediate-event{item['event']}-tex{item['target']}.bgra",
            "reference_file": item["reference_file"],
            "reference_sha256": item["reference_sha256"],
            "reference_width": item["reference_width"],
            "reference_height": item["reference_height"],
            "producer_events": item["producer_events"],
        })
    return {
        "schema": "gfx-frame-checkpoint-request/v1",
        "checkpoints": checkpoints,
    }


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
    continuous = (
        isinstance(history, dict)
        and history.get("status") == "ok"
        and int(history.get("sequence") or 0) > 0)
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
                    generation_ok = actual_generation == required
                    if continuous and required is not None:
                        # In a persistent sequence the session generation is
                        # cumulative across frames.  A carried or rewritten
                        # target must be at least as new as the frame-local
                        # producer count.
                        generation_ok = actual_generation >= required
                    if required is not None and not generation_ok:
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key, "kind": "stale_generation",
                            "expected_generation": required,
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
            elif key.startswith("tex:") and resource.get("origin") == GENERATED:
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
                if required is not None and actual_generation != required:
                    transfer_problems.append({
                        "event": event, "resource": key,
                        "kind": "stale_source_generation",
                        "expected": required,
                        "actual": actual_generation})
            if destination:
                destination_resource = resources.get(str(destination)) or {}
                producers = destination_resource.get("producers") or []
                expected_after = sum(
                    1 for producer in producers
                    if event_index(producer.get("event")) <= event)
                before = int(transfer.get("destination_generation_before") or 0)
                after = int(transfer.get("destination_generation_after") or 0)
                if (after != before + 1 or
                        (not continuous and after != expected_after) or
                        (continuous and after < expected_after)):
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
    for key in graph.get("renderer_generated_sampled") or []:
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
            if continuous and generation >= expected:
                continue
            if generation != expected:
                stale.append({
                    "event": item.get("event"),
                    "expected_generation": expected,
                    "actual_generation": generation,
                })
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
