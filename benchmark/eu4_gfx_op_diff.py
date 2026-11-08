"""Offline differential replay of a graphics-operation capture.

The adapter declares the equivalence scope and decides whether its prerequisite
is established. A replay never guesses a dependency from function names, and a
worker failure is never reported as a rendering mismatch.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_op_capture as capture  # noqa: E402
import eu4_gfx_shadow_worker as shadow  # noqa: E402

DIFF_SCHEMA = "gfx-op-diff/v0"
WORKER_SCHEMA = "metal-shadow/v0"
EXIT_CODES = {
    "pass": 0,
    "mismatch": 1,
    "incomplete": 2,
    "unsupported": 3,
    "unavailable": 4,
}
NEXT_ACTION = {
    "pass": "independent review may proceed; this proves only the declared scope",
    "mismatch": "inspect the diagnostics, fix Metal, rerun the offline replay",
    "unsupported": "create a bounded reconstruction task for the missing behavior",
    "incomplete": "request a new capture with the required evidence",
    "unavailable": "stop; fix the worker or environment before changing Metal",
}


class DiffUnavailable(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


class DiffUsage(RuntimeError):
    pass


@dataclass(frozen=True)
class FieldDiff:
    field: str
    original: Any
    metal: Any
    matched: bool
    note: str = ""


@dataclass(frozen=True)
class Qualification:
    qualified: bool
    reason: str = ""


@dataclass
class ReplayContext:
    startup_status: str
    startup_fields: list[FieldDiff] = field(default_factory=list)
    prior: list[dict] = field(default_factory=list)
    resources: dict[str, dict] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    teardown_done: set[str] = field(default_factory=set)
    bindings: dict[int, str] = field(default_factory=dict)


class Adapter(Protocol):
    symbol: str
    worker_op: str
    equivalence_scope: str

    def validate(self, record: capture.OperationRecord) -> str | None: ...
    def qualify(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> Qualification: ...
    def command(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> tuple[str, dict]: ...
    def followups(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> list[tuple[str, dict]]: ...
    def compare(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> list[FieldDiff]: ...
    def report(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> dict: ...


class ViewportAdapter:
    symbol = "_GfxSetViewPort"
    worker_op = "set_viewport"
    equivalence_scope = "function_cpu_state"

    def validate(self, record: capture.OperationRecord) -> str | None:
        if record.input_size != 24 or record.output_size != 24:
            return "viewport_shape"
        if len(record.input_words) != 6 or len(record.output_words) != 6:
            return "viewport_shape"
        return None

    def qualify(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> Qualification:
        if context.startup_status != "matched":
            return Qualification(False, "startup_context_not_qualified")
        return Qualification(True)

    def command(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> tuple[str, dict]:
        return "set_viewport", {"words": list(record.input_words)}

    def followups(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> list[tuple[str, dict]]:
        return []

    def compare(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> list[FieldDiff]:
        viewport = state.get("viewport")
        if (
            not isinstance(viewport, list)
            or len(viewport) != 6
            or not all(type(value) is int for value in viewport)
        ):
            raise DiffUnavailable("worker_state_shape", "viewport")
        return [
            FieldDiff(
                field=f"viewport[{index}]",
                original=expected,
                metal=viewport[index],
                matched=viewport[index] == expected,
            )
            for index, expected in enumerate(record.output_words)
        ]

    def report(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> dict:
        viewport = state.get("viewport") if isinstance(state, dict) else None
        return {
            "input": list(record.input_words),
            "expected": list(record.output_words),
            "metal": list(viewport) if isinstance(viewport, list) else None,
        }


def _payload(record: capture.OperationRecord) -> bytes | None:
    return record.payload_bytes


def _b64(data: bytes | None) -> str | None:
    if data is None:
        return None
    return base64.b64encode(data).decode("ascii")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _observed_resource(
    followups: list[tuple[str, dict]], resource_id: str
) -> dict | None:
    for op, response in followups:
        if op != "observe":
            continue
        result = response.get("result") if isinstance(response, dict) else None
        resources = result.get("resources") if isinstance(result, dict) else None
        entry = resources.get(resource_id) if isinstance(resources, dict) else None
        if isinstance(entry, dict):
            return entry
    return None


def _observed_buffer(entry: dict | None) -> tuple[bool, bytes | None]:
    if not isinstance(entry, dict):
        return False, None
    buffer = entry.get("buffer")
    if not isinstance(buffer, dict):
        return False, None
    known = buffer.get("contents_known") is True
    text = buffer.get("bytes_base64")
    if not known or not isinstance(text, str):
        return known, None
    try:
        return True, base64.b64decode(text)
    except (ValueError, TypeError):
        return False, None


def _content_fields(
    prefix: str, expected: bytes | None, metal: bytes | None
) -> list[FieldDiff]:
    if expected is None or metal is None:
        return [
            FieldDiff(
                field=f"{prefix}.contents",
                original=None if expected is None else _digest(expected),
                metal=None if metal is None else _digest(metal),
                matched=False,
                note="unknown or unavailable contents",
            )
        ]
    fields = [
        FieldDiff(
            field=f"{prefix}.length",
            original=len(expected),
            metal=len(metal),
            matched=len(expected) == len(metal),
        )
    ]
    if expected == metal:
        fields.append(
            FieldDiff(
                field=f"{prefix}.contents",
                original=_digest(expected),
                metal=_digest(metal),
                matched=True,
            )
        )
        return fields
    limit = min(len(expected), len(metal))
    offset = next((i for i in range(limit) if expected[i] != metal[i]), limit)
    fields.append(
        FieldDiff(
            field=f"{prefix}.contents",
            original=_digest(expected),
            metal=_digest(metal),
            matched=False,
            note=(
                f"first_difference_offset={offset} "
                f"original_byte={expected[offset] if offset < len(expected) else None} "
                f"metal_byte={metal[offset] if offset < len(metal) else None}"
            ),
        )
    )
    return fields


class VertexBufferCreateAdapter:
    symbol = "_GfxCreateVertexBuffer"
    worker_op = "create_vertex_buffer"
    equivalence_scope = "vertex_buffer_cpu_contents"

    def validate(self, record: capture.OperationRecord) -> str | None:
        if record.kind != "vertex_buffer_create":
            return "vertex_create_kind"
        count = record.meta.get("count")
        stride = record.meta.get("stride")
        if type(count) is not int or type(stride) is not int:
            return "vertex_create_meta"
        if count < 0 or stride < 0 or record.resource_id is None:
            return "vertex_create_meta"
        if record.payload_size != count * stride:
            return "vertex_create_payload"
        if record.contents_known and record.payload_bytes is None:
            return "vertex_create_payload"
        return None

    def qualify(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> Qualification:
        if context.startup_status != "matched":
            return Qualification(False, "startup_context_not_qualified")
        if not record.contents_known:
            return Qualification(False, "unknown_original_contents")
        return Qualification(True)

    def command(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> tuple[str, dict]:
        count = record.meta["count"]
        stride = record.meta["stride"]
        data = _payload(record) if record.contents_known else None
        context.resources[record.resource_id] = {
            "bytes": data,
            "count": count,
            "stride": stride,
            "known": record.contents_known,
        }
        if record.resource_id not in context.created:
            context.created.append(record.resource_id)
        return "create_vertex_buffer", {
            "resource_id": record.resource_id,
            "count": count,
            "stride": stride,
            "usage": record.meta.get("usage", 0),
            "data_base64": _b64(data),
        }

    def followups(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> list[tuple[str, dict]]:
        return [("observe", {"resources": [record.resource_id]})]

    def compare(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> list[FieldDiff]:
        known, metal = _observed_buffer(_observed_resource(followups, record.resource_id))
        if not known:
            raise DiffUnavailable("worker_contents_unknown", record.resource_id)
        expected = record.payload_bytes if record.contents_known else None
        if expected is None:
            raise DiffUnavailable("original_contents_unknown", record.resource_id)
        return _content_fields("contents", expected, metal)

    def report(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> dict:
        known, metal = _observed_buffer(_observed_resource(followups, record.resource_id))
        expected = record.payload_bytes if record.contents_known else None
        entry = {
            "resource_id": record.resource_id,
            "count": record.meta.get("count"),
            "stride": record.meta.get("stride"),
            "usage": record.meta.get("usage"),
        }
        return {
            "input": entry,
            "expected": {
                "contents_known": expected is not None,
                "length": None if expected is None else len(expected),
                "sha256": None if expected is None else _digest(expected),
            },
            "metal": {
                "contents_known": known,
                "length": None if metal is None else len(metal),
                "sha256": None if metal is None else _digest(metal),
            },
        }


class VertexBufferUpdateAdapter:
    symbol = "_GfxUpdateVertexBuffer"
    worker_op = "update_vertex_buffer"
    equivalence_scope = "vertex_buffer_cpu_contents"

    def validate(self, record: capture.OperationRecord) -> str | None:
        if record.kind != "vertex_buffer_update":
            return "vertex_update_kind"
        for key in ("count", "stride", "offset", "partial"):
            if type(record.meta.get(key)) is not int:
                return "vertex_update_meta"
        if record.resource_id is None:
            return "vertex_update_resource"
        if record.meta["count"] < 0 or record.meta["stride"] < 0:
            return "vertex_update_meta"
        if record.payload_size != record.meta["count"] * record.meta["stride"]:
            return "vertex_update_payload"
        return None

    def qualify(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> Qualification:
        if context.startup_status != "matched":
            return Qualification(False, "startup_context_not_qualified")
        resource = context.resources.get(record.resource_id)
        if resource is None or not resource.get("known"):
            return Qualification(False, "unknown_original_contents")
        if not record.contents_known:
            return Qualification(False, "unknown_original_contents")
        if record.payload_bytes is None:
            return Qualification(False, "missing_update_payload")
        offset = record.meta["offset"]
        if record.meta["partial"] == 1:
            if offset < 0 or offset > len(resource["bytes"]):
                return Qualification(False, "update_bounds")
            if offset + len(record.payload_bytes) > len(resource["bytes"]):
                return Qualification(False, "update_bounds")
        return Qualification(True)

    def command(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> tuple[str, dict]:
        return "update_vertex_buffer", {
            "resource_id": record.resource_id,
            "count": record.meta["count"],
            "offset": record.meta["offset"],
            "partial": record.meta["partial"],
            "data_base64": _b64(record.payload_bytes),
        }

    def followups(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> list[tuple[str, dict]]:
        return [("observe", {"resources": [record.resource_id]})]

    def compare(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> list[FieldDiff]:
        resource = context.resources[record.resource_id]
        payload = record.payload_bytes
        if payload is None:
            raise DiffUnavailable("missing_update_payload", record.resource_id)
        if record.meta["partial"] == 1:
            offset = record.meta["offset"]
            updated = (
                resource["bytes"][:offset] + payload + resource["bytes"][offset + len(payload):]
            )
        else:
            updated = payload
        known, metal = _observed_buffer(_observed_resource(followups, record.resource_id))
        if not known:
            raise DiffUnavailable("worker_contents_unknown", record.resource_id)
        resource["bytes"] = updated
        resource["count"] = record.meta["count"]
        resource["stride"] = record.meta["stride"]
        return _content_fields("contents", updated, metal)

    def report(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> dict:
        known, metal = _observed_buffer(_observed_resource(followups, record.resource_id))
        resource = context.resources.get(record.resource_id, {})
        expected = resource.get("bytes")
        return {
            "input": {
                "resource_id": record.resource_id,
                "count": record.meta.get("count"),
                "stride": record.meta.get("stride"),
                "offset": record.meta.get("offset"),
                "partial": record.meta.get("partial"),
                "sha256": None if record.payload_bytes is None else _digest(record.payload_bytes),
            },
            "expected": {
                "length": None if expected is None else len(expected),
                "sha256": None if expected is None else _digest(expected),
            },
            "metal": {
                "contents_known": known,
                "length": None if metal is None else len(metal),
                "sha256": None if metal is None else _digest(metal),
            },
        }


class VertexBufferBindAdapter:
    symbol = "_GfxSetVertexBuffers"
    worker_op = "set_vertex_buffers"
    equivalence_scope = "vertex_buffer_binding"

    def validate(self, record: capture.OperationRecord) -> str | None:
        if record.kind != "vertex_buffer_bind":
            return "vertex_bind_kind"
        if type(record.meta.get("count")) is not int or type(record.meta.get("start")) is not int:
            return "vertex_bind_meta"
        ids = record.meta.get("ids")
        if not isinstance(ids, list) or len(ids) != record.meta["count"]:
            return "vertex_bind_ids"
        return None

    def qualify(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> Qualification:
        if context.startup_status != "matched":
            return Qualification(False, "startup_context_not_qualified")
        for resource_id in record.meta["ids"]:
            if resource_id not in context.resources:
                return Qualification(False, "unknown_bound_resource")
        offsets = record.meta.get("offsets") or []
        words = record.meta.get("words") or []
        if any(type(value) is not int or value != 0 for value in [*offsets, *words]):
            return Qualification(False, "binding_offsets_not_reproducible")
        return Qualification(True)

    def command(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> tuple[str, dict]:
        return "set_vertex_buffers", {
            "start": record.meta["start"],
            "resources": list(record.meta["ids"]),
        }

    def followups(
        self, record: capture.OperationRecord, context: ReplayContext
    ) -> list[tuple[str, dict]]:
        return []

    def compare(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> list[FieldDiff]:
        slots = state.get("slots")
        if not isinstance(slots, list):
            raise DiffUnavailable("worker_state_shape", "slots")
        start = record.meta["start"]
        fields: list[FieldDiff] = []
        for index, resource_id in enumerate(record.meta["ids"]):
            slot = start + index
            entry = next(
                (item for item in slots if isinstance(item, dict) and item.get("slot") == slot),
                None,
            )
            metal = entry.get("resource_id") if isinstance(entry, dict) else None
            fields.append(
                FieldDiff(
                    field=f"binding[{slot}]",
                    original=resource_id,
                    metal=metal,
                    matched=metal == resource_id,
                )
            )
            if metal == resource_id:
                context.bindings[slot] = resource_id
        return fields

    def report(
        self,
        record: capture.OperationRecord,
        state: dict,
        followups: list[tuple[str, dict]],
        context: ReplayContext,
    ) -> dict:
        slots = state.get("slots") if isinstance(state, dict) else None
        start = record.meta.get("start")
        metal = []
        if isinstance(slots, list) and type(start) is int:
            for index, _resource_id in enumerate(record.meta.get("ids", [])):
                slot = start + index
                entry = next(
                    (item for item in slots if isinstance(item, dict) and item.get("slot") == slot),
                    None,
                )
                metal.append(entry.get("resource_id") if isinstance(entry, dict) else None)
        return {
            "input": {
                "start": start,
                "ids": list(record.meta.get("ids", [])),
                "offsets": list(record.meta.get("offsets", [])),
                "words": list(record.meta.get("words", [])),
            },
            "expected": list(record.meta.get("ids", [])),
            "metal": metal,
        }


ADAPTERS: dict[str, Adapter] = {
    "_GfxSetViewPort": ViewportAdapter(),
    "_GfxCreateVertexBuffer": VertexBufferCreateAdapter(),
    "_GfxUpdateVertexBuffer": VertexBufferUpdateAdapter(),
    "_GfxSetVertexBuffers": VertexBufferBindAdapter(),
}


def _envelope_error(resp: Any, op: str, seq: int) -> str | None:
    if not isinstance(resp, dict):
        return "non_object"
    if resp.get("schema") != WORKER_SCHEMA:
        return "bad_schema"
    if resp.get("op") != op:
        return "op_mismatch"
    if type(resp.get("seq")) is not int or resp["seq"] != seq:
        return "seq_mismatch"
    if type(resp.get("next")) is not int or resp["next"] != seq + 1:
        return "next_mismatch"
    if type(resp.get("ok")) is not bool:
        return "ok_type"
    return None


def _planned_commands(
    artifact: capture.CaptureArtifact,
) -> list[tuple[str, dict]]:
    startup = artifact.startup
    return [
        (
            "settings",
            {
                "display": startup.settings_entry["display"],
                "windowed": startup.settings_entry["windowed"],
                "samples": list(startup.settings_entry["sample_values"]),
            },
        ),
        (
            "configure_window",
            {
                "window_id": startup.window_id,
                "source": startup.window_source,
                "width": startup.init_exit["width"],
                "height": startup.init_exit["height"],
            },
        ),
        ("init", {}),
    ]


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _field_json(item: FieldDiff) -> dict:
    value = {
        "field": item.field,
        "original": item.original,
        "metal": item.metal,
        "matched": item.matched,
    }
    if item.note:
        value["note"] = item.note
    return value


def _require_int(container: dict, key: str) -> int:
    value = container.get(key)
    if type(value) is not int:
        raise DiffUnavailable("worker_startup_shape", key)
    return value


def _startup_compare(
    artifact: capture.CaptureArtifact, responses: dict[str, dict]
) -> tuple[str, list[FieldDiff]]:
    fields: list[FieldDiff] = []
    settings = responses.get("settings")
    configure = responses.get("configure_window")
    init = responses.get("init")
    if settings is None or configure is None or init is None:
        raise DiffUnavailable("worker_startup_missing", "settings/configure/init")
    configure_result = configure.get("result")
    init_result = init.get("result")
    init_state = init.get("state")
    if not isinstance(configure_result, dict) or not isinstance(init_result, dict) or not isinstance(init_state, dict):
        raise DiffUnavailable("worker_startup_shape")

    def equal(name: str, original: Any, metal: Any, note: str = "") -> None:
        fields.append(
            FieldDiff(
                field=name,
                original=original,
                metal=metal,
                matched=original == metal,
                note=note,
            )
        )

    startup = artifact.startup
    equal("configure_window.window_id", startup.window_id, _require_int(configure_result, "window_id"))
    equal("configure_window.source", startup.window_source, _require_int(configure_result, "source"))
    equal("configure_window.width", startup.init_exit["width"], _require_int(configure_result, "width"))
    equal("configure_window.height", startup.init_exit["height"], _require_int(configure_result, "height"), "conditioned_on_reference")
    equal("init.success", startup.init_exit["success"], _require_int(init_result, "success"))
    if type(init_state.get("initialized")) is not bool or type(
        init_state.get("backend_present")
    ) is not bool:
        raise DiffUnavailable("worker_startup_shape", "initialized")
    initialized = (
        init_state.get("initialized") is True
        and init_state.get("backend_present") is True
    )
    fields.append(
        FieldDiff(
            field="init.initialized",
            original={"master_non_null": True},
            metal={
                "initialized": init_state.get("initialized"),
                "backend_present": init_state.get("backend_present"),
            },
            matched=initialized,
        )
    )
    for key in ("width", "height", "source", "primitive", "dirty"):
        equal(f"init.{key}", startup.init_exit[key], _require_int(init_state, key))
    viewport = init_state.get("viewport")
    if not isinstance(viewport, list) or len(viewport) != 6 or not all(type(v) is int for v in viewport):
        raise DiffUnavailable("worker_state_shape", "viewport")
    equal("init.viewport", list(startup.init_exit["viewport"]), viewport)
    status = "matched" if all(item.matched for item in fields) else "mismatch"
    return status, fields


def _call(
    worker,
    op: str,
    args: dict,
    seq: int,
) -> tuple[dict | None, tuple[str, str] | None]:
    try:
        response = worker.call(op, args, seq)
    except (
        TimeoutError,
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        return None, ("unavailable", f"worker_failure: {exc}")
    if response is None:
        return None, ("unavailable", "worker_eof")
    reason = _envelope_error(response, op, seq)
    if reason is not None:
        return None, ("unavailable", f"worker_protocol: {reason}")
    if response["ok"] is False:
        error = str(response.get("error") or "not_ok")
        if error == "unknown_op":
            return response, ("unsupported", "worker_unknown_op")
        return response, ("unavailable", f"worker_{error}")
    if not isinstance(response.get("state"), dict):
        return response, ("unavailable", "worker_state_missing")
    if not isinstance(response.get("result"), dict):
        return response, ("unavailable", "worker_result_missing")
    return response, None


def _default_worker_factory():
    shadow.build()
    return shadow.Worker()


def run(
    capture_path: Path | str,
    output: Path | str,
    *,
    worker_factory=None,
) -> dict:
    artifact = capture.load_capture(Path(capture_path))
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise DiffUsage(f"replay output already exists: {output}")

    if artifact.op_summary.get("overlaps", 0) > 0:
        comparison = _base_comparison(artifact)
        comparison.update(
            {
                "status": "unsupported",
                "reason": "ordering_overlap",
                "operations": [],
                "diagnostics": [],
                "next_action": NEXT_ACTION["unsupported"],
            }
        )
        _write_output(output, artifact, comparison, [], [], "")
        return _result(comparison, output)

    adapters: dict[int, Adapter | None] = {}
    for record in artifact.operations:
        adapter = ADAPTERS.get(record.symbol)
        adapters[record.index] = adapter
        if adapter is not None:
            shape = adapter.validate(record)
            if shape is not None:
                comparison = _base_comparison(artifact)
                comparison.update(
                    {
                        "status": "incomplete",
                        "reason": shape,
                        "operations": [],
                        "diagnostics": [],
                        "next_action": NEXT_ACTION["incomplete"],
                    }
                )
                _write_output(output, artifact, comparison, [], [], "")
                return _result(comparison, output)

    commands: list[dict] = []
    responses: list[Any] = []
    stderr = ""
    startup_status = "unavailable"
    startup_fields: list[FieldDiff] = []
    operation_results: list[dict] = []
    diagnostics: list[dict] = []
    worker_failure: str | None = None
    worker_unsupported: str | None = None
    teardown_ok = True
    shutdown_ok = False
    worker = None

    try:
        factory = worker_factory or _default_worker_factory
        try:
            worker = factory()
        except (OSError, RuntimeError, TimeoutError) as exc:
            raise DiffUnavailable("worker_start_failed", str(exc)) from exc

        responses_by_op: dict[str, dict] = {}
        sequence = _planned_commands(artifact)
        for op, args in sequence:
            seq = worker.next_seq
            command = {"schema": WORKER_SCHEMA, "seq": seq, "op": op, "args": args}
            commands.append(command)
            response, failure = _call(worker, op, args, seq)
            responses.append(response)
            if failure is not None:
                kind, detail = failure
                if kind == "unsupported":
                    worker_unsupported = detail
                else:
                    worker_failure = detail
                break
            responses_by_op[op] = response
        if worker_failure is None and worker_unsupported is None:
            try:
                startup_status, startup_fields = _startup_compare(
                    artifact, responses_by_op
                )
            except DiffUnavailable as exc:
                worker_failure = f"{exc.reason}: {exc.detail}"
                startup_status = "unavailable"
            context = ReplayContext(
                startup_status=startup_status, startup_fields=startup_fields
            )
            for record in artifact.operations if worker_failure is None else ():
                adapter = adapters[record.index]
                if adapter is None:
                    operation_results.append(
                        _operation_result(
                            record,
                            status="unsupported",
                            reason="adapter_missing",
                        )
                    )
                    continue
                qualification = adapter.qualify(record, context)
                if not qualification.qualified:
                    operation_results.append(
                        _operation_result(
                            record,
                            adapter=adapter,
                            status="unsupported",
                            reason=f"unqualified_dependency: {qualification.reason}",
                            context=context,
                        )
                    )
                    continue
                op, args = adapter.command(record, context)
                seq = worker.next_seq
                command = {
                    "schema": WORKER_SCHEMA,
                    "seq": seq,
                    "op": op,
                    "args": args,
                }
                commands.append(command)
                response, failure = _call(worker, op, args, seq)
                responses.append(response)
                if failure is not None:
                    kind, detail = failure
                    if kind == "unsupported":
                        operation_results.append(
                            _operation_result(
                                record,
                                adapter=adapter,
                                status="unsupported",
                                reason=detail,
                                context=context,
                            )
                        )
                        continue
                    worker_failure = detail
                    operation_results.append(
                        _operation_result(
                            record,
                            adapter=adapter,
                            status="unavailable",
                            reason=detail,
                            context=context,
                        )
                    )
                    break
                state = response["state"]
                followup_results: list[tuple[str, dict]] = []
                followup_failure = None
                for follow_op, follow_args in adapter.followups(record, context):
                    follow_seq = worker.next_seq
                    follow_command = {
                        "schema": WORKER_SCHEMA,
                        "seq": follow_seq,
                        "op": follow_op,
                        "args": follow_args,
                    }
                    commands.append(follow_command)
                    follow_response, follow_error = _call(
                        worker, follow_op, follow_args, follow_seq
                    )
                    responses.append(follow_response)
                    if follow_error is not None:
                        followup_failure = follow_error
                        break
                    followup_results.append((follow_op, follow_response))
                if followup_failure is not None:
                    kind, detail = followup_failure
                    if kind == "unsupported":
                        operation_results.append(
                            _operation_result(
                                record,
                                adapter=adapter,
                                status="unsupported",
                                reason=detail,
                                context=context,
                            )
                        )
                        continue
                    worker_failure = detail
                    operation_results.append(
                        _operation_result(
                            record,
                            adapter=adapter,
                            status="unavailable",
                            reason=detail,
                            context=context,
                        )
                    )
                    break
                try:
                    fields = adapter.compare(record, state, followup_results, context)
                except DiffUnavailable as exc:
                    worker_failure = f"{exc.reason}: {exc.detail}"
                    operation_results.append(
                        _operation_result(
                            record,
                            adapter=adapter,
                            status="unavailable",
                            reason=worker_failure,
                            context=context,
                        )
                    )
                    break
                status = "matched" if all(item.matched for item in fields) else "mismatch"
                result = _operation_result(
                    record,
                    adapter=adapter,
                    status=status,
                    reason="",
                    fields=fields,
                    state=state,
                    followups=followup_results,
                    context=context,
                    command=(op, args),
                )
                operation_results.append(result)
                context.prior.append(result)
                if status == "mismatch":
                    for item in fields:
                        if not item.matched:
                            diagnostic = {
                                "status": "mismatch",
                                "symbol": record.symbol,
                                "call_id": record.call_id,
                                "field": item.field,
                                "original": item.original,
                                "metal": item.metal,
                            }
                            if item.note:
                                diagnostic["note"] = item.note
                            diagnostics.append(diagnostic)
            if worker_failure is None and context is not None:
                for resource_id in context.created:
                    if resource_id in context.teardown_done:
                        continue
                    seq = worker.next_seq
                    teardown_args = {"resource_id": resource_id}
                    command = {
                        "schema": WORKER_SCHEMA,
                        "seq": seq,
                        "op": "destroy_vertex_buffer",
                        "args": teardown_args,
                        "harness_teardown": True,
                    }
                    commands.append(command)
                    response, failure = _call(
                        worker, "destroy_vertex_buffer", teardown_args, seq
                    )
                    responses.append(response)
                    if failure is not None:
                        teardown_ok = False
                        worker_failure = f"teardown: {failure[1]}"
                        break
                    context.teardown_done.add(resource_id)
        if worker_failure is None and worker_unsupported is None and isinstance(worker, object):
            try:
                seq = worker.next_seq
                command = {"schema": WORKER_SCHEMA, "seq": seq, "op": "shutdown", "args": {}}
                commands.append(command)
                response, failure = _call(worker, "shutdown", {}, seq)
                responses.append(response)
                shutdown_ok = failure is None
            except Exception:  # noqa: BLE001
                shutdown_ok = False
    finally:
        if worker is not None:
            try:
                stderr = worker.stderr_text() or ""
            except Exception:  # noqa: BLE001
                stderr = ""
            try:
                worker.close()
            except Exception:  # noqa: BLE001
                pass

    for item in startup_fields:
        if not item.matched:
            diagnostics.append(
                {
                    "status": "mismatch",
                    "symbol": "startup",
                    "call_id": None,
                    "field": item.field,
                    "original": item.original,
                    "metal": item.metal,
                }
            )

    if worker_failure is not None:
        status = "unavailable"
        reason = worker_failure
    elif worker_unsupported is not None:
        status = "unsupported"
        reason = worker_unsupported
    elif startup_status == "mismatch":
        status = "mismatch"
        reason = "startup_context"
    elif any(result["status"] == "mismatch" for result in operation_results):
        status = "mismatch"
        first = next(
            diagnostic for diagnostic in diagnostics if diagnostic["status"] == "mismatch"
        )
        reason = str(first["field"])
    elif any(
        result["status"] in ("unsupported", "unavailable")
        for result in operation_results
    ):
        status = "unsupported"
        reason = next(
            result["reason"]
            for result in operation_results
            if result["status"] in ("unsupported", "unavailable")
        )
    elif startup_status == "matched" and all(
        result["status"] == "matched" for result in operation_results
    ):
        status = "pass"
        reason = ""
    else:
        status = "unavailable"
        reason = "worker_session_incomplete"

    comparison = _base_comparison(artifact)
    comparison.update(
        {
            "status": status,
            "reason": reason,
            "startup": {
                "status": startup_status,
                "fields": [_field_json(item) for item in startup_fields],
            },
            "operations": operation_results,
            "diagnostics": diagnostics,
            "shutdown_ok": shutdown_ok,
            "teardown_ok": teardown_ok,
            "next_action": NEXT_ACTION[status],
            "counts": _counts(operation_results),
        }
    )
    _write_output(output, artifact, comparison, commands, responses, stderr)
    return _result(comparison, output)


def _operation_result(
    record: capture.OperationRecord,
    *,
    status: str,
    reason: str,
    fields: list[FieldDiff] | None = None,
    state: dict | None = None,
    command: tuple[str, dict] | None = None,
    adapter: Adapter | None = None,
    followups: list[tuple[str, dict]] | None = None,
    context: ReplayContext | None = None,
) -> dict:
    projection: dict = {}
    if adapter is not None:
        try:
            projection = adapter.report(
                record,
                state if isinstance(state, dict) else {},
                followups or [],
                context if context is not None else ReplayContext(startup_status="unavailable"),
            )
        except Exception:  # noqa: BLE001
            projection = {}
    value = {
        "symbol": record.symbol,
        "call_id": record.call_id,
        "completion_seq": record.completion_seq,
        "scope": adapter.equivalence_scope if adapter is not None else None,
        "status": status,
        "reason": reason,
        "input": projection.get("input", list(record.input_words)),
        "expected": projection.get("expected", list(record.output_words)),
        "metal": projection.get("metal"),
        "fields": [_field_json(item) for item in fields or []],
    }
    if command is not None:
        value["worker_op"] = command[0]
    return value


def _counts(operations: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for operation in operations:
        counts[operation["status"]] = counts.get(operation["status"], 0) + 1
    return counts


def _base_comparison(artifact: capture.CaptureArtifact) -> dict:
    return {
        "schema": DIFF_SCHEMA,
        "scope": "function_cpu_state",
        "capture": {
            "path": str(artifact.root),
            "session": artifact.session,
            "pid": artifact.pid,
            "executable_sha256": artifact.executable_sha256,
            "source_hashes": artifact.source_hashes,
        },
        "coverage": artifact.coverage,
    }


def _result(comparison: dict, output: Path) -> dict:
    return {
        "status": comparison["status"],
        "reason": comparison.get("reason", ""),
        "comparison": str(output / "comparison.json"),
        "report": str(output / "report.md"),
        "output": str(output),
    }


def _write_output(
    output: Path,
    artifact: capture.CaptureArtifact,
    comparison: dict,
    commands: list[dict],
    responses: list[Any],
    stderr: str,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    (output / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    )
    (output / "report.md").write_text(_render_report(comparison))
    _write_jsonl(output / "worker-commands.jsonl", commands)
    _write_jsonl(output / "worker-responses.jsonl", responses)
    if stderr:
        (output / "worker.stderr").write_text(stderr)


def _render_report(comparison: dict) -> str:
    lines = [
        "# gfx operation differential report",
        "",
        f"- schema: `{comparison['schema']}`",
        f"- status: `{comparison['status']}`",
        f"- reason: `{comparison.get('reason', '')}`",
        f"- scope: `{comparison.get('scope', '')}`",
        f"- next action: {comparison.get('next_action', '')}",
        "",
        "A `pass` establishes only the declared CPU-state scope for the captured",
        "invocations. It does not establish GPU state, pixels, or engine-path",
        "equivalence.",
        "",
        "## Startup",
        "",
    ]
    startup = comparison.get("startup") or {}
    if startup:
        lines.append(f"- status: `{startup.get('status')}`")
        for item in startup.get("fields", []):
            lines.append(
                "| {field} | {original} | {metal} | {matched} |".format(
                    field=item["field"],
                    original=json.dumps(item["original"]),
                    metal=json.dumps(item["metal"]),
                    matched=item["matched"],
                )
            )
    lines.extend(["", "## Operations", ""])
    operations = comparison.get("operations") or []
    if not operations:
        lines.append("No operation was compared.")
    for operation in operations:
        lines.append(
            "- `{symbol}` call {call_id}: `{status}` {reason}".format(
                symbol=operation["symbol"],
                call_id=operation["call_id"],
                status=operation["status"],
                reason=operation.get("reason", ""),
            )
        )
        for item in operation.get("fields", []):
            if not item["matched"]:
                lines.append(
                    "  - {field}: original={original} metal={metal}".format(
                        field=item["field"],
                        original=json.dumps(item["original"]),
                        metal=json.dumps(item["metal"]),
                    )
                )
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            json.dumps(comparison.get("coverage", {}), indent=2, sort_keys=True),
            "",
            "## Counts",
            "",
            json.dumps(comparison.get("counts", {}), indent=2, sort_keys=True),
            "",
        ]
    )
    return "\n".join(lines)
