"""The campaign fields both backends share.

A campaign directory states an objective, a checker, an optional verification
manifest, a candidate workspace and a checker timeout. None of those are
backend-specific, so both readers -- the OpenCode loader and the DSH sidecar
reader -- resolve them here instead of each keeping its own copy of the parsing
and its own error messages.

Backend-specific concerns stay out: the OpenCode loader adds runtime/model
fields, and the DSH sidecar adds orchestration limits and a run root.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


class CampaignContractError(ValueError):
    """A campaign directory does not state a usable contract."""


@dataclass(frozen=True)
class CampaignContract:
    id: str
    path: Path
    root: Path
    objective_path: Path
    objective_text: str
    objective_digest: str
    accept: tuple[str, ...]
    checker: Path
    verification_manifest: Path | None
    workspace_mode: str
    seed: Path | None
    worktree: Path | None
    baseline_commit: str | None
    accept_timeout: float
    raw: dict


def read_contract(path: Path) -> CampaignContract:
    """Resolve one campaign directory's shared contract."""
    path = Path(path).resolve()
    if not path.is_file():
        raise CampaignContractError(f"campaign not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignContractError(f"campaign is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignContractError("campaign must be a JSON object")
    missing = [
        key
        for key in ("id", "objective", "workspace", "accept", "accept_timeout_seconds")
        if key not in data
    ]
    if missing:
        raise CampaignContractError("campaign missing " + ", ".join(missing))

    root = path.parent
    objective_path, objective_text = _objective(root, data["objective"])
    accept, checker = _accept(root, data["accept"])
    manifest = _manifest(root, data.get("verification_manifest"))
    mode, seed, worktree, baseline = _workspace(root, data["workspace"])
    return CampaignContract(
        id=str(data["id"]),
        path=path,
        root=root,
        objective_path=objective_path,
        objective_text=objective_text,
        objective_digest=hashlib.sha256(objective_text.encode("utf-8")).hexdigest(),
        accept=accept,
        checker=checker,
        verification_manifest=manifest,
        workspace_mode=mode,
        seed=seed,
        worktree=worktree,
        baseline_commit=baseline,
        accept_timeout=float(data["accept_timeout_seconds"]),
        raw=data,
    )


def _objective(root: Path, raw) -> tuple[Path, str]:
    candidate = Path(str(raw))
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_file():
        raise CampaignContractError(f"objective not found: {resolved}")
    # Containment is a backend policy, not a shared one: existing campaigns
    # legitimately reuse a checker or objective from a sibling directory, so the
    # contract only resolves what the campaign states.
    return resolved, resolved.read_text(encoding="utf-8")


def _accept(root: Path, raw) -> tuple[tuple[str, ...], Path]:
    if not isinstance(raw, list) or not raw:
        raise CampaignContractError("campaign accept must be a non-empty list")
    accept = tuple(str(part) for part in raw)
    for part in accept:
        candidate = Path(part)
        resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if resolved.is_file():
            return accept, resolved
    raise CampaignContractError("accept command does not name a checker file")


def _manifest(root: Path, raw) -> Path | None:
    if not raw:
        return None
    candidate = Path(str(raw))
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def _workspace(root: Path, spec) -> tuple[str, Path | None, Path | None, str | None]:
    if isinstance(spec, str):
        return "seed", (root / spec).resolve(), None, None
    if not isinstance(spec, dict) or spec.get("mode") != "worktree":
        raise CampaignContractError("workspace must be a seed path or a worktree object")
    raw = Path(str(spec["path"]))
    live = raw if raw.is_absolute() else _repo_root(root) / raw
    commit = str(spec.get("commit") or "")
    if not commit:
        raise CampaignContractError("worktree commit is required")
    return "worktree", None, live.resolve(), commit


def _repo_root(start: Path) -> Path:
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise CampaignContractError("repository root not found")
