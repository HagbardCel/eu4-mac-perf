"""Deterministic controller for a P3 evaluation attempt.

The controller owns everything the agent must not: the tool-call budget, the
attempt limit, the wall clock, the process tree, candidate freezing, the single
held-out certification and the terminal receipt.

Sequence for one run:

1. Create the run directory, budget ledger, controller token and trusted run
   state (the immutable monotonic deadline the MCP server enforces).
2. Materialize a fresh candidate-only workspace (no held-out, no Oracle source).
3. Let the adapter take bounded attempts against the five visible tools.
4. Terminate and reap every remaining child process group.
5. Freeze the candidate into a read-only snapshot.
6. Run the trusted visible suite as a diagnostic, and - only for a completed,
   non-aborted attempt whose visible suite passes - certify **once** from the
   snapshot using the controller token.
7. Seal a terminal receipt - always, including after timeout, crash, a candidate
   that does not compile, or a trusted-harness fault during finalization.

Two distinct clocks bound the work. The **run deadline** bounds the agent, its
MCP tools and the visible tests it initiates; a fresh **finalization budget**
bounds freezing, the trusted visible verification, certification and receipt
publication. A fresh finalization budget never revives an expired run nor makes
an aborted run eligible for certification.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oracle.slice import compare, corpus, harness, receipts, runstate
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
    ToolError,
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

CERTIFICATION_RAN = "ran"


@dataclass(frozen=True)
class RunConfig:
    run_id: str
    candidate_src: Path
    base_dir: Path
    budget_calls: int = 40
    max_attempts: int = 4
    no_progress_limit: int = 3
    wall_seconds: float = 120.0
    finalization_seconds: float = 60.0
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
    certification: str
    candidate_verified: bool


def _fixture_root(fixtures_root: Path | None) -> Path:
    return Path(fixtures_root) if fixtures_root else corpus.FIXTURES_DIR


def _suite_verdict(candidate: Path, set_name: str, *, build_dir: Path, run_id: str,
                   timeout_s: float, cc: str, fixtures_root: Path | None,
                   deadline: float | None) -> dict[str, Any]:
    root = _fixture_root(fixtures_root)
    fixtures = corpus.load_corpus(set_name, root=root)
    build = harness.build(candidate, build_dir, cc=cc, deadline=deadline)
    diagnostics = [harness.evaluate(build, fixture, run_id=run_id, timeout_s=timeout_s,
                                    deadline=deadline)
                   for fixture in fixtures]
    return compare.verdict(diagnostics)


def _failed_verdict(set_name: str, status: str, note: str,
                    fixtures_root: Path | None) -> dict[str, Any]:
    """A non-PASS verdict for a suite that could not be evaluated at all."""
    try:
        total = len(corpus.load_corpus(set_name, root=_fixture_root(fixtures_root)))
    except Exception:  # pragma: no cover - defensive
        total = 0
    return {"status": status, "total": total, "passed": 0, "failures": [], "note": note}


def _manifest(set_name: str, fixtures_root: Path | None) -> dict[str, Any] | None:
    try:
        return corpus.manifest(corpus.load_corpus(set_name, root=_fixture_root(fixtures_root)),
                               set_name=set_name)
    except Exception:  # pragma: no cover - defensive
        return None


def _toolchain(cc: str, flags: tuple[str, ...]) -> dict[str, Any]:
    slice_dir = harness.SLICE_DIR
    try:
        version = harness.compiler_version(cc)
    except harness.HarnessError as exc:  # pragma: no cover - defensive
        version = f"unavailable: {exc}"
    return {
        "cc": cc,
        "flags": list(flags),
        "compiler_version": version,
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
    # The MCP server - a separate process - loads this deadline independently.
    runstate.write_run_state(run_dir, run_id=config.run_id, deadline_monotonic=deadline,
                             budget_seconds=config.wall_seconds)
    supervisor = ProcessSupervisor(deadline=deadline, log_dir=run_dir / "logs")
    bridge = Bridge(run_dir=run_dir, workspace=workspace.root, ledger=ledger,
                    visible_root=config.fixtures_root, timeout_s=config.timeout_s, cc=config.cc,
                    run_deadline=deadline)
    tools = ToolClient(bridge, deadline=deadline)

    attempts: list[dict[str, Any]] = []
    abort_reason: str | None = None
    controller_error: str | None = None
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
    except Exception as exc:  # a trusted-state fault must not lose the receipt
        abort_reason = abort_reason or "controller_error"
        controller_error = f"{type(exc).__name__}: {exc}"
    finally:
        # Terminate and reap every remaining child process group, descendants
        # included, before anything downstream reads the candidate.
        supervisor.terminate_all()

    # --- bounded finalization -------------------------------------------------
    finalize_started = time.monotonic()
    finalize_deadline = finalize_started + config.finalization_seconds

    frozen: FrozenCandidate | None = None
    freeze_error: str | None = None
    try:
        frozen = freeze_candidate(workspace.candidate_path, run_dir / "snapshot" / "final")
    except WorkspaceError as exc:
        freeze_error = str(exc)

    visible: dict[str, Any] | None = None
    held_out: dict[str, Any] | None = None
    certification = "not_attempted"
    finalization_error: str | None = None
    if frozen is not None:
        # The visible suite is always run for a frozen candidate: it is the
        # diagnostic evidence for an aborted or failed attempt.
        try:
            visible = _suite_verdict(frozen.path, corpus.VISIBLE,
                                     build_dir=run_dir / "verdict" / "visible",
                                     run_id=config.run_id, timeout_s=config.timeout_s,
                                     cc=config.cc, fixtures_root=config.fixtures_root,
                                     deadline=finalize_deadline)
        except harness.BuildError as exc:
            visible = _failed_verdict(corpus.VISIBLE, compare.STATUS_BUILD_ERROR,
                                      "candidate failed to compile: " + (exc.stderr or "")[-400:],
                                      config.fixtures_root)
        except harness.HarnessError as exc:
            visible = _failed_verdict(corpus.VISIBLE, compare.STATUS_HARNESS_ERROR, str(exc),
                                      config.fixtures_root)
            finalization_error = f"{type(exc).__name__}: {exc}"
        # Only a completed, non-aborted attempt whose visible suite passes may
        # spend the held-out corpus.
        if abort_reason is None and visible is not None and visible["status"] == compare.STATUS_PASS:
            certification = CERTIFICATION_RAN
            try:
                held_out = certify_candidate(frozen.path, run_dir=run_dir, run_id=config.run_id,
                                             token=token, timeout_s=config.timeout_s, cc=config.cc,
                                             fixtures_root=config.fixtures_root,
                                             deadline=finalize_deadline)
            except harness.BuildError as exc:
                held_out = _failed_verdict(corpus.HELD_OUT, compare.STATUS_BUILD_ERROR,
                                           "candidate failed to compile: " + (exc.stderr or "")[-400:],
                                           config.fixtures_root)
                certification = "failed: build"
            except (harness.HarnessError, ToolError) as exc:
                held_out = _failed_verdict(corpus.HELD_OUT, compare.STATUS_HARNESS_ERROR, str(exc),
                                           config.fixtures_root)
                certification = f"failed: {type(exc).__name__}"
                finalization_error = f"{type(exc).__name__}: {exc}"
        else:
            reason = ("agent aborted: " + abort_reason) if abort_reason else "visible suite did not pass"
            certification = f"skipped: {reason}"

    candidate_verified = bool(
        visible is not None and visible["status"] == compare.STATUS_PASS
        and held_out is not None and held_out["status"] == compare.STATUS_PASS
    )

    # Abort-first: a candidate may independently verify, but a run only
    # *completes* when the agent completed, did not abort and passed both suites.
    if frozen is None or abort_reason is not None or controller_error is not None:
        status = STATUS_ABORTED
    elif candidate_verified:
        status = STATUS_CERTIFIED
    elif visible is None or visible["status"] != compare.STATUS_PASS:
        status = STATUS_VISIBLE_FAILED
    else:
        status = STATUS_HELD_OUT_FAILED

    visible_manifest = _manifest(corpus.VISIBLE, config.fixtures_root)
    held_manifest = _manifest(corpus.HELD_OUT, config.fixtures_root)
    toolchain = _toolchain(config.cc, harness.COMPILE_FLAGS)
    try:
        granted = ledger.granted()
        limit = ledger.limit()
    except Exception as exc:  # pragma: no cover - defensive
        granted = limit = None
        finalization_error = finalization_error or f"{type(exc).__name__}: {exc}"

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
            "budget": {"granted": granted, "limit": limit},
        },
        "execution": {
            "attempts": attempts,
            "wall_seconds": round(time.monotonic() - started, 6),
            "abort_reason": abort_reason,
            "controller_error": controller_error,
            "finalization": {
                "budget_seconds": config.finalization_seconds,
                "duration_s": round(time.monotonic() - finalize_started, 6),
                "certification": certification,
                "error": finalization_error,
            },
        },
        "verdict": {
            "visible": visible,
            "held_out": held_out,
            "status": status,
            "candidate_verified": candidate_verified,
            "certification": certification,
        },
    }
    try:
        receipt = receipts.seal(payload)
        receipt_path = receipts.write(run_dir / "receipt.json", receipt)
    except OSError as exc:
        raise receipts.ReceiptError(f"failed to store the terminal receipt: {exc}") from exc
    finally:
        ledger.close()

    return RunResult(run_id=config.run_id, status=status, abort_reason=abort_reason,
                     visible=visible, held_out=held_out, attempts=attempts,
                     receipt=receipt, receipt_path=receipt_path, run_dir=run_dir,
                     certification=certification, candidate_verified=candidate_verified)
