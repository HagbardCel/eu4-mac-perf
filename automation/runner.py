"""Continue one agent session until an external checker passes."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from automation.opencode import ContractError
from automation.state import (
    Campaign,
    atomic_write,
    campaign_lock,
    ensure_workspace,
    file_sha256,
    load_state,
    save_state,
)
from automation.telemetry import append

_INSTRUCTIONS = (Path(__file__).parent / "prompts" / "agent.md").read_text(encoding="utf-8")
_OUTPUT_CAP = 65536


class Runner:
    def __init__(self, campaign: Campaign, agent, run_root: Path, *,
                 clock=None, sleep=None, secrets: list[str] | None = None):
        self.campaign = campaign
        self.agent = agent
        self.run_root = Path(run_root)
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.secrets = secrets or []
        if hasattr(agent, "protected_root"):
            agent.protected_root = campaign.root
        self.state_path = self.run_root / "state.json"
        self.log_path = self.run_root / "events.jsonl"
        self.live = self.run_root / "workspace"
        self._tick = self.clock()
        self._unknown = 0

    def execute(self, *, resume_only: bool) -> dict:
        self.run_root.mkdir(parents=True, exist_ok=True)
        with campaign_lock(self.run_root / "campaign.lock"):
            existed = self.state_path.exists()
            if resume_only and not existed:
                raise RuntimeError("no campaign state to resume")
            if existed:
                state = load_state(self.state_path)
            else:
                ensure_workspace(self.campaign.seed, self.live)
                state = self._fresh()
                save_state(self.state_path, state)
            self._guard(state)
            if state["status"] == "complete" or state.get("pause_reason") == "checker_modified":
                return state
            self._log(state, "runner.resumed" if existed else "runner.started", _rid("runner"))
            try:
                self._loop(state)
            except KeyboardInterrupt:
                self._charge(state)
                if state["status"] == "running":
                    state["status"] = "paused"
                    state["pause_reason"] = "interrupted"
                save_state(self.state_path, state)
                self._log(state, "runner.paused", _rid("runner"), reason="interrupted")
                raise
            return state

    def _loop(self, state: dict) -> None:
        if state.get("pause_reason") == "model_mismatch":
            return
        state["status"] = "running"
        state["pause_reason"] = None
        while state["status"] == "running":
            self._charge(state)
            if state["elapsed_seconds"] >= self.campaign.max_seconds:
                self._pause(state, "time_limit")
                return
            self._guard(state)
            if state["status"] != "running":
                return
            if not state.get("session_id"):
                if not self._ensure_session(state, replacement=bool(state.get("rounds"))):
                    if state["status"] != "running":
                        return
                    self.sleep(0.2)
                    continue
            if not state.get("pending_submission_id"):
                if state.get("last_verdict") == "pass":
                    self._complete(state)
                    return
                self._arm(state)
                if state["status"] != "running":
                    return
            observation = self._reconcile(state)
            state["last_observation"] = observation.phase
            save_state(self.state_path, state)
            if observation.phase == "needs_input":
                self._pause(state, "blocked_on_permission")
                return
            if observation.phase == "missing":
                state["session_id"] = None
                save_state(self.state_path, state)
                continue
            if observation.phase == "unknown":
                self._unknown += 1
                if self._unknown >= 3:
                    self._pause(state, "runtime_unavailable")
                    return
                self.sleep(0.2)
                continue
            self._unknown = 0
            if observation.phase in ("executing", "queued"):
                self._ingest(state)
                self.sleep(0.2)
                continue
            if observation.phase == "settled":
                self._ingest(state)
                self._validate(state)

    def _reconcile(self, state: dict):
        submission_id = state["pending_submission_id"]
        observation = self.agent.inspect(state["session_id"], submission_id)
        if observation.phase == "unknown" and observation.record == "absent":
            observation = self.agent.submit(
                state["session_id"], state["pending_text"], submission_id)
        return observation

    def _arm(self, state: dict) -> None:
        if state["rounds"] >= self.campaign.max_rounds:
            self._pause(state, "round_limit")
            return
        if state.get("last_verdict") in ("fail", "inconclusive"):
            text = (
                f"Validation {state['last_verdict']}: {state.get('last_diagnostics') or ''}\n"
                "Continue from progress.md."
            )
        else:
            text = self._prompt(False)
        state["pending_submission_id"] = "msg_" + uuid.uuid4().hex
        state["pending_text"] = text
        save_state(self.state_path, state)
        self._log(state, "runner.continuation", "runner/submit/" + state["pending_submission_id"])

    def _validate(self, state: dict) -> None:
        if not self._digest_matches(state):
            self._pause(state, "checker_modified")
            return
        started = self.clock()
        outcome = run_accept(self.campaign, self.live)
        duration = self.clock() - started
        if not self._digest_matches(state):
            self._pause(state, "checker_modified")
            return
        state["rounds"] += 1
        state["last_verdict"] = outcome["verdict"]
        state["last_diagnostics"] = outcome["diagnostics"]
        state["validated_submission_id"] = state["pending_submission_id"]
        state["pending_submission_id"] = None
        state["pending_text"] = None
        self._log(
            state, "validation", f"runner/validation/{state['rounds']}",
            verdict=outcome["verdict"], diagnostics=outcome["diagnostics"],
            exit_code=outcome["exit_code"], duration_ms=int(duration * 1000),
            rounds=state["rounds"],
        )
        save_state(self.state_path, state)
        if outcome["ok"] and outcome["verdict"] == "pass":
            self._complete(state)
        elif outcome["ok"] and outcome["verdict"] in ("fail", "inconclusive"):
            return
        else:
            self._pause(state, "validation_unavailable")

    def _ensure_session(self, state: dict, *, replacement: bool) -> bool:
        try:
            session_id = self.agent.create(self.live, self._prompt(replacement))
        except ContractError as exc:
            self._pause(state, exc.reason)
            return False
        except Exception:
            self._unknown += 1
            if self._unknown >= 3:
                self._pause(state, "runtime_unavailable")
            return False
        state["session_id"] = session_id
        observed = getattr(self.agent, "observed_model", None)
        if observed:
            state["model"] = observed
        if observed and observed != self.campaign.model:
            self._pause(state, "model_mismatch")
            return False
        self._unknown = 0
        self._log(
            state, "runner.session", "runner/session/" + session_id,
            action="replaced" if replacement else "created",
        )
        save_state(self.state_path, state)
        return True

    def _prompt(self, replacement: bool) -> str:
        text = _INSTRUCTIONS + "\n\n" + self.campaign.objective_text
        if replacement or (self.live / "progress.md").exists():
            text += "\n\nRead progress.md and continue. Do not repeat completed work."
        return text

    def _fresh(self) -> dict:
        return {
            "campaign_id": self.campaign.id,
            "checker_sha256": file_sha256(self.campaign.checker),
            "elapsed_seconds": 0.0,
            "last_diagnostics": None,
            "last_observation": None,
            "last_verdict": None,
            "model": None,
            "pause_reason": None,
            "pending_submission_id": None,
            "pending_text": None,
            "rounds": 0,
            "runtime": self.campaign.runtime,
            "session_id": None,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "validated_submission_id": None,
            "workspace": str(self.live),
        }

    def _guard(self, state: dict) -> None:
        if state.get("checker_sha256") and not self._digest_matches(state):
            self._pause(state, "checker_modified")

    def _digest_matches(self, state: dict) -> bool:
        return file_sha256(self.campaign.checker) == state["checker_sha256"]

    def _charge(self, state: dict) -> None:
        now = self.clock()
        state["elapsed_seconds"] = round(state["elapsed_seconds"] + (now - self._tick), 6)
        self._tick = now

    def _pause(self, state: dict, reason: str) -> None:
        self._charge(state)
        state["status"] = "paused"
        state["pause_reason"] = reason
        save_state(self.state_path, state)
        self._log(state, "runner.paused", _rid("runner"), reason=reason,
                  elapsed_seconds=state["elapsed_seconds"])

    def _complete(self, state: dict) -> None:
        self._charge(state)
        state["status"] = "complete"
        state["pause_reason"] = None
        save_state(self.state_path, state)
        self._log(state, "runner.completed", _rid("runner"),
                  elapsed_seconds=state["elapsed_seconds"])

    def _ingest(self, state: dict) -> None:
        session_id = state.get("session_id")
        if not session_id:
            return
        try:
            history = self.agent.history(session_id)
        except Exception:
            return
        raw = self.run_root / "raw" / f"{session_id}.json"
        atomic_write(raw, json_text(history.raw))
        records = []
        for event in history.events:
            record = {
                "schema": 1,
                "ts": _now(),
                "campaign_id": self.campaign.id,
                "runtime": self.campaign.runtime,
                "session_id": session_id,
            }
            record.update(event)
            records.append(record)
        if history.model and not state.get("model"):
            state["model"] = history.model
        append(self.log_path, records, self.secrets)

    def _log(self, state: dict, kind: str, event_id: str, **data) -> None:
        record = {
            "schema": 1,
            "ts": _now(),
            "campaign_id": self.campaign.id,
            "runtime": self.campaign.runtime,
            "session_id": state.get("session_id") or "",
            "event_id": event_id,
            "type": kind,
        }
        record.update(data)
        append(self.log_path, [record], self.secrets)


def run_accept(campaign: Campaign, workspace: Path) -> dict:
    env = os.environ.copy()
    env.pop("OPENCODE_SERVER_PASSWORD", None)
    env.pop("OPENCODE_SERVER_USERNAME", None)
    try:
        completed = subprocess.run(
            [*campaign.accept, str(workspace)],
            cwd=campaign.root,
            env=env,
            capture_output=True,
            timeout=campaign.accept_timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return _bad(None, "timeout")
    # shortcut: the process can emit more than the cap before we notice; the tail is not logged
    if len(completed.stdout) > _OUTPUT_CAP:
        return _bad(completed.returncode, "truncated")
    try:
        payload = json.loads(completed.stdout.decode())
    except (json.JSONDecodeError, UnicodeError):
        return _bad(completed.returncode, "unreadable")
    verdict = payload.get("verdict") if isinstance(payload, dict) else None
    diagnostics = ""
    if isinstance(payload, dict) and payload.get("diagnostics") is not None:
        diagnostics = str(payload["diagnostics"])
    if verdict not in ("pass", "fail", "inconclusive"):
        return _bad(completed.returncode, diagnostics or "unreadable")
    if verdict == "pass" and completed.returncode != 0:
        return _bad(completed.returncode, diagnostics)
    return {
        "ok": True,
        "verdict": verdict,
        "exit_code": completed.returncode,
        "diagnostics": diagnostics,
    }


def _bad(exit_code, diagnostics: str) -> dict:
    return {"ok": False, "verdict": None, "exit_code": exit_code, "diagnostics": diagnostics}


def json_text(value) -> str:
    return json.dumps(value, indent=2) + "\n"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rid(prefix: str) -> str:
    return prefix + "/" + uuid.uuid4().hex
