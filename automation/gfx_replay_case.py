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
import os
import re
import shutil
import tempfile
from pathlib import Path

from automation import gfx_frame_graph, gfx_independent_replay, m6_compare, m6_frame

SCHEMA = "gfx-strategy-b-case/v1"
SEQUENCE_SCHEMA = "gfx-strategy-b-case/v2"
SEQUENCE_MANIFEST_SCHEMA = "gfx-strategy-b-case-sequence/v1"
CASE_SCHEMAS = (SCHEMA, SEQUENCE_SCHEMA)


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
    return isinstance(value, dict) and value.get("schema") in CASE_SCHEMAS


def _validate_case_destination(case_dir: Path, inputs: list[Path]) -> Path:
    """Validate a case destination without creating or deleting anything."""
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
    return case_dir


def prepare_case_output(case_dir: Path, inputs: list[Path]) -> Path:
    """Validate and prepare a case directory without deleting unrelated data."""
    case_dir = _validate_case_destination(case_dir, inputs)
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)
    return case_dir


class _StagedCase:
    """Build a case in a bounded staging directory, then publish it atomically.

    A failed or over-budget build never deletes or overwrites a source input,
    and the published case directory only ever contains a complete package.
    """

    def __init__(self, case_dir: Path, inputs: list[Path]):
        self.destination = _validate_case_destination(case_dir, inputs)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.path = Path(tempfile.mkdtemp(
            prefix=f".{self.destination.name}-staging-",
            dir=self.destination.parent))

    def commit(self) -> Path:
        # Move the previous case aside first, then publish.  Replacement never
        # leaves the destination missing: if the publish fails the previous
        # directory is restored.
        backup = None
        if self.destination.exists():
            for attempt in range(1000):
                candidate = self.destination.with_name(
                    f".{self.destination.name}-previous-{os.getpid()}-{attempt}")
                if not candidate.exists():
                    backup = candidate
                    break
            if backup is None:
                raise CaseError(
                    "output_conflict",
                    f"cannot stage a replacement for {self.destination}")
            os.replace(self.destination, backup)
        try:
            os.replace(self.path, self.destination)
        except OSError:
            if backup is not None and not self.destination.exists():
                os.replace(backup, self.destination)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        return self.destination

    def discard(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def _case_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*")
               if path.is_file())


REPLAY_ARTIFACTS = (
    "replay.json", "replay-provenance.jsonl", "metal-frame.bgra",
    "checkpoint-request.json", "shadow-report.json", "replay-report.json",
    "comparison.json", "state-coverage.json",
)
# The pre-write estimate only decides the fast path.  The staged package is
# measured before publication, so manifest/README formatting overhead is
# charged against the configured budget there rather than estimated here.

# Structurally required payloads of a complete v2 sequence package.  The
# manifest inventory must contain each of these; verify rechecks size and
# digest for every entry.  Per-frame and checkpoint payloads are added from
# the sequence manifest.
REQUIRED_SEQUENCE_PAYLOADS = (
    "sequence.json",
    "shaders/shaders.json",
    "diagnostic-report.json",
    "dependency-graph.json",
    "graph.json",
)


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*")
               if path.is_file())


def _tree_bytes_files(roots: list[Path]) -> int:
    return sum(path.stat().st_size for root in roots
               for path in root.rglob("*") if path.is_file())


def _normalize_metadata_paths(value):
    """Basename path-valued provenance fields the v2 relocation rewrites."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if (key in ("dir", "capture_dir") and isinstance(item, str)
                    and item):
                result[key] = Path(item).name
            else:
                result[key] = _normalize_metadata_paths(item)
        return result
    if isinstance(value, list):
        return [_normalize_metadata_paths(item) for item in value]
    return value


def _normalized_frame_bytes(frames: list[Path]) -> int:
    """Content bytes independent of capture-machine directory names.

    Sequence/seal metadata is measured in the same normalized form the v2
    relocation writes, so two identical captures at different paths report the
    same source size.
    """
    total = 0
    for frame in frames:
        for path in frame.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(frame)
            if relative.parts and relative.parts[0] == "shaders":
                # The effective translation directory is counted once below;
                # frame-local shader copies must not be double-counted.
                continue
            if path.name in ("frame-sequence.json", "frame-seal.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    total += path.stat().st_size
                    continue
                total += len(json.dumps(
                    _normalize_metadata_paths(document), indent=2) + "\n")
            else:
                total += path.stat().st_size
    return total


def _replay_artifact_bytes(metal_dir: Path, report: dict) -> int:
    total = 0
    for name in REPLAY_ARTIFACTS:
        path = metal_dir / name
        if path.is_file():
            total += path.stat().st_size
    for item in report.get("checkpoints") or []:
        source = Path(str(item.get("metal_file") or item.get("file") or ""))
        if not source.is_absolute():
            source = metal_dir / source
        if source.is_file():
            total += source.stat().st_size
    return total


def _difference_bytes(report: dict) -> int:
    final = (report.get("pixels") or {}).get("final") or {}
    width = int(final.get("width") or (report.get("frame") or {}).get("width") or 0)
    height = int(final.get("height") or (report.get("frame") or {}).get("height") or 0)
    if width <= 0 or height <= 0:
        return 0
    return width * height * 4


def _budget_missing(required_bytes: int, max_bytes: int, *,
                    required_frames: int | None = None,
                    detail: str) -> list[dict]:
    entry = {
        "kind": "artifact_budget",
        "reason": "artifact_budget",
        "required_bytes": required_bytes,
        "max_bytes": max_bytes,
        "detail": detail,
    }
    if required_frames is not None:
        entry["required_frames"] = required_frames
    return [entry]


def _incomplete_case(
    destination: Path,
    *,
    metal_dir: Path,
    report: dict,
    missing: list[dict],
    schema: str,
    case_id: str,
    title: str,
    inputs: list[Path] | None = None,
    reason: str = "artifact_budget",
    required_bytes: int = 0,
    max_bytes: int = 0,
) -> dict:
    """Publish a bounded summary when the reproduction is not complete.

    The summary contains the structured reason, the missing dependencies, the
    required byte estimate (when the reason is the artifact budget) and the
    scrubbed diagnostics.  It never publishes a replay command and is
    explicitly marked incomplete.  If even this minimum summary exceeds the
    budget, packaging fails with an explicit ``case_budget`` error instead of
    writing an oversized case.
    """
    staged = _StagedCase(destination, [metal_dir, *(inputs or [])])
    work = staged.path
    try:
        reproduction = {
            "status": "incomplete",
            "independently_reproducible": False,
            "reason": reason,
            "status_reason": reason,
            "required_bytes": required_bytes,
            "max_bytes": max_bytes,
            "missing": missing,
        }
        _write_json(work / "reproduction-incomplete.json", reproduction)
        replacements = {str(metal_dir): "replay", str(work): "."}
        portable = _scrub_paths(
            _portable_diagnosis(report, work, "replay"), replacements)
        _write_json(work / "diagnostic-report.json", portable)
        graph = json.loads(json.dumps(
            report.get("graph") or gfx_frame_graph.build(metal_dir)))
        graph["frame_dir"] = "replay"
        _portable_paths(graph, replay_dir="replay", capture_dir="replay")
        graph = _scrub_paths(graph, replacements)
        _write_json(work / "dependency-graph.json", graph)
        _write_json(work / "graph.json", {
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
            "schema": schema,
            "case_id": case_id,
            "title": title,
            "diagnostic_report": "diagnostic-report.json",
            "dependency_graph": "dependency-graph.json",
            "graph": "graph.json",
            "reproduction": reproduction,
            "classification": report.get("classification"),
            "first_divergence": report.get("first_divergence"),
            "qualification": report.get("qualification"),
            "replay_command": [],
            "verify_command": [
                "python3.14", "-m", "automation.gfx_replay_case", "verify",
                "--case", ".",
            ],
            "files": [],
        }
        (work / "README.md").write_text(_readme(manifest), encoding="utf-8")
        manifest["files"] = [
            item for item in _relative_files(work)
            if item["path"] != "case.json"]
        _write_json(work / "case.json", manifest)
        total = _case_bytes(work)
        if max_bytes > 0 and total > max_bytes:
            raise CaseError(
                "case_budget",
                f"the minimum diagnostic summary needs {total} bytes, which "
                f"exceeds the {max_bytes}-byte case budget")
        staged.commit()
        return manifest
    except Exception:
        staged.discard()
        raise


def _relocate_capture_sequence(case_dir: Path) -> None:
    """Make a single-frame case independent of any predecessor directory.

    A capture with carried renderer output cannot be reproduced from one frame;
    refusing is safer than packaging a case whose history points at an
    original campaign directory.
    """
    path = case_dir / "capture" / "frame-sequence.json"
    if not path.is_file():
        return
    document = _json(path)
    carried = document.get("carried_renderer_outputs") or []
    if carried:
        raise CaseError(
            "missing_predecessor",
            "single-frame case declares carried renderer output: " +
            ", ".join(map(str, carried)))
    original = document.get("previous")
    document["portable_original_sequence_index"] = document.get(
        "sequence_index")
    document["portable_original_campaign_frames"] = document.get(
        "campaign_frames")
    if isinstance(original, dict):
        document["portable_original_previous"] = original
    document["sequence_index"] = 1
    document["campaign_frames"] = 1
    document["previous"] = None
    _write_json(path, document)


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
    _relocate_capture_sequence(case_dir)
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
            elif key == "path" and isinstance(item, str) and Path(item).is_absolute():
                # A conventional absolute artifact path; keep only the
                # basename so the report is relocatable.
                value[key] = Path(item).name
            elif key == "dir" and isinstance(item, str) and Path(item).is_absolute():
                # Resource-history provenance must not point at the original
                # campaign directory in a portable case.
                value[key] = capture_dir
            else:
                _portable_paths(item, replay_dir=replay_dir,
                                capture_dir=capture_dir)
    elif isinstance(value, list):
        for item in value:
            _portable_paths(item, replay_dir=replay_dir,
                            capture_dir=capture_dir)


def _portable_diagnosis(report: dict, case_dir: Path,
                        capture_dir: str = "capture") -> dict:
    """Rewrite absolute diagnostic evidence paths to case-relative ones."""
    value = json.loads(json.dumps(report))
    value["frame_dir"] = capture_dir
    value["metal_result_dir"] = "replay"
    _portable_paths(value, replay_dir="replay", capture_dir=capture_dir)
    for item in value.get("checkpoints") or []:
        if item.get("metal_file"):
            item["metal_file"] = "replay/" + Path(item["metal_file"]).name
        if item.get("reference_file"):
            item["reference_file"] = capture_dir + "/blobs/" + Path(
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


def _valid_shader_dir(path: Path) -> bool:
    return path.is_dir() and (path / "shaders.json").is_file()


def _frame_shader_dir(frame_dir: Path, metal_dir: Path,
                      shaders: Path | str | None,
                      frame_shaders: dict | None = None) -> Path | None:
    """Resolve the shader directory a frame's draws were captured against.

    A frame-local ``shaders/`` directory wins, mirroring the sequence CLI's
    ``<frame>/shaders`` override.  A live coordinator keeps each frame's
    translation beside that frame's Metal output (for example
    ``shadow/frame-000003/shaders``), so an explicit per-frame mapping takes
    precedence over the single fallback.  The explicitly supplied directory is
    the fallback for frames without their own translation; the sibling
    ``translated/`` and Metal result directories are the legacy fallbacks.
    Returns ``None`` when no verified translation exists.
    """
    if frame_shaders:
        # The live coordinator's mapping records the translation its shadow
        # host actually executed.  It wins over a possibly stale
        # ``<frame>/shaders`` left by a previous offline translation.
        mapped = (frame_shaders.get(frame_dir)
                  or frame_shaders.get(str(frame_dir))
                  or frame_shaders.get(str(frame_dir.resolve())))
        if mapped is not None:
            path = Path(mapped).expanduser().resolve()
            if _valid_shader_dir(path):
                return path
            raise CaseError("shaders_missing", str(path))
    frame_local = (frame_dir / "shaders").resolve()
    if _valid_shader_dir(frame_local):
        return frame_local
    if shaders is not None:
        path = Path(shaders).expanduser().resolve()
        if _valid_shader_dir(path):
            return path
        raise CaseError("shaders_missing", str(path))
    for candidate in (frame_dir.parent / "translated", metal_dir / "shaders"):
        candidate = candidate.resolve()
        if _valid_shader_dir(candidate):
            return candidate
    return None


def _frame_shader_selection(frame_dir: Path, directory: Path | None,
                            shaders: Path | str | None,
                            frame_shaders: dict | None) -> str:
    """Name the authoritative translation source selected for a frame."""
    if directory is None:
        return "missing"
    selected = directory.resolve()
    if frame_shaders:
        mapped = (frame_shaders.get(frame_dir)
                  or frame_shaders.get(str(frame_dir))
                  or frame_shaders.get(str(frame_dir.resolve())))
        if mapped is not None and Path(mapped).expanduser().resolve() == selected:
            return "live_coordinator_mapping"
    if (frame_dir / "shaders").resolve() == selected:
        return "frame_local"
    if shaders is not None and Path(shaders).expanduser().resolve() == selected:
        return "explicit_fallback"
    if (frame_dir.parent / "translated").resolve() == selected:
        return "sibling_translated"
    if (frame_dir / "shaders").resolve() != selected:
        return "metal_result_or_legacy"
    return "unknown"


def _single_frame_shader_dir(frame: Path, metal_dir: Path,
                             shaders: Path | str | None,
                             frame_shaders: dict | None) -> Path:
    directory = _frame_shader_dir(frame, metal_dir, shaders, frame_shaders)
    if directory is None:
        raise CaseError(
            "shaders_missing",
            f"no translated shader directory for {frame}")
    return directory


def _shader_member(shader_dir: Path, name) -> Path | None:
    """Resolve a declared shader payload and require it to stay inside the dir."""
    if not isinstance(name, str) or not name:
        return None
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = shader_dir.resolve()
    target = (shader_dir / relative).resolve()
    if target != root and root not in target.parents:
        return None
    return target if target.is_file() else None


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _valid_sha256(value) -> bool:
    return isinstance(value, str) and bool(_SHA256_PATTERN.match(value))


def _frame_shader_identity_problems(frame_dir: Path, shader_dir: Path,
                                    index: int) -> list[dict]:
    """Validate one frame's shader provenance, identically for package/verify.

    Every draw-referenced program must have a translated entry whose vertex and
    pixel stages are usable, and each stage used by a captured draw must carry
    an authoritative 64-hex captured identity that equals the translation's
    ``source_sha256``.  Missing, malformed, contradictory or mismatched
    identities are reported; they are never treated as "not applicable".
    """
    problems: list[dict] = []

    def add(kind: str, detail: str) -> None:
        problems.append({"kind": kind, "frame": index, "detail": detail})

    try:
        report = _json(shader_dir / "shaders.json")
    except (OSError, UnicodeError, json.JSONDecodeError, CaseError) as exc:
        return [{
            "kind": "shader_artifact", "frame": index,
            "detail": f"shader report is unreadable: {exc}",
        }]
    programs = {
        int(program.get("program")): program
        for program in report.get("programs") or []
        if isinstance(program, dict)
        and isinstance(program.get("program"), int)
    }
    # Per program and stage: captured identities observed in draw events plus
    # any contradictory repeats.
    captured: dict[int, dict[str, set[str]]] = {}
    captured_stages: dict[int, set[str]] = {}
    events_path = frame_dir / "frame-events.jsonl"
    try:
        event_lines = events_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        event_lines = []
    for line in event_lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "draw":
            continue
        program = event.get("program")
        if not isinstance(program, dict):
            continue
        try:
            name = int(program.get("name"))
        except (TypeError, ValueError):
            continue
        shaders = program.get("shaders")
        if not isinstance(shaders, list) or not shaders:
            add("shader_identity",
                f"program {name} draw has no captured shader-stage entries")
            continue
        for item in shaders:
            if not isinstance(item, dict):
                add("shader_identity",
                    f"program {name} has a malformed captured stage entry")
                continue
            stage_name = str(item.get("stage") or "")
            captured_stages.setdefault(name, set()).add(stage_name)
            digest = item.get("sha256")
            if not _valid_sha256(digest):
                add("shader_identity",
                    f"program {name} {stage_name or '?'} has no valid captured "
                    "sha256 identity")
                continue
            captured.setdefault(name, {}).setdefault(
                stage_name, set()).add(str(digest))
    for name in _frame_programs(frame_dir):
        program = programs.get(name)
        if program is None:
            add("shader_artifact",
                f"program {name} has no translated shader entry")
            continue
        if program.get("status") != "ok":
            add("shader_artifact",
                f"program {name} translation status is "
                f"{program.get('status')!r}")
            continue
        stages = program.get("stages")
        if not isinstance(stages, dict):
            add("shader_artifact", f"program {name} has no stages")
            continue
        for stage_name in ("vertex", "pixel"):
            stage = stages.get(stage_name)
            if not isinstance(stage, dict):
                add("shader_artifact",
                    f"program {name} has no {stage_name} stage")
                continue
            msl_file = stage.get("msl_file")
            if _shader_member(shader_dir, msl_file) is None:
                add("shader_artifact",
                    f"program {name} {stage_name} MSL payload is missing or "
                    f"outside the translation: {msl_file!r}")
            for field in ("glsl_file", "spv_file"):
                declared = stage.get(field)
                if declared and _shader_member(shader_dir, declared) is None:
                    add("shader_artifact",
                        f"program {name} {stage_name} {field} is missing or "
                        f"outside the translation: {declared!r}")
            if not _valid_sha256(stage.get("source_sha256")):
                add("shader_identity",
                    f"program {name} {stage_name} translation has no valid "
                    "captured stage identity")
        for required_stage in ("vertex", "pixel"):
            if required_stage not in captured_stages.get(name, set()):
                add("shader_identity",
                    f"program {name} draw has no captured {required_stage} "
                    "stage entry")
        for stage_name, identities in captured.get(name, {}).items():
            if len(identities) > 1:
                add("shader_identity",
                    f"program {name} {stage_name} has conflicting captured "
                    f"sha256 identities: {sorted(identities)}")
                continue
            captured_sha = next(iter(identities))
            stage = stages.get(stage_name)
            if not isinstance(stage, dict):
                add("shader_identity",
                    f"program {name} captured stage {stage_name!r} has no "
                    "translation")
                continue
            source_sha = stage.get("source_sha256")
            if not _valid_sha256(source_sha):
                add("shader_identity",
                    f"program {name} {stage_name} translation has no valid "
                    "captured stage identity")
            elif str(source_sha) != captured_sha:
                add("shader_identity",
                    f"program {name} {stage_name} translated identity "
                    f"{source_sha} disagrees with captured {captured_sha}")
    return problems


def _frame_programs(frame_dir: Path) -> list[int]:
    """Programs referenced by draw events, in stable numeric order."""
    programs: set[int] = set()
    events = frame_dir / "frame-events.jsonl"
    try:
        lines = events.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("kind") != "draw":
            continue
        program = event.get("program")
        if isinstance(program, dict):
            try:
                programs.add(int(program.get("name")))
            except (TypeError, ValueError):
                continue
    return sorted(programs)


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

    ``max_bytes`` bounds the complete generated package (captures, shaders,
    replay artifacts, checkpoint readbacks, manifests and difference image).
    When the requested reproduction cannot fit, a bounded summary marked
    ``reproduction.status=incomplete`` is published instead of an oversized
    case.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = Path(metal_dir).expanduser().resolve()
    destination = Path(case_dir).expanduser().resolve()
    shader_source = _diagnosis_shaders(frame_dir, metal_dir, shaders)
    shader_problems = _frame_shader_identity_problems(
        frame_dir, shader_source, 1)
    if shader_problems:
        reason = ("shader_identity_unavailable"
                  if any(problem.get("kind") == "shader_identity"
                         for problem in shader_problems)
                  else "shader_artifact_unavailable")
        return _incomplete_case(
            destination, metal_dir=metal_dir, report=report,
            inputs=[frame_dir, shader_source],
            missing=shader_problems, reason=reason,
            schema=SCHEMA, case_id=case_id, title=title)
    required_bytes = (
        _tree_bytes(frame_dir) + _tree_bytes(shader_source) +
        _replay_artifact_bytes(metal_dir, report) +
        (_difference_bytes(report) if include_difference else 0))
    if max_bytes > 0 and required_bytes > max_bytes:
        return _incomplete_case(
            destination, metal_dir=metal_dir, report=report,
            inputs=[frame_dir, shader_source],
            required_bytes=required_bytes, max_bytes=max_bytes,
            missing=_budget_missing(
                required_bytes, max_bytes,
                detail="the single-frame case exceeds the artifact budget"),
            schema=SCHEMA, case_id=case_id, title=title)
    staged = _StagedCase(destination, [frame_dir, metal_dir, shader_source])
    work = staged.path
    try:
        shutil.copytree(frame_dir, work / "capture")
        _relocate_capture_sequence(work)
        _relocate_capture_seal(work)
        shutil.copytree(shader_source, work / "shaders")
        (work / "replay").mkdir(parents=True)
        for name in REPLAY_ARTIFACTS:
            source = metal_dir / name
            if source.is_file():
                shutil.copy2(source, work / "replay" / name)
        for item in report.get("checkpoints") or []:
            source = Path(str(item.get("metal_file") or item.get("file") or ""))
            if not source.is_absolute():
                source = metal_dir / source
            if source.is_file():
                shutil.copy2(source, work / "replay" / source.name)
        portable = _portable_diagnosis(report, work)
        _write_json(work / "diagnostic-report.json", portable)
        graph = json.loads(json.dumps(
            report.get("graph") or gfx_frame_graph.build(work / "capture")))
        graph["frame_dir"] = "capture"
        _write_json(work / "dependency-graph.json", graph)
        _write_json(work / "graph.json", {
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
                    (work / "difference").mkdir()
                    difference = m6_compare.write_difference(
                        work / "capture", work / "replay", width, height,
                        work / "difference")
                    difference["sha256"] = _sha(work / "difference" / difference["raw_file"])
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
        manifest["files"] = _relative_files(work)
        _write_json(work / "case.json", manifest)
        (work / "README.md").write_text(_readme(manifest), encoding="utf-8")
        total = _case_bytes(work)
        if max_bytes > 0 and total > max_bytes:
            staged.discard()
            return _incomplete_case(
                destination, metal_dir=metal_dir, report=report,
                inputs=[frame_dir, shader_source],
                required_bytes=total, max_bytes=max_bytes,
                missing=_budget_missing(
                    total, max_bytes,
                    detail="the generated single-frame case exceeds the "
                           "artifact budget"),
                schema=SCHEMA, case_id=case_id, title=title)
        staged.commit()
        return manifest
    finally:
        staged.discard()


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CaseError("invalid_json", str(path))
    return value


def _linked_copy(source: Path, destination: Path) -> None:
    """Copy a tree, hard-linking immutable payloads where the filesystem allows.

    A relocated case must not depend on the original campaign directory.  Hard
    links are only an allocation optimisation: if they are unsupported the file
    is copied, and verification always re-hashes the payload.
    """
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        immutable = (
            path.name in ("frame-trace.json", "frame-events.jsonl",
                          "frame-reference.bgra")
            or "blobs" in relative.parts)
        if immutable:
            try:
                os.link(path, target)
                continue
            except OSError:
                pass
        shutil.copy2(path, target)


def _relocate_seal(frame_dir: Path, relative: str) -> None:
    seal_path = frame_dir / "frame-seal.json"
    if not seal_path.is_file():
        return
    seal = _json(seal_path)
    original = seal.get("capture_dir")
    seal["capture_dir"] = relative
    if original and original != relative:
        # Provenance only: never keep an absolute campaign path in a portable
        # v2 inventory.
        seal["portable_original_capture_dir"] = Path(original).name
    _write_json(seal_path, seal)


def _relocate_sequence(frame_dir: Path, index: int, total: int,
                       previous: Path | None) -> dict:
    """Renumber only the ordered container metadata.

    The authoritative identity of the capture (session, capture id, checksums,
    lifecycle, renderer targets) is never rewritten.  The original sequence
    index and predecessor link are retained under ``portable_original_*`` so
    the case remains bound to its originating ordered sequence.
    """
    path = frame_dir / "frame-sequence.json"
    document = _json(path)
    original_index = int(document.get("sequence_index") or 0)
    original_previous = document.get("previous")
    document["portable_original_sequence_index"] = original_index
    document["portable_original_campaign_frames"] = document.get(
        "campaign_frames")
    if isinstance(original_previous, dict):
        # The original directory is provenance, not evidence.  Keeping only
        # its basename avoids embedding an environment-specific absolute path
        # in the case inventory (which would make the case digest differ
        # between otherwise identical runs).
        retained = dict(original_previous)
        if isinstance(retained.get("dir"), str) and retained["dir"]:
            retained["dir"] = Path(retained["dir"]).name
        document["portable_original_previous"] = retained
    document["sequence_index"] = index
    document["campaign_frames"] = total
    if index == 1 or previous is None:
        document["previous"] = None
    else:
        previous_sequence = _json(previous / "frame-sequence.json")
        document["previous"] = {
            "capture_frame_id": previous_sequence.get("capture_frame_id"),
            "dir": f"../{index - 1:06d}",
            "manifest_sha256": _sha(previous / "frame-trace.json"),
            "event_stream_sha256": _sha(previous / "frame-events.jsonl"),
        }
    _write_json(path, document)
    return document


def _sequence_lifecycle_events(sequence: dict) -> dict[str, list[tuple[str, int]]]:
    events: dict[str, list[tuple[str, int]]] = {}
    lifecycle = sequence.get("lifecycle")
    if not isinstance(lifecycle, list):
        return events
    for item in lifecycle:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        try:
            event = int(item.get("event"))
        except (TypeError, ValueError):
            continue
        for name in item.get("names") or []:
            if isinstance(name, str) and name:
                events.setdefault(name, []).append((str(kind), event))
    return events


def _required_prefix(frames: list[Path]) -> tuple[list[Path], dict]:
    """Return the smallest contiguous prefix closed over carried resources.

    A predecessor can itself carry a resource produced even earlier.  The
    prefix must therefore be transitively closed, not just closed over the
    target's declarations; otherwise the first relocated frame starts with
    carried output and no producer.
    """
    sequences = [_json(frame / "frame-sequence.json") for frame in frames]
    target_index = len(frames) - 1
    target_carried = set(sequences[-1].get("carried_renderer_outputs") or [])
    if not target_carried:
        return [frames[-1]], {
            "minimal_prefix": True,
            "excluded_frames": len(frames) - 1,
            "carried_resources": [],
        }
    for frame in frames:
        trace = m6_frame.load_trace(frame, verify_blobs=True)
        history = gfx_frame_graph.load_frame_history(frame, trace)
        if history.get("status") != "verified":
            reasons = history.get("reasons") or ["invalid"]
            if any("carried resource" in reason and "identity" in reason
                   for reason in reasons):
                raise CaseError(
                    "missing_predecessor",
                    f"{frame}: " + "; ".join(reasons))
            raise CaseError(
                "sequence_history", f"{frame}: " + "; ".join(reasons))

    producers: dict[str, list[int]] = {}
    carried_frames: dict[str, list[int]] = {}
    for index, sequence in enumerate(sequences):
        for name in sequence.get("render_targets") or []:
            if isinstance(name, str):
                producers.setdefault(name, []).append(index)
        for name in sequence.get("carried_renderer_outputs") or []:
            if isinstance(name, str):
                carried_frames.setdefault(name, []).append(index)

    start = target_index
    while True:
        moved = False
        for index in range(start, target_index + 1):
            for name in sequences[index].get("carried_renderer_outputs") or []:
                candidates = [item for item in producers.get(name, [])
                              if item < index]
                if not candidates:
                    raise CaseError(
                        "missing_predecessor",
                        f"carried resource {name} has no producer before "
                        f"frame {index + 1}")
                producer = max(candidates)
                if producer < start:
                    start = producer
                    moved = True
        if not moved:
            break

    if sequences[start].get("carried_renderer_outputs"):
        raise CaseError(
            "missing_predecessor",
            "the first selected frame still carries renderer output without "
            "an included producer")

    carried_union: set[str] = set()
    for index in range(start, target_index + 1):
        carried_union.update(sequences[index].get("carried_renderer_outputs")
                             or [])
    for name in sorted(carried_union):
        indices = [item for item in carried_frames.get(name, [])
                   if item >= start]
        if not indices:
            continue
        last_carry = max(indices)
        candidates = [item for item in producers.get(name, [])
                      if item <= last_carry]
        if not candidates:
            raise CaseError(
                "missing_predecessor",
                f"carried resource {name} has no included producer")
        last_producer = max(candidates)
        for index in range(last_producer, last_carry + 1):
            events = _sequence_lifecycle_events(sequences[index])
            for kind, event in events.get(name, []):
                if kind not in ("delete", "mutation"):
                    continue
                same_frame_write = name in (
                    sequences[index].get("render_targets") or [])
                later_write = any(
                    name in (sequences[item].get("render_targets") or [])
                    for item in range(index + 1, last_carry + 1))
                if kind == "mutation":
                    # Metal fails closed on a renderer-owned CPU mutation at
                    # its event; only a producer in a later frame can make the
                    # carried resource reproducible again.
                    if later_write:
                        continue
                elif same_frame_write or later_write:
                    continue
                # A delete in an *intermediate* frame that last carries the
                # resource is normal end-of-life cleanup: later frames no
                # longer need it.  A delete in the target frame cannot be
                # packaged as complete: replay removes the resource from the
                # final Metal history, so the target's own carried-sample
                # provenance and the verifier's carried-resource check can no
                # longer reconcile even when the pixels match.
                if kind == "delete" and index == last_carry:
                    if index != target_index:
                        continue
                    raise CaseError(
                        "missing_mutation_record",
                        f"{name} is deleted by the target frame at event "
                        f"{event} after being carried; a target-frame cleanup "
                        "delete leaves no retained Metal history for the "
                        "carried-resource provenance check")
                raise CaseError(
                    "missing_mutation_record",
                    f"{name} {kind} at frame {index + 1} event {event} has no "
                    "later Metal producer before it is carried")
    selected = frames[start:]
    return selected, {
        "minimal_prefix": len(selected) < len(frames),
        "excluded_frames": len(frames) - len(selected),
        "carried_resources": sorted(carried_union),
    }

def _receipt_shader_identity(shader_dir: Path | None) -> dict:
    """Canonical shader-report and shader-artifact identity for a receipt."""
    if shader_dir is None or not Path(shader_dir).is_dir():
        return {"shaders_json_sha256": "", "artifacts_sha256": "",
                "artifact_count": 0}
    directory = Path(shader_dir)
    report = directory / "shaders.json"
    if not report.is_file():
        return {"shaders_json_sha256": "", "artifacts_sha256": "",
                "artifact_count": 0}
    artifacts_sha256, artifact_count = m6_frame.artifact_digest(directory)
    return {
        "shaders_json_sha256": _sha(report),
        "artifacts_sha256": artifacts_sha256,
        "artifact_count": artifact_count,
    }


def _receipt_identity_problems(
    receipt: dict,
    *,
    sequence: dict,
    frame: Path,
    shader_dir: Path | None,
    request: Path,
) -> list[tuple[str, str]]:
    """Check every authoritative live-receipt identity field available.

    The receipt produced by the live coordinator binds more than the checkpoint
    request: frame/session identity, the captured trace and event-stream
    digests, the request file/count and the canonical shader-report and
    shader-artifact digests.  Preservation and portable verification must agree
    on all of them, otherwise the receipt describes different evidence.
    """
    problems: list[tuple[str, str]] = []

    def check(field: str, expected) -> None:
        actual = receipt.get(field)
        if actual is None:
            problems.append((field, f"receipt is missing {field}"))
        elif actual != expected:
            problems.append(
                (field, f"receipt {field} is {actual!r}, expected {expected!r}"))

    trace_path = frame / "frame-trace.json"
    events_path = frame / "frame-events.jsonl"
    check("capture_frame_id", sequence.get("capture_frame_id"))
    check("pid", sequence.get("pid"))
    check("session_id", sequence.get("session_id"))
    check("trace_manifest_sha256",
          _sha(trace_path) if trace_path.is_file() else None)
    check("event_stream_sha256",
          _sha(events_path) if events_path.is_file() else None)
    if events_path.is_file():
        check("event_stream_bytes", events_path.stat().st_size)
    else:
        check("event_stream_bytes", None)
    check("checkpoint_request_file", request.name)
    check("checkpoint_request_sha256", _sha(request) if request.is_file() else None)
    try:
        expected_count = len(_json(request).get("checkpoints") or [])
    except (OSError, UnicodeError, json.JSONDecodeError, CaseError):
        expected_count = None
    check("checkpoint_count", expected_count)
    # A preserved receipt records the source campaign index; a derived receipt
    # records the portable index.  Prefix selection renumbers the container, so
    # both are authoritative for their respective origin and must agree with
    # one of the two recorded indices.
    allowed_sequences = {
        value for value in (
            sequence.get("sequence_index"),
            sequence.get("portable_original_sequence_index"))
        if isinstance(value, int) and not isinstance(value, bool)
    }
    receipt_sequence = receipt.get("sequence")
    if (receipt_sequence is not None
            and receipt_sequence not in allowed_sequences):
        problems.append((
            "sequence",
            f"receipt sequence {receipt_sequence!r} is not one of "
            f"{sorted(allowed_sequences)}"))
    for field, value in _receipt_shader_identity(shader_dir).items():
        check(field, value)
    return problems


def _checkpoint_setup(frames: list[Path], case_dir: Path,
                      sources: list[Path | str] | None = None) -> list[dict]:
    """Publish one bound checkpoint request/receipt per included frame.

    When the live coordinator already published a frame's request and receipt,
    copy that exact pair instead of synthesising a replacement.  The copied
    receipt is used only when it binds the request digest, capture identity and
    session of the frame being packaged; otherwise the deterministic request is
    regenerated and a fresh receipt is written.
    """
    base = case_dir / "checkpoints"
    base.mkdir(parents=True, exist_ok=True)
    records = []
    for index, frame in enumerate(frames, start=1):
        graph = gfx_frame_graph.build(frame)
        if graph.get("status") == "invalid":
            raise CaseError(
                "checkpoint_graph",
                f"frame {index}: " + "; ".join(
                    graph.get("reasons") or ["invalid"]))
        directory = base / f"frame-{index:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        request_path = directory / "checkpoint-request.json"
        receipt_path = directory / "shader-receipt.json"
        sequence = _json(frame / "frame-sequence.json")
        source_value = (sources[index - 1]
                        if sources and index - 1 < len(sources) else None)
        source = (Path(source_value).expanduser().resolve()
                  if source_value is not None else None)
        preservation = ("source_not_provided" if source is None
                        else "source_incomplete")
        preservation_detail = None
        if source is not None:
            source_request = source / "checkpoint-request.json"
            source_receipt = source / "shader-receipt.json"
            if source_request.is_file() and source_receipt.is_file():
                # Only preserve a request that is identical to the
                # deterministic request for this graph; a stale or substituted
                # checkpoint artifact must never be copied into the case.
                expected_path = directory / ".expected-checkpoint-request.json"
                gfx_frame_graph.write_checkpoint_request(graph, expected_path)
                expected_sha = _sha(expected_path)
                expected_path.unlink()
                if _sha(source_request) != expected_sha:
                    preservation = "source_request_differs"
                    preservation_detail = (
                        "the source checkpoint request differs from the "
                        "deterministic request for this captured graph")
                else:
                    preservation = "source_receipt_unreadable"
                    try:
                        receipt = _json(source_receipt)
                    except (OSError, UnicodeError, json.JSONDecodeError,
                            CaseError) as exc:
                        receipt = {}
                        preservation_detail = str(exc)
                    identity_kind = None
                    identity_detail = None
                    if receipt:
                        for field, detail in _receipt_identity_problems(
                                receipt, sequence=sequence, frame=frame,
                                shader_dir=frame / "shaders",
                                request=source_request):
                            identity_detail = detail
                            if field == "checkpoint_request_sha256":
                                identity_kind = (
                                    "source_receipt_request_mismatch")
                            elif field == "capture_frame_id":
                                identity_kind = (
                                    "source_receipt_capture_mismatch")
                            elif field == "session_id":
                                identity_kind = (
                                    "source_receipt_session_mismatch")
                            else:
                                identity_kind = (
                                    "source_receipt_identity_mismatch")
                            break
                    if identity_kind is not None:
                        preservation = identity_kind
                        preservation_detail = identity_detail
                    elif receipt:
                        shutil.copy2(source_request, request_path)
                        shutil.copy2(source_receipt, receipt_path)
                        preservation = "preserved"
                        records.append({
                            "index": index,
                            "directory": directory.relative_to(
                                case_dir).as_posix(),
                            "request_sha256": _sha(request_path),
                            "checkpoint_count": len(
                                _json(request_path).get("checkpoints") or []),
                            "graph_status": graph.get("status"),
                            "preserved": True,
                            "preservation": preservation,
                            "preservation_detail": None,
                            "receipt_origin": "preserved_live_receipt",
                        })
                        continue
        request = gfx_frame_graph.write_checkpoint_request(graph, request_path)
        request_sha = _sha(request_path)
        seal = None
        if (frame / "frame-seal.json").is_file():
            try:
                seal = m6_frame.load_seal(frame)
            except m6_frame.FrameError as exc:
                raise CaseError("invalid_seal", f"{frame}: {exc}") from exc
        identity = {
            "capture_frame_id": sequence.get("capture_frame_id"),
            "pid": sequence.get("pid"),
            "session_id": sequence.get("session_id"),
            "trace_manifest_sha256": sequence.get("manifest_sha256"),
            "event_stream_sha256": sequence.get("event_stream_sha256"),
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
        }
        if seal is not None:
            identity.update({
                "capture_frame_id": seal.get("capture_frame_id"),
                "pid": seal.get("pid"),
                "session_id": seal.get("session_id"),
                "trace_manifest_sha256": seal.get("manifest_sha256"),
                "event_stream_sha256": seal.get("event_stream_sha256"),
                "event_stream_bytes": seal.get("event_stream_bytes"),
            })
        receipt = {
            "schema": "m7-shadow-shader-receipt/v1",
            **identity,
            **_receipt_shader_identity(frame / "shaders"),
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": request_sha,
            "checkpoint_count": len(request.get("checkpoints") or []),
            "sequence": index,
            "graph_status": graph.get("status"),
        }
        _write_json(receipt_path, receipt)
        records.append({
            "index": index,
            "directory": directory.relative_to(case_dir).as_posix(),
            "request_sha256": request_sha,
            "checkpoint_count": len(request.get("checkpoints") or []),
            "graph_status": graph.get("status"),
            "preserved": False,
            "preservation": preservation,
            "preservation_detail": preservation_detail,
            "receipt_origin": "derived_receipt",
        })
    return records


def _scrub_paths(value, replacements: dict[str, str]):
    """Replace known absolute roots inside free-form report strings."""
    if isinstance(value, dict):
        return {key: _scrub_paths(item, replacements)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_paths(item, replacements) for item in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            if old and old in value:
                value = value.replace(old, new)
    return value


def package_sequence_diagnosis(
    frames: list[Path],
    metal_dir: Path | str,
    case_dir: Path | str,
    report: dict,
    *,
    case_id: str = "diagnosis-case",
    title: str = "M7 cross-frame differential diagnosis",
    shaders: Path | str | None = None,
    frame_shaders: dict | None = None,
    checkpoint_sources: list[Path | str] | dict | None = None,
    max_bytes: int = 512 * 1024 * 1024,
) -> dict:
    """Package the minimal ordered frame prefix needed to reproduce a fault.

    The final frame remains the diagnosed target.  Predecessors are included
    only when the target samples a renderer-owned resource it did not produce,
    and they are renumbered as a relocatable ordered sequence while retaining
    every authoritative capture identity and digest.

    ``max_bytes`` bounds the complete generated package: captured frames,
    shaders, replay/checkpoint artifacts and manifests.  The sequence path does
    not generate a difference image; the single-frame path budgets one.
    The package is staged and measured before it is published, so the final
    directory respects the budget.  When the required history does not fit, a
    bounded summary marked ``reproduction.status=incomplete`` is published
    (never a valid reproduction command); if even that summary cannot fit, an
    explicit ``case_budget`` error is raised.
    """
    frames = [Path(frame).expanduser().resolve() for frame in frames]
    metal_dir = Path(metal_dir).expanduser().resolve()
    destination = Path(case_dir).expanduser().resolve()
    if not frames:
        raise CaseError("empty_sequence")
    if frame_shaders:
        # Resolve mapping keys once so /var and /private/var spellings of the
        # same live frame directory cannot silently miss their translation.
        normalized_shaders = {}
        for key, value in frame_shaders.items():
            try:
                normalized_shaders[str(Path(key).expanduser().resolve())] = value
            except (OSError, TypeError, ValueError):
                normalized_shaders[str(key)] = value
        frame_shaders = normalized_shaders
    if len(frames) == 1:
        shader_dir = _single_frame_shader_dir(
            frames[0], metal_dir, shaders, frame_shaders)
        manifest = package_diagnosis(
            frames[0], metal_dir, destination, report, case_id=case_id,
            title=title, shaders=shader_dir, max_bytes=max_bytes)
        manifest["shader_source"] = _frame_shader_selection(
            frames[0], shader_dir, shaders, frame_shaders)
        _write_json(destination / "case.json", manifest)
        return manifest
    selected, selection = _required_prefix(frames)
    full_source_bytes = _normalized_frame_bytes(frames)
    prefix_source_bytes = _normalized_frame_bytes(selected)
    if len(selected) == 1:
        shader_dir = _single_frame_shader_dir(
            selected[0], metal_dir, shaders, frame_shaders)
        manifest = package_diagnosis(
            selected[0], metal_dir, destination, report, case_id=case_id,
            title=title, shaders=shader_dir, max_bytes=max_bytes)
        manifest["shader_source"] = _frame_shader_selection(
            selected[0], shader_dir, shaders, frame_shaders)
        _write_json(destination / "case.json", manifest)
        return manifest
    shader_dirs: list[Path] = []
    shader_selections: list[str] = []
    missing_shaders: list[dict] = []
    for index, frame in enumerate(selected, start=1):
        directory = _frame_shader_dir(frame, metal_dir, shaders,
                                      frame_shaders)
        shader_selections.append(
            _frame_shader_selection(frame, directory, shaders, frame_shaders))
        if directory is None:
            missing_shaders.append({
                "kind": "shader_artifact",
                "frame": index,
                "frame_dir": f"frames/{index:06d}",
                "detail": "no translated shader directory for this frame "
                          "(frame/shaders, sibling translated/ or the Metal "
                          "result directory)",
            })
            continue
        shader_dirs.append(directory)
    if missing_shaders:
        return _incomplete_case(
            destination, metal_dir=metal_dir, report=report,
            inputs=[*selected, *shader_dirs],
            missing=missing_shaders,
            reason="shader_artifact_unavailable",
            schema=SEQUENCE_SCHEMA, case_id=case_id, title=title)
    shader_problems: list[dict] = []
    for index, (frame, directory) in enumerate(
            zip(selected, shader_dirs), start=1):
        shader_problems.extend(
            _frame_shader_identity_problems(frame, directory, index))
    if shader_problems:
        reason = ("shader_identity_unavailable"
                  if any(problem.get("kind") == "shader_identity"
                         for problem in shader_problems)
                  else "shader_artifact_unavailable")
        return _incomplete_case(
            destination, metal_dir=metal_dir, report=report,
            inputs=[*selected, *shader_dirs],
            missing=shader_problems,
            reason=reason,
            schema=SEQUENCE_SCHEMA, case_id=case_id, title=title)
    shader_source = shader_dirs[-1]
    unique_shader_dirs = list(dict.fromkeys(shader_dirs))
    required_bytes = prefix_source_bytes
    required_bytes += sum(_tree_bytes(directory)
                          for directory in unique_shader_dirs)
    required_bytes += _replay_artifact_bytes(metal_dir, report)
    if max_bytes > 0 and required_bytes > max_bytes:
        return _incomplete_case(
            destination, metal_dir=metal_dir, report=report,
            inputs=[*selected, *unique_shader_dirs],
            required_bytes=required_bytes, max_bytes=max_bytes,
            missing=_budget_missing(
                required_bytes, max_bytes,
                required_frames=len(selected),
                detail="the minimal cross-frame prefix exceeds the case "
                       "budget"),
            schema=SEQUENCE_SCHEMA, case_id=case_id, title=title)
    staged = _StagedCase(
        destination, [*selected, metal_dir, *unique_shader_dirs])
    work = staged.path
    try:
        # Portable seal validation needs the owning case identity; the complete
        # manifest is written after checkpoint receipts are ready.
        _write_json(work / "case.json", {
            "schema": SEQUENCE_SCHEMA, "case_id": case_id, "title": title,
        })
        copied: list[Path] = []
        previous_copy = None
        for index, source in enumerate(selected, start=1):
            frame_copy = work / "frames" / f"{index:06d}"
            _linked_copy(source, frame_copy)
            sequence = _relocate_sequence(frame_copy, index, len(selected),
                                          previous_copy)
            _relocate_seal(frame_copy, f"frames/{index:06d}")
            shutil.rmtree(frame_copy / "shaders", ignore_errors=True)
            _linked_copy(shader_dirs[index - 1], frame_copy / "shaders")
            sequence["shader_dir"] = f"frames/{index:06d}/shaders"
            sequence["shader_source"] = shader_selections[index - 1]
            sequence["shader_report_sha256"] = _sha(
                frame_copy / "shaders" / "shaders.json")
            sequence["shader_programs"] = _frame_programs(source)
            _write_json(frame_copy / "frame-sequence.json", sequence)
            if sequence.get("session_id") != _json(
                    selected[-1] / "frame-sequence.json").get("session_id"):
                raise CaseError("session_identity", str(source))
            copied.append(frame_copy)
            previous_copy = frame_copy
        _linked_copy(shader_source, work / "shaders")
        (work / "replay").mkdir(parents=True)
        replay_replacements = {str(metal_dir): "replay",
                               str(work): "."}
        for index, source in enumerate(selected, start=1):
            replay_replacements[str(source)] = f"frames/{index:06d}"
            replay_replacements[str(shader_dirs[index - 1])] = (
                f"frames/{index:06d}/shaders")
        for parent in {str(Path(frame).parent) for frame in frames}:
            replay_replacements[parent] = "campaign"
        for name in REPLAY_ARTIFACTS:
            source = metal_dir / name
            if not source.is_file():
                continue
            artifact_path = work / "replay" / name
            if name.endswith(".jsonl"):
                lines = []
                for line in source.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        lines.append(line)
                        continue
                    lines.append(json.dumps(
                        _scrub_paths(value, replay_replacements)))
                artifact_path.write_text("\n".join(lines) + "\n",
                                          encoding="utf-8")
            elif name.endswith(".json"):
                try:
                    value = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    shutil.copy2(source, artifact_path)
                    continue
                _write_json(artifact_path,
                            _scrub_paths(value, replay_replacements))
            else:
                shutil.copy2(source, artifact_path)
        for item in report.get("checkpoints") or []:
            source = Path(str(item.get("metal_file") or item.get("file") or ""))
            if not source.is_absolute():
                source = metal_dir / source
            if source.is_file():
                shutil.copy2(source, work / "replay" / source.name)
        target_relative = f"frames/{len(copied):06d}"
        replacements = {str(metal_dir): "replay", str(work): "."}
        for index, source in enumerate(selected, start=1):
            replacements[str(source)] = f"frames/{index:06d}"
        portable = _scrub_paths(
            _portable_diagnosis(report, work, target_relative), replacements)
        _write_json(work / "diagnostic-report.json", portable)
        graph = json.loads(json.dumps(
            report.get("graph") or gfx_frame_graph.build(copied[-1])))
        graph["frame_dir"] = target_relative
        # Retained graph entries carry the original campaign paths.  Rewrite
        # them to the relocated container before publishing, so the case
        # digest does not depend on the capture machine's directory names.
        replacements = {str(metal_dir): "replay", str(work): "."}
        for index, source in enumerate(selected, start=1):
            replacements[str(source)] = f"frames/{index:06d}"
        graph = _scrub_paths(graph, replacements)
        _portable_paths(graph, replay_dir="replay",
                        capture_dir=target_relative)
        graph = _scrub_paths(graph, replacements)
        _write_json(work / "dependency-graph.json", graph)
        _write_json(work / "graph.json", {
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
        selected_sources = None
        if checkpoint_sources is not None:
            if isinstance(checkpoint_sources, dict):
                selected_sources = [
                    checkpoint_sources.get(str(frame))
                    or checkpoint_sources.get(frame)
                    for frame in selected]
            else:
                # A list is aligned with the caller's full ordered frames;
                # remap it after prefix selection so a suffix cannot bind a
                # predecessor's checkpoint receipt to the wrong frame.
                by_frame = {
                    str(frame.resolve()): Path(source)
                    for frame, source in zip(frames, checkpoint_sources)}
                selected_sources = [
                    by_frame.get(str(frame.resolve())) for frame in selected]
            selected_sources = [
                (Path(item) if item is not None else None)
                for item in selected_sources]
        checkpoints = _checkpoint_setup(copied, work, selected_sources)
        entries = []
        for index, (source, frame_copy) in enumerate(
                zip(selected, copied), start=1):
            original = _json(source / "frame-sequence.json")
            sequence = _json(frame_copy / "frame-sequence.json")
            checkpoint = checkpoints[index - 1]
            entries.append({
                "index": index,
                "dir": f"frames/{index:06d}",
                "capture_frame_id": sequence.get("capture_frame_id"),
                "present_index": sequence.get("present_index"),
                "scene_index": sequence.get("scene_index"),
                "original_sequence_index": int(
                    original.get("sequence_index") or 0),
                "session_id": sequence.get("session_id"),
                "manifest_sha256": _sha(frame_copy / "frame-trace.json"),
                "event_stream_sha256": _sha(
                    frame_copy / "frame-events.jsonl"),
                "reference_sha256": _sha(
                    frame_copy / "frame-reference.bgra"),
                "checkpoint_request_sha256": checkpoint["request_sha256"],
                "checkpoint_count": checkpoint["checkpoint_count"],
                "graph_status": checkpoint["graph_status"],
                "shader_dir": sequence.get("shader_dir"),
                "shader_source": sequence.get("shader_source"),
                "shader_report_sha256": sequence.get(
                    "shader_report_sha256"),
                "shader_programs": sequence.get("shader_programs") or [],
                "checkpoint_preserved": checkpoint.get("preserved"),
                "checkpoint_preservation": checkpoint.get("preservation"),
                "checkpoint_preservation_detail": checkpoint.get(
                    "preservation_detail"),
                "receipt_origin": checkpoint.get("receipt_origin"),
            })
        present_indices = [entry.get("present_index") for entry in entries]
        identity_complete = all(isinstance(item, int) and item > 0
                                for item in present_indices)
        identity_gap_free = bool(
            identity_complete
            and all(present_indices[index] == present_indices[index - 1] + 1
                    for index in range(1, len(present_indices))))
        sequence_manifest = {
            "schema": SEQUENCE_MANIFEST_SCHEMA,
            "session_id": entries[-1]["session_id"],
            "engine_identity": {
                "status": ("gap_free" if identity_gap_free
                           else "gap" if identity_complete
                           else "unavailable"),
                "gap_free": identity_gap_free,
                "present_indices": present_indices,
                "scene_indices": [entry.get("scene_index")
                                  for entry in entries],
            },
            "pid": _json(copied[-1] / "frame-sequence.json").get("pid"),
            "target_index": len(entries),
            "frames": entries,
            "minimal_prefix": selection["minimal_prefix"],
            "excluded_frames": selection["excluded_frames"],
            "carried_resources": selection["carried_resources"],
            "source_prefix_bytes": prefix_source_bytes,
            "full_source_bytes": full_source_bytes,
            "excluded_source_bytes": full_source_bytes - prefix_source_bytes,
            "reproduction": {
                "status": "complete",
                "independently_reproducible": True,
                "missing": [],
            },
        }
        _write_json(work / "sequence.json", sequence_manifest)
        manifest = {
            "schema": SEQUENCE_SCHEMA,
            "case_id": case_id,
            "title": title,
            "sequence": "sequence.json",
            "shaders": "shaders",
            "replay": "replay",
            "diagnostic_report": "diagnostic-report.json",
            "dependency_graph": "dependency-graph.json",
            "graph": "graph.json",
            "target_frame": target_relative,
            "reproduction": sequence_manifest["reproduction"],
            "classification": report.get("classification"),
            "first_divergence": report.get("first_divergence"),
            "qualification": report.get("qualification"),
            "replay_command": [
                "python3.14", "-m", "automation.gfx_replay_case", "replay",
                "--case", ".", "--out", "replay-rerun",
            ],
            "verify_command": [
                "python3.14", "-m", "automation.gfx_replay_case", "verify",
                "--case", ".",
            ],
            "files": [],
        }
        (work / "README.md").write_text(_readme(manifest), encoding="utf-8")
        manifest["files"] = [
            item for item in _relative_files(work)
            if item["path"] != "case.json"]
        _write_json(work / "case.json", manifest)
        total = _case_bytes(work)
        if max_bytes > 0 and total > max_bytes:
            staged.discard()
            return _incomplete_case(
                destination, metal_dir=metal_dir, report=report,
                inputs=[*selected, *unique_shader_dirs],
                required_bytes=total, max_bytes=max_bytes,
                missing=_budget_missing(
                    total, max_bytes,
                    required_frames=len(selected),
                    detail="the generated cross-frame prefix exceeds the "
                           "artifact budget"),
                schema=SEQUENCE_SCHEMA, case_id=case_id, title=title)
        staged.commit()
        return manifest
    finally:
        staged.discard()


def _verify_sequence(case_dir: Path, manifest: dict) -> tuple[list[str], dict]:
    problems: list[str] = []
    declared = str(manifest.get("sequence") or "sequence.json")
    sequence_path, path_problem = _case_relative_path(case_dir, declared)
    if path_problem is not None:
        return [f"sequence manifest path is invalid: {path_problem}"], {}
    if declared != "sequence.json":
        # replay_case always reads sequence.json; verifying any other path
        # would validate evidence that is never executed.
        return [
            f"sequence manifest path must be sequence.json, not {declared!r}"
        ], {}
    try:
        sequence = _json(sequence_path or case_dir / "sequence.json")
    except (OSError, UnicodeError, json.JSONDecodeError, CaseError) as exc:
        return [f"sequence manifest is unreadable: {exc}"], {}
    if sequence.get("schema") != SEQUENCE_MANIFEST_SCHEMA:
        problems.append("sequence manifest schema is invalid")
    session = sequence.get("session_id")
    entries = sequence.get("frames")
    if not isinstance(entries, list) or not entries:
        return problems + ["sequence has no frames"], {}
    if int(sequence.get("target_index") or 0) != len(entries):
        problems.append("target frame is not the last sequence entry")
    previous_sequence = None
    previous_dir = None
    produced: set[str] = set()
    deleted_after_producer: set[str] = set()
    mutated_after_producer: set[str] = set()
    carried_by_target: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            problems.append(f"sequence entry {index} is invalid")
            continue
        if entry.get("index") != index:
            problems.append(f"sequence entry {index} has the wrong index")
        relative = str(entry.get("dir") or "")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            problems.append(f"sequence entry {index} has a nonportable path")
            continue
        frame = (case_dir / relative).resolve()
        if not frame.is_dir():
            problems.append(f"sequence entry {index} is missing")
            continue
        try:
            sequence_record = _json(frame / "frame-sequence.json")
        except (OSError, UnicodeError, json.JSONDecodeError, CaseError) as exc:
            problems.append(f"frame {index} sequence record is unreadable: {exc}")
            continue
        if "lifecycle" not in sequence_record or not isinstance(
                sequence_record.get("lifecycle"), list):
            problems.append(
                f"frame {index} has no lifecycle mutation record array")
        if sequence_record.get("sequence_index") != index:
            problems.append(f"frame {index} sequence index is reordered")
        if sequence_record.get("campaign_frames") != len(entries):
            problems.append(f"frame {index} campaign length is inconsistent")
        if sequence_record.get("session_id") != session:
            problems.append(f"frame {index} belongs to another session")
        if sequence_record.get("capture_frame_id") != entry.get(
                "capture_frame_id"):
            problems.append(f"frame {index} capture identity is substituted")
        for field in ("present_index", "scene_index"):
            if entry.get(field) != sequence_record.get(field):
                problems.append(
                    f"frame {index} engine {field} is substituted")
        if isinstance(entry.get("present_index"), int):
            if (previous_sequence is not None
                    and isinstance(previous_sequence.get("present_index"), int)
                    and entry["present_index"]
                    != previous_sequence["present_index"] + 1):
                problems.append(
                    f"frame {index} engine present index is not consecutive")
        if entry.get("original_sequence_index") not in (
                sequence_record.get("portable_original_sequence_index"),
                sequence_record.get("sequence_index")):
            problems.append(f"frame {index} original order is not bound")
        for field, name in (("manifest_sha256", "frame-trace.json"),
                            ("event_stream_sha256", "frame-events.jsonl"),
                            ("reference_sha256", "frame-reference.bgra")):
            actual = _sha(frame / name)
            if entry.get(field) != actual or sequence_record.get(field) != actual:
                problems.append(f"frame {index} {name} digest drift")
        previous = sequence_record.get("previous")
        if index == 1:
            if previous is not None:
                problems.append("first sequence frame declares a predecessor")
        else:
            if not isinstance(previous, dict):
                problems.append(f"frame {index} has no predecessor identity")
            else:
                if previous.get("dir") != f"../{index - 1:06d}":
                    problems.append(f"frame {index} predecessor is reordered")
                expected = previous_sequence or {}
                for field in ("capture_frame_id", "manifest_sha256",
                              "event_stream_sha256"):
                    if previous.get(field) != expected.get(field):
                        problems.append(
                            f"frame {index} predecessor {field} is reordered")
        try:
            trace = m6_frame.load_trace(frame, verify_blobs=True)
            history = gfx_frame_graph.load_frame_history(frame, trace)
        except Exception as exc:  # noqa: BLE001 - explicit verification result
            problems.append(f"frame {index} history is unreadable: {exc}")
            history = {}
            trace = {}
        shader_relative = entry.get("shader_dir")
        receipt_shader_dir: Path | None = None
        if not isinstance(shader_relative, str) or not shader_relative:
            problems.append(f"frame {index} has no shader directory binding")
        else:
            shader_path, shader_problem = _case_relative_path(
                case_dir, shader_relative)
            if shader_problem is not None:
                problems.append(
                    f"frame {index} shader directory is invalid: "
                    f"{shader_problem}")
            elif not (shader_path / "shaders.json").is_file():
                problems.append(
                    f"frame {index} shader report is missing")
            else:
                receipt_shader_dir = shader_path
                try:
                    shader_report = _json(shader_path / "shaders.json")
                except (OSError, UnicodeError, json.JSONDecodeError,
                        CaseError) as exc:
                    problems.append(
                        f"frame {index} shader report is unreadable: {exc}")
                    shader_report = {}
                declared_sha = entry.get("shader_report_sha256")
                actual_sha = _sha(shader_path / "shaders.json")
                if declared_sha != actual_sha:
                    problems.append(
                        f"frame {index} shader report digest drift")
                for problem in _frame_shader_identity_problems(
                        frame, shader_path, index):
                    problems.append(
                        f"frame {index} {problem['detail']}")
        declared_programs = entry.get("shader_programs")
        actual_programs = _frame_programs(frame)
        if (declared_programs is not None
                and list(declared_programs) != actual_programs):
            problems.append(
                f"frame {index} declared shader programs do not match its "
                f"draw events: {declared_programs} != {actual_programs}")
        if history.get("status") != "verified":
            problems.append(
                f"frame {index} history is invalid: " +
                "; ".join(history.get("reasons") or ["unknown"]))
        for name in (history.get("delete_events") or {}):
            if name in produced:
                deleted_after_producer.add(name)
        for name in (history.get("mutation_events") or {}):
            if name in produced:
                mutated_after_producer.add(name)
        for name in sequence_record.get("render_targets") or []:
            if isinstance(name, str):
                produced.add(name)
                deleted_after_producer.discard(name)
        if index == len(entries):
            carried_by_target = list(
                sequence_record.get("carried_renderer_outputs") or [])
        checkpoint_sha = entry.get("checkpoint_request_sha256")
        if checkpoint_sha:
            request = case_dir / "checkpoints" / f"frame-{index:03d}" / (
                "checkpoint-request.json")
            receipt = request.parent / "shader-receipt.json"
            if not request.is_file():
                problems.append(f"frame {index} checkpoint request is missing")
            elif _sha(request) != checkpoint_sha:
                problems.append(
                    f"frame {index} checkpoint request is stale or substituted")
            if not receipt.is_file():
                problems.append(f"frame {index} checkpoint receipt is missing")
            else:
                try:
                    receipt_value = _json(receipt)
                except (OSError, UnicodeError, json.JSONDecodeError,
                        CaseError):
                    receipt_value = {}
                if request.is_file():
                    for field, detail in _receipt_identity_problems(
                            receipt_value, sequence=sequence_record,
                            frame=frame, shader_dir=receipt_shader_dir,
                            request=request):
                        problems.append(
                            f"frame {index} checkpoint receipt identity "
                            f"disagrees: {detail}")
                if entry.get("checkpoint_count") is not None:
                    try:
                        expected_count = len(
                            _json(request).get("checkpoints") or [])
                    except (OSError, UnicodeError, json.JSONDecodeError,
                            CaseError):
                        expected_count = None
                    if (expected_count is not None and
                            entry.get("checkpoint_count") != expected_count):
                        problems.append(
                            f"frame {index} checkpoint request count drifted "
                            f"from the declared sequence")
                origin = entry.get("receipt_origin")
                if origin not in ("preserved_live_receipt", "derived_receipt"):
                    problems.append(
                        f"frame {index} checkpoint receipt origin is "
                        "missing or invalid")
                expected_origin = ("preserved_live_receipt"
                                   if entry.get("checkpoint_preserved")
                                   else "derived_receipt")
                if (origin in ("preserved_live_receipt", "derived_receipt")
                        and origin != expected_origin):
                    problems.append(
                        f"frame {index} checkpoint receipt origin disagrees "
                        "with its preservation record")
        previous_sequence = sequence_record
        previous_dir = frame
    for name in carried_by_target:
        if name not in produced:
            problems.append(
                f"carried resource {name} has no included predecessor")
        if name in deleted_after_producer:
            problems.append(
                f"carried resource {name} is deleted without a later Metal "
                "producer")
        if name in mutated_after_producer:
            problems.append(
                f"carried resource {name} is mutated without a later Metal "
                "producer")
    if previous_dir is None:
        problems.append("target frame is missing")
    identity = sequence.get("engine_identity")
    if isinstance(identity, dict) and identity.get("status") == "gap":
        problems.append("engine frame sequence is not gap-free")
    summary = {
        "schema": SEQUENCE_MANIFEST_SCHEMA,
        "engine_identity": identity or {
            "status": "unavailable", "gap_free": False},
        "status": "verified" if not problems else "invalid",
        "frames": len(entries),
        "target_index": sequence.get("target_index"),
        "minimal_prefix": sequence.get("minimal_prefix"),
        "excluded_frames": sequence.get("excluded_frames"),
        "session_id": session,
        "carried_resources": carried_by_target,
    }
    return problems, summary


_REPLAY_TERMINAL_CLASSIFICATIONS = {
    "gpu_execution_failure", "invalid_provenance", "incomplete_capture",
    "incomplete_diagnostics", "fault_experiment",
}
_REPLAY_INFRASTRUCTURE_REASONS = {
    "host_failure", "frame_timeout", "overall_timeout", "tool_failure",
    "sequence_order", "frame_identity", "cancelled", "time_budget",
}


def _sequence_replay_problems(report: dict, expected_frames: int,
                              target_index: int) -> list[str]:
    """Explain why a sequence replay did not execute its declared target.

    A portable reproduction is only successful when the campaign completed,
    every declared frame was executed once in order, each frame was diagnosed,
    the last executed frame is the declared target, and no terminal execution,
    history, provenance, identity or infrastructure failure occurred.  Pixel
    equivalence is deliberately *not* required: a fully executed, divergent
    campaign is a valid reproduction of a rendering defect.
    """
    problems: list[str] = []
    status = report.get("status")
    stop_reason = report.get("stop_reason")
    if status != "complete":
        problems.append(
            f"campaign status is {status!r}, not 'complete'"
            + (f" (stop_reason={stop_reason!r})" if stop_reason else ""))
    if stop_reason in _REPLAY_INFRASTRUCTURE_REASONS:
        problems.append(f"infrastructure failure: {stop_reason}")
    rows = [row for row in report.get("frames") or [] if isinstance(row, dict)]
    if len(rows) != expected_frames:
        problems.append(
            f"executed {len(rows)} of {expected_frames} declared frames")
    sequences = [row.get("sequence") for row in rows]
    expected_sequences = list(range(1, expected_frames + 1))
    if sequences != expected_sequences:
        problems.append(
            f"executed sequence {sequences} is not {expected_sequences}")
    for row in rows:
        sequence = row.get("sequence")
        if row.get("ok") is False:
            problems.append(
                f"frame {sequence} GPU execution failed: "
                f"{row.get('error') or row.get('stage') or 'unknown'}")
        if row.get("history_status") not in (1, None):
            problems.append(
                f"frame {sequence} resource history failed: "
                f"history_status={row.get('history_status')}")
        classification = row.get("classification")
        if classification in _REPLAY_TERMINAL_CLASSIFICATIONS:
            problems.append(
                f"frame {sequence} terminal classification: {classification}")
        if row.get("diagnostic_completed") is not True:
            problems.append(
                f"frame {sequence} has no completed diagnostic evidence: "
                f"{(row.get('diagnostic_evidence') or {}).get('missing')}")
    last_sequence = sequences[-1] if sequences else None
    if last_sequence != target_index:
        problems.append(
            f"last executed frame {last_sequence} is not the declared target "
            f"{target_index}")
    return problems


def _replay_sequence_case(case_dir: Path, out_dir: Path,
                          *, faults: dict | None = None) -> dict:
    from automation import m7_continuous

    sequence = _json(case_dir / "sequence.json")
    frames = [(case_dir / entry["dir"]).resolve()
              for entry in sequence.get("frames") or []]
    target_index = int(sequence.get("target_index") or len(frames))
    environment = {
        "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": (
            "1" if (faults or {}).get("disable_render_target_vflip") else None),
        "EU4_FRAME_REPLAY_SAMPLER_SHIFT": (faults or {}).get("sampler_shift"),
        "EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN": (
            faults or {}).get("sampler_shift_min"),
    }
    saved: dict[str, str | None] = {}
    for key, value in environment.items():
        if value is None:
            continue
        saved[key] = os.environ.get(key)
        os.environ[key] = str(value)
    try:
        report = m7_continuous.run_campaign(
            frames, out_dir, shaders=case_dir / "shaders",
            stop_on_difference=False, max_frames=len(frames),
            timeout=3600.0, frame_timeout=180.0)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    problems = _sequence_replay_problems(report, len(frames), target_index)
    if problems:
        # Never fall back to the last available frame: a diagnosis produced
        # before the declared target is not a reproduction of this case.
        raise CaseError("sequence_replay", json.dumps({
            "status": report.get("status"),
            "stop_reason": report.get("stop_reason"),
            "stopped_at": report.get("stopped_at"),
            "expected_frames": len(frames),
            "executed_frames": len(report.get("frames") or []),
            "target_index": target_index,
            "last_sequence": (
                (report.get("frames") or [{}])[-1].get("sequence")
                if report.get("frames") else None),
            "reasons": problems,
        }, sort_keys=True))
    last = (report.get("frames") or [])[-1]
    diagnostic_path = Path(str(last.get("diagnostic_report") or ""))
    if not diagnostic_path.is_file():
        raise CaseError("sequence_replay", "no target frame diagnosis")
    diagnosis = _json(diagnostic_path)
    diagnosis = dict(diagnosis)
    diagnosis["campaign"] = {
        "schema": report.get("schema"),
        "status": report.get("status"),
        "stop_reason": report.get("stop_reason"),
        "frames": len(report.get("frames") or []),
        "target_sequence": last.get("sequence"),
    }
    diagnosis["status"] = "ok"
    diagnosis["outcome"] = diagnosis.get("qualification")
    diagnosis["final"] = {
        "status": diagnosis.get("classification"),
        "sequence": last.get("sequence"),
        "first_divergence": diagnosis.get("first_divergence"),
    }
    earliest = None
    for row in report.get("frames") or []:
        statistics = row.get("pixels") or {}
        if int(statistics.get("mismatched_pixels") or 0) > 0:
            divergence = row.get("first_divergence") or {}
            earliest = {
                "frame": row.get("sequence"),
                "classification": row.get("classification"),
                "event": divergence.get("event"),
                "operation": divergence.get("operation"),
            }
            break
    diagnosis["reproduction"] = {
        "earliest_affected_frame": (earliest or {}).get("frame"),
        "earliest_affected_event": (earliest or {}).get("event"),
        "earliest_affected_operation": (earliest or {}).get("operation"),
        "first_divergent_frame": earliest,
        "target_sequence": last.get("sequence"),
    }
    return diagnosis


def _readme(manifest: dict) -> str:
    divergence = manifest.get("first_divergence") or {}
    sequence = manifest.get("sequence")
    lines = [
        f"# Strategy B replay case: {manifest.get('case_id')}",
        "",
        manifest.get("title", ""),
        "",
    ]
    reproduction = manifest.get("reproduction") or {}
    if reproduction.get("status") == "incomplete":
        lines += [
            f"- Reproduction: `incomplete` "
            f"(independently reproducible: "
            f"{reproduction.get('independently_reproducible')})",
            f"- Classification: `{manifest.get('classification')}`",
        ]
    elif sequence:
        lines += [
            f"- Ordered frame prefix: `{sequence}` "
            f"({reproduction.get('status', 'unknown')})",
            f"- Target frame: `{manifest.get('target_frame')}`",
            f"- Graph status: `{manifest.get('graph_status') or (manifest.get('qualification') or {}).get('axes', {}).get('operation_coverage')}`",
            f"- Classification: `{manifest.get('classification')}`",
        ]
    else:
        lines += [
            f"- Capture: `{manifest.get('capture')}`",
            f"- Graph status: `{manifest.get('graph_status') or (manifest.get('qualification') or {}).get('axes', {}).get('operation_coverage')}`",
            f"- Replay status: `{manifest.get('replay_status') or manifest.get('classification')}`",
            f"- Classification: `{manifest.get('classification')}`",
        ]
    lines += [
        f"- First divergence: `{divergence.get('kind')}`"
        + (f" at event {divergence.get('event')}" if divergence.get("event") is not None else ""),
        "",
    ]
    if reproduction.get("status") == "incomplete":
        lines += [
            "## Reproduction incomplete",
            "",
            "This directory preserves the diagnostic evidence, but the required",
            "cross-frame history could not be packaged within the artifact",
            "budget.  It is not independently reproducible.",
            "",
        ]
    else:
        lines += [
            "## Replay",
            "",
            "From this directory:",
            "",
            "```sh",
            " ".join(manifest.get("replay_command") or []),
            "```",
            "",
        ]
    if sequence:
        lines += [
            "The replay uses the existing Metal replay host through one",
            "persistent session for the minimal ordered prefix.  Predecessor",
            "frames are included only to produce renderer-owned resources that",
            "the target samples; captured OpenGL intermediate images are",
            "reference checkpoints only.",
            "",
        ]
    elif not sequence and reproduction.get("status") != "incomplete":
        lines += [
            "The replay uses the existing M6 Metal replay host and the captured",
            "OpenGL frame. Renderer-generated intermediates are produced by Metal;",
            "captured OpenGL intermediate images are reference checkpoints only.",
            "",
        ]
    lines += [
        "## Verify",
        "",
        "```sh",
        " ".join(manifest.get("verify_command") or []),
        "```",
        "",
        "Every file digest in `case.json` is rechecked before the case is used.",
    ]
    return "\n".join(lines) + "\n"


def _case_relative_path(case_dir: Path, relative: str) -> tuple[Path | None, str | None]:
    """Resolve a manifest path and reject absolute, traversal or symlink escapes."""
    raw = Path(relative)
    if not relative or raw.is_absolute() or ".." in raw.parts:
        return None, f"nonportable manifest path {relative!r}"
    root = case_dir.resolve()
    candidate = (case_dir / raw).resolve()
    if candidate != root and root not in candidate.parents:
        return None, f"manifest path escapes the case directory: {relative}"
    return candidate, None


def _declared_inventory(
    case_dir: Path, manifest: dict,
) -> tuple[dict[str, dict], list[str]]:
    declared: dict[str, dict] = {}
    problems: list[str] = []
    for item in manifest.get("files") or []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            problems.append("case manifest contains an invalid file entry")
            continue
        path = item["path"]
        if path in declared:
            problems.append(f"duplicate manifest path {path}")
            continue
        target, path_problem = _case_relative_path(case_dir, path)
        if path_problem is not None:
            problems.append(path_problem)
            continue
        if target is None or not target.is_file():
            problems.append(f"missing {path}")
            continue
        if target.stat().st_size != item.get("bytes"):
            problems.append(f"size drift {path}")
        elif _sha(target) != item.get("sha256"):
            problems.append(f"digest drift {path}")
        declared[path] = item
    return declared, problems


def _shader_report_payloads(case_dir: Path, shader_dir: str,
                            referenced: set[int]) -> list[str]:
    """Required translated payloads for one frame's referenced programs."""
    required = [f"{shader_dir}/shaders.json"]
    try:
        report = _json(case_dir / shader_dir / "shaders.json")
    except (OSError, UnicodeError, json.JSONDecodeError, CaseError):
        return required
    for program in report.get("programs") or []:
        if (not isinstance(program, dict)
                or program.get("program") not in referenced):
            continue
        stages = program.get("stages")
        if not isinstance(stages, dict):
            continue
        for stage in stages.values():
            if not isinstance(stage, dict):
                continue
            for field in ("msl_file", "glsl_file", "spv_file"):
                name = stage.get(field)
                if isinstance(name, str) and name:
                    required.append(f"{shader_dir}/{name}")
    return required


def _sequence_inventory(sequence: dict, case_dir: Path) -> list[str]:
    required = list(REQUIRED_SEQUENCE_PAYLOADS)
    for entry in sequence.get("frames") or []:
        if not isinstance(entry, dict):
            continue
        relative = str(entry.get("dir") or "")
        if not relative:
            continue
        for name in ("frame-sequence.json", "frame-trace.json",
                     "frame-events.jsonl", "frame-reference.bgra"):
            required.append(f"{relative}/{name}")
        shader_dir = entry.get("shader_dir")
        if isinstance(shader_dir, str) and shader_dir:
            required.extend(_shader_report_payloads(
                case_dir, shader_dir,
                set(_frame_programs(case_dir / relative))))
        if entry.get("checkpoint_request_sha256"):
            index = entry.get("index")
            if isinstance(index, int):
                required.append(
                    f"checkpoints/frame-{index:03d}/checkpoint-request.json")
                required.append(
                    f"checkpoints/frame-{index:03d}/shader-receipt.json")
    return required


def verify(case_dir: Path | str) -> dict:
    case_dir = Path(case_dir).expanduser().resolve()
    manifest_path = case_dir / "case.json"
    if not manifest_path.is_file():
        raise CaseError("missing_manifest", str(manifest_path))
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise CaseError("schema", "unexpected case schema")
    if manifest.get("schema") not in CASE_SCHEMAS:
        raise CaseError("schema", "unexpected case schema")
    problems: list[str] = []
    declared, inventory_problems = _declared_inventory(case_dir, manifest)
    problems.extend(inventory_problems)
    sequence_summary: dict = {}
    is_sequence = manifest.get("schema") == SEQUENCE_SCHEMA
    reproduction = manifest.get("reproduction") or {}
    incomplete = reproduction.get("status") == "incomplete"
    if incomplete:
        # A bounded diagnostic-only summary has no sequence or replay command;
        # verify its declared evidence without pretending the absent
        # reproduction payloads are corrupt.  This applies to v1 single-frame
        # and v2 sequence summaries alike.
        required = ["reproduction-incomplete.json",
                    "diagnostic-report.json", "dependency-graph.json",
                    "graph.json"]
        sequence_summary = {
            "status": "incomplete",
            "reason": reproduction.get("reason"),
        } if is_sequence else {}
    elif is_sequence:
        required = ["sequence.json", "shaders/shaders.json",
                    "diagnostic-report.json", "dependency-graph.json",
                    "graph.json"]
        sequence_problems, sequence_summary = _verify_sequence(
            case_dir, manifest)
        problems.extend(sequence_problems)
    else:
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
    if is_sequence and not incomplete:
        # A complete v2 package must declare every structurally required
        # payload in its SHA-256 inventory.  Undeclared files are rejected too:
        # a substituted shader, checkpoint or resource blob must not hide
        # outside the inventory.
        try:
            sequence = _json(case_dir / "sequence.json")
        except (OSError, UnicodeError, json.JSONDecodeError, CaseError):
            sequence = {}
        for name in _sequence_inventory(sequence, case_dir):
            if name not in declared:
                problems.append(f"required payload is not declared: {name}")
        # Only authoritative payload locations are exhaustively inventoried.
        # The documented replay command writes its output into a scratch
        # directory inside the case; that output is not reproduction evidence
        # and must not invalidate an otherwise complete package.
        authoritative = ("frames/", "shaders/", "checkpoints/")
        on_disk: set[str] = set()
        for directory, _subdirs, files in os.walk(case_dir):
            for name in files:
                path = Path(directory) / name
                try:
                    relative = path.relative_to(case_dir).as_posix()
                except ValueError:
                    problems.append(f"payload outside the case directory: {path}")
                    continue
                if not relative.startswith(authoritative):
                    continue
                on_disk.add(relative)
        for name in sorted(on_disk - set(declared)):
            problems.append(f"payload is not declared in the inventory: {name}")
    if not is_sequence:
        shader_relative = str(manifest.get("shaders") or "shaders")
        shader_path, shader_problem = _case_relative_path(
            case_dir, shader_relative)
        if shader_problem is not None:
            problems.append(f"shader directory is invalid: {shader_problem}")
        elif (case_dir / "capture" / "frame-trace.json").is_file():
            for problem in _frame_shader_identity_problems(
                    case_dir / "capture", shader_path, 1):
                problems.append(problem["detail"])
    graph: dict = {}
    try:
        graph = json.loads((case_dir / "graph.json").read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        problems.append("graph.json is unreadable")
    if reproduction.get("independently_reproducible") is False:
        problems.append("reproduction is explicitly incomplete")
    verified = not problems
    if is_sequence:
        # A sequence is independently reproducible only when its own manifest
        # says so *and* every drift/integrity check above passed.
        independently_reproducible = bool(
            verified and reproduction.get("independently_reproducible"))
    else:
        # v1 single-frame cases pre-date the explicit reproduction block.
        # Missing metadata stays backward-compatible only for a genuinely
        # complete case: an incomplete or corrupted v1 case is never claimed
        # independently reproducible, even though its budget-limited summary
        # is expected and verified as incomplete evidence.
        independently_reproducible = bool(
            verified
            and not incomplete
            and reproduction.get("independently_reproducible") is not False)
    return {
        "schema": "gfx-strategy-b-case-verification/v1",
        "case_id": manifest.get("case_id"),
        "ok": verified,
        "problems": problems,
        "files": len(manifest.get("files") or []),
        "graph_status": graph.get("status"),
        "replay_status": manifest.get("replay_status"),
        "sequence": sequence_summary,
        "reproduction_status": reproduction.get("status"),
        "reproduction_reason": reproduction.get("reason"),
        "independently_reproducible": independently_reproducible,
    }


def replay_case(case_dir: Path | str, out_dir: Path | str,
                *, faults: dict | None = None) -> dict:
    case_dir = Path(case_dir).expanduser().resolve()
    verification = verify(case_dir)
    if not verification["ok"]:
        raise CaseError("verification", "; ".join(verification["problems"]))
    manifest = json.loads((case_dir / "case.json").read_text())
    if manifest.get("schema") == SEQUENCE_SCHEMA:
        return _replay_sequence_case(case_dir, Path(out_dir).expanduser().resolve(),
                                     faults=faults)
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
