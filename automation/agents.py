"""Shared agent types. The runner does not import OpenCode."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Observation:
    """What the runner may do next. ``record`` is empty when the transport failed."""

    phase: str
    record: str = ""


@dataclass
class History:
    raw: list
    events: list[dict]
    model: str | None = None
    tokens: dict | None = None
    cost: float | None = None


class Agent(Protocol):
    def create(self, workspace: Path, prompt: str) -> str: ...

    def inspect(self, session_id: str, submission_id: str | None) -> Observation: ...

    def submit(self, session_id: str, message: str, submission_id: str) -> Observation: ...

    def history(self, session_id: str) -> History: ...
