"""Real OpenCode and integration hooks for the L3.2 completion coordinator.

The adapter materializes generated campaigns from the tracked templates, runs
the existing Runner, parses the trusted checker diagnostics, snapshots
candidates, launches reviewer/contract campaigns, and integrates accepted
deltas with a durable operation journal and compare-and-swap branch update.

The checker-runner is injectable so the orchestration can be tested without a
live EU4 capture; the production path uses the trusted v2 checker.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import launch_ledger  # noqa: E402
from automation.gfx_reviewer_v2 import materialize_reviewer  # noqa: E402
from automation.gfx_contract_runner import run as contract_run  # noqa: E402
from automation.gfx_l32b_complete import Hooks, prepare_campaign_run_root  # noqa: E402
from automation.gfx_snapshot_v2 import snapshot_contract_v1, snapshot_v2  # noqa: E402
from automation.opencode import OpenCodeAgent  # noqa: E402
from automation.runner import Runner  # noqa: E402
from automation.state import atomic_write, load_campaign  # noqa: E402


class HookError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_diagnostics(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _next_version(version: str) -> str:
    return f"v{int(version[1:]) + 1}"


class L32bHooks:
    def __init__(
        self,
        *,
        repo: Path,
        workflow: dict,
        run_root: Path,
        agent_factory: Callable[[str], object] | None = None,
        checker_runner: Callable[[Path, dict], dict] | None = None,
        model: str | None = None,
    ):
        self.repo = Path(repo).resolve()
        self.workflow = workflow
        self.run_root = Path(run_root).resolve()
        self.workflow_id = str(workflow["workflow_id"])
        self.agent_factory = agent_factory or self._opencode_agent
        self.checker_runner = checker_runner or self._post_integration
        self.model = model or str(workflow.get("model", "opencode/big-pickle"))
        self.templates = self.repo / "campaigns"

    def _opencode_agent(self, model: str):
        return OpenCodeAgent(
            os.environ.get("OPENCODE_URL", "http://127.0.0.1:4096"),
            password=os.environ.get("OPENCODE_PASSWORD"),
            timeout=float(os.environ.get("OPENCODE_TIMEOUT", "120")),
            model=model,
        )

    def hooks(self) -> Hooks:
        return Hooks(
            run_implementer=self.run_implementer,
            snapshot=self.snapshot,
            run_contract=self.run_contract,
            snapshot_contract=self.snapshot_contract,
            run_reviewer=self.run_reviewer,
            run_adversarial_cases=self.run_adversarial_cases,
            run_remediation=self.run_remediation,
            run_research=self.run_research,
            run_capture=self.run_capture,
            integrate=self.integrate,
        )

    # ---- campaign materialization -------------------------------------

    def _trusted(self, state: dict) -> dict:
        trusted = state.get("trusted_contract") or self.workflow.get("trusted_contract") or {}
        commit = trusted.get("commit")
        if not commit or commit == "PIN_AT_RUNTIME":
            commit = self.workflow.get("baseline_commit")
        return {
            "commit": commit,
            "contract_version": str(
                trusted.get("contract_version", trusted.get("version", "v0"))
            ),
            "contract_sha256": trusted.get(
                "contract_sha256", trusted.get("sha256")
            ),
            "manifest": trusted.get("manifest"),
        }

    def _materialize(
        self,
        *,
        kind: str,
        base: str,
        operation_id: str,
        template: Path,
        trusted: dict,
        extra: dict | None = None,
    ) -> dict:
        campaign_id = f"eu4-l32b-{kind}-{self.workflow_id}-{operation_id}"
        runner_root = self.repo / ".local" / "automation" / campaign_id
        campaign_dir = self.run_root / "campaigns" / campaign_id
        campaign_dir.mkdir(parents=True, exist_ok=True)
        workspace = runner_root / "workspace"
        if not workspace.exists():
            completed = subprocess.run(
                ["git", "-C", str(self.repo), "worktree", "add", "--detach",
                 str(workspace), base],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise HookError(completed.stderr.strip() or "worktree creation failed")
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0 or head.stdout.strip() != base:
            raise HookError("campaign worktree HEAD is not the requested base")
        campaign_json = {
            "id": campaign_id,
            "objective": str(template / "objective.md"),
            "runtime": "opencode",
            "model": self.model,
            "opencode_version": "2.2.5",
            "workspace": {"mode": "worktree", "path": str(workspace), "commit": base},
            "accept": ["python3", str(template / "accept.py"), "--campaign-dir", str(campaign_dir)],
            "max_rounds": int(self.workflow.get("max_rounds", 8)),
            "max_seconds": float(self.workflow.get("max_seconds", 7200)),
            "accept_timeout_seconds": float(self.workflow.get("accept_timeout_seconds", 900)),
            "verification_manifest": str(template / self._manifest_name(kind)),
            "live_observation": {
                "launch_limit": self._launch_limit(kind),
                "deadline_seconds": float(self.workflow.get("launch_deadline_seconds", 120)),
            },
            "template": str(template),
            "trusted_contract": trusted,
        }
        if extra:
            campaign_json.update(extra)
        atomic_write(campaign_dir / "campaign.json", json.dumps(campaign_json, indent=2) + "\n")
        ledger = self.run_root / "live-launch-ledger.json"
        launch_ledger.create_ledger(
            ledger,
            workflow_id=self.workflow_id,
            limit=int(self.workflow.get("global_launch_limit", 6)),
            deadline_seconds=float(self.workflow.get("launch_deadline_seconds", 120)),
        )
        launch_ledger.register_campaign(ledger, campaign_id, self._launch_limit(kind))
        prepare_campaign_run_root(
            runner_root,
            workflow_id=self.workflow_id,
            ledger_path=ledger,
            limit=int(self.workflow.get("global_launch_limit", 6)),
        )
        return {
            "campaign_id": campaign_id,
            "campaign_dir": campaign_dir,
            "campaign_json": campaign_dir / "campaign.json",
            "run_root": runner_root,
            "workspace": workspace,
            "template": template,
            "base": base,
            "trusted": trusted,
        }

    def _launch_limit(self, kind: str) -> int:
        if kind in ("contract", "contract-fix", "reviewer"):
            return 0
        if kind == "capture":
            return 1
        return int(self.workflow.get("per_campaign_launch_limit", 3))

    def _manifest_name(self, kind: str) -> str:
        return "contract-manifest.json" if kind in ("contract", "contract-fix") else "l32b-manifest.json"

    def _run(self, campaign: dict) -> dict:
        loaded = load_campaign(campaign["campaign_json"])
        agent = self.agent_factory(self.model)
        runner = Runner(loaded, agent, campaign["run_root"])
        resume = (campaign["run_root"] / "state.json").is_file()
        return runner.execute(resume_only=resume)

    def _normalize(self, state: dict, campaign: dict) -> dict:
        diag = _parse_diagnostics(state.get("last_diagnostics"))
        result = {
            "status": state.get("status"),
            "last_verdict": state.get("last_verdict"),
            "pause_reason": state.get("pause_reason"),
            "workspace": str(campaign["workspace"]),
            "campaign_dir": str(campaign["campaign_dir"]),
            "campaign_id": campaign["campaign_id"],
            "candidate_base_commit": campaign["base"],
            "trusted_contract_commit": campaign["trusted"].get("commit"),
            "session_id": state.get("session_id"),
            "established": diag.get("established") or [],
            "missing": diag.get("missing") or [],
            "provisional": diag.get("provisional") or [],
            "diagnostics": diag,
        }
        if state.get("pause_reason") == "checkpoint_yield" \
                and diag.get("reason") == "unsupported_evidence_contract":
            result["checkpoint"] = diag.get("checkpoint")
            result["checkpoint_sha256"] = diag.get("checkpoint_sha256")
        if state.get("last_verdict") == "pass":
            result["qualification_receipt"] = diag.get("qualification_receipt")
            result["packet_ready"] = diag.get("packet_ready")
        return result

    # ---- implementer --------------------------------------------------

    def run_implementer(self, state: dict) -> dict:
        trusted = self._trusted(state)
        base = (
            (state.get("integration") or {}).get("commit")
            or trusted.get("commit")
            or self.workflow.get("baseline_commit")
        )
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"impl-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        campaign = self._materialize(
            kind="impl",
            base=str(base),
            operation_id=operation_id,
            template=self.templates / "eu4-l32b-complete",
            trusted=trusted,
        )
        return self._normalize(self._run(campaign), campaign)

    def snapshot(self, workspace: Path, state: dict) -> dict:
        campaign_dir = Path(str(state.get("campaign_dir") or ""))
        if not campaign_dir.is_dir():
            raise HookError("implementation campaign directory is missing")
        result = snapshot_v2(workspace, campaign_dir, int(state.get("round", 0)) + 1)
        result["campaign_dir"] = str(campaign_dir)
        return result

    # ---- contract -----------------------------------------------------

    def _write_contract_config(
        self,
        campaign: dict,
        template: Path,
        version: str,
        base_version: str,
        checkpoint: str,
    ) -> None:
        contract = _json(template / "contract.json")
        contract.update(
            {
                "candidate_version": version,
                "base_contract_version": base_version,
                "checkpoint": checkpoint,
                "allowed_paths": [
                    "progress.md",
                    f"automation/gfx_contracts/{version}.py",
                    f"automation/gfx_contracts/{version}.spec.json",
                    f"tests/test_gfx_contract_{version}.py",
                    f"analysis/gfx-draw/v0/l32b/contracts/{version}/**",
                ],
                "test_modules": [f"tests.test_gfx_contract_{version}"],
            }
        )
        atomic_write(
            campaign["campaign_dir"] / "contract.json",
            json.dumps(contract, indent=2) + "\n",
        )
        manifest = template / "contract-manifest.json"
        if manifest.is_file():
            (campaign["campaign_dir"] / "contract-manifest.json").write_bytes(
                manifest.read_bytes()
            )

    def run_contract(self, checkpoint: str, state: dict) -> dict:
        trusted = self._trusted(state)
        version = _next_version(str(trusted.get("contract_version", "v0")))
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"contract-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        template = self.templates / "eu4-l32b-contract"
        campaign = self._materialize(
            kind="contract",
            base=str(trusted.get("commit")),
            operation_id=operation_id,
            template=template,
            trusted=trusted,
            extra={
                "candidate_version": version,
                "base_contract_version": trusted.get("contract_version", "v0"),
                "checkpoint": checkpoint,
            },
        )
        self._write_contract_config(
            campaign, template, version,
            str(trusted.get("contract_version", "v0")), checkpoint,
        )
        runner_state = self._run(campaign)
        result = self._normalize(runner_state, campaign)
        if runner_state.get("last_verdict") == "pass":
            snapshot = snapshot_contract_v1(
                campaign["workspace"], campaign["campaign_dir"],
                int(state.get("campaign_sequence", 0)) or 1,
            )
            snapshot["campaign_dir"] = str(campaign["campaign_dir"])
            result["candidate"] = snapshot
            result["campaign_dir"] = str(campaign["campaign_dir"])
            result["contract_version"] = version
            result["contract_sha256"] = snapshot["contract_sha256"]
            result["workspace"] = str(campaign["workspace"])
        return result

    def snapshot_contract(self, workspace: Path, state: dict) -> dict:
        campaign_dir = Path(str(state.get("contract_campaign_dir") or ""))
        if not campaign_dir.is_dir():
            raise HookError("contract campaign directory is missing")
        result = snapshot_contract_v1(
            workspace, campaign_dir, int(state.get("round", 0)) + 1
        )
        result["campaign_dir"] = str(campaign_dir)
        return result

    def run_adversarial_cases(self, checkpoint: str, contract: dict,
                              cases_path: Path, state: dict) -> dict:
        workspace = Path(str(state.get("contract_workspace") or ""))
        version = str(state.get("contract_version") or "")
        if not workspace.is_dir() or not version:
            raise HookError("contract workspace/version is missing")
        return contract_run(
            workspace, version, Path(checkpoint), cases_path=Path(cases_path)
        )

    # ---- reviewer -----------------------------------------------------

    def run_reviewer(self, target: dict, state: dict) -> dict:
        campaign_dir = Path(str(target.get("campaign_dir") or state.get("campaign_dir") or ""))
        if not campaign_dir.is_dir():
            raise HookError("reviewer source campaign directory is missing")
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"review-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        review_id = f"eu4-l32b-review-{target.get('campaign_id')}-{operation_id}"
        review = materialize_reviewer(
            campaign_dir, target, int(state.get("campaign_sequence", 0)) or 1,
            review_id=review_id,
        )
        ledger = self.run_root / "live-launch-ledger.json"
        launch_ledger.create_ledger(
            ledger,
            workflow_id=self.workflow_id,
            limit=int(self.workflow.get("global_launch_limit", 6)),
            deadline_seconds=float(self.workflow.get("launch_deadline_seconds", 120)),
        )
        launch_ledger.register_campaign(ledger, review["review_id"], 0)
        prepare_campaign_run_root(
            self.repo / ".local" / "automation" / review["review_id"],
            workflow_id=self.workflow_id,
            ledger_path=ledger,
            limit=int(self.workflow.get("global_launch_limit", 6)),
        )
        loaded = load_campaign(Path(review["campaign"]))
        runner_root = self.repo / ".local" / "automation" / review["review_id"]
        runner = Runner(
            loaded,
            self.agent_factory(self.model),
            runner_root,
            env=review["env"],
        )
        result = runner.execute(resume_only=(runner_root / "state.json").is_file())
        return {
            "path": review["report"],
            "session_id": result.get("session_id"),
            "review_id": review["review_id"],
        }

    # ---- remediation / research --------------------------------------

    def run_remediation(self, state: dict) -> dict:
        target = state.get("remediation_target")
        trusted = self._trusted(state)
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"remediation-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        if target == "contract":
            rejected = state.get("contract") or {}
            base = rejected.get("candidate_commit")
            version = str(state.get("contract_version") or "")
            template = self.templates / "eu4-l32b-contract"
            campaign = self._materialize(
                kind="contract-fix",
                base=str(base),
                operation_id=operation_id,
                template=template,
                trusted=trusted,
                extra={
                    "candidate_version": version,
                    "base_contract_version": trusted.get("contract_version", "v0"),
                    "checkpoint": state.get("contract_checkpoint"),
                },
            )
            self._write_contract_config(
                campaign, template, version,
                str(trusted.get("contract_version", "v0")),
                str(state.get("contract_checkpoint")),
            )
        else:
            rejected = state.get("candidate") or {}
            base = rejected.get("candidate_commit")
            campaign = self._materialize(
                kind="impl-fix",
                base=str(base),
                operation_id=operation_id,
                template=self.templates / "eu4-l32b-complete",
                trusted=trusted,
            )
        return self._normalize(self._run(campaign), campaign)

    def run_capture(self, state: dict) -> dict:
        requirement = state.get("capture_requirement") or {}
        pending = state.get("pending_integration") or {}
        staged = str(pending.get("staged_commit") or "")
        required = str(requirement.get("commit") or "")
        if not staged or staged != required:
            return {"detail": "capture target is not the pending staged commit"}
        commit = staged
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"capture-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        campaign = self._materialize(
            kind="capture",
            base=commit,
            operation_id=operation_id,
            template=self.templates / "eu4-l32b-complete",
            trusted=self._trusted(state),
            extra={"capture_requirement": requirement},
        )
        self._run(campaign)
        captures = campaign["run_root"] / "captures"
        receipt = campaign["run_root"] / "live-launch.json"
        if not captures.is_dir() or not receipt.is_file():
            return {"detail": "capture campaign produced no authenticated capture"}
        directories = sorted(
            (path for path in captures.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
        )
        if not directories:
            return {"detail": "capture campaign produced no capture directory"}
        capture = directories[-1]
        reference, problem = self._verify_capture(
            campaign["run_root"], capture, receipt
        )
        if problem:
            return {"detail": problem}
        return {
            "capture": str(capture),
            "capture_receipt": str(receipt),
            "capture_reference": reference,
            "candidate_commit": commit,
        }

    def _verify_capture(
        self, run_root: Path, capture: Path, receipt_path: Path
    ) -> tuple[dict | None, str | None]:
        counter = {}
        if receipt_path.is_file():
            try:
                counter = _json(receipt_path)
            except (OSError, UnicodeError, json.JSONDecodeError):
                counter = {}
        mirror_tokens = [
            item.get("token")
            for item in counter.get("launches") or []
            if isinstance(item, dict) and item.get("token")
        ]
        try:
            pointer = launch_ledger.read_pointer(run_root)
        except launch_ledger.LedgerError as exc:
            return None, f"capture ledger pointer is invalid: {exc}"
        if pointer is None:
            return None, "capture ledger pointer is missing"
        try:
            ledger = launch_ledger.load_ledger(Path(pointer["ledger"]))
        except launch_ledger.LedgerError as exc:
            return None, f"capture ledger is invalid: {exc}"
        candidates = [
            item for item in ledger.get("launches") or []
            if isinstance(item, dict)
            and item.get("campaign_id") == run_root.name
            and item.get("status") == "completed"
            and Path(str(item.get("output") or "")).resolve() == capture.resolve()
        ]
        if mirror_tokens:
            candidates = [
                item for item in candidates if item.get("token") in mirror_tokens
            ]
        if len(candidates) != 1:
            return None, "capture ledger entry is missing or ambiguous"
        record = candidates[0]
        request = capture / "capture-request.json"
        if not request.is_file():
            return None, "capture request is missing"
        try:
            document = _json(request)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            return None, f"capture request is unreadable: {exc}"
        if not document.get("library_sha256"):
            return None, "capture request has no observer identity"
        if not (capture / "operations-summary.json").is_file():
            return None, "capture summary is missing"
        return {
            "token": record.get("token"),
            "capture": str(capture),
            "campaign_id": record.get("campaign_id"),
            "output": record.get("output"),
            "library_sha256": document.get("library_sha256"),
            "selection": {
                "receipt": record,
                "plan_path": str(capture / "capture-plan.json"),
                "plan_sha256": None,
            },
        }, None

    def run_research(self, state: dict) -> dict:
        trusted = self._trusted(state)
        base = (
            (state.get("integration") or {}).get("commit")
            or trusted.get("commit")
            or self.workflow.get("baseline_commit")
        )
        operation_id = str(
            (state.get("active_campaign") or {}).get("operation_id")
            or f"research-{int(state.get('campaign_sequence', 0)) + 1}"
        )
        campaign = self._materialize(
            kind="research",
            base=str(base),
            operation_id=operation_id,
            template=self.templates / "eu4-l32b-complete",
            trusted=trusted,
        )
        return self._normalize(self._run(campaign), campaign)

    # ---- integration --------------------------------------------------

    def integrate(self, candidate: dict, contract: dict, state: dict, kind=None) -> dict:
        candidate_commit = candidate.get("candidate_commit")
        contract_commit = (contract or {}).get("candidate_commit") if isinstance(contract, dict) else None
        pending = state.get("pending_integration") or {}
        if pending:
            base = str(pending["base_head"])
            integration_id = str(pending["integration_id"])
        else:
            base = (
                (state.get("integration") or {}).get("commit")
                or self._trusted(state).get("commit")
                or self.workflow.get("baseline_commit")
            )
            integration_id = hashlib.sha256(
                json.dumps(
                    {
                        "kind": kind,
                        "candidate": candidate_commit,
                        "contract": contract_commit,
                        "base": base,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
        state["_integration_base"] = base
        journal_path = self.run_root / "integrations" / f"{integration_id}.json"
        journal = _json(journal_path) if journal_path.is_file() else None
        if journal and journal.get("status") == "verified":
            return {
                "ok": True,
                "commit": journal["commit"],
                "verification": journal["verification"],
            }
        if journal is None:
            journal = {
                "schema": "eu4-l32b-integration-journal/v1",
                "integration_id": integration_id,
                "kind": kind,
                "candidate_commit": candidate_commit,
                "contract_commit": contract_commit,
                "base_head": base,
                "status": "prepared",
            }
            atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
        branch = str(self.workflow.get("integration_branch") or "l32b/autonomous-completion")
        ref = f"refs/heads/{branch}"
        commit = journal.get("new_commit")
        if not commit:
            commit = self._staged_commit(
                base=base,
                candidate_commit=candidate_commit,
                candidate_parent=(candidate or {}).get("pin_commit"),
                contract_commit=contract_commit,
                contract_parent=(contract or {}).get("pin_commit"),
                integration_id=integration_id,
            )
            journal["new_commit"] = commit
            journal["status"] = "staged"
            atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
        if self.checker_runner is None:
            raise HookError("post-integration checker is not configured")
        verification = None
        if journal.get("status") in ("verified_staged", "pending_capture"):
            verification = journal.get("verification")
            if journal.get("status") == "pending_capture" \
                    and not state.get("capture_receipt"):
                return {
                    "ok": True,
                    "commit": commit,
                    "verification": verification,
                    "integration_id": integration_id,
                    "base_head": base,
                    "published": False,
                }
            if journal.get("status") == "verified_staged":
                verification = journal.get("verification")
        if verification is None:
            verification = self.checker_runner(Path(commit), state)
            if not isinstance(verification, dict):
                raise HookError("post-integration checker returned no verification")
            verification.setdefault("commit", commit)
            verification.setdefault("kind", kind)
            status = verification.get("status") or "verified"
            if status == "pending_capture":
                journal["status"] = "pending_capture"
                journal["verification"] = verification
                atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
                return {
                    "ok": True,
                    "commit": commit,
                    "verification": verification,
                    "published": False,
                }
            if status != "verified":
                journal["status"] = "failed"
                journal["verification"] = verification
                atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
                raise HookError("post-integration verification failed")
            journal["status"] = "verified_staged"
            journal["verification"] = verification
            atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
        current = subprocess.run(
            ["git", "-C", str(self.repo), "rev-parse", "--verify", ref],
            capture_output=True,
            text=True,
            check=False,
        )
        current_commit = current.stdout.strip() if current.returncode == 0 else ""
        if current_commit == commit:
            pass
        elif current_commit == base:
            updated = subprocess.run(
                ["git", "-C", str(self.repo), "update-ref", ref, commit, base],
                capture_output=True,
                text=True,
                check=False,
            )
            if updated.returncode != 0:
                raise HookError(
                    updated.stderr.strip() or "integration ref update failed"
                )
        else:
            raise HookError(
                "integration branch moved; refusing to reinterpret the new HEAD"
            )
        journal["status"] = "verified"
        journal["verification"] = verification
        atomic_write(journal_path, json.dumps(journal, indent=2) + "\n")
        return {
            "ok": True,
            "commit": commit,
            "verification": verification,
            "integration_id": integration_id,
            "base_head": base,
            "published": True,
        }

    def _post_integration(self, commit: Path, state: dict) -> dict:
        from automation.gfx_checkpoint import source_tree_digest_at_commit
        from automation.gfx_draw_accept_v2 import build_candidate

        commit = str(commit)
        base = str(
            state.get("_integration_base")
            or (state.get("pending_integration") or {}).get("base_head")
            or (state.get("integration") or {}).get("base_head")
            or self._trusted(state).get("commit")
        )
        capture = state.get("capture")
        receipt = state.get("capture_receipt")
        if not capture or not receipt:
            return {
                "status": "pending_capture",
                "commit": commit,
                "base_head": base,
                "capture_required": True,
            }
        worktree = self.run_root / "requalification" / commit
        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            added = subprocess.run(
                ["git", "-C", str(self.repo), "worktree", "add", "--detach",
                 str(worktree), commit],
                capture_output=True,
                text=True,
                check=False,
            )
            if added.returncode != 0:
                return {"status": "failed", "detail": added.stderr.strip()}
        head = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
        status = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        )
        if head.stdout.strip() != commit or status.stdout.strip():
            return {"status": "failed", "detail": "requalification worktree is not clean"}
        checker_commit = str(
            state.get("checker_freeze_commit")
            or self.workflow.get("checker_freeze_commit")
            or self._trusted(state).get("commit")
            or self.workflow.get("baseline_commit")
        )
        for rel in (
            "automation/gfx_draw_accept_v2.py",
            "automation/gfx_contracts/api.py",
            "campaigns/eu4-l32b-complete/accept.py",
        ):
            frozen = subprocess.run(
                ["git", "-C", str(self.repo), "show", f"{checker_commit}:{rel}"],
                capture_output=True, check=False,
            )
            current = (worktree / rel)
            if frozen.returncode != 0 or not current.is_file() \
                    or current.read_bytes() != frozen.stdout:
                return {
                    "status": "failed",
                    "checker": "fail",
                    "detail": f"trusted checker modified: {rel}",
                }
        diff = subprocess.run(
            ["git", "-C", str(self.repo), "diff", "--name-only", base, commit],
            capture_output=True, text=True, check=False,
        )
        if diff.returncode != 0:
            return {"status": "failed", "detail": "integration diff failed"}
        changed = [line for line in diff.stdout.splitlines() if line]
        template = worktree / "campaigns" / "eu4-l32b-complete"
        config = _json(template / "l32b.json")
        allowed = [str(item) for item in config.get("allowed_paths", [])]
        kind = state.get("promotion_kind")
        trusted = self._trusted(state)
        contract_paths: list[str] = []
        if kind == "contract":
            version = str(state.get("contract_version") or "")
            contract_sha = str(state.get("contract_sha256") or "")
            contract = state.get("contract") or {}
            wanted = {
                f"automation/gfx_contracts/{version}.py",
                f"automation/gfx_contracts/{version}.spec.json",
                f"tests/test_gfx_contract_{version}.py",
            }
            contract_paths = [
                path for path in (contract.get("changed_paths") or [])
                if path in wanted
                or path.startswith(f"analysis/gfx-draw/v0/l32b/contracts/{version}/")
            ]
            module = f"automation/gfx_contracts/{version}.py"
            actual = subprocess.run(
                ["git", "-C", str(self.repo), "show", f"{commit}:{module}"],
                capture_output=True, check=False,
            )
            if actual.returncode != 0 or hashlib.sha256(actual.stdout).hexdigest() != contract_sha:
                return {
                    "status": "failed",
                    "checker": "fail",
                    "detail": "qualified contract module differs at the integration commit",
                }
            version_effective = version
            sha_effective = contract_sha
        else:
            version_effective = str(trusted.get("contract_version", "v0"))
            sha_effective = trusted.get("contract_sha256")
        effective_allowed = allowed + contract_paths
        from automation.gfx_draw_accept_v2 import path_allowed

        outside = [path for path in changed if not path_allowed(path, effective_allowed)]
        if outside:
            return {
                "status": "failed",
                "checker": "fail",
                "detail": "integration changes outside the allowlist: "
                          + ", ".join(outside[:6]),
            }
        digest = source_tree_digest_at_commit(self.repo, commit, changed)
        candidate_sha, failure = build_candidate(worktree)
        if failure:
            return {"status": "failed", "checker": failure.get("verdict", "fail")}
        request_path = Path(str(capture)) / "capture-request.json"
        if not request_path.is_file():
            return {"status": "failed", "detail": "capture request is missing"}
        request = _json(request_path)
        captured_library = str(request.get("library_sha256") or "")
        if captured_library and captured_library != candidate_sha:
            return {
                "status": "pending_capture",
                "commit": commit,
                "base_head": base,
                "capture_required": True,
                "observer_library_sha256": candidate_sha,
            }
        campaign_dir = self.run_root / "requalification" / commit / "campaign"
        campaign_dir.mkdir(parents=True, exist_ok=True)
        campaign = {
            "id": f"requalify-{commit[:12]}",
            "objective": str(template / "objective.md"),
            "runtime": "opencode",
            "model": self.model,
            "opencode_version": "2.2.5",
            "workspace": {"mode": "worktree", "path": str(worktree), "commit": commit},
            "accept": ["python3", str(template / "accept.py"),
                       "--campaign-dir", str(campaign_dir)],
            "max_rounds": 1,
            "max_seconds": 120,
            "accept_timeout_seconds": 120,
            "verification_manifest": str(template / "l32b-manifest.json"),
            "live_observation": {"launch_limit": 0, "deadline_seconds": 120},
            "template": str(template),
            "trusted_contract": {
                "commit": commit,
                "contract_version": version_effective,
                "contract_sha256": sha_effective,
                "manifest": trusted.get("manifest"),
            },
            "requalification": {
                "capture": str(capture),
                "changed_paths": changed,
                "candidate_digest": digest,
                "allowed_paths": effective_allowed,
                "selection": (
                    (state.get("capture_reference") or {}).get("selection") or {}
                ),
            },
        }
        atomic_write(campaign_dir / "campaign.json",
                     json.dumps(campaign, indent=2) + "\n")
        env = {
            **os.environ,
            "PYTHONPATH": f"{worktree / 'benchmark'}:{worktree}",
        }
        completed = subprocess.run(
            [*campaign["accept"], str(worktree)],
            cwd=str(campaign_dir),
            env=env,
            capture_output=True,
            timeout=120,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout.decode())
        except (json.JSONDecodeError, UnicodeError):
            return {"status": "failed", "detail": "requalification output is unreadable"}
        verdict = payload.get("verdict")
        diagnostics = _parse_diagnostics(payload.get("diagnostics"))
        if verdict not in ("pass", "checkpoint", "inconclusive"):
            return {
                "status": "failed",
                "checker": verdict or "fail",
                "detail": diagnostics.get("reason") or "requalification failed",
            }
        established = diagnostics.get("established") or []
        unresolved = list(diagnostics.get("missing") or []) \
            + list(diagnostics.get("provisional") or [])
        previous = set(
            (state.get("integration_verification") or {}).get("established_fields") or []
        )
        regressions = sorted(previous - set(established))
        receipt_path = diagnostics.get("qualification_receipt")
        return {
            "status": "verified",
            "schema": "eu4-l32b-integration-verification/v1",
            "commit": commit,
            "kind": state.get("promotion_kind"),
            "build_ok": True,
            "tests_ok": True,
            "checker": verdict,
            "established_fields": established,
            "unresolved_fields": unresolved,
            "regressions": regressions,
            "packet_ready": bool(diagnostics.get("packet_ready")),
            "qualification_receipt_sha256": (
                _sha(Path(str(receipt_path))) if receipt_path else None
            ),
        }

    def _staged_commit(
        self,
        *,
        base: str,
        candidate_commit: str | None,
        candidate_parent: str | None,
        contract_commit: str | None,
        contract_parent: str | None,
        integration_id: str,
    ) -> str:
        staging = self.run_root / "integration" / integration_id
        if staging.exists():
            raise HookError(f"staging worktree already exists: {staging}")
        staging.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["git", "-C", str(self.repo), "worktree", "add", "--detach",
             str(staging), base],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise HookError(completed.stderr.strip() or "staging worktree failed")
        try:
            for commit, parent in (
                (candidate_commit, candidate_parent),
                (contract_commit, contract_parent),
            ):
                if not commit:
                    continue
                diff = subprocess.run(
                    ["git", "-C", str(self.repo), "diff", "--binary",
                     str(parent or base), commit],
                    capture_output=True,
                    check=False,
                )
                if diff.returncode != 0:
                    raise HookError("integration diff failed")
                if not diff.stdout:
                    continue
                applied = subprocess.run(
                    ["git", "-C", str(staging), "apply", "--index", "--3way", "-"],
                    input=diff.stdout,
                    capture_output=True,
                    check=False,
                )
                if applied.returncode != 0:
                    raise HookError(
                        applied.stderr.decode() or "integration apply failed"
                    )
            tree = subprocess.run(
                ["git", "-C", str(staging), "write-tree"],
                capture_output=True,
                text=True,
                check=False,
            )
            if tree.returncode != 0:
                raise HookError("integration write-tree failed")
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "gfx-integration",
                "GIT_AUTHOR_EMAIL": "gfx-integration@local",
                "GIT_COMMITTER_NAME": "gfx-integration",
                "GIT_COMMITTER_EMAIL": "gfx-integration@local",
            }
            commit = subprocess.run(
                ["git", "-C", str(self.repo), "commit-tree", tree.stdout.strip(),
                 "-p", base, "-m", f"L3.2b integration {integration_id}"],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            if commit.returncode != 0:
                raise HookError(commit.stderr.strip() or "integration commit failed")
            return commit.stdout.strip()
        finally:
            subprocess.run(
                ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(staging)],
                capture_output=True,
                check=False,
            )
