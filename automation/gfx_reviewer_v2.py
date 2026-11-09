"""Resumable reviewer materialization for generated L3.2 campaigns.

The legacy ``gfx_candidate_snapshot.materialize_reviewer`` stays unchanged
because it is pinned by the L3.2b-I manifest. This module adds idempotent
reviewer reuse and a scoped environment for the reviewer subprocess.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from automation.gfx_candidate_snapshot import SnapshotError
from automation.state import repo_root


def materialize_reviewer(
    campaign_dir: Path,
    candidate: dict,
    round_id: int = 1,
    *,
    review_id: str | None = None,
) -> dict:
    repo = repo_root(Path(campaign_dir).resolve())
    review_id = review_id or f"eu4-l32b-review-{candidate['campaign_id']}-r{round_id}"
    review_root = repo / ".local" / "automation" / review_id
    reviewer_dir = review_root / "reviewer"
    report = review_root / "review.json"
    env = {
        "EU4_REVIEW_REPORT_PATH": str(report),
        "EU4_REVIEW_EXPECTED_CANDIDATE": candidate["candidate_commit"],
        "EU4_REVIEW_EXPECTED_ORACLE": candidate["oracle_manifest_sha256"],
    }
    if review_root.exists():
        campaign_path = reviewer_dir / "campaign.json"
        if not campaign_path.is_file():
            raise SnapshotError(f"reviewer campaign is incomplete: {review_root}")
        existing = json.loads(campaign_path.read_text(encoding="utf-8"))
        pinned = (existing.get("workspace") or {}).get("commit")
        if pinned != candidate["candidate_commit"]:
            raise SnapshotError(
                "existing reviewer campaign targets a different candidate"
            )
        return {
            "review_id": review_id,
            "campaign": str(campaign_path),
            "workspace": str(review_root / "workspace"),
            "report": str(report),
            "env": env,
            "command": f"python3 -m automation.cli run {campaign_path}",
        }
    shutil.copytree(ROOT / "campaigns" / "eu4-l32b-reviewer", reviewer_dir)
    campaign = json.loads((reviewer_dir / "campaign.json").read_text())
    campaign["id"] = review_id
    campaign["workspace"] = {
        "mode": "worktree",
        "path": str(review_root / "workspace"),
        "commit": candidate["candidate_commit"],
    }
    (reviewer_dir / "campaign.json").write_text(
        json.dumps(campaign, indent=2) + "\n"
    )
    workspace = review_root / "workspace"
    completed = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(workspace),
         candidate["candidate_commit"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        shutil.rmtree(review_root, ignore_errors=True)
        raise SnapshotError(
            completed.stderr.strip() or "reviewer worktree creation failed"
        )
    return {
        "review_id": review_id,
        "campaign": str(reviewer_dir / "campaign.json"),
        "workspace": str(review_root / "workspace"),
        "report": str(report),
        "env": env,
        "command": f"python3 -m automation.cli run {reviewer_dir / 'campaign.json'}",
    }
