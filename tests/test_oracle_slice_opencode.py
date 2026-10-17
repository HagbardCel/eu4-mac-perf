"""P3-B: the pinned OpenCode binding, exercised offline.

No model, no network and no real ``opencode`` binary are involved. The adapter is
driven against a *stub* executable that speaks the pinned wire formats (a
``--version`` line and a line-delimited JSON event stream on ``run``) and that
repairs the candidate the way a model would. That makes the binding, the evidence
chain and the seal/verify path testable without a run.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from oracle.core import canonical_bytes
from oracle.slice import controller, corpus, harness, opencode, receipts
from oracle.slice.controller import (
    STATUS_ABORTED,
    STATUS_CERTIFIED,
    RunConfig,
)
from oracle.slice.opencode import OpenCodeAdapter, OpenCodeRun, materialize

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _tool_event(tool, status, arguments, error=None):
    state = {"status": status, "input": arguments}
    if error is not None:
        state["error"] = error
    return {"type": "tool_use", "part": {"tool": tool, "state": state}}


def _completed_stream(deny_shell=True, leak=None):
    events = [
        {"type": "step_start", "part": {}},
        {"type": "text", "part": {"text": "Inspect then test."}},
        _tool_event("oracle_inspect", "completed", {"function_id": "synth:apply"}),
        _tool_event("oracle_test", "completed", {"function_id": "synth:apply"}),
    ]
    if deny_shell:
        events.append(_tool_event("shell", "error", {"command": "echo SHELLRUN"},
                                  error="Permission denied: shell"))
    if leak is not None:
        events.append({"type": "text", "part": {"text": leak}})
    return events


def _write_stub(root: Path, events, *, write_candidate=True) -> Path:
    """A stand-in for the pinned binary: same argv surface, deterministic output."""
    (root / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (root / "repair.c").write_text((SLICE_DIR / "reference.c").read_text(), encoding="utf-8")
    stub = root / "opencode-stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, shutil, sys\n"
        "from pathlib import Path\n"
        "here = Path(__file__).resolve().parent\n"
        "if '--version' in sys.argv:\n"
        "    print('opencode v9.9.9-stub')\n"
        "    sys.exit(0)\n"
        "for event in json.loads((here / 'events.json').read_text()):\n"
        "    sys.stdout.write(json.dumps(event) + '\\n')\n"
        f"if {write_candidate!r}:\n"
        "    shutil.copyfile(here / 'repair.c', Path.cwd() / 'candidate.c')\n",
        encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _adapter(tmp: Path, stub: Path, **overrides) -> OpenCodeAdapter:
    base = tmp / "staging"
    params = dict(
        run_id="run-e2e",
        run_dir=base / "run-e2e",
        workspace=base / "run-e2e" / "workspace",
        repo_root=REPO_ROOT,
        python=Path(sys.executable),
        binary=stub,
        provider="opencode",
        model_id="deepseek-v4.1-flash-free",
        variant="high",
    )
    params.update(overrides)
    return OpenCodeAdapter(OpenCodeRun(**params))


def _run(tmp: Path, adapter, **overrides) -> controller.RunResult:
    params = dict(
        run_id="run-e2e",
        candidate_src=SLICE_DIR / "target.c",
        base_dir=tmp / "staging",
        budget_calls=60,
        max_attempts=1,
        wall_seconds=120.0,
        model={"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"},
    )
    params.update(overrides)
    return controller.run(RunConfig(**params), adapter)


class MaterializeTests(unittest.TestCase):
    def test_substitutes_every_token(self):
        self.assertEqual(materialize("@A@/@B@", {"@A@": "x", "@B@": "y"}), "x/y")

    def test_unused_token_is_refused(self):
        with self.assertRaises(opencode.BindingError):
            materialize("no tokens", {"@A@": "x"})

    def test_surviving_placeholder_is_refused(self):
        with self.assertRaises(opencode.BindingError):
            materialize("@A@ @LEFTOVER@", {"@A@": "x"})

    def test_tracked_template_renders_valid_json_with_pinned_restrictions(self):
        template = (SLICE_DIR / "opencode_config" / opencode.CONFIG_TEMPLATE).read_text()
        rendered = materialize(template, {
            "@MODEL@": "opencode/deepseek-v4.1-flash-free#high",
            "@PYTHON@": "/usr/bin/python3",
            "@REPO_ROOT@": "/repo",
            "@RUN_DIR@": "/staging/run",
            "@WORKSPACE@": "/staging/run/workspace",
        })
        config = json.loads(rendered)
        self.assertFalse(config["codemode"])
        self.assertFalse(config["autoupdate"])
        server = config["mcp"]["servers"]["oracle"]
        self.assertEqual(server["type"], "local")
        self.assertFalse(server["codemode"])
        self.assertEqual(server["command"][1:4], ["-m", "oracle.slice.bridge", "serve"])
        self.assertEqual(server["environment"]["PYTHONPATH"], "/repo")
        for action in ("shell", "subagent", "webfetch", "websearch", "browser", "question"):
            self.assertEqual(config["permission"][action], "deny")
        self.assertEqual(config["permission"]["external_directory"], {"*": "deny"})


class EventStreamTests(unittest.TestCase):
    def test_malformed_line_fails_closed(self):
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events('{"type": "text"}\nnot json\n')

    def test_empty_and_non_object_streams_are_refused(self):
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events("\n\n")
        with self.assertRaises(opencode.BindingError):
            opencode.parse_events("[1, 2, 3]\n")

    def test_summary_records_calls_and_normalizes_denials(self):
        events = opencode.parse_events("\n".join(json.dumps(e) for e in _completed_stream()))
        summary = opencode.summarize(events, run_id="run-e2e", stream_sha256="a" * 64,
                                     stream_bytes=10)
        names = {c["tool"] for c in summary["oracle_tool_calls"]}
        self.assertEqual(names, {"oracle_inspect", "oracle_test"})
        self.assertEqual(summary["denials"], [{
            "action": "shell", "resource": "echo SHELLRUN", "decision": "deny",
            "reason": "Permission denied: shell",
        }])

    def test_a_refusal_is_not_a_denial(self):
        events = [{"type": "text", "part": {"text": "I will not call shell."}},
                  _tool_event("read", "error", {"filePath": "/etc/hosts"},
                              error="File not found")]
        summary = opencode.summarize(events, run_id="r", stream_sha256="a" * 64, stream_bytes=1)
        self.assertEqual(summary["denials"], [])

    def test_an_absent_tool_produces_no_denial(self):
        summary = opencode.summarize([{"type": "text", "part": {"text": "no shell tool"}}],
                                     run_id="r", stream_sha256="a" * 64, stream_bytes=1)
        self.assertEqual(summary["denials"], [])
        self.assertEqual(summary["oracle_tool_calls"], [])


class LeakScanTests(unittest.TestCase):
    def test_fingerprints_include_the_prefix_and_every_held_out_fixture(self):
        fingerprints = opencode.held_out_fingerprints()
        self.assertIn(opencode.HELD_OUT_PREFIX, fingerprints)
        for fixture in corpus.load_corpus(corpus.HELD_OUT):
            self.assertIn(fixture.fixture_id, fingerprints)

    def test_a_held_out_id_in_the_stream_is_conclusive(self):
        leaks = opencode.scan_for_leaks('{"text": "see hid-003"}', ["hid-", "hid-003"])
        self.assertEqual(leaks, ["hid-", "hid-003"])


class OpenCodeEndToEndTests(unittest.TestCase):
    def test_a_real_repair_through_the_adapter_is_certified_and_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            result = _run(root, adapter)

            self.assertEqual(result.status, STATUS_CERTIFIED)
            payload = result.receipt["payload"]
            binding = payload["toolchain"]["opencode"]
            self.assertEqual(binding["binary"]["version"], "opencode v9.9.9-stub")
            self.assertEqual(binding["binary"]["name"], "opencode-stub")
            self.assertEqual(len(binding["binary"]["sha256"]), 64)
            self.assertEqual(binding["model"],
                             {"provider": "opencode", "id": "deepseek-v4.1-flash-free",
                              "variant": "high"})
            # The receipt is self-consistent with the summary it publishes.
            summary = adapter.sanitized_summary()
            self.assertEqual(binding["events"]["summary_sha256"],
                             opencode._sha256(canonical_bytes(summary)))
            self.assertEqual(binding["events"]["raw_sha256"], summary["stream"]["sha256"])
            self.assertEqual(binding["events"]["raw_bytes"], summary["stream"]["bytes"])
            self.assertGreater(binding["events"]["raw_bytes"], 0)
            self.assertEqual(binding["events"]["tool_calls"], 2)
            self.assertEqual(binding["events"]["denials"][0]["action"], "shell")
            # The model identity the controller recorded agrees with the binding.
            self.assertEqual(payload["agent"]["model"], binding["model"])
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])

            published = root / "agent.events.json"
            adapter.write_sanitized_summary(published)
            self.assertEqual(json.loads(published.read_text()), summary)

    def test_held_out_material_in_the_stream_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(leak="expected hid-000 2147483647"))
            result = _run(root, _adapter(root, stub))
            payload = result.receipt["payload"]
            # The candidate itself verified; only acceptance is withheld.
            self.assertTrue(payload["verdict"]["candidate_verified"])
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(payload["toolchain"].get("opencode"), None)
            self.assertIn("held-out material", payload["execution"]["finalization"]["error"])
            receipts.verify(result.receipt)

    def test_a_production_capped_stream_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            result = _run(root, adapter)
            stream = adapter._streams[0]
            # Simulate an event stream that filled the OS-enforced production cap:
            # the capture can no longer be trusted to be complete.
            stream.write_bytes(b"x" * harness.MAX_OUTPUT_BYTES)
            with self.assertRaises(opencode.BindingError):
                adapter.receipt_metadata()

    def test_probe_mode_is_non_accepting_and_spends_no_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(), write_candidate=False)
            adapter = _adapter(root, stub, mode="probe",
                               prompt_path=SLICE_DIR / "opencode_config" / "pilot.prompt.txt")
            result = _run(root, adapter)
            payload = result.receipt["payload"]
            self.assertNotEqual(result.status, STATUS_CERTIFIED)
            self.assertEqual(payload["verdict"]["held_out"], None)
            self.assertEqual(payload["execution"]["abort_reason"], "gave_up")
            self.assertEqual(payload["verdict"]["certification"],
                             "skipped: agent aborted: gave_up")

    def test_a_mismatched_pinned_binary_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            result = _run(root, _adapter(root, stub, expected_binary_sha256="0" * 64))
            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_ABORTED)
            # A binding fault is a trusted-host configuration failure, not an
            # agent crash; the attempt note names the cause and the run cannot be
            # accepted while the adapter was never prepared.
            self.assertIn("binary digest does not match", payload["execution"]["attempts"][0]["note"])
            self.assertIn("never prepared",
                          payload["execution"]["finalization"]["error"])
            self.assertNotIn("opencode", payload["toolchain"])
            receipts.verify(result.receipt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
