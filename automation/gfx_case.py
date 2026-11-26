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
from pathlib import Path

from automation import gfx_align, gfx_frame_compare, gfx_qualification, gfx_replay_case
from automation.gfx_artifacts import sha256_file

SCHEMA = gfx_replay_case.LEGACY_SCHEMA

CONFIRMED_RENDERING = "rendering_difference"
UNALIGNED = "unaligned"
GPU_FAILURE = "gpu_execution_failure"
PROCESS_FAILURE = "process_failure"
INSTRUMENTATION = "instrumentation_failure"
INCOMPLETE = gfx_replay_case.LEGACY_INCOMPLETE
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
        "sha256": sha256_file(dest),
    }


def _classify(result: dict, pair: dict | None, comparison: dict | None) -> str:
    stop_reason = (result.get("stop") or {}).get("reason")
    for role, process in sorted((result.get("processes") or {}).items()):
        if not isinstance(process, dict):
            continue
        code = process.get("exit_code_before_stop", process.get("exit_code"))
        if code in (-9, -15) and stop_reason != "process_exit":
            continue
        if type(code) is int and code != 0:
            return PROCESS_FAILURE
    for termination in result.get("terminations") or []:
        if not isinstance(termination, dict):
            continue
        code = termination.get("returncode")
        if code in (-9, -15) and stop_reason != "process_exit":
            continue
        if type(code) is int and code != 0:
            return PROCESS_FAILURE
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
        return _difference_classification(result, pair, comparison)
    if status == gfx_frame_compare.PASS:
        return _pass_classification(result, pair, comparison)
    if status in (gfx_frame_compare.UNVERIFIED, "not_compared", None):
        return UNALIGNED
    return INCOMPLETE


_CAMPAIGN_FLOOR = {
    "alignment_failure": UNALIGNED,
    "incomplete": INCOMPLETE,
    "gpu_execution_failure": GPU_FAILURE,
    "infrastructure_failure": INSTRUMENTATION,
    "process_failure": PROCESS_FAILURE,
}

# Campaign vocabulary (`classify_campaign`), which differs from the case
# vocabulary for incomplete/instrumentation statuses.
_CAMPAIGN_STATUSES = frozenset({
    "match", "rendering_difference", "gpu_execution_failure",
    "alignment_failure", "incomplete", "infrastructure_failure",
    "process_failure",
})


def _text(value) -> str | None:
    return value if isinstance(value, str) and value else None


def campaign_status(result: dict) -> dict:
    """Resolve the source campaign's authoritative qualification.

    A packaged case may only report what its campaign established.  Both the
    recorded ``classification`` and the ``qualification`` record must exist and
    agree, and the ``qualified`` flag must agree with the status.  An absent,
    unknown or contradictory campaign status is missing evidence, never an
    unqualified pass.
    """
    classification = _text(result.get("classification"))
    qualification = result.get("qualification")
    qualification = qualification if isinstance(qualification, dict) else None
    status = _text(qualification.get("status")) if qualification else None
    qualified = qualification.get("qualified") if qualification else None
    if not isinstance(qualified, bool):
        qualified = None
    problems: list[str] = []
    if classification is None:
        problems.append("the source campaign has no classification")
    if qualification is None:
        problems.append("the source campaign has no qualification record")
    elif status is None:
        problems.append("the source campaign has no qualification status")
    if (classification is not None and status is not None
            and classification != status):
        problems.append(
            "campaign status is contradictory: "
            f"classification {classification!r}, qualification {status!r}")
    resolved = classification if classification is not None else status
    if resolved is not None and resolved not in _CAMPAIGN_STATUSES:
        problems.append(f"campaign status {resolved!r} is unknown")
    if qualification is not None and qualified is None:
        problems.append("the campaign qualification flag is absent")
    if resolved is not None and qualified is not None:
        if resolved == MATCH and qualified is not True:
            problems.append("the campaign match is not marked qualified")
        elif resolved != MATCH and qualified is not False:
            problems.append(f"the campaign {resolved} is marked qualified")
    consistent = not problems
    return {
        "status": resolved if consistent else None,
        "recorded_status": resolved,
        "qualified": qualified,
        "problems": problems,
        "consistent": consistent,
    }


def _campaign_floor(result: dict) -> str:
    """The campaign status a case may not exceed, or INCOMPLETE when unresolved."""
    campaign = campaign_status(result)
    if not campaign["consistent"]:
        return INCOMPLETE
    if campaign["status"] == MATCH or campaign["status"] == CONFIRMED_RENDERING:
        return campaign["status"]
    return _CAMPAIGN_FLOOR.get(campaign["status"] or "", INCOMPLETE)


def _pass_classification(result: dict, pair: dict | None,
                         comparison: dict | None) -> str:
    """Local pixel equality is not campaign qualification.

    The case may report ``match`` only when its source campaign is a consistent,
    qualified ``match``; a passing pair from an incomplete/alignment-failed/
    unqualified campaign keeps the campaign floor.  Missing or contradictory
    campaign evidence is conservatively incomplete.
    """
    floor = _campaign_floor(result)
    if floor == CONFIRMED_RENDERING:
        # The campaign confirmed a difference; a locally equal pair cannot
        # reproduce it, so the package stays inconclusive.
        return INCOMPLETE
    if floor != MATCH:
        return floor
    pair_status = (pair or {}).get("status")
    alignment = (comparison or {}).get("alignment")
    alignment_status = (alignment or {}).get("status") if isinstance(
        alignment, dict) else None
    if pair_status != gfx_align.ALIGNED or alignment_status != gfx_align.ALIGNED:
        return UNALIGNED
    return MATCH


def difference_evidence(result: dict, pair: dict | None,
                        comparison: dict | None) -> dict:
    """The campaign-wide renderer-difference predicate for this case."""
    return gfx_qualification.renderer_difference_evidence(
        result, pair=pair, comparison=comparison)


def _difference_classification(result: dict, pair: dict | None,
                               comparison: dict | None) -> str:
    """Never claim more than the campaign evidence supports."""
    floor = _campaign_floor(result)
    if floor == MATCH:
        # A campaign qualified as a match cannot contain a differing pair.
        return INCOMPLETE
    if floor != CONFIRMED_RENDERING:
        return floor
    evidence = difference_evidence(result, pair, comparison)
    return CONFIRMED_RENDERING if evidence["supported"] else INCOMPLETE


def _choose_pair(result: dict, pair_index: int | None = None) -> dict | None:
    frames = result.get("frames") or {}
    summary = frames.get("comparison") or {}
    if pair_index is not None:
        raise ValueError(
            "pair_index selection is not available in this result schema; "
            "inspect the recorded first/last pair instead"
        )
    if summary.get("first_failure"):
        return summary["first_failure"]
    return frames.get("last_pair")


def _analysis(result: dict, pair: dict | None, comparison: dict | None,
              classification: str, frame_files: dict) -> str:
    lines = [
        "# Diagnostic case", "",
        f"Session `{result.get('session_id')}` · **{classification}**", "",
        "## Confirmed observations", "",
        "- [Result](result.json) records the stop reason and selected pair.",
        "- [Frame comparison](difference/statistics.json) records alignment, "
        "pixel and region metrics, and the packaged frame identities.",
        "- [GPU health](gpu/health.json) and [raw failures](gpu/raw-failures.json) "
        "record execution observations.",
        "- [Manifest](manifest.json) records process provenance and the artifact budget.",
    ]
    statistics = (comparison or {}).get("statistics") or {}
    if statistics:
        lines += [
            f"- Differing pixels: {statistics.get('mismatched_pixels')} / "
            f"{statistics.get('pixels')} ({statistics.get('mismatched_fraction')}).",
            f"- Max channel error: {statistics.get('max_channel_error')}. "
            f"Mean absolute error: {statistics.get('mean_abs_error')}.",
        ]
    mirrored = (comparison or {}).get("mirrored_regions")
    if mirrored:
        lines.append("- Horizontal mirroring was observed in " + ", ".join(mirrored) + ".")
    lines += [
        "", "## Excluded explanations", "",
        "- The comparator accounts for row origin and pixel format. A scaled "
        "candidate cannot qualify as bit-identical under an exact policy.",
        "", "## Remaining hypotheses", "",
    ]
    status = (comparison or {}).get("status")
    if classification == CONFIRMED_RENDERING:
        lines.append("- The aligned frame differs. Inspect the first differing "
                     "pass before attributing the change to a particular operation.")
    elif classification == PROCESS_FAILURE:
        lines.append("- Inspect the recorded process exit and crash logs for "
                     "the execution failure.")
    elif classification == GPU_FAILURE:
        lines.append("- Inspect the first GPU or API failure before comparing pixels.")
    elif status in (gfx_frame_compare.PASS, gfx_frame_compare.FAIL) and classification != MATCH:
        campaign = campaign_status(result)
        blockers = campaign["problems"]
        if status == gfx_frame_compare.FAIL and classification == INCOMPLETE:
            blockers = blockers + difference_evidence(result, pair, comparison)["problems"]
        detail = "; ".join(blockers) or f"campaign verdict `{campaign['recorded_status']}`"
        scope = ("Local pixel equality does not qualify the campaign"
                 if status == gfx_frame_compare.PASS else "Local pixels differ, but renderer attribution is withheld")
        lines.append(f"- {scope} ({detail}).")
    elif classification == UNALIGNED:
        lines.append("- The pair needs corroborating frame correspondence.")
    else:
        lines.append("- The package establishes no renderer difference.")
    lines += ["", "## Missing evidence", ""]
    for role in ("reference", "candidate"):
        if role not in frame_files:
            lines.append(f"- Packaged {role} pixels.")
    lines += [
        "- A per-draw operation prefix for pass-level attribution.",
        "", "## Next inspection point", "",
        "- Replay the packaged image comparison and inspect the worst region. "
        "Use a retained per-draw capture to localize the difference when available.",
        "", "Another EU4 launch is needed for this package only if a targeted "
        "observation is required and the retained evidence cannot supply it.", "",
    ]
    return "\n".join(lines)


def build_case(run_dir: Path, *, out_dir: Path | None = None,
               pair_index: int | None = None,
               ) -> dict:
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
    for name in ("frames", "difference", "gpu", "repro"):
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
            "path": f"frames/{role}-frame-{index}.bgra",
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
                reference_meta = frame_files["reference"]
                reference_image = gfx_frame_compare.decode_frame(
                    (case_dir / reference_meta["path"]).read_bytes(),
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
                difference = {"scale": None, "method": "top-left RGBA difference, amplification x4"}
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
    repro = {
        "schema": "gfx-diagnostic-repro/v1",
        "kind": "image-comparison",
        "comparison": comparison,
        "frames": frame_files,
        "policy": (result.get("scenario") or {}).get("comparison"),
        "command": f"python3 -m automation.agent_tools eu4_replay case --case {case_dir}",
        "note": "Re-run the offline comparator on the packaged payloads.",
    }
    _write_json(case_dir / "repro" / "repro.json", repro)
    (case_dir / "analysis.md").write_text(
        _analysis(result, pair, comparison, classification, frame_files),
        encoding="utf-8"
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
        value = build_case(args.run, out_dir=args.out, pair_index=args.pair_index)
        print(json.dumps(value, indent=2))
        return 0
    if args.command == "compare":
        print(json.dumps(gfx_replay_case.compare_legacy_case(args.case), indent=2))
        return 0
    print(json.dumps(gfx_replay_case.inspect_legacy_case(args.case), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
