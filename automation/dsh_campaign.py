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

# Reader defaults. A sidecar may omit exactly these fields; the resolved
# Sidecar value is then the constant below, never a value inferred from the
# environment or the campaign. They are documented here because the bridge and
# the launcher profiles use the resolved fields as declarations. A present but
# malformed value is rejected by ``_optional_*`` instead of being coerced to a
# default or a truthy string.
DEFAULT_RUN_ROOT = DEFAULT_STATE_ROOT  # <state root>/<campaign id>
DEFAULT_WORKSPACE_BASE = "run_root"
DEFAULT_CANDIDATE_WORKSPACE = "workspace"  # <workspace base>/workspace
DEFAULT_RETRY_MODE = "deterministic"
DEFAULT_CONFIRMATION_ROUNDS = 0
DEFAULT_MAX_IDENTICAL_RETRIES = 0
DEFAULT_MAX_ROUNDS = 6
DEFAULT_REQUIRE_CHECKER_PASS = True
DEFAULT_REQUIRE_INDEPENDENT_REVIEW = False
DEFAULT_MAX_VALIDATIONS = 8
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 60.0
DEFAULT_WALL_CLOCK_SECONDS = 1800.0
DEFAULT_MAX_REVIEW_ATTEMPTS = 2
DEFAULT_EVIDENCE_PATHS: tuple[str, ...] = ()
DEFAULT_REPLAY_INPUTS: tuple[str, ...] = ()
DEFAULT_ALLOW_INCOMPLETE_INPUTS = False
DEFAULT_LIVE_CAPTURE_ENABLED = False
DEFAULT_ALLOW_SYMLINKS = False


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
    replay_inputs: tuple[str, ...] = ()
    allow_incomplete_inputs: bool = False


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


def _optional_string(value, name: str, default: str) -> str:
    """A documented default applies only to an absent field."""
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise SidecarError(f"{name} must be a non-empty string")
    return value


def _optional_bool(value, name: str, default: bool) -> bool:
    if value is None:
        return default
    if type(value) is not bool:
        raise SidecarError(f"{name} must be a boolean")
    return value


def _optional_int(value, name: str, default: int) -> int:
    if value is None:
        return default
    if type(value) is not int:
        raise SidecarError(f"{name} must be an integer")
    return value


def _optional_number(value, name: str, default: float) -> float:
    if value is None:
        return default
    if type(value) not in (int, float):
        raise SidecarError(f"{name} must be a number")
    return float(value)


def _string_list(value, name: str) -> tuple[str, ...]:
    """An absent list is the documented empty default; anything else is typed."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise SidecarError(f"{name} must be a list")
    entries: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SidecarError(f"{name} entries must be non-empty strings")
        entries.append(item)
    return tuple(entries)


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
    workspace_base = _load_workspace_base(data)
    workspace = _load_workspace(workspace_raw, run_root, workspace_base, infra)
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
        replay_inputs=_load_replay_inputs(data),
        allow_incomplete_inputs=_optional_bool(
            data.get("allow_incomplete_inputs"), "allow_incomplete_inputs",
            DEFAULT_ALLOW_INCOMPLETE_INPUTS),
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
    """Validate the trusted material.

    The checker is *not* required to live inside the campaign directory: a
    campaign states its own accept command, so the sidecar cannot inject a
    checker, and campaigns legitimately reuse a shared one. The seed is checked
    because it is the directory the candidate is copied from.
    """
    if not contract.checker.is_file():
        raise SidecarError(f"checker not found: {contract.checker}")
    if contract.seed is not None and not contract.seed.is_dir():
        raise SidecarError(f"seed workspace not found: {contract.seed}")


def _load_candidate(data: dict, contract: _Contract):
    candidate = _require_object(data.get("candidate"), "candidate")
    mode = _optional_string(candidate.get("mode"), "candidate.mode", "")
    if mode not in MODES:
        raise SidecarError("candidate.mode must be seed or worktree")

    declared = data.get("candidate_baseline_commit")
    if declared is not None and not isinstance(declared, str):
        raise SidecarError("candidate_baseline_commit must be a string")
    baseline = declared or contract.baseline_commit
    if declared and contract.baseline_commit and declared != contract.baseline_commit:
        raise SidecarError(
            "candidate_baseline_commit does not match the source campaign pin; "
            "these are different identities and must not be substituted"
        )

    workspace_raw = _optional_string(
        candidate.get("workspace"), "candidate.workspace", "")
    seed = None
    if mode == "worktree":
        if not workspace_raw:
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
    raw = _optional_string(data.get("run_root"), "run_root", "")
    if raw:
        return _resolve(infra, raw)
    # Documented default: the host-owned trusted state root, one directory per
    # campaign. Absent means this exact path; it is not inherited from a
    # sibling sidecar or an environment variable.
    return (DEFAULT_RUN_ROOT.expanduser() / campaign_id).resolve()


WORKSPACE_BASES = ("run_root", "infra")


def _load_replay_inputs(data: dict) -> tuple[str, ...]:
    """Files beside the candidate workspace that the checker also reads.

    A reconstruction checker keeps its research checkpoint next to the worktree,
    not inside it, so replay has to reproduce that state or a finished candidate
    looks unfinished. Declared, not guessed: this must never copy arbitrary
    siblings into a replay directory. An absent list is the documented empty
    default; a malformed entry fails closed.
    """
    raw = data.get("replay_inputs")
    if raw is None:
        return DEFAULT_REPLAY_INPUTS
    names = _string_list(raw, "replay_inputs")
    for entry in names:
        if "/" in entry or "\\" in entry or entry in (".", ".."):
            raise SidecarError(f"replay_inputs must be plain file names: {entry!r}")
    return names


def _load_workspace_base(data: dict) -> str:
    candidate = _require_object(data.get("candidate"), "candidate")
    base = _optional_string(
        candidate.get("workspace_base"), "candidate.workspace_base",
        DEFAULT_WORKSPACE_BASE)
    if base not in WORKSPACE_BASES:
        raise SidecarError("candidate.workspace_base must be run_root or infra")
    return base


def _load_workspace(raw: str, run_root: Path, base: str, infra: Path) -> Path:
    """Where the agent edits.

    ``infra`` puts the candidate inside the trusted checkout, which is what the
    DSH sandbox can reach, while the qualification records stay in the host state
    root that the agent cannot write. ``run_root`` keeps them together for
    throwaway experiments. An absent workspace resolves to the documented
    ``<workspace base>/workspace`` default.
    """
    if not raw:
        return ((infra if base == "infra" else run_root)
                / DEFAULT_CANDIDATE_WORKSPACE).resolve()
    declared = Path(raw).expanduser()
    if declared.is_absolute():
        return declared.resolve()
    root = infra if base == "infra" else run_root
    return (root / declared).resolve()


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
    allowed = _string_list(document.get("allowed_paths"),
                           "candidate.policy.allowed_paths")
    forbidden = _string_list(document.get("forbidden_paths"),
                             "candidate.policy.forbidden_paths")
    allow_symlinks = _optional_bool(
        document.get("allow_symlinks"), "candidate.policy.allow_symlinks",
        DEFAULT_ALLOW_SYMLINKS)
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
    mode = _optional_string(retry.get("mode"), "checker_retry.mode",
                            DEFAULT_RETRY_MODE)
    if mode not in RETRY_MODES:
        raise SidecarError("checker_retry.mode must be deterministic or stateful")
    rounds = _optional_int(retry.get("confirmation_rounds"),
                           "checker_retry.confirmation_rounds",
                           DEFAULT_CONFIRMATION_ROUNDS)
    identical = _optional_int(retry.get("max_identical_retries"),
                              "checker_retry.max_identical_retries",
                              DEFAULT_MAX_IDENTICAL_RETRIES)
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
    raw = evidence.get("paths")
    if raw is None:
        return DEFAULT_EVIDENCE_PATHS
    return _string_list(raw, "evidence.paths")


def _load_live(data: dict) -> bool:
    live = _require_object(data.get("live_capture"), "live_capture")
    enabled = _optional_bool(live.get("enabled"), "live_capture.enabled",
                             DEFAULT_LIVE_CAPTURE_ENABLED)
    if enabled:
        raise SidecarError(
            "live_capture.enabled is not a sidecar feature: EU4 launch exclusivity is "
            "enforced by the host-wide process lease, and the launch ledger accounts for "
            "budget, not concurrency"
        )
    return enabled


def _load_goal_limits(data: dict):
    goal = _require_object(data.get("goal"), "goal")
    limits = _require_object(data.get("limits"), "limits")
    max_rounds = _optional_int(goal.get("max_rounds"), "goal.max_rounds",
                               DEFAULT_MAX_ROUNDS)
    if max_rounds < 1:
        raise SidecarError("goal.max_rounds must be positive")
    require_pass = _optional_bool(goal.get("require_checker_pass"),
                                  "goal.require_checker_pass",
                                  DEFAULT_REQUIRE_CHECKER_PASS)
    require_review = _optional_bool(goal.get("require_independent_review"),
                                    "goal.require_independent_review",
                                    DEFAULT_REQUIRE_INDEPENDENT_REVIEW)
    max_validations = _optional_int(limits.get("max_validations"),
                                    "limits.max_validations",
                                    DEFAULT_MAX_VALIDATIONS)
    if max_validations < 1:
        raise SidecarError("limits.max_validations must be positive")
    timeout = _optional_number(limits.get("validation_timeout_seconds"),
                               "limits.validation_timeout_seconds",
                               DEFAULT_VALIDATION_TIMEOUT_SECONDS)
    wall = _optional_number(limits.get("wall_clock_seconds"),
                            "limits.wall_clock_seconds",
                            DEFAULT_WALL_CLOCK_SECONDS)
    review = _optional_int(limits.get("max_review_attempts"),
                           "limits.max_review_attempts",
                           DEFAULT_MAX_REVIEW_ATTEMPTS)
    if timeout <= 0 or wall <= 0:
        raise SidecarError("limits timeouts must be positive")
    if review < 0:
        raise SidecarError("limits.max_review_attempts must be non-negative")
    return (max_rounds, require_pass, require_review, max_validations, timeout, wall, review)
