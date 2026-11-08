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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_draw_packet as trusted_packet  # noqa: E402
import eu4_gfx_op_capture as trusted_capture  # noqa: E402
from automation.gfx_checkpoint import validate_checkpoint  # noqa: E402
from automation.gfx_contracts import ContractContext  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_candidate_validator(workspace: Path, version: str):
    path = Path(workspace) / "automation" / "gfx_contracts" / f"{version}.py"
    if not path.is_file():
        raise RuntimeError(f"candidate contract module is missing: {path}")
    spec = importlib.util.spec_from_file_location(
        f"candidate_contract_{version}", str(path)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate contract module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(Path(workspace)))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(Path(workspace)))
        except ValueError:
            pass
    validators = getattr(module, "VALIDATORS", None)
    if not isinstance(validators, dict):
        raise RuntimeError("candidate contract has no VALIDATORS mapping")
    return validators


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


def _structurally_valid(
    evidence: list, packet_dir: Path, capture_dir: Path
) -> str | None:
    """A forged case must remain structurally admissible."""
    for item in evidence:
        if not isinstance(item, dict):
            return "forged evidence is not an object"
        kind = item.get("type")
        if kind not in {
            "draw_snapshot", "resource_observation", "draw_record",
            "primitive_set", "static_table", "static_reconstruction",
        }:
            return f"forged evidence type is unsupported: {kind!r}"
        payload = item.get("payload")
        if isinstance(payload, str) and payload:
            candidate = Path(payload)
            if candidate.is_absolute() or ".." in candidate.parts:
                return "forged evidence payload path is invalid"
            found = None
            for base in (packet_dir, capture_dir):
                path = (base / candidate).resolve()
                try:
                    path.relative_to(base.resolve())
                except ValueError:
                    continue
                if path.is_file():
                    found = path
                    break
            if found is None:
                return "forged evidence payload is missing"
            if item.get("sha256") and _sha(found) != item["sha256"]:
                return "forged evidence payload hash differs"
            if item.get("size") is not None and found.stat().st_size != item["size"]:
                return "forged evidence payload size differs"
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
    errors = validate_checkpoint(checkpoint_path)
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
            fields.append({"field": name, "ok": False, "error": "validator is missing"})
            continue
        item = inputs.get(name) or {}
        value = item.get("value")
        if not isinstance(value, dict):
            fields.append({"field": name, "ok": False, "error": "positive value is missing"})
            continue
        try:
            error = validator(value, ctx)
        except Exception as exc:  # noqa: BLE001 - qualification report, not control flow
            fields.append({"field": name, "ok": False, "error": f"validator_error: {exc}"})
            continue
        fields.append(
            {"field": name, "ok": error is None, "error": error}
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
                    {"field": name, "ok": False, "error": "validator is missing"}
                )
                continue
            structural = _structurally_valid(
                forged_evidence or [], bundle / "packet", ctx.capture_dir
            )
            if structural:
                cases.append(
                    {"field": name, "ok": False,
                     "error": f"case is not structurally admissible: {structural}"}
                )
                continue
            forged = dict(item)
            forged["value"] = forged_value
            forged["evidence"] = forged_evidence
            try:
                error = validator(forged_value, ctx)
            except Exception as exc:  # noqa: BLE001
                cases.append(
                    {"field": name, "ok": False, "error": f"validator_error: {exc}"}
                )
                continue
            expected = str(case.get("expected_error_prefix") or "")
            ok = bool(error) and (not expected or str(error).startswith(expected))
            cases.append(
                {
                    "field": name,
                    "mutation_class": case.get("mutation_class"),
                    "ok": ok,
                    "error": error,
                    "expected_error_prefix": expected,
                }
            )
    ok = all(row["ok"] for row in fields) and all(row["ok"] for row in cases)
    return {
        "schema": "eu4-gfx-contract-run/v1",
        "version": version,
        "checkpoint_sha256": _sha(checkpoint_path),
        "ok": ok,
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
