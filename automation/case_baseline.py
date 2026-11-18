"""Identity-bound reuse of an already-reproduced diagnostic case.

M7-P1 proved that a verified case and its replay baseline can be reused instead
of recaptured for every hypothesis.  This module makes that reuse decision
explicit and fail-closed.  It is deliberately *not* a cache: it records one
identity for one existing baseline result and compares a later request against
it.  A mismatch invalidates the baseline and the caller must recompute.

The identity covers the inputs and dependencies that can change a comparison:

* the case manifest and every declared payload (the manifest is the inventory);
* the frame seal / capture identity;
* the diagnostic report and graph;
* the replay result and Metal readback (the backend result being reused);
* the translated shader payloads;
* the renderer implementation sources and the comparison configuration;
* the interpreter/platform environment that produced the result.

No result is reused merely because the case id or scene selection matches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path

SCHEMA = "gfx-case-baseline/v1"
IDENTITY_SCHEMA = "gfx-case-identity/v1"
ROOT = Path(__file__).resolve().parents[1]
RENDERER_SOURCES = (
    "benchmark/eu4_gfx_frame_replay.mm",
    "benchmark/eu4_gfx_frame_replay.h",
    "benchmark/eu4_gfx_frame_trace.c",
    "benchmark/eu4_gfx_frame_trace.h",
)


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


def _inventory_digest(manifest: dict) -> tuple[str, int]:
    """Digest the declared (path, bytes, sha256) inventory deterministically."""
    rows = []
    for item in manifest.get("files") or []:
        if isinstance(item, dict):
            rows.append([item.get("path"), item.get("bytes"),
                         item.get("sha256")])
    rows.sort(key=lambda row: str(row[0]))
    return hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), len(rows)


def _shader_digest(manifest: dict) -> str:
    rows = [[item.get("path"), item.get("sha256")]
            for item in manifest.get("files") or []
            if isinstance(item, dict)
            and str(item.get("path") or "").startswith("shaders/")]
    rows.sort(key=lambda row: str(row[0]))
    return hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def identity(case_dir: Path | str, *,
             backend: str = "metal",
             policy: Path | str | None = None,
             replay_dir: Path | str | None = None) -> dict:
    """Compute the complete identity of one already-produced baseline."""
    case_dir = Path(case_dir).expanduser().resolve()
    manifest = _json(case_dir / "case.json")
    if not manifest:
        raise BaselineError("missing_manifest", str(case_dir / "case.json"))
    replay = (Path(replay_dir).expanduser().resolve()
              if replay_dir is not None else case_dir / "replay")
    report_path = case_dir / str(
        manifest.get("diagnostic_report") or "diagnostic-report.json")
    graph_path = case_dir / str(manifest.get("graph") or "graph.json")
    policy_path = (Path(policy).expanduser().resolve() if policy is not None
                   else ROOT / "analysis" / "m6" / "comparison-policy.json")
    renderer = {
        name: _sha256(ROOT / name) for name in RENDERER_SOURCES
    }
    inventory_digest, payload_count = _inventory_digest(manifest)
    values = {
        "schema": IDENTITY_SCHEMA,
        "case_id": manifest.get("case_id"),
        "case_schema": manifest.get("schema"),
        "backend": backend,
        "case_manifest_sha256": _sha256(case_dir / "case.json"),
        "seal_sha256": _sha256(case_dir / "capture" / "frame-seal.json"),
        "diagnostic_report_sha256": _sha256(report_path),
        "graph_sha256": _sha256(graph_path),
        "replay_result_sha256": _sha256(replay / "replay.json"),
        "metal_frame_sha256": _sha256(replay / "metal-frame.bgra"),
        "shader_inventory_sha256": _shader_digest(manifest),
        "payload_inventory_sha256": inventory_digest,
        "payload_count": payload_count,
        "policy_sha256": _sha256(policy_path),
        "renderer_sources": renderer,
        "environment": {
            "python": list(sys.version_info[:3]),
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
        },
        "capture": {
            key: _json(case_dir / "capture" / "frame-seal.json").get(key)
            for key in ("session_id", "capture_frame_id",
                        "event_stream_sha256", "present_index",
                        "scene_index")
        },
    }
    values["fingerprint"] = hashlib.sha256(json.dumps(
        values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return values


def _result_summary(case_dir: Path) -> dict:
    report = _json(case_dir / "diagnostic-report.json")
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
    """Decide whether a stored baseline may be reused for this case."""
    problems: list[dict] = []
    if not isinstance(baseline, dict) or baseline.get("schema") != SCHEMA:
        problems.append({"code": "baseline_schema",
                         "detail": f"baseline schema must be {SCHEMA}"})
        stored = {}
    else:
        stored = baseline.get("identity") or {}
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
        "reason": ("the complete identity matches; reuse the existing "
                   "baseline result" if reusable
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
    result["baseline"] = baseline
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
