"""Deterministic project operations for DSH campaigns.

This is not an agentic runner. DSH owns goal continuation; every operation here
is a bounded, idempotent filesystem or process action that either produces a
verdict or refuses to. All agent scheduling stays native to DSH.

Operations: ``prepare``, ``status``, ``validate``, ``goal-prompt``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from automation import dsh_receipts as receipts
from automation import dsh_review as review
from automation import candidate_snapshot as snapshot
from automation import session_state
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

# Refusals an operator or candidate can resolve without ending the campaign.
# Everything else is terminal: the campaign cannot safely proceed, so the plugin
# pauses the goal through the native goal service.
PROBE_PREFIX = ".dsh-prepare-probe-"


REMEDIABLE_REFUSALS = frozenset({
    "fresh_validation_failed",  # the checker said no again; repair and retry
    "not_qualified",            # a validation has to run first
    "stale",                    # the qualification no longer matches; re-validate
})

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
        # Bind the checked campaign explicitly at the checker boundary.  The
        # event-20 checker used to fall back to its historical sidecar and to
        # accept an ambient trusted-root override; neither may redirect the
        # selected DSH campaign.
        self.accept_env = {
            "EU4_M7_CANDIDATE_SIDECAR": str(sidecar.sidecar_path),
        }
        self.accept_env_scrub = (
            "EU4_M7_CANDIDATE_SIDECAR",
            "EU4_M7_EVENT20_TRUSTED_ROOT",
        )


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
        residue = _registered_probe_names(sidecar.run_root, sidecar.workspace)
        delta = [entry for entry in snapshot.enumerate_delta(sidecar.workspace)
                 if not (entry.get("kind") == "add"
                         and not entry.get("old_path")
                         and _is_registered_probe(entry["path"], residue))]
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
                # A retargeted symlink keeps its path and mode, so the target
                # itself has to be part of the identity.
                "target_sha256": (
                    receipts.digest_text(os.readlink(sidecar.workspace / entry["path"]))
                    if entry["exists"] and entry["symlink"]
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

    _digest, entries = receipts.tree_digest(sidecar.workspace)
    # Only host-recorded probes are harness residue, never a prefix guess.
    residue = _registered_probe_names(sidecar.run_root, sidecar.workspace)
    entries = [entry for entry in entries
               if not _is_registered_probe(entry["path"], residue)]
    digest = receipts.digest_text(receipts.canonical_json(entries))
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


def _module_row(name: str) -> dict:
    import importlib.util

    try:
        spec = importlib.util.find_spec(f"automation.{name}")
    except (ImportError, AttributeError, ValueError):
        return {"module": name, "sha256": None}
    origin = Path(spec.origin).resolve() if spec and spec.origin else None
    return {
        "module": name,
        "sha256": receipts.digest_file(origin)
        if origin is not None and origin.is_file() else None,
    }


def _trusted_modules_digest(checker: Path, strict: bool = True) -> tuple[str, list[dict]]:
    """Digest everything the checker path executes.

    Two sources, so there is no second allowlist to maintain: the modules this
    path runs unconditionally, and the closure the checker itself names. The
    closure is what ``checker-compat`` reports, so the analysis and the anchor
    cannot drift apart.
    """
    rows: list[dict] = [ _module_row(name) for name in TRUSTED_MODULES ]
    seen = {row["module"] for row in rows}
    imported, _outside = _checker_imports(checker)
    for entry in imported:
        if entry["module"] in seen:
            continue
        seen.add(entry["module"])
        rows.append({
            "module": entry["module"],
            "sha256": entry.get("sha256"),
            "source": "checker-import",
        })
    missing = sorted(row["module"] for row in rows if not row.get("sha256"))
    if missing and strict:
        # A module we cannot digest is a module we cannot vouch for.
        raise Refusal("checker_dependency_unresolved", ", ".join(missing[:6]))
    rows.sort(key=lambda row: row["module"])
    return receipts.digest_text(receipts.canonical_json(rows)), rows


PROBE_REGISTRY = "probe-residue"


def _probe_registry_path(run_root: Path) -> Path:
    return Path(run_root) / PROBE_REGISTRY


def _load_probe_rows(run_root: Path) -> list[dict]:
    value = receipts.read_json(_probe_registry_path(run_root)) or {}
    rows = value.get("rows") if isinstance(value, dict) else None
    return [row for row in rows or [] if isinstance(row, dict)]


def _save_probe_rows(run_root: Path, rows: list[dict]) -> None:
    receipts.atomic_write(
        _probe_registry_path(run_root),
        receipts.canonical_json({
            "schema": "eu4-dsh-probe-residue/v1",
            "rows": rows,
        }),
    )


def _registered_probe_names(run_root: Path, workspace: Path) -> set[str]:
    """Names the host itself created in this workspace, never a prefix guess."""
    resolved = Path(workspace).resolve()
    names: set[str] = set()
    for row in _load_probe_rows(run_root):
        name = str(row.get("name") or "")
        if (Path(str(row.get("workspace") or "")).resolve() == resolved
                and Path(name).name == name and name.startswith(PROBE_PREFIX)):
            names.add(name)
    return names


def _register_probe(run_root: Path, workspace: Path, name: str) -> None:
    rows = [row for row in _load_probe_rows(run_root)
            if not (Path(str(row.get("workspace") or "")).resolve()
                    == Path(workspace).resolve()
                    and row.get("name") == name)]
    rows.append({"workspace": str(Path(workspace).resolve()),
                 "name": name, "created_at": _now()})
    _save_probe_rows(run_root, rows)


def _unregister_probe(run_root: Path, workspace: Path, name: str) -> None:
    rows = [row for row in _load_probe_rows(run_root)
            if not (Path(str(row.get("workspace") or "")).resolve()
                    == Path(workspace).resolve()
                    and row.get("name") == name)]
    _save_probe_rows(run_root, rows)


def _reap_probe_residue(sidecar: Sidecar) -> str | None:
    """Remove only probes this host recorded; never a candidate path."""
    workspace = Path(sidecar.workspace)
    rows = _load_probe_rows(sidecar.run_root)
    keep: list[dict] = []
    problem: str | None = None
    for row in rows:
        if Path(str(row.get("workspace") or "")).resolve() != workspace.resolve():
            keep.append(row)
            continue
        name = str(row.get("name") or "")
        if Path(name).name != name or not name.startswith(PROBE_PREFIX):
            keep.append(row)
            continue
        path = workspace / name
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            keep.append(row)
            problem = (f"could not remove recorded probe residue {path}: {exc}; "
                       "repair the workspace before preparing")
    _save_probe_rows(sidecar.run_root, keep)
    return problem


def _is_registered_probe(path: str | Path, names: set[str]) -> bool:
    candidate = Path(path)
    return candidate.parent == Path(".") and candidate.name in names


def _workspace_write_problem(workspace: Path, run_root: Path) -> str | None:
    """Probe the candidate workspace with a host-recorded unique file.

    The registry records the exact name before creation; cleanup removes only
    that path.  A killed prepare leaves a name the next prepare can reap, while
    a candidate-authored path that merely resembles a probe is never filtered.
    """
    workspace = Path(workspace)
    run_root = Path(run_root)
    if not workspace.is_dir():
        return f"candidate workspace is not a directory: {workspace}"
    name = f"{PROBE_PREFIX}{uuid.uuid4().hex}"
    _register_probe(run_root, workspace, name)
    probe = workspace / name
    descriptor: int | None = None
    created = False
    problem: str | None = None
    try:
        descriptor = os.open(probe,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        os.write(descriptor, b"probe\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
    except OSError as exc:
        problem = f"candidate workspace is not writable: {workspace}: {exc}"
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if created:
            try:
                probe.unlink()
                _unregister_probe(run_root, workspace, name)
            except OSError as exc:
                residue = f"probe cleanup failed: {probe}: {exc}"
                problem = residue if problem is None else f"{problem}; {residue}"
        else:
            _unregister_probe(run_root, workspace, name)
    return problem


def _head_or_none(path: Path) -> str | None:
    try:
        return _git(path, "rev-parse", "HEAD")
    except Refusal:
        return None


def _startup_block(sidecar: Sidecar, roots: dict, session=None) -> dict:
    """Compact facts an agent needs before spending a launch or validation."""
    workspace = Path(sidecar.workspace)
    head = _head_or_none(workspace) if sidecar.mode == "worktree" else None
    first_command = (
        ["git", "-C", str(workspace), "status", "--short"]
        if head else ["ls", str(workspace)]
    )
    block = {
        "campaign_id": sidecar.campaign_id,
        "sidecar": str(sidecar.sidecar_path),
        "source_campaign": str(sidecar.source_campaign),
        "objective_digest": sidecar.objective_digest,
        "workspace": str(workspace),
        "workspace_mode": sidecar.mode,
        "workspace_head": head,
        "workspace_writable": True,
        "run_root": str(sidecar.run_root),
        "checker": str(sidecar.checker),
        "execution_roots": roots,
        "allowed_paths": list(sidecar.allowed_paths),
        "forbidden_paths": list(sidecar.forbidden_paths),
        "policy": {
            "digest": sidecar.policy_digest,
            "allowed_paths": list(sidecar.allowed_paths),
            "forbidden_paths": list(sidecar.forbidden_paths),
            "allow_symlinks": bool(sidecar.allow_symlinks),
        },
        "live_capture": {
            "enabled": sidecar.live_capture_enabled,
            "policy": (
                "EU4 launch exclusivity is enforced by the host-wide process "
                "lease; a sidecar cannot enable live capture"),
        },
        "closing": {
            "require_checker_pass": sidecar.require_checker_pass,
            "require_independent_review": sidecar.require_independent_review,
            "validation_timeout_seconds": sidecar.validation_timeout_seconds,
            "autonomy": (
                "no validation, replay or goal-round quota; continue while a "
                "justified experiment remains and stop only on verified success, "
                "a concrete blocker or the operator deadline"),
        },
        "first_command": first_command,
    }
    if session is not None:
        block["session"] = session.as_dict()
    return block


def _identity(sidecar: Sidecar, infra_commit: str) -> dict:
    modules_digest, modules_rows = _trusted_modules_digest(sidecar.checker)
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
        "trusted_modules_digest": modules_digest,
        "trusted_modules": modules_rows,
        "verification_manifest_digest": manifest_digest,
        "evidence_inputs_digest": _evidence_digest(sidecar),
        "candidate": _candidate(sidecar),
    }


AUTHORITY_SCHEMA = "eu4-dsh-authority/v1"
_MISSING_AUTHORITY = object()

# A legacy anchor predates the authority snapshot. These are the fields it did
# record; the remaining authority fields are covered by sidecar_sha256, which
# changes whenever the sidecar -- and therefore the limits or policy source --
# changes at all.
LEGACY_AUTHORITY_PATHS = {
    "campaign_id": ("identities", "campaign_id"),
    "campaign.source_campaign_sha256": ("identities", "source_campaign_sha256"),
    "campaign.sidecar_sha256": ("identities", "sidecar_sha256"),
    "campaign.infrastructure_commit": ("identities", "infrastructure_commit"),
    "campaign.candidate_baseline_commit": ("identities", "candidate_baseline_commit"),
    "campaign.policy_digest": ("identities", "policy_digest"),
    "campaign.objective_digest": ("identities", "objective_digest"),
    "campaign.checker_sha256": ("identities", "checker_sha256"),
    "campaign.trusted_modules_digest": ("identities", "trusted_modules_digest"),
    "campaign.verification_manifest_digest": (
        "identities", "verification_manifest_digest"),
    "campaign.evidence_inputs_digest": ("identities", "evidence_inputs_digest"),
    "candidate.mode": ("identities", "candidate", "mode"),
}


def _authority_surface(sidecar: Sidecar) -> dict:
    """Authority facts that need no candidate walk.

    These are enough to name a changed workspace, policy or limits before any
    candidate inspection can fail for a reason that hides the real drift.
    """
    return {
        "campaign_id": sidecar.campaign_id,
        "workspace": str(Path(sidecar.workspace).resolve()),
        "run_root": str(Path(sidecar.run_root).resolve()),
        "workspace_mode": sidecar.mode,
        "policy.digest": sidecar.policy_digest,
        "policy.allowed_paths": list(sidecar.allowed_paths),
        "policy.forbidden_paths": list(sidecar.forbidden_paths),
        "policy.allow_symlinks": bool(sidecar.allow_symlinks),
        "limits.validation_timeout_seconds": sidecar.validation_timeout_seconds,
        "closing.require_checker_pass": bool(sidecar.require_checker_pass),
        "closing.require_independent_review": bool(
            sidecar.require_independent_review),
        "campaign.sidecar_sha256": sidecar.sidecar_sha256,
        "campaign.source_campaign_sha256": sidecar.source_campaign_sha256,
        "campaign.objective_digest": sidecar.objective_digest,
        "campaign.candidate_baseline_commit": sidecar.candidate_baseline_commit,
        "campaign.checker_sha256": receipts.digest_file(sidecar.checker),
    }


def _authority_snapshot(sidecar: Sidecar, identities: dict) -> dict:
    """The full boundary an anchored context was authorized against."""
    candidate = identities.get("candidate") or {}
    value = _authority_surface(sidecar)
    value.update({
        "campaign.infrastructure_commit": identities.get("infrastructure_commit"),
        "campaign.trusted_modules_digest": identities.get(
            "trusted_modules_digest"),
        "campaign.verification_manifest_digest": identities.get(
            "verification_manifest_digest"),
        "campaign.evidence_inputs_digest": identities.get(
            "evidence_inputs_digest"),
        # Candidate commit/digest are mutable by design: an agent edits and
        # commits the candidate.  They are qualification inputs, not authority;
        # only the campaign baseline commit and checker/policy/limits are
        # frozen by the anchor.
        "candidate.mode": candidate.get("mode"),
    })
    return value


def _legacy_authority_value(stored: dict, key: str):
    if key == "workspace":
        return stored.get("workspace", _MISSING_AUTHORITY)
    if key == "run_root":
        return stored.get("run_root", _MISSING_AUTHORITY)
    path = LEGACY_AUTHORITY_PATHS.get(key)
    if path is None:
        return _MISSING_AUTHORITY
    value = stored
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return _MISSING_AUTHORITY
        value = value[part]
    return value


def _authority_drift(stored: dict, current: dict) -> list[str]:
    """Names of authority fields that no longer match the stored anchor.

    A new anchor records the whole snapshot. A legacy anchor is compared on the
    fields it recorded (including the workspace path); limits and policy source
    changes are still caught by sidecar_sha256, because both live in the
    sidecar. Missing anchor fields are not treated as authorization.
    """
    anchor = stored.get("authority")
    if isinstance(anchor, dict) and anchor:
        return [key for key in current if anchor.get(key) != current[key]]
    drift: list[str] = []
    for key, value in current.items():
        expected = _legacy_authority_value(stored, key)
        if expected is _MISSING_AUTHORITY:
            continue
        if expected != value:
            drift.append(key)
    return drift


def _check_authority(stored: dict, current: dict) -> None:
    drift = _authority_drift(stored, current)
    if drift:
        raise Refusal(
            "identity_modified",
            "protected material changed since prepare: " + ", ".join(drift),
        )


def _qualification_state(sidecar: Sidecar, identities: dict,
                         root: Path | None = None) -> dict:
    """Read-only view of the stored qualification; never a new verdict."""
    receipt = receipts.load_current_receipt(root or sidecar.run_root)
    if receipt is None:
        return {"state": "none", "detail": "no validation receipt is recorded"}
    problems = receipts.verify_receipt_digest(receipt)
    if problems:
        return {"state": "corrupt", "detail": "; ".join(problems[:3])}
    stored_ids = receipt.get("identities") or {}
    if receipts.identity_digest(stored_ids) != receipts.identity_digest(identities):
        return {
            "state": "stale",
            "detail": "the receipt does not describe the current candidate",
        }
    outcome = receipt.get("outcome") or {}
    verdict = outcome.get("checker_verdict")
    excerpt = str(outcome.get("diagnostics_excerpt") or "")[:800]
    return {
        "state": "pass" if verdict == "pass" else "not_passed",
        "checker_verdict": verdict,
        "receipt_digest": receipt.get("receipt_digest"),
        # Bounded checker prose is a locator for the agent, not a verdict.
        "diagnostics_excerpt": excerpt,
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
    """The goal policy the plugin acts on.

    The values are GOAL_POLICY's, never the classification or verdict names:
    an infrastructure failure (timeout, unreadable output, a checker that could
    not run) pauses the goal, and a checker `blocked` verdict maps to "block".
    """
    if classification == "verdict":
        return GOAL_POLICY.get(str(verdict), GOAL_POLICY["unavailable"])
    return GOAL_POLICY["unavailable"]


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


def _read_anchor(path: Path) -> dict | None:
    """Read the identity anchor; corrupt state is a structured refusal.

    ``receipts.read_json`` lets a decode error escape. An unreadable anchor is
    not the same state as a missing one: reporting it as ``not_prepared`` would
    tell an operator to start a campaign that already has protected material,
    and a traceback would lose the classification entirely.
    """
    anchor_path = Path(path)
    if not anchor_path.is_file():
        if anchor_path.exists():
            raise Refusal(
                "anchor_corrupt",
                f"{anchor_path}: exists but is not a regular file")
        return None
    try:
        value = receipts.read_json(anchor_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Refusal("anchor_corrupt", f"{anchor_path}: {exc}") from exc
    if value is None:
        raise Refusal("anchor_corrupt", f"{anchor_path}: JSON null is not an anchor")
    if not isinstance(value, dict):
        raise Refusal("anchor_corrupt", f"{anchor_path}: expected a JSON object")
    identities = value.get("identities")
    if value.get("schema") != "eu4-dsh-identity/v1" \
            or not isinstance(identities, dict) or not identities:
        raise Refusal(
            "anchor_corrupt",
            f"{anchor_path}: not an identity anchor (missing schema/identities); "
            "an empty object must not authorize a context")
    return value


def _prepared_identities(sidecar: Sidecar) -> dict:
    """Trusted material captured by ``prepare``; the anchor later runs compare to."""
    stored = _read_anchor(receipts.paths(sidecar.run_root).identity)
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
    # Resolve (not create) the per-session root before any trusted check. A
    # refused prepare must not leave an empty session directory that a later
    # reconnect could adopt. `_prepare_locked` creates it immediately before the
    # session record is written, after every refusal point.
    session = _session_layout(sidecar.run_root,
                              getattr(args, "session_id", None),
                              create=False, must_exist=False)
    infra_commit = _git(infra_root(), "rev-parse", "HEAD")
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": False, "operation": "prepare",
              "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy",
              "goal_policy": GOAL_POLICY["unavailable"], "detail": str(exc)})
        return EXIT_OK
    try:
        return _prepare_locked(args, sidecar, roots, stored, infra_commit, session)
    finally:
        lock.__exit__(None, None, None)


def _accept_target(accept) -> int | None:
    """The target declared by the campaign's own acceptance command."""
    if not isinstance(accept, list):
        return None
    for index, part in enumerate(accept):
        if str(part) == "--target-end" and index + 1 < len(accept):
            try:
                return int(accept[index + 1])
            except (TypeError, ValueError):
                return None
    return None


def _fixture_preflight(sidecar: Sidecar) -> None:
    """Refuse a campaign whose trusted fixture cannot qualify its target.

    This is the existing launcher preflight, extended with the checker's own
    static validator: fixture seal/digests, observation coverage, and any
    input/output overlap inside the requested prefix.  It runs once at launch,
    before the agent or an expensive replay starts.
    """
    try:
        campaign = json.loads(
            Path(sidecar.source_campaign).read_text(encoding="utf-8"))
        sidecar_raw = json.loads(
            Path(sidecar.sidecar_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Refusal("fixture_ineligible",
                      f"campaign configuration is unreadable: {exc}") from exc
    scope = campaign.get("fixture_scope") or {}
    target = _accept_target(campaign.get("accept"))
    if target is None:
        target = scope.get("end") if isinstance(scope, dict) else None
    if target is None:
        return
    trusted = (sidecar_raw.get("trusted_reference_root")
               or campaign.get("trusted_reference_root"))
    frame = (campaign.get("fixture_frame")
             or (Path(str(trusted)).expanduser() / "frame"
                 if trusted else None))
    if not trusted or frame is None:
        raise Refusal(
            "fixture_ineligible",
            "campaign declares a target prefix but no trusted reference")
    if sys.platform != "darwin" or shutil.which("clang") is None:
        raise Refusal(
            "fixture_ineligible",
            "trusted Metal replay requires macOS with the clang toolchain")
    frame = Path(str(frame)).expanduser()
    if not frame.is_absolute():
        frame = infra_root() / frame
    frame = frame.resolve()
    from automation import gfx_prefix_check

    try:
        root = gfx_prefix_check.resolve_trusted_reference(
            trusted, sidecar.workspace, root=infra_root())
        report = gfx_prefix_check.preflight_fixture(
            frame, require_end=int(target), trusted_root=root)
        if sidecar.mode == "worktree":
            gfx_prefix_check.verify_candidate(sidecar.workspace,
                                              sidecar.sidecar_path)
    except (gfx_prefix_check.PrefixCheckError, OSError, subprocess.SubprocessError, ValueError) as exc:
        raise Refusal("fixture_ineligible", str(exc)) from exc
    if report.get("problems"):
        raise Refusal("fixture_ineligible",
                      "; ".join(str(p) for p in report["problems"]))
    if report.get("authority") not in ("authentic", "derived", "synthetic"):
        raise Refusal(
            "fixture_ineligible",
            f"trusted fixture authority is {report.get('authority')!r}")


def _prepare_locked(args, sidecar, roots, stored, infra_commit, session) -> int:
    if sidecar.mode == "seed":
        ensure_workspace(sidecar.seed, sidecar.workspace)
    else:
        require_worktree(
            sidecar.workspace, infra_root().resolve(),
            sidecar.candidate_baseline_commit or "",
        )

    residue_problem = _reap_probe_residue(sidecar)
    if residue_problem:
        raise Refusal("workspace_not_writable", residue_problem)
    write_problem = _workspace_write_problem(
        sidecar.workspace, sidecar.run_root)
    if write_problem:
        raise Refusal("workspace_not_writable", write_problem)
    if getattr(args, "create_anchor", False):
        _fixture_preflight(sidecar)

    identities = _identity(sidecar, infra_commit)
    authority = _authority_snapshot(sidecar, identities)
    existing = _read_anchor(stored.identity)
    previous_anchor_digest = None
    if existing is not None and getattr(args, "reanchor", False):
        previous_anchor_digest = hashlib.sha256(
            stored.identity.read_bytes()).hexdigest()
        existing = None
    if existing is None and not (
            getattr(args, "create_anchor", False)
            or getattr(args, "reanchor", False)):
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
        _check_authority(existing, authority)
    else:
        anchor_value = {
            "schema": "eu4-dsh-identity/v1",
            "campaign_id": sidecar.campaign_id,
            "prepared_at": _now(),
            "execution_roots": roots,
            "identities": identities,
            "workspace": str(sidecar.workspace),
            "run_root": str(sidecar.run_root),
            "authority": {"schema": AUTHORITY_SCHEMA, **authority},
        }
        if previous_anchor_digest is not None:
            anchor_value["reanchored_at"] = _now()
            anchor_value["previous_anchor_sha256"] = previous_anchor_digest
        receipts.write_identity(sidecar.run_root, anchor_value)

    identity_digest = receipts.identity_digest(identities)
    verification = _qualification_state(
        sidecar, identities, root=session.root)
    startup = _startup_block(sidecar, roots, session)
    attempts = _validation_attempts(_campaign_attempt_history(sidecar.run_root))
    startup_text = _startup_text(
        startup, attempts=attempts, identity_digest=identity_digest,
        verification=verification,
        candidate_digest=(identities.get("candidate") or {}).get("digest"))
    # The anchor above is campaign-wide trusted material. Only now, after every
    # refusal point, create the isolated session root; the record names the
    # validated session and its writable root, and a reconnect finds the same
    # file instead of rewriting it.
    if session.isolated:
        # Idempotent: also re-resolves and re-tightens an existing root to 0700,
        # which the previous create=True-on-every-call path did.
        session = _session_layout(sidecar.run_root,
                                  getattr(args, "session_id", None),
                                  create=True)
    try:
        session_record, record_created = session_state.write_session_record(
            session, campaign_id=sidecar.campaign_id)
    except session_state.SessionRootError as exc:
        raise Refusal("session_conflict", str(exc)) from exc

    emit({
        "ok": True,
        "operation": "prepare",
        "campaign_id": sidecar.campaign_id,
        "objective": sidecar.objective_text,
        "goal_text": sidecar.objective_text + "\n\n" + startup_text,
        "startup_text": startup_text,
        "require_independent_review": sidecar.require_independent_review,
        "checker_timeout_seconds": sidecar.validation_timeout_seconds,
        "execution_roots": roots,
        "workspace": str(sidecar.workspace),
        "run_root": str(sidecar.run_root),
        "session": session.as_dict(),
        "session_record": str(session_record) if session_record else None,
        "session_record_created": record_created,
        "identities": identities,
        "identity_digest": identity_digest,
        "verification": verification,
        "anchored_now": existing is None,
        "startup": _startup_block(sidecar, roots, session),
    })
    return EXIT_OK


VALIDATIONS_LOG = "validations.jsonl"


def _session_layout(run_root: Path, session_id, *, create: bool,
                    must_exist: bool = True):
    """Resolve the writable root for one DSH session.

    ``session_id=None`` keeps the pre-existing shared run-root layout and is
    reported as such; an unsafe id is a refusal, never a fallback to the shared
    root (which would silently mix two sessions).
    """
    try:
        return session_state.layout(
            run_root, session_id, create=create, must_exist=must_exist)
    except session_state.SessionRootError as exc:
        raise Refusal("invalid_session", str(exc)) from exc


def _state_root(value) -> Path:
    """Accept a SessionLayout or a plain run root."""
    root = getattr(value, "root", None)
    return Path(root if root is not None else value)


def _validation_history(run_root: Path) -> list[dict]:
    """Actual validation attempts recorded by the bridge, not the model."""
    path = Path(run_root) / VALIDATIONS_LOG
    rows: list[dict] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _append_validation(run_root: Path, row: dict) -> None:
    path = Path(run_root) / VALIDATIONS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _validation_attempts(history: list[dict]) -> list[dict]:
    """One logical attempt per distinct ``attempt_id``.

    New records have a ``start`` row written before the checker runs and a
    ``result`` row after it.  A start without a result is an interrupted
    attempt; it stays visible in the audit trail after a restart.  Older logs
    have one self-contained row per attempt and remain readable; each such row
    is treated as its own attempt.
    """
    attempts: dict[str, dict] = {}
    order: list[str] = []
    for index, row in enumerate(history):
        raw = row.get("attempt_id")
        key = str(raw) if raw else f"legacy-{index}"
        attempt = attempts.get(key)
        if attempt is None:
            attempt = {"attempt_id": key, "terminal": bool(not raw)}
            attempts[key] = attempt
            order.append(key)
        # Keep the first timestamp: a completed attempt started when its start
        # row was written, which is the strict wall-clock reference.
        if "at" in row and "at" not in attempt:
            attempt["at"] = row["at"]
        for field in ("operation", "candidate_digest", "classification",
                      "kind", "checker_verdict", "digest", "exit_code",
                      "duration_ms", "qualified"):
            if field in row:
                attempt[field] = row[field]
        if raw:
            if row.get("phase") == "start":
                attempt["terminal"] = False
            else:
                attempt["terminal"] = True
        else:
            attempt["terminal"] = True
            if "qualified" not in row:
                attempt["qualified"] = row.get("checker_verdict") == "pass"
    return [attempts[key] for key in order]


def _startup_text(block: dict, *, attempts: list[dict],
                 identity_digest: str | None = None,
                 verification: dict | None = None,
                 candidate_digest: str | None = None) -> str:
    """Compact first-context text for the initial goal message.

    It is derived only from the prepared anchor and persisted attempt records;
    it never invents a trusted checker, a case locator, a remaining allowance, or
    an authority that the anchor did not verify.
    """
    closing = block.get("closing") or {}
    policy = block.get("policy") or {}
    verification = verification or {"state": "none"}
    used = len(attempts)
    last = attempts[-1] if attempts else None
    verification_line = f"- verification: {verification.get('state')}"
    if verification.get("checker_verdict"):
        verification_line += f" (checker_verdict={verification['checker_verdict']})"
    if verification.get("receipt_digest"):
        verification_line += (
            f" (receipt={str(verification['receipt_digest'])[:16]})")
    if verification.get("detail"):
        verification_line += f" -- {verification['detail']}"
    digest = str(identity_digest or "")
    lines = [
        "EU4 harness context (prepared, not model-reported):",
        f"- workspace: {block.get('workspace')} "
        f"(mode={block.get('workspace_mode')}, "
        f"head={block.get('workspace_head') or 'seed'})",
        f"- campaign: {block.get('campaign_id')} "
        f"(sidecar={block.get('sidecar')}, run_root={block.get('run_root')})",
        f"- identity: digest={digest[:16] or 'unavailable'} "
        "(verified against the prepared anchor: workspace, policy, limits, "
        "sidecar and campaign)",
        f"- policy: digest={str(policy.get('digest') or '')[:16] or 'unavailable'}"
        f"; allow_symlinks={bool(policy.get('allow_symlinks'))}",
        f"- writable scope: {', '.join(block.get('allowed_paths') or []) or 'campaign defaults'}",
        f"- forbidden: {', '.join(block.get('forbidden_paths') or []) or 'none declared'}",
        f"- candidate: digest={str(candidate_digest or '')[:16] or 'unavailable'}"
        " (current workspace head; evidence is bound to this revision)",
        f"- autonomy: no validation, replay or goal-round quota"
        + f"; per-checker timeout={closing.get('validation_timeout_seconds')}s"
        + "; the operator deadline is enforced by the campaign guard",
        "- deadline: only `python3 -m automation.campaign_guard status "
        "--state $EU4_CAMPAIGN_STATE` is authoritative; never report deadline "
        "exhaustion unless it reports no remaining time or the guard refuses "
        "the action with kind=deadline",
        f"- attempts recorded (audit only, not a quota): {used}",
        f"- live capture: "
        f"{'enabled' if (block.get('live_capture') or {}).get('enabled') else 'disabled'}"
        " (host-wide process lease still required)",
        verification_line,
        ("- last checker excerpt: "
         + " ".join(str(verification.get("diagnostics_excerpt") or "").split())[:600]
         if verification.get("diagnostics_excerpt") else
         "- last checker excerpt: none recorded"),
        "- last attempt: " + (
            f"{last.get('operation')} classification={last.get('classification')} "
            f"qualified={last.get('qualified')} digest={str(last.get('digest') or '')[:16]}"
            if last else "none recorded"),
        "- case context: no locator is bound at prepare time (unbound case "
        "locator); use `python3.14 -m automation.agent_tools eu4_session "
        "discover --query <session-or-case>` and verify before reuse; do not "
        "authorize on unverified locator metadata",
        "- next: run the focused test/module for the touched contract, then "
        "`eu4_validate_candidate` with the current candidate digest",
        "- stop path: `eu4_finalize_candidate` only after a checker pass"
        + (" and independent review" if closing.get("require_independent_review")
           else "")
        + "; an unresolved result must be recorded as a block, not certified",
        f"- first command: {' '.join(block.get('first_command') or [])}",
    ]
    return "\n".join(lines)


def _start_attempt(where, *, operation: str,
                   candidate_digest: str | None,
                   attempt_id: str | None = None) -> str:
    """Persist an attempt before the expensive checker can start."""
    attempt_id = attempt_id or (
        f"val-{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    _append_validation(_state_root(where), {
        "attempt_id": attempt_id,
        "phase": "start",
        "at": time.time(),
        "operation": operation,
        "candidate_digest": candidate_digest,
    })
    return attempt_id


def _finish_attempt(where, attempt_id: str, *, operation: str,
                    classification: str, kind: str | None,
                    verdict: str | None, diagnostics: str,
                    exit_code, duration_ms: int,
                    qualified: bool) -> None:
    """Persist the terminal result of an attempt started earlier."""
    _append_validation(_state_root(where), {
        "attempt_id": attempt_id,
        "phase": "result",
        "at": time.time(),
        "operation": operation,
        "classification": classification,
        "kind": kind,
        "checker_verdict": verdict,
        "digest": receipts.digest_text(diagnostics),
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "qualified": bool(qualified),
    })


def _campaign_validation_roots(run_root: Path) -> list[Path]:
    """Every root that can hold this campaign's attempt logs.

    The campaign root keeps the legacy shared log; each isolated session has its
    own ``sessions/<id>/validations.jsonl``.  Symlinked or non-directory entries
    are skipped: a log is read-only input to the audit trail, and an
    unresolved link must not widen what the campaign reads.
    """
    root = Path(run_root)
    roots = [root]
    sessions = root / session_state.SESSIONS_DIRNAME
    if sessions.is_dir() and not sessions.is_symlink():
        for entry in sorted(sessions.iterdir()):
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                session_state.validate_session_id(entry.name)
            except session_state.SessionRootError:
                continue
            roots.append(entry)
    return roots


def _campaign_attempt_history(run_root: Path) -> list[dict]:
    """Merge the campaign root and every session's attempt log."""
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for root in _campaign_validation_roots(run_root):
        for row in _validation_history(root):
            attempt_id = row.get("attempt_id")
            if attempt_id:
                key = (str(attempt_id), str(row.get("phase") or ""))
                if key in seen:
                    continue
                seen.add(key)
            rows.append(row)
    return rows


def _begin_attempt(layout_value, *, operation: str,
                   candidate_digest: str | None) -> str:
    """Start one checker attempt in the session's validations log."""
    attempt_id = f"val-{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    _start_attempt(layout_value, operation=operation,
                   candidate_digest=candidate_digest, attempt_id=attempt_id)
    return attempt_id


def _policy_stop_payload(sidecar: Sidecar, stop: dict,
                         history: list[dict], current: dict) -> dict:
    return {
        "ok": True,
        "operation": None,  # callers set this
        "campaign_id": sidecar.campaign_id,
        "classification": "policy_stop",
        "kind": stop["kind"],
        "goal_policy": GOAL_POLICY["unavailable"],
        "detail": stop.get("detail") or stop.get("kind"),
        "validations": len(_validation_attempts(history)),
        "candidate_digest": (current.get("candidate") or {}).get("digest"),
    }


def _campaign_safety_stop() -> dict | None:
    """A campaign-wide stop or an expired guard deadline, or ``None``.

    The bridge holds the trusted checker, but a stopped campaign must refuse
    every expensive operation, not only the replay wrappers.  The host-wide
    activation marker makes this check work without inheriting a state path.
    """
    from automation import campaign_guard

    try:
        campaign_guard.require_active_from_env()
    except campaign_guard.CampaignRefusal as exc:
        return {"kind": exc.kind, "detail": exc.detail}
    return None


def op_validate(args) -> int:
    """Run the trusted checker once and record the qualification.

    DSH decides when the agent continues.  The only campaign-wide refusal here
    is the operator's execution deadline; attempts are logged for audit and for
    the reconnect summary but never counted against a quota.
    """
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    session_id = getattr(args, "session_id", None) or None
    # Resolve, do not create: a refusal (missing/corrupt anchor, exhausted
    # limits) must not leave an empty per-session root behind.
    session = _session_layout(sidecar.run_root, session_id, create=False,
                              must_exist=False)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "validate", "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy",
              "goal_policy": GOAL_POLICY["unavailable"], "detail": str(exc)})
        return EXIT_OK
    try:
        infra_commit = _git(infra_root(), "rev-parse", "HEAD")
        before = _identity(sidecar, infra_commit)
        _check_anchor(_prepared_identities(sidecar), before)
        digest = before["candidate"]["digest"]

        # A safety stop or an expired operator allowance refuses expensive
        # work; the attempt history is audited, not used as an allowance.
        history = _campaign_attempt_history(sidecar.run_root)
        stop = _campaign_safety_stop()
        if stop is not None:
            payload = _policy_stop_payload(sidecar, stop, history, before)
            payload["operation"] = "validate"
            emit(payload)
            return EXIT_OK

        # The detailed start row is written in this session's log so an
        # interrupted attempt is visible after a reconnect.  Only now, after
        # every refusal point, create the isolated root.
        if session.isolated:
            session = _session_layout(sidecar.run_root, session_id, create=True)
        attempt_id = _begin_attempt(
            session, operation="validate", candidate_digest=digest)
        started = time.monotonic()
        checker_exception = None
        try:
            outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        except Exception as exc:  # noqa: BLE001 - the attempt must be recorded
            checker_exception = exc
            outcome = {
                "ok": False,
                "verdict": None,
                "exit_code": None,
                "diagnostics": f"{type(exc).__name__}: {exc}",
            }
        duration_ms = int((time.monotonic() - started) * 1000)

        after = _identity(sidecar, infra_commit)
        classification, kind, verdict = classify(outcome)
        if checker_exception is not None:
            classification, kind = "infrastructure_error", "checker_exception"

        drift = after != before
        incomplete = False
        if not drift and classification == "verdict" and verdict == "pass" \
                and not sidecar.allow_incomplete_inputs:
            inventory = snapshot.ignored_inputs(sidecar.workspace)
            incomplete = bool(inventory["truncated"])

        diagnostics = str(outcome.get("diagnostics") or "")
        qualified = (not drift and not incomplete
                     and classification == "verdict" and verdict == "pass")
        _finish_attempt(
            session, attempt_id, operation="validate",
            classification=("identity_drift" if drift else
                            "inputs_incomplete" if incomplete else
                            classification),
            kind=("identity_drift" if drift else
                  "inputs_incomplete" if incomplete else kind),
            verdict=verdict, diagnostics=diagnostics,
            exit_code=outcome.get("exit_code"), duration_ms=duration_ms,
            qualified=qualified)

        if drift:
            changed = [key for key in before if before[key] != after.get(key)]
            raise Refusal("identity_drift",
                          "identity changed during the checker run: " + ", ".join(changed))
        if incomplete:
            raise Refusal(
                "inputs_incomplete",
                "the ignored-input inventory was truncated, so the evidence "
                "cannot be shown to cover everything the checker read; declare "
                "allow_incomplete_inputs on the campaign to accept that",
            )
        diagnostics_digest = receipts.digest_text(diagnostics)
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
                # What the checker saw beyond the tracked candidate: generated
                # inputs and the declared sibling inputs (a research checkpoint).
                # Recorded here and re-checked at finalization.
                "inputs_digest": snapshot.ignored_digest(sidecar.workspace),
                "replay_inputs": _replay_inputs_rows(sidecar),
            },
            "session": {"session_id": session_id},
            "session_root": str(session.root),
            "session_layout": session.kind,
            "created_at": _now(),
        }
        path, sealed = receipts.write_receipt(session.root, receipt)
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
            "session": session.as_dict(),
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
        _digest, entries = receipts.tree_digest(sidecar.workspace)
        residue = _registered_probe_names(
            sidecar.run_root, sidecar.workspace)
        entries = [entry for entry in entries
                   if not _is_registered_probe(entry["path"], residue)]
        digest = receipts.digest_text(receipts.canonical_json(entries))
        return {"mode": "seed", "digest": digest, "entries": len(entries),
                "commit": None}

    infra = infra_root().resolve()
    baseline = sidecar.candidate_baseline_commit or ""
    residue = _registered_probe_names(sidecar.run_root, sidecar.workspace)
    delta = [entry for entry in snapshot.enumerate_delta(sidecar.workspace)
             if not (entry.get("kind") == "add"
                     and not entry.get("old_path")
                     and _is_registered_probe(entry["path"], residue))]
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


def _replay_inputs_rows(sidecar: Sidecar) -> list[dict]:
    """Digests of the declared sibling inputs the checker also reads.

    Bound when a validation passes and compared before replay: a checkpoint that
    changed after the passing run must not be silently replayed as if it were the
    one the checker accepted.
    """
    rows: list[dict] = []
    for name in sidecar.replay_inputs:
        path = sidecar.workspace.resolve().parent / name
        rows.append({
            "path": name,
            "sha256": receipts.digest_file(path) if path.is_file() else None,
        })
    return rows


def _replay_frozen(sidecar: Sidecar, frozen: dict, *,
                   session=None, session_id: str | None = None) -> dict:
    """Run the checker against the frozen candidate rebuilt over its baseline.

    The replay worktree stays at the *pinned baseline*, with the frozen delta
    materialized as uncommitted changes. Reconstruction checkers deliberately
    require ``HEAD`` to still be the pin -- a worktree at the frozen commit would
    look like a rewritten history to them -- so moving HEAD here would break
    valid candidates for reasons that have nothing to do with the candidate.
    """
    commit = frozen.get("commit")
    if not commit:
        # Seed candidates have no commit; the digest already describes the tree
        # the checker just accepted.
        return {"mode": "seed", "verdict": "skipped"}

    baseline = frozen.get("baseline") or sidecar.candidate_baseline_commit or ""
    if not baseline:
        raise Refusal("frozen_mismatch", "the frozen candidate has no pinned baseline")
    infra = infra_root().resolve()
    replay_root = sidecar.run_root / "replay"
    _drop_worktree(replay_root)
    subprocess.run(
        ["git", "-C", str(infra), "worktree", "add", "--detach",
         str(replay_root), baseline],
        check=True, capture_output=True,
    )
    # Checker-relevant state that lives *outside* the candidate tree -- the
    # research checkpoint a reconstruction checker reads -- has to be reproduced
    # beside the replay, or a finished candidate looks unfinished to it.
    inputs: list[dict] = []
    try:
        for name in sidecar.replay_inputs:
            source = sidecar.workspace.resolve().parent / name
            if not source.is_file():
                raise Refusal(
                    "replay_inputs_missing",
                    f"the campaign declares {name} as a replay input, but it is "
                    "missing beside the candidate workspace",
                )
            destination = replay_root.parent / name
            shutil.copyfile(source, destination)
            inputs.append({"path": name, "sha256": receipts.digest_file(destination)})

        patch = subprocess.run(
            ["git", "-C", str(infra), "diff", "--binary", baseline, commit],
            capture_output=True, check=True,
        ).stdout
        if patch:
            applied = subprocess.run(
                ["git", "-C", str(replay_root), "apply", "--binary", "--index", "-"],
                input=patch, capture_output=True,
            )
            if applied.returncode != 0:
                raise Refusal(
                    "frozen_mismatch",
                    "the frozen delta does not apply to the pinned baseline: "
                    + applied.stderr.decode("utf-8", "replace")[:300],
                )
        head = subprocess.run(
            ["git", "-C", str(replay_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        if head != baseline:
            raise Refusal(
                "frozen_mismatch",
                f"the replay worktree is at {head[:12]}, not the pinned baseline",
            )
        # Reproduction is another physical checker execution: it is charged
        # against the same campaign-wide limits before it starts.
        history = _campaign_attempt_history(sidecar.run_root)
        stop = _campaign_safety_stop()
        if stop is not None:
            raise Refusal(stop["kind"], stop["detail"])
        attempt_id = _begin_attempt(
            session if session is not None else sidecar.run_root,
            operation="finalize-replay", candidate_digest=commit)
        started = time.monotonic()
        checker_exception = None
        try:
            outcome = run_accept(_AcceptShim(sidecar), replay_root)
        except Exception as exc:  # noqa: BLE001 - the attempt must be recorded
            checker_exception = exc
            outcome = {
                "ok": False,
                "verdict": None,
                "exit_code": None,
                "diagnostics": f"{type(exc).__name__}: {exc}",
            }
        duration_ms = int((time.monotonic() - started) * 1000)
        classification, kind, verdict = classify(outcome)
        if checker_exception is not None:
            classification, kind = "infrastructure_error", "checker_exception"
        _finish_attempt(
            session if session is not None else sidecar.run_root,
            attempt_id, operation="finalize-replay",
            classification=classification, kind=kind, verdict=verdict,
            diagnostics=str(outcome.get("diagnostics") or ""),
            exit_code=outcome.get("exit_code"), duration_ms=duration_ms,
            qualified=(classification == "verdict" and verdict == "pass"))
    finally:
        _drop_worktree(replay_root)

    if classification != "verdict" or verdict != "pass":
        raise Refusal(
            "frozen_mismatch",
            "the frozen candidate does not reproduce the accepted result: "
            f"{verdict or classification}/{kind}",
        )
    return {"mode": "worktree", "commit": commit, "baseline": baseline,
            "head": head, "verdict": verdict, "inputs": inputs}


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


def _review_problem(sidecar: Sidecar, frozen: dict, *, require_live: bool = True,
                    receipt_root: Path | None = None) -> list[str]:
    """Whether an existing review qualifies this frozen candidate.

    ``receipt_root`` is the session's writable root when the campaign runs
    isolated; the review artifacts themselves stay campaign-wide.
    """
    paths = _review_paths(sidecar)
    if not paths["report"].is_file():
        return ["no independent review report exists for this candidate"]
    try:
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"review report unreadable: {exc}"]
    oracle = _oracle_digest(
        sidecar,
        (receipts.load_current_receipt(
            receipt_root if receipt_root is not None else sidecar.run_root) or {}
         ).get("identities") or {},
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


def _imported_modules(source: Path) -> set[str]:
    """``automation.*`` modules a file names, including the ``from`` forms."""
    import ast

    try:
        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("automation."):
                    # Full path: `automation.gfx_contracts.api` and
                    # `automation.gfx_contracts` are different files.
                    names.add(alias.name.split(".", 1)[1])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("automation."):
                names.add(module.split(".", 1)[1])
            elif module == "automation":
                # `from automation import gfx_checkpoint` names no submodule in
                # ``module``, so the imported names themselves are the modules.
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
    return names


def _checker_imports(checker: Path) -> tuple[list[dict], list[str]]:
    """The ``automation.*`` closure the checker will actually execute.

    The walk follows imports transitively, because a checker that imports
    ``automation.gfx_draw_accept_v2`` also executes everything that module
    imports. Package contents are included too: ``automation.gfx_contracts``
    selects a validator version with ``importlib.import_module`` at runtime, so
    the modules inside the package are digested rather than guessing which one.

    The walk is static. A dynamic import that builds a name from data cannot be
    seen here, which is why an unresolvable name refuses instead of being ignored.
    """
    import importlib.util

    infra = infra_root().resolve()
    rows: list[dict] = []
    outside: list[str] = []
    seen: set[str] = set()

    def record(name: str) -> Path | None:
        try:
            spec = importlib.util.find_spec(f"automation.{name}")
        except (ImportError, AttributeError, ValueError):
            spec = None
        origin = Path(spec.origin).resolve() if spec and spec.origin else None
        rows.append({
            "module": name,
            "resolved": str(origin) if origin else None,
            "sha256": receipts.digest_file(origin)
            if origin is not None and origin.is_file() else None,
            "inside_infrastructure": bool(origin) and inside(origin, infra),
        })
        if origin is None or not inside(origin, infra):
            outside.append(name)
        return origin

    queue: list[Path] = [checker]
    while queue:
        source = queue.pop()
        for name in sorted(_imported_modules(source)):
            # `a.b.c` implies its parents: the package module runs too, and its
            # __init__ is where the versioned validator is selected.
            parts = name.split(".")
            for index in range(len(parts)):
                prefix = ".".join(parts[: index + 1])
                if prefix in seen:
                    continue
                seen.add(prefix)
                origin = record(prefix)
                if origin is None or origin.suffix != ".py":
                    continue
                queue.append(origin)
                if origin.name == "__init__.py":
                    package = origin.parent
                    for candidate in sorted(package.rglob("*.py")):
                        dotted = ".".join(
                            candidate.relative_to(package.parent).with_suffix("").parts
                        )
                        if dotted.endswith(".__init__"):
                            dotted = dotted[: -len(".__init__")]
                        if dotted in seen:
                            continue
                        seen.add(dotted)
                        rows.append({
                            "module": dotted,
                            "resolved": str(candidate),
                            "sha256": receipts.digest_file(candidate),
                            "inside_infrastructure": inside(candidate, infra),
                        })
    rows.sort(key=lambda row: row["module"])
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
                # Administrative diagnostic on the pristine baseline: it never
                # qualifies a candidate and is recorded separately.
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
        # Reporting mode: an unresolvable dependency is what compat is for, so
        # do not refuse the analysis that names it.
        "trusted_modules_digest": _trusted_modules_digest(sidecar.checker, strict=False)[0],
        "imports": rows,
        "baseline": baseline,
        "problems": problems,
    })
    return EXIT_OK if compat == "ok" else EXIT_INTEGRITY


def op_review_run(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    requested_session = _session_layout(
        sidecar.run_root, getattr(args, "session_id", None) or None,
        create=False, must_exist=False)
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
        receipt = receipts.load_current_receipt(requested_session.root)
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
        runner_env = review.reviewer_env(os.environ)
        if mode == "live":
            if not runner_env.get("DSH_HOME"):
                raise Refusal(
                    "review_environment",
                    "the reviewer needs DSH_HOME so it uses this campaign's DSH "
                    "home rather than the operator's default installation",
                )
            if not (runner_env.get("OPENCODE_GO_API_KEY")
                    or runner_env.get("DEEPSEEK_API_KEY")):
                # The shipped one-shot profile does not read the interactive
                # credential store, so without a key in the environment the
                # reviewer would launch and fail to authenticate.
                raise Refusal(
                    "review_environment",
                    "the reviewer needs an API key in the environment: the "
                    "one-shot profile cannot read the stored credential. Export "
                    "OPENCODE_GO_API_KEY (or DEEPSEEK_API_KEY) before starting "
                    "the campaign",
                )
        before = snapshot.fingerprint(paths["worktree"])
        try:
            outcome = review.run_reviewer(
                command, cwd=paths["worktree"], timeout=args.timeout, env=runner_env
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
                "goal_policy": GOAL_POLICY["unavailable"],
                "detail": str(exc),
                "candidate_commit": frozen["commit"],
            })
            return EXIT_OK
        moved = snapshot.recheck(paths["worktree"], before)
        if moved:
            # The changed paths go into the refusal; the checkout itself does not
            # outlive the operation, so a rejected review leaves no stale tree.
            _drop_worktree(paths["worktree"])
            raise Refusal(
                "review_worktree_modified",
                "the reviewer changed its worktree: " + "; ".join(moved[:4]),
            )
        if outcome["exit_code"] != 0:
            # The reviewer crashed; nothing was decided about the candidate.
            emit({
                "ok": False, "operation": "review-run",
                "campaign_id": sidecar.campaign_id,
                "classification": "infrastructure_error", "kind": "review_failed",
                "goal_policy": GOAL_POLICY["unavailable"],
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
            "session": requested_session.as_dict(),
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def op_review_verify(args) -> int:
    sidecar = load_sidecar(Path(args.sidecar))
    _roots(sidecar)
    stored = _setup(sidecar.run_root)
    session = _session_layout(sidecar.run_root,
                              getattr(args, "session_id", None) or None,
                              create=False, must_exist=False)
    try:
        lock = campaign_lock(stored.lock)
        lock.__enter__()
    except RuntimeError as exc:
        emit({"ok": True, "operation": "review-verify",
              "campaign_id": sidecar.campaign_id,
              "classification": "policy_stop", "kind": "busy", "detail": str(exc)})
        return EXIT_OK
    try:
        return _review_verify_locked(args, sidecar, session)
    finally:
        lock.__exit__(None, None, None)


def _review_verify_locked(args, sidecar, session=None) -> int:
    current = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
    _check_anchor(_prepared_identities(sidecar), current)
    frozen = (_freeze_or_existing(sidecar, current))
    # Strict by default: a scripted (test double) receipt never authorizes
    # completion. --allow-scripted exists so the pipeline itself can be
    # inspected without a live model run.
    problems = _review_problem(
        sidecar, frozen, require_live=not getattr(args, "allow_scripted", False),
        receipt_root=session.root if session is not None else None,
    )
    emit({
        "ok": not problems,
        "operation": "review-verify",
        "campaign_id": sidecar.campaign_id,
        "candidate_commit": frozen.get("commit"),
        "qualified": not problems,
        "problems": problems,
        "session": session.as_dict() if session is not None else None,
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
    session_id = getattr(args, "session_id", None) or None
    # Resolve, do not create: a refusal (missing/corrupt anchor, exhausted
    # limits) must not leave an empty per-session root behind.
    session = _session_layout(sidecar.run_root, session_id, create=False,
                              must_exist=False)
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

        # The receipt is read from this session's root: a reconnect resumes it,
        # while another session's receipt is never adopted as this session's
        # qualification.
        receipt = receipts.load_current_receipt(session.root)
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

        bound_replay_inputs = (receipt.get("outcome") or {}).get("replay_inputs")
        if bound_replay_inputs is not None:
            current_replay_inputs = _replay_inputs_rows(sidecar)
            if current_replay_inputs != bound_replay_inputs:
                changed = sorted(
                    row["path"]
                    for row, was in zip(current_replay_inputs, bound_replay_inputs)
                    if row != was
                )
                raise Refusal(
                    "replay_inputs_changed",
                    "the declared replay inputs changed since the validated run"
                    + (f": {', '.join(changed)}" if changed else "")
                    + "; re-run validate",
                )

        bound_inputs = (receipt.get("outcome") or {}).get("inputs_digest")
        if bound_inputs is not None:
            current_inputs = snapshot.ignored_digest(sidecar.workspace)
            if current_inputs != bound_inputs:
                raise Refusal(
                    "inputs_changed",
                    "the checker inputs changed since the validated run; "
                    "re-run validate",
                )


        # A fresh qualification run is a checker execution: it is charged
        # against the same operator deadline as validate and refuses before
        # starting.
        history = _campaign_attempt_history(sidecar.run_root)
        stop = _campaign_safety_stop()
        if stop is not None:
            payload = _policy_stop_payload(sidecar, stop, history, current)
            payload["operation"] = "finalize"
            emit(payload)
            return EXIT_OK

        attempt_id = _begin_attempt(
            session, operation="finalize",
            candidate_digest=(current.get("candidate") or {}).get("digest"))
        started = time.monotonic()
        checker_exception = None
        try:
            outcome = run_accept(_AcceptShim(sidecar), sidecar.workspace)
        except Exception as exc:  # noqa: BLE001 - the attempt must be recorded
            checker_exception = exc
            outcome = {
                "ok": False,
                "verdict": None,
                "exit_code": None,
                "diagnostics": f"{type(exc).__name__}: {exc}",
            }
        duration_ms = int((time.monotonic() - started) * 1000)
        classification, kind, verdict = classify(outcome)
        if checker_exception is not None:
            classification, kind = "infrastructure_error", "checker_exception"

        after = _identity(sidecar, _git(infra_root(), "rev-parse", "HEAD"))
        drift = after != current
        _finish_attempt(
            session, attempt_id, operation="finalize",
            classification="identity_drift" if drift else classification,
            kind="identity_drift" if drift else kind,
            verdict=verdict,
            diagnostics=str(outcome.get("diagnostics") or ""),
            exit_code=outcome.get("exit_code"), duration_ms=duration_ms,
            qualified=(not drift and classification == "verdict"
                       and verdict == "pass"))
        if drift:
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
            receipts.write_receipt(session.root, fresh)
            raise Refusal(
                "fresh_validation_failed",
                "the confirmation run for this candidate returned "
                f"{verdict or classification}; a stored pass cannot authorize completion",
            )

        frozen = _freeze_once(sidecar, current)
        replay = _replay_frozen(sidecar, frozen, session=session,
                                session_id=session_id)
        if sidecar.require_independent_review:
            problems = _review_problem(sidecar, frozen, require_live=True,
                                       receipt_root=session.root)
            if problems:
                raise Refusal("review_required", "; ".join(problems))

        record = {
            "schema": "eu4-dsh-finalization/v1",
            "campaign_id": sidecar.campaign_id,
            "identity_digest": receipts.identity_digest(current),
            "frozen_candidate": frozen,
            "replay": replay,
            "ignored_inputs": snapshot.ignored_inputs(sidecar.workspace),
            "fresh_verdict": verdict,
            "duration_ms": duration_ms,
            "session": {"session_id": session_id},
            "session_root": str(session.root),
            "session_layout": session.kind,
            "created_at": _now(),
        }
        record_path = session.finalization_path
        receipts.atomic_write(record_path, receipts.canonical_json(record))
        emit({
            "ok": True,
            "operation": "finalize",
            "campaign_id": sidecar.campaign_id,
            "classification": "verdict",
            "checker_verdict": verdict,
            "goal_policy": "qualified",
            "frozen_candidate": frozen,
            "replay": replay,
            "identity_digest": receipts.identity_digest(current),
            "finalization": record_path.name,
            "session": session.as_dict(),
        })
        return EXIT_OK
    finally:
        lock.__exit__(None, None, None)


def _context_refusal(sidecar: Sidecar, *, status: str, kind: str,
                     detail: str, anchored: bool = True,
                     drift: list[str] | None = None) -> int:
    """One explicit, non-authorizing context result.

    There is deliberately no ``startup_text`` or ``goal_text`` here: a caller
    must not be able to mistake a drift or a missing anchor for an authorized
    startup context.
    """
    emit({
        "ok": False,
        "operation": "context",
        "campaign_id": sidecar.campaign_id,
        "status": status,
        "outcome": "refused",
        "anchored": anchored,
        "classification": "integrity_refusal",
        "kind": kind,
        "goal_policy": GOAL_POLICY["unavailable"],
        "drift": list(drift or []),
        "detail": detail,
    })
    return EXIT_INTEGRITY


def op_context(args) -> int:
    """Read-only startup/reconnect context; never anchors, probes or locks.

    The initial goal message cannot cover a session resumed after a restart, so
    this operation rebuilds the same compact facts from the sidecar, anchor and
    durable attempt history without mutating the campaign. It also *verifies*
    the boundary: the sidecar, workspace, policy, limits and campaign
    identities must still match the anchor recorded by ``prepare``. Any drift is
    an explicit refusal that names the changed fields; no authorized-looking
    startup text is emitted.
    """
    try:
        sidecar = load_sidecar(Path(args.sidecar))
    except SidecarError as exc:
        emit({
            "ok": False,
            "operation": "context",
            "status": "refused",
            "outcome": "refused",
            "anchored": False,
            "classification": "integrity_refusal",
            "kind": "sidecar",
            "goal_policy": GOAL_POLICY["unavailable"],
            "drift": [],
            "detail": str(exc),
        })
        return EXIT_INTEGRITY
    run_paths = receipts.paths(sidecar.run_root)
    try:
        anchor = _read_anchor(run_paths.identity)
    except Refusal as exc:
        return _context_refusal(
            sidecar, status="refused", kind=exc.kind,
            detail=exc.detail or str(exc),
        )
    if anchor is None:
        return _context_refusal(
            sidecar, status="unprepared", kind="not_prepared", anchored=False,
            detail=(
                "no identity anchor exists for this campaign, so no startup "
                "context is authorized; start the campaign through "
                "dsh/scripts/run.sh --campaign (or run-campaign.sh) so the "
                "anchor is created from a prepared state"
            ),
        )

    # Compare the facts that need no candidate walk first: a changed workspace,
    # policy or limits must be reported as such even when the new workspace or
    # policy would make the execution-root or candidate walk refuse for a
    # different reason.
    drift = _authority_drift(anchor, _authority_surface(sidecar))
    if drift:
        return _context_refusal(
            sidecar, status="drift", kind="identity_modified", drift=drift,
            detail=("protected material changed since prepare: "
                    + ", ".join(drift)),
        )

    try:
        roots = _roots(sidecar)
        identities = _identity(
            sidecar, _git(infra_root(), "rev-parse", "HEAD"))
    except Refusal as exc:
        return _context_refusal(
            sidecar, status="refused", kind=exc.kind,
            detail=exc.detail or str(exc),
        )

    authority = _authority_snapshot(sidecar, identities)
    drift = _authority_drift(anchor, authority)
    if drift:
        return _context_refusal(
            sidecar, status="drift", kind="identity_modified", drift=drift,
            detail=("protected material changed since prepare: "
                    + ", ".join(drift)),
        )

    identity_digest = receipts.identity_digest(identities)
    context_session = _session_layout(
        sidecar.run_root, getattr(args, "session_id", None), create=False)
    verification = _qualification_state(
        sidecar, identities, root=context_session.root)
    block = _startup_block(sidecar, roots, context_session)
    attempts = _validation_attempts(_campaign_attempt_history(sidecar.run_root))
    text = _startup_text(
        block, attempts=attempts, identity_digest=identity_digest,
        verification=verification,
        candidate_digest=(identities.get("candidate") or {}).get("digest"))
    emit({
        "ok": True,
        "operation": "context",
        "campaign_id": sidecar.campaign_id,
        "status": "authorized",
        "outcome": "authorized",
        "anchored": True,
        "anchored_at": anchor.get("prepared_at"),
        "drift": [],
        "identity_digest": identity_digest,
        "authority_digest": receipts.identity_digest(authority),
        "verification": verification,
        "startup": block,
        "startup_text": text,
        "goal_text": sidecar.objective_text + "\n\n" + text,
        "next_command": " ".join(block.get("first_command") or []),
    })
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m automation.dsh_bridge",
        description="Deterministic EU4 project operations for DSH campaigns.",
    )
    sub = parser.add_subparsers(dest="operation", required=True)

    for name in ("prepare", "context", "validate", "finalize",
                 "checker-compat", "review-run", "review-verify"):
        child = sub.add_parser(name)
        child.add_argument("--sidecar", required=True)
        if name in ("prepare", "context", "validate", "finalize",
                    "review-run", "review-verify"):
            child.add_argument("--session-id", default=None)
        if name == "prepare":
            child.add_argument("--create-anchor", action="store_true")
            child.add_argument(
                "--reanchor", action="store_true",
                help="explicitly replace the existing identity anchor after a "
                     "reviewed code revision change")
        if name == "review-verify":
            child.add_argument("--allow-scripted", action="store_true")
        if name == "review-run":
            child.add_argument("--reviewer-cmd", default=None)
            child.add_argument("--timeout", type=float, default=900.0)
    return parser


OPERATIONS = {
    "prepare": op_prepare,
    "context": op_context,
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
              "kind": "sidecar", "goal_policy": GOAL_POLICY["unavailable"],
              "detail": str(exc)})
        return EXIT_INTEGRITY
    except Refusal as exc:
        emit({"ok": False, "operation": args.operation,
              "classification": "integrity_refusal", "kind": exc.kind,
              "goal_policy": (
                  GOAL_POLICY["fail"] if exc.kind in REMEDIABLE_REFUSALS
                  else GOAL_POLICY["unavailable"]
              ),
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
            "goal_policy": GOAL_POLICY["unavailable"],
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return EXIT_INTEGRITY


if __name__ == "__main__":
    raise SystemExit(main())
