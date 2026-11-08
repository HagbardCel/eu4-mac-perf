"""Build the L3.2a draw packet from one passive capture.

The packet is a qualification artifact, not a replay: it establishes the
authentic ``_GfxDraw`` invocation and its effective parameters, and reports
everything it could not establish. L3.2a deliberately leaves shader, texture,
constant and render-state inputs missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT / "benchmark"))
import eu4_gfx_op_capture as capture  # noqa: E402

SCHEMA = "gfx-draw-packet/v0"
PACKET_FILE = "draw-packet.json"
DECISION_0003_CALLER = 0xEDFCD1
MASTER_SIZE = 0x171
PRIMITIVE_EVIDENCE = (
    ROOT / "analysis" / "qualification" / "v0" / "primitive-table.evidence.json"
)

REQUIRED_INPUTS = (
    "draw_invocation",
    "primitive_semantics",
    "shader_identity",
    "shader_stage_sources",
    "vertex_buffer_bindings",
    "vertex_buffer_contents",
    "vertex_attribute_layout",
    "texture_slot_0_identity",
    "texture_slot_0_contents",
    "sampler_state",
    "matrix_constant",
    "render_state_blend",
    "render_state_depth_stencil",
    "render_state_rasterizer",
    "scissor_state",
    "viewport",
    "render_target",
)

PREDICATE = (
    "caller_known && caller_rva == target_caller && master_complete && "
    "shader(master+0x70) != 0 && mode(master+8) == 5 && effective_count >= 3"
)


class PacketError(RuntimeError):
    pass


def _input(
    status: str,
    evidence: list[dict] | None = None,
    *,
    detail: str | None = None,
    assumptions: list[str] | None = None,
) -> dict:
    value: dict = {"status": status, "evidence": evidence or []}
    if detail:
        value["detail"] = detail
    if assumptions:
        value["assumptions"] = assumptions
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _i32(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<i", raw, offset)[0]


def _u32(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _u64(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", raw, offset)[0]


def parse_master(raw: bytes) -> dict:
    if len(raw) != MASTER_SIZE:
        raise PacketError(f"master snapshot is {len(raw)} bytes, expected {MASTER_SIZE}")
    viewport = [_i32(raw, 0xAC + index * 4) for index in range(6)]
    constants = []
    for slot in range(6):
        count = _u32(raw, 0xC8 + slot * 0x10)
        pointer = _u64(raw, 0xD0 + slot * 0x10)
        if count or pointer:
            constants.append({"slot": slot, "vec4_count": count, "pointer": pointer})
    vertex = []
    for slot in range(4):
        header = _u64(raw, 0x130 + slot * 8)
        offset = _u32(raw, 0x150 + slot * 4)
        stride = _u32(raw, 0x160 + slot * 4)
        if header or offset or stride:
            vertex.append(
                {
                    "slot": slot,
                    "header": header,
                    "offset": offset,
                    "stride": stride,
                }
            )
    return {
        "mode": _i32(raw, 0x08),
        "blend_hex": raw[0x0C:0x30].hex(),
        "depth_hex": raw[0x30:0x54].hex(),
        "raster_hex": raw[0x54:0x64].hex(),
        "effective_count": _i32(raw, 0x64),
        "stride_word": _i32(raw, 0x6C),
        "shader": _u64(raw, 0x70),
        "render_target": _u64(raw, 0x78),
        "window": {"width": _i32(raw, 0xA0), "height": _i32(raw, 0xA4)},
        "viewport_words": viewport,
        "gl_viewport": {
            "x": viewport[4],
            "y": viewport[5],
            "width": viewport[1],
            "height": viewport[0],
        },
        "constants": constants,
        "vertex_buffers": vertex,
        "constants_dirty": raw[0x128] != 0,
        "attribs_dirty": raw[0x170] != 0,
        "sha256": _sha256(raw),
    }


def _primitive_mapping() -> dict | None:
    try:
        value = json.loads(PRIMITIVE_EVIDENCE.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    return value


def _record_sample(record: capture.OperationRecord) -> dict:
    return {
        "call_id": record.call_id,
        "completion_seq": record.completion_seq,
        "thread_id": record.thread_id,
        "caller_rva": record.caller_rva,
        "mode": record.meta.get("mode"),
        "raw_count": record.meta.get("raw_count"),
        "effective_count": record.meta.get("effective_count"),
        "qualified": record.meta.get("qualified"),
    }


def _diagnostics(summary: dict, draws: list[capture.OperationRecord]) -> dict:
    modes = {
        str(index): count
        for index, count in enumerate(summary.get("draw_modes") or [])
        if count
    }
    return {
        "observed_draws": summary.get("draw_observed", len(draws)),
        "retained_draws": len(draws),
        "at_caller": summary.get("draw_at_caller", 0),
        "qualified": summary.get("draw_qualified", 0),
        "modes": modes,
        "counts": summary.get("draw_counts")
        or {"zero": 0, "one": 0, "two": 0, "at_least_three": 0},
        "callers": summary.get("draw_callers") or [],
        "caller_overflow": summary.get("draw_caller_overflow", 0),
        "samples": [_record_sample(record) for record in draws[-8:]],
        "qualification_predicate": PREDICATE,
        "decision_0003": {
            "caller_rva": "0xedfcd1",
            "mode": 5,
            "minimum_effective_count": 3,
        },
    }


def _provenance(
    artifact: capture.CaptureArtifact, snapshot_file: str | None
) -> dict:
    hashes = {
        name: value
        for name, value in artifact.source_hashes.items()
        if name in capture.REQUIRED_FILES or name == snapshot_file
    }
    try:
        capture_display = str(artifact.root.relative_to(ROOT))
    except ValueError:
        capture_display = str(artifact.root)
    request = None
    request_path = artifact.root / "capture-request.json"
    if request_path.is_file():
        try:
            request = json.loads(request_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            request = None
    return {
        "session": artifact.session,
        "pid": artifact.pid,
        "executable_sha256": artifact.executable_sha256,
        "image_base": f"0x{artifact.image_base:x}",
        "capture": capture_display,
        "source_hashes": hashes,
        "targets": artifact.op_summary.get("targets", []),
        "retention": artifact.op_summary.get("retention", "legacy"),
        "stop_policy": artifact.op_summary.get("stop_policy", "quota"),
        "observed": artifact.op_summary.get("observed", artifact.op_summary.get("events", 0)),
        "retained_events": artifact.op_summary.get("events", 0),
        "retention_budget_exhausted": bool(
            artifact.op_summary.get("retention_budget_exhausted")
        ),
        "payload_gaps": artifact.op_summary.get("payload_gaps", 0),
        "capture_request": request,
    }


def _unavailable(reason: str, detail: str = "") -> dict:
    packet = {
        "schema": SCHEMA,
        "status": "unavailable",
        "reason": reason,
        "packet_ready": False,
        "target": None,
        "inputs": {},
        "missing": list(REQUIRED_INPUTS),
        "diagnostics": {},
        "provenance": {},
        "assumptions": [],
        "ready_definition": (
            "the selected draw's required source inputs and effective bindings "
            "are captured and qualified, with render-target assumptions listed"
        ),
    }
    if detail:
        packet["detail"] = detail
    return packet


def build_packet(
    capture_path: Path | str, expected_caller: int = DECISION_0003_CALLER
) -> dict:
    try:
        artifact = capture.load_capture(capture_path, require_events=False)
    except capture.CaptureIncomplete as exc:
        return _unavailable(exc.reason, exc.detail)

    summary = artifact.op_summary
    symbols = {target.get("symbol") for target in summary.get("targets", [])}
    if "_GfxDraw" not in symbols:
        return _unavailable("not_a_draw_capture", "_GfxDraw is not an observed target")
    if summary.get("stop_policy") != "target_draw":
        return _unavailable("not_a_draw_probe", "stop_policy is not target_draw")
    if summary.get("retention") != "state":
        return _unavailable("not_a_draw_probe", "retention is not state")
    if summary.get("retention_budget_exhausted") is True:
        return _unavailable("retention_budget", "capture hit an observation budget")
    target_caller = summary.get("target_draw_caller", 0)
    if target_caller != expected_caller:
        return _unavailable(
            "caller_mismatch",
            f"capture target 0x{int(target_caller):x} != expected 0x{expected_caller:x}",
        )

    draws = [record for record in artifact.operations if record.symbol == "_GfxDraw"]
    qualifying = [
        record for record in draws if record.meta.get("qualified") is True
    ]
    diagnostics = _diagnostics(summary, draws)
    if not qualifying:
        packet = {
            "schema": SCHEMA,
            "status": "draw_not_qualified",
            "reason": "no_target_draw",
            "packet_ready": False,
            "target": {
                "symbol": "_GfxDraw",
                "caller_rva": f"0x{expected_caller:x}",
                "qualified": False,
            },
            "inputs": {},
            "missing": list(REQUIRED_INPUTS),
            "diagnostics": diagnostics,
            "provenance": _provenance(artifact, None),
            "assumptions": [],
            "ready_definition": (
                "the selected draw's required source inputs and effective "
                "bindings are captured and qualified, with render-target "
                "assumptions listed"
            ),
        }
        return packet

    target = qualifying[0]
    raw = target.payload_bytes
    if raw is None:
        return _unavailable("draw_payload_missing", "draw master snapshot is absent")
    try:
        master = parse_master(raw)
    except PacketError as exc:
        return _unavailable("draw_payload_invalid", str(exc))

    raw_count = target.meta.get("raw_count")
    if type(raw_count) is not int:
        return _unavailable("draw_meta_mismatch", "raw_count is not an integer")
    master_count = master["effective_count"]
    expected_effective = raw_count if raw_count > 0 else master_count
    expected_source = "esi" if raw_count > 0 else "master"
    if target.meta.get("mode") != master["mode"]:
        return _unavailable(
            "draw_meta_mismatch",
            f"mode: meta {target.meta.get('mode')} != snapshot {master['mode']}",
        )
    if target.meta.get("effective_count") != expected_effective:
        return _unavailable(
            "draw_meta_mismatch",
            f"effective_count: meta {target.meta.get('effective_count')} != "
            f"recomputed {expected_effective}",
        )
    if target.meta.get("count_source") != expected_source:
        return _unavailable(
            "draw_meta_mismatch",
            f"count_source: meta {target.meta.get('count_source')} != "
            f"recomputed {expected_source}",
        )
    if target.meta.get("shader_non_null") != (master["shader"] != 0):
        return _unavailable(
            "draw_meta_mismatch",
            "shader_non_null disagrees with the master snapshot",
        )

    snapshot_name = target.payload_file
    provenance = _provenance(artifact, snapshot_name)
    inputs: dict[str, dict] = {}
    snapshot_evidence = {
        "type": "draw_snapshot",
        "call_id": target.call_id,
        "payload": snapshot_name,
        "sha256": target.payload_sha256,
        "size": target.payload_size,
    }
    inputs["draw_invocation"] = _input(
        "established",
        [
            snapshot_evidence,
            {
                "type": "draw_record",
                "call_id": target.call_id,
                "completion_seq": target.completion_seq,
                "thread_id": target.thread_id,
                "caller_rva": target.caller_rva,
            },
        ],
    )

    primitive_enum = target.meta.get("primitive_enum")
    mapping = _primitive_mapping()
    if primitive_enum is None or primitive_enum < 0:
        inputs["primitive_semantics"] = _input(
            "missing",
            detail="no _GfxSetPrimitiveType observation preceded this draw",
        )
        primitive_detail = None
    elif mapping is None:
        inputs["primitive_semantics"] = _input(
            "unavailable", detail="primitive mapping evidence is not readable"
        )
        primitive_detail = None
    else:
        table = mapping.get("output", {}).get("mapping", {})
        expected_mode = table.get(str(primitive_enum))
        if expected_mode == master["mode"] == 5:
            inputs["primitive_semantics"] = _input(
                "established",
                [
                    {
                        "type": "primitive_set",
                        "source": "draw meta (thread-local last primitive)",
                        "call_id": target.meta.get("primitive_call_id"),
                        "primitive_enum": primitive_enum,
                    },
                    {
                        "type": "static_table",
                        "path": str(PRIMITIVE_EVIDENCE.relative_to(ROOT)),
                        "engine_enum": primitive_enum,
                        "gl_mode": expected_mode,
                    },
                ],
            )
            primitive_detail = None
        else:
            inputs["primitive_semantics"] = _input(
                "unsupported",
                detail=(
                    f"engine enum {primitive_enum} maps to {expected_mode}, "
                    f"draw mode is {master['mode']}"
                ),
            )
            primitive_detail = "primitive_table_mismatch"

    inputs["viewport"] = _input(
        "established",
        [snapshot_evidence],
    )
    inputs["viewport"]["value"] = {
        "words": master["viewport_words"],
        "gl_viewport": master["gl_viewport"],
    }

    assumptions = [
        "default backbuffer format is project-derived BGRA8Unorm (P5.1 contract)",
        "render-target contents before the draw are not captured in L3.2a",
    ]
    if master["render_target"] == 0:
        render_target = _input(
            "established",
            [snapshot_evidence],
            assumptions=assumptions,
        )
        render_target["value"] = {
            "kind": "default_backbuffer",
            "width": master["window"]["width"],
            "height": master["window"]["height"],
            "format": "BGRA8Unorm",
            "format_evidence": "project_derived",
        }
    else:
        render_target = _input(
            "incomplete",
            [
                {
                    "type": "session_local_pointer",
                    "value": f"0x{master['render_target']:x}",
                    "note": "header fields are not captured in L3.2a",
                }
            ],
        )
    inputs["render_target"] = render_target

    bindings = _input(
        "established",
        [snapshot_evidence],
    )
    bindings["value"] = {
        "slot_0_count": master["effective_count"],
        "slot_0_stride_word": master["stride_word"],
        "slots": [
            {
                "slot": item["slot"],
                "session_local_header": f"0x{item['header']:x}",
                "offset": item["offset"],
                "stride": item["stride"],
            }
            for item in master["vertex_buffers"]
        ],
        "identity": "session_local_only",
    }
    inputs["vertex_buffer_bindings"] = bindings

    for name in REQUIRED_INPUTS:
        if name in inputs:
            continue
        inputs[name] = _input("missing")

    missing = [
        name
        for name in REQUIRED_INPUTS
        if inputs[name].get("status") != "established"
    ]
    unsupported = [
        name for name in REQUIRED_INPUTS if inputs[name].get("status") == "unsupported"
    ]
    if primitive_detail:
        status = "unsupported"
        reason = primitive_detail
    elif missing:
        status = "incomplete"
        reason = "missing_inputs"
    else:
        status = "ready"
        reason = ""

    packet = {
        "schema": SCHEMA,
        "status": status,
        "reason": reason,
        "packet_ready": status == "ready",
        "target": {
            "symbol": "_GfxDraw",
            "rva": target.rva,
            "caller_rva": f"0x{expected_caller:x}",
            "qualified": True,
            "invocation": {
                "call_id": target.call_id,
                "completion_seq": target.completion_seq,
                "thread_id": target.thread_id,
            },
            "mode": master["mode"],
            "raw_count": target.meta.get("raw_count"),
            "first": target.meta.get("first"),
            "effective_count": expected_effective,
            "count_source": target.meta.get("count_source"),
            "shader_non_null": master["shader"] != 0,
            "primitive_enum": primitive_enum,
            "primitive_call_id": target.meta.get("primitive_call_id"),
            "master_sha256": master["sha256"],
        },
        "inputs": inputs,
        "missing": missing,
        "unsupported": unsupported,
        "diagnostics": diagnostics,
        "provenance": provenance,
        "assumptions": assumptions,
        "ready_definition": (
            "the selected draw's required source inputs and effective bindings "
            "are captured and qualified, with render-target assumptions listed"
        ),
    }
    return packet


def write_packet(
    capture_path: Path | str,
    output: Path | str,
    expected_caller: int = DECISION_0003_CALLER,
) -> dict:
    packet = build_packet(capture_path, expected_caller=expected_caller)
    destination = Path(output).expanduser().resolve()
    if destination.exists():
        raise PacketError(f"packet output already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    snapshot_name = None
    target = packet.get("target") or {}
    if target.get("qualified") is True:
        artifact = capture.load_capture(capture_path, require_events=False)
        target_call = target["invocation"]["call_id"]
        record = next(
            (item for item in artifact.operations if item.call_id == target_call),
            None,
        )
        if record is None or record.payload_bytes is None:
            raise PacketError("qualifying draw payload is unavailable")
        payload_dir = destination / "payloads"
        payload_dir.mkdir(exist_ok=True)
        snapshot_name = f"payloads/{target_call}-draw.bin"
        snapshot_path = destination / snapshot_name
        temporary = snapshot_path.with_name(snapshot_path.name + ".tmp")
        temporary.write_bytes(record.payload_bytes)
        os.replace(temporary, snapshot_path)
        packet.setdefault("provenance", {}).setdefault("source_hashes", {})[
            snapshot_name
        ] = _sha256(record.payload_bytes)
    packet_path = destination / PACKET_FILE
    temporary = packet_path.with_name(packet_path.name + ".tmp")
    temporary.write_text(json.dumps(packet, indent=2) + "\n")
    os.replace(temporary, packet_path)
    return {"packet": packet, "output": str(destination)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--caller-rva", type=lambda value: int(value, 0),
                        default=DECISION_0003_CALLER)
    args = parser.parse_args(argv)
    try:
        result = write_packet(
            Path(args.capture), Path(args.output), expected_caller=args.caller_rva
        )
    except PacketError as exc:
        print(f"packet refused: {exc}")
        return 2
    print(json.dumps(result["packet"], indent=2))
    return {"ready": 0, "incomplete": 2, "draw_not_qualified": 2,
            "unsupported": 3, "unavailable": 4}.get(
        result["packet"]["status"], 4
    )


if __name__ == "__main__":
    raise SystemExit(main())
