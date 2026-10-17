"""Pinned OpenCode binding for the P3 slice (PR-B).

PR-A proved the deterministic half of the P3 gate with an in-process stub. PR-B
is the real thing: the *pinned* OpenCode binary, driven by a *pinned* model,
repairs the candidate and the controller certifies the result. This module owns
the agent-facing half of that and nothing else - it never decides whether a
candidate is correct.

Three properties are load-bearing:

* **The permission surface is not the agent's to edit.** ``opencode.json`` is
  rendered from a tracked template by substituting a declared map, written
  *outside* the agent's workspace, and handed to the child through
  ``OPENCODE_CONFIG``. The agent's file tools operate inside the workspace only
  (external paths are denied), so it cannot rewrite the configuration that
  constrains it. The **template** digest is a pre-registered identity; the
  **materialized** digest is recorded in the receipt, so the exact configuration
  the agent ran under is reproducible from the tracked template plus the
  declared substitution map.

* **Evidence comes from the complete capture, not the retention window.** The
  supervisor retains only ``harness.MAX_CAPTURE_BYTES`` of a child's output for
  display, but the event stream is a line-delimited JSON log that legitimately
  exceeds that. The binding is therefore built from the on-disk capture file
  (OS-capped at ``harness.MAX_OUTPUT_BYTES``) and *fails closed* on a missing,
  empty, malformed or production-capped stream, so an incomplete record can
  never be mistaken for a complete one.

* **Binding is validated by the controller.** This module only *reports*
  identities; the controller checks their shape and folds them into the sealed
  receipt before publication. A partial or malformed report prevents acceptance.

Configuration and event formats were pinned against the installed binary
(``opencode v2.0.30``); see ``docs/oracle/P3_SLICE.md``.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

from oracle.core import canonical_bytes
from oracle.slice import corpus, harness, receipts
from oracle.slice.adapters import AttemptResult, BindingError

CONFIG_TEMPLATE = "opencode.json.template"
REPAIR_PROMPT = "prompt.txt"
PILOT_PROMPT = "pilot.prompt.txt"

SUMMARY_SCHEMA = "eu4-oracle-opencode-events/v1"

#: The refusal prefix the pinned binary writes into ``part.state.error`` when a
#: permission rule rejects a direct tool call. Verified against v2.0.30; it is a
#: *structured* signal (tool + input + ``status == "error"``), not model prose.
PERMISSION_DENIED_PREFIX = "Permission denied:"

#: Held-out identifiers always start with this prefix, so the leak scan needs no
#: corpus access to catch a stray fixture id.
HELD_OUT_PREFIX = "hid-"

_PLACEHOLDER = re.compile(r"@[A-Z_]+@")


# --- configuration materialization --------------------------------------------


def materialize(template_text: str, substitutions: dict[str, str]) -> str:
    """Render a template by literal substitution, refusing silent partials.

    Every declared token must appear (so a template edit that drops one is a
    loud failure rather than dead configuration), and no placeholder may survive
    (so an undeclared token cannot leak a literal ``@...@`` into the child's
    configuration).
    """
    rendered = template_text
    for token, value in substitutions.items():
        if token not in rendered:
            raise BindingError(f"config template does not use the token {token}")
        rendered = rendered.replace(token, value)
    leftover = sorted(set(_PLACEHOLDER.findall(rendered)))
    if leftover:
        raise BindingError(
            "config template has unsubstituted placeholders: " + ", ".join(leftover))
    return rendered


# --- event stream -------------------------------------------------------------


def parse_events(text: str) -> list[dict[str, Any]]:
    """Parse a line-delimited JSON event stream, failing closed on any defect.

    A malformed line is *not* skipped: silently dropping a line could hide a tool
    call or a denial, so the whole binding is refused instead.
    """
    events: list[dict[str, Any]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BindingError(f"event stream line {number} is not valid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise BindingError(f"event stream line {number} is not a JSON object")
        events.append(event)
    if not events:
        raise BindingError("event stream contains no events")
    return events


def _tool_calls(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        state = part.get("state")
        if not isinstance(state, dict):
            state = {}
        arguments = state.get("input")
        calls.append({
            "tool": part.get("tool"),
            "status": state.get("status"),
            "input": arguments if isinstance(arguments, dict) else {},
            "error": state.get("error"),
        })
    return calls


def _resource(arguments: dict[str, Any]) -> str:
    for key in ("command", "filePath", "path", "pattern", "url", "query"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return "*"


def _denial(call: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a machine-observed permission refusal, or return ``None``.

    Only ``status == "error"`` with the pinned refusal prefix counts. An absent
    tool produces no event at all, a model refusal produces text, and an
    unrelated failure produces a different error string - none of them are
    denials, and none of them are silently accepted as one.
    """
    if call.get("status") != "error":
        return None
    error = call.get("error")
    if not isinstance(error, str) or not error.startswith(PERMISSION_DENIED_PREFIX):
        return None
    return {
        "action": str(call.get("tool")),
        "resource": _resource(call.get("input") or {}),
        "decision": "deny",
        "reason": error,
    }


def summarize(events: list[dict[str, Any]], *, run_id: str, stream_sha256: str,
              stream_bytes: int) -> dict[str, Any]:
    """A compact, publishable digest of an event stream.

    Model text and tool outputs are deliberately excluded: they can carry held-out
    fragments and are not evidence. What is kept - event types, tool names and
    statuses, and normalized denials - is exactly what the acceptance criteria
    need, and nothing that could leak.
    """
    calls = _tool_calls(events)
    recorded = [{"tool": c["tool"], "status": c["status"]} for c in calls]
    oracle = [c for c in recorded
              if isinstance(c["tool"], str) and c["tool"].startswith("oracle_")]
    denials = [d for d in (_denial(c) for c in calls) if d is not None]
    return {
        "schema": SUMMARY_SCHEMA,
        "run_id": run_id,
        "stream": {"bytes": stream_bytes, "sha256": stream_sha256},
        "event_types": dict(sorted(Counter(e.get("type") for e in events).items(),
                                   key=lambda item: str(item[0]))),
        "tool_calls": recorded,
        "oracle_tool_calls": oracle,
        "denials": denials,
    }


# --- held-out leak detection (C3b) --------------------------------------------

def held_out_fingerprints(root: Path | None = None) -> list[str]:
    """Identifiers whose appearance anywhere agent-visible is conclusive.

    The visible-only tool surface cannot *serve* held-out material, so any
    appearance of a held-out fixture id, of its file digest, or of its expected
    observation can only come from exfiltration, not from the API. This is a
    **detection** net, labelled as such: PR-F adds OS-level isolation.
    """
    fingerprints = [HELD_OUT_PREFIX]
    for fixture in corpus.load_corpus(corpus.HELD_OUT, root=root):
        fingerprints.append(fixture.fixture_id)
        fingerprints.append(fixture.sha256)
        fingerprints.append(_sha256(canonical_bytes(fixture.expected)))
    return fingerprints


def scan_for_leaks(text: str, fingerprints: Iterable[str]) -> list[str]:
    """Return the fingerprints present in ``text`` (case-insensitive)."""
    lowered = text.lower()
    return sorted({f for f in fingerprints if f and f.lower() in lowered})


# --- hashing helpers ----------------------------------------------------------

def _sha256(data: bytes) -> str:
    from hashlib import sha256
    return sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return receipts.file_sha256(Path(path))


# --- the adapter --------------------------------------------------------------


@dataclass(frozen=True)
class OpenCodeRun:
    """Everything the adapter needs to bind one pinned OpenCode execution."""

    run_id: str
    run_dir: Path
    workspace: Path
    repo_root: Path
    python: Path
    binary: Path
    provider: str
    model_id: str
    variant: str | None = None
    prompt_path: Path | None = None
    template_dir: Path | None = None
    timeout_s: float = 1800.0
    #: ``"repair"`` runs the task and reports completion; ``"probe"`` runs the
    #: pilot, which is deliberately non-accepting and ends with ``gave_up`` so it
    #: can never be mistaken for a certified repair.
    mode: str = "repair"
    expected_binary_sha256: str | None = None
    expected_version: str | None = None

    @property
    def model(self) -> str:
        base = f"{self.provider}/{self.model_id}"
        return f"{base}#{self.variant}" if self.variant else base

    @property
    def default_prompt(self) -> str:
        return PILOT_PROMPT if self.mode == "probe" else REPAIR_PROMPT


class OpenCodeAdapter:
    """Drives the pinned OpenCode binary through the controller's supervisor."""

    name = "opencode"

    def __init__(self, run: OpenCodeRun):
        self.run = run
        self._config_path = Path(run.run_dir) / "opencode.json"
        self._xdg = Path(run.run_dir) / "xdg"
        self._prepared = False
        self._identity: dict[str, str] | None = None
        self._template_sha256: str | None = None
        self._materialized_sha256: str | None = None
        self._prompt_sha256: str | None = None
        self._prompt_text: str | None = None
        self._streams: list[Path] = []
        self._raw_sha256: str | None = None
        self._raw_bytes = 0
        self._summary: dict[str, Any] | None = None

    # -- layout ---------------------------------------------------------------
    def _template_dir(self) -> Path:
        if self.run.template_dir is not None:
            return Path(self.run.template_dir)
        return Path(__file__).resolve().parent / "opencode_config"

    def _prompt_path(self) -> Path:
        if self.run.prompt_path is not None:
            return Path(self.run.prompt_path)
        return self._template_dir() / self.run.default_prompt

    def _prompt(self) -> str:
        if self._prompt_text is None:
            self._prompt_text = self._prompt_path().read_text(encoding="utf-8")
        return self._prompt_text

    # -- preparation ----------------------------------------------------------
    def _render_config(self) -> str:
        template = (self._template_dir() / CONFIG_TEMPLATE).read_text(encoding="utf-8")
        return materialize(template, {
            "@MODEL@": self.run.model,
            "@PYTHON@": str(self.run.python),
            "@REPO_ROOT@": str(self.run.repo_root),
            "@RUN_DIR@": str(self.run.run_dir),
            "@WORKSPACE@": str(self.run.workspace),
        })

    def _probe_identity(self, supervisor: Any) -> dict[str, str]:
        binary = Path(self.run.binary)
        if not binary.is_file():
            raise BindingError(f"OpenCode binary not found: {binary}")
        digest = _sha256_file(binary)
        if self.run.expected_binary_sha256 and digest != self.run.expected_binary_sha256:
            raise BindingError(
                "OpenCode binary digest does not match the pinned value "
                f"({digest} != {self.run.expected_binary_sha256})")
        result = supervisor.run_group([str(binary), "--version"])
        text = f"{result.stdout}\n{result.stderr}"
        version = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if result.returncode != 0 or not version:
            raise BindingError("could not establish the OpenCode version")
        if self.run.expected_version and self.run.expected_version not in version:
            raise BindingError(
                f"OpenCode version does not match the pinned value: {version!r}")
        return {"name": binary.name, "sha256": digest, "version": version}

    def prepare(self, supervisor: Any) -> None:
        """Materialize the child's configuration once, before the first run."""
        if self._prepared:
            return
        run_dir, workspace = Path(self.run.run_dir), Path(self.run.workspace)
        if not run_dir.is_dir():
            raise BindingError(f"run directory does not exist: {run_dir}")
        if not workspace.is_dir():
            raise BindingError(f"workspace does not exist: {workspace}")
        if not (workspace / "candidate.c").is_file():
            raise BindingError(f"workspace has no candidate.c: {workspace}")

        rendered = self._render_config()
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(rendered, encoding="utf-8")
        self._config_path.chmod(0o600)
        for name in ("config", "data", "cache", "state"):
            (self._xdg / name).mkdir(parents=True, exist_ok=True)

        self._template_sha256 = _sha256_file(self._template_dir() / CONFIG_TEMPLATE)
        self._materialized_sha256 = _sha256(rendered.encode("utf-8"))
        self._prompt_sha256 = _sha256_file(self._prompt_path())
        self._identity = self._probe_identity(supervisor)
        self._prepared = True

    # -- invocation -----------------------------------------------------------
    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update({
            "XDG_CONFIG_HOME": str(self._xdg / "config"),
            "XDG_DATA_HOME": str(self._xdg / "data"),
            "XDG_CACHE_HOME": str(self._xdg / "cache"),
            "XDG_STATE_HOME": str(self._xdg / "state"),
            # The configuration lives outside the workspace, so the agent's own
            # file tools cannot reach it; no ambient project configuration from a
            # cwd is relied upon either.
            "OPENCODE_CONFIG": str(self._config_path),
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        })
        return env

    def step(self, ctx: Any) -> AttemptResult:
        self.prepare(ctx.supervisor)
        argv = [str(self.run.binary), "run", "--standalone", "--format", "json", "--auto",
                "--model", self.run.model, self._prompt()]
        run = ctx.supervisor.run_group(argv, cwd=Path(self.run.workspace), env=self._child_env())
        if run.stdout_path is not None:
            self._streams.append(run.stdout_path)
        if run.timed_out:
            return AttemptResult("timeout", "opencode run exceeded the run deadline")
        if run.returncode != 0:
            tail = (run.stderr or "").strip().splitlines()[-1:] or [""]
            return AttemptResult("crashed", f"opencode run exited {run.returncode}: {tail[0][:200]}")
        if self.run.mode == "probe":
            return AttemptResult("gave_up", "pilot probe completed")
        return AttemptResult("done", "opencode run completed")

    # -- evidence -------------------------------------------------------------
    def _raw_stream(self) -> bytes:
        if not self._streams:
            raise BindingError("no OpenCode event stream was captured")
        chunks: list[bytes] = []
        for path in self._streams:
            path = Path(path)
            if not path.is_file():
                raise BindingError(f"event stream is missing: {path}")
            size = path.stat().st_size
            if size == 0:
                raise BindingError(f"event stream is empty: {path}")
            if size >= harness.MAX_OUTPUT_BYTES:
                raise BindingError(
                    "event stream reached the production cap, so the capture is not "
                    f"complete: {path}")
            chunks.append(path.read_bytes())
        return b"".join(chunks)

    def receipt_metadata(self) -> dict[str, Any] | None:
        if not self._prepared or self._identity is None:
            raise BindingError("the OpenCode adapter was never prepared")
        raw = self._raw_stream()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BindingError(f"event stream is not valid UTF-8: {exc}") from exc
        events = parse_events(text)

        leaks = scan_for_leaks(text, held_out_fingerprints())
        if leaks:
            raise BindingError(
                "held-out material appeared in the agent-visible surface: "
                + ", ".join(leaks))

        self._raw_sha256 = _sha256(raw)
        self._raw_bytes = len(raw)
        summary = summarize(events, run_id=self.run.run_id,
                            stream_sha256=self._raw_sha256, stream_bytes=self._raw_bytes)
        self._summary = summary
        denials = summary["denials"]
        completed_oracle = [c for c in summary["oracle_tool_calls"]
                            if c["status"] == "completed"]
        return {
            "adapter": "opencode",
            "binary": dict(self._identity),
            "model": {"provider": self.run.provider, "id": self.run.model_id,
                      "variant": self.run.variant},
            "prompt_sha256": self._prompt_sha256,
            "config": {"template_sha256": self._template_sha256,
                       "materialized_sha256": self._materialized_sha256},
            "events": {
                "raw_sha256": self._raw_sha256,
                "raw_bytes": self._raw_bytes,
                "summary_sha256": _sha256(canonical_bytes(summary)),
                "tool_calls": len(completed_oracle),
                "denials": denials,
            },
        }

    def sanitized_summary(self) -> dict[str, Any]:
        if self._summary is None:
            raise BindingError("no event summary has been built yet")
        return self._summary

    def write_sanitized_summary(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(self.sanitized_summary()) + b"\n")
        return path
