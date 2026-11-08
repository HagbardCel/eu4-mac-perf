"""Separate-process runner for candidate contract validators.

Candidate validator code is never imported into the checker process. This
module runs in its own process, loads the candidate contract from the candidate
workspace, and evaluates it against a checkpoint's original evidence and the
independent reviewer's adversarial cases. This is qualification isolation, not
a security sandbox.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_draw_packet as trusted_packet  # noqa: E402
import eu4_gfx_op_capture as trusted_capture  # noqa: E402
from automation.gfx_checkpoint import validate_checkpoint  # noqa: E402
from automation.gfx_contracts import ContractContext, retained_versions  # noqa: E402
from automation.gfx_contracts.api import verify_static_span  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_candidate_validator(workspace: Path, version: str):
    workspace = Path(workspace)
    merged: dict = {}
    for current in retained_versions(version):
        path = workspace / "automation" / "gfx_contracts" / f"{current}.py"
        if not path.is_file():
            raise RuntimeError(f"candidate contract module is missing: {path}")
        spec = importlib.util.spec_from_file_location(
            f"candidate_contract_{current}", str(path)
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"candidate contract module {current} cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(workspace))
        try:
            spec.loader.exec_module(module)
        finally:
            try:
                sys.path.remove(str(workspace))
            except ValueError:
                pass
        validators = getattr(module, "VALIDATORS", None)
        if not isinstance(validators, dict):
            raise RuntimeError(f"candidate contract {current} has no VALIDATORS mapping")
        overlap = sorted(set(merged) & set(validators))
        if overlap:
            raise RuntimeError(
                f"candidate contract {current} redefines fields: " + ", ".join(overlap)
            )
        merged.update(validators)
    return merged


def _artifact_for(checkpoint: dict, loader=None):
    reference = checkpoint.get("capture") or {}
    capture_dir = Path(str(reference.get("capture_dir") or ""))
    if loader is not None:
        return loader(capture_dir)
    try:
        return trusted_capture.load_capture(capture_dir, require_events=False)
    except trusted_capture.CaptureIncomplete as exc:
        raise RuntimeError(f"checkpoint capture is incomplete: {exc.reason}") from exc


def _context(
    checkpoint: dict,
    bundle: Path,
    artifact,
    target_metadata: dict,
) -> tuple[ContractContext, dict]:
    packet = _json(bundle / "packet" / "draw-packet.json")
    target_id = (checkpoint.get("target") or {}).get("call_id")
    target = next(
        (row for row in artifact.operations if row.call_id == target_id),
        None,
    )
    if target is None or target.symbol != "_GfxDraw":
        raise RuntimeError("checkpoint target draw is not in the capture")
    if target.payload_bytes is None:
        raise RuntimeError("checkpoint target draw has no master payload")
    master = trusted_packet.parse_master(target.payload_bytes)
    return ContractContext(
        artifact=artifact,
        target=target,
        master=master,
        packet_dir=bundle / "packet",
        capture_dir=Path(str((checkpoint.get("capture") or {}).get("capture_dir") or "")),
        config={},
        repo=Path(ROOT),
        target_metadata=target_metadata,
    ), packet


def _resolve_evidence(
    evidence: list,
    *,
    artifact,
    target,
    packet_dir: Path,
    capture_dir: Path,
    base_ctx: ContractContext,
) -> tuple[dict[str, Path], str | None]:
    """Resolve forged evidence and require genuine original records.

    A case that references a nonexistent call id, mismatched payload or
    unverifiable static span is an invalid test, not a semantic rejection.
    """
    paths: dict[str, Path] = {}
    target_payload = target.payload_bytes
    for item in evidence:
        if not isinstance(item, dict):
            return {}, "forged evidence is not an object"
        kind = item.get("type")
        if kind == "draw_snapshot":
            if item.get("call_id") != target.call_id:
                return {}, "draw snapshot is not the selected draw"
            if target_payload is None:
                return {}, "selected draw has no captured payload"
            offset = item.get("offset", 0)
            length = item.get("length", len(target_payload))
            if type(offset) is not int or type(length) is not int \
                    or offset < 0 or length < 0 or offset + length > len(target_payload):
                return {}, "draw snapshot slice is invalid"
            payload = _resolve_path(str(item.get("payload", "")), [packet_dir, capture_dir])
            if payload is None:
                return {}, "draw snapshot payload is missing"
            data = payload.read_bytes()
            if item.get("sha256") and _sha(payload) != item["sha256"]:
                return {}, "draw snapshot payload hash differs"
            if item.get("size") is not None and len(data) != item["size"]:
                return {}, "draw snapshot payload size differs"
            if data != target_payload[offset:offset + length]:
                return {}, "draw snapshot differs from the selected draw"
            paths[str(item.get("payload"))] = payload
        elif kind == "resource_observation":
            record = next(
                (row for row in artifact.operations
                 if row.call_id == item.get("call_id")),
                None,
            )
            if record is None:
                return {}, "forged resource call is not in the capture"
            if record.symbol != item.get("symbol") \
                    or record.resource_id != item.get("resource_id"):
                return {}, "forged resource identity differs from the original record"
            if not record.fields_ok or record.payload_bytes is None:
                return {}, "forged resource record is not structurally valid"
            payload = _resolve_path(str(item.get("payload", "")), [packet_dir, capture_dir])
            if payload is None:
                return {}, "forged resource payload is missing"
            if item.get("sha256") and _sha(payload) != item["sha256"]:
                return {}, "forged resource payload hash differs"
            if item.get("size") is not None and payload.stat().st_size != item["size"]:
                return {}, "forged resource payload size differs"
            if payload.read_bytes() != record.payload_bytes:
                return {}, "forged resource payload differs from the original record"
            paths[str(item.get("payload"))] = payload
        elif kind == "draw_record":
            record = next(
                (row for row in artifact.operations
                 if row.call_id == item.get("call_id")),
                None,
            )
            if record is None or record.symbol != "_GfxDraw":
                return {}, "forged draw record is not a genuine draw"
        elif kind == "primitive_set":
            record = next(
                (row for row in artifact.operations
                 if row.call_id == item.get("call_id")),
                None,
            )
            if record is None or record.symbol != "_GfxSetPrimitiveType":
                return {}, "forged primitive record is not genuine"
            if record.call_id >= target.call_id:
                return {}, "forged primitive record does not precede the draw"
        elif kind == "static_table":
            rel = item.get("path")
            if not isinstance(rel, str) or not rel:
                return {}, "forged static table has no path"
            candidate = Path(rel)
            if candidate.is_absolute() or ".." in candidate.parts:
                return {}, "forged static table path is invalid"
            path = (ROOT / candidate).resolve()
            try:
                path.relative_to(ROOT.resolve())
            except ValueError:
                return {}, "forged static table path escapes the repository"
            if not path.is_file():
                return {}, "forged static table is missing"
            if item.get("sha256") and _sha(path) != item["sha256"]:
                return {}, "forged static table hash differs"
            paths[rel] = path
        elif kind == "static_reconstruction":
            error = verify_static_span(item, base_ctx)
            if error:
                return {}, error
        elif kind == "unresolved":
            return {}, "unresolved evidence cannot support a case"
        else:
            return {}, f"unsupported forged evidence type: {kind!r}"
    return paths, None


def _resolve_path(text: str, bases: list[Path]) -> Path | None:
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        return None
    for base in bases:
        path = (base.resolve() / candidate).resolve()
        try:
            path.relative_to(base.resolve())
        except ValueError:
            continue
        if path.is_file():
            return path
    return None


def run(
    workspace: Path,
    version: str,
    checkpoint_path: Path,
    *,
    cases_path: Path | None = None,
    artifact_loader=None,
) -> dict:
    workspace = Path(workspace).resolve()
    checkpoint_path = Path(checkpoint_path).resolve()
    errors = validate_checkpoint(checkpoint_path, artifact_loader=artifact_loader)
    if errors:
        return {
            "schema": "eu4-gfx-contract-run/v1",
            "ok": False,
            "errors": errors,
            "fields": [],
            "cases": [],
        }
    checkpoint = _json(checkpoint_path)
    bundle = checkpoint_path.parent
    try:
        validators = load_candidate_validator(workspace, version)
        artifact = _artifact_for(checkpoint, artifact_loader)
        target_metadata = _json(ROOT / "reverse_engineering" / "target.json")
        ctx, packet = _context(checkpoint, bundle, artifact, target_metadata)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        return {
            "schema": "eu4-gfx-contract-run/v1",
            "ok": False,
            "errors": [str(exc)],
            "fields": [],
            "cases": [],
        }
    inputs = packet.get("inputs") or {}
    fields = []
    for gap in checkpoint.get("contract_gaps") or []:
        name = gap.get("name")
        if not isinstance(name, str):
            continue
        validator = validators.get(name)
        if not callable(validator):
            fields.append({"field": name, "ok": False, "outcome": "validator_missing"})
            continue
        item = inputs.get(name) or {}
        value = item.get("value")
        if not isinstance(value, dict):
            fields.append({"field": name, "ok": False, "outcome": "positive_value_missing"})
            continue
        evidence = item.get("evidence") or []
        paths, structural = _resolve_evidence(
            evidence,
            artifact=ctx.artifact,
            target=ctx.target,
            packet_dir=ctx.packet_dir,
            capture_dir=ctx.capture_dir,
            base_ctx=ctx,
        )
        if structural:
            fields.append(
                {"field": name, "ok": False, "outcome": "invalid_evidence",
                 "error": structural}
            )
            continue
        field_ctx = replace(
            ctx,
            field_name=name,
            field_evidence=list(evidence),
            field_evidence_paths=paths,
            evidence_paths=paths,
        )
        try:
            error = validator(value, field_ctx)
        except Exception as exc:  # noqa: BLE001 - qualification report, not control flow
            fields.append(
                {"field": name, "ok": False, "outcome": "validator_error",
                 "error": str(exc)}
            )
            continue
        fields.append(
            {"field": name, "ok": error is None, "outcome": "positive",
             "error": error}
        )

    cases = []
    if cases_path is not None and Path(cases_path).is_file():
        try:
            case_doc = _json(Path(cases_path))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return {
                "schema": "eu4-gfx-contract-run/v1",
                "ok": False,
                "errors": [f"adversarial cases are unreadable: {exc}"],
                "fields": fields,
                "cases": [],
            }
        for case in case_doc.get("fields") or []:
            if not isinstance(case, dict):
                continue
            name = case.get("field")
            validator = validators.get(name)
            item = inputs.get(name) or {}
            forged_value = case.get("forged_value", item.get("value"))
            forged_evidence = case.get("forged_evidence", item.get("evidence"))
            if not callable(validator):
                cases.append(
                    {"field": name, "ok": False, "outcome": "validator_missing"}
                )
                continue
            paths, structural = _resolve_evidence(
                forged_evidence or [],
                artifact=ctx.artifact,
                target=ctx.target,
                packet_dir=ctx.packet_dir,
                capture_dir=ctx.capture_dir,
                base_ctx=ctx,
            )
            if structural:
                cases.append(
                    {"field": name, "ok": False, "outcome": "invalid_test",
                     "error": structural}
                )
                continue
            case_ctx = replace(
                ctx,
                field_name=name,
                field_evidence=list(forged_evidence or []),
                field_evidence_paths=paths,
                evidence_paths=paths,
            )
            try:
                error = validator(forged_value, case_ctx)
            except Exception as exc:  # noqa: BLE001
                cases.append(
                    {"field": name, "ok": False, "outcome": "validator_error",
                     "error": str(exc)}
                )
                continue
            expected = str(case.get("expected_error_prefix") or "")
            if error and (not expected or str(error).startswith(expected)):
                outcome = "semantic_rejection"
                ok = True
            elif error:
                outcome = "wrong_rejection"
                ok = False
            else:
                outcome = "false_pass"
                ok = False
            cases.append(
                {
                    "field": name,
                    "mutation_class": case.get("mutation_class"),
                    "ok": ok,
                    "outcome": outcome,
                    "error": error,
                    "expected_error_prefix": expected,
                }
            )
    positive_ok = bool(fields) and all(row["ok"] for row in fields)
    negative_ok = bool(cases) and all(row["ok"] for row in cases)
    ok = positive_ok and negative_ok
    return {
        "schema": "eu4-gfx-contract-run/v1",
        "version": version,
        "checkpoint_sha256": _sha(checkpoint_path),
        "ok": ok,
        "positive_ok": positive_ok,
        "negative_ok": negative_ok,
        "validator_fields": sorted(validators),
        "fields": fields,
        "cases": cases,
        "errors": [],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--cases")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        result = run(
            Path(args.workspace),
            args.version,
            Path(args.checkpoint),
            cases_path=Path(args.cases) if args.cases else None,
        )
    except (OSError, RuntimeError) as exc:
        result = {
            "schema": "eu4-gfx-contract-run/v1",
            "ok": False,
            "errors": [str(exc)],
            "fields": [],
            "cases": [],
        }
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
