"""Read and validate a live graphics-operation capture.

The artifact is the unchanged gfx-startup/v0 session plus the gfx-op sidecar.
Every check here is about evidence, not Metal: a failure means the capture
cannot establish the expected behavior, so callers must report `incomplete`.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET = json.loads((ROOT / "reverse_engineering" / "target.json").read_text())
PINNED_SHA = TARGET["sha256"]

CAPTURE_SCHEMA = "gfx-startup/v0"
OP_SCHEMA = "gfx-op/v0"
OP_SUMMARY_SCHEMA = "gfx-op-summary/v0"
REQUIRED_FILES = (
    "startup.status",
    "metadata.json",
    "summary.json",
    "events.jsonl",
    "operations-summary.json",
    "operations.jsonl",
)


class CaptureIncomplete(RuntimeError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def _require(condition: bool, reason: str, detail: str = "") -> None:
    if not condition:
        raise CaptureIncomplete(reason, detail)


def _exact_int(value: Any) -> bool:
    return type(value) is int


def _exact_bool(value: Any) -> bool:
    return type(value) is bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CaptureIncomplete("invalid_json", f"{path.name}: {exc}") from exc
    _require(isinstance(value, dict), "invalid_json", path.name)
    return value


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeError) as exc:
        raise CaptureIncomplete("invalid_jsonl", f"{path.name}: {exc}") from exc
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureIncomplete(
                "invalid_jsonl", f"{path.name}:{number}: {exc}"
            ) from exc
        _require(isinstance(value, dict), "invalid_jsonl", f"{path.name}:{number}")
        rows.append(value)
    return rows


def _read_status(path: Path) -> dict[str, str]:
    try:
        text = path.read_text()
    except (OSError, UnicodeError) as exc:
        raise CaptureIncomplete("invalid_status", str(exc)) from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def decode_window(window_hex: str) -> tuple[int, int]:
    _require(isinstance(window_hex, str), "invalid_window")
    try:
        raw = bytes.fromhex(window_hex)
    except ValueError as exc:
        raise CaptureIncomplete("invalid_window", str(exc)) from exc
    _require(len(raw) == 32, "invalid_window", f"length {len(raw)}")
    return (
        struct.unpack_from("<I", raw, 0)[0],
        struct.unpack_from("<I", raw, 0x18)[0],
    )


def decode_words(hex_text: str, size: int) -> tuple[int, ...]:
    _require(isinstance(hex_text, str), "invalid_blob")
    try:
        raw = bytes.fromhex(hex_text)
    except ValueError as exc:
        raise CaptureIncomplete("invalid_blob", str(exc)) from exc
    _require(len(raw) == size, "invalid_blob", f"length {len(raw)} != {size}")
    if size % 4 != 0:
        return ()
    return tuple(struct.unpack_from(f"<{size // 4}i", raw, 0))


@dataclass(frozen=True)
class OperationRecord:
    index: int
    call_id: int
    completion_seq: int
    symbol: str
    rva: str
    thread_id: int
    caller_rva: int | None
    caller_known: bool
    overlap: bool
    fields_ok: bool
    input_hex: str
    output_hex: str
    input_size: int
    output_size: int
    input_words: tuple[int, ...]
    output_words: tuple[int, ...]
    return_value: int | None
    output_base_non_null: bool
    kind: str
    resource_id: str | None
    contents_known: bool
    meta: dict
    payload_file: str | None
    payload_size: int
    payload_sha256: str | None
    payload_bytes: bytes | None
    raw: dict


@dataclass(frozen=True)
class StartupContext:
    settings_entry: dict
    settings_exit: dict
    init_entry: dict
    init_exit: dict
    init_call_id: int
    window_id: int
    window_source: int


@dataclass(frozen=True)
class CaptureArtifact:
    root: Path
    session: str
    pid: int
    executable_sha256: str
    image_base: int
    startup: StartupContext
    operations: tuple[OperationRecord, ...]
    summary: dict
    op_summary: dict
    source_hashes: dict[str, str]
    coverage: dict


def _validate_events(root: Path) -> tuple[StartupContext, list[dict]]:
    events = _read_jsonl(root / "events.jsonl")
    _require(len(events) == 4, "startup_event_count", str(len(events)))
    expected = (
        ("settings", "entry"),
        ("settings", "exit"),
        ("init", "entry"),
        ("init", "exit"),
    )
    for index, ((site, phase), event) in enumerate(zip(expected, events), 1):
        _require(event.get("schema") == CAPTURE_SCHEMA, "bad_event_schema", str(index))
        _require(event.get("site") == site, "bad_event_site", str(index))
        _require(event.get("phase") == phase, "bad_event_phase", str(index))
        _require(event.get("fields_ok") is True, "bad_event_fields", str(index))
        _require(event.get("seq") == index, "bad_event_seq", str(index))
    settings_entry, settings_exit, init_entry, init_exit = events
    _require(
        settings_entry.get("call_id") == settings_exit.get("call_id"),
        "settings_call_id",
    )
    _require(
        init_entry.get("call_id") == init_exit.get("call_id"), "init_call_id"
    )
    _require(_exact_int(init_exit.get("call_id")), "init_call_id_type")
    for key in ("display", "windowed", "sample_count"):
        _require(_exact_int(settings_entry.get(key)), "settings_input_type", key)
    values = settings_entry.get("sample_values")
    flags = settings_exit.get("sample_flags")
    _require(isinstance(values, list), "settings_samples_type")
    _require(isinstance(flags, list), "settings_flags_type")
    _require(
        settings_entry.get("sample_count") == len(values) == len(flags),
        "settings_sample_length",
    )
    _require(all(_exact_int(v) for v in values), "settings_sample_type")
    _require(all(_exact_int(f) and f in (0, 1) for f in flags), "settings_flag_type")
    for key in ("byte_10", "span", "constant"):
        _require(_exact_int(settings_exit.get(key)), "settings_output_type", key)
    _require(init_entry.get("window_ok") is True, "missing_window")
    window_id, window_source = decode_window(init_entry.get("window_hex"))
    _require(init_exit.get("success") == 1, "init_not_successful")
    _require(init_exit.get("master_non_null") is True, "init_master_missing")
    viewport = init_exit.get("viewport")
    _require(
        isinstance(viewport, list)
        and len(viewport) == 6
        and all(_exact_int(v) for v in viewport),
        "init_viewport_shape",
    )
    for key in ("width", "height", "source", "primitive", "dirty"):
        _require(_exact_int(init_exit.get(key)), "init_output_type", key)
    return (
        StartupContext(
            settings_entry=settings_entry,
            settings_exit=settings_exit,
            init_entry=init_entry,
            init_exit=init_exit,
            init_call_id=init_exit["call_id"],
            window_id=window_id,
            window_source=window_source,
        ),
        events,
    )


def _validate_summary(root: Path, metadata: dict, status: dict[str, str]) -> dict:
    summary = _read_json(root / "summary.json")
    _require(summary.get("schema") == CAPTURE_SCHEMA, "bad_summary_schema")
    _require(summary.get("complete") is True, "startup_incomplete")
    _require(summary.get("state") == 2, "startup_not_sealed")
    for key in ("overflow", "unmatched", "unavailable", "in_flight"):
        _require(summary.get(key) == 0, "startup_counter", key)
    _require(summary.get("events_copied") is True, "events_not_copied")
    _require(summary.get("event_count") == 4, "startup_event_count")
    _require(summary.get("settings_calls") == 1, "ambiguous_settings")
    _require(summary.get("init_calls") == 1, "ambiguous_init")
    _require(summary.get("qualifying_init") == 1, "ambiguous_qualifying_init")
    _require(status.get("state") == "installed", "status_not_installed")
    _require(str(metadata.get("pid")) == status.get("pid"), "pid_mismatch")
    _require(metadata.get("session") == status.get("session"), "session_mismatch")
    _require(
        metadata.get("executable_sha256") == PINNED_SHA,
        "executable_sha_mismatch",
    )
    _require(status.get("module") == PINNED_SHA, "module_sha_mismatch")
    return summary


def _validate_op_summary(
    root: Path, metadata: dict, init_call_id: int, *, require_events: bool = True
) -> dict:
    summary = _read_json(root / "operations-summary.json")
    _require(summary.get("schema") == OP_SUMMARY_SCHEMA, "bad_op_summary_schema")
    _require(summary.get("enabled") is True, "operations_not_enabled")
    _require(summary.get("session") == metadata.get("session"), "op_session_mismatch")
    _require(summary.get("pid") == metadata.get("pid"), "op_pid_mismatch")
    _require(
        summary.get("executable_sha256") == metadata.get("executable_sha256"),
        "op_sha_mismatch",
    )
    _require(
        summary.get("image_base") == metadata.get("image_base"),
        "op_image_base_mismatch",
    )
    _require(summary.get("state") == "sealed", "op_not_sealed")
    _require(summary.get("integrity_ok") is True, "op_integrity", str(summary.get("reason", "")))
    _require(summary.get("records_copied") is True, "op_records_not_copied")
    _require(_exact_int(summary.get("events")), "op_events_type")
    _require(require_events is False or summary["events"] >= 1, "op_no_events")
    for key in ("unmatched", "unavailable", "overflow", "in_flight"):
        _require(summary.get(key) == 0, "op_counter", key)
    retention = summary.get("retention", "legacy")
    _require(retention in ("legacy", "state"), "op_retention", str(retention))
    stop_policy = summary.get("stop_policy", "quota")
    _require(stop_policy in ("quota", "target_draw"), "op_stop_policy", str(stop_policy))
    _require(_exact_int(summary.get("observed", summary["events"])), "op_observed_type")
    _require(
        _exact_bool(summary.get("retention_budget_exhausted", False)),
        "op_retention_budget",
    )
    _require(_exact_int(summary.get("payload_gaps", 0)), "op_payload_gaps")
    _require(
        _exact_bool(summary.get("target_draw_qualified", False)),
        "op_target_qualified_type",
    )
    target_caller = summary.get("target_draw_caller", 0)
    _require(_exact_int(target_caller) and target_caller >= 0, "op_target_caller")
    target_call = summary.get("target_draw_call_id", 0)
    _require(_exact_int(target_call) and target_call >= 0, "op_target_call")
    for key in (
        "draw_observed",
        "draw_at_caller",
        "draw_qualified",
        "primitive_sets",
        "draw_caller_overflow",
    ):
        _require(_exact_int(summary.get(key, 0)) and summary.get(key, 0) >= 0, "op_draw_counter", key)
    modes = summary.get("draw_modes")
    if modes is None:
        modes = [0] * 16
    _require(
        isinstance(modes, list)
        and len(modes) == 16
        and all(_exact_int(value) and value >= 0 for value in modes),
        "op_draw_modes",
    )
    counts = summary.get("draw_counts", {})
    _require(isinstance(counts, dict), "op_draw_counts")
    for key in ("zero", "one", "two", "at_least_three"):
        _require(_exact_int(counts.get(key, 0)) and counts.get(key, 0) >= 0, "op_draw_count", key)
    callers = summary.get("draw_callers", [])
    _require(isinstance(callers, list), "op_draw_callers")
    for item in callers:
        _require(isinstance(item, dict), "op_draw_caller_shape")
        _require(_exact_int(item.get("rva")) and _exact_int(item.get("count")), "op_draw_caller_value")
    _require(summary.get("init_seen") is True, "op_init_not_seen")
    _require(summary.get("init_call_id") == init_call_id, "op_init_call_id")
    _require(_exact_int(summary.get("overlaps")), "op_overlaps_type")
    _require(_exact_int(summary.get("capacity")), "op_capacity_type")
    _require(_exact_int(summary.get("max_events")), "op_max_events_type")
    targets = summary.get("targets")
    _require(isinstance(targets, list) and targets, "op_targets")
    seen = set()
    for target in targets:
        _require(isinstance(target, dict), "op_target_shape")
        symbol = target.get("symbol")
        rva = target.get("rva")
        _require(isinstance(symbol, str) and symbol, "op_target_symbol")
        _require(isinstance(rva, str) and rva.startswith("0x"), "op_target_rva")
        _require(symbol not in seen, "op_target_duplicate")
        seen.add(symbol)
    return summary


def _validate_records(
    root: Path, summary: dict, init_call_id: int
) -> tuple[OperationRecord, ...]:
    rows = _read_jsonl(root / "operations.jsonl")
    _require(len(rows) == summary["events"], "op_event_count", str(len(rows)))
    targets = {
        item["symbol"]: item["rva"].lower() for item in summary["targets"]
    }
    records: list[OperationRecord] = []
    seen_calls: set[int] = set()
    payload_hashes: dict[str, str] = {}
    for index, row in enumerate(rows, 1):
        _require(row.get("schema") == OP_SCHEMA, "op_schema", str(index))
        call_id = row.get("call_id")
        completion = row.get("completion_seq")
        _require(_exact_int(call_id), "op_call_id", str(index))
        _require(_exact_int(completion), "op_completion", str(index))
        _require(completion == index, "op_completion_order", str(index))
        _require(call_id > init_call_id, "op_before_init", str(index))
        _require(call_id not in seen_calls, "op_duplicate_call", str(index))
        seen_calls.add(call_id)
        symbol = row.get("symbol")
        rva = row.get("rva")
        _require(isinstance(symbol, str) and symbol in targets, "op_symbol", str(index))
        _require(
            isinstance(rva, str) and rva.lower() == targets[symbol],
            "op_rva",
            str(index),
        )
        _require(row.get("caller_known") is True, "op_caller_unknown", str(index))
        _require(_exact_int(row.get("caller_rva")), "op_caller_rva", str(index))
        _require(_exact_int(row.get("thread_id")), "op_thread_id", str(index))
        _require(_exact_bool(row.get("overlap")), "op_overlap_type", str(index))
        _require(row.get("fields_ok") is True, "op_fields", str(index))
        _require(_exact_bool(row.get("output_base_non_null")), "op_output_base")
        input_size = row.get("input_size")
        output_size = row.get("output_size")
        _require(_exact_int(input_size), "op_input_size", str(index))
        _require(_exact_int(output_size), "op_output_size", str(index))
        input_hex = row.get("input_hex")
        output_hex = row.get("output_hex")
        _require(
            isinstance(input_hex, str) and len(input_hex) == 2 * input_size,
            "op_input_hex",
            str(index),
        )
        _require(
            isinstance(output_hex, str) and len(output_hex) == 2 * output_size,
            "op_output_hex",
            str(index),
        )
        return_value = row.get("return")
        _require(
            return_value is None or _exact_int(return_value),
            "op_return_type",
            str(index),
        )
        kind = row.get("kind", "operation")
        _require(isinstance(kind, str) and kind, "op_kind", str(index))
        resource_id = row.get("resource_id")
        _require(resource_id is None or isinstance(resource_id, str), "op_resource", str(index))
        contents = row.get("contents_known", True)
        _require(_exact_bool(contents), "op_contents_known", str(index))
        payload = row.get("payload")
        payload_file = None
        payload_size = 0
        payload_sha = None
        payload_bytes = None
        if payload is not None:
            _require(isinstance(payload, dict), "op_payload", str(index))
            payload_file = payload.get("file")
            payload_size = payload.get("size")
            _require(
                isinstance(payload_file, str) and payload_file,
                "op_payload_file",
                str(index),
            )
            _require(_exact_int(payload_size) and payload_size >= 0, "op_payload_size", str(index))
            relative = Path(payload_file)
            _require(
                not relative.is_absolute()
                and ".." not in relative.parts
                and relative.parts
                and relative.parts[0] == "payloads",
                "op_payload_path",
                str(index),
            )
            path = (root / relative).resolve()
            _require(
                str(path).startswith(str(root.resolve()) + "/"),
                "op_payload_path",
                str(index),
            )
            _require(path.is_file(), "op_payload_missing", str(index))
            data = path.read_bytes()
            _require(len(data) == payload_size, "op_payload_length", str(index))
            payload_bytes = data
            import hashlib
            payload_sha = hashlib.sha256(data).hexdigest()
            payload_hashes[payload_file] = payload_sha
        meta = {
            key: row[key]
            for key in (
                "count",
                "stride",
                "usage",
                "offset",
                "partial",
                "start",
                "ids",
                "offsets",
                "words",
                "offsets_null",
                "words_null",
                "header_non_null",
                "mode",
                "raw_count",
                "first",
                "effective_count",
                "count_source",
                "shader_non_null",
                "master_complete",
                "caller_match",
                "qualified",
                "primitive_enum",
                "primitive_call_id",
            )
            if key in row
        }
        if symbol == "_GfxDraw":
            _require(kind == "draw", "op_draw_kind", str(index))
            for key in ("mode", "raw_count", "first", "effective_count",
                        "primitive_enum", "primitive_call_id"):
                _require(_exact_int(meta.get(key)), "op_draw_int", key)
            for key in ("shader_non_null", "master_complete", "caller_match",
                        "qualified"):
                _require(_exact_bool(meta.get(key)), "op_draw_bool", key)
            _require(meta.get("count_source") in ("esi", "master", "unknown"),
                     "op_draw_count_source", str(index))
            _require(payload_bytes is not None and payload_size == 0x171,
                     "op_draw_payload", str(index))
        elif symbol == "_GfxSetPrimitiveType":
            _require(kind == "primitive_set", "op_primitive_kind", str(index))
            _require(_exact_int(meta.get("primitive_enum")), "op_primitive_enum",
                     str(index))
        records.append(
            OperationRecord(
                index=index,
                call_id=call_id,
                completion_seq=completion,
                symbol=symbol,
                rva=rva,
                thread_id=row.get("thread_id") if _exact_int(row.get("thread_id")) else 0,
                caller_rva=row.get("caller_rva"),
                caller_known=True,
                overlap=row.get("overlap"),
                fields_ok=row.get("fields_ok"),
                input_hex=input_hex,
                output_hex=output_hex,
                input_size=input_size,
                output_size=output_size,
                input_words=decode_words(input_hex, input_size),
                output_words=decode_words(output_hex, output_size),
                return_value=return_value,
                output_base_non_null=row.get("output_base_non_null"),
                kind=kind,
                resource_id=resource_id,
                contents_known=contents,
                meta=meta,
                payload_file=payload_file,
                payload_size=payload_size,
                payload_sha256=payload_sha,
                payload_bytes=payload_bytes,
                raw=row,
            )
        )
    if summary["overlaps"] == 0:
        call_ids = [record.call_id for record in records]
        _require(call_ids == sorted(call_ids), "op_call_order")
    draw_rows = [record for record in records if record.symbol == "_GfxDraw"]
    qualifying = [
        record for record in draw_rows if record.meta.get("qualified") is True
    ]
    if summary.get("stop_policy", "quota") == "target_draw":
        _require(
            len(qualifying) <= 1,
            "op_draw_qualified_ambiguous",
            str(len(qualifying)),
        )
        if summary.get("target_draw_qualified") is True:
            _require(bool(qualifying), "op_draw_qualified_missing")
            _require(
                qualifying[0].call_id == summary.get("target_draw_call_id"),
                "op_draw_qualified_call_id",
            )
        else:
            _require(not qualifying, "op_draw_qualified_without_target")
    _require(
        summary.get("draw_observed", 0) >= len(draw_rows),
        "op_draw_observed",
    )
    return tuple(records)


def load_capture(path: Path | str, *, require_events: bool = True) -> CaptureArtifact:
    root = Path(path).expanduser().resolve()
    _require(root.is_dir(), "missing_capture", str(root))
    hashes: dict[str, str] = {}
    for name in REQUIRED_FILES:
        file = root / name
        _require(file.is_file(), "missing_file", name)
        hashes[name] = sha256_file(file)
    status = _read_status(root / "startup.status")
    metadata = _read_json(root / "metadata.json")
    _require(metadata.get("schema") == CAPTURE_SCHEMA, "metadata_schema")
    _require(metadata.get("executable_sha256") == PINNED_SHA, "executable_sha")
    image_base_text = metadata.get("image_base")
    _require(isinstance(image_base_text, str), "image_base_type")
    try:
        image_base = int(image_base_text, 0)
    except ValueError as exc:
        raise CaptureIncomplete("image_base_type", image_base_text) from exc
    v0_summary = _validate_summary(root, metadata, status)
    context, _ = _validate_events(root)
    _require(
        context.init_call_id > 0,
        "init_call_id",
    )
    op_summary = _validate_op_summary(
        root, metadata, context.init_call_id, require_events=require_events
    )
    operations = _validate_records(root, op_summary, context.init_call_id)
    for record in operations:
        if record.payload_file is not None and record.payload_sha256 is not None:
            hashes[record.payload_file] = record.payload_sha256
    coverage = {
        "events": len(operations),
        "observed": op_summary.get("observed", len(operations)),
        "retention": op_summary.get("retention", "legacy"),
        "targets_seen": sorted({record.symbol for record in operations}),
        "distinct_inputs": len(
            {(record.symbol, record.input_hex) for record in operations}
        ),
        "overlaps": op_summary["overlaps"],
    }
    return CaptureArtifact(
        root=root,
        session=str(metadata.get("session", "")),
        pid=int(metadata.get("pid", -1)),
        executable_sha256=str(metadata.get("executable_sha256", "")),
        image_base=image_base,
        startup=context,
        operations=operations,
        summary=v0_summary,
        op_summary=op_summary,
        source_hashes=hashes,
        coverage=coverage,
    )
