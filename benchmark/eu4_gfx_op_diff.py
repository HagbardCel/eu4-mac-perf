"""Offline differential replay of a graphics-operation capture.

The adapter declares the equivalence scope and decides whether its prerequisite
is established. A replay never guesses a dependency from function names, and a
worker failure is never reported as a rendering mismatch.
"""

from __future__ import annotations

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


class Adapter(Protocol):
    symbol: str
    worker_op: str
    equivalence_scope: str

    def validate(self, record: capture.OperationRecord) -> str | None: ...
    def qualify(self, context: ReplayContext) -> Qualification: ...
    def command(self, record: capture.OperationRecord) -> tuple[str, dict]: ...
    def compare(
        self, record: capture.OperationRecord, state: dict
    ) -> list[FieldDiff]: ...


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

    def qualify(self, context: ReplayContext) -> Qualification:
        if context.startup_status != "matched":
            return Qualification(False, "startup_context_not_qualified")
        return Qualification(True)

    def command(self, record: capture.OperationRecord) -> tuple[str, dict]:
        return "set_viewport", {"words": list(record.input_words)}

    def compare(
        self, record: capture.OperationRecord, state: dict
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


ADAPTERS: dict[str, Adapter] = {
    "_GfxSetViewPort": ViewportAdapter(),
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
                qualification = adapter.qualify(context)
                if not qualification.qualified:
                    operation_results.append(
                        _operation_result(
                            record,
                            status="unsupported",
                            reason=f"unqualified_dependency: {qualification.reason}",
                        )
                    )
                    continue
                op, args = adapter.command(record)
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
                                status="unsupported",
                                reason=detail,
                            )
                        )
                        continue
                    worker_failure = detail
                    operation_results.append(
                        _operation_result(
                            record,
                            status="unavailable",
                            reason=detail,
                        )
                    )
                    break
                state = response["state"]
                try:
                    fields = adapter.compare(record, state)
                except DiffUnavailable as exc:
                    worker_failure = f"{exc.reason}: {exc.detail}"
                    operation_results.append(
                        _operation_result(
                            record,
                            status="unavailable",
                            reason=worker_failure,
                        )
                    )
                    break
                status = "matched" if all(item.matched for item in fields) else "mismatch"
                result = _operation_result(
                    record,
                    status=status,
                    reason="",
                    fields=fields,
                    state=state,
                    command=(op, args),
                )
                operation_results.append(result)
                context.prior.append(result)
                if status == "mismatch":
                    for item in fields:
                        if not item.matched:
                            diagnostics.append(
                                {
                                    "status": "mismatch",
                                    "symbol": record.symbol,
                                    "call_id": record.call_id,
                                    "field": item.field,
                                    "original": item.original,
                                    "metal": item.metal,
                                }
                            )
        if worker_failure is None and isinstance(worker, object):
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
) -> dict:
    value = {
        "symbol": record.symbol,
        "call_id": record.call_id,
        "completion_seq": record.completion_seq,
        "scope": getattr(ADAPTERS.get(record.symbol), "equivalence_scope", None),
        "status": status,
        "reason": reason,
        "input": list(record.input_words),
        "expected": list(record.output_words),
        "metal": list(state.get("viewport", [])) if isinstance(state, dict) else None,
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
