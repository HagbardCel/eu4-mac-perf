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


def build(frame_dir: Path | str) -> dict:
    """Derive the resource graph and coverage classification for one frame."""
    from automation import m6_frame

    frame_dir = Path(frame_dir)
    try:
        trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
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
        row = {"index": index, "kind": kind, "target": key,
               "samples": [], "writes": []}
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
            if operation != "glCopyTexSubImage2D":
                contract_problems.append(
                    f"unsupported copy operation at event {index}: {operation!r}")
            if event.get("source_level") not in (0, None) or event.get(
                    "destination_level") not in (0, None):
                contract_problems.append(
                    f"unsupported mip level at event {index}")
            if event.get("source_resolved") is False or event.get(
                    "destination_resolved") is False:
                contract_problems.append(
                    f"unresolved copy source or destination at event {index}")
            if source_fbo == 0 and event.get("read_buffer") not in (
                    0x0405, None):
                contract_problems.append(
                    f"default-framebuffer copy does not read GL_BACK at "
                    f"event {index}")
            if source in ("", "0") or source_fbo == 0:
                source_key = "fb:0"
            else:
                source_key = f"tex:{source}"
            sampled = _resource(resources, source_key, kind="framebuffer"
                                if source_key.startswith("fb:") else "texture")
            sampled["consumers"].append(
                {"event": index, "unit": None, "sampler": kind})
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
                     "destination_level": event.get("destination_level")})
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
    plan.sort(key=lambda item: (item["event"], item["target"]))
    return plan


def write_checkpoint_request(graph: dict, path: Path | str) -> dict:
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
    value = {
        "schema": "gfx-frame-checkpoint-request/v1",
        "checkpoints": checkpoints,
    }
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
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
                    if required is not None and int(
                            row.get("generation") or 0) != required:
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key, "kind": "stale_generation",
                            "expected_generation": required,
                            "actual_generation": int(row.get("generation") or 0)})
                elif resource.get("origin") == EXTERNAL:
                    if source != "external_capture":
                        consumer_problems.append({
                            "event": event, "sampler": sampler,
                            "resource": key, "kind": "external_not_captured"})
    if consumer_problems:
        reasons.append(
            f"{len(consumer_problems)} expected consumer binding(s) failed")
        status = "invalid"
    seeded: list[dict] = []
    for key in graph.get("renderer_generated_sampled") or []:
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
        status = "invalid" if any(item.get("external_bindings") for item in seeded) else "partial"
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
