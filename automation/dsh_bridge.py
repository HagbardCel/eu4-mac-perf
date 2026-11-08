"""Deterministic project operations for DSH campaigns.

This is not an agentic runner. DSH owns goal continuation; every operation here
is a bounded, idempotent filesystem or process action that either produces a
verdict or refuses to. All agent scheduling stays native to DSH.

Operations: ``prepare``, ``status``, ``validate``, ``goal-prompt``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from automation import dsh_receipts as receipts
from automation import dsh_snapshot as snapshot
from automation.dsh_campaign import (
    Sidecar,
    SidecarError,
    inside,
    infra_root,
    load_sidecar,
    path_allowed,
)
from automation.runner import run_accept
from automation.state import campaign_lock, ensure_workspace, require_worktree
from automation.verification_manifest import (
    digest_manifest,
    load_manifest,
    verify_manifest,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INTEGRITY = 3
INFRA_ERROR_KINDS = ("timeout", "truncated", "unreadable")

GOAL_POLICY = {
    "pass": "qualified",
    "fail": "remediate",
    "inconclusive": "investigate",
    "blocked": "block",
    "unavailable": "pause",
}


class Refusal(RuntimeError):
    """A fail-closed rejection that must never be reported as a verdict."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def emit(value: dict) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise Refusal("git", completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


class _AcceptShim:
    """The three attributes ``automation.runner.run_accept`` reads.

    Reusing the runner's execution and verdict parsing keeps one source of truth
    for timeout handling, the output cap and the verdict vocabulary.
    """

    def __init__(self, sidecar: Sidecar):
        self.root = sidecar.campaign_root
        self.accept = list(sidecar.accept)
        self.accept_timeout = sidecar.validation_timeout_seconds


def _roots(sidecar: Sidecar) -> dict:
    infra = infra_root().resolve()
    campaign = sidecar.campaign_root.resolve()
    candidate = sidecar.workspace.resolve()
    problems: list[str] = []
    if infra == candidate:
        problems.append("candidate root equals the infrastructure root")
    if inside(candidate, infra) or inside(infra, candidate):
        problems.append("candidate and infrastructure roots are nested")
    if not inside(campaign, infra):
        problems.append("campaign root is outside the infrastructure root")
    if not inside(sidecar.checker, campaign):
        problems.append("checker is outside the campaign root")
    if (candidate / "automation" / "dsh_bridge.py").exists():
        problems.append("candidate ships automation/dsh_bridge.py and could shadow it")

    # A host that declares its effective workspace is held to it; one that does
    # not is recorded as unverified rather than assumed correct.
    declared = os.environ.get("EU4_DSH_WORKSPACE")
    if declared is not None and Path(declared).resolve() != candidate:
        problems.append(
            f"declared DSH workspace {declared} is not the campaign workspace {candidate}"
        )
    sandbox = os.environ.get("EU4_DSH_SANDBOX_MODE")
    if sandbox == "danger-full-access":
        problems.append(
            "qualification requires workspace-write or stricter; "
            "danger-full-access provides no boundary for qualification state"
        )
    if problems:
        raise Refusal("execution_root", "; ".join(problems))
    return {
        "infrastructure_root": str(infra),
        "campaign_root": str(campaign),
        "candidate_root": str(candidate),
        "checker": str(sidecar.checker),
    }


def _changed_paths(workspace: Path) -> list[dict]:
    completed = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1", "-z",
         "--untracked-files=all"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise Refusal("worktree", completed.stderr.decode() or "git status failed")
    parts = completed.stdout.split(b"\0")
    found: list[dict] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        if not entry:
            break
        status = entry[:2].decode()
        found.append({"path": entry[3:].decode(), "status": status})
        index += 1
        if "R" in status or "C" in status:
            if index < len(parts) and parts[index]:
                found.append({"path": parts[index].decode(), "status": status + "-src"})
                index += 1
    return found


def _candidate(sidecar: Sidecar) -> dict:
    """Identify the candidate: content-addressed, and cheap for large worktrees."""
    infra = infra_root().resolve()
    if sidecar.mode == "worktree":
        commit = sidecar.candidate_baseline_commit or ""
        require_worktree(sidecar.workspace, infra, commit)
        detached = subprocess.run(
            ["git", "-C", str(sidecar.workspace), "symbolic-ref", "-q", "HEAD"],
            capture_output=True,
            check=False,
        )
        if detached.returncode == 0:
            raise Refusal("worktree", "candidate worktree is not detached")
        delta = snapshot.enumerate_delta(sidecar.workspace)
        problems = snapshot.validate_delta(
            delta,
            sidecar.allowed_paths,
            sidecar.forbidden_paths,
            allow_symlinks=sidecar.allow_symlinks,
        )
        if problems:
            raise Refusal("policy", "; ".join(problems[:6]))
        rows = [
            {
                "path": entry["path"],
                "old_path": entry.get("old_path"),
                "kind": entry["kind"],
                "mode": entry["mode"],
                "sha256": (
                    receipts.digest_file(sidecar.workspace / entry["path"])
                    if entry["exists"] and not entry["symlink"]
                    else None
                ),
            }
            for entry in delta
        ]
        payload = {"mode": "worktree", "commit": commit, "delta": rows}
        return {
            "mode": "worktree",
            "commit": commit,
            "digest": receipts.digest_text(receipts.canonical_json(payload)),
            "changed_paths": [entry["path"] for entry in delta],
        }

    digest, entries = receipts.tree_digest(sidecar.workspace)
    for entry in entries:
        _require_allowed(sidecar, entry["path"])
    return {
        "mode": "seed",
        "commit": None,
        "digest": digest,
        "changed_paths": [entry["path"] for entry in entries],
    }


def _require_allowed(sidecar: Sidecar, rel: str) -> None:
    if not path_allowed(rel, sidecar.allowed_paths):
        raise Refusal("policy", f"candidate path outside allowlist: {rel}")
    for pattern in sidecar.forbidden_paths:
        if pattern == rel or rel.startswith(pattern.rstrip("/") + "/"):
            raise Refusal("policy", f"frozen path modified: {rel}")


def _evidence_digest(sidecar: Sidecar) -> str:
    rows = []
    for pattern in sidecar.evidence_paths:
        matches = sorted(
            path for path in sidecar.workspace.glob(pattern) if path.is_file()
        )
        if not matches:
            rows.append({"pattern": pattern, "exists": False, "sha256": None})
            continue
        for path in matches:
            rows.append({
                "pattern": pattern,
                "path": path.relative_to(sidecar.workspace).as_posix(),
                "exists": True,
                "sha256": receipts.digest_file(path),
            })
    return receipts.digest_text(receipts.canonical_json(rows))


def _identity(sidecar: Sidecar, infra_commit: str) -> dict:
    manifest_digest = None
    if sidecar.verification_manifest:
        manifest = load_manifest(sidecar.verification_manifest)
        errors = verify_manifest(infra_root().resolve(), manifest)
        if errors:
            raise Refusal("manifest", "; ".join(errors[:6]))
        manifest_digest = digest_manifest(manifest)
    return {
        "campaign_id": sidecar.campaign_id,
        "source_campaign_sha256": sidecar.source_campaign_sha256,
        "sidecar_sha256": sidecar.sidecar_sha256,
        "infrastructure_commit": infra_commit,
        "candidate_baseline_commit": sidecar.candidate_baseline_commit,
        "policy_digest": sidecar.policy_digest,
        "objective_digest": sidecar.objective_digest,
        "checker_sha256": receipts.digest_file(sidecar.checker),
        "verification_manifest_digest": manifest_digest,
        "evidence_inputs_digest": _evidence_digest(sidecar),
        "candidate": _candidate(sidecar),
    }


def classify(outcome: dict) -> tuple[str, str | None, str | None]:
    """Map a ``run_accept`` result to (classification, kind, checker verdict)."""
    if outcome.get("ok"):
        verdict = outcome.get("verdict")
        if verdict == "pass" and outcome.get("exit_code") not in (0, None):
            return "infrastructure_error", "exit_code", None
        return "verdict", None, verdict
    detail = str(outcome.get("diagnostics") or "").strip()
    if detail in INFRA_ERROR_KINDS:
        kind = detail
    elif outcome.get("exit_code") not in (0, None):
        kind = "exit_code"
    else:
        kind = "unreadable"
    return "infrastructure_error", kind, None


def _goal_policy(classification: str, verdict: str | None) -> str:
    if classification == "verdict":
        return GOAL_POLICY.get(str(verdict), "pause")
    return "unavailable"


ANCHORED_IDENTITIES = (
    "checker_sha256",
    "sidecar_sha256",
    "source_campaign_sha256",
    "verification_manifest_digest",
    "policy_digest",
    "objective_digest",
    "candidate_baseline_commit",
)


def _prepared_identities(sidecar: Sidecar) -> dict:
    """Trusted material captured by ``prepare``; the anchor later runs compare to."""
    stored = receipts.read_json(receipts.paths(sidecar.run_root).identity)
    if not stored:
        raise Refusal("not_prepared", "run `prepare` before `validate`")
    identities = stored.get("identities")
    if not isinstance(identities, dict):
        raise Refusal("not_prepared", "identity.json carries no identities")
    return identities


def _check_anchor(prepared: dict, current: dict) -> None:
    drift = [key for key in ANCHORED_IDENTITIES if prepared.get(key) != current.get(key)]
    if drift:
        raise Refusal(
            "identity_modified",
            "protected material changed since prepare: " + ", ".join(drift),
        )


def _setup(run_root: Path) -> receipts.RunPaths:
    stored = receipts.paths(run_root)
    stored.ensure()
    return stored


def _workspace_ok(sidecar: Sidecar) -> bool | None:
    """True/False when the host declared its workspace, None when unverified."""
    declared = os.environ.get("EU4_DSH_WORKSPACE")
    if declared is None:
        return None
    return Path(declared).resolve() == sidecar.workspace.resolve()


def op_prepare(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    roots = _roots(sidecar)
    stored = _setup(sidecar.run_root)
    infra_commit = _git(infra_root(), "rev-parse", "HEAD")

    if sidecar.mode == "seed":
        ensure_workspace(sidecar.seed, sidecar.workspace)
    else:
        require_worktree(
            sidecar.workspace, infra_root().resolve(),
            sidecar.candidate_baseline_commit or "",
        )

    identities = _identity(sidecar, infra_commit)
    receipts.write_identity(sidecar.run_root, {
        "schema": "eu4-dsh-identity/v1",
        "campaign_id": sidecar.campaign_id,
        "prepared_at": _now(),
        "execution_roots": roots,
        "identities": identities,
        "workspace": str(sidecar.workspace),
        "run_root": str(sidecar.run_root),
    })
    counters = receipts.load_counters(sidecar.run_root)
    if counters["started_at"] is None:
        counters["started_at"] = _now()
    receipts.save_counters(sidecar.run_root, counters)
    receipts.append_event(sidecar.run_root, {
        "ts": _now(), "event": "prepare", "campaign_id": sidecar.campaign_id,
        "infrastructure_commit": infra_commit,
    })
    emit({
        "ok": True,
        "operation": "prepare",
        "campaign_id": sidecar.campaign_id,
        "execution_roots": roots,
        "workspace": str(sidecar.workspace),
        "run_root": str(sidecar.run_root),
        "identities": identities,
        "identity_digest": receipts.identity_digest(identities),
    })
    return EXIT_OK


def op_status(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    receipt = receipts.load_current_receipt(sidecar.run_root)
    counters = receipts.load_counters(sidecar.run_root)
    stale: list[str] = []
    fresh = None
    if receipt:
        try:
            fresh = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        except Refusal as exc:
            stale.append(f"identity unavailable: {exc}")
        else:
            stored_ids = receipt.get("identities") or {}
            for key in (
                "source_campaign_sha256", "sidecar_sha256", "policy_digest",
                "objective_digest", "checker_sha256",
                "verification_manifest_digest", "evidence_inputs_digest",
                "candidate_baseline_commit",
            ):
                if stored_ids.get(key) != fresh.get(key):
                    stale.append(key)
            if (stored_ids.get("candidate") or {}).get("digest") != fresh["candidate"]["digest"]:
                stale.append("candidate.digest")
    emit({
        "ok": True,
        "operation": "status",
        "campaign_id": sidecar.campaign_id,
        "workspace": str(sidecar.workspace),
        "run_root": str(sidecar.run_root),
        "counters": counters,
        "receipt": None if receipt is None else {
            "path": str(receipts.paths(sidecar.run_root).current),
            "receipt_digest": receipt.get("receipt_digest"),
            "outcome": receipt.get("outcome"),
            "validation_round": receipt.get("validation_round"),
        },
        "stale": stale,
        "current": bool(receipt) and not stale,
        "limits": {
            "max_validations": sidecar.max_validations,
            "retry_mode": sidecar.retry_mode,
            "confirmation_rounds": sidecar.confirmation_rounds,
            "max_identical_retries": sidecar.max_identical_retries,
        },
    })
    return EXIT_OK


def op_validate(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)

    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({
            "ok": True, "operation": "validate", "campaign_id": sidecar.campaign_id,
            "classification": "policy_stop", "kind": "busy",
            "goal_policy": "unavailable", "detail": str(exc),
        })
        return EXIT_OK

    try:
        counters = receipts.load_counters(sidecar.run_root)
        infra_commit = _git(infra_root(), "rev-parse", "HEAD")
        before = _identity(sidecar, infra_commit)
        _check_anchor(_prepared_identities(sidecar), before)
        digest = before["candidate"]["digest"]

        # Idempotency applies to success only: an identical, verified pass
        # receipt is reused instead of re-running the checker. Failure
        # repetition is governed by the checker-aware retry policy below.
        prior = receipts.load_current_receipt(sidecar.run_root)
        if prior is not None:
            problems = receipts.verify_receipt_digest(prior)
            if problems:
                raise Refusal("receipt_corrupt", "; ".join(problems))
            stored = prior.get("identities") or {}
            if (
                receipts.identity_digest(stored) == receipts.identity_digest(before)
                and (prior.get("outcome") or {}).get("checker_verdict") == "pass"
            ):
                emit({
                    "ok": True,
                    "operation": "validate",
                    "campaign_id": sidecar.campaign_id,
                    "classification": "verdict",
                    "kind": None,
                    "checker_verdict": "pass",
                    "goal_policy": "qualified",
                    "reused": True,
                    "diagnostics": (prior.get("outcome") or {}).get("diagnostics_excerpt"),
                    "receipt_digest": prior.get("receipt_digest"),
                    "identity_digest": receipts.identity_digest(before),
                    "candidate_digest": digest,
                    "validation_round": prior.get("validation_round"),
                })
                return EXIT_OK

        if counters["validations"] >= sidecar.max_validations:
            counters["policy_stops"] += 1
            receipts.save_counters(sidecar.run_root, counters)
            emit({
                "ok": True, "operation": "validate", "campaign_id": sidecar.campaign_id,
                "classification": "policy_stop", "kind": "validation_budget",
                "goal_policy": "unavailable",
                "detail": f"{counters['validations']}/{sidecar.max_validations} validations used",
            })
            return EXIT_OK

        identical = (
            counters["last_candidate_digest"] == digest
            and counters["last_verdict"] == "fail"
        )
        allowance = (
            sidecar.confirmation_rounds
            if sidecar.retry_mode == "stateful"
            else sidecar.max_identical_retries
        )
        if identical and counters["identical_retries"] >= allowance:
            counters["policy_stops"] += 1
            receipts.save_counters(sidecar.run_root, counters)
            receipts.append_event(sidecar.run_root, {
                "ts": _now(), "event": "policy_stop", "kind": "no_progress",
                "candidate_digest": digest,
            })
            emit({
                "ok": True, "operation": "validate", "campaign_id": sidecar.campaign_id,
                "classification": "policy_stop", "kind": "no_progress",
                "goal_policy": "unavailable",
                "detail": (
                    "the candidate is unchanged since the last identical failure and the "
                    "declared retry allowance is exhausted; change the candidate or report "
                    "a BLOCKER rather than re-running the same check"
                ),
                "candidate_digest": digest,
                "retry_mode": sidecar.retry_mode,
                "identical_retries": counters["identical_retries"],
            })
            return EXIT_OK

        started = time.monotonic()
        outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        duration_ms = int((time.monotonic() - started) * 1000)

        after = _identity(sidecar, infra_commit)
        if after != before:
            counters["integrity_refusals"] += 1
            receipts.save_counters(sidecar.run_root, counters)
            changed = [k for k in before if before[k] != after.get(k)]
            raise Refusal(
                "identity_drift",
                "identity changed during the checker run: " + ", ".join(changed),
            )

        classification, kind, verdict = classify(outcome)
        diagnostics = str(outcome.get("diagnostics") or "")
        round_number = counters["validations"] + 1
        receipt = {
            "schema": receipts.SCHEMA,
            "identities": before,
            # The bridge cannot observe DSH's sandbox. It records what the host
            # told it (if anything) and marks the assertion unverified rather
            # than claiming a mode it has not checked. M4 promotes a verified
            # assertion into a hard gate.
            "workspace_assertion": {
                "declared": str(sidecar.workspace),
                "effective": os.environ.get("EU4_DSH_WORKSPACE"),
                "cwd": str(Path.cwd()),
                "sandbox_mode": os.environ.get("EU4_DSH_SANDBOX_MODE"),
                "verified": os.environ.get("EU4_DSH_WORKSPACE") is not None,
                "ok": _workspace_ok(sidecar),
            },
            "outcome": {
                "checker_verdict": verdict,
                "bridge_classification": classification,
                "kind": kind,
                "diagnostics_digest": receipts.digest_text(diagnostics),
                "diagnostics_excerpt": diagnostics[:2000],
                "exit_code": outcome.get("exit_code"),
                "duration_ms": duration_ms,
            },
            "validation_round": round_number,
            "attempts": {
                "validations": round_number,
                "identical_retries": counters["identical_retries"] + (1 if identical else 0),
            },
            "session": {"session_id": args.session_id or None},
            "created_at": _now(),
        }
        path, sealed = receipts.write_receipt(sidecar.run_root, receipt)

        counters["validations"] = round_number
        counters["elapsed_seconds"] = round(
            counters["elapsed_seconds"] + duration_ms / 1000.0, 6
        )
        counters["last_candidate_digest"] = digest
        counters["last_verdict"] = verdict
        counters["last_diagnostics_digest"] = receipt["outcome"]["diagnostics_digest"]
        if verdict == "pass":
            counters["identical_retries"] = 0
        elif identical:
            counters["identical_retries"] += 1
        else:
            counters["identical_retries"] = 0
        receipts.save_counters(sidecar.run_root, counters)
        receipts.append_event(sidecar.run_root, {
            "ts": _now(), "event": "validate", "classification": classification,
            "verdict": verdict, "kind": kind, "receipt": path.name,
        })

        emit({
            "ok": True,
            "operation": "validate",
            "campaign_id": sidecar.campaign_id,
            "classification": classification,
            "kind": kind,
            "checker_verdict": verdict,
            "goal_policy": _goal_policy(classification, verdict),
            "diagnostics": diagnostics,
            "exit_code": outcome.get("exit_code"),
            "duration_ms": duration_ms,
            "receipt": path.name,
            "receipt_digest": sealed["receipt_digest"],
            "identity_digest": receipts.identity_digest(before),
            "candidate_digest": digest,
            "validation_round": round_number,
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def _freeze(sidecar: Sidecar) -> dict:
    """Materialize the exact candidate delta as a commit and verify it.

    The snapshot must reproduce the change that was on disk — no more and no
    less — or the review evidence would describe a candidate nobody validated.
    """
    if sidecar.mode != "worktree":
        digest, entries = receipts.tree_digest(sidecar.workspace)
        return {"mode": "seed", "digest": digest, "entries": len(entries),
                "commit": None}

    infra = infra_root().resolve()
    baseline = sidecar.candidate_baseline_commit or ""
    delta = snapshot.enumerate_delta(sidecar.workspace)
    problems = snapshot.validate_delta(
        delta, sidecar.allowed_paths, sidecar.forbidden_paths,
        allow_symlinks=sidecar.allow_symlinks,
    )
    if problems:
        raise Refusal("policy", "; ".join(problems[:6]))
    before = snapshot.fingerprint(sidecar.workspace)
    built = snapshot.build_candidate(
        sidecar.workspace, infra, baseline, delta,
        message=f"eu4 DSH candidate snapshot ({sidecar.campaign_id})",
    )
    problems = snapshot.verify_round_trip(infra, baseline, built["candidate"], delta)
    if problems:
        raise Refusal("snapshot", "; ".join(problems[:6]))
    problems = snapshot.recheck(sidecar.workspace, before)
    if problems:
        raise Refusal("snapshot_moved", "; ".join(problems))
    ref = f"refs/dsh-candidate/{sidecar.campaign_id}/{built['candidate'][:12]}"
    _git(infra, "update-ref", ref, built["candidate"])
    return {
        "mode": "worktree",
        "commit": built["candidate"],
        "tree": built["tree"],
        "baseline": baseline,
        "ref": ref,
        "paths": built["paths"],
    }


def op_snapshot(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "snapshot", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy", "detail": str(exc)})
        return EXIT_OK
    try:
        current = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        _check_anchor(_prepared_identities(sidecar), current)
        frozen = _freeze(sidecar)
        receipts.append_journal(sidecar.run_root, {
            "kind": "snapshot",
            "campaign_id": sidecar.campaign_id,
            "candidate": frozen,
        })
        emit({
            "ok": True,
            "operation": "snapshot",
            "campaign_id": sidecar.campaign_id,
            "candidate": frozen,
            "candidate_digest": current["candidate"]["digest"],
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


FINALIZE_VALIDATION_LIMIT = 2


def op_finalize(args) -> int:
    """Confirm a candidate against a FRESH checker run, then report a freeze.

    A stored receipt is never authority: it can be edited by anything that can
    write the state root, and a self-hash cannot tell a real pass from a
    re-hashed forgery. The checker is therefore re-run here, and only that
    result can authorize completion.
    """
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored_paths = _setup(sidecar.run_root)

    try:
        lock = campaign_lock(stored_paths.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "finalize", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy", "detail": str(exc)})
        return EXIT_OK

    try:
        counters = receipts.load_counters(sidecar.run_root)
        infra_commit = _git(infra_root(), "rev-parse", "HEAD")
        current = _identity(sidecar, infra_commit)
        _check_anchor(_prepared_identities(sidecar), current)

        receipt = receipts.load_current_receipt(sidecar.run_root)
        if receipt is None:
            raise Refusal("not_qualified", "no validation receipt; run validate first")
        problems = receipts.verify_receipt_digest(receipt)
        if problems:
            raise Refusal("receipt_corrupt", "; ".join(problems))
        problems = receipts.journal_problem(sidecar.run_root, receipt)
        if problems:
            raise Refusal("journal_mismatch", "; ".join(problems[:4]))

        stored_ids = receipt.get("identities") or {}
        if receipts.identity_digest(stored_ids) != receipts.identity_digest(current):
            raise Refusal(
                "stale",
                "the qualification receipt does not describe the current candidate; "
                "re-run validate",
            )
        if (receipt.get("outcome") or {}).get("checker_verdict") != "pass":
            raise Refusal(
                "not_qualified",
                "the latest validation did not pass; there is nothing to finalize",
            )

        if counters["finalizations"] >= FINALIZE_VALIDATION_LIMIT:
            emit({"ok": True, "operation": "finalize",
                  "campaign_id": sidecar.campaign_id,
                  "classification": "policy_stop", "kind": "finalize_budget",
                  "detail": f"{counters['finalizations']}/{FINALIZE_VALIDATION_LIMIT} "
                            "finalization confirmations used"})
            return EXIT_OK

        started = time.monotonic()
        outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        duration_ms = int((time.monotonic() - started) * 1000)
        classification, kind, verdict = classify(outcome)

        after = _identity(sidecar, infra_commit)
        counters["finalizations"] += 1
        counters["last_finalize_verdict"] = verdict
        receipts.save_counters(sidecar.run_root, counters)

        if after != current:
            raise Refusal("identity_drift",
                          "identity changed during the finalization run")

        if classification != "verdict" or verdict != "pass":
            fresh = dict(receipt)
            fresh.pop("receipt_digest", None)
            fresh["outcome"] = {
                "checker_verdict": verdict,
                "bridge_classification": classification,
                "kind": kind,
                "diagnostics_digest": receipts.digest_text(
                    str(outcome.get("diagnostics") or "")),
                "diagnostics_excerpt": str(outcome.get("diagnostics") or "")[:2000],
                "exit_code": outcome.get("exit_code"),
                "duration_ms": duration_ms,
            }
            fresh["validation_round"] = counters["validations"]
            path, sealed = receipts.write_receipt(sidecar.run_root, fresh)
            receipts.append_event(sidecar.run_root, {
                "ts": _now(), "event": "finalize_refused",
                "verdict": verdict, "classification": classification,
            })
            raise Refusal(
                "fresh_validation_failed",
                "the confirmation run for this candidate returned "
                f"{verdict or classification}; a stored receipt cannot authorize "
                f"completion (see {path.name})",
            )

        frozen = _freeze(sidecar)
        record = {
            "schema": "eu4-dsh-finalization/v1",
            "frozen_candidate": frozen,
            "campaign_id": sidecar.campaign_id,
            "identity_digest": receipts.identity_digest(current),
            "candidate": current["candidate"],
            "fresh_receipt_digest": receipt.get("receipt_digest"),
            "fresh_verdict": verdict,
            "duration_ms": duration_ms,
            "session": {"session_id": getattr(args, "session_id", None)},
            "created_at": _now(),
        }
        record_path = sidecar.run_root / "finalization.json"
        receipts.atomic_write(
            record_path, receipts.canonical_json(record)
        )
        receipts.append_journal(sidecar.run_root, {
            "kind": "finalization",
            "campaign_id": sidecar.campaign_id,
            "identity_digest": receipts.identity_digest(current),
            "candidate_digest": current["candidate"]["digest"],
            "frozen_candidate": frozen,
        })
        receipts.append_event(sidecar.run_root, {
            "ts": _now(), "event": "finalized",
            "candidate_digest": current["candidate"]["digest"],
        })
        emit({
            "ok": True,
            "operation": "finalize",
            "campaign_id": sidecar.campaign_id,
            "classification": "verdict",
            "checker_verdict": verdict,
            "goal_policy": "qualified",
            "frozen_candidate": frozen,
            "identity_digest": receipts.identity_digest(current),
            "finalization": str(record_path.name),
            "finalizations_used": counters["finalizations"],
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def op_goal_prompt(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    objective = sidecar.objective_text.strip()
    emit({
        "operation": "goal-prompt",
        "campaign_id": sidecar.campaign_id,
        "max_goal_rounds": sidecar.max_rounds,
        "objective": (
            f"{objective}\n\n"
            f"Campaign: {sidecar.campaign_id}\n"
            f"Workspace: {sidecar.workspace}\n"
            "Completion is not self-certified. `update_goal(action=\"complete\")` is denied "
            "in this profile; call `eu4_finalize_candidate` once "
            "`eu4_validate_candidate` reports a checker pass."
        ),
    })
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.dsh_bridge",
        description="Deterministic EU4 project operations for DSH campaigns.",
    )
    sub = parser.add_subparsers(dest="operation", required=True)

    for name in ("prepare", "status", "validate", "snapshot", "finalize", "goal-prompt"):
        child = sub.add_parser(name)
        child.add_argument("--sidecar", required=True)
        if name in ("validate", "finalize"):
            child.add_argument("--session-id", default=None)
    return parser


OPERATIONS = {
    "prepare": op_prepare,
    "status": op_status,
    "validate": op_validate,
    "snapshot": op_snapshot,
    "finalize": op_finalize,
    "goal-prompt": op_goal_prompt,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return OPERATIONS[args.operation](args)
    except SidecarError as exc:
        emit({"ok": False, "operation": args.operation, "classification": "integrity_refusal",
              "kind": "sidecar", "detail": str(exc)})
        return EXIT_INTEGRITY
    except Refusal as exc:
        emit({"ok": False, "operation": args.operation,
              "classification": "integrity_refusal", "kind": exc.kind,
              "detail": exc.detail or str(exc)})
        return EXIT_INTEGRITY


if __name__ == "__main__":
    raise SystemExit(main())
