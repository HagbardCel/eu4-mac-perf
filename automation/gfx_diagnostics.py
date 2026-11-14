"""Capture-validity and GPU-execution health for EU4 graphics diagnostics.

The audit in ``analysis/infra/diagnostic-integrity-audit.md`` found that an
artifact existing on disk was repeatedly treated as evidence that rendering had
succeeded.  This module makes the two questions explicit and separate:

* **capture validity** -- is this artifact a trustworthy observation of the
  operation it claims to describe?  It is ``valid``, ``invalid`` or
  ``unverified``.  A missing or contradictory field is never ``valid``.
* **GPU execution health** -- did the API accept and complete the work?  A
  completed command buffer proves submission/execution, not per-draw pixel
  correctness.

The functions are pure and dependency-free so they can be used by the CLI, the
dual-process coordinator and native harness tests alike.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

VALID = "valid"
INVALID = "invalid"
UNVERIFIED = "unverified"
VALIDITY = (VALID, INVALID, UNVERIFIED)

CAPTURE_SCHEMA = "gfx-capture-validity/v1"
HEALTH_SCHEMA = "gfx-gpu-health/v1"
HEALTHY = "healthy"
DEGRADED = "degraded"
FAILED = "failed"
UNKNOWN = "unknown"

# Classes whose presence makes a requested rendering result invalid.  The
# classes are deliberately about execution, not about pixels: a successful
# command buffer can still carry a refused draw or a pipeline creation error.
BLOCKING_CLASSES = frozenset(
    {
        "draw_refused",
        "shader_pipeline",
        "command_buffer",
        "validation",
        "resolve",
        "readback",
        "render_target",
    }
)
# A configuration/instrumentation failure invalidates evidence but is reported
# separately so it is never mistaken for a renderer defect.
INSTRUMENTATION_CLASSES = frozenset({"instrumentation", "capture", "provenance"})
DRAW_FAILURE_REASONS = frozenset({"unsupported", "failed", "gpu_failed"})


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path, *, limit: int = 20000) -> list[dict]:
    rows: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as source:
            for number, line in enumerate(source):
                if number >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except (OSError, UnicodeError):
        return rows
    return rows


def _int(value: Any) -> int | None:
    return value if type(value) is int else None


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if type(value) is int:
        return value != 0
    return None


def validity(
    status: str,
    *,
    reasons: Iterable[str] = (),
    evidence: dict | None = None,
) -> dict:
    """Build one capture-validity record.  ``status`` must be one of VALIDITY."""
    if status not in VALIDITY:
        raise ValueError(f"unknown capture validity: {status!r}")
    return {
        "schema": CAPTURE_SCHEMA,
        "status": status,
        "reasons": list(reasons),
        "evidence": evidence or {},
    }


def _validity_from_checks(checks: list[tuple[str, bool | None]]) -> dict:
    """Three-valued conjunction: a single explicit failure is invalid; any
    unknown with no failure is unverified; all true is valid."""
    reasons: list[str] = []
    unknown: list[str] = []
    for name, value in checks:
        if value is False:
            reasons.append(f"{name}:false")
        elif value is None:
            unknown.append(name)
    if reasons:
        return validity(INVALID, reasons=reasons + [f"{name}:unverified" for name in unknown])
    if unknown:
        return validity(UNVERIFIED, reasons=[f"{name}:missing" for name in unknown])
    return validity(VALID)


def _payload_matches(record: dict, base: Path | None) -> tuple[bool | None, str | None]:
    """Check a declared raw capture against its size and digest.

    Returns ``(None, reason)`` when the declaration is absent, ``(False, reason)``
    when it is contradicted, and ``(True, None)`` when it verifies.
    """
    capture = record.get("capture")
    if not isinstance(capture, dict):
        return None, "capture block is absent"
    file_name = capture.get("raw_file")
    size = _int(capture.get("raw_bytes"))
    digest = capture.get("raw_sha256")
    if not isinstance(file_name, str) or not file_name:
        return None, "raw_file is absent"
    if size is None or not isinstance(digest, str) or len(digest) != 64:
        return False, "raw_bytes/raw_sha256 declaration is incomplete"
    if base is None:
        return None, "capture directory is unavailable"
    path = base / file_name
    try:
        payload = path.read_bytes()
    except OSError:
        return False, "raw capture file is unreadable"
    if len(payload) != size:
        return False, "raw capture size does not match its declaration"
    if _sha256(payload) != digest:
        return False, "raw capture digest does not match its declaration"
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    width, height = _int(target.get("width")), _int(target.get("height"))
    if width is not None and height is not None and width > 0 and height > 0:
        if size != width * height * 4:
            return False, "raw capture is not tightly packed BGRA8 for its extent"
    return True, None


def assess_probe(result: dict, *, base: Path | None = None) -> dict:
    """Assess a ``probe-result.json``-shaped record.

    A probe may legitimately stop at a draw without presenting; that is
    ``unverified`` for a presentation request, not ``valid``.  A record that
    claims ``overall: captured`` while its status flags or payload disagree is
    ``invalid``.
    """
    if not isinstance(result, dict):
        return validity(INVALID, reasons=["result is not an object"])
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    overall = result.get("overall")
    checks: list[tuple[str, bool | None]] = [
        ("overall_present", bool(overall)),
        ("draw_reached", _flag(status.get("draw_reached"))),
        ("draw_encoded", _flag(status.get("draw_encoded"))),
        ("gpu_completed", _flag(status.get("gpu_completed"))),
        ("pixels_captured", _flag(status.get("pixels_captured"))),
    ]
    artifact = result.get("artifacts")
    checkpoint = artifact.get("checkpoint") if isinstance(artifact, dict) else None
    payload_ok: bool | None = None
    payload_reason: str | None = None
    if isinstance(checkpoint, dict):
        checks.append(("capture_valid", assess_checkpoint(checkpoint, base=base)["status"] == VALID))
        payload_ok, payload_reason = _payload_matches(checkpoint, base)
        checks.append(("payload_matches", payload_ok))
    elif overall == "captured":
        # A record that claims a capture without a capture artifact is contradicted,
        # not merely unverified.
        checks.append(("capture_artifact", False))
    elif overall == "presented":
        # Presentation is observable, but without a capture artifact the pixels
        # remain unverified.
        checks.append(("capture_artifact", None))
    record = _validity_from_checks(checks)
    extra = [payload_reason] if payload_reason else []
    if overall not in (None, "captured", "presented") and record["status"] == VALID:
        record = validity(INVALID, reasons=[f"overall:{overall}"], evidence=record["evidence"])
    if extra:
        record["reasons"] = record["reasons"] + extra
    record["evidence"] = {
        "overall": overall,
        "draw_reached": _flag(status.get("draw_reached")),
        "draw_encoded": _flag(status.get("draw_encoded")),
        "gpu_completed": _flag(status.get("gpu_completed")),
        "pixels_captured": _flag(status.get("pixels_captured")),
    }
    return record


def assess_checkpoint(record: dict, *, base: Path | None = None) -> dict:
    """Assess one ``gfx-checkpoint/v1`` record and its raw payload."""
    if not isinstance(record, dict) or record.get("schema") not in (
        "gfx-checkpoint/v1",
        None,
    ):
        return validity(INVALID, reasons=["unexpected checkpoint schema"])
    status = record.get("status") if isinstance(record.get("status"), dict) else {}
    gpu = record.get("gpu") if isinstance(record.get("gpu"), dict) else {}
    checks: list[tuple[str, bool | None]] = [
        ("draw_reached", _flag(status.get("draw_reached"))),
        ("draw_encoded", _flag(status.get("draw_encoded"))),
        ("gpu_completed", _flag(gpu.get("completed")) if gpu else None),
        ("pixels_captured", _flag(status.get("pixels_captured"))),
    ]
    command_status = _int(gpu.get("command_status"))
    checks.append(("command_status_completed", None if command_status is None else command_status == 4))
    payload_ok, payload_reason = _payload_matches(record, base)
    checks.append(("payload_matches", payload_ok))
    record_out = _validity_from_checks(checks)
    if payload_reason:
        record_out["reasons"].append(payload_reason)
    record_out["evidence"] = {
        "checkpoint": record.get("checkpoint"),
        "caller_rva": (record.get("caller") or {}).get("rva"),
        "gpu": gpu,
    }
    return record_out


def assess_replay(record: dict, *, base: Path | None = None) -> dict:
    """Assess a Metal replay result (``replay.json``-shaped)."""
    if not isinstance(record, dict):
        return validity(INVALID, reasons=["result is not an object"])
    checks: list[tuple[str, bool | None]] = [
        ("status_ok", record.get("status") == "ok" if "status" in record else None),
        ("failed_zero", record.get("failed") == 0 if "failed" in record else None),
        ("encoded_all", (
            record.get("encoded") == record.get("events")
            if type(record.get("encoded")) is int and type(record.get("events")) is int
            else None
        )),
        ("command_completed", (
            _int(record.get("command_status")) == 4
            if record.get("command_status") is not None
            else None
        )),
    ]
    frame = record.get("frame") if isinstance(record.get("frame"), dict) else None
    payload_ok: bool | None = None
    if frame is not None:
        capture = {
            "capture": {
                "raw_file": frame.get("raw_file"),
                "raw_bytes": frame.get("raw_bytes"),
                "raw_sha256": frame.get("raw_sha256"),
            },
            "target": {"width": frame.get("width"), "height": frame.get("height")},
        }
        payload_ok, reason = _payload_matches(capture, base)
        if payload_ok is not True and reason:
            checks.append(("payload_matches", False if payload_ok is False else None))
        else:
            checks.append(("payload_matches", payload_ok))
    else:
        checks.append(("frame_present", None))
    return _validity_from_checks(checks)


def assess_frame_observation(record: dict, *, base: Path | None = None) -> dict:
    """Assess one per-frame observation emitted by the native stream."""
    if not isinstance(record, dict):
        return validity(INVALID, reasons=["observation is not an object"])
    checks: list[tuple[str, bool | None]] = [
        ("frame_index", type(record.get("frame_index")) is int),
        ("width", type(record.get("width")) is int and record["width"] > 0),
        ("height", type(record.get("height")) is int and record["height"] > 0),
        ("pixel_format", isinstance(record.get("pixel_format"), str)),
        ("sha256", isinstance(record.get("sha256"), str) and len(record["sha256"]) == 64),
        ("gpu_completed", _flag(record.get("gpu_completed"))),
    ]
    if record.get("pixels_present") is False:
        checks.append(("pixels_present", False))
    return _validity_from_checks(checks)


# ---------------------------------------------------------------------------
# GPU health


@dataclass(frozen=True)
class HealthEvent:
    """One observed unhealthy (or informative) execution event."""

    kind: str
    severity: str
    signature: str
    message: str
    source: str = ""
    symbol: str | None = None
    draw: str | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "signature": self.signature,
            "message": self.message,
            "source": self.source,
            "symbol": self.symbol,
            "draw": self.draw,
            "detail": self.detail,
        }


_NUMBER = re.compile(r"0x[0-9a-fA-F]+|\b\d+\b")
_QUOTED = re.compile(r'"[^"]*"')


def normalize_message(text: str) -> str:
    """Collapse addresses/counts/quoted payloads so repeats aggregate."""
    collapsed = _QUOTED.sub('"<value>"', text)
    collapsed = _NUMBER.sub("<n>", collapsed)
    return " ".join(collapsed.split())[:512]


def _event(
    kind: str,
    message: str,
    *,
    severity: str = "error",
    symbol: str | None = None,
    draw: str | None = None,
    source: str = "",
    detail: dict | None = None,
    signature: str | None = None,
) -> HealthEvent:
    return HealthEvent(
        kind=kind,
        severity=severity,
        signature=signature or f"{kind}:{normalize_message(message)}",
        message=message,
        source=source,
        symbol=symbol,
        draw=draw,
        detail=detail or {},
    )


class GpuHealth:
    """Aggregating collector for GPU/API execution health.

    ``events`` retains the first occurrence of each signature (bounded), while
    ``counts`` and ``repeat_aggregated`` record how often it recurred.  This is
    the bounded representation the requirement asks for: thousands of identical
    validation messages become one entry with a count.
    """

    def __init__(self, *, max_distinct: int = 256):
        self.max_distinct = max_distinct
        self.events: list[HealthEvent] = []
        self.counts: dict[str, int] = {}
        self.first_seen: dict[str, int] = {}
        self.last_seen: dict[str, int] = {}
        self._order = 0
        self._truncated = 0

    def observe(self, event: HealthEvent) -> bool:
        """Record one event.  Returns True when a new signature was retained."""
        self._order += 1
        signature = event.signature
        if signature in self.counts:
            self.counts[signature] += 1
            self.last_seen[signature] = self._order
            return False
        if len(self.events) >= self.max_distinct:
            self._truncated += 1
            self.counts[signature] = self.counts.get(signature, 0) + 1
            return False
        self.events.append(event)
        self.counts[signature] = 1
        self.first_seen[signature] = self._order
        self.last_seen[signature] = self._order
        return True

    def add(
        self,
        kind: str,
        message: str,
        *,
        severity: str = "error",
        symbol: str | None = None,
        draw: str | None = None,
        source: str = "",
        detail: dict | None = None,
        signature: str | None = None,
    ) -> HealthEvent:
        event = _event(
            kind,
            message,
            severity=severity,
            symbol=symbol,
            draw=draw,
            source=source,
            detail=detail,
            signature=signature,
        )
        self.observe(event)
        return event

    def first_blocking(self) -> HealthEvent | None:
        for event in self.events:
            if event.severity == "error" and event.kind in BLOCKING_CLASSES:
                return event
        return None

    def aggregated(self) -> list[dict]:
        rows = []
        for event in self.events:
            row = event.as_dict()
            row["count"] = self.counts[event.signature]
            rows.append(row)
        # Signatures beyond the retention bound are still represented.
        retained = {event.signature for event in self.events}
        for signature in sorted(self.counts):
            if signature in retained:
                continue
            rows.append(
                {
                    "kind": "other",
                    "severity": "error",
                    "signature": signature,
                    "message": "additional repeated diagnostic (signature only)",
                    "source": "",
                    "symbol": None,
                    "draw": None,
                    "detail": {"aggregated_only": True},
                    "count": self.counts[signature],
                }
            )
        return rows

    def verdict(self) -> str:
        if not self.events:
            return UNKNOWN
        if any(event.severity == "error" and event.kind in BLOCKING_CLASSES for event in self.events):
            return FAILED
        if any(event.severity == "error" for event in self.events):
            return DEGRADED
        return HEALTHY

    def summary(self, *, pixel_correct: bool | None = None) -> dict:
        blocking = [
            event for event in self.events
            if event.severity == "error" and event.kind in BLOCKING_CLASSES
        ]
        first = blocking[0] if blocking else None
        return {
            "schema": HEALTH_SCHEMA,
            "verdict": self.verdict(),
            "distinct_diagnostics": len(self.events),
            "total_diagnostics": sum(self.counts.values()),
            "truncated_signatures": self._truncated,
            "first_failure": first.as_dict() | {"count": self.counts[first.signature]}
            if first is not None else None,
            "aggregated": self.aggregated(),
            # Command-buffer completion never implies pixel correctness.  This
            # stays None unless an explicit aligned comparison produced it.
            "pixel_correctness": pixel_correct,
            "instrumentation_failures": [
                event.as_dict()
                for event in self.events
                if event.kind in INSTRUMENTATION_CLASSES
            ],
        }


def health_gate(summary: dict, *, result_requires_pixels: bool = False) -> dict:
    """Decide whether a requested diagnostic result is still valid.

    Returns ``{"allow": bool, "reason": str, "status": ...}``.  A GPU failure
    always stops the current requested result.  A capture that cannot be
    verified does not pass merely because the command buffer completed.
    """
    if not isinstance(summary, dict):
        return {"allow": False, "status": INVALID, "reason": "health summary is absent"}
    if summary.get("verdict") == FAILED:
        first = summary.get("first_failure") or {}
        reason = first.get("message") or "GPU/API execution failure"
        return {"allow": False, "status": FAILED, "reason": reason, "first_failure": first}
    if result_requires_pixels and summary.get("pixel_correctness") is not True:
        return {
            "allow": False,
            "status": UNVERIFIED,
            "reason": "GPU completion is not pixel correctness; no aligned comparison is available",
        }
    if summary.get("verdict") == DEGRADED:
        return {
            "allow": False,
            "status": INVALID,
            "reason": "a non-execution diagnostic failure makes the result unverified",
        }
    return {"allow": True, "status": VALID, "reason": ""}


_VALIDATION_PATTERNS = (
    re.compile(r"metal validation", re.IGNORECASE),
    re.compile(r"validation layer", re.IGNORECASE),
    re.compile(r"MTL(CommandBuffer|RenderCommandEncoder|Texture|Buffer)", re.IGNORECASE),
    re.compile(r"failed assertion", re.IGNORECASE),
    re.compile(r"\[Metal\]", re.IGNORECASE),
)


def _scan_stderr(path: Path, health: GpuHealth, *, limit_lines: int = 20000) -> None:
    """Parse Metal validation output.  Repeated lines aggregate by signature."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as source:
            for number, line in enumerate(source):
                if number >= limit_lines:
                    break
                text = line.strip()
                if not text:
                    continue
                if any(pattern.search(text) for pattern in _VALIDATION_PATTERNS):
                    health.add(
                        "validation",
                        text,
                        source=str(path),
                        signature="validation:" + normalize_message(text),
                    )
    except OSError:
        return


def _scan_stop(path: Path, health: GpuHealth) -> None:
    stop = _read_json(path)
    if stop is None:
        return
    reason = str(stop.get("reason") or "")
    symbol = stop.get("symbol") if isinstance(stop.get("symbol"), str) else None
    if reason == "gpu_failed":
        health.add("command_buffer", "Metal command buffer failed", symbol=symbol,
                   source=str(path), detail={"rva": stop.get("rva")})
    elif reason in ("unsupported", "failed"):
        kind = "draw_refused" if symbol in ("_GfxDraw", "_GfxDrawIndexed", "_GfxDrawIndexedInstanced") else "render_target"
        health.add(kind, f"{symbol or 'operation'} stopped: {reason}", symbol=symbol,
                   source=str(path), detail={"rva": stop.get("rva"), "reason": reason})
    elif reason == "shader-cache":
        health.add("instrumentation", "shader cache could not be loaded", symbol=symbol,
                   source=str(path), severity="error")
    elif reason == "missing_backend":
        health.add("instrumentation", "Metal backend is missing", symbol=symbol,
                   source=str(path), severity="error")


def scan_run(run_dir: Path, *, health: GpuHealth | None = None) -> dict:
    """Scan one live run directory into an aggregated health report.

    The scan is bounded: one pass over the small JSON artifacts, a bounded
    number of ``resolve-trace.jsonl`` rows and a bounded stderr read.  It never
    loads raw framebuffers.
    """
    run_dir = Path(run_dir)
    health = health or GpuHealth()
    if not run_dir.is_dir():
        health.add("instrumentation", "run directory is absent", source=str(run_dir))
        return health.summary()

    for path in sorted(run_dir.glob("draw-refused-*.json")):
        record = _read_json(path)
        if record is None:
            health.add("instrumentation", "draw refusal record is unreadable", source=str(path))
            continue
        reason = str(record.get("reason") or "draw refused")
        caller = record.get("caller_rva")
        health.add(
            "draw_refused",
            reason,
            symbol=str(record.get("variant") or "_GfxDraw"),
            draw=str(caller) if caller is not None else None,
            source=str(path),
            signature=f"draw_refused:{record.get('variant')}:{normalize_message(reason)}",
            detail={"caller_rva": caller, "route": record.get("route")},
        )

    for name, kind in (
        ("draw-unsupported.json", "draw_refused"),
        ("generic-pipeline-error.json", "shader_pipeline"),
        ("diagnostic-failure.json", "capture"),
        ("render-target-failure.json", "render_target"),
    ):
        path = run_dir / name
        if path.is_file():
            record = _read_json(path) or {}
            message = str(record.get("error") or record.get("reason") or name)
            health.add(kind, message, source=str(path), detail=record if len(str(record)) < 2048 else {})

    stop = run_dir / "stop.json"
    if stop.is_file():
        _scan_stop(stop, health)

    resolve = run_dir / "resolve-trace.jsonl"
    if resolve.is_file():
        for row in _read_jsonl(resolve):
            status = str(row.get("status") or "")
            if "fail" in status.lower() or "missing" in status.lower():
                health.add(
                    "resolve",
                    status,
                    source=str(resolve),
                    draw=str(row.get("draw_seq")) if row.get("draw_seq") is not None else None,
                    detail={"seq": row.get("seq"), "status": status},
                    signature=f"resolve:{status}",
                )

    for path in sorted(run_dir.glob("checkpoint-*.json")):
        record = _read_json(path)
        if record is None:
            health.add("instrumentation", "checkpoint record is unreadable", source=str(path))
            continue
        validity_record = assess_checkpoint(record, base=run_dir)
        if validity_record["status"] == INVALID:
            health.add(
                "capture",
                "checkpoint capture is invalid: " + ",".join(validity_record["reasons"][:4]),
                source=str(path),
                severity="error",
            )
        elif validity_record["status"] == UNVERIFIED:
            health.add(
                "capture",
                "checkpoint capture is unverified",
                source=str(path),
                severity="warning",
            )

    probe = run_dir / "probe-result.json"
    if probe.is_file():
        record = _read_json(probe) or {}
        validity_record = assess_probe(record, base=run_dir)
        if validity_record["status"] != VALID:
            health.add(
                "capture",
                f"probe result is {validity_record['status']}",
                source=str(probe),
                severity="error" if validity_record["status"] == INVALID else "warning",
                detail={"reasons": validity_record["reasons"][:8]},
            )

    for name in ("game.stderr", "stderr.log", "metal.stderr"):
        path = run_dir / name
        if path.is_file():
            _scan_stderr(path, health)
            break

    health_file = run_dir / "gpu-health.jsonl"
    if health_file.is_file():
        for row in _read_jsonl(health_file):
            kind = str(row.get("kind") or "validation")
            health.add(
                kind,
                str(row.get("message") or kind),
                severity=str(row.get("severity") or "error"),
                symbol=row.get("symbol") if isinstance(row.get("symbol"), str) else None,
                draw=str(row.get("draw")) if row.get("draw") is not None else None,
                source=str(health_file),
                detail={key: row[key] for key in ("rva", "pipeline", "shader") if key in row},
                signature=str(row.get("signature")) if row.get("signature") else None,
            )
    return health.summary()


def report_to_json(summary: dict) -> str:
    return json.dumps(summary, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Direct CLI (no agent framework required)

def _parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Capture validity and GPU execution-health gate",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    health = sub.add_parser("health", help="scan one or more live run directories")
    health.add_argument("--run", action="append", required=True, type=Path)
    health.add_argument("--require-pixels", action="store_true",
                        help="require an aligned pixel comparison for a pass")
    health.add_argument("--out", type=Path, default=None)

    valid = sub.add_parser("validity", help="assess one capture record")
    valid.add_argument("--record", required=True, type=Path)
    valid.add_argument("--kind", required=True,
                       choices=("probe", "checkpoint", "replay", "frame"))
    valid.add_argument("--base", type=Path, default=None,
                       help="directory holding the declared raw payload")
    return parser


def main(argv: list[str] | None = None) -> int:
    import sys

    args = _parser().parse_args(argv)
    if args.command == "validity":
        record = _read_json(args.record)
        if record is None:
            print(json.dumps({"schema": CAPTURE_SCHEMA, "status": INVALID,
                              "reasons": ["record is absent or unreadable"]}))
            return 2
        assess = {
            "probe": assess_probe,
            "checkpoint": assess_checkpoint,
            "replay": assess_replay,
            "frame": assess_frame_observation,
        }[args.kind]
        result = assess(record, base=args.base or args.record.parent)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == VALID else 2

    runs = []
    worst = HEALTHY
    order = {HEALTHY: 0, UNKNOWN: 1, DEGRADED: 2, FAILED: 3}
    gate_results = []
    for run in args.run:
        summary = scan_run(run)
        gate = health_gate(summary, result_requires_pixels=args.require_pixels)
        gate_results.append(gate)
        runs.append({"run": str(run), "summary": summary, "gate": gate})
        if order.get(summary["verdict"], 3) > order.get(worst, 0):
            worst = summary["verdict"]
    combined = {
        "schema": "gfx-health-gate/v1",
        "runs": runs,
        "verdict": worst,
        "allow": all(item["allow"] for item in gate_results),
        "require_pixels": bool(args.require_pixels),
    }
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(combined, indent=2, sort_keys=True) + "\n")
    print(json.dumps(combined, indent=2, sort_keys=True))
    return 0 if combined["allow"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
