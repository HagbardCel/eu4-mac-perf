"""Deterministic controller for a P3 evaluation attempt.

The controller owns everything the agent must not: the tool-call budget, the
attempt limit, the wall clock, the process tree, candidate freezing, the single
held-out certification and the terminal receipt.

Sequence for one run:

1. Create the run directory, budget ledger and controller token.
2. Materialize a fresh candidate-only workspace (no held-out, no Oracle source).
3. Let the adapter take bounded attempts against the five visible tools.
4. Terminate and reap every remaining child process group.
5. Freeze the candidate into a read-only snapshot.
6. Certify **once** from the snapshot, using the controller token.
7. Seal a terminal receipt - always, including after timeout or crash.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.slice import compare, corpus, harness, receipts
from oracle.slice.adapters import (
    AgentAdapter,
    AttemptContext,
    AttemptResult,
    BudgetStop,
    DeadlineExceeded,
    ProcessSupervisor,
    ToolClient,
)
from oracle.slice.bridge import (
    RUN_ID_FILE,
    Bridge,
    certify_candidate,
    write_controller_token,
)
from oracle.slice.budget import RunBudgetLedger
from oracle.slice.workspace import FrozenCandidate, WorkspaceError, freeze_candidate, materialize_workspace

PHASE = "P3-A"

STATUS_CERTIFIED = "certified"
STATUS_VISIBLE_FAILED = "visible_failed"
STATUS_HELD_OUT_FAILED = "held_out_failed"
STATUS_ABORTED = "aborted"


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    candidate_src: Path
    base_dir: Path
    budget_calls: int = 40
    max_attempts: int = 4
    no_progress_limit: int = 3
    wall_seconds: float = 120.0
    timeout_s: float = harness.DEFAULT_TIMEOUT_S
    cc: str = "cc"
    fixtures_root: Path | None = None
    baseline_commit: str | None = None
    model: dict[str, Any] | None = None


@dataclass
class RunResult:
    run_id: str
    status: str
    abort_reason: str | None
    visible: dict[str, Any] | None
    held_out: dict[str, Any] | None
    attempts: list[dict[str, Any]]
    receipt: dict[str, Any]
    receipt_path: Path
    run_dir: Path


def _suite_verdict(candidate: Path, set_name: str, *, build_dir: Path, run_id: str,
                   timeout_s: float, cc: str, fixtures_root: Path | None) -> dict[str, Any]:
    root = Path(fixtures_root) if fixtures_root else corpus.FIXTURES_DIR
    fixtures = corpus.load_corpus(set_name, root=root)
    build = harness.build(candidate, build_dir, cc=cc)
    diagnostics = [harness.evaluate(build, fixture, run_id=run_id, timeout_s=timeout_s)
                   for fixture in fixtures]
    return compare.verdict(diagnostics)


def _toolchain(cc: str, flags: tuple[str, ...], compiler_version: str) -> dict[str, Any]:
    slice_dir = harness.SLICE_DIR
    return {
        "cc": cc,
        "flags": list(flags),
        "compiler_version": compiler_version,
        "harness_sha256": receipts.file_sha256(slice_dir / "harness.py"),
        "comparator_sha256": receipts.file_sha256(slice_dir / "compare.py"),
        "driver_sha256": receipts.file_sha256(slice_dir / "driver.c"),
        "interface_sha256": receipts.file_sha256(slice_dir / "synth.h"),
    }


def run(config: RunConfig, adapter: AgentAdapter) -> RunResult:
    run_dir = Path(config.base_dir) / config.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / RUN_ID_FILE).write_text(config.run_id, encoding="utf-8")

    ledger = RunBudgetLedger.create(run_dir, config.run_id, config.budget_calls)
    token = write_controller_token(run_dir)
    workspace = materialize_workspace(
        run_dir / "workspace",
        candidate_src=config.candidate_src,
        header_src=harness.SLICE_DIR / "synth.h",
        visible_root=config.fixtures_root,
    )
    defective_sha = receipts.file_sha256(config.candidate_src)

    started = time.monotonic()
    deadline = started + config.wall_seconds
    supervisor = ProcessSupervisor(deadline=deadline, log_dir=run_dir / "logs")
    bridge = Bridge(run_dir=run_dir, workspace=workspace.root, ledger=ledger,
                    visible_root=config.fixtures_root, timeout_s=config.timeout_s, cc=config.cc)
    tools = ToolClient(bridge, deadline=deadline)

    attempts: list[dict[str, Any]] = []
    abort_reason: str | None = None
    last_candidate_sha: str | None = None
    no_progress = 0
    try:
        attempt = 0
        while attempt < config.max_attempts:
            if supervisor.expired():
                abort_reason = "timeout"
                break
            if ledger.remaining() <= 0:
                abort_reason = "budget"
                break
            attempt += 1
            ctx = AttemptContext(attempt=attempt, workspace=workspace.root,
                                 candidate_path=workspace.candidate_path, tools=tools,
                                 deadline=deadline, supervisor=supervisor)
            tool_calls_before = tools.calls
            attempt_started = time.monotonic()
            try:
                result = adapter.step(ctx)
            except BudgetStop as exc:
                result = AttemptResult("budget", str(exc))
            except DeadlineExceeded as exc:
                result = AttemptResult("timeout", str(exc))
            except Exception as exc:  # an adapter fault is not a controller fault
                result = AttemptResult("crashed", f"{type(exc).__name__}: {exc}")
            candidate_sha = (receipts.file_sha256(workspace.candidate_path)
                             if workspace.candidate_path.is_file() else None)
            attempts.append({
                "attempt": attempt,
                "status": result.status,
                "note": result.note,
                "tool_calls": tools.calls - tool_calls_before,
                "duration_s": round(time.monotonic() - attempt_started, 6),
                "candidate_sha256": candidate_sha,
            })
            if result.status == "done":
                break
            if result.status in ("gave_up", "crashed", "timeout", "budget"):
                abort_reason = result.status
                break
            no_progress = no_progress + 1 if candidate_sha == last_candidate_sha else 0
            last_candidate_sha = candidate_sha
            if no_progress >= config.no_progress_limit:
                abort_reason = "no_progress"
                break
        else:
            abort_reason = "max_attempts"
    finally:
        supervisor.terminate_all()

    # Freeze before certifying: certification must not read a writable path.
    frozen: FrozenCandidate | None = None
    freeze_error: str | None = None
    try:
        frozen = freeze_candidate(workspace.candidate_path, run_dir / "snapshot" / "final")
    except WorkspaceError as exc:
        freeze_error = str(exc)

    visible: dict[str, Any] | None = None
    held_out: dict[str, Any] | None = None
    if frozen is not None:
        visible = _suite_verdict(frozen.path, corpus.VISIBLE, build_dir=run_dir / "verdict" / "visible",
                                 run_id=config.run_id, timeout_s=config.timeout_s, cc=config.cc,
                                 fixtures_root=config.fixtures_root)
        held_out = certify_candidate(frozen.path, run_dir=run_dir, run_id=config.run_id, token=token,
                                     timeout_s=config.timeout_s, cc=config.cc,
                                     fixtures_root=config.fixtures_root)

    if frozen is None:
        status = STATUS_ABORTED
    elif (visible is not None and visible["status"] == compare.STATUS_PASS
          and held_out is not None and held_out["status"] == compare.STATUS_PASS):
        status = STATUS_CERTIFIED
    elif visible is None or visible["status"] != compare.STATUS_PASS:
        status = STATUS_VISIBLE_FAILED
    else:
        status = STATUS_HELD_OUT_FAILED

    visible_manifest = corpus.manifest(corpus.load_corpus(
        corpus.VISIBLE, root=Path(config.fixtures_root) if config.fixtures_root else corpus.FIXTURES_DIR),
        set_name=corpus.VISIBLE)
    held_manifest = corpus.manifest(corpus.load_corpus(
        corpus.HELD_OUT, root=Path(config.fixtures_root) if config.fixtures_root else corpus.FIXTURES_DIR),
        set_name=corpus.HELD_OUT)

    toolchain = _toolchain(config.cc, harness.COMPILE_FLAGS,
                           harness.compiler_version(config.cc))
    payload: dict[str, Any] = {
        "phase": PHASE,
        "run_id": config.run_id,
        "source": {
            "baseline_commit": config.baseline_commit,
            "defective_candidate_sha256": defective_sha,
            "final_candidate_sha256": frozen.sha256 if frozen else None,
            "snapshot": str(frozen.path) if frozen else None,
            "freeze_error": freeze_error,
        },
        "corpus": {"visible": visible_manifest, "held_out": held_manifest},
        "toolchain": toolchain,
        "agent": {
            "adapter": adapter.name,
            "model": config.model,
            "tool_calls": tools.calls,
            "budget": {"granted": ledger.granted(), "limit": ledger.limit()},
        },
        "execution": {
            "attempts": attempts,
            "wall_seconds": round(time.monotonic() - started, 6),
            "abort_reason": abort_reason,
        },
        "verdict": {"visible": visible, "held_out": held_out, "status": status},
    }
    receipt = receipts.seal(payload)
    receipt_path = receipts.write(run_dir / "receipt.json", receipt)
    ledger.close()

    return RunResult(run_id=config.run_id, status=status, abort_reason=abort_reason,
                     visible=visible, held_out=held_out, attempts=attempts,
                     receipt=receipt, receipt_path=receipt_path, run_dir=run_dir)
