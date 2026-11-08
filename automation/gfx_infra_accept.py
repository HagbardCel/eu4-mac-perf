"""Bootstrap acceptance: frozen infrastructure tests plus scope checks.

The bootstrap campaign develops the trusted checker revision, checkpoint,
ledger, contract runner and coordinator. Its acceptance is the frozen test
suite in ``tests/test_gfx_infra_frozen.py`` run from this checkout against the
candidate workspace, plus the candidate's own regression modules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_draw_accept_v2 import changed_paths, path_allowed, worktree_problem  # noqa: E402

FROZEN = ROOT / "tests" / "test_gfx_infra_frozen.py"


def emit(verdict: str, diagnostics: str, **extra: object) -> dict:
    value = {"verdict": verdict, "diagnostics": diagnostics}
    value.update(extra)
    return value


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run(workspace: Path, arguments: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace / "benchmark") + os.pathsep + str(workspace)
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    return subprocess.run(
        arguments, cwd=str(workspace), env=env, capture_output=True,
        text=True, timeout=timeout, check=False,
    )


def check(workspace: Path, campaign_dir: Path) -> dict:
    workspace = Path(workspace).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    try:
        campaign = _json(campaign_dir / "campaign.json")
        config = _json(campaign_dir / "infra.json")
        pin = str(campaign["workspace"]["commit"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"bootstrap configuration unreadable: {exc}")
    problem = worktree_problem(workspace, pin)
    if problem:
        return emit(problem[0], problem[1])
    try:
        paths = changed_paths(workspace)
    except OSError as exc:
        return emit("unavailable", f"worktree status unavailable: {exc}")
    allowed = [str(item) for item in config["allowed_paths"]]
    illegal = [path for path in paths if not path_allowed(path, allowed)]
    if illegal:
        return emit("fail", "source scope violated: " + ", ".join(illegal[:8]))
    frozen = [str(item) for item in config.get("forbidden_paths", [])]
    touched = [path for path in paths if any(
        path == pattern or path.startswith(pattern.rstrip("/") + "/")
        for pattern in frozen
    )]
    if touched:
        return emit("fail", "frozen path modified: " + ", ".join(touched[:8]))

    if not FROZEN.is_file():
        return emit("unavailable", "frozen infrastructure test suite is missing")
    completed = _run(workspace, [sys.executable, str(FROZEN)])
    if completed.returncode != 0:
        return emit(
            "fail",
            "frozen infrastructure tests failed: "
            + (completed.stderr or completed.stdout or "")[-3000:],
        )
    modules = [str(item) for item in config.get("test_modules", [])]
    if modules:
        completed = _run(
            workspace, [sys.executable, "-m", "unittest", *modules]
        )
        if completed.returncode != 0:
            return emit(
                "fail",
                "candidate regression tests failed: "
                + (completed.stderr or completed.stdout or "")[-3000:],
            )
    return emit(
        "pass",
        json.dumps({"changed_paths": len(paths), "frozen": str(FROZEN)}, sort_keys=True),
    )
