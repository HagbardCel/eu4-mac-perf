"""Bounded automatic diagnostic flight recorder (PR 4).

Ordinary campaigns keep a small rolling record of recent frames, health and
comparison outcomes.  On the first failure the record is frozen and written as
evidence; no continuous full-resolution archive is created.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import deque
from pathlib import Path

SCHEMA = "gfx-flight-recorder/v1"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class FlightRecorder:
    def __init__(self, *, max_frames: int = 256, max_events: int = 512):
        if max_frames <= 0 or max_events <= 0:
            raise ValueError("flight recorder bounds must be positive")
        self.max_frames = max_frames
        self.max_events = max_events
        self.frames: deque[dict] = deque(maxlen=max_frames)
        self.events: deque[dict] = deque(maxlen=max_events)
        self.dropped_frames = 0
        self.dropped_events = 0
        self.frozen = False
        self.freeze_reason = ""

    def frame(self, value: dict) -> None:
        if self.frozen:
            self.dropped_frames += 1
            return
        if len(self.frames) == self.max_frames:
            self.dropped_frames += 1
        self.frames.append(dict(value))

    def event(self, kind: str, message: str, **detail) -> None:
        if self.frozen:
            self.dropped_events += 1
            return
        if len(self.events) == self.max_events:
            self.dropped_events += 1
        self.events.append({
            "at": _now(),
            "kind": kind,
            "message": message,
            "detail": detail,
        })

    def freeze(self, reason: str) -> None:
        if not self.frozen:
            self.frozen = True
            self.freeze_reason = reason

    def snapshot(self) -> dict:
        return {
            "schema": SCHEMA,
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "max_frames": self.max_frames,
            "max_events": self.max_events,
            "frames": list(self.frames),
            "events": list(self.events),
            "dropped_frames": self.dropped_frames,
            "dropped_events": self.dropped_events,
        }

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.snapshot(), indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        return path
