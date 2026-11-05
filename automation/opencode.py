"""OpenCode 2.2.5 V2 HTTP adapter.

Verified against the local server's ``/openapi.json`` (binary ``opencode v2.2.5``).
That spec has no JSON health route; ``GET /global/health`` is the web UI.
Settlement is the inbox, the active list, and a later idle message. The
experimental wait route is not called: a 204 arrived while work was still queued.
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from automation.agents import ContractError, History, Observation

VERIFIED_VERSION = "2.2.5"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_TERMINAL = {"succeeded", "failed", "interrupted"}
_HISTORY_PAGES = 100


class TransportError(RuntimeError):
    pass


class OpenCodeAgent:
    def __init__(self, base_url: str, *, username: str, password: str | None,
                 timeout: float, model: str):
        _require_loopback(base_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model = model
        self.observed_model: str | None = None
        self.protected_root: Path | None = None
        token = None
        if password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._headers = {"Accept": "application/json"}
        if token:
            self._headers["Authorization"] = "Basic " + token

    def create(self, workspace: Path, prompt: str) -> str:
        workspace = workspace.resolve()
        provider, _, model_id = self.model.partition("/")
        status, payload = self._call("POST", "/api/session", {
            "title": (prompt or "automation").splitlines()[0][:80],
            "location": {"directory": str(workspace)},
            "model": {"providerID": provider, "id": model_id},
            "permissions": permission_rules(workspace, self.protected_root),
        })
        if status != 200 or not isinstance(payload, dict):
            raise TransportError(f"create failed: {status}")
        data = payload["data"]
        if Path(data["location"]["directory"]).resolve() != workspace:
            raise ContractError("workspace_mismatch")
        observed = _model_name(data.get("model"))
        self.observed_model = observed
        if observed != self.model:
            raise ContractError("model_mismatch")
        return data["id"]

    def inspect(self, session_id: str, submission_id: str | None) -> Observation:
        try:
            status, _payload = self._call("GET", f"/api/session/{session_id}")
        except TransportError:
            return Observation("unknown")
        if status == 404:
            return Observation("missing")
        if status != 200:
            return Observation("unknown")
        try:
            return self._observe(session_id, submission_id)
        except TransportError:
            return Observation("unknown")

    def submit(self, session_id: str, message: str, submission_id: str) -> Observation:
        try:
            status, _payload = self._call("POST", f"/api/session/{session_id}/prompt", {
                "id": submission_id,
                "text": message,
            })
        except TransportError:
            return Observation("unknown")
        if status == 404:
            return Observation("missing")
        if status not in (200, 409):
            return Observation("unknown")
        return self.inspect(session_id, submission_id)

    def history(self, session_id: str) -> History:
        messages = self._messages(session_id)
        events = normalize(messages)
        model = None
        tokens = None
        cost = None
        for message in messages:
            if message.get("type") != "assistant":
                continue
            model = _model_name(message.get("model")) or model
            if isinstance(message.get("tokens"), dict):
                tokens = message["tokens"]
            if isinstance(message.get("cost"), (int, float)):
                cost = message["cost"]
        return History(raw=messages, events=events, model=model, tokens=tokens, cost=cost)

    def _observe(self, session_id: str, submission_id: str | None) -> Observation:
        if self._permission_pending(session_id):
            return Observation("needs_input", "present")
        if submission_id and self._queued(session_id, submission_id):
            return Observation("queued", "present")
        if self._active(session_id):
            return Observation("executing", "present")
        if not submission_id:
            return Observation("unknown")
        messages = self._messages(session_id)
        if _settled(messages, submission_id):
            return Observation("settled", "present")
        if _delivered(messages, submission_id):
            return Observation("executing", "present")
        return Observation("unknown", "absent")

    def _permission_pending(self, session_id: str) -> bool:
        status, payload = self._call("GET", f"/api/session/{session_id}/permission")
        if status != 200 or not isinstance(payload, dict):
            raise TransportError(f"permission failed: {status}")
        return bool(payload.get("data"))

    def _active(self, session_id: str) -> bool:
        status, payload = self._call("GET", "/api/session/active")
        if status != 200 or not isinstance(payload, dict):
            raise TransportError(f"active failed: {status}")
        return session_id in (payload.get("data") or {})

    def _queued(self, session_id: str, submission_id: str) -> bool:
        status, payload = self._call("GET", f"/api/session/{session_id}/inbox")
        if status != 200 or not isinstance(payload, dict):
            raise TransportError(f"inbox failed: {status}")
        return any(item.get("id") == submission_id for item in payload.get("data") or [])

    def _messages(self, session_id: str) -> list:
        cursor = None
        found: list = []
        seen: set[str] = set()
        requested: set[str] = set()
        for _page in range(_HISTORY_PAGES):
            if cursor is not None:
                if cursor in requested:
                    raise ContractError("history_incomplete")
                requested.add(cursor)
                query = "?limit=50&cursor=" + urllib.parse.quote(cursor, safe="")
            else:
                query = "?limit=50&order=asc"
            status, payload = self._call("GET", f"/api/session/{session_id}/message{query}")
            if status == 404:
                raise TransportError("session missing")
            if status != 200 or not isinstance(payload, dict):
                raise TransportError(f"history failed: {status}")
            page = payload.get("data") or []
            fresh = [item for item in page if item.get("id") not in seen]
            next_cursor = (payload.get("cursor") or {}).get("next")
            if not fresh:
                # A follow-up that only repeats seen ids and an unchanged cursor is the
                # 2.2.5 end marker. Any other live cursor is an incomplete page.
                if not next_cursor or next_cursor == cursor:
                    return found
                raise ContractError("history_incomplete")
            for item in fresh:
                seen.add(item.get("id"))
            found.extend(fresh)
            if not next_cursor:
                return found
            if next_cursor == cursor or next_cursor in requested:
                raise ContractError("history_incomplete")
            cursor = next_cursor
        raise ContractError("history_incomplete")

    def _call(self, method: str, path: str, body: dict | None = None, timeout: float | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=self._headers)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout if timeout is None else timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            code = exc.code
            exc.close()
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            return code, parsed
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            raise TransportError(str(exc)) from exc
        try:
            return status, json.loads(raw) if raw else None
        except json.JSONDecodeError as exc:
            raise TransportError(str(exc)) from exc


def permission_rules(workspace: Path, protected: Path | None = None) -> list[dict]:
    """State the intended scope. This is not a filesystem sandbox.

    A leading deny of ``*`` matches relative tool paths such as ``progress.md``
    and blocks the workspace. The protected directory is denied by its absolute
    prefix; relative workspace paths are allowed by ``**``.
    """
    root = str(workspace.resolve())
    rules = []
    if protected is not None:
        denied = str(Path(protected).resolve())
        rules.append({"action": "read", "resource": denied + "/**", "effect": "deny"})
        rules.append({"action": "edit", "resource": denied + "/**", "effect": "deny"})
    rules.extend([
        {"action": "read", "resource": "**", "effect": "allow"},
        {"action": "edit", "resource": "**", "effect": "allow"},
        {"action": "read", "resource": root + "/**", "effect": "allow"},
        {"action": "edit", "resource": root + "/**", "effect": "allow"},
        {"action": "bash", "resource": "*", "effect": "allow"},
        {"action": "skill", "resource": "opengl-metal-reconstruction", "effect": "allow"},
    ])
    return rules


def normalize(messages: list) -> list[dict]:
    events = []
    for message in messages:
        kind = message.get("type")
        message_id = message.get("id") or "message"
        if kind == "user":
            events.append({
                "event_id": f"{message_id}/message",
                "type": "message",
                "role": "user",
                "text": message.get("text") or "",
            })
        elif kind == "assistant":
            events.extend(_assistant_events(message, message_id))
        elif kind == "idle" and message.get("outcome") in _TERMINAL:
            event = {
                "event_id": f"{message_id}/turn.end",
                "type": "turn.end",
                "status": message.get("outcome"),
            }
            events.append(event)
    return events


def _assistant_events(message: dict, message_id: str) -> list[dict]:
    events = []
    text = []
    for part in message.get("content") or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and part.get("text"):
            text.append(part["text"])
        if part.get("type") == "tool":
            event = _tool_event(message_id, part)
            if event:
                events.append(event)
    record = {
        "event_id": f"{message_id}/message",
        "type": "message",
        "role": "assistant",
    }
    if text:
        record["text"] = "\n".join(text)
    model = _model_name(message.get("model"))
    if model:
        record["model"] = model
    if isinstance(message.get("tokens"), dict):
        record["tokens"] = message["tokens"]
    if isinstance(message.get("cost"), (int, float)):
        record["cost"] = message["cost"]
    events.append(record)
    return events


def _tool_event(message_id: str, part: dict) -> dict | None:
    state = part.get("state") or {}
    status = state.get("status")
    if status in ("running", "streaming"):
        kind = "tool.started"
    elif status in ("completed", "error"):
        kind = "tool.completed"
    else:
        return None
    event = {
        "event_id": f"{message_id}/{part.get('id')}/{kind}",
        "type": kind,
        "tool": part.get("name") or "",
        "status": status,
    }
    duration = _duration(part.get("time") or {})
    if duration is not None:
        event["duration_ms"] = duration
    path = _tool_path(state.get("input"))
    if path:
        event["path"] = path
    args = _tool_args(state.get("input"))
    if args:
        event["args"] = args
    if kind == "tool.completed":
        output = _tool_text(state) or _error_text(state.get("error"))
        if output:
            event["text"] = output
    return event


def _duration(time: dict) -> int | None:
    start = time.get("ran")
    end = time.get("completed")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return int(end - start)
    return None


def _tool_args(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    summary = {}
    for key, item in value.items():
        if isinstance(item, str):
            summary[key] = item[:200]
        elif isinstance(item, (int, float, bool)):
            summary[key] = item
    return summary or None


def _tool_path(value) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("path", "filePath", "file"):
        if isinstance(value.get(key), str):
            return value[key]
    return None


def _error_text(error) -> str:
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return ""


def _tool_text(state: dict) -> str:
    parts = []
    for item in state.get("content") or []:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _model_name(model) -> str | None:
    if not isinstance(model, dict):
        return None
    provider = model.get("providerID")
    model_id = model.get("id")
    if provider and model_id:
        return f"{provider}/{model_id}"
    return None


def _settled(messages: list, submission_id: str) -> bool:
    index = _user_index(messages, submission_id)
    if index is None:
        return False
    return any(
        message.get("type") == "idle" and message.get("outcome") in _TERMINAL
        for message in messages[index + 1:]
    )


def _delivered(messages: list, submission_id: str) -> bool:
    return _user_index(messages, submission_id) is not None


def _user_index(messages: list, submission_id: str) -> int | None:
    for index, message in enumerate(messages):
        if message.get("type") == "user" and message.get("id") == submission_id:
            return index
    return None


def _require_loopback(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or host not in _LOOPBACK:
        raise ValueError(f"server must be loopback, got {url}")
