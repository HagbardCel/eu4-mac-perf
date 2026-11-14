"""Independent Metal replay of a captured OpenGL frame (Strategy B, Phase 3/4).

This is the execution layer over the existing M6 replay host.  It does not
implement a second renderer: it prepares the captured workload, invokes the
production replay host, reads back the same checkpoints from both APIs, and
classifies the result.  A replay is never called *complete* because the final
image matches; renderer-generated resources must have been produced by Metal
and every required operation must have executed.

Fault injection is limited to the replay process (environment switches), so the
historical sampler-binding defect can be evaluated without touching the M6
development branch.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from automation import gfx_frame_graph, m6_compare, m6_frame

SCHEMA = "gfx-independent-replay/v1"
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "analysis" / "m6" / "comparison-policy.json"


class ReplayError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _shader_dir(frame_dir: Path, explicit: Path | str | None) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise ReplayError("shaders_missing", str(path))
        return path
    for candidate in (frame_dir / "shaders", frame_dir.parent / "translated"):
        if (candidate / "shaders.json").is_file():
            return candidate.resolve()
    raise ReplayError(
        "shaders_missing",
        "no translated shader directory (frame/shaders or sibling translated/)",
    )


def _swap_red_blue(payload: bytes) -> bytes:
    data = bytearray(payload)
    for offset in range(0, len(data) - 3, 4):
        data[offset], data[offset + 2] = data[offset + 2], data[offset]
    return bytes(data)


def _load_pixels(path: Path, width: int, height: int) -> bytes:
    payload = path.read_bytes()
    if len(payload) != width * height * 4:
        raise ReplayError(
            "checkpoint_size",
            f"{path} is {len(payload)} bytes, expected {width * height * 4}",
        )
    return payload


def compare_checkpoints(graph: dict, replay: dict, frame_dir: Path,
                        out_dir: Path) -> list[dict]:
    """Compare replay checkpoints with their captured OpenGL reference."""
    results: list[dict] = []
    resources = graph.get("resources") or {}
    for checkpoint in replay.get("checkpoints") or []:
        event = checkpoint.get("event")
        target = str(checkpoint.get("target"))
        resource_id = f"tex:{target}"
        resource = resources.get(resource_id) or {}
        file_name = checkpoint.get("file")
        width = int(checkpoint.get("width") or 0)
        height = int(checkpoint.get("height") or 0)
        metal_path = out_dir / str(file_name or "")
        reference_file = checkpoint.get("reference_file")
        reference_path = frame_dir / "blobs" / Path(str(reference_file)).name
        entry = {
            "event": event,
            "target": target,
            "resource": resource_id,
            "width": width,
            "height": height,
            "format": checkpoint.get("format") or resource.get("format"),
            "producer_events": [
                item for item in
                (next((row.get("producer_events") for row in
                       (replay.get("_checkpoint_plan") or [])
                       if row.get("event") == event and
                       str(row.get("target")) == target), []) or [])
            ],
            "metal_file": str(metal_path),
            "reference_file": str(reference_path),
            "authoritative": True,
            "status": "invalid",
        }
        if not checkpoint.get("ok") or not metal_path.is_file():
            entry["reason"] = checkpoint.get("error") or "checkpoint file is absent"
            results.append(entry)
            continue
        if not reference_path.is_file():
            entry["reason"] = "captured reference blob is absent"
            results.append(entry)
            continue
        try:
            metal = _load_pixels(metal_path, width, height)
            reference = _load_pixels(reference_path, width, height)
        except ReplayError as exc:
            entry["reason"] = str(exc)
            results.append(entry)
            continue
        internal = str(resource.get("format") or "")
        channel_swap = internal in ("RGB8", "SRGB8_ALPHA8")
        candidate = _swap_red_blue(metal) if channel_swap else metal
        statistics = m6_compare.compare_images(
            reference, candidate, width, height)
        entry["normalization"] = {
            "channel_swap": "bgra_to_rgba" if channel_swap else None,
            "row_origin": "declared_lower_left",
        }
        entry["statistics"] = {
            key: statistics[key] for key in (
                "pixels", "mismatched_pixels", "mismatched_fraction",
                "alpha_different_pixels", "max_channel_error",
                "mean_abs_error", "mean_max_channel_error",
            )
        }
        entry["worst_regions"] = statistics["regions"][:4]
        entry["metal_sha256"] = _sha(metal_path)
        entry["reference_sha256"] = _sha(reference_path)
        if channel_swap:
            # GL RGB8 has no alpha channel and Metal has no RGB8 target; the
            # readback transform is a declared normalization.  Keep the
            # checkpoint diagnostic, not authoritative.
            entry["authoritative"] = False
        if statistics["mismatched_pixels"] == 0:
            entry["status"] = "match"
        elif entry["authoritative"]:
            entry["status"] = "divergent"
        else:
            entry["status"] = "divergent_diagnostic"
        results.append(entry)
    results.sort(key=lambda item: (item.get("event") or 0, item.get("target") or ""))
    return results


def binding_audit(replay: dict) -> dict:
    """Verify each shader's declared texture unit resolves to its texture.

    The capture contract is ``texN`` -> engine texture unit N.  A translation
    that assigns samplers sequentially can move a payload to a different unit;
    the shader then samples a neighbouring texture.  This audit uses the
    replay's own provenance and reports the exact sampler/unit/texture that no
    longer agrees with the translated declaration.
    """
    samples = replay.get("_provenance_samples") or []
    by_event: dict[int, list[dict]] = {}
    for sample in samples:
        by_event.setdefault(int(sample.get("event") or 0), []).append(sample)
    mismatches: list[dict] = []
    for event in sorted(by_event):
        rows = by_event[event]
        bound = {}
        for row in rows:
            bound[int(row.get("unit") or 0)] = str(row.get("texture") or "")
        for row in rows:
            shader_unit = int(row.get("fault_shader_unit")
                              if row.get("fault_shader_unit") is not None
                              else row.get("unit") or 0)
            expected = str(row.get("texture") or "")
            actual = bound.get(shader_unit, "")
            if expected != actual:
                mismatches.append({
                    "event": event,
                    "kind": "shader_unit_collision",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "bound_unit": int(row.get("unit") or 0),
                    "expected_texture": expected,
                    "actual_texture_at_shader_unit": actual,
                    "shift": row.get("fault_sampler_shift") or 0,
                })
            missing_unit = int(row.get("fault_missing_engine_unit") or -1)
            if missing_unit >= 0:
                mismatches.append({
                    "event": event,
                    "kind": "missing_texture_unit",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "engine_unit": missing_unit,
                    "expected_texture": str(row.get("expected_texture") or ""),
                    "selected_texture": "",
                    "shift": row.get("fault_sampler_shift") or 0,
                    "note": "the selected engine unit has no captured texture; "
                            "the shader would sample the default texture",
                })
            declared = str(row.get("expected_texture") or "")
            selected = str(row.get("selected_texture") or "")
            if declared and selected and declared != selected:
                mismatches.append({
                    "event": event,
                    "kind": "wrong_texture_selected",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "engine_unit": int(row.get("selected_engine_unit") or -1),
                    "expected_texture": declared,
                    "selected_texture": selected,
                    "shift": row.get("fault_sampler_shift") or 0,
                })
    return {
        "schema": "gfx-replay-binding-audit/v1",
        "checked_events": len(by_event),
        "checked_bindings": len(samples),
        "mismatched_bindings": len(mismatches),
        "earliest_event": mismatches[0]["event"] if mismatches else None,
        "mismatches": mismatches[:256],
        "ok": not mismatches,
    }


def final_comparison(frame_dir: Path, out_dir: Path,
                     policy_path: Path | str | None = None) -> dict:
    manifest = json.loads((frame_dir / "frame-trace.json").read_text())
    frame = manifest.get("frame") or {}
    width = int(frame.get("width") or 0)
    height = int(frame.get("height") or 0)
    reference = frame_dir / str(frame.get("reference") or "")
    candidate = out_dir / "metal-frame.bgra"
    if not candidate.is_file():
        return {"status": "invalid", "reason": "Metal final readback is absent"}
    statistics = m6_compare.compare_images(
        reference.read_bytes(), candidate.read_bytes(), width, height)
    policy = json.loads(Path(policy_path or DEFAULT_POLICY).read_text())
    thresholds = policy.get("thresholds") or {}
    passed = (
        statistics["mismatched_pixels"] == 0
        if int(thresholds.get("mismatched_fraction", 0)) == 0
        else statistics["mismatched_fraction"]
        <= float(thresholds["mismatched_fraction"])
    )
    return {
        "status": "match" if passed else "divergent",
        "width": width,
        "height": height,
        "reference_sha256": _sha(reference),
        "candidate_sha256": _sha(candidate),
        "statistics": {
            key: statistics[key] for key in (
                "pixels", "mismatched_pixels", "mismatched_fraction",
                "alpha_different_pixels", "max_channel_error",
                "mean_abs_error", "mean_max_channel_error",
            )
        },
        "worst_regions": statistics["regions"][:4],
        "policy": str(policy_path or DEFAULT_POLICY),
    }


def first_divergence(graph: dict, checkpoints: list[dict],
                     final: dict, *, fault: dict | None = None) -> dict:
    """Earliest confirmed intermediate or final divergence with provenance."""
    divergent = [
        item for item in checkpoints
        if item.get("status") == "divergent" and item.get("authoritative")
    ]
    if divergent:
        first = divergent[0]
        resource = (graph.get("resources") or {}).get(first["resource"]) or {}
        producers = [item for item in resource.get("producers") or []
                     if item.get("event", 0) < (first.get("event") or 0)]
        upstream = [
            {"event": row["event"],
             "target": row["target"],
             "status": next((item["status"] for item in checkpoints
                             if item.get("event") == row["event"]
                             and str(item.get("target")) == row["target"]),
                            "not_checkpointed")}
            for row in graph.get("events") or []
            if row.get("event") is not None and row.get("event") < first["event"]
            and row.get("samples") and row.get("target") == first["resource"]
        ]
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "intermediate",
            "event": first["event"],
            "target": first["target"],
            "resource": first["resource"],
            "producer_event": producers[-1]["event"] if producers else None,
            "producer": producers[-1] if producers else None,
            "statistics": first.get("statistics"),
            "upstream_checkpoints": upstream,
            "reason": (
                "the earliest authoritative intermediate checkpoint differs; "
                "its inputs are not necessarily the cause"),
            "fault": fault,
        }
    if final.get("status") == "divergent":
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "final_only",
            "event": None,
            "statistics": final.get("statistics"),
            "reason": (
                "no authoritative intermediate checkpoint diverged before the "
                "final output; increase checkpoint detail around the last pass"),
            "fault": fault,
        }
    diagnostic = [item for item in checkpoints
                  if item.get("status") == "divergent_diagnostic"]
    return {
        "schema": "gfx-first-divergence/v1",
        "kind": "none",
        "event": None,
        "diagnostic_checkpoints": [
            {"event": item["event"], "target": item["target"],
             "statistics": item.get("statistics")} for item in diagnostic
        ],
        "reason": "all authoritative checkpoints and the final output match",
        "fault": fault,
    }


def _parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay one captured OpenGL frame through the production Metal host")
    parser.add_argument("--frame", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--shaders", type=Path, default=None)
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--disable-render-target-vflip", action="store_true")
    parser.add_argument("--sampler-shift", type=int, default=None)
    parser.add_argument("--sampler-shift-min", type=int, default=7)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    faults: dict = {}
    if args.disable_render_target_vflip:
        faults["disable_render_target_vflip"] = True
    if args.sampler_shift is not None:
        faults["sampler_shift"] = args.sampler_shift
        faults["sampler_shift_min"] = args.sampler_shift_min
    try:
        report = replay(args.frame, args.out, shaders=args.shaders,
                        faults=faults, policy=args.policy)
    except ReplayError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "status": report.get("status"),
        "graph": (report.get("graph") or {}).get("status"),
        "evaluation": (report.get("evaluation") or {}).get("status"),
        "final": (report.get("final") or {}).get("status"),
        "mismatched_pixels": ((report.get("final") or {}).get("statistics") or {}).get(
            "mismatched_pixels"),
        "checkpoints": [
            {"event": item["event"], "target": item["target"],
             "status": item["status"],
             "mismatched_pixels": item["statistics"]["mismatched_pixels"]}
            for item in report.get("checkpoints") or []
        ],
        "binding_audit": {
            "ok": (report.get("binding_audit") or {}).get("ok"),
            "mismatched": (report.get("binding_audit") or {}).get("mismatched_bindings"),
            "earliest_event": (report.get("binding_audit") or {}).get("earliest_event"),
        },
        "first_divergence": report.get("first_divergence"),
        "report": str(Path(args.out) / "replay-report.json"),
    }, indent=2))
    return 0


def replay(
    frame_dir: Path | str,
    out_dir: Path | str,
    *,
    shaders: Path | str | None = None,
    faults: dict | None = None,
    policy: Path | str | None = None,
    timeout: float = 1800.0,
) -> dict:
    """Execute one captured workload through the production Metal replay host."""
    frame_dir = Path(frame_dir).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    if out_dir == frame_dir or frame_dir in out_dir.parents or out_dir in frame_dir.parents:
        raise ReplayError("output_overlap", "replay output must not overlap the capture")
    if out_dir.exists():
        import shutil

        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    graph = gfx_frame_graph.build(frame_dir)
    request = gfx_frame_graph.write_checkpoint_request(
        graph, out_dir / "checkpoint-request.json")
    shader_dir = _shader_dir(frame_dir, shaders)
    host = m6_frame.build_replay_host()
    command = [
        str(host), "--frame", str(frame_dir), "--shaders", str(shader_dir),
        "--out", str(out_dir), "--checkpoints",
        str(out_dir / "checkpoint-request.json"),
    ]
    env = dict(os.environ)
    env.pop("EU4_FRAME_REPLAY_DISABLE_RT_FLIP", None)
    env.pop("EU4_FRAME_REPLAY_SAMPLER_SHIFT", None)
    faults = dict(faults or {})
    if faults.get("disable_render_target_vflip"):
        env["EU4_FRAME_REPLAY_DISABLE_RT_FLIP"] = "1"
    if faults.get("sampler_shift") is not None:
        env["EU4_FRAME_REPLAY_SAMPLER_SHIFT"] = str(faults["sampler_shift"])
        if faults.get("sampler_shift_min") is not None:
            env["EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"] = str(
                faults["sampler_shift_min"])
    ran = subprocess.run(
        command, capture_output=True, text=True, check=False, env=env,
        timeout=timeout,
    )
    result_path = out_dir / "replay.json"
    if ran.returncode != 0 or not result_path.is_file():
        return {
            "schema": SCHEMA,
            "frame_dir": str(frame_dir),
            "out_dir": str(out_dir),
            "status": "invalid",
            "faults": faults,
            "returncode": ran.returncode,
            "stderr": (ran.stderr or "")[-4000:],
            "graph": graph,
        }
    replay_result = json.loads(result_path.read_text())
    replay_result["_provenance_samples"] = _read_jsonl(
        out_dir / "replay-provenance.jsonl")
    replay_result["_checkpoint_plan"] = request.get("checkpoints") or []
    evaluation = gfx_frame_graph.evaluate_replay(graph, replay_result)
    checkpoints = compare_checkpoints(graph, replay_result, frame_dir, out_dir)
    audit = binding_audit(replay_result)
    final = final_comparison(frame_dir, out_dir, policy)
    divergence = first_divergence(graph, checkpoints, final, fault=faults or None)
    status = evaluation["status"]
    if final.get("status") != "match" and status == "complete":
        status = "partial"
    if any(item.get("status") == "divergent" and item.get("authoritative")
           for item in checkpoints) and status == "complete":
        status = "partial"
    if not audit.get("ok") and status == "complete":
        status = "partial"
    report = {
        "schema": SCHEMA,
        "frame_dir": str(frame_dir),
        "out_dir": str(out_dir),
        "status": status,
        "faults": faults,
        "graph": graph,
        "evaluation": evaluation,
        "checkpoints": checkpoints,
        "binding_audit": audit,
        "final": final,
        "first_divergence": divergence,
        "replay": {
            key: replay_result.get(key) for key in (
                "status", "events", "encoded", "failed", "command_status",
                "readback", "provenance", "failures",
            )
        },
        "artifacts": {
            "replay_json": str(result_path),
            "provenance": str(out_dir / "replay-provenance.jsonl"),
            "final_readback": str(out_dir / "metal-frame.bgra"),
            "checkpoint_request": str(out_dir / "checkpoint-request.json"),
        },
    }
    (out_dir / "replay-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
