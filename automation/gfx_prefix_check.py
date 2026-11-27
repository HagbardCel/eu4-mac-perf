"""Independent exact-prefix qualification for a captured graphics range.

The checker is campaign-independent.  It does not trust the renderer, the
replay report, or the model:

* the sealed frame, its event stream and its observation file are verified from
  disk;
* the checkpoint request is bound to the frame by the authoritative receipt;
* every comparison happens once, in
  :func:`gfx_independent_replay.reconcile_checkpoints`, with the channel scope
  derived from the sealed attachment identity;
* a missing, stale, foreign, duplicated or normalized observation is a failure.

It returns a structured verdict (``EXACT_PREFIX``, ``EXACT_RANGE``,
``FIRST_DIVERGENCE``, ``INCOMPLETE_EVIDENCE`` or ``UNSUPPORTED``).  Campaign
names such as ``EVENT20_EXACT`` belong to the campaign adapter.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from automation import (
    campaign_guard, dsh_campaign, gfx_frame_graph, gfx_independent_replay,
    gfx_observation, gfx_frame, replay_host_dependencies, state,
)
from automation.gfx_artifacts import sha256_file as _sha256

ROOT = Path(__file__).resolve().parents[1]


class PrefixCheckError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrefixCheckError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PrefixCheckError(f"not an object: {path}")
    return value


def _require(condition: bool, problems: list[str], message: str) -> None:
    if not condition:
        problems.append(message)


def classify_prefix(events_out: list[dict], *, problems: list[str],
                    end: int, require_end: int | None) -> dict:
    """Classify only authenticated evidence, walking the requested prefix.

    ``end`` is the capture's observation coverage; ``require_end`` is the
    acceptance boundary.  Later observations stay available in ``events`` as
    diagnostics but neither qualify nor invalidate the requested prefix.  A
    missing, invalid or excluded event inside the boundary stops an exact walk.
    A later mismatch may not be promoted to ``FIRST_DIVERGENCE`` when an earlier
    event could not be verified.

    The contract is: the complete replay must remain structurally valid (the
    global reconciliation, request binding and identity checks already enforce
    that), while pixel equality is asserted only through ``require_end``.
    """
    rows = {int(item["event"]): item for item in events_out
            if isinstance(item.get("event"), int)}
    target = end if require_end is None else min(int(require_end), end)
    verified_end = -1
    first_divergence = None
    unverified_event = None
    for event in range(target + 1):
        row = rows.get(event)
        if row is None:
            unverified_event = event
            break
        status = row.get("status")
        if status == "match":
            verified_end = event
            continue
        if status == "divergent" and row.get("authoritative", True):
            first_divergence = event
            break
        unverified_event = event
        break
    invalid_events = sorted(
        event for event, row in rows.items()
        if event <= target
        and row.get("status") not in ("match", "divergent",
                                      "unsupported_auxiliary"))
    excluded_events = sorted(
        event for event, row in rows.items()
        if event <= target and row.get("status") == "unsupported_auxiliary")
    verified_color_events = sorted(
        event for event, row in rows.items()
        if event <= target and row.get("status") == "match")
    if problems or unverified_event is not None:
        verdict = "INCOMPLETE_EVIDENCE"
        status = "incomplete"
        first_divergence = None
        verified_prefix = ({"start": 0, "end": verified_end}
                           if verified_end >= 0 else None)
    elif first_divergence is not None:
        verdict = "FIRST_DIVERGENCE"
        status = "divergent"
        verified_prefix = ({"start": 0, "end": first_divergence - 1}
                           if first_divergence > 0 else None)
    elif excluded_events:
        verdict = "UNSUPPORTED"
        status = "unsupported"
        verified_prefix = {"start": 0, "end": verified_end}
    elif require_end is not None and end < int(require_end):
        verdict = "EXACT_RANGE"
        status = "qualified_range"
        verified_prefix = {"start": 0, "end": end}
    else:
        verdict = "EXACT_PREFIX"
        status = "qualified"
        verified_prefix = {"start": 0, "end": target}
    return {
        "first_divergence": first_divergence,
        "unverified_event": unverified_event,
        "invalid_events": invalid_events,
        "excluded_events": excluded_events,
        "verified_color_events": verified_color_events,
        "verified_prefix": verified_prefix,
        "verdict": verdict,
        "status": status,
    }


def classify_selection(events_out: list[dict], *, problems: list[str],
                       selection: list[int]) -> dict:
    """Classify independently selected post-event observations.

    A selected set is never a prefix: unobserved events are simply not part of
    the claim, and no initial-state anchor exists.  A divergent selected event
    is the earliest selected divergence.
    """
    rows = {int(item["event"]): item for item in events_out
            if isinstance(item.get("event"), int)}
    matched: list[int] = []
    first_divergence = None
    unverified = None
    excluded: list[int] = []
    for event in selection:
        row = rows.get(event)
        if row is None:
            unverified = event
            break
        status = row.get("status")
        if status == "match":
            matched.append(event)
            continue
        if status == "divergent" and row.get("authoritative", True):
            first_divergence = event
            break
        if status == "unsupported_auxiliary":
            excluded.append(event)
            unverified = event
            break
        unverified = event
        break
    if problems or unverified is not None:
        verdict = "UNSUPPORTED" if excluded and not problems else \
            "INCOMPLETE_EVIDENCE"
        status = "unsupported" if verdict == "UNSUPPORTED" else "incomplete"
        first_divergence = None
    elif first_divergence is not None:
        verdict = "FIRST_DIVERGENCE"
        status = "divergent"
    else:
        verdict = "EXACT_SELECTION"
        status = "qualified_selection"
    return {
        "verdict": verdict,
        "status": status,
        "first_divergence": first_divergence,
        "unverified_event": unverified,
        "verified_prefix": None,
        "verified_events": matched,
        "excluded_events": excluded,
    }


def _tree_manifest(root: Path) -> list[dict]:
    files = []
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            files.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return files


def _root_digest(files: list[dict]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(f"{item['path']}\0{item['bytes']}\0{item['sha256']}\n".encode())
    return digest.hexdigest()


def resolve_trusted_reference(configured: str | Path | None,
                              candidate_workspace: Path | str | None,
                              *, root: Path | str = ROOT) -> Path:
    """Resolve the one explicitly configured trusted reference root.

    The value comes from trusted campaign configuration.  A missing value, a
    directory without a trusted-evidence record, a directory containing more
    than one seal, or a root inside the candidate workspace is refused.  The
    newest directory is never selected implicitly.
    """
    if configured is None or str(configured).strip() == "":
        raise PrefixCheckError("trusted_reference_root is not configured")
    path = Path(str(configured)).expanduser()
    if not path.is_absolute():
        path = Path(root) / path
    path = path.resolve()
    if candidate_workspace is not None:
        try:
            path.relative_to(Path(candidate_workspace).expanduser().resolve())
        except ValueError:
            pass
        else:
            raise PrefixCheckError(
                "trusted reference root is inside the candidate workspace")
    if not path.is_dir():
        raise PrefixCheckError(
            f"trusted reference root does not exist: {path}")
    direct = path / "trusted-evidence.json"
    if direct.is_file():
        return path
    nested = sorted(path.glob("*/trusted-evidence.json"))
    if len(nested) == 1:
        return nested[0].parent.resolve()
    if not nested:
        raise PrefixCheckError(
            f"trusted reference root has no trusted-evidence record: {path}")
    raise PrefixCheckError(
        "trusted reference root is ambiguous: more than one sealed evidence "
        "record exists; configure the exact seal directory")


def prepare_trusted_evidence(frame_dir: Path | str, destination: Path | str,
                             *, source_note: str = "",
                             read_only: bool = True, copy: bool = True,
                             validate: bool = True,
                             replace: bool = False) -> dict:
    """Copy a sealed frame outside the candidate workspace and bind its bytes.

    The destination must be disjoint from the source and outside the candidate
    workspace.  Existing evidence is never recursively deleted: replacement
    requires ``replace=True`` and moves the previous tree to a dated sibling.
    ``read_only`` is a POSIX permission hardening, recorded as such; it is not a
    claim that the filesystem itself is mounted read-only.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not frame_dir.is_dir():
        raise PrefixCheckError(f"frame directory does not exist: {frame_dir}")
    if gfx_independent_replay._path_overlap(frame_dir, destination):
        raise PrefixCheckError(
            "trusted evidence destination overlaps the source frame")
    if validate:
        gfx_frame.load_seal(frame_dir)
        gfx_frame.load_trace(frame_dir, verify_blobs=True)
    replaced_to = None
    if destination.exists():
        if not replace:
            raise PrefixCheckError(
                "trusted evidence destination already exists; pass replace to "
                "publish a reviewed replacement")
        if destination.is_symlink():
            raise PrefixCheckError(
                "trusted evidence destination is a symlink")
        record = destination / "trusted-evidence.json"
        if destination.is_dir() and not record.is_file():
            raise PrefixCheckError(
                "existing destination is not a trusted evidence root")
        backup = destination.with_name(
            f"{destination.name}.replaced-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        if backup.exists():
            raise PrefixCheckError(
                f"replacement backup already exists: {backup}")
        os.replace(destination, backup)
        replaced_to = str(backup)
    destination.mkdir(parents=True)
    if copy:
        target = destination / "frame"
        shutil.copytree(frame_dir, target, symlinks=True)
        frame_relative = "frame"
        recorded_frame = None
    else:
        target = frame_dir
        frame_relative = None
        recorded_frame = str(frame_dir)
    files = _tree_manifest(target)
    seal_path = frame_dir / "frame-seal.json"
    original_capture_dir = None
    if seal_path.is_file():
        try:
            original_capture_dir = _read_json(seal_path).get("capture_dir")
        except PrefixCheckError:
            original_capture_dir = None
    record = {
        "schema": "gfx-trusted-evidence/v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_note": source_note,
        "original_capture_dir": original_capture_dir,
        "frame_relative": frame_relative,
        "frame_path": recorded_frame,
        "files": files,
        "root_sha256": _root_digest(files),
        "replaced_to": replaced_to,
        "read_only": False,
    }
    if read_only and copy:
        # Explicit POSIX permission hardening, not a filesystem guarantee.
        for path in sorted(target.rglob("*"), reverse=True):
            try:
                path.chmod(0o555 if path.is_dir() else 0o444)
            except OSError:
                pass
        record.update({
            "read_only": True,
            "read_only_method": "posix_mode",
            "permissions": {"directories": "0555", "files": "0444"},
        })
        record["writable_after_hardening"] = os.access(target, os.W_OK)
    (destination / "trusted-evidence.json").write_text(
        json.dumps(record, indent=2) + "\n")
    if read_only and copy:
        try:
            destination.chmod(0o555)
        except OSError:
            pass
    return record


def trusted_frame_path(trusted_root: Path | str, *, portable: bool = False) -> tuple[Path, dict]:
    """Read the existing evidence descriptor and resolve its frame path."""
    root = Path(trusted_root).expanduser().resolve()
    record = _read_json(root / "trusted-evidence.json")
    if record.get("schema") not in (
            "gfx-trusted-evidence/v1", "m7-trusted-evidence/v1"):
        raise PrefixCheckError("unexpected trusted-evidence schema")
    relative = record.get("frame_relative")
    if portable and (record.get("frame_path") or not isinstance(relative, str)
                     or not relative or Path(relative).is_absolute()
                     or ".." in Path(relative).parts):
        raise PrefixCheckError("trusted reference has no portable frame path")
    if record.get("frame_path"):
        frame = Path(str(record["frame_path"])).expanduser().resolve()
    else:
        frame = (root / str(relative or "")).resolve()
    if portable and (frame == root or not frame.is_relative_to(root)):
        raise PrefixCheckError("trusted frame escapes its publication root")
    return frame, record


def verify_trusted_evidence(trusted_root: Path | str,
                            frame_dir: Path | str) -> dict:
    """Verify a digest-bound trusted root and return its record."""
    trusted_root = Path(trusted_root).expanduser().resolve()
    expected_frame, record = trusted_frame_path(trusted_root)
    if Path(frame_dir).expanduser().resolve() != expected_frame:
        raise PrefixCheckError(
            "frame directory is not the trusted-evidence frame")
    files = _tree_manifest(expected_frame)
    if files != record.get("files"):
        raise PrefixCheckError("trusted-evidence file manifest changed")
    if _root_digest(files) != record.get("root_sha256"):
        raise PrefixCheckError("trusted-evidence root digest changed")
    record = dict(record)
    record["_root"] = str(trusted_root)
    return record


def verify_adjacent_trusted_evidence(frame_dir: Path) -> dict | None:
    """Resolve the same optional trusted copy used by event inspection."""
    root = frame_dir.parent
    path = root / "trusted-evidence.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PrefixCheckError(f"{path}: {exc}") from exc
    try:
        return verify_trusted_evidence(root, frame_dir)
    except (OSError, ValueError, RuntimeError) as exc:
        raise PrefixCheckError(f"{path}: {exc}") from exc


def verify_execution_outputs(execution: dict, metal_dir: Path) -> list[str]:
    """Return provenance problems for a trusted execution's outputs."""
    problems: list[str] = []
    metal_dir = Path(metal_dir)
    for item in execution.get("outputs") or []:
        path = metal_dir / str(item.get("file"))
        if (not path.is_file()
                or path.stat().st_size != item.get("bytes")
                or _sha256(path) != item.get("sha256")):
            problems.append(
                "trusted checkpoint output changed after execution: "
                f"{item.get('file')}")
    replay_path = metal_dir / "replay.json"
    if (not replay_path.is_file()
            or _sha256(replay_path) != execution.get("replay_json_sha256")):
        problems.append("trusted replay result changed after execution")
    receipt_path = metal_dir / "shader-receipt.json"
    if (not receipt_path.is_file()
            or _sha256(receipt_path) != execution.get("receipt_sha256")):
        problems.append("trusted execution receipt changed after execution")
    return problems


def verify_candidate(workspace: Path | str,
                     sidecar_path: Path | str) -> dict:
    """Verify the worktree, pinned layout, dependency closure and path policy."""
    workspace = Path(workspace).expanduser().resolve()
    sidecar = dsh_campaign.load_sidecar(Path(sidecar_path))
    if sidecar.workspace.resolve() != workspace:
        raise PrefixCheckError(
            "candidate workspace is not the campaign sidecar workspace")
    try:
        state.require_worktree(
            workspace, ROOT, sidecar.candidate_baseline_commit or "")
    except RuntimeError as exc:
        raise PrefixCheckError(f"candidate worktree is not authorized: {exc}") from exc
    revision = state.trusted_git(workspace, "rev-parse", "HEAD").strip()
    changed: set[str] = set()
    status = state.trusted_git(workspace, "status", "--porcelain=v1", "-z")
    for entry in status.split("\0"):
        if len(entry) < 4:
            continue
        path = entry[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    committed = state.trusted_git(
        workspace, "diff", "--name-only",
        sidecar.candidate_baseline_commit or "HEAD").splitlines()
    changed.update(path for path in committed if path)
    for path in sorted(changed):
        if not dsh_campaign.path_allowed(path, sidecar.allowed_paths):
            raise PrefixCheckError(
                f"candidate change is outside the allowed paths: {path}")
        if dsh_campaign.path_allowed(path, sidecar.forbidden_paths):
            raise PrefixCheckError(
                f"candidate change is in a forbidden path: {path}")
    sources = replay_host_dependencies.candidate_sources(
        ROOT, sidecar.candidate_baseline_commit or "")
    return {
        "workspace": str(workspace),
        "sidecar": str(Path(sidecar_path).resolve()),
        "baseline_commit": sidecar.candidate_baseline_commit,
        "source_paths": list(sources),
        "source_digests": replay_host_dependencies.relative_dependencies(workspace, sources, strict=True),
        "revision": revision,
        "changed_paths": sorted(changed),
        "policy_digest": sidecar.policy_digest,
    }


def _execution_environment(execution_dir: Path) -> dict:
    """Environment for candidate replay execution.

    EU4 evidence-location variables are removed so the candidate process is not
    handed a path to authenticated reference evidence.  This is input
    separation, not a sandbox: agents are cooperative, and native candidate code
    can still read the filesystem.  Independence is established by the
    reference-free replay input and the checker's own pixel comparison.
    """
    home = execution_dir / "home"
    tmp = execution_dir / "tmp"
    home.mkdir(exist_ok=True)
    tmp.mkdir(exist_ok=True)
    injected_names = {
        "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
        "LIBRARY_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH",
    }
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("EU4_", "DYLD_"))
        and key not in injected_names
    }
    environment["HOME"] = str(home)
    environment["TMPDIR"] = str(tmp)
    return environment


def _cached_candidate_build(snapshot_root: Path, destination: Path,
                            cache_root: Path, sources) -> tuple[Path, dict]:
    """Build or reuse a candidate host keyed by its complete source identity."""
    identity = replay_host_dependencies.build_identity(snapshot_root, sources, strict=True)
    cache_entry = cache_root / identity["fingerprint"]
    metadata_path = cache_entry / "build.json"
    cached_host = cache_entry / "gfx-frame-replay"
    if metadata_path.is_file() and cached_host.is_file():
        try:
            metadata = _read_json(metadata_path)
        except PrefixCheckError:
            metadata = None
        if (metadata is not None
                and metadata.get("build_fingerprint") == identity["fingerprint"]
                and metadata.get("source_digests") == identity["dependencies"]
                and metadata.get("host_sha256") == _sha256(cached_host)):
            shutil.copy2(cached_host, destination)
            if _sha256(destination) != metadata["host_sha256"]:
                raise OSError("cached candidate host changed while copying")
            return destination, {
                **metadata, "schema": "gfx-replay-host-candidate-build/v1",
                "cache_hit": True, "host_path": str(destination),
            }
    temporary = destination.with_name(destination.name + ".tmp")
    record = replay_host_dependencies.build_snapshot_host(
        snapshot_root, temporary, sources=sources)
    if record["build_fingerprint"] != identity["fingerprint"]:
        raise OSError("snapshot identity changed before candidate build")
    os.replace(temporary, destination)
    record["cache_hit"] = False
    record["host_path"] = str(destination)
    record["host_sha256"] = _sha256(destination)
    cache_entry.mkdir(parents=True, exist_ok=True)
    metadata = {**record, "host_path": str(cached_host)}
    (cache_entry / "build.json.tmp").write_text(
        json.dumps(metadata, indent=2) + "\n")
    os.replace(cache_entry / "build.json.tmp", metadata_path)
    if not cached_host.exists():
        shutil.copy2(destination, cached_host)
    return destination, record


def _run_trusted_replay(frame_dir: Path, request: dict | None,
                        run_dir: Path | str | None = None, *,
                        trace: dict | None = None,
                        graph: dict | None = None,
                        candidate_workspace: Path | str | None = None,
                        candidate_sidecar: Path | str | None = None,
                        diagnostics: dict | None = None,
                        fenced_host: tuple[Path, dict] | None = None) -> dict:
    """Execute one Metal replay in a fresh, self-contained execution directory.

    ``run_dir`` is a campaign output root.  Every call creates a unique child
    that holds its own replay input, translation, candidate snapshot, host,
    Metal outputs and receipts; a failed attempt is preserved.  Immutable
    translation and candidate-build artifacts may be reused from the root's
    cache when their input identity is unchanged.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    if trace is None:
        trace = gfx_frame.load_trace(frame_dir, verify_blobs=True)
    if run_dir is None:
        run_root = Path(tempfile.mkdtemp(prefix="gfx-observation-check-"))
    else:
        run_root = Path(run_dir).expanduser().resolve()
        run_root.mkdir(parents=True, exist_ok=True)
    execution_dir = Path(tempfile.mkdtemp(prefix="exec-", dir=run_root))
    if diagnostics is not None:
        diagnostics["execution_dir"] = str(execution_dir)
    replay_input = execution_dir / "replay-input"
    input_record = gfx_frame.materialize_replay_input(
        frame_dir, trace, replay_input, graph=graph)
    if fenced_host is not None and input_record["shared_input_outputs"]:
        raise PrefixCheckError("fenced Metal input overlaps protected original outputs")
    request_path = None
    if request is not None:
        request_path = execution_dir / "checkpoint-request.json"
        native_request = request
        if fenced_host is not None:
            fields = ("event", "target", "file", "resource", "timing")
            native_request = {
                "schema": request["schema"],
                "checkpoints": [{key: row[key] for key in fields if key in row}
                                for row in request["checkpoints"]],
            }
        request_path.write_text(json.dumps(native_request, indent=2) + "\n")
    shaders = execution_dir / "shaders"
    translated = gfx_frame.translate_for_replay(
        frame_dir, shaders, cache_dir=run_root / "cache" / "translation",
        timeout=300)
    if translated.returncode != 0:
        raise PrefixCheckError(
            "trusted shader translation failed: "
            + (translated.stderr.strip() or translated.stdout.strip())[-400:])
    candidate = None
    if candidate_workspace is not None:
        if candidate_sidecar is None:
            raise PrefixCheckError(
                "trusted candidate execution requires an explicit sidecar")
        candidate = verify_candidate(Path(candidate_workspace),
                                     Path(candidate_sidecar))
        workspace = Path(candidate["workspace"])
        sources = candidate["source_paths"]
        before = candidate["source_digests"]
        snapshot_root = execution_dir / "candidate-snapshot"
        snapshot = replay_host_dependencies.snapshot_sources(
            workspace, snapshot_root, sources)
        host = execution_dir / "candidate-gfx-frame-replay"
        try:
            host, candidate_build = _cached_candidate_build(
                snapshot_root, host, run_root / "cache" / "hosts", sources)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrefixCheckError(
                f"candidate replay host build failed: {exc}") from exc
        after = replay_host_dependencies.relative_dependencies(workspace, sources, strict=True)
        if before != snapshot["digests"] or before != after:
            raise PrefixCheckError(
                "candidate renderer source changed between snapshot and build")
        if candidate_build.get("source_digests") != after:
            raise PrefixCheckError(
                "candidate source digest does not match the built snapshot")
        candidate["source_digest"] = hashlib.sha256(json.dumps(
            after, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        candidate["snapshot_files"] = snapshot["files"]
        candidate["build"] = candidate_build
    elif fenced_host is not None:
        host, candidate = fenced_host
    else:
        try:
            host = gfx_frame.build_replay_host()
        except (gfx_frame.FrameError, OSError, RuntimeError) as exc:
            raise PrefixCheckError(
                f"trusted replay host build failed: {exc}") from exc
    replay_dir = execution_dir / "metal"
    replay_dir.mkdir()
    completed, replay, execution = gfx_frame.execute_frame(
        host, frame=replay_input, shaders=shaders, out=replay_dir, checkpoints=request_path,
        env=None if fenced_host else _execution_environment(execution_dir),
        cwd=str(execution_dir), fenced=fenced_host is not None,
        checkpoint_outputs=(row["file"] for row in (request or {}).get("checkpoints", [])),
        restricted_inputs=input_record)
    if completed.returncode != 0:
        raise PrefixCheckError(
            f"trusted Metal replay exited with {completed.returncode}: "
            + (completed.stderr.strip() or completed.stdout.strip())[-2400:])
    if candidate is not None and execution["replay_host_sha256"] != candidate["build"]["host_sha256"]:
        raise PrefixCheckError("candidate replay host changed after execution")
    if candidate is not None and fenced_host is None:
        post_execution = replay_host_dependencies.relative_dependencies(workspace, sources, strict=True)
        if post_execution != candidate["source_digests"]:
            raise PrefixCheckError(
                "candidate renderer source changed after the trusted build")
    if candidate is not None and fenced_host is None and _sha256(host) != execution["replay_host_sha256"]:
        raise PrefixCheckError("candidate replay host changed after execution")
    if not replay or replay.get("status") != "ok" or replay.get("failed") != 0:
        raise PrefixCheckError("trusted Metal replay did not complete cleanly")
    if fenced_host is not None:
        from automation import gfx_independent_replay

        try:
            gfx_independent_replay.recorded_shader_compile_options(replay)
        except gfx_independent_replay.ReplayError as exc:
            raise PrefixCheckError(str(exc)) from exc
        execution["candidate"] = candidate
    if request is None:
        return {"metal_dir": replay_dir, "result": replay, "execution": execution}
    (replay_dir / "checkpoint-request.json").write_text(
        json.dumps(request, indent=2) + "\n")
    seal_path = frame_dir / "frame-seal.json"
    seal = _read_json(seal_path) if seal_path.is_file() else {}
    receipt = gfx_frame.build_shader_receipt(
        seal, replay_dir / "checkpoint-request.json", shaders)
    (replay_dir / "shader-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n")
    if fenced_host is not None:
        outputs = [row for row in execution["outputs"] if row["file"] != "replay.json"]
    else:
        outputs = [{"file": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
                   for path in sorted(replay_dir.iterdir()) if path.is_file()
                   and path.name not in ("replay.json", "shader-receipt.json", "checkpoint-request.json")]
    isolation = None
    if fenced_host is not None:
        isolation = {"mechanism": "seatbelt", "reference_roots": [str(frame_dir)]}
    elif candidate is not None:
        isolation = {
            "mechanism": "input-separation",
            "trust": (
                "candidate agents are cooperative participants; the replay "
                "input contains no reference pixels and the comparison is "
                "performed by the checker after execution"),
            "reference_roots": [str(frame_dir)],
        }
    execution.update(
        schema="gfx-trusted-execution/v1",
        mode="candidate-build-and-replay" if candidate is not None else "checker-native-replay",
        reference_isolation=isolation, candidate=candidate, outputs=outputs,
        execution_dir=str(execution_dir), frame_dir=str(frame_dir),
        request_sha256=_sha256(replay_dir / "checkpoint-request.json"),
        receipt_sha256=_sha256(replay_dir / "shader-receipt.json"))
    record_path = execution_dir / "execution.json"
    record_path.write_text(json.dumps(execution, indent=2) + "\n")
    (replay_dir / "trusted-execution.json").write_text(
        json.dumps(execution, indent=2) + "\n")
    return {
        "metal_dir": replay_dir,
        "request_path": replay_dir / "checkpoint-request.json",
        "execution_dir": execution_dir,
        "execution_record": record_path,
        "execution": execution,
    }


def _invalid_result(result: dict, problems: list[str], *, status: str,
                    events: list | None = None,
                    execution: dict | None = None) -> dict:
    result["problems_count"] = len(problems)
    if execution is not None:
        result["execution"] = execution
    result.update({
        "verdict": "INCOMPLETE_EVIDENCE" if status != "unsupported"
                   else "UNSUPPORTED",
        "status": status,
        "first_divergence": None,
        "verified_prefix": None,
        "events": events or [],
    })
    return result


def _scope_overlap(overlap: list[dict], scope: set[int]) -> list[dict]:
    """Overlap entries that make an expected output inside *scope* unattributable."""
    affected = []
    for item in overlap:
        events = {
            entry.get("event") for entry in item.get("expected_by") or []
            if (entry.get("kind") == "observation"
                and entry.get("role") == "color"
                and entry.get("timing") == "post_event")
        }
        if events & scope:
            affected.append(item)
    return affected


def preflight_fixture(frame_dir: Path | str, *, require_end: int,
                      trusted_root: Path | str | None = None) -> dict:
    """Cheap static eligibility check before an expensive trusted replay.

    Reuses the sealed-frame loader, the resource graph and the same
    provenance-aware input inventory as :func:`evaluate`.  It answers only
    whether the configured trusted fixture can qualify the requested prefix;
    it never executes candidate code.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    problems: list[str] = []
    result: dict = {"schema": "gfx-prefix-preflight/v1",
                    "frame_dir": str(frame_dir),
                    "target_end": int(require_end),
                    "problems": problems}
    trusted_record = None
    if trusted_root is not None:
        try:
            trusted_record = verify_trusted_evidence(trusted_root, frame_dir)
        except (PrefixCheckError, OSError) as exc:
            problems.append(f"trusted evidence is not usable: {exc}")
            return result
    try:
        seal = gfx_frame.load_seal(frame_dir, trusted_evidence=trusted_record)
        trace = gfx_frame.load_trace(frame_dir, verify_blobs=True)
    except (gfx_frame.FrameError, OSError, ValueError) as exc:
        problems.append(f"sealed frame is not usable: {exc}")
        return result
    result["authority"] = gfx_observation.authority(trace, seal)
    result["trusted_evidence"] = trusted_record is not None
    graph = gfx_frame_graph.build(frame_dir, trace=trace)
    prefix = graph.get("prefix") or {}
    if prefix.get("status") != "complete":
        problems.append(
            "observation coverage is not a complete prefix: "
            + str(prefix.get("status") or "unknown"))
        return result
    end = prefix.get("end")
    result["prefix_end"] = end
    if not isinstance(end, int) or end < int(require_end):
        problems.append(
            f"observation scope ends at event {end}, not {require_end}")
        return result
    overlap = _scope_overlap(
        gfx_frame.replay_input_overlap(trace, frame_dir, graph=graph),
        set(range(int(require_end) + 1)))
    result["shared_input_outputs"] = overlap
    for item in overlap:
        problems.append(
            f"expected payload {item['file']} remains a candidate input "
            f"({item['input_file']}); the fixture cannot qualify events "
            f"0..{require_end} independently")
    return result


def evaluate(frame_dir: Path | str, metal_dir: Path | str | None = None,
             *, request_path: Path | str | None = None,
             require_end: int | None = None,
             trusted_execution: bool = False,
             trusted_root: Path | str | None = None,
             trusted_run_dir: Path | str | None = None,
             candidate_workspace: Path | str | None = None,
             candidate_sidecar: Path | str | None = None,
             fenced_host: tuple[Path, dict] | None = None) -> dict:
    """Verify one sealed frame's exact prefix against its Metal replay.

    ``frame_dir`` contains the seal, event stream, blobs and observation file.
    ``metal_dir`` contains the replay result plus Metal observation outputs.
    The trusted checker must run outside the candidate workspace.

    Pixel comparison happens once, in
    :func:`gfx_independent_replay.reconcile_checkpoints`; this function
    validates identities, binding and coverage and classifies the reconciled
    result.  It never re-reads the same pixels to repeat that comparison.
    Reconciliation of the complete replay must stay structurally valid; the
    requested prefix bounds the pixel-equality claim, not the integrity checks.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = (Path(metal_dir).expanduser().resolve()
                 if metal_dir is not None else None)
    problems: list[str] = []
    result: dict = {
        "schema": "gfx-observation-check/v2",
        "frame_dir": str(frame_dir),
        "metal_dir": str(metal_dir),
        "required_through": require_end,
        "problems": problems,
    }
    trusted_record = None
    if trusted_root is not None:
        try:
            trusted_record = verify_trusted_evidence(trusted_root, frame_dir)
        except (PrefixCheckError, OSError) as exc:
            problems.append(f"trusted evidence is not usable: {exc}")
            return _invalid_result(result, problems, status="invalid")
        result["trusted_evidence"] = {
            "root": str(Path(trusted_root).expanduser().resolve()),
            "root_sha256": trusted_record.get("root_sha256"),
            "file_count": len(trusted_record.get("files") or []),
        }
    try:
        seal = gfx_frame.load_seal(frame_dir, trusted_evidence=trusted_record)
        trace = gfx_frame.load_trace(frame_dir, verify_blobs=True)
    except (gfx_frame.FrameError, OSError, ValueError) as exc:
        problems.append(f"sealed frame is not usable: {exc}")
        return _invalid_result(result, problems, status="invalid")
    result["evidence_limitations"] = []
    provenance = gfx_observation.authority(trace, seal)
    trusted_authority = trusted_record is not None
    if provenance == "authentic" and not trusted_authority:
        provenance = "unpinned"
        result["evidence_limitations"].append(
            "the frame claims authentic authority but is not rooted in "
            "externally pinned trusted evidence")
    result["evidence"] = {
        "authority": provenance,
        "trusted_evidence": trusted_authority,
        "session_id": seal.get("session_id"),
        "capture_frame_id": seal.get("capture_frame_id"),
        "manifest_sha256": seal.get("manifest_sha256"),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "reference_sha256": seal.get("reference_sha256"),
    }
    graph = gfx_frame_graph.build(frame_dir, trace=trace, trusted_evidence=trusted_record)
    overlap = gfx_frame.replay_input_overlap(trace, frame_dir, graph=graph)
    result["shared_input_outputs"] = overlap
    for item in overlap:
        roles = ", ".join(sorted({
            f"{entry['kind']}:{entry.get('role')}"
            for entry in item.get("expected_by") or []
        }))
        result["evidence_limitations"].append(
            f"expected payload {item['file']} is accessible as renderer input "
            f"{item['input_file']} ({roles}); its bytes cannot be independently "
            "attributed")
    shared_files = {
        item["file"] for item in overlap
        if any(entry.get("kind") == "observation"
               and entry.get("role") == "color"
               and entry.get("timing") == "post_event"
               for entry in item.get("expected_by") or [])
    }
    result["capture"] = gfx_independent_replay.capture_identity(seal, trace)
    result["graph_status"] = graph.get("status")
    result["graph_reasons"] = graph.get("reasons") or []
    prefix = graph.get("prefix") or {}
    result["prefix_status"] = prefix.get("status")
    result["prefix_end"] = prefix.get("end")
    mode = str(prefix.get("mode") or "prefix")
    if prefix.get("status") not in ("complete", "selected"):
        problems.append("observation coverage is not complete")
        problems.extend(str(problem) for problem in prefix.get("problems") or [])
        return _invalid_result(result, problems, status="incomplete")
    selection: list[int] = []
    if mode == "prefix":
        end = int(prefix["end"])
        require_end = end if require_end is None else int(require_end)
        result["required_through"] = require_end
        if end < require_end:
            problems.append(
                f"capture prefix ends at event {end}, not {require_end}")
    else:
        end = None
        selection = [int(value) for value in prefix.get("events") or []]
        result["required_through"] = None
        result["selection"] = selection
    result["initial_state"] = prefix.get("initial_state") or []
    result["state_coverage"] = prefix.get("state_coverage") or []
    result["prefix_coverage"] = {
        "status": prefix.get("status"),
        "mode": mode,
        "end": end,
        "events": selection,
        "post_events": len(prefix.get("post_events") or []),
        "excluded_events": prefix.get("excluded_events") or [],
    }
    expected_request = gfx_frame_graph.checkpoint_request(graph)
    expected_canonical = json.dumps(
        expected_request, sort_keys=True, separators=(",", ":"))
    result["expected_request_sha256"] = hashlib.sha256(
        expected_canonical.encode()).hexdigest()
    if trusted_execution:
        # A safety stop ends the campaign; replay count is not budgeted, so
        # the agent may validate as many corrected candidates as it can
        # justify.
        execution_diagnostics: dict = {}
        try:
            campaign_guard.require_active_from_env()
            trusted = _run_trusted_replay(
                frame_dir, expected_request, trusted_run_dir,
                trace=trace, graph=graph,
                candidate_workspace=candidate_workspace,
                candidate_sidecar=candidate_sidecar,
                diagnostics=execution_diagnostics, fenced_host=fenced_host)
        except campaign_guard.CampaignRefusal as exc:
            problems.append(f"trusted replay refused: {exc}")
            result["failed_execution"] = execution_diagnostics
            return _invalid_result(
                result, problems, status="invalid",
                execution={"mode": "candidate-build-and-replay",
                           "status": "refused", "error": str(exc)})
        except (PrefixCheckError, OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"trusted Metal execution failed: {exc}")
            result["failed_execution"] = execution_diagnostics
            return _invalid_result(
                result, problems, status="invalid",
                execution={"mode": "candidate-build-and-replay",
                           "status": "failed", "error": str(exc)})
        result["execution"] = trusted["execution"]
        result["execution_dir"] = str(trusted["execution_dir"])
        if trusted_record is not None:
            try:
                rechecked = verify_trusted_evidence(trusted_root, frame_dir)
            except (PrefixCheckError, OSError) as exc:
                problems.append(
                    f"trusted evidence changed during execution: {exc}")
            else:
                if rechecked.get("root_sha256") != trusted_record.get(
                        "root_sha256"):
                    problems.append(
                        "trusted evidence root digest changed during execution")
        metal_dir = trusted["metal_dir"]
        execution = trusted["execution"]
        problems.extend(verify_execution_outputs(execution, metal_dir))
    else:
        if metal_dir is None:
            problems.append("Metal result directory is required without "
                            "trusted execution")
            return _invalid_result(result, problems, status="invalid")
    request_file = (Path(request_path).expanduser().resolve()
                    if request_path is not None
                    else metal_dir / "checkpoint-request.json")
    if not request_file.is_file():
        problems.append(f"checkpoint request is absent: {request_file}")
        return _invalid_result(result, problems, status="invalid")
    try:
        request = _read_json(request_file)
    except PrefixCheckError as exc:
        problems.append(str(exc))
        return _invalid_result(result, problems, status="invalid")
    request_sha = _sha256(request_file)
    result["request"] = {"path": str(request_file), "sha256": request_sha}
    if json.dumps(request, sort_keys=True,
                  separators=(",", ":")) != expected_canonical:
        problems.append(
            "published checkpoint request does not agree with the independent "
            "derivation from the sealed frame")
    # The existing authoritative binding path: it validates the published
    # request against the graph plan and binds it to the sealed frame through
    # the shader receipt.  A candidate-authored receipt is never trusted on
    # its own.
    _, _, binding_problems, binding = \
        gfx_independent_replay.checkpoint_request_evidence(
            graph, metal_dir, seal=seal)
    result["request_binding"] = binding
    for problem in binding_problems:
        problems.append(
            "checkpoint request is not bound: "
            + str(problem.get("detail") or problem.get("kind")))
    replay_path = metal_dir / "replay.json"
    try:
        replay = _read_json(replay_path)
    except PrefixCheckError as exc:
        problems.append(f"Metal replay result is not usable: {exc}")
        return _invalid_result(result, problems, status="invalid")
    result["replay"] = {
        "path": str(replay_path),
        "sha256": _sha256(replay_path),
        "status": replay.get("status"),
        "events": replay.get("events"),
        "encoded": replay.get("encoded"),
        "failed": replay.get("failed"),
    }
    _require(replay.get("status") == "ok", problems,
             "Metal replay status is not ok")
    _require(replay.get("failed") == 0, problems,
             "Metal replay reported failed events")
    _require(replay.get("events") == len(trace.get("_events") or []),
             problems, "Metal replay event count does not match the frame")
    _require(replay.get("encoded") == replay.get("events"), problems,
             "Metal replay did not encode every event")
    executed_request_sha = (execution.get("executed_request_sha256", request_sha)
                            if fenced_host is not None and trusted_execution else request_sha)
    for field, value in (
            ("checkpoint_request_sha256", executed_request_sha),
            ("checkpoint_request_file", request_file.name)):
        if replay.get(field) is not None:
            _require(replay.get(field) == value, problems,
                     f"Metal replay {field} does not bind the request")
    try:
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            graph, request, replay, frame_dir, metal_dir,
            capture=result["capture"])
    except (gfx_independent_replay.ReplayError, OSError, ValueError) as exc:
        problems.append(f"checkpoint reconciliation failed: {exc}")
        return _invalid_result(result, problems, status="invalid")
    result["checkpoint_validation"] = validation
    _require(validation.get("status") == "ok", problems,
             "checkpoint reconciliation reported invalid evidence")

    sealed = {int(record["event"]): record
              for record in prefix.get("post_events") or []}
    excluded = {int(event) for event in prefix.get("excluded_events") or []}
    result["excluded_events"] = sorted(excluded)
    events_out: list[dict] = []
    seen: set[int] = set()
    for entry in results:
        if entry.get("timing") != "post_event":
            continue
        event = int(entry.get("event"))
        seen.add(event)
        beyond_target = (mode == "prefix" and require_end is not None
                         and event > int(require_end))
        observation = sealed.get(event)
        row = {
            "event": event,
            "target": entry.get("target"),
            "resource": entry.get("resource"),
            "timing": entry.get("timing"),
            "status": entry.get("status"),
            "reference_file": entry.get("reference_file"),
            "metal_file": entry.get("metal_file"),
            "reference_sha256": entry.get("reference_sha256"),
            "metal_sha256": entry.get("metal_sha256"),
            "dimensions": [entry.get("width"), entry.get("height")],
            "reference_pixel_format": entry.get("reference_pixel_format"),
            "candidate_pixel_format": entry.get("candidate_pixel_format"),
            "alpha_applicable": entry.get("alpha_applicable"),
            "compared_channels": entry.get("compared_channels"),
            "independently_verified": bool(
                (observation or {}).get("color", {}).get("file")
                not in shared_files),
            "statistics": entry.get("statistics") or {},
        }
        problem = None
        if entry.get("status") == "invalid":
            problem = str(entry.get("reason") or "reconciled status is invalid")
        elif event in excluded:
            problem = "unexpected result for an excluded event"
        elif observation is None:
            problem = "sealed observation is absent"
        elif mode == "prefix" and event > end:
            problem = "result is outside the verified coverage"
        else:
            scope = gfx_observation.format_scope(observation)
            colour = observation.get("color") or {}
            checks = (
                (entry.get("resource"), observation.get("resource"),
                 "resource"),
                (str(entry.get("target")), str(observation.get("target_name")),
                 "target"),
                (entry.get("timing"), observation.get("timing"), "timing"),
                (entry.get("reference_file"),
                 str(frame_dir / "blobs" / str(colour.get("file") or "")),
                 "reference file"),
                (entry.get("reference_sha256"), colour.get("sha256"),
                 "reference digest"),
                (entry.get("reference_pixel_format"),
                 scope.get("pixel_format"), "reference pixel format"),
                (entry.get("candidate_pixel_format"),
                 scope.get("pixel_format"), "candidate pixel format"),
                (entry.get("compared_channels"),
                 scope.get("compared_channels"), "compared channels"),
                (entry.get("alpha_applicable"),
                 scope.get("alpha_applicable"), "alpha applicability"),
                (entry.get("width"), observation.get("width"), "width"),
                (entry.get("height"), observation.get("height"), "height"),
            )
            for actual, expected, label in checks:
                if actual != expected:
                    problem = f"{label} disagrees with the sealed observation"
                    break
            if problem is None and scope["problems"]:
                problem = scope["problems"][0]
            if problem is None and entry.get("status") not in (
                    "match", "divergent"):
                problem = str(entry.get("reason")
                              or "reconciled status is not match or divergence")
        if problem is not None:
            if not beyond_target:
                problems.append(f"prefix event {event}: {problem}")
            row["status"] = "invalid"
            row["problem"] = problem
        events_out.append(row)
    expected_events = (range(end + 1) if mode == "prefix" else selection)
    for event in expected_events:
        if event in seen:
            continue
        if event in excluded:
            events_out.append({
                "event": event,
                "status": "unsupported_auxiliary",
                "excluded": True,
                "problem": "auxiliary target with a non-comparable colour "
                           "format; accounted for but not compared",
            })
            continue
        if mode != "prefix" or require_end is None or event <= require_end:
            problems.append(f"event {event} has no reconciled result")
        events_out.append({
            "event": event,
            "status": "missing",
            "problem": "no reconciled Metal result",
        })
    events_out.sort(key=lambda item: item["event"])
    result["events"] = events_out
    result["observed_events"] = len(events_out)
    scope_events = set()
    if mode == "prefix" and end is not None:
        scope_end = end if require_end is None else min(int(require_end), end)
        scope_events = set(range(scope_end + 1))
    elif mode == "selected":
        scope_events = set(selection)
    result["independent_equality"] = not _scope_overlap(overlap, scope_events)
    if not result["independent_equality"]:
        result["evidence_limitations"].append(
            "one or more compared post-event colour outputs are also renderer "
            "inputs; the pixel match is not independently attributable")
    if mode == "prefix":
        classification = classify_prefix(
            events_out, problems=problems, end=end, require_end=require_end)
    else:
        classification = classify_selection(
            events_out, problems=problems, selection=selection)
    result.update(classification)
    result["exact"] = classification["verdict"] == "EXACT_PREFIX"
    # The single acceptance predicate: pixel equality and independent
    # attribution.  Campaign adapters add only their reference authority.
    result["qualified"] = bool(
        result["exact"] and result["independent_equality"])
    result["problems_count"] = len(problems)
    return result


def _build_fenced_candidate(repo: Path, trusted_revision: str, commit: str,
                            directory: Path) -> tuple[Path, dict]:
    """Build the Metal core from the candidate and the CLI from the trusted checkout."""
    import platform

    from automation import candidate_snapshot, native_build

    host = replay_host_dependencies
    sources = host.candidate_sources(repo, trusted_revision)
    closure = host.dependency_paths(repo, sources, strict=True)
    entry_point = sources[1]
    snapshot = directory / "sources"
    digests = candidate_snapshot.materialize_commit(
        repo, commit, snapshot, tuple(path for path in closure if path != entry_point))
    candidate_snapshot.stage_trusted_sources(repo, snapshot, (entry_point,), digests)

    clang, sdk = native_build.qualification_toolchain("clang")
    build = directory / "build"
    build.mkdir()
    executable = build / "metal-replay"
    flags = host.BUILD_FLAGS
    linked = native_build.qualification_compile(
        clang, sdk, [*flags, "-o", executable, *(snapshot / path for path in sources)],
        build=build, readable=(snapshot,))
    if linked.returncode:
        raise PrefixCheckError(f"Metal compiler exit {linked.returncode}: "
                               + (linked.stderr or linked.stdout)[-2500:])
    producer = {"revision": commit, "source_digests": digests,
                "build": {"host_sha256": _sha256(executable)},
                "recipe": native_build.qualification_recipe(clang, sdk, flags),
                "environment": {"macos": platform.mac_ver()[0], "os_build": platform.version()},
                "architecture": platform.machine()}
    return executable, producer


def qualify_candidate(repo: Path, trusted_revision: str, commit: str, directory: Path,
                      frame: Path, trusted: dict, case: dict) -> dict:
    from automation import dsh_receipts

    directory = directory.resolve()
    executable, producer = _build_fenced_candidate(repo, trusted_revision, commit, directory)
    result = evaluate(
        frame, trusted_root=trusted["_root"], trusted_execution=True,
        require_end=case["require_end"], trusted_run_dir=directory,
        fenced_host=(executable, producer))
    observed = {row["event"]: row for row in result.get("events", [])
                if row.get("status") in ("match", "divergent")
                and row.get("independently_verified")}
    exact_regressions = [event for event in case.get("preserve_exact", [])
                         if observed.get(event, {}).get("status") != "match"]
    if result.get("problems") or result.get("failed_execution"):
        verdict = "execution_failure"
    elif not result.get("independent_equality") or exact_regressions:
        verdict = "regression"
    elif result.get("qualified"):
        verdict = "exact"
    elif result.get("verdict") == "FIRST_DIVERGENCE":
        verdict = "different"
    else:
        verdict = "unsupported"
    event = result.get("first_divergence")
    execution_dir = result.get("execution_dir")
    evidence = (Path(execution_dir) / "metal" / "trusted-execution.json"
                if execution_dir else None)
    outputs = []
    if evidence is not None:
        execution_record = _read_json(evidence)
        names = [item["file"] for item in execution_record["outputs"]]
        outputs = dsh_receipts.artifact_records(directory, (
            (evidence.parent / name).relative_to(directory).as_posix()
            for name in [*names, evidence.name, "replay.json", "shader-receipt.json", "checkpoint-request.json"]))
    return {
        "verdict": verdict, "evidence_tier": "original-eu4", "outputs": outputs,
        "execution": "completed" if result.get("independent_equality") else "failed",
        "independently_verified": bool(result.get("independent_equality")),
        "execution_receipt_sha256": _sha256(evidence) if evidence is not None else None,
        "reference": case["reference"], "coverage": sorted(observed),
        "candidate": producer, "scope": {"through": case["require_end"]},
        "execution_identity": execution_record if evidence is not None else None,
        "earliest_observed_divergence": observed.get(event),
        "preserved_exact": [n for n in observed if observed[n]["status"] == "match"],
        "execution_dir": (execution_dir
                          or (result.get("failed_execution") or {}).get("execution_dir")),
        "problems": result.get("problems", []) + [
            f"previously exact event {required} is {observed.get(required, {}).get('status', 'unverified')}"
            for required in exact_regressions],
    }


def qualify_full_frame(repo: Path, trusted_revision: str, commit: str, directory: Path,
                       frame: Path, reference: dict, comparison_policy: dict) -> dict:
    """Preserve the pre-seal M6 frame using its separately pinned file identities."""
    from automation import gfx_frame_pixels

    trace = gfx_frame.load_trace(frame, verify_blobs=True)
    identities = {"manifest_sha256": _sha256(frame / "frame-trace.json"),
                  "event_stream_sha256": _sha256(frame / "frame-events.jsonl"),
                  "image_sha256": trace["frame"]["reference_sha256"]}
    if any(reference.get(key) != value for key, value in identities.items()):
        return {"verdict": "invalid_provenance", "problems": ["final-frame reference identity differs"]}
    if len(trace["_events"]) != reference["event_count"]:
        return {"verdict": "invalid_provenance", "problems": ["original final-frame event coverage differs"]}
    executable, producer = _build_fenced_candidate(repo, trusted_revision, commit, directory)
    graph = gfx_frame_graph.build(frame, trace=trace)
    executed = _run_trusted_replay(frame, None, directory, trace=trace, graph=graph,
                                   fenced_host=(executable, producer))
    output = executed["metal_dir"]
    output_record = executed["result"]
    outputs = [{**item, "file": (output / item["file"]).relative_to(directory).as_posix()}
               for item in executed["execution"]["outputs"]]
    if (output_record.get("events") != reference["event_count"]
            or output_record.get("encoded") != reference["event_count"]):
        raise PrefixCheckError("final-frame replay did not encode the protected event set")
    if (output_record.get("frame") or {}).get("raw_file") != "metal-frame.bgra":
        raise PrefixCheckError("final-frame readback file identity differs")
    report = gfx_frame_pixels.compare(frame, output, comparison_policy)
    stats = report["statistics"]
    exact = report["status"] == "pass" and stats["mismatched_pixels"] == 0
    return {"verdict": "exact" if exact else "different", "evidence_tier": "original-eu4",
            "execution": "completed", "independently_verified": True, "reference": identities,
            "candidate": producer, "execution_identity": executed["execution"], "coverage": ["all-events-encoded", "exact-bgra"],
            "scope": {"events": reference["event_count"], "channels": "BGRA"},
            "outputs": outputs,
            "earliest_observed_divergence": None if exact else {"phase": "final", "statistics": stats},
            "problems": [] if exact else ["final attachment differs"]}


def _main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="copy and bind a trusted reference")
    prepare.add_argument("--frame", required=True)
    prepare.add_argument("--destination", required=True)
    prepare.add_argument("--note", default="")
    prepare.add_argument("--no-copy", action="store_true")
    prepare.add_argument("--replace", action="store_true")
    verify = sub.add_parser("verify", help="verify a trusted reference root")
    verify.add_argument("--root", required=True)
    verify.add_argument("--frame", required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        record = prepare_trusted_evidence(
            args.frame, args.destination, source_note=args.note,
            copy=not args.no_copy, replace=args.replace)
        print(json.dumps({
            "schema": "gfx-trusted-evidence-prepared/v1",
            "destination": str(Path(args.destination).expanduser().resolve()),
            "frame_relative": record.get("frame_relative"),
            "frame_path": record.get("frame_path"),
            "root_sha256": record.get("root_sha256"),
            "file_count": len(record.get("files") or []),
        }, indent=2))
        return 0
    record = verify_trusted_evidence(args.root, args.frame)
    print(json.dumps({
        "schema": "gfx-trusted-evidence-verified/v1",
        "root": str(Path(args.root).expanduser().resolve()),
        "root_sha256": record.get("root_sha256"),
        "file_count": len(record.get("files") or []),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
