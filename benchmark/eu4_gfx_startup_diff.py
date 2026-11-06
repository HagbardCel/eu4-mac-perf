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


class DiffError(Exception):
    pass


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
        flags.append(1 if sample == threshold and bool(supported) else 0)
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


def _require_event(event: dict, site: str, phase: str) -> None:
    if event.get("schema") != CAPTURE_SCHEMA:
        raise DiffError(f"bad schema on {site}/{phase}")
    if event.get("site") != site or event.get("phase") != phase:
        raise DiffError(f"expected {site}/{phase}")
    if not event.get("fields_ok"):
        raise DiffError(f"fields_ok false on {site}/{phase}")


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

    exe_sha = metadata.get("executable_sha256")
    if exe_sha != PINNED_SHA:
        raise DiffError("executable sha mismatch")
    if status.get("module") and status["module"] != exe_sha:
        raise DiffError("status module sha mismatch")
    if str(metadata.get("pid")) != status.get("pid"):
        raise DiffError("pid mismatch")

    events = [
        json.loads(line)
        for line in paths["events.jsonl"].read_text().splitlines()
        if line.strip()
    ]
    if not events:
        raise DiffError("no events")

    # Find first replayable settings pair followed by qualifying init pair.
    i = 0
    selected = None
    while i < len(events):
        if (
            i + 3 < len(events)
            and events[i].get("site") == "settings"
            and events[i].get("phase") == "entry"
            and events[i + 1].get("site") == "settings"
            and events[i + 1].get("phase") == "exit"
            and events[i + 2].get("site") == "init"
            and events[i + 2].get("phase") == "entry"
            and events[i + 3].get("site") == "init"
            and events[i + 3].get("phase") == "exit"
        ):
            window = events[i : i + 4]
            exit_init = window[3]
            if (
                exit_init.get("success") == 1
                and exit_init.get("master_non_null")
                and exit_init.get("fields_ok")
            ):
                selected = window
                break
            i += 1
            continue
        if events[i].get("site") in ("settings", "init"):
            # Non-replayable interleaving among startup sites.
            if selected is None and i + 1 < len(events):
                raise DiffError("unsupported_sequence")
        i += 1
    if selected is None:
        # Distinguish empty vs wrong order among present startup events.
        sites = [(e.get("site"), e.get("phase")) for e in events]
        if any(s[0] in ("settings", "init") for s in sites):
            raise DiffError("unsupported_sequence")
        raise DiffError("no qualifying init")

    for idx, event in enumerate(selected):
        if event.get("seq") != selected[0]["seq"] + idx:
            raise DiffError("non-contiguous seq")

    s_entry, s_exit, i_entry, i_exit = selected
    _require_event(s_entry, "settings", "entry")
    _require_event(s_exit, "settings", "exit")
    _require_event(i_entry, "init", "entry")
    _require_event(i_exit, "init", "exit")
    if s_entry.get("call_id") != s_exit.get("call_id"):
        raise DiffError("settings call_id mismatch")
    if i_entry.get("call_id") != i_exit.get("call_id"):
        raise DiffError("init call_id mismatch")
    if s_entry.get("sample_count") != s_exit.get("sample_count"):
        raise DiffError("sample_count mismatch")
    if s_entry.get("sample_values") != s_exit.get("sample_values"):
        raise DiffError("sample_values mismatch")
    values = s_entry.get("sample_values")
    if not isinstance(values, list) or s_entry.get("sample_count") != len(values):
        raise DiffError("invalid sample_values")
    if s_exit.get("sample_captured") != len(s_exit.get("sample_flags") or []):
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
                "sample_flags": list(s_exit["sample_flags"]),
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


def validate_envelope(resp: dict | None, op: str, seq: int) -> str | None:
    if not isinstance(resp, dict):
        return "missing_response"
    if resp.get("schema") != WORKER_SCHEMA:
        return "bad_schema"
    if resp.get("op") != op:
        return "op_mismatch"
    if resp.get("seq") != seq:
        return "seq_mismatch"
    if not resp.get("ok"):
        return resp.get("error") or "not_ok"
    if "state" not in resp or not isinstance(resp["state"], dict):
        return "missing_state"
    if "result" not in resp or not isinstance(resp["result"], dict):
        return "missing_result"
    return None


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


def compare(reference: dict, responses: dict[str, dict | None]) -> dict:
    fields: list[dict] = []
    msaa: list[dict] = []
    strict_ok = True
    provenance_ok = reference.get("schema") == REF_SCHEMA
    if not provenance_ok:
        strict_ok = False

    settings_resp = responses.get("settings")
    init_resp = responses.get("init")
    configure_resp = responses.get("configure_window")

    env_settings = validate_envelope(settings_resp, "settings", 1)
    env_configure = validate_envelope(configure_resp, "configure_window", 2)
    env_init = validate_envelope(init_resp, "init", 3)

    s_in = reference["settings"]["input"]
    s_out = reference["settings"]["output"]
    i_in = reference["init"]["input"]
    i_out = reference["init"]["output"]

    if env_settings:
        fields.append(
            field(
                "settings_envelope",
                "unavailable",
                gate="strict",
                note=str(env_settings),
            )
        )
        strict_ok = False
        metal_settings: dict = {}
    else:
        metal_settings = settings_resp["result"]  # type: ignore[index]

    if env_configure:
        fields.append(
            field(
                "configure_window_envelope",
                "unavailable",
                gate="strict",
                note=str(env_configure),
            )
        )
        strict_ok = False

    if env_init:
        fields.append(
            field("init_envelope", "unavailable", gate="strict", note=str(env_init))
        )
        strict_ok = False
        metal_state: dict = {}
        init_ok = False
    else:
        metal_state = init_resp["state"]  # type: ignore[index]
        init_ok = bool(init_resp["result"].get("success") == 1)  # type: ignore[index]
        if not init_ok:
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

    # Settings return: ABI unresolved.
    fields.append(
        field(
            "settings.return",
            "abi_unresolved",
            gate="outside",
            original=s_out["return"],
            metal=metal_settings.get("return") if metal_settings else None,
            note="original rax int64 vs worker int; not a strict gate",
        )
    )

    if metal_settings:
        strict_eq("settings.byte_10", s_out["byte_10"], metal_settings.get("byte_10"))
        strict_eq("settings.span", s_out["span"], metal_settings.get("span"))
        strict_eq("settings.constant", s_out["constant"], metal_settings.get("constant"))
        aniso_m = metal_settings.get("anisotropy")
        fields.append(
            field(
                "settings.anisotropy",
                "matched" if aniso_m == s_out["anisotropy"] else "capability_difference",
                gate="outside",
                original=s_out["anisotropy"],
                metal=aniso_m,
            )
        )

        samples = list(s_in["sample_values"])
        gl_flags = list(s_out["sample_flags"])
        metal_flags = list(metal_settings.get("flags") or [])
        supports = list(metal_settings.get("device_supports") or [])
        if len(metal_flags) != len(samples) or len(supports) != len(samples):
            fields.append(
                field(
                    "settings.msaa_lengths",
                    "unavailable",
                    gate="strict",
                    note="flags/supports length mismatch",
                )
            )
            strict_ok = False
        else:
            expected_flags = flags_for(samples, supports)
            algo_ok = metal_flags == expected_flags
            if not algo_ok:
                strict_ok = False
            fields.append(
                field(
                    "settings.msaa_algorithm",
                    "matched" if algo_ok else "diverged",
                    gate="strict",
                    original=expected_flags,
                    metal=metal_flags,
                    note="Metal flags vs flags_for(device_supports)",
                )
            )
            for idx, sample in enumerate(samples):
                gl_f = gl_flags[idx] if idx < len(gl_flags) else None
                m_f = metal_flags[idx]
                cap = "matched" if gl_f == m_f else "capability_difference"
                msaa.append(
                    {
                        "sample": sample,
                        "gl_flag": gl_f,
                        "metal_flag": m_f,
                        "device_supports": bool(supports[idx]),
                        "expected_metal_flag": expected_flags[idx],
                        "gl_vs_metal": cap,
                        "algorithm": (
                            "matched" if m_f == expected_flags[idx] else "diverged"
                        ),
                    }
                )
    else:
        fields.append(
            field("settings.msaa_algorithm", "unavailable", gate="strict")
        )
        strict_ok = False

    # Init comparisons.
    if metal_state:
        metal_success = 1 if init_ok else 0
        strict_eq("init.success", i_out["success"], metal_success)
        struct_ok = bool(i_out["master_non_null"]) and bool(
            metal_state.get("initialized")
        ) and bool(metal_state.get("backend_present"))
        if not struct_ok:
            strict_ok = False
        fields.append(
            field(
                "init.initialized",
                "matched" if struct_ok else "diverged",
                gate="strict",
                original={"master_non_null": i_out["master_non_null"]},
                metal={
                    "initialized": metal_state.get("initialized"),
                    "backend_present": metal_state.get("backend_present"),
                },
            )
        )
        strict_eq(
            "init.width",
            i_out["width"],
            metal_state.get("width"),
            note="conditioned_on_reference",
        )
        strict_eq(
            "init.height",
            i_out["height"],
            metal_state.get("height"),
            note="conditioned_on_reference",
        )
        strict_eq("init.source", i_out["source"], metal_state.get("source"))
        strict_eq("init.primitive", i_out["primitive"], metal_state.get("primitive"))
        strict_eq("init.viewport", i_out["viewport"], metal_state.get("viewport"))
        strict_eq("init.dirty", i_out["dirty"], metal_state.get("dirty"))

        lod_m = metal_state.get("texture_lod")
        fields.append(
            field(
                "init.texture_lod",
                "matched" if lod_m == i_out["lod"] else "capability_difference",
                gate="outside",
                original=i_out["lod"],
                metal=lod_m,
                note="unresolved semantic impact; Metal initializes to 0",
            )
        )
        max_m = metal_state.get("max_texture")
        fields.append(
            field(
                "init.max_texture",
                "matched" if max_m == i_out["max_texture"] else "capability_difference",
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
                    metal={"snap_present": isinstance(snap, str) and len(snap) > 0},
                    note="presence vs Metal snap existence only; bytes not compared",
                )
            )
        fields.append(
            field(
                "init.context_backend",
                "matched"
                if i_out["gl_context_present"] and metal_state.get("backend_present")
                else "diverged",
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
        ):
            fields.append(field(name, "unavailable", gate="strict"))
            strict_ok = False

    fields.append(
        field(
            "init.input_source",
            "matched",
            gate="outside",
            original=i_in["source"],
            metal=None,
            note="fixture preserves captured input source; not rewritten for configure_window",
        )
    )

    shut = responses.get("shutdown")
    shutdown_ok = validate_envelope(shut, "shutdown", 4) is None if shut else False

    contract_pass = bool(provenance_ok and strict_ok and init_ok and not env_init)
    return {
        "schema": DIFF_SCHEMA,
        "contract_pass": contract_pass,
        "provenance_ok": provenance_ok,
        "metal_init_ok": init_ok if metal_state else False,
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
    if lod and lod["classification"] != "matched":
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


def replay(reference: dict) -> tuple[list[dict], list[dict | None], str]:
    import eu4_gfx_shadow_worker as shadow

    shadow.build()
    worker = shadow.Worker()
    commands: list[dict] = []
    responses: list[dict | None] = []
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
            except Exception as exc:  # noqa: BLE001 — surface as unavailable
                resp = None
                stderr = worker.stderr_text() or str(exc)
            responses.append(resp)
            if resp is None or not resp.get("ok"):
                stderr = worker.stderr_text() or stderr
                # Continue to attempt shutdown only if init path died early.
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
    responses: list[dict | None],
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
    out = Path(args.out) if args.out else REFERENCE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(reference) + "\n")
    print(out)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    reference = json.loads(Path(args.reference).read_text())
    responses_raw = [
        json.loads(line)
        for line in Path(args.responses).read_text().splitlines()
        if line.strip()
    ]
    by_op = {resp.get("op"): resp for resp in responses_raw if isinstance(resp, dict)}
    comparison = compare(reference, by_op)
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
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else ROOT / "results" / f"{stamp}-gfx-startup-diff"
    commands, responses, stderr = replay(reference)
    by_op: dict[str, dict | None] = {}
    for cmd, resp in zip(commands, responses):
        by_op[cmd["op"]] = resp
    comparison = compare(reference, by_op)
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
