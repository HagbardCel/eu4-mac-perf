"""Durable provisional checkpoints for contract-gap handoff.

The trusted checker writes a checkpoint only after it has validated the
candidate's observable evidence. The checkpoint is a work order for an
independent contract qualification campaign, not an acceptance of L3.2b.
The bundle is immutable, lives outside the candidate worktree and outside the
checker's temporary packet directory, and records every referenced sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_draw_packet as trusted_packet  # noqa: E402
import eu4_gfx_op_capture as trusted_capture  # noqa: E402
from automation.gfx_evidence_policy import evidence_relevant  # noqa: E402

SCHEMA = "eu4-gfx-provisional-checkpoint/v1"
BUNDLE_SCHEMA = "eu4-gfx-checkpoint-bundle/v1"
CAPTURE_REFERENCE_SCHEMA = "eu4-gfx-capture-reference/v1"


class CheckpointError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _write_json(path: Path, value: Any) -> str:
    text = _canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_tree_digest(workspace: Path, changed: list[str]) -> str:
    """Stable digest of the candidate's allowlisted changed files."""
    rows = []
    for rel in sorted(changed):
        path = Path(workspace) / rel
        if not path.is_file():
            raise CheckpointError(f"changed path is not a file: {rel}")
        rows.append({"path": rel, "sha256": _sha(path), "size": path.stat().st_size})
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def source_tree_digest_at_commit(repo: Path, commit: str, changed: list[str]) -> str:
    """Recompute the candidate source digest from a committed tree."""
    rows = []
    for rel in sorted(changed):
        completed = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise CheckpointError(f"changed path is not at the candidate commit: {rel}")
        data = completed.stdout
        rows.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def _resolve(base: Path, text: str) -> Path | None:
    candidate = Path(str(text or ""))
    if not str(candidate) or candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (base.resolve() / candidate).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _referenced_sidecars(
    packet: dict, packet_dir: Path, capture_dir: Path
) -> list[dict]:
    """Collect every sidecar/payload the packet claims, with its source."""
    found: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(relative: str, base: Path, label: str) -> None:
        if not isinstance(relative, str) or not relative:
            return
        path = _resolve(base, relative)
        if path is None:
            raise CheckpointError(f"referenced sidecar is missing: {relative}")
        key = (str(path), label)
        if key in seen:
            return
        seen.add(key)
        found.append(
            {
                "label": label,
                "source": "packet" if base == packet_dir else "capture",
                "relative": relative,
                "source_path": str(path),
                "sha256": _sha(path),
                "size": path.stat().st_size,
            }
        )

    inputs = packet.get("inputs")
    if isinstance(inputs, dict):
        for name, item in inputs.items():
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if isinstance(value, dict) and isinstance(value.get("sidecar"), str):
                add(value["sidecar"], packet_dir, f"{name}:sidecar")
            for index, evidence in enumerate(item.get("evidence") or []):
                if not isinstance(evidence, dict):
                    continue
                payload = evidence.get("payload")
                if not isinstance(payload, str) or not payload:
                    continue
                packet_path = _resolve(packet_dir, payload)
                if packet_path is not None:
                    add(payload, packet_dir, f"{name}:evidence[{index}]")
                else:
                    add(payload, capture_dir, f"{name}:evidence[{index}]")
    return found


def write_checkpoint(
    run_root: Path,
    *,
    campaign_id: str,
    candidate_base_commit: str,
    trusted_contract_commit: str,
    checker_sha256: str,
    contract_version: str,
    contract_sha256: str | None,
    manifest_sha256: str,
    candidate_digest: str,
    observer_library_sha256: str,
    capture: dict,
    packet: dict,
    packet_dir: Path,
    capture_dir: Path,
    target: dict,
    fields: list[dict],
    contract_gaps: list[dict],
    checkpoint_kind: str,
    blocked_reason: str,
) -> dict:
    """Write a checkpoint bundle atomically and return its identity."""
    if checkpoint_kind not in ("partial", "complete_provisional"):
        raise CheckpointError(f"invalid checkpoint kind: {checkpoint_kind!r}")
    session = str(capture.get("session") or "unknown")
    bundle_name = f"{campaign_id}-{session}-{candidate_digest}"
    root = Path(run_root) / "checkpoints"
    final = root / bundle_name
    if final.exists():
        # A retry of the same checker result is idempotent only when the
        # existing checkpoint names the same candidate and capture.
        try:
            existing = json.loads((final / "checkpoint.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"existing checkpoint is unreadable: {exc}") from exc
        if existing.get("candidate_digest") != candidate_digest:
            raise CheckpointError("checkpoint bundle name collision")
        return {
            "path": str(final / "checkpoint.json"),
            "sha256": _sha(final / "checkpoint.json"),
            "bundle": str(final),
            "bundle_manifest_sha256": existing.get("bundle_manifest_sha256"),
        }
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=bundle_name + ".tmp-", dir=str(root)))
    try:
        payload_dir = temporary / "packet"
        payload_dir.mkdir()
        packet_path = packet_dir / "draw-packet.json"
        if not packet_path.is_file():
            raise CheckpointError("candidate packet is missing")
        shutil.copy2(packet_path, payload_dir / "draw-packet.json")
        source_payloads = packet_dir / "payloads"
        if source_payloads.is_dir():
            shutil.copytree(source_payloads, payload_dir / "payloads")

        sidecars = _referenced_sidecars(packet, packet_dir, capture_dir)
        sidecar_dir = temporary / "sidecars"
        sidecar_dir.mkdir()
        for index, item in enumerate(sidecars):
            source = Path(item["source_path"])
            target_name = f"{index:03d}-{source.name}"
            shutil.copy2(source, sidecar_dir / target_name)
            item["bundle_path"] = f"sidecars/{target_name}"
        (temporary / "sidecar-map.json").write_text(
            _canonical({"schema": "eu4-gfx-checkpoint-sidecars/v1", "sidecars": sidecars}),
            encoding="utf-8",
        )

        reference = {
            "schema": CAPTURE_REFERENCE_SCHEMA,
            "capture_dir": str(Path(capture_dir).resolve()),
            "session": capture.get("session"),
            "pid": capture.get("pid"),
            "executable_sha256": capture.get("executable_sha256"),
            "library_sha256": capture.get("library_sha256"),
            "receipt_token": capture.get("receipt_token"),
            "capture_plan": capture.get("capture_plan"),
            "capture_plan_sha256": capture.get("capture_plan_sha256"),
        }
        (temporary / "capture-reference.json").write_text(
            _canonical(reference), encoding="utf-8"
        )

        files = []
        for path in sorted(temporary.rglob("*")):
            if not path.is_file():
                continue
            files.append(
                {
                    "path": path.relative_to(temporary).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha(path),
                }
            )
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "files": files,
        }
        manifest_path = temporary / "bundle-manifest.json"
        _write_json(manifest_path, manifest)
        manifest_sha = _sha(manifest_path)

        checkpoint = {
            "schema": SCHEMA,
            "checkpoint_kind": checkpoint_kind,
            "campaign_id": campaign_id,
            "candidate_base_commit": candidate_base_commit,
            "candidate_digest": candidate_digest,
            "trusted_contract_commit": trusted_contract_commit,
            "checker_sha256": checker_sha256,
            "contract_version": contract_version,
            "contract_sha256": contract_sha256,
            "manifest_sha256": manifest_sha256,
            "observer_library_sha256": observer_library_sha256,
            "capture": reference,
            "packet": {
                "path": "packet/draw-packet.json",
                "sha256": _sha(payload_dir / "draw-packet.json"),
                "schema": packet.get("schema"),
            },
            "target": target,
            "fields": fields,
            "contract_gaps": contract_gaps,
            "blocked_reason": blocked_reason,
            "bundle_manifest_sha256": manifest_sha,
        }
        checkpoint_path = temporary / "checkpoint.json"
        _write_json(checkpoint_path, checkpoint)
        os.replace(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "path": str(final / "checkpoint.json"),
        "sha256": _sha(final / "checkpoint.json"),
        "bundle": str(final),
        "bundle_manifest_sha256": manifest_sha,
    }


def validate_qualified_bundle(
    bundle_dir: Path,
    *,
    expected_candidate_digest: str | None = None,
    expected_checker_sha256: str | None = None,
    expected_trusted_commit: str | None = None,
    expected_fields: set[str] | None = None,
    require_packet_ready: bool = True,
) -> list[str]:
    """Validate a persisted qualified bundle and its receipt."""
    bundle = Path(bundle_dir)
    receipt_path = bundle / "qualification-receipt.json"
    manifest_path = bundle / "bundle-manifest.json"
    errors: list[str] = []
    if not receipt_path.is_file():
        return ["qualified receipt is missing"]
    if not manifest_path.is_file():
        return ["qualified bundle manifest is missing"]
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"qualified bundle is unreadable: {exc}"]
    if receipt.get("schema") != "eu4-gfx-qualification-receipt/v1":
        errors.append("qualified receipt has the wrong schema")
    if receipt.get("bundle_manifest_sha256") != _sha(manifest_path):
        errors.append("qualified receipt manifest hash differs")
    if expected_candidate_digest is not None \
            and receipt.get("candidate_digest") != expected_candidate_digest:
        errors.append("qualified receipt candidate digest differs")
    if expected_checker_sha256 is not None \
            and receipt.get("checker_sha256") != expected_checker_sha256:
        errors.append("qualified receipt checker identity differs")
    if expected_trusted_commit is not None \
            and receipt.get("trusted_contract_commit") != expected_trusted_commit:
        errors.append("qualified receipt trusted contract differs")
    packet = receipt.get("packet") or {}
    if require_packet_ready and packet.get("packet_ready") is not True:
        errors.append("qualified receipt does not establish packet_ready")
    fields = receipt.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append("qualified receipt has no field results")
    else:
        names = set()
        for row in fields:
            if not isinstance(row, dict):
                errors.append("qualified receipt field is not an object")
                continue
            names.add(row.get("name"))
            if row.get("state") != "validated":
                errors.append(f"qualified field {row.get('name')} is not validated")
        if expected_fields is not None and not expected_fields <= names:
            errors.append(
                "qualified receipt omits fields: "
                + ", ".join(sorted(expected_fields - names))
            )
    capture = receipt.get("capture")
    if not isinstance(capture, dict) or not capture.get("session") \
            or not capture.get("library_sha256"):
        errors.append("qualified receipt capture identity is incomplete")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("qualified bundle manifest has no files")
    else:
        for item in files:
            if not isinstance(item, dict):
                errors.append("qualified bundle manifest entry is invalid")
                continue
            rel = item.get("path")
            if not isinstance(rel, str) or rel.startswith("/") or ".." in Path(rel).parts:
                errors.append(f"qualified bundle path is invalid: {rel!r}")
                continue
            member = bundle / rel
            if not member.is_file():
                errors.append(f"qualified bundle member is missing: {rel}")
                continue
            if member.stat().st_size != item.get("size"):
                errors.append(f"qualified bundle member size differs: {rel}")
            if _sha(member) != item.get("sha256"):
                errors.append(f"qualified bundle member hash differs: {rel}")
    packet_member = bundle / str(packet.get("path") or "")
    if not packet_member.is_file():
        errors.append("qualified packet is missing from the bundle")
    elif _sha(packet_member) != packet.get("sha256"):
        errors.append("qualified packet hash differs")
    return errors


def write_qualified_bundle(
    run_root: Path,
    *,
    campaign_id: str,
    candidate_digest: str,
    capture: dict,
    packet: dict,
    packet_dir: Path,
    capture_dir: Path,
    target: dict,
    fields: list[dict],
    receipt_fields: dict,
) -> dict:
    """Persist the exact validated packet and receipt before temp cleanup.

    The bundle manifest hashes the packet, copied payloads/sidecars and the
    capture reference. The receipt binds the manifest; the manifest never
    hashes itself or the receipt.
    """
    session = str(capture.get("session") or "unknown")
    root = Path(run_root) / "qualified"
    final = root / f"{session}-{candidate_digest}"
    receipt_path = final / "qualification-receipt.json"
    if final.exists():
        errors = validate_qualified_bundle(
            final,
            expected_candidate_digest=candidate_digest,
            expected_checker_sha256=receipt_fields.get("checker_sha256"),
            expected_trusted_commit=receipt_fields.get("trusted_contract_commit"),
            expected_fields={
                str(row.get("name"))
                for row in fields
                if isinstance(row, dict) and row.get("state") == "validated"
            },
            require_packet_ready=bool(packet.get("packet_ready")),
        )
        if errors:
            raise CheckpointError(
                "existing qualified bundle does not match: " + "; ".join(errors[:6])
            )
        return {
            "bundle": str(final),
            "receipt": str(receipt_path),
            "receipt_sha256": _sha(receipt_path),
        }
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="qualified-", dir=str(root)))
    try:
        packet_root = temporary / "packet"
        packet_root.mkdir()
        packet_path = Path(packet_dir) / "draw-packet.json"
        if not packet_path.is_file():
            raise CheckpointError("candidate packet is missing")
        shutil.copy2(packet_path, packet_root / "draw-packet.json")
        source_payloads = Path(packet_dir) / "payloads"
        if source_payloads.is_dir():
            shutil.copytree(source_payloads, packet_root / "payloads")

        sidecars = _referenced_sidecars(packet, Path(packet_dir), Path(capture_dir))
        sidecar_dir = temporary / "sidecars"
        sidecar_dir.mkdir()
        for index, item in enumerate(sidecars):
            source = Path(item["source_path"])
            target_name = f"{index:03d}-{source.name}"
            shutil.copy2(source, sidecar_dir / target_name)
            item["bundle_path"] = f"sidecars/{target_name}"
        (temporary / "sidecar-map.json").write_text(
            _canonical({"schema": "eu4-gfx-checkpoint-sidecars/v1", "sidecars": sidecars}),
            encoding="utf-8",
        )
        reference = {
            "schema": CAPTURE_REFERENCE_SCHEMA,
            "capture_dir": str(Path(capture_dir).resolve()),
            "session": capture.get("session"),
            "pid": capture.get("pid"),
            "executable_sha256": capture.get("executable_sha256"),
            "library_sha256": capture.get("library_sha256"),
            "receipt_token": capture.get("receipt_token"),
            "capture_plan": capture.get("capture_plan"),
            "capture_plan_sha256": capture.get("capture_plan_sha256"),
        }
        (temporary / "capture-reference.json").write_text(
            _canonical(reference), encoding="utf-8"
        )
        files = []
        for path in sorted(temporary.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(temporary).as_posix()
            if rel in ("bundle-manifest.json", "qualification-receipt.json"):
                continue
            files.append(
                {
                    "path": rel,
                    "size": path.stat().st_size,
                    "sha256": _sha(path),
                }
            )
        manifest = {"schema": BUNDLE_SCHEMA, "files": files}
        manifest_path = temporary / "bundle-manifest.json"
        _write_json(manifest_path, manifest)
        receipt = dict(receipt_fields)
        receipt.update(
            {
                "schema": "eu4-gfx-qualification-receipt/v1",
                "bundle": str(final),
                "bundle_manifest_sha256": _sha(manifest_path),
                "packet": {
                    "path": "packet/draw-packet.json",
                    "sha256": _sha(packet_root / "draw-packet.json"),
                    "schema": packet.get("schema"),
                    "packet_ready": bool(packet.get("packet_ready")),
                },
                "capture": reference,
                "target": target,
                "fields": fields,
            }
        )
        _write_json(temporary / "qualification-receipt.json", receipt)
        os.replace(temporary, final)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "bundle": str(final),
        "receipt": str(final / "qualification-receipt.json"),
        "receipt_sha256": _sha(final / "qualification-receipt.json"),
    }


def validate_checkpoint(
    path: Path,
    *,
    expected_campaign_id: str | None = None,
    expected_candidate_base: str | None = None,
    expected_trusted_contract: str | None = None,
    expected_checker_sha256: str | None = None,
    expected_manifest_sha256: str | None = None,
    artifact_loader: Callable[[Path], Any] | None = None,
) -> list[str]:
    """Validate a checkpoint bundle; return human-readable errors."""
    path = Path(path)
    errors: list[str] = []
    if not path.is_file():
        return [f"checkpoint is missing: {path}"]
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"checkpoint is unreadable: {exc}"]
    if checkpoint.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    for key, expected in (
        ("campaign_id", expected_campaign_id),
        ("candidate_base_commit", expected_candidate_base),
        ("trusted_contract_commit", expected_trusted_contract),
        ("checker_sha256", expected_checker_sha256),
        ("manifest_sha256", expected_manifest_sha256),
    ):
        if expected is not None and checkpoint.get(key) != expected:
            errors.append(f"{key} mismatch")
    if checkpoint.get("checkpoint_kind") not in ("partial", "complete_provisional"):
        errors.append("checkpoint_kind is invalid")
    if not checkpoint.get("candidate_digest"):
        errors.append("candidate_digest is missing")
    if not checkpoint.get("observer_library_sha256"):
        errors.append("observer_library_sha256 is missing")
    bundle = path.parent
    manifest_path = bundle / "bundle-manifest.json"
    if not manifest_path.is_file():
        return errors + ["bundle manifest is missing"]
    if checkpoint.get("bundle_manifest_sha256") != _sha(manifest_path):
        errors.append("bundle manifest digest differs from the checkpoint")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return errors + [f"bundle manifest is unreadable: {exc}"]
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        errors.append("bundle manifest has no files")
    else:
        for item in files:
            if not isinstance(item, dict):
                errors.append("bundle manifest entry is not an object")
                continue
            rel = item.get("path")
            if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in Path(rel).parts:
                errors.append(f"bundle manifest path is invalid: {rel!r}")
                continue
            member = bundle / rel
            if not member.is_file():
                errors.append(f"bundle member is missing: {rel}")
                continue
            if member.stat().st_size != item.get("size"):
                errors.append(f"bundle member size differs: {rel}")
            if _sha(member) != item.get("sha256"):
                errors.append(f"bundle member hash differs: {rel}")
    packet = checkpoint.get("packet")
    packet_value = None
    if not isinstance(packet, dict):
        errors.append("packet identity is missing")
    else:
        member = bundle / str(packet.get("path") or "")
        if not member.is_file():
            errors.append("checkpoint packet is missing from the bundle")
        elif _sha(member) != packet.get("sha256"):
            errors.append("checkpoint packet hash differs")
        else:
            try:
                packet_value = json.loads(member.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(f"checkpoint packet is unreadable: {exc}")
    reference = checkpoint.get("capture")
    if not isinstance(reference, dict):
        errors.append("capture reference is missing")
    else:
        capture_dir = Path(str(reference.get("capture_dir") or ""))
        if not capture_dir.is_dir():
            errors.append("original capture directory is missing")
        if not reference.get("session"):
            errors.append("capture session is missing")
        if not reference.get("library_sha256"):
            errors.append("capture observer library identity is missing")
    gaps = checkpoint.get("contract_gaps")
    if not isinstance(gaps, list) or not gaps:
        errors.append("checkpoint has no contract gaps")
    fields = checkpoint.get("fields")
    if not isinstance(fields, list) or not fields:
        errors.append("checkpoint has no fields")
    if isinstance(reference, dict) and isinstance(fields, list) and packet_value is not None:
        capture_dir = Path(str(reference.get("capture_dir") or ""))
        artifact = None
        if capture_dir.is_dir():
            try:
                if artifact_loader is not None:
                    artifact = artifact_loader(capture_dir)
                else:
                    artifact = trusted_capture.load_capture(
                        capture_dir, require_events=False
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"checkpoint capture cannot be revalidated: {exc}")
        if artifact is not None:
            target_id = (checkpoint.get("target") or {}).get("call_id")
            target = next(
                (row for row in artifact.operations if row.call_id == target_id),
                None,
            )
            if target is None or target.symbol != "_GfxDraw" or target.payload_bytes is None:
                errors.append("checkpoint target draw is not in the capture")
            else:
                try:
                    master = trusted_packet.parse_master(target.payload_bytes)
                except trusted_packet.PacketError as exc:
                    errors.append(f"checkpoint target master is invalid: {exc}")
                else:
                    ctx = {
                        "artifact": artifact,
                        "target": target,
                        "master": master,
                        "packet_dir": bundle / "packet",
                    }
                    for field in fields:
                        if not isinstance(field, dict) or field.get("state") != "provisional":
                            continue
                        name = field.get("name")
                        item = (packet_value.get("inputs") or {}).get(name) or {}
                        relevant, basis = evidence_relevant(
                            str(name), item.get("evidence") or [], ctx
                        )
                        if not relevant:
                            errors.append(
                                f"provisional field {name} has no field-relevant evidence: {basis}"
                            )
                        if field.get("evidence_relevant") is not True:
                            errors.append(
                                f"provisional field {name} is not marked evidence-relevant"
                            )
                        if field.get("evidence_basis") != basis:
                            errors.append(
                                f"provisional field {name} evidence basis differs"
                            )
    return errors
