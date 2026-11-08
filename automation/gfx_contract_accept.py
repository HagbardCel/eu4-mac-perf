"""Minimal trusted qualification for a proposed contract version.

The candidate proposes a validator module, a spec and tests. This checker
verifies scope and provenance, validates the spec's original-code evidence
references, runs the candidate tests, and requires the candidate validator to
accept the provisional checkpoint's original evidence. It never imports the
candidate validator into its own process; ``gfx_contract_runner`` does that in
a separate process. The independent reviewer's adversarial cases are run by the
coordinator after review.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_checkpoint import validate_checkpoint  # noqa: E402
from automation.gfx_contracts import ContractError, load_contract  # noqa: E402
from automation.gfx_draw_accept_v2 import (  # noqa: E402
    changed_paths,
    path_allowed,
    worktree_problem,
)
from automation.manifest_revision import verify_manifest_at_commit  # noqa: E402
from automation.verification_manifest import load_manifest  # noqa: E402

SCHEMA = "eu4-gfx-contract-spec/v1"


def emit(verdict: str, diagnostics: str, **extra: object) -> dict:
    value = {"verdict": verdict, "diagnostics": diagnostics}
    value.update(extra)
    return value


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run(workspace: Path, arguments: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(workspace / "benchmark") + os.pathsep + str(workspace)
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    return subprocess.run(
        arguments, cwd=str(workspace), env=env, capture_output=True,
        text=True, timeout=timeout, check=False,
    )


def _spec_problem(
    spec: dict, version: str, gaps: set[str], workspace: Path
) -> str | None:
    if spec.get("schema") != SCHEMA:
        return f"contract spec schema must be {SCHEMA}"
    if spec.get("version") != version:
        return "contract spec version differs from the candidate version"
    fields = spec.get("fields")
    if not isinstance(fields, list) or not fields:
        return "contract spec has no fields"
    names = set()
    for field in fields:
        if not isinstance(field, dict):
            return "contract spec field is not an object"
        name = field.get("name")
        if not isinstance(name, str) or not name:
            return "contract spec field has no name"
        names.add(name)
        if not isinstance(field.get("validator"), str) or not field["validator"]:
            return f"contract spec field {name} has no validator"
        refs = field.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            return f"contract spec field {name} has no original-code evidence"
        for ref in refs:
            if not isinstance(ref, dict):
                return f"contract spec field {name} evidence ref is not an object"
            path = ref.get("path")
            digest = ref.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                return f"contract spec field {name} evidence ref is incomplete"
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                return f"contract spec field {name} evidence path is invalid"
            resolved = (workspace / candidate).resolve()
            try:
                resolved.relative_to(workspace.resolve())
            except ValueError:
                resolved = (ROOT / candidate).resolve()
                try:
                    resolved.relative_to(ROOT.resolve())
                except ValueError:
                    return f"contract spec field {name} evidence path escapes the repository"
            if not resolved.is_file():
                return f"contract spec field {name} evidence file is missing"
            if _sha(resolved) != digest:
                return f"contract spec field {name} evidence hash differs"
    if names != gaps:
        return "contract spec fields do not match the checkpoint's contract gaps"
    return None


def check(workspace: Path, campaign_dir: Path) -> dict:
    workspace = Path(workspace).resolve()
    campaign_dir = Path(campaign_dir).resolve()
    try:
        campaign = _json(campaign_dir / "campaign.json")
        config = _json(campaign_dir / "contract.json")
        pin = str(campaign["workspace"]["commit"])
        version = str(config["candidate_version"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"contract campaign configuration unreadable: {exc}")
    problem = worktree_problem(workspace, pin)
    if problem:
        return emit(problem[0], problem[1])
    manifest_path = campaign_dir / str(config["manifest"])
    if not manifest_path.is_file():
        return emit("unavailable", "contract verification manifest is missing")
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return emit("unavailable", f"contract verification manifest unreadable: {exc}")
    errors = verify_manifest_at_commit(ROOT, manifest, pin)
    if errors:
        return emit("unavailable", "contract verification manifest: " + "; ".join(errors[:6]))
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

    module_rel = f"automation/gfx_contracts/{version}.py"
    spec_rel = f"automation/gfx_contracts/{version}.spec.json"
    tests_rel = f"tests/test_gfx_contract_{version}.py"
    for rel in (module_rel, spec_rel, tests_rel):
        if not (workspace / rel).is_file():
            return emit("fail", f"candidate contract is missing {rel}")
    try:
        spec = _json(workspace / spec_rel)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return emit("fail", f"contract spec unreadable: {exc}")
    checkpoint_path = Path(str(config.get("checkpoint") or ""))
    if not checkpoint_path.is_absolute():
        checkpoint_path = (campaign_dir / checkpoint_path).resolve()
    checkpoint_errors = validate_checkpoint(checkpoint_path)
    if checkpoint_errors:
        return emit("unavailable", "checkpoint invalid: " + "; ".join(checkpoint_errors[:6]))
    checkpoint = _json(checkpoint_path)
    gaps = {
        gap.get("name")
        for gap in checkpoint.get("contract_gaps") or []
        if isinstance(gap, dict) and isinstance(gap.get("name"), str)
    }
    spec_error = _spec_problem(spec, version, gaps, workspace)
    if spec_error:
        return emit("fail", spec_error)

    completed = _run(
        workspace,
        [sys.executable, "-m", "unittest", *[str(item) for item in config["test_modules"]]],
    )
    if completed.returncode != 0:
        return emit(
            "fail",
            "candidate contract tests failed: "
            + (completed.stderr or completed.stdout or "")[-2500:],
        )

    runner = _run(
        workspace,
        [
            sys.executable, "-m", "automation.gfx_contract_runner",
            "--workspace", str(workspace),
            "--version", version,
            "--checkpoint", str(checkpoint_path),
        ],
    )
    try:
        run_result = json.loads(runner.stdout or "{}")
    except json.JSONDecodeError:
        return emit("fail", "contract runner output is unreadable")
    if not run_result.get("positive_ok"):
        return emit(
            "fail",
            "candidate validator does not accept the checkpoint evidence: "
            + json.dumps(run_result.get("fields") or run_result.get("errors") or [])[:2000],
        )
    base_version = str(config.get("base_contract_version", "v0"))
    try:
        base_contract = load_contract(base_version)
    except ContractError as exc:
        return emit("unavailable", f"base contract is unavailable: {exc}")
    base_fields = set(base_contract.validators)
    validator_fields = set(run_result.get("validator_fields") or [])
    if not base_fields <= validator_fields:
        return emit(
            "fail",
            "candidate contract drops previously qualified fields: "
            + ", ".join(sorted(base_fields - validator_fields)),
        )
    retained = [
        (workspace / "automation" / "gfx_contracts" / f"{current}.py").read_bytes()
        for current in base_contract.versions + (version,)
    ]
    digest = hashlib.sha256(
        b"".join(retained)
        + (workspace / spec_rel).read_bytes()
        + (workspace / tests_rel).read_bytes()
    ).hexdigest()
    return emit(
        "pass",
        json.dumps(
            {
                "contract_version": version,
                "contract_digest": digest,
                "fields": sorted(gaps),
                "checkpoint": str(checkpoint_path),
                "checkpoint_sha256": _sha(checkpoint_path),
            },
            sort_keys=True,
        ),
        contract_digest=digest,
    )
