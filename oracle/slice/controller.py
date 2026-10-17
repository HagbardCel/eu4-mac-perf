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

Classification is abort-first, then infrastructure, then the candidate verdicts.
A trusted-host fault (a missing or malformed corpus, a harness or toolchain that
cannot operate, a compiler whose identity cannot be established) is recorded in
``execution.finalization.error`` and classified ``aborted`` - it is never
reported as ``visible_failed`` or ``held_out_failed``, because the candidate did
not fail the comparison; the Oracle could not complete it.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oracle.slice import budget, compare, corpus, harness, receipts, runstate
from oracle.slice.adapters import (
    AgentAdapter,
    AttemptContext,
    AttemptResult,
    BindingError,
    BudgetStop,
    DeadlineExceeded,
    ProcessSupervisor,
    ToolClient,
)
from oracle.slice.bridge import (
    RUN_ID_FILE,
    TEST_DIR,
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
#: Repair acceptance only: the gates ran and the final candidate is byte-identical
#: to the defective input. Not a visible failure - the candidate was never
#: repaired at all - and never certified.
STATUS_NOT_REPAIRED = "not_repaired"
#: Repair acceptance only: the candidate changed, but a repair gate (required
#: diagnostics, trusted test evidence, call reconciliation) was not satisfied, so
#: the held-out corpus was never spent.
STATUS_ACCEPTANCE_GATE_FAILED = "acceptance_gate_failed"

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
    #: The source revision the pre-registration pinned (its ``baseline_commit``).
    #: The pins and the replay are checked against this revision.
    baseline_commit: str | None = None
    #: The commit that published the pre-registration, and its repository-relative
    #: path. Recorded so a later, offline check can re-verify that the declaration
    #: was a committed object - not merely a readable file - before the run. See
    #: ``opencode_run.py``; generic P3-A runs leave both ``None``.
    declaration_commit: str | None = None
    preregistration_path: str | None = None
    model: dict[str, Any] | None = None
    #: Repair acceptance (P3-B). When set, the controller enforces the repair
    #: contract *before* the held-out corpus may be spent: the candidate must have
    #: changed, the adapter must have completed the required diagnostics, a
    #: trusted test record must attest the frozen bytes, and the call census must
    #: reconcile. Generic P3-A runs leave this off, so a deterministic adapter is
    #: never asked for an OpenCode event stream.
    require_repair_acceptance: bool = False


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


def _toolchain(cc: str, flags: tuple[str, ...], *, deadline: float | None = None,
               capture_dir: Path | None = None) -> tuple[dict[str, Any], str | None]:
    """Toolchain identity for the receipt, plus why any part is unavailable.

    Every trusted read is guarded. A source that cannot be read is recorded as an
    explicit ``None`` digest and reported back to the caller, so incomplete
    provenance becomes an infrastructure failure with a terminal receipt rather
    than an exception that escapes before the receipt is sealed.
    """
    slice_dir = harness.SLICE_DIR
    try:
        version = harness.compiler_version(cc, deadline=deadline, capture_dir=capture_dir)
    except harness.HarnessError as exc:  # pragma: no cover - defensive
        version = f"unavailable: {exc}"
    toolchain: dict[str, Any] = {"cc": cc, "flags": list(flags), "compiler_version": version}
    problem: str | None = None
    for key, name in (("harness_sha256", "harness.py"),
                      ("comparator_sha256", "compare.py"),
                      ("driver_sha256", "driver.c"),
                      ("interface_sha256", "synth.h")):
        try:
            toolchain[key] = receipts.file_sha256(slice_dir / name)
        except OSError as exc:
            toolchain[key] = None
            problem = problem or (
                f"toolchain metadata unavailable ({name}): {type(exc).__name__}: {exc}")
    return toolchain, problem


def _compiler_identity_known(toolchain: dict[str, Any]) -> bool:
    """Whether the recorded toolchain identifies its compiler unambiguously."""
    version = toolchain.get("compiler_version")
    if not isinstance(version, str) or not version:
        return False
    return version != "unknown" and not version.startswith("unavailable")


# --- binding provenance --------------------------------------------------------

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


def _require_sha256(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _HEX64.match(value):
        raise BindingError(f"{what} must be a 64-character lowercase hex digest")
    return value


def _require_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise BindingError(f"{what} is required")
    return value


def _require_mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BindingError(f"{what} must be an object")
    return value


def _require_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BindingError(f"{what} must be a non-negative integer")
    return value


def _require_counts(value: Any, what: str) -> dict[str, int]:
    """Validate a ``{name: count}`` census: string keys, non-negative integers."""
    mapping = _require_mapping(value, what)
    counts: dict[str, int] = {}
    for key, count in mapping.items():
        if not isinstance(key, str) or not key:
            raise BindingError(f"{what} keys must be non-empty strings")
        counts[key] = _require_int(count, f"{what}.{key}")
    return dict(sorted(counts.items()))


def _validate_binding(raw: Any) -> dict[str, Any]:
    """Validate an adapter's binding metadata against a fixed schema.

    The controller - not the model - folds this into the sealed receipt, so the
    shape is checked rather than trusted: an adapter that returns a partial or
    malformed object prevents acceptance instead of weakening the receipt.
    """
    binding = _require_mapping(raw, "binding metadata")
    adapter = _require_text(binding.get("adapter"), "binding.adapter")
    binary = _require_mapping(binding.get("binary"), "binding.binary")
    model = _require_mapping(binding.get("model"), "binding.model")
    config = _require_mapping(binding.get("config"), "binding.config")
    events = _require_mapping(binding.get("events"), "binding.events")
    variant = model.get("variant")
    if variant is not None and not isinstance(variant, str):
        raise BindingError("binding.model.variant must be a string or null")
    denials = events.get("denials")
    if not isinstance(denials, list):
        raise BindingError("binding.events.denials must be a list")
    validated_denials: list[dict[str, Any]] = []
    for index, entry in enumerate(denials):
        item = _require_mapping(entry, f"binding.events.denials[{index}]")
        if item.get("decision") != "deny":
            raise BindingError(
                f"binding.events.denials[{index}].decision must be 'deny'")
        reason = item.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise BindingError(
                f"binding.events.denials[{index}].reason must be a string or null")
        validated_denials.append({
            "action": _require_text(item.get("action"), f"binding.events.denials[{index}].action"),
            "resource": _require_text(item.get("resource"), f"binding.events.denials[{index}].resource"),
            "decision": "deny",
            "reason": reason,
        })
    completed = events.get("completed_tools")
    if not isinstance(completed, list):
        raise BindingError("binding.events.completed_tools must be a list")
    validated_tools: list[str] = []
    for index, tool in enumerate(completed):
        if not isinstance(tool, str) or not tool:
            raise BindingError(
                f"binding.events.completed_tools[{index}] must be a non-empty string")
        validated_tools.append(tool)
    return {
        "adapter": adapter,
        "binary": {
            "name": _require_text(binary.get("name"), "binding.binary.name"),
            "sha256": _require_sha256(binary.get("sha256"), "binding.binary.sha256"),
            "version": _require_text(binary.get("version"), "binding.binary.version"),
        },
        "model": {
            "provider": _require_text(model.get("provider"), "binding.model.provider"),
            "id": _require_text(model.get("id"), "binding.model.id"),
            "variant": variant,
        },
        "prompt_sha256": _require_sha256(binding.get("prompt_sha256"), "binding.prompt_sha256"),
        "config": {
            "template_sha256": _require_sha256(
                config.get("template_sha256"), "binding.config.template_sha256"),
            "materialized_sha256": _require_sha256(
                config.get("materialized_sha256"), "binding.config.materialized_sha256"),
        },
        "events": {
            "raw_sha256": _require_sha256(events.get("raw_sha256"), "binding.events.raw_sha256"),
            "raw_bytes": _require_int(events.get("raw_bytes"), "binding.events.raw_bytes"),
            "summary_sha256": _require_sha256(
                events.get("summary_sha256"), "binding.events.summary_sha256"),
            "tool_calls": _require_int(events.get("tool_calls"), "binding.events.tool_calls"),
            "oracle_invocations": _require_int(events.get("oracle_invocations"),
                                               "binding.events.oracle_invocations"),
            "oracle_by_tool": _require_counts(events.get("oracle_by_tool"),
                                              "binding.events.oracle_by_tool"),
            "oracle_denials": _require_counts(events.get("oracle_denials"),
                                              "binding.events.oracle_denials"),
            "completed_tools": validated_tools,
            "denials": validated_denials,
        },
    }


def _binding_metadata(adapter: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Collect and validate binding metadata, fail-closed on any fault."""
    getter = getattr(adapter, "receipt_metadata", None)
    if getter is None:
        return None, None
    try:
        raw = getter()
    except BindingError as exc:
        return None, f"binding metadata unavailable: {exc}"
    except Exception as exc:  # an adapter fault must not lose the receipt
        return None, f"binding metadata unavailable: {type(exc).__name__}: {exc}"
    if raw is None:
        return None, None
    try:
        return _validate_binding(raw), None
    except BindingError as exc:
        return None, f"binding metadata invalid: {exc}"


def _ledger_granted(ledger: Any) -> int | None:
    """The ledger's authoritative granted-call total, or ``None`` if unreadable."""
    try:
        return int(ledger.granted())
    except Exception:  # pragma: no cover - defensive
        return None


# --- repair-acceptance gates ---------------------------------------------------

#: The diagnostics a repair agent must have *completed* before its candidate may
#: be certified: one to consult the declared evidence, one to test what it wrote.
REQUIRED_TOOLS = ("oracle_inspect", "oracle_test")


def _test_records(run_dir: Path) -> list[dict[str, Any]]:
    """Every trusted ``oracle_test`` record stored in the run directory."""
    records: list[dict[str, Any]] = []
    for path in sorted((Path(run_dir) / TEST_DIR).glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # pragma: no cover - defensive
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _reconcile_calls(binding: dict[str, Any] | None, journal: list[dict[str, Any]],
                     granted: int | None, invocation_count: int | None) -> dict[str, Any]:
    """Reconcile the trusted journal against the agent-visible event census.

    The identity is ``N_invocations = N_admitted + N_rejected_before_admission +
    N_denied``, where a *denied* call is an OpenCode permission refusal (it never
    reaches the bridge) and a *rejected* call is a bridge refusal before
    admission (it does, and consumes no reservation). An admitted call that then
    fails stays admitted - it is never re-counted as a pre-admission rejection.
    An invocation that fits no class fails the gate: the accounting must close.
    """
    problems: list[str] = []
    admitted = [r for r in journal if r.get("decision") == budget.DECISION_ADMITTED]
    rejected = [r for r in journal if r.get("decision") == budget.DECISION_REJECTED]
    answerable = [r for r in rejected if r.get("reason") != budget.REASON_UNANSWERABLE]
    unanswerable = [r for r in rejected if r.get("reason") == budget.REASON_UNANSWERABLE]

    if granted is None:
        problems.append("the ledger's granted total could not be read")
    elif len(admitted) != granted:
        problems.append(
            f"the journal admits {len(admitted)} call(s) but the ledger granted {granted}")

    if binding is None:
        problems.append(
            "no adapter event census is available, so invocations cannot be classified")
        denied_total: int | None = None
        oracle_by_tool: dict[str, Any] = {}
        oracle_denials: dict[str, Any] = {}
        binding_invocations = None
    else:
        events = binding["events"]
        oracle_by_tool = dict(events.get("oracle_by_tool") or {})
        oracle_denials = dict(events.get("oracle_denials") or {})
        binding_invocations = events.get("oracle_invocations")
        denied_total = sum(int(v) for v in oracle_denials.values())
        if isinstance(binding_invocations, int) and \
                sum(int(v) for v in oracle_by_tool.values()) != binding_invocations:
            problems.append(
                f"the per-tool census sums to {sum(int(v) for v in oracle_by_tool.values())} "
                f"but the binding reports {binding_invocations} invocation(s)")
        reached = len(admitted) + len(answerable)
        if isinstance(binding_invocations, int) and reached + denied_total != binding_invocations:
            problems.append(
                f"bridge-reached calls ({reached}) plus permission denials "
                f"({denied_total}) do not account for {binding_invocations} oracle invocation(s)")
        if isinstance(invocation_count, int) and isinstance(binding_invocations, int) \
                and invocation_count != binding_invocations:
            problems.append(
                f"the sealed event census records {invocation_count} invocation(s) but the "
                f"binding reports {binding_invocations}")
        journal_by_tool = Counter(f"oracle_{r.get('tool')}" for r in admitted + answerable)
        stream_by_tool = Counter({str(k): int(v) for k, v in oracle_by_tool.items()})
        stream_by_tool.subtract({str(k): int(v) for k, v in oracle_denials.items()})
        expected = Counter({k: v for k, v in stream_by_tool.items() if v})
        if journal_by_tool != expected:
            problems.append(
                "per-tool reconciliation failed: the journal shows "
                f"{dict(sorted(journal_by_tool.items()))} but the event stream implies "
                f"{dict(sorted(expected.items()))}")

    return {
        "ok": not problems,
        "admitted": len(admitted),
        "rejected_before_admission": len(answerable),
        "denied": denied_total,
        "unanswerable": len(unanswerable),
        "reached": len(admitted) + len(answerable),
        "invocations": binding_invocations,
        "oracle_by_tool": dict(sorted(oracle_by_tool.items())),
        "oracle_denials": dict(sorted(oracle_denials.items())),
        "problems": problems,
    }


def _acceptance_gates(*, config: RunConfig, run_dir: Path, frozen: FrozenCandidate,
                      defective_sha: str, binding: dict[str, Any] | None,
                      binding_problem: str | None, journal: list[dict[str, Any]],
                      granted: int | None) -> dict[str, Any]:
    """Every precondition of a repair acceptance, evaluated *before* the holdout.

    Each gate is recorded whether it passes or fails, so the receipt shows what
    was checked rather than only what went wrong.
    """
    gates: dict[str, dict[str, Any]] = {}

    changed = frozen.sha256 != defective_sha
    gates["candidate_changed"] = {
        "ok": changed,
        "defective_sha256": defective_sha,
        "final_sha256": frozen.sha256,
        "detail": "" if changed else
                  "the final candidate is byte-identical to the defective input",
    }

    gates["binding"] = {
        "ok": binding is not None,
        "detail": "" if binding is not None else (binding_problem or "no binding metadata"),
    }

    completed = sorted(binding["events"]["completed_tools"]) if binding else []
    missing = [tool for tool in REQUIRED_TOOLS if tool not in completed]
    gates["required_tools"] = {
        "ok": not missing,
        "required": list(REQUIRED_TOOLS),
        "completed": completed,
        "detail": "" if not missing else "never completed: " + ", ".join(missing),
    }

    records = _test_records(run_dir)
    matching = [r for r in records if r.get("candidate_sha256") == frozen.sha256]
    attested = [r for r in matching if r.get("source_identity") == "attested"]
    if attested:
        detail = ""
    elif matching:
        detail = ("a test record names the frozen candidate but its source identity is "
                  "not attested")
    elif records:
        detail = "no trusted test record names the frozen candidate"
    else:
        detail = "the agent stored no trusted test record for this run"
    gates["trusted_test_record"] = {
        "ok": bool(attested),
        "records": len(records),
        "matching_records": len(matching),
        "attested_records": len(attested),
        "final_sha256": frozen.sha256,
        "detail": detail,
    }

    invocation_count = None if binding is None else binding["events"].get("oracle_invocations")
    gates["reconciliation"] = _reconcile_calls(binding, journal, granted, invocation_count)

    failed = [name for name, gate in gates.items() if not gate["ok"]]
    if not failed:
        outcome = "passed"
    elif "candidate_changed" in failed:
        # An unchanged candidate dominates: there was nothing to accept, whatever
        # else was also missing.
        outcome = "not_repaired"
    else:
        outcome = "gate_failed"
    return {"required": True, "gates": gates, "failed": failed, "outcome": outcome}


def _acceptance_not_required() -> dict[str, Any]:
    return {"required": False, "gates": {}, "failed": [], "outcome": "not_required"}


#: A run id is exactly one path component. The grammar is deliberately narrow: an
#: absolute id, a separator, or a ``.``/``..`` component would move the run
#: directory out from under the staging root, and the run directory is where the
#: agent's workspace is materialized.
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

#: The bridge generates an ``oracle_diff`` test id as ``f"{run_id}-{12 hex}"`` and
#: the tool schema/regex reject any id longer than 128 characters, so a run id much
#: over 96 would produce test ids the agent could never use. It also keeps the
#: derived directory and file names well within the 255-byte component limit.
RUN_ID_MAX_LENGTH = 96


def run_id_problems(run_id: Any) -> list[str]:
    """Why ``run_id`` is not a usable single path component, if it is not."""
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        return [f"run id {run_id!r} must be a non-empty single path component "
                "(letters, digits, '.', '_', '-')"]
    if len(run_id) > RUN_ID_MAX_LENGTH:
        return [f"run id {run_id!r} is longer than {RUN_ID_MAX_LENGTH} characters"]
    return []


def run_directory_problems(base_dir: Path, run_id: Any) -> list[str]:
    """Why the run directory is not a safe, fresh location, if it is not.

    The agent's workspace is materialized under the run directory, so the run
    directory must be a *fresh* child of the staging root: it must stay strictly
    inside the resolved base directory, it must not itself be a symlink, and a
    pre-existing run directory must not contain a symlink either. A ``workspace``
    symlink pointing back into the repository would otherwise defeat the isolation
    the staging check establishes, because ``materialize_workspace`` follows the
    link when it writes ``candidate.c``. The run never creates a symlink under its
    own directory, so refusing one costs nothing.
    """
    problems = run_id_problems(run_id)
    if problems:
        return problems
    base = Path(base_dir).resolve()
    candidate = base / run_id
    if candidate.is_symlink():
        return [f"the run directory {candidate} must not be a symlink"]
    resolved = candidate.resolve()
    if resolved == base or not resolved.is_relative_to(base):
        return [f"the run directory {resolved} must lie strictly inside {base}"]
    if candidate.is_dir():
        for child in sorted(candidate.iterdir()):
            if child.is_symlink():
                problems.append(
                    f"the run directory child {child.name!r} must not be a symlink")
    return problems


def run(config: RunConfig, adapter: AgentAdapter) -> RunResult:
    problems = run_directory_problems(config.base_dir, config.run_id)
    if problems:
        # A malformed run identity is not a failed evaluation - none has begun - so
        # this is a precondition failure, raised before a receipt can exist.
        raise BindingError("invalid run directory: " + "; ".join(problems))
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
            # Tool accounting is *ledger-authoritative*: a real agent calls the
            # Oracle through the MCP server process, so ``tools.calls`` (which
            # counts only in-process calls) would read zero. The shared SQLite
            # ledger is the exact count of agent-initiated calls in either case.
            granted_before = _ledger_granted(ledger)
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
            # The wall clock belongs to the controller, not to the adapter: the
            # deadline is re-asserted for *every* result, not only for ``done``.
            # This matters most on the final permitted attempt, where a late
            # ``progress`` would otherwise be misreported as an attempt-limit
            # exhaustion instead of a deadline violation.
            if supervisor.expired():
                result = AttemptResult(
                    "timeout",
                    f"run deadline exceeded during the agent attempt ({result.status})")
            candidate_sha = (receipts.file_sha256(workspace.candidate_path)
                             if workspace.candidate_path.is_file() else None)
            # Hashing the candidate is outside the adapter's execution time, so
            # the deadline is re-checked before a ``done`` result may be accepted.
            if result.status == "done" and supervisor.expired():
                result = AttemptResult(
                    "timeout", "run deadline exceeded before the candidate was accepted")
            granted_after = _ledger_granted(ledger)
            attempt_tool_calls = (
                granted_after - granted_before
                if granted_before is not None and granted_after is not None else None)
            attempts.append({
                "attempt": attempt,
                "status": result.status,
                "note": result.note,
                "tool_calls": attempt_tool_calls,
                "in_process_tool_calls": tools.calls - tool_calls_before,
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

    # Every expected trusted-host fault below is converted into an explicit
    # result object *before* the receipt is constructed, so receipt construction
    # cannot fail for an expected fault. The ``finally`` guarantees the ledger is
    # closed on every path, including an unexpected one.
    frozen: FrozenCandidate | None = None
    freeze_error: str | None = None
    visible: dict[str, Any] | None = None
    held_out: dict[str, Any] | None = None
    certification = "not_attempted"
    finalization_error: str | None = None
    journal_rows: list[dict[str, Any]] = []
    acceptance: dict[str, Any] = _acceptance_not_required()
    try:
        # No call can be in flight once every child group is reaped, so the journal
        # can be sealed and read as the run's complete trusted call record, and the
        # adapter's event census can be collected. Both feed the repair gates, which
        # run before any held-out spend.
        try:
            ledger.seal_pending()
            journal_rows = ledger.tool_calls()
        except Exception as exc:  # pragma: no cover - defensive
            finalization_error = f"{type(exc).__name__}: {exc}"
        binding, binding_problem = _binding_metadata(adapter)

        try:
            frozen = freeze_candidate(workspace.candidate_path, run_dir / "snapshot" / "final")
        except WorkspaceError as exc:
            # The agent's own doing - it deleted or replaced its candidate - or a
            # controller-state anomaly: a freeze failure, not a trusted-host
            # I/O fault.
            freeze_error = str(exc)
        except OSError as exc:
            # Trusted-host I/O: the snapshot could not be read, written or made
            # read-only. Recorded on the common finalization channel too.
            freeze_error = str(exc)
            finalization_error = finalization_error or f"{type(exc).__name__}: {exc}"

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
            except (harness.HarnessError, corpus.CorpusError, OSError) as exc:
                # A malformed or missing *visible* corpus - or any trusted-host
                # I/O fault while building or evaluating it - is an
                # infrastructure failure, not a candidate failure.
                visible = _failed_verdict(corpus.VISIBLE, compare.STATUS_HARNESS_ERROR, str(exc),
                                          config.fixtures_root)
                finalization_error = f"{type(exc).__name__}: {exc}"
            # Repair-acceptance gates. They are evaluated *after* the visible-only
            # diagnostic suite and *before* certification, so a run that fails a
            # gate never spends the held-out corpus - and an unchanged candidate is
            # reported as its own non-accepting outcome, not as a visible failure.
            if config.require_repair_acceptance:
                acceptance = _acceptance_gates(
                    config=config, run_dir=run_dir, frozen=frozen, defective_sha=defective_sha,
                    binding=binding, binding_problem=binding_problem, journal=journal_rows,
                    granted=_ledger_granted(ledger))
            gates_ok = not config.require_repair_acceptance or not acceptance["failed"]

            # Only a completed, non-aborted attempt whose repair gates are satisfied
            # and whose visible suite passes may spend the held-out corpus. The
            # gates are named first so a rejected run reports *why* the holdout was
            # withheld rather than being masked by a downstream visible failure.
            if abort_reason is not None:
                certification = f"skipped: agent aborted: {abort_reason}"
            elif not gates_ok:
                certification = ("skipped: acceptance gates failed ("
                                 + ", ".join(acceptance["failed"]) + ")")
            elif visible is None or visible["status"] != compare.STATUS_PASS:
                certification = "skipped: visible suite did not pass"
            else:
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
                except (harness.HarnessError, ToolError, corpus.CorpusError, OSError) as exc:
                    # A trusted-host fault while spending the holdout - including
                    # an I/O fault - is an infrastructure failure, never a
                    # candidate verdict.
                    held_out = _failed_verdict(corpus.HELD_OUT, compare.STATUS_HARNESS_ERROR, str(exc),
                                               config.fixtures_root)
                    certification = f"failed: {type(exc).__name__}"
                    finalization_error = f"{type(exc).__name__}: {exc}"

        candidate_verified = bool(
            visible is not None and visible["status"] == compare.STATUS_PASS
            and held_out is not None and held_out["status"] == compare.STATUS_PASS
        )

        toolchain, toolchain_problem = _toolchain(config.cc, harness.COMPILE_FLAGS,
                                                  deadline=finalize_deadline,
                                                  capture_dir=run_dir / "verdict" / "toolchain")
        # Incomplete provenance outranks a merely degraded probe: an unreadable
        # trusted source cannot be hashed into the receipt at all, so the run is
        # an infrastructure failure whatever the compiler reports. The verdict
        # evidence itself is preserved.
        if toolchain_problem is not None:
            finalization_error = finalization_error or toolchain_problem
            if certification == CERTIFICATION_RAN:
                certification = "failed: incomplete toolchain metadata"
        # An accepted result must identify its toolchain: an unidentifiable
        # compiler would silently weaken the receipt's reproducibility claim, so
        # it prevents acceptance instead.
        elif candidate_verified and not _compiler_identity_known(toolchain):
            finalization_error = finalization_error or (
                "compiler identity could not be established: "
                f"{toolchain.get('compiler_version')!r}")
            certification = "failed: unknown compiler identity"

        # The agent's external toolchain - if the adapter binds one - supplies the
        # pinned executable, model, configuration, prompt and event-evidence
        # identities. It was collected before any held-out spend (the repair gates
        # need its census); it is validated and folded here, before the receipt is
        # sealed, so a partial or malformed binding prevents acceptance.
        if binding is not None:
            toolchain["opencode"] = binding
        if binding_problem is not None:
            finalization_error = finalization_error or binding_problem
            if certification == CERTIFICATION_RAN:
                certification = "failed: incomplete binding metadata"

        try:
            granted: int | None = ledger.granted()
            limit: int | None = ledger.limit()
        except Exception as exc:  # pragma: no cover - defensive
            granted = limit = None
            finalization_error = finalization_error or f"{type(exc).__name__}: {exc}"

        # The journal is mandatory supporting evidence, so its publication is part
        # of finalization and must succeed *before* the terminal status is decided:
        # otherwise a run whose evidence could not be published would still be
        # classified as certified. A failure is an infrastructure fault - the suite
        # verdicts are preserved, but the run is aborted, not accepted.
        journal_text, journal_summary = budget.export_calls(journal_rows)
        try:
            (run_dir / budget.JOURNAL_EXPORT_FILE).write_text(journal_text, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            finalization_error = finalization_error or (
                f"the tool-call journal could not be exported: {exc}")
            journal_summary = None

        # Abort-first, then infrastructure, then the acceptance gates, then the
        # candidate verdicts. A trusted host that cannot complete the evaluation
        # must never be reported as a candidate failure, and it must never be
        # reported as acceptance. An unchanged candidate gets its own outcome
        # rather than masquerading as a visible failure.
        if frozen is None or abort_reason is not None or controller_error is not None:
            status = STATUS_ABORTED
        elif finalization_error is not None:
            status = STATUS_ABORTED
        elif acceptance["outcome"] == "not_repaired":
            status = STATUS_NOT_REPAIRED
        elif acceptance["failed"]:
            status = STATUS_ACCEPTANCE_GATE_FAILED
        elif candidate_verified:
            status = STATUS_CERTIFIED
        elif visible is None or visible["status"] != compare.STATUS_PASS:
            status = STATUS_VISIBLE_FAILED
        else:
            status = STATUS_HELD_OUT_FAILED

        visible_manifest = _manifest(corpus.VISIBLE, config.fixtures_root)
        held_manifest = held_out.get("corpus") if isinstance(held_out, dict) else None
        if held_manifest is None:
            held_manifest = _manifest(corpus.HELD_OUT, config.fixtures_root)

        # The snapshot is recorded relative to the staging root so a published
        # receipt carries no user-specific absolute path. Older receipts recorded
        # an absolute path; those stay as they were sealed.
        snapshot: str | None = None
        snapshot_base: str | None = None
        if frozen is not None:
            try:
                snapshot = str(frozen.path.relative_to(Path(config.base_dir)))
                snapshot_base = "base_dir"
            except ValueError:  # pragma: no cover - defensive
                snapshot = str(frozen.path)

        payload: dict[str, Any] = {
            "phase": PHASE,
            "run_id": config.run_id,
            "source": {
                "baseline_commit": config.baseline_commit,
                "declaration_commit": config.declaration_commit,
                "preregistration_path": config.preregistration_path,
                "defective_candidate_sha256": defective_sha,
                "final_candidate_sha256": frozen.sha256 if frozen else None,
                "snapshot": snapshot,
                "snapshot_base": snapshot_base,
                "freeze_error": freeze_error,
            },
            "corpus": {"visible": visible_manifest, "held_out": held_manifest},
            "toolchain": toolchain,
            "agent": {
                "adapter": adapter.name,
                "model": config.model,
                "tool_calls": granted,
                "in_process_tool_calls": tools.calls,
                "budget": {"granted": granted, "limit": limit},
                "journal": journal_summary,
            },
            "execution": {
                "limits": {
                    "budget_calls": config.budget_calls,
                    "max_attempts": config.max_attempts,
                    "wall_seconds": config.wall_seconds,
                    "finalization_seconds": config.finalization_seconds,
                },
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
            "acceptance": acceptance,
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
