"""Self-contained investigation package for one confirmed divergence (PR 4).

The package is useful without the live processes: both frame payloads, a
normalized difference image, the exact metrics, alignment evidence, GPU health,
and an analysis that separates confirmed observations from hypotheses.  Missing
evidence is named explicitly; the analysis never asserts a root cause that the
data does not support.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

from automation import gfx_frame_compare

SCHEMA = "gfx-diagnostic-case/v1"

CONFIRMED_RENDERING = "rendering_difference"
UNALIGNED = "unaligned"
GPU_FAILURE = "gpu_execution_failure"
INSTRUMENTATION = "instrumentation_failure"
INCOMPLETE = "incomplete_evidence"
MATCH = "match"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy(src: Path, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "sha256": gfx_frame_compare._sha256(dest),
    }


def _classify(result: dict, pair: dict | None, comparison: dict | None) -> str:
    health = result.get("health") or {}
    if health.get("verdict") == "failed":
        return GPU_FAILURE
    if health.get("instrumentation_failures"):
        return INSTRUMENTATION
    if pair is None or comparison is None:
        return INCOMPLETE
    dumps = pair.get("dumps") or {}
    reference_found = (dumps.get("reference") or {}).get("found") or {}
    candidate_found = (dumps.get("candidate") or {}).get("found") or {}
    if not reference_found or not candidate_found:
        return INCOMPLETE
    status = comparison.get("status")
    if status == gfx_frame_compare.FAIL:
        return CONFIRMED_RENDERING
    if status == gfx_frame_compare.PASS:
        return MATCH
    if status in (gfx_frame_compare.UNVERIFIED, "not_compared", None):
        return UNALIGNED
    return INCOMPLETE


def _choose_pair(result: dict, pair_index: int | None = None) -> dict | None:
    frames = result.get("frames") or {}
    summary = frames.get("comparison") or {}
    if pair_index is not None:
        return None
    if summary.get("first_failure"):
        return summary["first_failure"]
    return frames.get("last_pair")


def _analysis(result: dict, pair: dict | None, comparison: dict | None,
              classification: str, artifacts: dict) -> str:
    scenario = result.get("scenario") or {}
    stop = result.get("stop") or {}
    health = result.get("health") or {}
    lines = [
        "# Diagnostic case",
        "",
        f"Session: `{result.get('session_id')}`  ",
        f"Scenario: `{scenario.get('name')}` (`{scenario.get('digest')}`)  ",
        f"Classification: **{classification}**  ",
        f"Stop: `{stop.get('reason')}` ({stop.get('trigger')})",
        "",
        "## Confirmed observations",
        "",
    ]
    if pair:
        alignment = (comparison or {}).get("alignment") or {}
        lines += [
            f"- Paired logical frame: reference {pair.get('frame_index')}, "
            f"candidate {pair.get('candidate_frame_index')}.",
            f"- Alignment evidence: {', '.join(alignment.get('evidence', [])) or 'none'}.",
            f"- Extent relation: {json.dumps(alignment.get('extent'))}.",
        ]
        statistics = (comparison or {}).get("statistics") or {}
        if statistics:
            lines += [
                f"- Differing pixels: {statistics.get('mismatched_pixels')} / "
                f"{statistics.get('pixels')} ({statistics.get('mismatched_fraction')}).",
                f"- Max channel error: {statistics.get('max_channel_error')}; "
                f"mean absolute error: {statistics.get('mean_abs_error')}.",
                f"- Difference bounding box: {statistics.get('difference_bbox')}.",
            ]
        regions = (comparison or {}).get("regions") or {}
        for name, region in regions.items():
            lines.append(
                f"- Region `{name}` ({region.get('rect')}): "
                f"{region.get('mismatched_fraction')} differing, "
                f"structural correlation {region.get('structural_correlation')}, "
                f"mirror {region.get('mirror', {}).get('mirrored')}."
            )
        if (comparison or {}).get("mirrored_regions"):
            lines.append(
                "- Horizontal mirroring is positively indicated in: "
                + ", ".join(comparison["mirrored_regions"])
            )
    else:
        lines.append("- No paired frame with preserved raw payloads was available.")
    lines += [
        f"- GPU health verdict: `{health.get('verdict')}`; first failure: "
        f"`{(health.get('first_failure') or {}).get('kind')}`.",
        f"- Artifact budget: {artifacts.get('bytes')} / {artifacts.get('max_bytes')} bytes; "
        f"dropped {artifacts.get('dropped')}.",
        "",
        "## Excluded explanations",
        "",
        "- Row-origin and BGRA/RGBA differences are decoded before comparison; they are "
        "recorded in `frames/` provenance rather than treated as renderer errors.",
        "- The candidate's 2x extent is normalized by area downscale and recorded; a "
        "scale-normalized candidate cannot pass an `exact` policy as bit-identical.",
        "- A pair whose caller composition/draw count/extent does not corroborate "
        "alignment is classified as `unaligned`, not as a rendering difference.",
        "",
        "## Remaining hypotheses",
        "",
    ]
    if classification == CONFIRMED_RENDERING:
        lines += [
            "- The difference is real at the compared frame; the responsible pass is "
            "not yet localized to one operation in this package.",
            "- Candidate causes consistent with the observed regions include texture-unit "
            "or resource binding, state decoding, resolve/orientation, or missing draws. "
            "These are hypotheses, not findings.",
        ]
    elif classification == UNALIGNED:
        lines += [
            "- Correspondence for this pair is not strong enough to justify a pixel "
            "verdict. The next step is additional semantic evidence, not renderer debugging.",
        ]
    elif classification == GPU_FAILURE:
        lines += [
            "- The first actionable GPU/API failure is recorded in `gpu/`; visual "
            "debugging would be premature until it is corrected.",
        ]
    else:
        lines += [
            "- The package does not contain a confirmed renderer difference.",
        ]
    lines += [
        "",
        "## Missing evidence",
        "",
    ]
    missing = []
    if pair and not (pair.get("dumps") or {}).get("reference", {}).get("found"):
        missing.append("- Reference raw frame for the paired index.")
    if pair and not (pair.get("dumps") or {}).get("candidate", {}).get("found"):
        missing.append("- Candidate raw frame for the paired index.")
    if not (Path(artifacts.get("root", "")) / "semantic.json").is_file():
        missing.append(
            "- Per-draw semantic operation prefix (caller/shader/uniform/sampler/target) "
            "for the failing frame."
        )
    lines += missing or ["- None identified for the compared frame."]
    lines += [
        "",
        "## Next inspection point",
        "",
        "- Inspect `difference/statistics.json` and the worst region, then compare the "
        "per-draw operation prefix around the first differing pass.",
        "- If a per-draw prefix is absent, a targeted capture of the failing caller/event "
        "is required; do not start a full campaign merely to reproduce the same frame.",
        "",
        f"Another EU4 launch is needed for this package: **{'yes' if missing else 'no'}**.",
        "",
    ]
    return "\n".join(lines)


def build_case(run_dir: Path, *, out_dir: Path | None = None,
               pair_index: int | None = None,
               semantic: dict | None = None,
               resources: dict | None = None) -> dict:
    run_dir = Path(run_dir).resolve()
    result = _read_json(run_dir / "result.json")
    if result is None:
        raise ValueError(f"no result.json in {run_dir}")
    session = _read_json(run_dir / "session.json") or {}
    pair = _choose_pair(result, pair_index)
    comparison = (pair or {}).get("comparison")
    classification = _classify(result, pair, comparison)
    case_dir = Path(out_dir) if out_dir is not None else run_dir / "case"
    case_dir.mkdir(parents=True, exist_ok=True)
    for name in ("frames", "difference", "operations", "resources", "gpu", "repro"):
        (case_dir / name).mkdir(exist_ok=True)
    artifacts = result.get("artifacts") or {}
    manifest = {
        "schema": SCHEMA,
        "created_utc": _now(),
        "session_id": result.get("session_id"),
        "scenario": result.get("scenario"),
        "build": result.get("build"),
        "processes": result.get("processes"),
        "ledger": result.get("ledger"),
        "preflight": result.get("preflight"),
        "artifacts": artifacts,
        "source_run": str(run_dir),
        "case_dir": str(case_dir),
    }
    _write_json(case_dir / "manifest.json", manifest)
    _write_json(case_dir / "result.json", {
        "schema": SCHEMA,
        "classification": classification,
        "stop": result.get("stop"),
        "frames": result.get("frames"),
        "health": result.get("health"),
        "comparison": comparison,
        "pair": pair,
        "session": {
            "session_id": session.get("session_id"),
            "profile": session.get("profile"),
            "terminal": result.get("terminal"),
        },
    })
    frame_files = {}
    difference = {}
    selected = [("reference", pair.get("frame_index"))] if pair else []
    if pair:
        selected.append(("candidate", pair.get("candidate_frame_index")))
    dumps = (pair or {}).get("dumps") or {}
    for role, index in selected:
        found = (dumps.get(role) or {}).get("found", {}) if isinstance(dumps.get(role), dict) else {}
        meta = found.get(str(index)) if isinstance(found, dict) else None
        if not meta:
            continue
        raw = Path(meta.get("raw_file", ""))
        if not raw.is_file():
            continue
        copied = _copy(raw, case_dir / "frames" / f"{role}-frame-{index}.bgra")
        copied.update({
            "role": role,
            "frame_index": index,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "origin": meta.get("origin"),
            "pixel_format": meta.get("pixel_format"),
        })
        frame_files[role] = copied
        try:
            image = gfx_frame_compare.decode_frame(
                raw.read_bytes(), int(meta["width"]), int(meta["height"]),
                origin=str(meta.get("origin", "top_left")),
                pixel_format=str(meta.get("pixel_format", "BGRA8")),
            )
            if role == "candidate" and "reference" in frame_files:
                reference_meta = next(iter((dumps.get("reference") or {}).get("found", {}).values()))
                reference_image = gfx_frame_compare.decode_frame(
                    Path(reference_meta["raw_file"]).read_bytes(),
                    int(reference_meta["width"]), int(reference_meta["height"]),
                    origin=str(reference_meta.get("origin", "top_left")),
                    pixel_format=str(reference_meta.get("pixel_format", "BGRA8")),
                )
                normalized, _ = gfx_frame_compare.normalize_candidate(
                    reference_image, image,
                    policy={"allow_scale_normalization": True},
                    candidate_meta=meta,
                )
                gfx_frame_compare.write_png(
                    case_dir / "frames" / "candidate-normalized.png", normalized)
                gfx_frame_compare.write_png(
                    case_dir / "frames" / "reference.png", reference_image)
                diff = gfx_frame_compare.difference_image(reference_image, normalized)
                gfx_frame_compare.write_png(case_dir / "difference" / "difference.png", diff)
                difference = {"scale": normalized.shape[1] // reference_image.shape[1] if False else None,
                              "method": "top-left RGBA difference, amplification x4"}
            else:
                gfx_frame_compare.write_png(case_dir / "frames" / f"{role}.png", image)
        except (OSError, KeyError, gfx_frame_compare.CompareError, ValueError) as exc:
            difference["error"] = f"{type(exc).__name__}: {exc}"
    _write_json(case_dir / "difference" / "statistics.json", {
        "comparison": comparison,
        "difference_image": difference,
        "frames": frame_files,
    })
    _write_json(case_dir / "gpu" / "health.json", result.get("health") or {})
    gpu_records = []
    for path in sorted(run_dir.glob("**/stop.json")) + sorted(run_dir.glob("**/draw-refused-*.json")):
        record = _read_json(path)
        if record:
            gpu_records.append({"path": str(path.relative_to(run_dir)), "record": record})
    _write_json(case_dir / "gpu" / "raw-failures.json", {"records": gpu_records})
    operations = {
        "alignment": (comparison or {}).get("alignment"),
        "comparison_reasons": (comparison or {}).get("reasons"),
        "first_divergence": None,
        "note": "A per-draw semantic prefix is required for operation-level localization.",
    }
    if semantic is not None:
        operations["semantic"] = semantic
        _write_json(case_dir / "operations" / "semantic.json", semantic)
    else:
        _write_json(case_dir / "semantic.json", {
            "status": "missing",
            "reason": "the live campaign stored frame bytes and health, not a per-draw operation prefix",
        })
    _write_json(case_dir / "operations" / "alignment.json", operations)
    if resources is not None:
        _write_json(case_dir / "resources" / "lineage.json", resources)
    else:
        _write_json(case_dir / "resources" / "lineage.json", {
            "schema": "gfx-resource-lineage/v1",
            "status": "missing",
            "reason": "resource events are captured by the selected-event replay path",
        })
    repro = {
        "schema": "gfx-diagnostic-repro/v1",
        "comparison": comparison,
        "frames": frame_files,
        "policy": (result.get("scenario") or {}).get("comparison"),
        "command": f"python3 -m automation.gfx_case compare --case {case_dir}",
        "note": "Re-run the offline comparator on the packaged payloads.",
    }
    _write_json(case_dir / "repro" / "repro.json", repro)
    (case_dir / "analysis.md").write_text(
        _analysis(result, pair, comparison, classification, artifacts), encoding="utf-8"
    )
    packaged = {
        "schema": SCHEMA,
        "classification": classification,
        "case_dir": str(case_dir),
        "files": sorted(
            str(path.relative_to(case_dir)) for path in case_dir.rglob("*") if path.is_file()
        ),
        "total_bytes": sum(path.stat().st_size for path in case_dir.rglob("*") if path.is_file()),
    }
    _write_json(case_dir / "case.json", packaged)
    return packaged


def compare_case(case_dir: Path) -> dict:
    """Re-run the offline comparator from the packaged repro description."""
    case_dir = Path(case_dir)
    repro = _read_json(case_dir / "repro" / "repro.json")
    if repro is None:
        raise ValueError(f"no repro/repro.json in {case_dir}")
    frames = repro.get("frames") or {}
    reference = frames.get("reference")
    candidate = frames.get("candidate")
    if not reference or not candidate:
        result = {"schema": "gfx-case-repro/v1", "status": INCOMPLETE,
                  "reason": "both packaged raw frames are required"}
    else:
        policy = repro.get("policy") or {"policy": "tolerant"}
        compare_policy = {
            "policy": policy.get("policy", "tolerant"),
            "pixel_tolerance": policy.get("max_channel_error", 0),
            "max_mismatched_fraction": policy.get("max_mismatched_fraction", 0.0),
            "max_mean_abs_error": policy.get("max_mean_abs_error", 0.0),
            "allow_scale_normalization": True,
        }
        reference_meta = {
            "width": reference["width"], "height": reference["height"],
            "origin": reference.get("origin"), "pixel_format": reference.get("pixel_format"),
            "sha256": reference.get("sha256"),
        }
        candidate_meta = {
            "width": candidate["width"], "height": candidate["height"],
            "origin": candidate.get("origin"), "pixel_format": candidate.get("pixel_format"),
            "sha256": candidate.get("sha256"),
        }
        try:
            result = gfx_frame_compare.compare_payloads(
                Path(reference["path"]).read_bytes(), reference_meta,
                Path(candidate["path"]).read_bytes(), candidate_meta,
                policy=compare_policy,
                regions=policy.get("regions") or {},
            )
            result["schema"] = "gfx-case-repro/v1"
        except (OSError, KeyError, TypeError, gfx_frame_compare.CompareError) as exc:
            result = {"schema": "gfx-case-repro/v1", "status": INCOMPLETE,
                      "reason": f"{type(exc).__name__}: {exc}"}
    _write_json(case_dir / "repro" / "comparison.json", result)
    return result


def inspect_case(case_dir: Path) -> dict:
    case_dir = Path(case_dir)
    manifest = _read_json(case_dir / "manifest.json") or {}
    result = _read_json(case_dir / "result.json") or {}
    return {
        "schema": SCHEMA,
        "case_dir": str(case_dir),
        "classification": result.get("classification"),
        "session_id": manifest.get("session_id"),
        "scenario": manifest.get("scenario"),
        "stop": result.get("stop"),
        "health": (result.get("health") or {}).get("verdict"),
        "comparison_status": (result.get("comparison") or {}).get("status"),
        "analysis": str(case_dir / "analysis.md"),
        "files": sorted(
            str(path.relative_to(case_dir)) for path in case_dir.rglob("*") if path.is_file()
        ),
    }


def _parser():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--run", required=True, type=Path)
    build.add_argument("--out", type=Path, default=None)
    build.add_argument("--pair-index", type=int, default=None)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--case", required=True, type=Path)
    compare = sub.add_parser("compare")
    compare.add_argument("--case", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "build":
        value = build_case(args.run, out_dir=args.out)
        print(json.dumps(value, indent=2))
        return 0
    if args.command == "compare":
        print(json.dumps(compare_case(args.case), indent=2))
        return 0
    print(json.dumps(inspect_case(args.case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
