"""Portable Strategy B replay case.

A case contains the captured workload, the translated shaders, the original
OpenGL checkpoints, the Metal replay outputs, the dependency graph, the
comparison report and an executable replay command.  All paths in the manifest
are relative and every payload is content-addressed, so the directory can be
moved without rewriting anything.

The package is built from the existing capture/replay tooling; it does not
introduce another renderer or case format for live diagnostics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from automation import gfx_frame_graph, gfx_independent_replay, m6_compare, m6_frame

SCHEMA = "gfx-strategy-b-case/v1"


class CaseError(RuntimeError):
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


def _relative_files(root: Path) -> list[dict]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha(path),
            })
    return files


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _owned_case(path: Path) -> bool:
    try:
        value = json.loads((path / "case.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("schema") == SCHEMA


def prepare_case_output(case_dir: Path, inputs: list[Path]) -> Path:
    """Validate and prepare a case directory without deleting unrelated data."""
    case_dir = case_dir.expanduser().resolve()
    if gfx_independent_replay._protected_output(case_dir):
        raise CaseError("output_protected",
                        f"refusing to replace protected output {case_dir}")
    for source in inputs:
        if gfx_independent_replay._path_overlap(
                case_dir, source.expanduser().resolve()):
            raise CaseError(
                "output_overlap",
                f"case directory {case_dir} overlaps input {source}")
    if case_dir.exists():
        if not case_dir.is_dir():
            raise CaseError("output_conflict", f"{case_dir} is not a directory")
        entries = list(case_dir.iterdir())
        if entries and not _owned_case(case_dir):
            raise CaseError(
                "output_not_owned",
                f"{case_dir} is nonempty and is not a prior portable case")
        if entries:
            shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


def _relocate_capture_seal(case_dir: Path) -> None:
    """Make the copied seal valid for the portable, relocatable capture path.

    The seal's authoritative identity and every content digest are unchanged;
    only the location field is rewritten, with the original retained for
    provenance.  ``load_seal`` accepts the marker only when the case manifest
    declares the ``capture`` directory at this location.
    """
    seal_path = case_dir / "capture" / "frame-seal.json"
    if not seal_path.is_file():
        return
    try:
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    if not isinstance(seal, dict):
        return
    original = seal.get("capture_dir")
    seal["capture_dir"] = "capture"
    if original and original != "capture":
        seal["portable_original_capture_dir"] = original
    _write_json(seal_path, seal)


def package(
    frame_dir: Path | str,
    replay_dir: Path | str,
    case_dir: Path | str,
    *,
    shaders: Path | str | None = None,
    case_id: str = "menu-occ300",
    title: str = "Authentic EU4 title frame through independent Metal replay",
) -> dict:
    """Copy the minimum evidence into a portable case and write its manifest."""
    frame_dir = Path(frame_dir).expanduser().resolve()
    replay_dir = Path(replay_dir).expanduser().resolve()
    case_dir = Path(case_dir).expanduser().resolve()
    report_path = replay_dir / "replay-report.json"
    if not report_path.is_file():
        raise CaseError("missing_replay", f"no replay report in {replay_dir}")
    report = json.loads(report_path.read_text())
    shader_source = gfx_independent_replay._shader_dir(frame_dir, shaders)
    prepare_case_output(case_dir, [frame_dir, replay_dir, shader_source])
    shutil.copytree(frame_dir, case_dir / "capture")
    _relocate_capture_seal(case_dir)
    shutil.copytree(shader_source, case_dir / "shaders")
    (case_dir / "replay").mkdir(parents=True)
    for name in ("replay.json", "replay-provenance.jsonl", "metal-frame.bgra",
                 "checkpoint-request.json"):
        source = replay_dir / name
        if source.is_file():
            shutil.copy2(source, case_dir / "replay" / name)
    for item in report.get("checkpoints") or []:
        # Comparison entries expose the replay output as ``metal_file``;
        # legacy entries may still use ``file``.
        source = Path(str(item.get("metal_file") or item.get("file") or ""))
        if not source.is_absolute():
            source = replay_dir / source
        if source.is_file():
            shutil.copy2(source, case_dir / "replay" / source.name)
    graph = report.get("graph") or gfx_frame_graph.build(case_dir / "capture")
    # Rewrite absolute artifact references in the report to case-relative ones.
    portable_report = json.loads(json.dumps(report))
    portable_report["frame_dir"] = "capture"
    portable_report["out_dir"] = "replay"
    for item in portable_report.get("checkpoints") or []:
        if item.get("metal_file"):
            item["metal_file"] = "replay/" + Path(item["metal_file"]).name
        if item.get("reference_file"):
            item["reference_file"] = "capture/blobs/" + Path(
                item["reference_file"]).name
    if isinstance(portable_report.get("artifacts"), dict):
        portable_report["artifacts"] = {
            key: (Path(value).name if isinstance(value, str) else value)
            for key, value in portable_report["artifacts"].items()
        }
    _write_json(case_dir / "report.json", portable_report)
    _write_json(case_dir / "graph.json", {
        "schema": gfx_frame_graph.SCHEMA,
        "status": graph.get("status"),
        "reasons": graph.get("reasons"),
        "counts": graph.get("counts"),
        "boundary": graph.get("boundary"),
        "external_inputs": graph.get("external_inputs"),
        "renderer_generated": graph.get("renderer_generated"),
        "renderer_generated_sampled": graph.get("renderer_generated_sampled"),
        "unproven": graph.get("unproven"),
    })
    manifest = {
        "schema": SCHEMA,
        "case_id": case_id,
        "title": title,
        "capture": "capture",
        "shaders": "shaders",
        "replay": "replay",
        "report": "report.json",
        "graph": "graph.json",
        "replay_command": [
            "python3.14", "-m", "automation.gfx_replay_case", "replay",
            "--case", ".",
            "--out", "replay-rerun",
        ],
        "verify_command": [
            "python3.14", "-m", "automation.gfx_replay_case", "verify",
            "--case", ".",
        ],
        "replay_status": portable_report.get("status"),
        "graph_status": graph.get("status"),
        "first_divergence": portable_report.get("first_divergence"),
        "files": _relative_files(case_dir),
    }
    _write_json(case_dir / "case.json", manifest)
    (case_dir / "README.md").write_text(_readme(manifest), encoding="utf-8")
    return manifest



def _portable_paths(value, *, replay_dir: str = "replay",
                    capture_dir: str = "capture") -> None:
    """Rewrite known absolute evidence paths in a nested report in place."""
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "metal_file" and isinstance(item, str) and item:
                value[key] = replay_dir + "/" + Path(item).name
            elif key == "reference_file" and isinstance(item, str) and item:
                value[key] = capture_dir + "/blobs/" + Path(item).name
            elif key == "frame_dir" and isinstance(item, str) and item:
                value[key] = capture_dir
            elif key == "metal_result_dir" and isinstance(item, str) and item:
                value[key] = replay_dir
            elif key == "path" and isinstance(item, str) and (
                    "/metal/" in item or "/output/" in item):
                # A conventional absolute path; keep only the basename.
                value[key] = Path(item).name
            else:
                _portable_paths(item, replay_dir=replay_dir,
                                capture_dir=capture_dir)
    elif isinstance(value, list):
        for item in value:
            _portable_paths(item, replay_dir=replay_dir,
                            capture_dir=capture_dir)


def _portable_diagnosis(report: dict, case_dir: Path) -> dict:
    """Rewrite absolute diagnostic evidence paths to case-relative ones."""
    value = json.loads(json.dumps(report))
    value["frame_dir"] = "capture"
    value["metal_result_dir"] = "replay"
    _portable_paths(value)
    for item in value.get("checkpoints") or []:
        if item.get("metal_file"):
            item["metal_file"] = "replay/" + Path(item["metal_file"]).name
        if item.get("reference_file"):
            item["reference_file"] = "capture/blobs/" + Path(
                item["reference_file"]).name
    artifacts = value.get("artifacts")
    if isinstance(artifacts, dict):
        value["artifacts"] = {
            key: (Path(path).name if isinstance(path, str) else path)
            for key, path in artifacts.items()
        }
    case = value.get("case")
    if isinstance(case, dict):
        value["case"] = {
            key: case[key] for key in ("case_id", "title", "status")
            if key in case
        }
    return value


def _diagnosis_shaders(frame_dir: Path, metal_dir: Path,
                       shaders: Path | str | None) -> Path:
    if shaders is not None:
        path = Path(shaders).expanduser().resolve()
        if (path / "shaders.json").is_file():
            return path
        raise CaseError("shaders_missing", str(path))
    for candidate in (frame_dir / "shaders", frame_dir.parent / "translated",
                      metal_dir / "shaders"):
        if (candidate / "shaders.json").is_file():
            return candidate.resolve()
    raise CaseError(
        "shaders_missing",
        "no translated shaders in frame/shaders, sibling translated/ or "
        "the Metal result directory")


def package_diagnosis(frame_dir: Path | str, metal_dir: Path | str,
                      case_dir: Path | str, report: dict, *,
                      case_id: str = "diagnosis-case",
                      title: str = "M7 same-frame differential diagnosis",
                      include_difference: bool = True,
                      shaders: Path | str | None = None,
                      max_bytes: int = 512 * 1024 * 1024) -> dict:
    """Package a PR3 diagnostic report as an immutable, portable case.

    The case contains the original sealed capture, the verified resource blobs
    referenced by the dependency graph, the translated shaders, the Metal
    replay (including requested checkpoints), the diagnostic report, the
    dependency graph and a deterministic reproduction command.  Every payload
    is listed with its SHA-256 in ``case.json`` and rechecked by ``verify``.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = Path(metal_dir).expanduser().resolve()
    case_dir = Path(case_dir).expanduser().resolve()
    shader_source = _diagnosis_shaders(frame_dir, metal_dir, shaders)
    if (max_bytes > 0 and
            sum(path.stat().st_size for path in frame_dir.rglob("*")
                if path.is_file()) > max_bytes):
        raise CaseError(
            "case_too_large",
            f"capture exceeds the {max_bytes}-byte case budget")
    prepare_case_output(case_dir, [frame_dir, metal_dir, shader_source])
    shutil.copytree(frame_dir, case_dir / "capture")
    _relocate_capture_seal(case_dir)
    shutil.copytree(shader_source, case_dir / "shaders")
    (case_dir / "replay").mkdir(parents=True)
    replay_names = (
        "replay.json", "replay-provenance.jsonl", "metal-frame.bgra",
        "checkpoint-request.json", "shadow-report.json", "replay-report.json",
        "comparison.json", "state-coverage.json",
    )
    for name in replay_names:
        source = metal_dir / name
        if source.is_file():
            shutil.copy2(source, case_dir / "replay" / name)
    for item in report.get("checkpoints") or []:
        source = Path(str(item.get("metal_file") or item.get("file") or ""))
        if not source.is_absolute():
            source = metal_dir / source
        if source.is_file():
            shutil.copy2(source, case_dir / "replay" / source.name)
    portable = _portable_diagnosis(report, case_dir)
    _write_json(case_dir / "diagnostic-report.json", portable)
    graph = json.loads(json.dumps(
        report.get("graph") or gfx_frame_graph.build(case_dir / "capture")))
    graph["frame_dir"] = "capture"
    _write_json(case_dir / "dependency-graph.json", graph)
    _write_json(case_dir / "graph.json", {
        "schema": gfx_frame_graph.SCHEMA,
        "status": graph.get("status"),
        "reasons": graph.get("reasons"),
        "counts": graph.get("counts"),
        "boundary": graph.get("boundary"),
        "external_inputs": graph.get("external_inputs"),
        "renderer_generated": graph.get("renderer_generated"),
        "renderer_generated_sampled": graph.get("renderer_generated_sampled"),
        "unproven": graph.get("unproven"),
    })
    difference = None
    if include_difference:
        try:
            final = (report.get("pixels") or {}).get("final") or {}
            width = int(final.get("width") or (report.get("frame") or {}).get("width") or 0)
            height = int(final.get("height") or (report.get("frame") or {}).get("height") or 0)
            if width > 0 and height > 0:
                (case_dir / "difference").mkdir()
                difference = m6_compare.write_difference(
                    case_dir / "capture", case_dir / "replay", width, height,
                    case_dir / "difference")
                difference["sha256"] = _sha(case_dir / "difference" / difference["raw_file"])
        except (OSError, ValueError, m6_compare.CompareError):
            difference = None
    manifest = {
        "schema": SCHEMA,
        "case_id": case_id,
        "title": title,
        "capture": "capture",
        "shaders": "shaders",
        "replay": "replay",
        "diagnostic_report": "diagnostic-report.json",
        "dependency_graph": "dependency-graph.json",
        "graph": "graph.json",
        "difference": difference,
        "diagnose_command": [
            "python3.14", "-m", "automation.gfx_independent_replay",
            "diagnose", "--frame", "capture", "--metal", "replay",
            "--out", "diagnosis", "--require-seal",
        ],
        "replay_command": [
            "python3.14", "-m", "automation.gfx_replay_case", "replay",
            "--case", ".", "--out", "replay-rerun",
        ],
        "verify_command": [
            "python3.14", "-m", "automation.gfx_replay_case", "verify",
            "--case", ".",
        ],
        "classification": report.get("classification"),
        "first_divergence": report.get("first_divergence"),
        "qualification": report.get("qualification"),
        "files": [],
    }
    manifest["files"] = _relative_files(case_dir)
    _write_json(case_dir / "case.json", manifest)
    (case_dir / "README.md").write_text(_readme(manifest), encoding="utf-8")
    return manifest


def _readme(manifest: dict) -> str:
    divergence = manifest.get("first_divergence") or {}
    lines = [
        f"# Strategy B replay case: {manifest.get('case_id')}",
        "",
        manifest.get("title", ""),
        "",
        f"- Capture: `{manifest.get('capture')}`",
        f"- Graph status: `{manifest.get('graph_status') or (manifest.get('qualification') or {}).get('axes', {}).get('operation_coverage')}`",
        f"- Replay status: `{manifest.get('replay_status') or manifest.get('classification')}`",
        f"- Classification: `{manifest.get('classification')}`",
        f"- First divergence: `{divergence.get('kind')}`"
        + (f" at event {divergence.get('event')}" if divergence.get("event") is not None else ""),
        "",
        "## Replay",
        "",
        "From this directory:",
        "",
        "```sh",
        " ".join(manifest.get("replay_command") or []),
        "```",
        "",
        "The replay uses the existing M6 Metal replay host and the captured",
        "OpenGL frame. Renderer-generated intermediates are produced by Metal;",
        "captured OpenGL intermediate images are reference checkpoints only.",
        "",
        "## Verify",
        "",
        "```sh",
        " ".join(manifest.get("verify_command") or []),
        "```",
        "",
        "Every file digest in `case.json` is rechecked before the case is used.",
    ]
    return "\n".join(lines) + "\n"


def verify(case_dir: Path | str) -> dict:
    case_dir = Path(case_dir).expanduser().resolve()
    manifest_path = case_dir / "case.json"
    if not manifest_path.is_file():
        raise CaseError("missing_manifest", str(manifest_path))
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != SCHEMA:
        raise CaseError("schema", "unexpected case schema")
    problems = []
    for item in manifest.get("files") or []:
        path = case_dir / item["path"]
        if not path.is_file():
            problems.append(f"missing {item['path']}")
            continue
        if path.stat().st_size != item["bytes"]:
            problems.append(f"size drift {item['path']}")
        elif _sha(path) != item["sha256"]:
            problems.append(f"digest drift {item['path']}")
    required = ["capture/frame-trace.json", "capture/frame-events.jsonl",
                "shaders/shaders.json", "graph.json"]
    if manifest.get("diagnostic_report"):
        required.append(str(manifest["diagnostic_report"]))
        required.append("dependency-graph.json")
    else:
        required.append("report.json")
    for name in required:
        if not (case_dir / name).is_file():
            problems.append(f"missing required {name}")
    graph: dict = {}
    try:
        graph = json.loads((case_dir / "graph.json").read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        problems.append("graph.json is unreadable")
    return {
        "schema": "gfx-strategy-b-case-verification/v1",
        "case_id": manifest.get("case_id"),
        "ok": not problems,
        "problems": problems,
        "files": len(manifest.get("files") or []),
        "graph_status": graph.get("status"),
        "replay_status": manifest.get("replay_status"),
    }


def replay_case(case_dir: Path | str, out_dir: Path | str,
                *, faults: dict | None = None) -> dict:
    case_dir = Path(case_dir).expanduser().resolve()
    verification = verify(case_dir)
    if not verification["ok"]:
        raise CaseError("verification", "; ".join(verification["problems"]))
    return gfx_independent_replay.replay(
        case_dir / "capture", out_dir, shaders=case_dir / "shaders",
        faults=faults,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable Strategy B replay case")
    sub = parser.add_subparsers(dest="command", required=True)
    package_parser = sub.add_parser("package")
    package_parser.add_argument("--frame", required=True, type=Path)
    package_parser.add_argument("--replay", required=True, type=Path)
    package_parser.add_argument("--case", required=True, type=Path)
    package_parser.add_argument("--shaders", type=Path, default=None)
    package_parser.add_argument("--case-id", default="menu-occ300")
    package_parser.add_argument("--title",
                                default="Authentic EU4 title frame through independent Metal replay")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--case", required=True, type=Path)
    replay_parser = sub.add_parser("replay")
    replay_parser.add_argument("--case", required=True, type=Path)
    replay_parser.add_argument("--out", required=True, type=Path)
    replay_parser.add_argument("--disable-render-target-vflip", action="store_true")
    replay_parser.add_argument("--sampler-shift", type=int, default=None)
    replay_parser.add_argument("--sampler-shift-min", type=int, default=7)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "package":
            value = package(args.frame, args.replay, args.case,
                            shaders=args.shaders, case_id=args.case_id,
                            title=args.title)
            print(json.dumps({key: value[key] for key in
                              ("schema", "case_id", "graph_status",
                               "replay_status")}, indent=2))
            return 0
        if args.command == "verify":
            value = verify(args.case)
            print(json.dumps(value, indent=2))
            return 0 if value["ok"] else 2
        if args.command == "replay":
            faults: dict = {}
            if args.disable_render_target_vflip:
                faults["disable_render_target_vflip"] = True
            if args.sampler_shift is not None:
                faults["sampler_shift"] = args.sampler_shift
                faults["sampler_shift_min"] = args.sampler_shift_min
            value = replay_case(args.case, args.out, faults=faults)
            outcome = value.get("outcome") or {}
            print(json.dumps({
                "status": value.get("status"),
                "outcome": outcome,
                "qualified": outcome.get("qualified"),
                "final": (value.get("final") or {}).get("status"),
                "first_divergence": value.get("first_divergence"),
            }, indent=2))
            return gfx_independent_replay.qualification_exit_code(value)
    except (CaseError, gfx_independent_replay.ReplayError,
            m6_frame.FrameError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
