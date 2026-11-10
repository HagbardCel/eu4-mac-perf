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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from automation import dsh_receipts as receipts
from automation import dsh_review as review
from automation import candidate_snapshot as snapshot
from automation import handoff as handoff_primitives
from automation.review_result import validate_review
from automation.dsh_campaign import (
    Sidecar,
    SidecarError,
    inside,
    infra_root,
    load_sidecar,
    path_allowed,
)
from automation.acceptance import run_accept
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
    """The three attributes ``automation.acceptance.run_accept`` reads.

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
    if inside(infra, candidate):
        problems.append("the infrastructure root is inside the candidate workspace")
    # A candidate inside the checkout is legitimate when it sits in .local/:
    # the agent's write sandbox is the session workspace, so a candidate outside
    # the checkout is unreachable to it. Anything else inside the checkout would
    # mean editing the trusted tree itself.
    if inside(candidate, infra) and not inside(candidate, infra / ".local"):
        problems.append(
            "a candidate inside the infrastructure checkout must live under .local/"
        )
    if not inside(campaign, infra):
        problems.append("campaign root is outside the infrastructure root")
    # The checker is not required to live inside the campaign directory: the
    # campaign itself states the accept command, so a sidecar cannot inject one,
    # and campaigns legitimately reuse a shared checker.
    # Anti-shadowing is a property of what Python actually imported, not of
    # what files exist: a candidate worktree of this very repository legitimately
    # contains automation/dsh_bridge.py. What must never happen is the bridge
    # itself importing that copy.
    import automation as automation_package  # noqa: PLC0415

    package_dir = Path(automation_package.__file__).resolve().parent
    if inside(package_dir, candidate) or candidate == package_dir:
        problems.append(
            f"the running automation package ({package_dir}) is inside the "
            "candidate workspace, so the bridge could be shadowed"
        )

    # A host that declares its effective workspace is held to it; one that does
    # not is recorded as unverified rather than assumed correct.
    declared = os.environ.get("EU4_DSH_WORKSPACE")
    if declared is not None and Path(declared).resolve() != candidate:
        problems.append(
            f"declared DSH workspace {declared} is not the campaign workspace {candidate}"
        )
    # The bridge cannot observe DSH's sandbox mode: nothing exports it, and an
    # environment variable would not be trustworthy anyway. What is enforced is
    # containment (the candidate must be reachable) plus the trusted-module
    # digests above, which detect edits to the machinery this path executes.
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


TRUSTED_MODULES = (
    "acceptance",
    "dsh_bridge",
    "dsh_receipts",
    "dsh_review",
    "campaign_contract",
    "candidate_snapshot",
    "dsh_campaign",
    "handoff",
    "review_result",
    "state",
    "verification_manifest",
)


def _trusted_modules_digest() -> str:
    """Digest the modules this path executes, from the running copies.

    The infrastructure *commit* only pins what is committed. A session whose
    workspace is the checkout can edit these files, and an uncommitted edit would
    otherwise change what the "fresh trusted checker run" executes without any
    identity drift.
    """
    import importlib.util

    rows = []
    for name in TRUSTED_MODULES:
        try:
            spec = importlib.util.find_spec(f"automation.{name}")
        except (ImportError, AttributeError, ValueError):
            rows.append({"module": name, "sha256": None})
            continue
        origin = Path(spec.origin).resolve() if spec and spec.origin else None
        rows.append({
            "module": name,
            "sha256": receipts.digest_file(origin)
            if origin is not None and origin.is_file() else None,
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
        "trusted_modules_digest": _trusted_modules_digest(),
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
    "trusted_modules_digest",
    "infrastructure_commit",
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
    """Create the identity anchor once, then verify it on every later call.

    The plugin calls this before each check so it can report the workspace, so it
    must not re-anchor: rewriting identity.json on every call would compare the
    current identity to itself and make the anchor meaningless. If an anchor
    exists and the trusted material no longer matches it, this refuses.
    """
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
    existing = receipts.read_json(stored.identity)
    if existing is None and not getattr(args, "create_anchor", False):
        # The plugin calls prepare before every check. If that call could create
        # the anchor, a session started in the wrong directory could anchor
        # whatever it had already edited. Only the launcher creates it.
        raise Refusal(
            "not_prepared",
            "no identity anchor for this campaign; start the campaign through "
            "dsh/scripts/run.sh --campaign (or run-campaign.sh) so the anchor is "
            "created from a prepared state",
        )
    if existing is not None:
        anchored = existing.get("identities") or {}
        _check_anchor(anchored, identities)
    else:
        receipts.write_identity(sidecar.run_root, {
            "schema": "eu4-dsh-identity/v1",
            "campaign_id": sidecar.campaign_id,
            "prepared_at": _now(),
            "execution_roots": roots,
            "identities": identities,
            "workspace": str(sidecar.workspace),
            "run_root": str(sidecar.run_root),
        })

    emit({
        "ok": True,
        "operation": "prepare",
        "campaign_id": sidecar.campaign_id,
        "objective": sidecar.objective_text,
        "require_independent_review": sidecar.require_independent_review,
        "execution_roots": roots,
        "workspace": str(sidecar.workspace),
        "run_root": str(sidecar.run_root),
        "identities": identities,
        "identity_digest": receipts.identity_digest(identities),
        "anchored_now": existing is None,
    })
    return EXIT_OK


def op_validate(args) -> int:
    """Run the trusted checker once and record the qualification.

    No attempt history, retry scheduler or budget: a caller that wants another
    check asks again, and DSH's own round cap bounds the campaign.
    """
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "validate", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy",
              "goal_policy": "unavailable", "detail": str(exc)})
        return EXIT_OK
    try:
        infra_commit = _git(infra_root(), "rev-parse", "HEAD")
        before = _identity(sidecar, infra_commit)
        _check_anchor(_prepared_identities(sidecar), before)
        digest = before["candidate"]["digest"]

        started = time.monotonic()
        outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        duration_ms = int((time.monotonic() - started) * 1000)

        after = _identity(sidecar, infra_commit)
        if after != before:
            changed = [key for key in before if before[key] != after.get(key)]
            raise Refusal("identity_drift",
                          "identity changed during the checker run: " + ", ".join(changed))

        classification, kind, verdict = classify(outcome)
        diagnostics = str(outcome.get("diagnostics") or "")
        receipt = {
            "schema": receipts.SCHEMA,
            "identities": before,
            "outcome": {
                "checker_verdict": verdict,
                "bridge_classification": classification,
                "kind": kind,
                "diagnostics_digest": receipts.digest_text(diagnostics),
                "diagnostics_excerpt": diagnostics[:2000],
                "exit_code": outcome.get("exit_code"),
                "duration_ms": duration_ms,
            },
            "session": {"session_id": args.session_id or None},
            "created_at": _now(),
        }
        path, sealed = receipts.write_receipt(sidecar.run_root, receipt)
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


def _oracle_digest(sidecar: Sidecar, identities: dict) -> str:
    """What the reviewer must hold the candidate to.

    A campaign with a verification manifest uses its digest. One without uses
    the sealed evidence-input digest -- but only when the sidecar actually
    declares evidence. Without that, ``evidence_inputs_digest`` is the digest of
    an empty list: a meaningless identity dressed up as an oracle. A live
    reviewer caught exactly this and returned ``unresolved`` naming it, so an
    undeclared evidence identity now refuses the review instead of being passed
    off as one.
    """
    manifest = identities.get("verification_manifest_digest")
    if manifest:
        return manifest
    if sidecar.evidence_paths:
        return identities.get("evidence_inputs_digest") or ""
    return ""


def _freeze_once(sidecar: Sidecar, identities: dict) -> dict:
    """Freeze the validated candidate once; reuse it while the identity holds."""
    path = sidecar.run_root / "freeze.json"
    digest = receipts.identity_digest(identities)
    existing = receipts.read_json(path)
    if existing and existing.get("identity_digest") == digest:
        return existing["frozen"]
    frozen = _freeze(sidecar)
    receipts.atomic_write(path, receipts.canonical_json({
        "schema": "eu4-dsh-freeze/v1",
        "campaign_id": sidecar.campaign_id,
        "identity_digest": digest,
        "frozen": frozen,
        "created_at": _now(),
    }))
    return frozen


def _drop_worktree(worktree: Path) -> None:
    if not worktree.exists():
        return
    subprocess.run(
        ["git", "-C", str(infra_root()), "worktree", "remove", "--force",
         "--force", str(worktree)],
        capture_output=True, text=True, check=False,
    )
    shutil.rmtree(worktree, ignore_errors=True)


def _review_paths(sidecar: Sidecar) -> dict:
    base = sidecar.run_root / "review"
    report = base / "review.json"
    return {
        "base": base,
        "report": report,
        "receipt": handoff_primitives.reviewer_receipt_path_for(report),
        "invocation": handoff_primitives.reviewer_invocation_path_for(report),
        "worktree": base / "worktree",
    }


def _review_problem(sidecar: Sidecar, frozen: dict, *, require_live: bool = True) -> list[str]:
    """Whether an existing review qualifies this frozen candidate."""
    paths = _review_paths(sidecar)
    if not paths["report"].is_file():
        return ["no independent review report exists for this candidate"]
    try:
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"review report unreadable: {exc}"]
    oracle = _oracle_digest(
        sidecar,
        (receipts.load_current_receipt(sidecar.run_root) or {}).get("identities") or {},
    )
    problems = validate_review(
        report,
        expected_candidate_commit=frozen.get("commit") or None,
        expected_oracle_manifest_sha256=oracle or None,
    )
    if problems:
        return [f"review report invalid: {p}" for p in problems[:4]]
    if report.get("outcome") != "qualified":
        findings = report.get("findings") or []
        detail = ", ".join(str(f.get("id")) for f in findings[:4]) or "none"
        return [f"review outcome was {report.get('outcome')} (findings: {detail})"]
    if not paths["receipt"].is_file():
        return ["the reviewer receipt is missing"]
    receipt = handoff_primitives.load_reviewer_receipt(paths["receipt"])
    report_sha = receipts.digest_file(paths["report"])
    problems = handoff_primitives.verify_reviewer_receipt(
        receipt,
        campaign_id=sidecar.campaign_id,
        candidate_commit=frozen.get("commit") or "",
        oracle_digest=oracle,
        report_sha256=report_sha,
        require_live=require_live,
    )
    return problems[:4]


CHECKER_IMPORT_RE = None


def _checker_imports(checker: Path) -> tuple[list[dict], list[str]]:
    """Which ``automation.*`` modules the checker will actually resolve.

    The checker runs from the trusted campaign root, so it imports the modern
    helpers rather than the ones from its own historical commit. That is a real
    semantic difference for an old campaign, so it is recorded rather than
    assumed harmless.
    """
    import importlib.util
    import re

    pattern = re.compile(r"automation[./]([a-zA-Z_][a-zA-Z0-9_]*)")
    text = checker.read_text(encoding="utf-8", errors="replace")
    names = sorted(set(pattern.findall(text)))
    rows: list[dict] = []
    outside: list[str] = []
    infra = infra_root().resolve()
    for name in names:
        try:
            spec = importlib.util.find_spec(f"automation.{name}")
        except (ImportError, AttributeError, ValueError):
            rows.append({"module": name, "resolved": None, "sha256": None})
            outside.append(name)
            continue
        origin = Path(spec.origin).resolve() if spec and spec.origin else None
        rows.append({
            "module": name,
            "resolved": str(origin) if origin else None,
            "sha256": receipts.digest_file(origin) if origin and origin.is_file() else None,
            "inside_infrastructure": bool(origin) and inside(origin, infra),
        })
        if origin and not inside(origin, infra):
            outside.append(name)
    return rows, outside


def op_checker_compat(args) -> int:
    """Preflight a campaign's checker before DSH relies on it.

    A historical campaign predates this bridge, and its checker may assume a
    campaign directory, pin or run-root layout that a sidecar cannot override.
    This records the checker's identity, the helpers it actually imports, and a
    baseline run against a pristine copy of the seed.
    """
    import tempfile

    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)

    rows, outside = _checker_imports(sidecar.checker)
    problems: list[str] = []
    if outside:
        problems.append(
            "checker imports automation helpers from outside the trusted checkout: "
            + ", ".join(outside)
        )

    baseline = None
    verdict = None
    added_worktree = None
    with tempfile.TemporaryDirectory(prefix="eu4-dsh-compat-") as tmp:
        workspace = Path(tmp) / "workspace"
        if sidecar.seed is not None:
            shutil.copytree(sidecar.seed, workspace)
        elif sidecar.mode == "worktree" and sidecar.candidate_baseline_commit:
            # A worktree-pinned campaign has no seed: its pristine baseline is
            # the pinned commit itself.
            added = subprocess.run(
                ["git", "-C", str(infra_root()), "worktree", "add", "--detach",
                 str(workspace), sidecar.candidate_baseline_commit],
                capture_output=True, text=True, check=False,
            )
            if added.returncode != 0:
                problems.append(
                    "could not materialize the pinned baseline for the compat run: "
                    + (added.stderr.strip() or "git worktree add failed")
                )
                workspace = None
            else:
                added_worktree = workspace
        else:
            problems.append("checker-compat needs a seed or a worktree pin")
            workspace = None

        outcome = None
        if workspace is not None:
            try:
                outcome = run_accept(_AcceptShim(sidecar), workspace)
            finally:
                # A temporary worktree is still a registered git worktree.
                if added_worktree is not None:
                    subprocess.run(
                        ["git", "-C", str(infra_root()), "worktree", "remove",
                         "--force", "--force", str(added_worktree)],
                        capture_output=True,
                    )
                    shutil.rmtree(added_worktree, ignore_errors=True)

        if outcome is not None:
            baseline = {"ok": outcome.get("ok"),
                        "exit_code": outcome.get("exit_code"),
                        "verdict": outcome.get("verdict"),
                        "diagnostics": str(outcome.get("diagnostics") or "")[:500]}
            if not outcome.get("ok"):
                problems.append(
                    "the checker produced no verdict on the pristine baseline: "
                    + str(outcome.get("diagnostics"))
                )
            else:
                verdict = outcome.get("verdict")
                if verdict == "pass":
                    problems.append(
                        "the checker already passes on the pristine baseline, so it "
                        "cannot distinguish a candidate from it"
                    )
                elif verdict not in ("fail", "inconclusive"):
                    # "unavailable" or "blocked" on the pristine baseline means
                    # the checker or its evidence does not match the pin, so no
                    # candidate could ever be judged against it.
                    problems.append(
                        f"the pristine baseline produced {verdict!r} rather than a "
                        "remediable verdict, so this checker cannot judge a candidate"
                    )

    compat = "ok" if not problems else "unsupported"
    emit({
        "ok": compat == "ok",
        "operation": "checker-compat",
        "campaign_id": sidecar.campaign_id,
        "compat_verdict": compat,
        "checker": str(sidecar.checker),
        "checker_sha256": receipts.digest_file(sidecar.checker),
        "trusted_modules_digest": _trusted_modules_digest(),
        "imports": rows,
        "baseline": baseline,
        "problems": problems,
    })
    return EXIT_OK if compat == "ok" else EXIT_INTEGRITY


def op_review_run(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "review-run", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy", "detail": str(exc)})
        return EXIT_OK
    try:
        current = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        _check_anchor(_prepared_identities(sidecar), current)
        receipt = receipts.load_current_receipt(sidecar.run_root)
        if receipt is None or (receipt.get("outcome") or {}).get("checker_verdict") != "pass":
            raise Refusal("not_qualified", "no passing validation to review")
        frozen = _freeze_once(sidecar, current)
        if not _oracle_digest(sidecar, current):
            raise Refusal(
                "review_identity_missing",
                "this campaign declares no verification manifest and no evidence "
                "paths, so a review could not be bound to any oracle identity",
            )
        if not frozen.get("commit"):
            raise Refusal(
                "review_needs_commit",
                "independent review needs a worktree candidate pinned to a commit; "
                "a seed candidate has no commit for a reviewer to check out",
            )
        paths = _review_paths(sidecar)
        paths["base"].mkdir(parents=True, exist_ok=True)
        # The reviewer worktree lives and dies with this operation: no separate
        # preparation or cleanup step, and no stale checkout between attempts.
        _drop_worktree(paths["worktree"])
        review.prepare_worktree(
            infra_root().resolve(), paths["worktree"], frozen["commit"]
        )
        oracle = _oracle_digest(sidecar, current)
        handoff_primitives.write_reviewer_invocation(
            paths["invocation"],
            campaign_id=sidecar.campaign_id,
            candidate_commit=frozen["commit"],
            oracle_digest=oracle,
            report_path=paths["report"],
            worktree=paths["worktree"],
        )
        task = review.build_task(
            frozen["commit"], oracle or None,
            "Review the candidate for correctness against the pinned evidence. "
            "Reject it if it modifies protected evidence, invents observations, "
            "or claims more than its checker establishes.",
        )
        command = review.reviewer_command(args.reviewer_cmd, task)
        mode = "scripted" if args.reviewer_cmd else "live"
        try:
            outcome = review.run_reviewer(
                command, cwd=paths["worktree"], timeout=args.timeout
            )
        except review.ReviewError as exc:
            # Not an integrity refusal: nothing was proven about the candidate,
            # the reviewer simply could not produce a result. Report it on the
            # infrastructure axis so the goal pauses instead of being blocked.
            emit({
                "ok": False,
                "operation": "review-run",
                "campaign_id": sidecar.campaign_id,
                "classification": "infrastructure_error",
                "kind": "review_failed",
                "goal_policy": "unavailable",
                "detail": str(exc),
                "candidate_commit": frozen["commit"],
            })
            return EXIT_OK
        if outcome["exit_code"] != 0:
            # The reviewer crashed; nothing was decided about the candidate.
            emit({
                "ok": False, "operation": "review-run",
                "campaign_id": sidecar.campaign_id,
                "classification": "infrastructure_error", "kind": "review_failed",
                "goal_policy": "unavailable",
                "detail": f"the reviewer exited {outcome['exit_code']}: "
                          f"{outcome['stderr'][:400]}",
            })
            return EXIT_OK
        payload = review.extract_review_payload(outcome["stdout"])
        if payload is None:
            raise Refusal("review_unparseable",
                          "no eu4-review/v1 object in the reviewer output")
        session_id = review.session_id_from(outcome["stdout"])
        if not session_id:
            raise Refusal(
                "review_identity",
                "the reviewer output carries no session identity; a receipt "
                "cannot be written without the host-observed session",
            )

        problems = validate_review(
            payload,
            expected_candidate_commit=frozen["commit"],
            expected_oracle_manifest_sha256=oracle or None,
        )
        receipts.atomic_write(paths["report"], receipts.canonical_json(payload))
        if problems:
            emit({
                "ok": False, "operation": "review-run",
                "campaign_id": sidecar.campaign_id,
                "classification": "integrity_refusal", "kind": "review_invalid",
                "detail": "; ".join(problems[:6]),
                "report": str(paths["report"]),
            })
            return EXIT_INTEGRITY

        report_sha = receipts.digest_file(paths["report"])
        handoff_primitives.write_reviewer_receipt(
            paths["receipt"],
            campaign_id=sidecar.campaign_id,
            candidate_commit=frozen["commit"],
            oracle_digest=oracle,
            reviewer_session_id=session_id,
            runner_status="complete",
            report_sha256=report_sha,
            qualification_mode=mode,
        )
        # The report and receipt live outside the worktree, so the checkout is
        # no longer needed.
        _drop_worktree(paths["worktree"])
        emit({
            "ok": True, "operation": "review-run",
            "campaign_id": sidecar.campaign_id,
            "candidate_commit": frozen["commit"],
            "outcome": payload.get("outcome"),
            "findings": len(payload.get("findings") or []),
            "reviewer_session_id": session_id,
            "qualification_mode": mode,
            "report": str(paths["report"]),
            "receipt": str(paths["receipt"]),
            # A completed review task is not the same as a qualified candidate.
            "qualified": payload.get("outcome") == "qualified",
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def op_review_verify(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    _setup(sidecar.run_root)
    current = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
    _check_anchor(_prepared_identities(sidecar), current)
    frozen = (_freeze_or_existing(sidecar, current))
    # Strict by default: a scripted (test double) receipt never authorizes
    # completion. --allow-scripted exists so the pipeline itself can be
    # inspected without a live model run.
    problems = _review_problem(
        sidecar, frozen, require_live=not getattr(args, "allow_scripted", False)
    )
    emit({
        "ok": not problems,
        "operation": "review-verify",
        "campaign_id": sidecar.campaign_id,
        "candidate_commit": frozen.get("commit"),
        "qualified": not problems,
        "problems": problems,
    })
    return EXIT_OK if not problems else EXIT_INTEGRITY


def _freeze_or_existing(sidecar: Sidecar, identities: dict) -> dict:
    existing = receipts.read_json(sidecar.run_root / "freeze.json")
    digest = receipts.identity_digest(identities)
    if existing and existing.get("identity_digest") == digest:
        return existing["frozen"]
    return _freeze_once(sidecar, identities)


def op_finalize(args) -> int:
    """Confirm the candidate with a fresh checker run, then freeze and gate.

    A stored pass is not authority: it can be edited by anything that can write
    the run root. The checker is re-run here and only that result may complete a
    goal.
    """
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "finalize", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy", "detail": str(exc)})
        return EXIT_OK
    try:
        current = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        _check_anchor(_prepared_identities(sidecar), current)

        receipt = receipts.load_current_receipt(sidecar.run_root)
        if receipt is None:
            raise Refusal("not_qualified", "no validation record; run validate first")
        problems = receipts.verify_receipt_digest(receipt)
        if problems:
            raise Refusal("receipt_corrupt", "; ".join(problems))
        stored_ids = receipt.get("identities") or {}
        if receipts.identity_digest(stored_ids) != receipts.identity_digest(current):
            raise Refusal("stale",
                          "the qualification does not describe the current candidate; "
                          "re-run validate")
        if (receipt.get("outcome") or {}).get("checker_verdict") != "pass":
            raise Refusal("not_qualified", "the latest validation did not pass")

        started = time.monotonic()
        outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        duration_ms = int((time.monotonic() - started) * 1000)
        classification, kind, verdict = classify(outcome)

        after = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        if after != current:
            raise Refusal("identity_drift", "identity changed during the finalization run")

        if classification != "verdict" or verdict != "pass":
            fresh = dict(receipt)
            fresh.pop("receipt_digest", None)
            fresh["outcome"] = {
                "checker_verdict": verdict,
                "bridge_classification": classification,
                "kind": kind,
                "diagnostics_digest": receipts.digest_text(str(outcome.get("diagnostics") or "")),
                "diagnostics_excerpt": str(outcome.get("diagnostics") or "")[:2000],
                "exit_code": outcome.get("exit_code"),
                "duration_ms": duration_ms,
            }
            receipts.write_receipt(sidecar.run_root, fresh)
            raise Refusal(
                "fresh_validation_failed",
                "the confirmation run for this candidate returned "
                f"{verdict or classification}; a stored pass cannot authorize completion",
            )

        frozen = _freeze_once(sidecar, current)
        if sidecar.require_independent_review:
            problems = _review_problem(sidecar, frozen, require_live=True)
            if problems:
                raise Refusal("review_required", "; ".join(problems))

        record = {
            "schema": "eu4-dsh-finalization/v1",
            "campaign_id": sidecar.campaign_id,
            "identity_digest": receipts.identity_digest(current),
            "frozen_candidate": frozen,
            "fresh_verdict": verdict,
            "duration_ms": duration_ms,
            "session": {"session_id": getattr(args, "session_id", None)},
            "created_at": _now(),
        }
        record_path = sidecar.run_root / "finalization.json"
        receipts.atomic_write(record_path, receipts.canonical_json(record))
        emit({
            "ok": True,
            "operation": "finalize",
            "campaign_id": sidecar.campaign_id,
            "classification": "verdict",
            "checker_verdict": verdict,
            "goal_policy": "qualified",
            "frozen_candidate": frozen,
            "identity_digest": receipts.identity_digest(current),
            "finalization": record_path.name,
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.dsh_bridge",
        description="Deterministic EU4 project operations for DSH campaigns.",
    )
    sub = parser.add_subparsers(dest="operation", required=True)

    for name in ("prepare", "validate", "finalize", "checker-compat",
                 "review-run", "review-verify"):
        child = sub.add_parser(name)
        child.add_argument("--sidecar", required=True)
        if name in ("validate", "finalize"):
            child.add_argument("--session-id", default=None)
        if name == "prepare":
            child.add_argument("--create-anchor", action="store_true")
        if name == "review-verify":
            child.add_argument("--allow-scripted", action="store_true")
        if name == "review-run":
            child.add_argument("--reviewer-cmd", default=None)
            child.add_argument("--timeout", type=float, default=900.0)
    return parser


OPERATIONS = {
    "prepare": op_prepare,
    "validate": op_validate,
    "finalize": op_finalize,
    "review-run": op_review_run,
    "review-verify": op_review_verify,
    "checker-compat": op_checker_compat,
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
    except Exception as exc:  # noqa: BLE001 - the CLI contract is "always JSON"
        # A caller (the DSH plugin) treats non-JSON as an infrastructure error,
        # so a traceback on stdout would be a silent fail-open risk. Report the
        # failure as a refusal and keep the traceback on stderr for debugging.
        import traceback

        traceback.print_exc(file=sys.stderr)
        emit({
            "ok": False,
            "operation": args.operation,
            "classification": "infrastructure_error",
            "kind": "internal_error",
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return EXIT_INTEGRITY


if __name__ == "__main__":
    raise SystemExit(main())
