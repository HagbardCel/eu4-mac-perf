"""Backend-neutral DSH campaign sidecar and execution-root resolution.

The OpenCode loader (``automation.state.load_campaign``) deliberately requires
``runtime == "opencode"``. A DSH campaign references the same campaign directory
without editing it: this module reads the shared contract fields (objective,
accept/checker, optional verification manifest) and adds the DSH-specific
execution configuration a sidecar carries.

Nothing here runs a checker or writes evidence; ``automation.dsh_bridge`` does
that. Keeping the reading and validation separate makes every rejection testable
without touching the filesystem beyond the files under inspection.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from automation.campaign_contract import CampaignContractError, read_contract
from automation.verification_manifest import canonical_json, digest_text

SCHEMA = "eu4-dsh-campaign/v1"
MODES = ("seed", "worktree")
RETRY_MODES = ("deterministic", "stateful")
DEFAULT_STATE_ROOT = Path("~/.local/share/eu4-dsh/state")


class SidecarError(RuntimeError):
    """A sidecar, policy or execution-root problem that must fail closed."""


@dataclass(frozen=True)
class Sidecar:
    """One resolved DSH campaign: shared contract plus DSH execution config."""

    campaign_id: str
    sidecar_path: Path
    sidecar_sha256: str
    source_campaign: Path
    source_campaign_sha256: str
    campaign_root: Path
    objective_text: str
    objective_digest: str
    accept: tuple[str, ...]
    checker: Path
    verification_manifest: Path | None
    mode: str
    workspace: Path
    seed: Path | None
    candidate_baseline_commit: str | None
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    allow_symlinks: bool
    policy_digest: str
    retry_mode: str
    confirmation_rounds: int
    max_identical_retries: int
    evidence_paths: tuple[str, ...]
    live_capture_enabled: bool
    max_rounds: int
    require_checker_pass: bool
    require_independent_review: bool
    max_validations: int
    validation_timeout_seconds: float
    wall_clock_seconds: float
    max_review_attempts: int
    run_root: Path


def path_allowed(rel: str, patterns) -> bool:
    """Mirror ``automation.gfx_draw_accept.path_allowed``.

    The trusted checker module is not imported here because importing it pulls
    the benchmark packet modules; ``tests/test_dsh_campaign.py`` asserts the two
    implementations agree so the copy cannot drift.
    """
    for pattern in patterns:
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
        elif fnmatch.fnmatch(rel, pattern):
            return True
    return False


def infra_root() -> Path:
    """The trusted checkout that owns this module."""
    return Path(__file__).resolve().parents[1]


def inside(child, parent) -> bool:
    """True when *child* is at or below *parent* after resolution."""
    return _inside(Path(child), Path(parent))


def _inside(child: Path, parent: Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except ValueError:
        return False


def _resolve(base: Path, value) -> Path:
    candidate = Path(str(value)).expanduser()
    return (candidate if candidate.is_absolute() else Path(base) / candidate).resolve()


def _require_object(value, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SidecarError(f"{name} must be an object")
    return value


def load_sidecar(path: Path, *, infra: Path | None = None) -> Sidecar:
    """Read and validate one sidecar. Raises {@link SidecarError} on any doubt."""
    path = Path(path).resolve()
    infra = Path(infra or infra_root()).resolve()
    if not path.is_file():
        raise SidecarError(f"sidecar not found: {path}")
    raw_text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SidecarError(f"sidecar is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SidecarError("sidecar must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise SidecarError(f"schema must be {SCHEMA}")

    campaign_id = str(data.get("campaign_id") or "").strip()
    if not campaign_id:
        raise SidecarError("campaign_id is required")
    if "/" in campaign_id or campaign_id in (".", ".."):
        raise SidecarError("campaign_id must be a plain identifier")

    source_campaign = _source_campaign(data, infra)
    contract = _contract(source_campaign)
    campaign_root = contract.campaign_root
    campaign = contract.raw
    _require_policy_containment(contract)

    objective_text = contract.objective_text
    objective_digest = contract.objective_digest
    accept = contract.accept
    checker = contract.checker
    manifest = contract.verification_manifest
    if manifest is not None and not manifest.is_file():
        raise SidecarError(f"verification manifest not found: {manifest}")

    mode, workspace_raw, seed, baseline = _load_candidate(data, contract)
    run_root = _load_run_root(data, infra, campaign_id)
    workspace = _load_workspace(workspace_raw, run_root)
    if run_root == workspace or _inside(run_root, workspace):
        raise SidecarError("run_root must not be inside the candidate workspace")
    if _inside(run_root, infra / ".local" / "automation"):
        raise SidecarError("run_root must not live under .local/automation (OpenCode state)")

    allowed, forbidden, allow_symlinks, policy_digest = _load_policy(
        data, campaign_root, mode, seed
    )
    retry_mode, confirmation_rounds, max_identical = _load_retry(data)
    evidence_paths = _load_evidence(data)
    live_enabled = _load_live(data)
    (max_rounds, require_pass, require_review, max_validations,
     timeout, wall, review_attempts) = _load_goal_limits(data)

    return Sidecar(
        campaign_id=campaign_id,
        sidecar_path=path,
        sidecar_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        source_campaign=source_campaign,
        source_campaign_sha256=hashlib.sha256(
            source_campaign.read_bytes()
        ).hexdigest(),
        campaign_root=campaign_root,
        objective_text=objective_text,
        objective_digest=objective_digest,
        accept=accept,
        checker=checker,
        verification_manifest=manifest,
        mode=mode,
        workspace=workspace,
        seed=seed,
        candidate_baseline_commit=baseline,
        allowed_paths=allowed,
        forbidden_paths=forbidden,
        allow_symlinks=allow_symlinks,
        policy_digest=policy_digest,
        retry_mode=retry_mode,
        confirmation_rounds=confirmation_rounds,
        max_identical_retries=max_identical,
        evidence_paths=evidence_paths,
        live_capture_enabled=live_enabled,
        max_rounds=max_rounds,
        require_checker_pass=require_pass,
        require_independent_review=require_review,
        max_validations=max_validations,
        validation_timeout_seconds=timeout,
        wall_clock_seconds=wall,
        max_review_attempts=review_attempts,
        run_root=run_root,
    )


def _source_campaign(data: dict, infra: Path) -> Path:
    raw = data.get("source_campaign")
    if not raw:
        raise SidecarError("source_campaign is required")
    source = _resolve(infra, raw)
    if not source.is_file():
        raise SidecarError(f"source campaign not found: {source}")
    if not _inside(source.parent, infra):
        raise SidecarError(
            "campaign root must live inside the trusted checkout: " + str(source.parent)
        )
    return source


class _Contract:
    """The shared campaign contract plus the fields this reader needs."""

    def __init__(self, contract, source: Path):
        self.campaign_root = source.parent
        self.raw = contract.raw
        self.objective_text = contract.objective_text
        self.objective_digest = contract.objective_digest
        self.accept = contract.accept
        self.checker = contract.checker
        self.verification_manifest = contract.verification_manifest
        self.seed = contract.seed
        self.baseline_commit = contract.baseline_commit


def _contract(source: Path) -> _Contract:
    try:
        return _Contract(read_contract(source), source)
    except CampaignContractError as exc:
        raise SidecarError(str(exc)) from exc


def _require_policy_containment(contract: _Contract) -> None:
    """A sidecar may only reference trusted material inside its campaign."""
    try:
        contract.checker.relative_to(contract.campaign_root)
    except ValueError as exc:
        raise SidecarError("checker must live inside the campaign directory") from exc
    if contract.seed is not None and not contract.seed.is_dir():
        raise SidecarError(f"seed workspace not found: {contract.seed}")


def _load_candidate(data: dict, contract: _Contract):
    candidate = _require_object(data.get("candidate"), "candidate")
    mode = str(candidate.get("mode") or "")
    if mode not in MODES:
        raise SidecarError("candidate.mode must be seed or worktree")

    declared = data.get("candidate_baseline_commit")
    baseline = str(declared) if declared else contract.baseline_commit
    if declared and contract.baseline_commit and str(declared) != contract.baseline_commit:
        raise SidecarError(
            "candidate_baseline_commit does not match the source campaign pin; "
            "these are different identities and must not be substituted"
        )

    workspace_raw = str(candidate.get("workspace") or "")
    seed = None
    if mode == "worktree":
        if not candidate.get("workspace"):
            raise SidecarError("candidate.workspace is required in worktree mode")
        if not baseline:
            raise SidecarError("worktree mode requires a candidate_baseline_commit")
    else:
        if contract.seed is None:
            raise SidecarError(
                "seed mode requires the source campaign to declare a seed workspace"
            )
        seed = contract.seed
    return mode, workspace_raw, seed, baseline


def _load_run_root(data: dict, infra: Path, campaign_id: str) -> Path:
    raw = data.get("run_root")
    if raw:
        return _resolve(infra, raw)
    # Default: the host-owned trusted state root, one directory per campaign.
    return (DEFAULT_STATE_ROOT.expanduser() / campaign_id).resolve()


def _load_workspace(raw: str, run_root: Path) -> Path:
    if not raw:
        return (run_root / "workspace").resolve()
    declared = Path(raw).expanduser()
    if declared.is_absolute():
        return declared.resolve()
    return (run_root / declared).resolve()


def _load_policy(data: dict, campaign_root: Path, mode: str, seed: Path | None):
    candidate = _require_object(data.get("candidate"), "candidate")
    policy_raw = _require_object(candidate.get("policy"), "candidate.policy")
    if "policy_file" in policy_raw:
        path = _resolve(campaign_root, policy_raw["policy_file"])
        if not path.is_file():
            raise SidecarError(f"policy file not found: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise SidecarError("policy file must be a JSON object")
        digest = digest_text(canonical_json(document))
    else:
        document = policy_raw
        digest = digest_text(canonical_json(policy_raw))
    allowed = tuple(str(item) for item in document.get("allowed_paths") or ())
    forbidden = tuple(str(item) for item in document.get("forbidden_paths") or ())
    allow_symlinks = bool(document.get("allow_symlinks") or False)
    if not allowed and mode == "seed" and seed is not None:
        # A seed campaign with no declared policy permits exactly the seed's own
        # file set plus progress.md, derived rather than hardcoded.
        derived = sorted(
            path.relative_to(seed).as_posix()
            for path in seed.rglob("*")
            if path.is_file()
        )
        if "progress.md" not in derived:
            derived.append("progress.md")
        allowed = tuple(derived)
    if not allowed:
        raise SidecarError("candidate.policy declares no allowed_paths")
    return allowed, forbidden, allow_symlinks, digest


def _load_retry(data: dict):
    retry = _require_object(data.get("checker_retry"), "checker_retry")
    mode = str(retry.get("mode") or "deterministic")
    if mode not in RETRY_MODES:
        raise SidecarError("checker_retry.mode must be deterministic or stateful")
    rounds = int(retry.get("confirmation_rounds") or 0)
    identical = int(retry.get("max_identical_retries") or 0)
    if rounds < 0 or identical < 0:
        raise SidecarError("checker_retry counters must be non-negative")
    if mode == "stateful" and rounds < 1:
        raise SidecarError(
            "stateful checkers need confirmation_rounds >= 1; otherwise an unchanged "
            "candidate can never reach its intended confirmation round"
        )
    if mode == "deterministic" and rounds:
        raise SidecarError("confirmation_rounds only applies to stateful checkers")
    return mode, rounds, identical


def _load_evidence(data: dict) -> tuple[str, ...]:
    evidence = _require_object(data.get("evidence"), "evidence")
    return tuple(str(item) for item in evidence.get("paths") or ())


def _load_live(data: dict) -> bool:
    live = _require_object(data.get("live_capture"), "live_capture")
    enabled = bool(live.get("enabled") or False)
    if enabled:
        raise SidecarError(
            "live_capture.enabled must be false: no host-wide EU4 process exclusivity "
            "exists yet, and the launch ledger accounts for budget, not concurrency"
        )
    return enabled


def _load_goal_limits(data: dict):
    goal = _require_object(data.get("goal"), "goal")
    limits = _require_object(data.get("limits"), "limits")
    max_rounds = int(goal.get("max_rounds") or 6)
    if max_rounds < 1:
        raise SidecarError("goal.max_rounds must be positive")
    require_pass = bool(goal.get("require_checker_pass", True))
    require_review = bool(goal.get("require_independent_review") or False)
    max_validations = int(limits.get("max_validations") or 8)
    if max_validations < 1:
        raise SidecarError("limits.max_validations must be positive")
    timeout = float(limits.get("validation_timeout_seconds") or 60)
    wall = float(limits.get("wall_clock_seconds") or 1800)
    review = int(limits.get("max_review_attempts") or 2)
    if timeout <= 0 or wall <= 0:
        raise SidecarError("limits timeouts must be positive")
    return (max_rounds, require_pass, require_review, max_validations, timeout, wall, review)
