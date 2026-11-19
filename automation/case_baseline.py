"""Identity-bound reuse of an already-reproduced diagnostic case.

M7-P1 proved that a verified case and its replay baseline can be reused instead
of recaptured for every hypothesis.  This module makes that reuse decision
explicit and fail-closed.  It is deliberately *not* a cache: it records one
identity for one existing baseline result and compares a later request against
it.  A mismatch invalidates the baseline and the caller must recompute.

The identity is only produced for a case that passes the existing portable
verifier.  In addition, every declared payload is re-hashed from disk (a
manifest entry is not evidence that the bytes still match), and the identity
covers the actual renderer dependency closure, the replay binary/source,
translated shaders, the comparison policy and the platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

from automation import gfx_replay_case, replay_host_dependencies

SCHEMA = "gfx-case-baseline/v1"
IDENTITY_SCHEMA = "gfx-case-identity/v1"
ROOT = Path(__file__).resolve().parents[1]

# The renderer build closure is owned by replay_host_dependencies so the
# incremental build and the baseline identity cannot drift apart.
RENDERER_ROOTS = replay_host_dependencies.REPLAY_SOURCES
# Non-header implementation that participates in the comparison or replay.
IMPLEMENTATION_SOURCES = (
    "automation/m6_frame.py",
    "automation/gfx_independent_replay.py",
    "automation/gfx_frame_compare.py",
    "automation/gfx_frame_graph.py",
    "automation/gfx_shader_translate.py",
    "automation/gfx_replay_case.py",
)
REPLAY_BINARY = "benchmark/.build/gfx_frame_replay"
class BaselineError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _relative(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def _renderer_closure(roots=None) -> dict[str, str]:
    """Hash the same renderer closure the replay-host build uses."""
    sources = [ROOT / name for name in (roots or RENDERER_ROOTS)]
    return replay_host_dependencies.relative_dependencies(ROOT, sources)


def _implementation_sources() -> dict[str, str]:
    return {name: _sha256(ROOT / name) or "" for name in IMPLEMENTATION_SOURCES}


def _inventory_digest(manifest: dict) -> tuple[str, int]:
    rows = []
    for item in manifest.get("files") or []:
        if isinstance(item, dict):
            rows.append([item.get("path"), item.get("bytes"),
                         item.get("sha256")])
    rows.sort(key=lambda row: str(row[0]))
    return hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), len(rows)


def _shader_inputs(manifest: dict) -> dict[str, str]:
    rows = {}
    for item in manifest.get("files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path.startswith("shaders/"):
            rows[path] = str(item.get("sha256") or "")
    return dict(sorted(rows.items()))


def _payload_count(manifest: dict) -> int:
    """Cheap inventory presence check; verify() owns the content hashing."""
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise BaselineError("payload_inventory",
                            "case.json has no payload inventory")
    return len(files)


_PAYLOAD_PROBLEM_PREFIXES = (
    "missing ",
    "digest drift ",
    "size drift ",
    "duplicate manifest path",
    "nonportable manifest path",
    "manifest path escapes",
)


def _current_producer() -> dict:
    """Identity of the candidate replay host that should produce this result."""
    from automation import m6_frame

    identity = replay_host_dependencies.build_identity()
    host = m6_frame.REPLAY_HOST
    binary_sha = _sha256(host)
    if not binary_sha:
        raise BaselineError(
            "producer_binary_missing",
            f"the candidate replay host is not built: {host}")
    stamp = _json(Path(str(host) + ".deps.json"))
    if (stamp.get("fingerprint") != identity["fingerprint"]
            or stamp.get("binary_sha256") != binary_sha):
        raise BaselineError(
            "producer_build_stale",
            "the replay-host build receipt does not match the current source "
            "closure and binary")
    return {
        "executable": str(host),
        "executable_sha256": binary_sha,
        "build_fingerprint": identity["fingerprint"],
        "source_revision": stamp.get("source_revision"),
    }


def current_producer() -> dict:
    """Identity of the candidate replay host that should produce a result.

    Public entry point for consumers (correction acceptance) that must bind a
    candidate artifact to the build it claims to come from.
    """
    return _current_producer()


def _verify_case(case_dir: Path) -> dict:
    """Run the single authoritative verifier, including payload hashing.

    ``gfx_replay_case.verify()`` re-hashes every declared payload from disk.
    The baseline must not repeat that pass; it only classifies a payload-only
    failure so callers still get the precise error kind.
    """
    try:
        value = gfx_replay_case.verify(case_dir)
    except Exception as exc:  # noqa: BLE001 - every verifier failure is closed
        raise BaselineError(
            "case_verification", f"{type(exc).__name__}: {exc}") from exc
    if value.get("ok") is not True:
        problems = [str(item) for item in
                    (value.get("problems") or ["unknown verification failure"])]
        payload_only = bool(problems) and all(
            problem.startswith(_PAYLOAD_PROBLEM_PREFIXES)
            for problem in problems)
        raise BaselineError(
            "payload_validation" if payload_only else "case_verification",
            "; ".join(problems[:8]))
    if value.get("independently_reproducible") is not True:
        raise BaselineError(
            "case_not_reproducible",
            "the case verifies but is not independently reproducible")
    producer = value.get("producer_identity") if isinstance(
        value.get("producer_identity"), dict) else {}
    if producer.get("status") == "legacy_missing":
        raise BaselineError(
            "producer_identity_missing",
            "the case has no replay producer receipt; it remains historical "
            "evidence but cannot be reused as a result from the current renderer")
    if producer.get("status") != "verified":
        raise BaselineError(
            "producer_identity_invalid",
            "; ".join(str(item) for item in
                      (producer.get("problems") or ["unknown producer problem"])[:8]))
    return value


def _replay_evidence(replay: Path) -> dict:
    result = _json(replay / "replay.json")
    if not result:
        raise BaselineError("replay_missing",
                            f"no readable {replay / 'replay.json'}")
    from automation import gfx_independent_replay

    completion = gfx_independent_replay.full_frame_completion_problems(result)
    if completion:
        raise BaselineError(
            "replay_incomplete",
            "the replay is not a complete full-frame execution: "
            + "; ".join(completion))
    metal = replay / "metal-frame.bgra"
    if not metal.is_file() or metal.stat().st_size == 0:
        raise BaselineError("replay_missing", f"no Metal readback at {metal}")
    return result


def identity(case_dir: Path | str, *,
             backend: str = "metal",
             policy: Path | str | None = None,
             replay_dir: Path | str | None = None) -> dict:
    """Compute the complete identity of one already-produced, verified baseline."""
    case_dir = Path(case_dir).expanduser().resolve()
    manifest = _json(case_dir / "case.json")
    if not manifest:
        raise BaselineError("missing_manifest", str(case_dir / "case.json"))
    payload_count = _payload_count(manifest)
    verification = _verify_case(case_dir)
    receipt = (verification.get("producer_identity") or {}).get("receipt") or {}
    recorded_producer = receipt.get("producer") or {}
    current_producer = _current_producer()
    for field in ("executable_sha256", "build_fingerprint"):
        if recorded_producer.get(field) != current_producer.get(field):
            raise BaselineError(
                "producer_mismatch",
                f"the replay result was produced by a different replay host "
                f"({field} differs); recompute it with the current renderer")
    seal = _json(case_dir / "capture" / "frame-seal.json")
    if not all(seal.get(key) for key in (
            "session_id", "capture_frame_id", "event_stream_sha256")):
        raise BaselineError(
            "capture_identity",
            "the case has no sealed capture identity "
            "(session_id/capture_frame_id/event_stream_sha256)")
    replay = (Path(replay_dir).expanduser().resolve()
              if replay_dir is not None else case_dir / "replay")
    expected_replay = (case_dir / "replay").resolve()
    if replay != expected_replay:
        # The producer receipt and capture binding live with the packaged
        # replay.  Accepting an arbitrary external directory would decouple the
        # verified case from the result being reused, so reject it rather than
        # inventing a second provenance system.
        raise BaselineError(
            "external_replay_override",
            f"replay directory {replay} is not the verified case replay "
            f"{expected_replay}; external replay overrides are not supported")
    replay_result = _replay_evidence(replay)
    report_path = case_dir / str(
        manifest.get("diagnostic_report") or "diagnostic-report.json")
    graph_path = case_dir / str(manifest.get("graph") or "graph.json")
    policy_path = (Path(policy).expanduser().resolve() if policy is not None
                   else ROOT / "analysis" / "m6" / "comparison-policy.json")
    binary = ROOT / REPLAY_BINARY
    inventory_digest, _count = _inventory_digest(manifest)
    values = {
        "schema": IDENTITY_SCHEMA,
        "case_id": manifest.get("case_id"),
        "case_schema": manifest.get("schema"),
        "backend": backend,
        "validity": {
            "verified": True,
            "verification_ok": verification.get("ok"),
            "independently_reproducible": verification.get(
                "independently_reproducible"),
            "graph_status": verification.get("graph_status"),
            "payload_validation": "ok",
            "payload_count": payload_count,
            "replay_status": replay_result.get("status"),
            "replay_stage": replay_result.get("stage"),
            "replay_readback": replay_result.get("readback"),
            "producer_identity": "verified",
        },
        "producer": {
            "executable_sha256": recorded_producer.get("executable_sha256"),
            "build_fingerprint": recorded_producer.get("build_fingerprint"),
            "current_executable_sha256": current_producer.get("executable_sha256"),
            "current_build_fingerprint": current_producer.get("build_fingerprint"),
        },
        "case_manifest_sha256": _sha256(case_dir / "case.json"),
        "seal_sha256": _sha256(case_dir / "capture" / "frame-seal.json"),
        "diagnostic_report_sha256": _sha256(report_path),
        "graph_sha256": _sha256(graph_path),
        "replay_result_sha256": _sha256(replay / "replay.json"),
        "metal_frame_sha256": _sha256(replay / "metal-frame.bgra"),
        "shader_inputs": _shader_inputs(manifest),
        "payload_inventory_sha256": inventory_digest,
        "payload_count": payload_count,
        "policy_sha256": _sha256(policy_path),
        "renderer_sources": _renderer_closure(),
        "implementation_sources": _implementation_sources(),
        "renderer_binary": {
            "path": REPLAY_BINARY,
            "sha256": _sha256(binary),
        },
        "comparison": {
            "backend": backend,
            "policy_sha256": _sha256(policy_path),
            "diagnostic_report_sha256": _sha256(report_path),
            "graph_sha256": _sha256(graph_path),
            "replay_result_sha256": _sha256(replay / "replay.json"),
            "metal_frame_sha256": _sha256(replay / "metal-frame.bgra"),
        },
        "environment": {
            "python": list(sys.version_info[:3]),
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "release": platform.release(),
            "mac_version": platform.mac_ver()[0] or None,
        },
        "capture": {
            key: seal.get(key) for key in
            ("session_id", "capture_frame_id", "event_stream_sha256",
             "present_index", "scene_index")
        },
    }
    values["fingerprint"] = hashlib.sha256(json.dumps(
        values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values


def _result_summary(case_dir: Path, manifest: dict | None = None) -> dict:
    manifest = manifest or _json(case_dir / "case.json")
    name = str(manifest.get("diagnostic_report")
               or "diagnostic-report.json")
    report = _json(case_dir / name)
    qualification = report.get("qualification") or {}
    final = ((report.get("pixels") or {}).get("final") or {})
    divergence = report.get("first_divergence") or {}
    return {
        "classification": report.get("classification"),
        "qualified": bool(qualification.get("qualified")),
        "diagnostic_qualified": bool(
            qualification.get("diagnostic_qualified")),
        "first_divergence": {
            key: divergence.get(key) for key in
            ("event", "resource", "target", "producer_event", "confidence")
        },
        "final": final.get("statistics") or final,
    }


def record(case_dir: Path | str, out: Path | str, *,
           backend: str = "metal", policy: Path | str | None = None,
           replay_dir: Path | str | None = None) -> dict:
    """Record one reusable baseline identity beside (not inside) its case."""
    case_dir = Path(case_dir).expanduser().resolve()
    value = {
        "schema": SCHEMA,
        "identity": identity(case_dir, backend=backend, policy=policy,
                             replay_dir=replay_dir),
        "result": _result_summary(case_dir),
    }
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n",
                         encoding="utf-8")
    os.replace(temporary, out)
    return value


def evaluate(current: dict, baseline: dict) -> dict:
    """Decide whether a stored baseline may be reused for this case.

    A matching fingerprint is necessary but not sufficient: the current case
    must prove it was verified with valid on-disk payloads.  An invalid current
    identity is never reusable even if a caller supplies a matching stored
    fingerprint.
    """
    problems: list[dict] = []
    if not isinstance(baseline, dict) or baseline.get("schema") != SCHEMA:
        problems.append({"code": "baseline_schema",
                         "detail": f"baseline schema must be {SCHEMA}"})
        stored = {}
    else:
        stored = baseline.get("identity") or {}
    current_validity = current.get("validity")
    if not isinstance(current_validity, dict) or current_validity.get(
            "verified") is not True:
        problems.append({"code": "current_not_verified",
                         "detail": "the current case has not passed the "
                                   "portable verifier"})
    elif current_validity.get("payload_validation") != "ok":
        problems.append({"code": "current_payloads_invalid",
                         "detail": "the current case's declared payloads were "
                                   "not validated from disk"})
    elif current_validity.get("replay_status") != "ok":
        problems.append({"code": "current_replay_incomplete",
                         "detail": "the current replay baseline is incomplete"})
    elif current_validity.get("producer_identity") != "verified":
        problems.append({"code": "current_producer_missing",
                         "detail": "the current result is not bound to a "
                                   "matching replay producer"})
    stored_validity = stored.get("validity")
    if not isinstance(stored_validity, dict) or stored_validity.get(
            "verified") is not True:
        problems.append({"code": "baseline_not_verified",
                         "detail": "the baseline was not recorded from a "
                                   "verified case"})
    elif stored_validity.get("payload_validation") != "ok":
        problems.append({"code": "baseline_payloads_unvalidated",
                         "detail": "the baseline does not record on-disk "
                                   "payload validation"})
    elif stored_validity.get("replay_status") != "ok":
        problems.append({"code": "baseline_replay_incomplete",
                         "detail": "the baseline does not record a completed "
                                   "replay"})
    current_fingerprint = current.get("fingerprint")
    if not isinstance(current_fingerprint, str) or not current_fingerprint:
        problems.append({"code": "current_identity_missing",
                         "detail": "the current case has no identity"})
    if stored.get("fingerprint") != current_fingerprint:
        problems.append({"code": "baseline_mismatch",
                         "detail": "the baseline was produced from different "
                                   "case, backend, configuration, shader or "
                                   "renderer inputs"})
    if stored.get("backend") != current.get("backend"):
        problems.append({"code": "baseline_backend",
                         "detail": "the baseline backend differs"})
    reusable = not problems
    return {
        "schema": "gfx-case-baseline-reuse/v1",
        "ok": reusable,
        "reusable": reusable,
        "current_fingerprint": current_fingerprint,
        "baseline_fingerprint": stored.get("fingerprint"),
        "backend": current.get("backend"),
        "problems": problems,
        "problems_count": len(problems),
        "reason": ("the complete verified identity matches; reuse the "
                   "existing baseline result" if reusable
                   else "recompute the affected candidate result"),
    }


def load_check(case_dir: Path | str, baseline_path: Path | str, *,
               backend: str = "metal", policy: Path | str | None = None,
               replay_dir: Path | str | None = None) -> dict:
    baseline = _json(Path(baseline_path).expanduser())
    if not baseline:
        raise BaselineError("missing_baseline", str(baseline_path))
    current = identity(case_dir, backend=backend, policy=policy,
                       replay_dir=replay_dir)
    result = evaluate(current, baseline)
    stored = baseline.get("identity") or {}
    result["baseline"] = {
        "schema": baseline.get("schema"),
        "fingerprint": stored.get("fingerprint"),
        "backend": stored.get("backend"),
        "capture": stored.get("capture"),
        "validity": stored.get("validity"),
        "renderer_binary": stored.get("renderer_binary"),
        "policy_sha256": stored.get("policy_sha256"),
        "result": baseline.get("result"),
    }
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("record", "check"):
        command = sub.add_parser(name)
        command.add_argument("--case", required=True, type=Path)
        command.add_argument("--baseline", required=True, type=Path)
        command.add_argument("--backend", default="metal")
        command.add_argument("--policy", type=Path, default=None)
        command.add_argument("--replay-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "record":
            result = record(args.case, args.baseline,
                            backend=args.backend, policy=args.policy,
                            replay_dir=args.replay_dir)
            status = 0
        else:
            result = load_check(args.case, args.baseline,
                                backend=args.backend, policy=args.policy,
                                replay_dir=args.replay_dir)
            status = 0 if result["ok"] else 2
    except (BaselineError, OSError, ValueError) as exc:
        print(json.dumps({"schema": SCHEMA, "ok": False,
                          "reason": f"{type(exc).__name__}: {exc}"},
                         indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
