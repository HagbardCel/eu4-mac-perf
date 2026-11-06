#!/usr/bin/env python3
"""Normalize PR2 gfx-startup captures and differentially compare Metal shadow init."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

REF_SCHEMA = "gfx-startup-ref/v0"
DIFF_SCHEMA = "gfx-startup-diff/v0"
CAPTURE_SCHEMA = "gfx-startup/v0"
WORKER_SCHEMA = "metal-shadow/v0"
PINNED_SHA = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

EXPECTED_HASHES = {
    "events.jsonl": "2282329b5e8604342938285ae3a05105ad64d58eb4f078ee39d926039a55788c",
    "summary.json": "3a09ce8073b7010a318d87e8a3b5eaebcbba6a819f00638a7e9b3238bad0c82a",
    "metadata.json": "0642a06d076731d7597957e2d8b6a0a7af4c96908acc9e51168e7f428b3190f0",
    "startup.status": "9ec9c802810554b66005044fd2fd1c5ed274abfdf5edec6ac39b0ba256c34f84",
}

REFERENCE_PATH = ROOT / "analysis" / "gfx-startup" / "v0" / "reference.json"
EXPECTED_OPS = ("settings", "configure_window", "init", "shutdown")


class DiffError(Exception):
    pass


def is_exact_int(value: Any) -> bool:
    return type(value) is int


def is_exact_bool(value: Any) -> bool:
    return type(value) is bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def flags_for(samples: list[int], supports: list[Any]) -> list[int]:
    threshold = 2
    flags: list[int] = []
    for sample, supported in zip(samples, supports):
        if sample > threshold and threshold <= 0x3FFFFFFF:
            threshold <<= 1
        flags.append(1 if sample == threshold and supported is True else 0)
    return flags


def parse_status(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def decode_window(window_hex: str) -> tuple[int, int]:
    raw = bytes.fromhex(window_hex)
    if len(raw) != 32:
        raise DiffError("window_hex must be 32 bytes")
    window_id = struct.unpack_from("<I", raw, 0)[0]
    source = struct.unpack_from("<I", raw, 0x18)[0]
    return int(window_id), int(source)


def _require_event(event: dict, site: str, phase: str, seq: int) -> None:
    if event.get("schema") != CAPTURE_SCHEMA:
        raise DiffError(f"bad schema on {site}/{phase}")
    if event.get("site") != site or event.get("phase") != phase:
        raise DiffError(f"expected {site}/{phase}")
    if event.get("fields_ok") is not True:
        raise DiffError(f"fields_ok false on {site}/{phase}")
    if not is_exact_int(event.get("seq")) or event["seq"] != seq:
        raise DiffError(f"bad seq on {site}/{phase}")


def normalize_capture(
    source_dir: Path,
    *,
    expected_hashes: dict[str, str] | None = None,
) -> dict:
    expected = expected_hashes if expected_hashes is not None else EXPECTED_HASHES
    paths = {name: source_dir / name for name in expected}
    for name, path in paths.items():
        if not path.is_file():
            raise DiffError(f"missing {name}")
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    for name, want in expected.items():
        if hashes[name] != want:
            raise DiffError(f"hash mismatch for {name}: {hashes[name]} != {want}")

    summary = json.loads(paths["summary.json"].read_text())
    metadata = json.loads(paths["metadata.json"].read_text())
    status = parse_status(paths["startup.status"])
    if not summary.get("complete"):
        raise DiffError("summary incomplete")
    if summary.get("state") != 2:
        raise DiffError("not sealed")
    for key in ("overflow", "unmatched", "unavailable", "in_flight"):
        if summary.get(key, 0) != 0:
            raise DiffError(f"nonzero {key}")
    if not summary.get("events_copied", True):
        raise DiffError("events not copied")
    if status.get("state") != "installed":
        raise DiffError("status not installed")
    if metadata.get("session") != status.get("session"):
        raise DiffError("session mismatch")

    exe_sha = metadata.get("executable_sha256")
    if exe_sha != PINNED_SHA:
        raise DiffError("executable sha mismatch")
    if status.get("module") != exe_sha:
        raise DiffError("status module sha mismatch")
    if str(metadata.get("pid")) != status.get("pid"):
        raise DiffError("pid mismatch")

    events = [
        json.loads(line)
        for line in paths["events.jsonl"].read_text().splitlines()
        if line.strip()
    ]
    if summary.get("event_count") != len(events):
        raise DiffError("event_count mismatch")
    if len(events) != 4:
        raise DiffError("unsupported_sequence")

    want = (
        ("settings", "entry"),
        ("settings", "exit"),
        ("init", "entry"),
        ("init", "exit"),
    )
    for idx, ((site, phase), event) in enumerate(zip(want, events), start=1):
        _require_event(event, site, phase, idx)

    s_entry, s_exit, i_entry, i_exit = events
    if s_entry.get("call_id") != s_exit.get("call_id"):
        raise DiffError("settings call_id mismatch")
    if i_entry.get("call_id") != i_exit.get("call_id"):
        raise DiffError("init call_id mismatch")
    settings_entries = sum(
        1 for e in events if e["site"] == "settings" and e["phase"] == "entry"
    )
    settings_exits = sum(
        1 for e in events if e["site"] == "settings" and e["phase"] == "exit"
    )
    init_entries = sum(1 for e in events if e["site"] == "init" and e["phase"] == "entry")
    init_exits = sum(1 for e in events if e["site"] == "init" and e["phase"] == "exit")
    if settings_entries != settings_exits or settings_entries != summary.get("settings_calls"):
        raise DiffError("settings call count mismatch")
    if init_entries != init_exits or init_entries != summary.get("init_calls"):
        raise DiffError("init call count mismatch")
    if (
        i_exit.get("success") != 1
        or i_exit.get("master_non_null") is not True
        or i_exit.get("fields_ok") is not True
    ):
        raise DiffError("no qualifying init")
    if s_entry.get("sample_count") != s_exit.get("sample_count"):
        raise DiffError("sample_count mismatch")
    if s_entry.get("sample_values") != s_exit.get("sample_values"):
        raise DiffError("sample_values mismatch")
    values = s_entry.get("sample_values")
    flags = s_exit.get("sample_flags")
    if not isinstance(values, list) or not is_exact_int(s_entry.get("sample_count")):
        raise DiffError("invalid sample_values")
    if s_entry["sample_count"] != len(values) or not isinstance(flags, list):
        raise DiffError("invalid sample_values")
    if s_exit.get("sample_captured") != len(flags):
        raise DiffError("invalid sample_flags")
    if not i_entry.get("window_ok") or not i_entry.get("window_hex"):
        raise DiffError("missing window_hex")

    window_id, source = decode_window(i_entry["window_hex"])
    return {
        "schema": REF_SCHEMA,
        "capture_schema": CAPTURE_SCHEMA,
        "normalizer_schema": REF_SCHEMA,
        "executable_sha256": exe_sha,
        "source_hashes": hashes,
        "settings": {
            "input": {
                "display": s_entry["display"],
                "windowed": s_entry["windowed"],
                "sample_count": s_entry["sample_count"],
                "sample_values": list(values),
            },
            "output": {
                "return": s_exit["return"],
                "sample_flags": list(flags),
                "byte_10": s_exit["byte_10"],
                "anisotropy": s_exit["anisotropy"],
                "span": s_exit["span"],
                "constant": s_exit["constant"],
            },
        },
        "init": {
            "input": {"window_id": window_id, "source": source},
            "output": {
                "success": i_exit["success"],
                "width": i_exit["width"],
                "height": i_exit["height"],
                "source": i_exit["source"],
                "primitive": i_exit["primitive"],
                "viewport": list(i_exit["viewport"]),
                "dirty": i_exit["dirty"],
                "lod": i_exit["lod"],
                "max_texture": i_exit["max_texture"],
                "blend_present": bool(i_exit["blend_present"]),
                "depth_present": bool(i_exit["depth_present"]),
                "raster_present": bool(i_exit["raster_present"]),
                "gl_context_present": bool(i_exit["gl_context_present"]),
                "master_non_null": bool(i_exit["master_non_null"]),
            },
        },
    }


def validate_reference(reference: dict) -> None:
    if not isinstance(reference, dict):
        raise DiffError("reference not an object")
    if reference.get("schema") != REF_SCHEMA:
        raise DiffError("bad reference schema")
    if reference.get("capture_schema") != CAPTURE_SCHEMA:
        raise DiffError("bad capture_schema")
    if reference.get("normalizer_schema") != REF_SCHEMA:
        raise DiffError("bad normalizer_schema")
    if reference.get("executable_sha256") != PINNED_SHA:
        raise DiffError("executable sha mismatch")
    if reference.get("source_hashes") != EXPECTED_HASHES:
        raise DiffError("source_hashes mismatch")
    try:
        s_in = reference["settings"]["input"]
        s_out = reference["settings"]["output"]
        i_in = reference["init"]["input"]
        i_out = reference["init"]["output"]
    except (KeyError, TypeError) as exc:
        raise DiffError("missing reference sections") from exc

    for key in ("display", "windowed", "sample_count"):
        if not is_exact_int(s_in.get(key)):
            raise DiffError(f"settings.input.{key} type")
    values = s_in.get("sample_values")
    flags = s_out.get("sample_flags")
    if not isinstance(values, list) or not isinstance(flags, list):
        raise DiffError("sample arrays missing")
    if s_in["sample_count"] != len(values) or len(values) != len(flags):
        raise DiffError("sample length mismatch")
    if not all(is_exact_int(v) for v in values):
        raise DiffError("sample_values type")
    if not all(is_exact_int(f) and f in (0, 1) for f in flags):
        raise DiffError("sample_flags type")
    for key in ("byte_10", "anisotropy", "span", "constant", "return"):
        if not is_exact_int(s_out.get(key)):
            raise DiffError(f"settings.output.{key} type")
    for key in ("window_id", "source"):
        if not is_exact_int(i_in.get(key)):
            raise DiffError(f"init.input.{key} type")
    for key in (
        "success",
        "width",
        "height",
        "source",
        "primitive",
        "dirty",
        "lod",
        "max_texture",
    ):
        if not is_exact_int(i_out.get(key)):
            raise DiffError(f"init.output.{key} type")
    viewport = i_out.get("viewport")
    if not isinstance(viewport, list) or len(viewport) != 6:
        raise DiffError("viewport shape")
    if not all(is_exact_int(v) for v in viewport):
        raise DiffError("viewport type")
    for key in (
        "blend_present",
        "depth_present",
        "raster_present",
        "gl_context_present",
        "master_non_null",
    ):
        if not is_exact_bool(i_out.get(key)):
            raise DiffError(f"init.output.{key} type")
    if i_out["success"] != 1 or i_out["master_non_null"] is not True:
        raise DiffError("reference init not successful")


def _envelope_basics(resp: dict, op: str, seq: int) -> str | None:
    if resp.get("schema") != WORKER_SCHEMA:
        return "bad_schema"
    if resp.get("op") != op:
        return "op_mismatch"
    if not is_exact_int(resp.get("seq")) or resp["seq"] != seq:
        return "seq_mismatch"
    if not is_exact_int(resp.get("next")) or resp["next"] != seq + 1:
        return "next_mismatch"
    if not is_exact_bool(resp.get("ok")):
        return "ok_type"
    return None


def validate_response_stream(responses: list[Any]) -> dict:
    """Return {outcome, reason, failed_op, failed_index}."""
    if not isinstance(responses, list):
        return {
            "outcome": "invalid_stream",
            "reason": "responses_not_list",
            "failed_op": None,
            "failed_index": None,
        }
    if len(responses) == 0:
        return {
            "outcome": "execution_failed",
            "reason": "empty_stream",
            "failed_op": "settings",
            "failed_index": 0,
        }
    if len(responses) > 4:
        return {
            "outcome": "invalid_stream",
            "reason": "too_many_responses",
            "failed_op": None,
            "failed_index": None,
        }

    seen: set[str] = set()
    for index, resp in enumerate(responses):
        expected_op = EXPECTED_OPS[index]
        expected_seq = index + 1

        if resp is None:
            if index + 1 != len(responses):
                return {
                    "outcome": "invalid_stream",
                    "reason": "none_not_terminal",
                    "failed_op": expected_op,
                    "failed_index": index,
                }
            return {
                "outcome": "execution_failed",
                "reason": "missing_response",
                "failed_op": expected_op,
                "failed_index": index,
            }

        if not isinstance(resp, dict):
            return {
                "outcome": "invalid_stream",
                "reason": "non_object_response",
                "failed_op": expected_op,
                "failed_index": index,
            }
        basic = _envelope_basics(resp, expected_op, expected_seq)
        if basic:
            return {
                "outcome": "invalid_stream",
                "reason": basic,
                "failed_op": expected_op,
                "failed_index": index,
            }
        if expected_op in seen:
            return {
                "outcome": "invalid_stream",
                "reason": "duplicate_op",
                "failed_op": expected_op,
                "failed_index": index,
            }
        seen.add(expected_op)

        if resp["ok"] is False:
            if index + 1 != len(responses):
                return {
                    "outcome": "invalid_stream",
                    "reason": "responses_after_failure",
                    "failed_op": expected_op,
                    "failed_index": index,
                }
            return {
                "outcome": "execution_failed",
                "reason": str(resp.get("error") or "not_ok"),
                "failed_op": expected_op,
                "failed_index": index,
            }

        # Successful response: require result/state objects for all ops.
        if "state" not in resp or not isinstance(resp["state"], dict):
            return {
                "outcome": "invalid_stream",
                "reason": "missing_state",
                "failed_op": expected_op,
                "failed_index": index,
            }
        if "result" not in resp or not isinstance(resp["result"], dict):
            return {
                "outcome": "invalid_stream",
                "reason": "missing_result",
                "failed_op": expected_op,
                "failed_index": index,
            }

    if len(responses) < 4:
        # Truncation without terminal None/false — treat as missing next op.
        return {
            "outcome": "execution_failed",
            "reason": "truncated_stream",
            "failed_op": EXPECTED_OPS[len(responses)],
            "failed_index": len(responses),
        }
    return {
        "outcome": "complete",
        "reason": "",
        "failed_op": None,
        "failed_index": None,
    }


def field(
    name: str,
    classification: str,
    *,
    gate: str,
    original: Any = None,
    metal: Any = None,
    note: str = "",
) -> dict:
    row = {
        "field": name,
        "classification": classification,
        "gate": gate,
        "original": original,
        "metal": metal,
    }
    if note:
        row["note"] = note
    return row


def compare(reference: dict, responses: list[Any]) -> dict:
    validate_reference(reference)
    fields: list[dict] = []
    msaa: list[dict] = []
    strict_ok = True
    stream = validate_response_stream(responses)
    if stream["outcome"] == "invalid_stream":
        fields.append(
            field(
                "response_stream",
                "unavailable",
                gate="strict",
                note=stream["reason"],
            )
        )
        strict_ok = False
    elif stream["outcome"] == "execution_failed":
        fields.append(
            field(
                "response_stream",
                "unavailable",
                gate="strict",
                note=f"{stream['failed_op']}:{stream['reason']}",
            )
        )
        strict_ok = False

    by_op: dict[str, Any] = {}
    for index, resp in enumerate(responses):
        if resp is None:
            by_op[EXPECTED_OPS[index]] = None
            break
        if isinstance(resp, dict) and resp.get("op") in EXPECTED_OPS:
            by_op[resp["op"]] = resp
        if isinstance(resp, dict) and resp.get("ok") is False:
            break

    s_in = reference["settings"]["input"]
    s_out = reference["settings"]["output"]
    i_in = reference["init"]["input"]
    i_out = reference["init"]["output"]

    settings_resp = by_op.get("settings")
    configure_resp = by_op.get("configure_window")
    init_resp = by_op.get("init")
    shut_resp = by_op.get("shutdown")

    metal_settings: dict = {}
    metal_state: dict = {}
    init_ok = False
    settings_usable = (
        stream["outcome"] != "invalid_stream"
        and isinstance(settings_resp, dict)
        and settings_resp.get("ok") is True
    )
    configure_usable = (
        stream["outcome"] != "invalid_stream"
        and isinstance(configure_resp, dict)
        and configure_resp.get("ok") is True
    )
    init_usable = (
        stream["outcome"] != "invalid_stream"
        and isinstance(init_resp, dict)
        and init_resp.get("ok") is True
    )

    def mark_unavailable(name: str, note: str = "") -> None:
        nonlocal strict_ok
        fields.append(field(name, "unavailable", gate="strict", note=note))
        strict_ok = False

    def strict_eq(name: str, original: Any, metal: Any, *, note: str = "") -> None:
        nonlocal strict_ok
        matched = original == metal
        if not matched:
            strict_ok = False
        fields.append(
            field(
                name,
                "matched" if matched else "diverged",
                gate="strict",
                original=original,
                metal=metal,
                note=note,
            )
        )

    fields.append(
        field(
            "settings.return",
            "abi_unresolved",
            gate="outside",
            original=s_out["return"],
            metal=(
                settings_resp["result"].get("return")
                if settings_usable
                else None
            ),
            note="original rax int64 vs worker int; not a strict gate",
        )
    )

    if settings_usable:
        metal_settings = settings_resp["result"]
        typed = True
        for key in ("byte_10", "span", "constant"):
            if not is_exact_int(metal_settings.get(key)):
                mark_unavailable(f"settings.{key}", "bad_type")
                typed = False
        flags = metal_settings.get("flags")
        supports = metal_settings.get("device_supports")
        if (
            not isinstance(flags, list)
            or not isinstance(supports, list)
            or len(flags) != len(s_in["sample_values"])
            or len(supports) != len(s_in["sample_values"])
            or not all(is_exact_int(f) and f in (0, 1) for f in flags)
            or not all(is_exact_bool(s) for s in supports)
        ):
            mark_unavailable("settings.msaa_arrays", "bad_type_or_length")
            typed = False
        if typed:
            strict_eq("settings.byte_10", s_out["byte_10"], metal_settings["byte_10"])
            strict_eq("settings.span", s_out["span"], metal_settings["span"])
            strict_eq("settings.constant", s_out["constant"], metal_settings["constant"])
            aniso = metal_settings.get("anisotropy")
            fields.append(
                field(
                    "settings.anisotropy",
                    (
                        "matched"
                        if is_exact_int(aniso) and aniso == s_out["anisotropy"]
                        else "capability_difference"
                        if is_exact_int(aniso)
                        else "unavailable"
                    ),
                    gate="outside",
                    original=s_out["anisotropy"],
                    metal=aniso,
                )
            )
            expected_flags = flags_for(s_in["sample_values"], supports)
            algo_ok = list(flags) == expected_flags
            if not algo_ok:
                strict_ok = False
            fields.append(
                field(
                    "settings.msaa_algorithm",
                    "matched" if algo_ok else "diverged",
                    gate="strict",
                    original=expected_flags,
                    metal=list(flags),
                    note="Metal flags vs flags_for(device_supports)",
                )
            )
            for idx, sample in enumerate(s_in["sample_values"]):
                gl_f = s_out["sample_flags"][idx]
                m_f = flags[idx]
                msaa.append(
                    {
                        "sample": sample,
                        "gl_flag": gl_f,
                        "metal_flag": m_f,
                        "device_supports": supports[idx],
                        "expected_metal_flag": expected_flags[idx],
                        "gl_vs_metal": (
                            "matched" if gl_f == m_f else "capability_difference"
                        ),
                        "algorithm": (
                            "matched" if m_f == expected_flags[idx] else "diverged"
                        ),
                    }
                )
    else:
        mark_unavailable("settings.byte_10")
        mark_unavailable("settings.span")
        mark_unavailable("settings.constant")
        mark_unavailable("settings.msaa_algorithm")

    if configure_usable:
        result = configure_resp["result"]
        echo_ok = True
        for key, want in (
            ("window_id", i_in["window_id"]),
            ("source", i_in["source"]),
            ("width", i_out["width"]),
            ("height", i_out["height"]),
        ):
            got = result.get(key)
            if not is_exact_int(got):
                mark_unavailable(f"configure_window.{key}", "bad_type")
                echo_ok = False
            else:
                strict_eq(f"configure_window.{key}", want, got)
        if not echo_ok:
            pass
    else:
        for key in ("window_id", "source", "width", "height"):
            mark_unavailable(f"configure_window.{key}")

    if init_usable:
        result = init_resp["result"]
        metal_state = init_resp["state"]
        success = result.get("success")
        if not is_exact_int(success):
            mark_unavailable("init.success", "bad_type")
        else:
            init_ok = success == 1
            strict_eq("init.success", i_out["success"], success)
            if not init_ok:
                strict_ok = False

        typed_state = True
        for key in ("width", "height", "source", "primitive", "dirty"):
            if not is_exact_int(metal_state.get(key)):
                mark_unavailable(f"init.{key}", "bad_type")
                typed_state = False
        viewport = metal_state.get("viewport")
        if (
            not isinstance(viewport, list)
            or len(viewport) != 6
            or not all(is_exact_int(v) for v in viewport)
        ):
            mark_unavailable("init.viewport", "bad_type")
            typed_state = False
        for key in ("initialized", "backend_present"):
            if not is_exact_bool(metal_state.get(key)):
                mark_unavailable(f"init.{key}", "bad_type")
                typed_state = False

        if typed_state and is_exact_int(success):
            struct_ok = (
                i_out["master_non_null"] is True
                and metal_state["initialized"] is True
                and metal_state["backend_present"] is True
            )
            if not struct_ok:
                strict_ok = False
            fields.append(
                field(
                    "init.initialized",
                    "matched" if struct_ok else "diverged",
                    gate="strict",
                    original={"master_non_null": i_out["master_non_null"]},
                    metal={
                        "initialized": metal_state["initialized"],
                        "backend_present": metal_state["backend_present"],
                    },
                )
            )
            strict_eq(
                "init.width",
                i_out["width"],
                metal_state["width"],
                note="conditioned_on_reference",
            )
            strict_eq(
                "init.height",
                i_out["height"],
                metal_state["height"],
                note="conditioned_on_reference",
            )
            strict_eq("init.source", i_out["source"], metal_state["source"])
            strict_eq("init.primitive", i_out["primitive"], metal_state["primitive"])
            strict_eq("init.viewport", i_out["viewport"], viewport)
            strict_eq("init.dirty", i_out["dirty"], metal_state["dirty"])

            lod_m = metal_state.get("texture_lod")
            if not is_exact_int(lod_m):
                mark_unavailable("init.texture_lod", "missing_or_bad_type")
            else:
                fields.append(
                    field(
                        "init.texture_lod",
                        (
                            "matched"
                            if lod_m == i_out["lod"]
                            else "capability_difference"
                        ),
                        gate="outside",
                        original=i_out["lod"],
                        metal=lod_m,
                        note="unresolved semantic impact; Metal initializes to 0",
                    )
                )
            max_m = metal_state.get("max_texture")
            if not is_exact_int(max_m):
                mark_unavailable("init.max_texture", "missing_or_bad_type")
            else:
                fields.append(
                    field(
                        "init.max_texture",
                        (
                            "matched"
                            if max_m == i_out["max_texture"]
                            else "capability_difference"
                        ),
                        gate="outside",
                        original=i_out["max_texture"],
                        metal=max_m,
                        note="capability limit; Metal value must still be present",
                    )
                )

            for name, present, snap_key in (
                ("blend", i_out["blend_present"], "blend_snap"),
                ("depth", i_out["depth_present"], "depth_snap"),
                ("raster", i_out["raster_present"], "raster_snap"),
            ):
                snap = metal_state.get(snap_key)
                fields.append(
                    field(
                        f"init.{name}",
                        "not_comparable",
                        gate="outside",
                        original={"present": present},
                        metal={
                            "snap_present": isinstance(snap, str) and len(snap) > 0
                        },
                        note="presence vs Metal snap existence only; bytes not compared",
                    )
                )
            fields.append(
                field(
                    "init.context_backend",
                    (
                        "matched"
                        if i_out["gl_context_present"]
                        and metal_state["backend_present"] is True
                        else "diverged"
                    ),
                    gate="outside",
                    original={"gl_context_present": i_out["gl_context_present"]},
                    metal={"backend_present": metal_state.get("backend_present")},
                    note="structural both-present only",
                )
            )
    else:
        for name in (
            "init.success",
            "init.initialized",
            "init.width",
            "init.height",
            "init.source",
            "init.primitive",
            "init.viewport",
            "init.dirty",
            "init.texture_lod",
            "init.max_texture",
        ):
            mark_unavailable(name)

    shutdown_ok = (
        isinstance(shut_resp, dict)
        and shut_resp.get("ok") is True
        and _envelope_basics(shut_resp, "shutdown", 4) is None
    )
    if init_ok and not shutdown_ok:
        fields.append(
            field(
                "shutdown",
                "unavailable",
                gate="outside",
                note="shutdown failed or missing after successful init",
            )
        )

    contract_pass = bool(
        strict_ok
        and init_ok
        and stream["outcome"] == "complete"
        and settings_usable
        and configure_usable
        and init_usable
    )
    return {
        "schema": DIFF_SCHEMA,
        "contract_pass": contract_pass,
        "provenance_ok": True,
        "stream_outcome": stream["outcome"],
        "stream_reason": stream["reason"],
        "metal_init_ok": init_ok,
        "shutdown_ok": shutdown_ok,
        "init_input_source": i_in["source"],
        "fields": fields,
        "msaa": msaa,
        "counts": {
            cls: sum(1 for f in fields if f["classification"] == cls)
            for cls in (
                "matched",
                "diverged",
                "capability_difference",
                "not_comparable",
                "abi_unresolved",
                "unavailable",
            )
        },
    }


def render_report(comparison: dict, reference: dict) -> str:
    lines = [
        "# gfx-startup differential report",
        "",
        f"- schema: `{comparison['schema']}`",
        f"- contract_pass: `{comparison['contract_pass']}`",
        f"- stream_outcome: `{comparison.get('stream_outcome')}`",
        f"- metal_init_ok: `{comparison['metal_init_ok']}`",
        f"- shutdown_ok: `{comparison['shutdown_ok']}`",
        f"- init input source (fixture): `{comparison['init_input_source']}`",
        "",
        "Width/height comparisons are `conditioned_on_reference`.",
        "",
        "## Fields",
        "",
        "| Field | Gate | Class | Original | Metal | Note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in comparison["fields"]:
        lines.append(
            "| {field} | {gate} | {classification} | {original} | {metal} | {note} |".format(
                field=row["field"],
                gate=row["gate"],
                classification=row["classification"],
                original=json.dumps(row.get("original"), ensure_ascii=True),
                metal=json.dumps(row.get("metal"), ensure_ascii=True),
                note=row.get("note", ""),
            )
        )
    if comparison["msaa"]:
        lines.extend(
            [
                "",
                "## MSAA",
                "",
                "| Sample | GL | Metal | device_supports | expected | gl_vs_metal | algorithm |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for row in comparison["msaa"]:
            lines.append(
                "| {sample} | {gl_flag} | {metal_flag} | {device_supports} | "
                "{expected_metal_flag} | {gl_vs_metal} | {algorithm} |".format(**row)
            )
    lines.extend(["", "## Counts", "", json.dumps(comparison["counts"], indent=2), ""])
    lod = next((f for f in comparison["fields"] if f["field"] == "init.texture_lod"), None)
    if lod and lod["classification"] == "capability_difference":
        lines.extend(
            [
                "## texture_lod",
                "",
                "Known capability difference with unresolved semantic impact "
                f"(original={lod.get('original')}, metal={lod.get('metal')}).",
                "",
            ]
        )
    _ = reference
    return "\n".join(lines)


def replay(reference: dict) -> tuple[list[dict], list[Any], str]:
    import eu4_gfx_shadow_worker as shadow

    validate_reference(reference)
    shadow.build()
    worker = shadow.Worker()
    commands: list[dict] = []
    responses: list[Any] = []
    stderr = ""
    try:
        s_in = reference["settings"]["input"]
        i_in = reference["init"]["input"]
        i_out = reference["init"]["output"]
        ops = [
            (
                "settings",
                {
                    "display": s_in["display"],
                    "windowed": s_in["windowed"],
                    "samples": s_in["sample_values"],
                },
            ),
            (
                "configure_window",
                {
                    "window_id": i_in["window_id"],
                    "source": i_in["source"],
                    "width": i_out["width"],
                    "height": i_out["height"],
                },
            ),
            ("init", {}),
            ("shutdown", {}),
        ]
        for op, args in ops:
            seq = worker.next_seq
            cmd = {"schema": WORKER_SCHEMA, "seq": seq, "op": op, "args": args}
            commands.append(cmd)
            try:
                resp = worker.call(op, args, seq)
            except Exception as exc:  # noqa: BLE001
                resp = None
                stderr = worker.stderr_text() or str(exc)
            responses.append(resp)
            if resp is None or not (isinstance(resp, dict) and resp.get("ok") is True):
                stderr = worker.stderr_text() or stderr
                if op != "shutdown":
                    break
    finally:
        if not stderr:
            stderr = worker.stderr_text()
        worker.close()
    return commands, responses, stderr


def write_run_dir(
    out_dir: Path,
    reference: dict,
    commands: list[dict],
    responses: list[Any],
    comparison: dict,
    stderr: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "reference.json").write_text(dumps(reference) + "\n")
    (out_dir / "worker-commands.jsonl").write_text(
        "".join(dumps(cmd) + "\n" for cmd in commands)
    )
    (out_dir / "worker-responses.jsonl").write_text(
        "".join(dumps(resp) + "\n" for resp in responses)
    )
    (out_dir / "comparison.json").write_text(dumps(comparison) + "\n")
    (out_dir / "report.md").write_text(render_report(comparison, reference))
    if stderr:
        (out_dir / "worker.stderr").write_text(stderr)


def cmd_normalize(args: argparse.Namespace) -> int:
    reference = normalize_capture(Path(args.source))
    validate_reference(reference)
    out = Path(args.out) if args.out else REFERENCE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(reference) + "\n")
    print(out)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    reference = json.loads(Path(args.reference).read_text())
    validate_reference(reference)
    responses_raw = []
    for line in Path(args.responses).read_text().splitlines():
        if not line.strip():
            continue
        if line.strip() == "null":
            responses_raw.append(None)
        else:
            responses_raw.append(json.loads(line))
    comparison = compare(reference, responses_raw)
    out = Path(args.out) if args.out else None
    text = dumps(comparison)
    if out:
        out.write_text(text + "\n")
        print(out)
    else:
        print(text)
    return 0 if comparison["contract_pass"] else 1


def cmd_run(args: argparse.Namespace) -> int:
    if args.source:
        reference = normalize_capture(Path(args.source))
    else:
        reference = json.loads(Path(args.reference or REFERENCE_PATH).read_text())
    validate_reference(reference)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "results" / f"{stamp}-gfx-startup-diff"
    commands, responses, stderr = replay(reference)
    comparison = compare(reference, responses)
    write_run_dir(out_dir, reference, commands, responses, comparison, stderr)
    print(dumps({"out_dir": str(out_dir), "contract_pass": comparison["contract_pass"]}))
    return 0 if comparison["contract_pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    norm = sub.add_parser("normalize")
    norm.add_argument("--source", required=True)
    norm.add_argument("--out")
    norm.set_defaults(func=cmd_normalize)

    cmp_p = sub.add_parser("compare")
    cmp_p.add_argument("--reference", required=True)
    cmp_p.add_argument("--responses", required=True)
    cmp_p.add_argument("--out")
    cmp_p.set_defaults(func=cmd_compare)

    run = sub.add_parser("run")
    run.add_argument("--source")
    run.add_argument("--reference")
    run.add_argument("--out")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    try:
        return args.func(args)
    except DiffError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
