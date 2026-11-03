"""Deterministic agent for offline runner tests."""

from __future__ import annotations

from automation.agents import History, Observation


class FakeAgent:
    def __init__(self, model: str = "fake/model"):
        self.observed_model = model
        self.sessions: dict = {}
        self.prompts: list[str] = []
        self.submits: list[str] = []
        self.conflicts = 0
        self.interrupt = False
        self.hide_once = False
        self.phase_after_submit = "settled"
        self.inspect_phase: str | None = None
        self.on_submit = None
        self._next = 0

    def create(self, workspace, prompt: str) -> str:
        self.prompts.append(prompt)
        self._next += 1
        session_id = f"ses_fake_{self._next}"
        self.sessions[session_id] = {
            "workspace": workspace,
            "admitted": {},
            "events": [],
            "raw": [],
        }
        self.session_id = session_id
        return session_id

    def inspect(self, session_id: str, submission_id: str | None) -> Observation:
        if self.inspect_phase:
            return Observation(self.inspect_phase)
        session = self.sessions.get(session_id)
        if session is None:
            return Observation("missing")
        if not submission_id:
            return Observation("unknown")
        phase = session["admitted"].get(submission_id)
        if phase is None or phase == "hidden":
            return Observation("unknown", "absent")
        return Observation(phase, "present")

    def submit(self, session_id: str, message: str, submission_id: str) -> Observation:
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt
        session = self.sessions.get(session_id)
        if session is None:
            return Observation("missing")
        if submission_id in session["admitted"]:
            self.conflicts += 1
            session["admitted"][submission_id] = "settled"
            return Observation("settled", "present")
        self.submits.append(submission_id)
        if self.on_submit:
            self.on_submit(session["workspace"])
        phase = self.phase_after_submit
        if self.hide_once:
            self.hide_once = False
            phase = "hidden"
        session["admitted"][submission_id] = phase
        session["raw"].append({"id": submission_id, "type": "user", "text": message})
        if phase == "hidden":
            return Observation("unknown", "absent")
        return Observation(phase, "present")

    def history(self, session_id: str) -> History:
        session = self.sessions[session_id]
        return History(raw=list(session["raw"]), events=list(session["events"]), model=self.observed_model)
