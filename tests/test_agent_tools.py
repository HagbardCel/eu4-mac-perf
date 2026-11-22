"""PR 5: one JSON tool core for every agent surface."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RATIONALE = (
    '{"question": "q", "evidence": ["."], "gap": "g", '
    '"hypothesis": "h", "action": "a", "expected_information": "e", '
    '"cheaper_alternatives_checked": true}'
)


def _with_rationale(argv):
    """Test helper: executing replays need a rationale; verification does not.

    The dedicated refusal test calls ``eu4_replay`` directly, without this
    helper, so the requirement is still exercised.
    """
    items = list(argv)
    if items and items[0] == "eu4_replay" and "--rationale" not in items:
        items += ["--rationale", RATIONALE]
    return items


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import agent_tools, gfx_replay_case, gfx_session  # noqa: E402
from reverse_engineering import index_db  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.index = cls.root / "index.sqlite"
        index_db.rebuild_index(cls.index, FIXTURE / "inventory", FIXTURE / "decompiled")

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def call(self, argv, env=None):
        output = self.root / "tool.json"
        argv = _with_rationale(argv)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            if env:
                with mock.patch.dict(os.environ, env):
                    code = agent_tools.main(["--out", str(output), *argv])
            else:
                code = agent_tools.main(["--out", str(output), *argv])
        return code, json.loads(output.read_text())

    def test_every_tool_has_an_envelope(self):
        code, value = self.call([
            "eu4_re_query", "resolve", "SShaderOpenGL",
            "--index", str(self.index),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(value["schema"], agent_tools.SCHEMA)
        self.assertEqual(value["tool"], "eu4_re_query")
        self.assertTrue(value["ok"])
        self.assertEqual(value["status"], "ok")

    def test_re_query_decompile_is_dispatched(self):
        code, value = self.call(["eu4_re_query", "decompile", "0x2000",
                                 "--index", str(self.index), "--max-bytes", "32"])
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "ok")
        self.assertIn("indirect_call", value["result"]["pseudocode"])
        self.assertTrue(value["result"].get("truncated"))

    def test_re_query_function_and_abi(self):
        code, value = self.call(["eu4_re_query", "function", "0x2000",
                                 "--index", str(self.index)])
        self.assertEqual(code, 0)
        self.assertIn("Flush", value["result"]["name_demangled"])
        code, value = self.call(["eu4_re_query", "abi", "0x2000",
                                 "--index", str(self.index)])
        self.assertEqual(value["status"], "unavailable")

    def test_session_list_and_inspect(self):
        with tempfile.TemporaryDirectory() as registry:
            env = {"EU4_GFX_SESSION_ROOT": registry}
            with mock.patch.dict(os.environ, env):
                session = gfx_session.Session.create(
                    ROOT / ".local" / "agent-tools-test",
                    workflow_id="tool-test", profile="early",
                    session_root_path=Path(registry),
                )
                try:
                    code, value = self.call(["eu4_session", "list"])
                    self.assertTrue(any(row["session_id"] == session.session_id
                                        for row in value["result"]["sessions"]))
                    code, value = self.call(["eu4_session", "inspect",
                                             "--run", str(session.run_dir)])
                    self.assertEqual(value["result"]["session_id"], session.session_id)
                finally:
                    session.close()

    def test_session_terminate_refuses_a_foreign_worktree(self):
        with tempfile.TemporaryDirectory() as registry:
            with mock.patch.dict(os.environ, {"EU4_GFX_SESSION_ROOT": registry}):
                session = gfx_session.Session.create(
                    Path(registry) / "runs", workflow_id="foreign",
                    profile="early", session_root_path=Path(registry),
                )
                try:
                    state = json.loads((session.run_dir / "session.json").read_text())
                    state["worktree"] = "/some/other/worktree"
                    (session.run_dir / "session.json").write_text(json.dumps(state))
                    code, value = self.call(["eu4_session", "terminate",
                                             "--run", str(session.run_dir)])
                    self.assertEqual(code, 2)
                    self.assertEqual(value["status"], "refused")
                    self.assertIn("another worktree", value["reason"])
                finally:
                    session.close()

    def test_capture_health_on_a_run(self):
        run = self.root / "run"
        (run / "reference" / "observe").mkdir(parents=True)
        (run / "reference" / "observe" / "frame-observe.jsonl").write_text(
            json.dumps({"frame_index": 1, "sha256": "a" * 64, "width": 8, "height": 6,
                        "capture_valid": True, "gpu_completed": True}) + "\n"
        )
        code, value = self.call(["eu4_capture", "health", "--run", str(run),
                                 "--require-pixels"])
        self.assertEqual(code, 0)
        self.assertEqual(value["result"]["health"]["verdict"], "unknown")
        self.assertFalse(value["result"]["gate"]["allow"])

    def test_capture_request_uses_the_native_request_protocol(self):
        run = self.root / "request-run"
        (run / "candidate" / "observe").mkdir(parents=True)
        code, value = self.call([
            "eu4_capture", "request", "--run", str(run),
            "--role", "candidate", "--frame", "7", "--frame", "9",
        ])
        self.assertEqual(code, 0, value)
        request = json.loads(
            (run / "candidate" / "observe" / "capture-request.json").read_text())
        self.assertEqual(request["schema"], "gfx-frame-request/v1")
        self.assertIsInstance(request["request_id"], int)
        self.assertEqual(request["request_id"], value["result"]["request_id"])
        self.assertEqual(request["frames"], [7, 9])
        self.assertEqual(request["capacity"], 256)
        code, value = self.call([
            "eu4_capture", "request", "--run", str(run),
            "--role", "candidate",
            *[token for index in range(257) for token in ("--frame", str(index))],
        ])
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "refused")

    def test_diff_inspect_and_case_inspect(self):
        run = self.run_fixture()
        code, value = self.call(["eu4_diff_inspect", "--run", str(run),
                                 "--build-case"])
        self.assertEqual(code, 0)
        self.assertEqual(value["result"]["comparison"]["status"], "fail")
        case_dir = value["result"]["case"]["case_dir"]
        code, value = self.call(["eu4_case_inspect", "--case", case_dir])
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["result"]["classification"], "rendering_difference")
        code, value = self.call(["eu4_replay", "case", "--case", case_dir])
        self.assertEqual(code, 0)
        self.assertEqual(value["status"], "ok")
        self.assertIn(value["result"]["replay_status"],
                      ("pass", "fail", "unverified"),
                      msg=json.dumps(value)[:2000])

    def test_diff_run_tool_success_is_separate_from_qualification(self):
        from unittest import mock
        from automation import gfx_dual_run
        scenario = self.root / "scenario.json"
        scenario.write_text(json.dumps({
            "schema": "gfx-dual-scenario/v1",
            "executable_sha256": json.loads(
                (ROOT / "reverse_engineering" / "target.json").read_text())["sha256"],
            "logical_extent": [64, 64],
        }))
        result = {
            "schema": "gfx-dual-run/v1", "result": "completed",
            "run_dir": str(self.run_fixture()),
            "frames": {"paired": 100, "comparison": {"compared": 0}},
            "stop": {"reason": "frame_limit"},
            "qualification": {"status": "incomplete", "qualified": False},
        }
        scenario_value = {
            "observation": {"max_frames": 100},
            "comparison": {"policy": "tolerant"},
        }
        with mock.patch.object(gfx_dual_run, "run_campaign", return_value=result), \
             mock.patch.object(gfx_dual_run.gfx_scenario, "load",
                               return_value=scenario_value):
            code, value = self.call([
                "eu4_diff_run", "--scenario", str(scenario),
                "--run-root", str(self.root), "--ledger",
                str(self.root / "missing.json")])
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertEqual(value["result"]["classification"], "incomplete")
        self.assertFalse(value["result"]["qualification"]["qualified"])

    def test_independent_replay_invalid_checkpoint_is_not_ok(self):
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        report = {
            "status": "invalid",
            "outcome": {"execution": "ok", "coverage": "complete",
                        "pixels": "match", "semantic": "match",
                        "checkpoints": "invalid", "qualified": False},
            "checkpoint_validation": {
                "status": "invalid",
                "problems": [{"kind": "missing_result"}],
            },
            "checkpoints": [], "graph": {"status": "complete"},
            "evaluation": {"status": "complete", "reasons": []},
            "binding_audit": {"ok": True}, "final": {"status": "match"},
            "first_divergence": {"kind": "invalid_checkpoint"},
        }
        args = SimpleNamespace(
            replay_op="independent", rationale=RATIONALE, frame="frame", replay_out=str(self.root / "out"),
            shaders=None, disable_render_target_vflip=False,
            sampler_shift=None, sampler_shift_min=7,
        )
        with mock.patch.object(gfx_independent_replay, "replay",
                               return_value=report):
            value = agent_tools.eu4_replay(args)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["result"]["checkpoint_validation"]["status"],
                         "invalid")

    def test_an_expenseive_replay_without_a_rationale_is_refused(self):
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        args = SimpleNamespace(
            replay_op="independent", frame="frame",
            replay_out=str(self.root / "out"), shaders=None,
            disable_render_target_vflip=False, sampler_shift=None,
            sampler_shift_min=7, rationale=None,
        )
        with mock.patch.object(
                gfx_independent_replay, "replay",
                side_effect=AssertionError("the replay must not run")):
            value = agent_tools.eu4_replay(args)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "refused")
        self.assertIn("rationale", value["reason"])

    def independent_replay_report(self, classification, **overrides):
        frame = {
            "capture_frame_id": 1, "present_index": 616, "scene_index": 1,
            "events": 325, "sealed": True,
        }
        report = {
            "schema": "gfx-independent-replay/v1",
            "out_dir": str(self.root / "out"),
            "classification": classification,
            "qualification": {
                "schema": "m7-qualification-contract/v1",
                "axes": {
                    "capture_validity": "valid",
                    "frame_shader_identity": "verified",
                    "gpu_execution": "ok",
                    "resource_provenance": "verified",
                    "operation_coverage": "complete",
                    "rendering_state_coverage": "complete",
                    "pixel_comparison": (
                        "match" if classification == "qualified_match"
                        else "divergent"),
                    "first_divergence_confidence": (
                        "none" if classification == "qualified_match"
                        else "confirmed"),
                },
                "classification": classification,
                "qualified": classification == "qualified_match",
                "renderer_equivalence": classification == "qualified_match",
            },
            "evaluation": {"status": "complete", "reasons": []},
            "checkpoints": [{
                "event": 14, "target": "0", "resource": "fb:0",
                "status": "match", "authoritative": True,
                "statistics": {"mismatched_pixels": 0,
                               "max_channel_error": 0},
            }, {
                "event": 15, "target": "0", "resource": "fb:0",
                "status": "divergent", "authoritative": True,
                "statistics": {"mismatched_pixels": 607,
                               "max_channel_error": 2},
            }],
            "checkpoint_validation": {
                "status": "ok", "requested": 2, "returned": 2,
                "matched": 1, "diagnostic": 0, "divergent": 1, "invalid": 0,
                "problems": [],
            },
            "state_coverage": {"status": "complete"},
            "provenance": {"status": "verified"},
            "operation_coverage": {"status": "complete"},
            "binding_audit": {"ok": True},
            "final": {
                "status": ("match" if classification == "qualified_match"
                           else "divergent"),
                "exact": classification == "qualified_match",
                "statistics": {"pixels": 1930176,
                               "mismatched_pixels": 0
                               if classification == "qualified_match" else 607,
                               "max_channel_error": 0
                               if classification == "qualified_match" else 2,
                               "alpha_different_pixels": 0},
            },
            "first_divergence": ({"kind": "none", "confidence": "none"}
                                 if classification == "qualified_match"
                                 else {
                                     "kind": "intermediate",
                                     "event": 15, "target": "0",
                                     "resource": "fb:0", "operation": "draw",
                                     "program": "301", "producer_event": 14,
                                     "confidence": "confirmed",
                                     "root_cause": "not_established",
                                     "reason": "checkpoint 15 differs",
                                 }),
            "frame": frame,
            "artifacts": {
                "replay_json": "replay.json",
                "producer_receipt": "producer-receipt.json",
            },
        }
        report.update(overrides)
        return report

    def test_completed_divergent_replay_is_not_a_tool_failure(self):
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        args = SimpleNamespace(
            replay_op="independent", rationale=RATIONALE, frame="frame",
            replay_out=str(self.root / "out"), shaders=None,
            disable_render_target_vflip=False, sampler_shift=None,
            sampler_shift_min=7,
        )
        with mock.patch.object(
                gfx_independent_replay, "replay",
                return_value=self.independent_replay_report(
                    "qualified_difference")):
            value = agent_tools.eu4_replay(args)
        self.assertTrue(value["ok"])
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertTrue(value["outcome"]["diagnostic_complete"])
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertEqual(value["outcome"]["evidence"], "valid")
        self.assertIsNone(value["failure"]["kind"])
        self.assertEqual(value["failure"]["class"], "comparison")
        self.assertEqual(value["result"]["first_divergence"]["event"], 15)
        self.assertLessEqual(value["result"]["output_bytes"], 8192)
        self.assertEqual(value["result"]["report"],
                         str(self.root / "out" / "replay-report.json"))

    def test_unverified_identity_replay_is_incomplete_not_qualified(self):
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        report = self.independent_replay_report("qualified_match")
        report["qualification"] = dict(
            report["qualification"],
            axes=dict(report["qualification"]["axes"],
                      frame_shader_identity="unverified_session"),
            qualified=False, renderer_equivalence=False)
        report["classification"] = "incomplete_diagnostics"
        report["qualification"]["classification"] = "incomplete_diagnostics"
        args = SimpleNamespace(
            replay_op="independent", rationale=RATIONALE, frame="frame",
            replay_out=str(self.root / "out"), shaders=None,
            disable_render_target_vflip=False, sampler_shift=None,
            sampler_shift_min=7,
        )
        with mock.patch.object(gfx_independent_replay, "replay",
                               return_value=report):
            value = agent_tools.eu4_replay(args)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")
        self.assertEqual(value["outcome"]["evidence"], "unverified")
        self.assertFalse(value["outcome"]["qualified"])

    def test_adversarial_replay_envelope_stays_within_the_cap(self):
        """Hundreds of Unicode reasons and metadata cannot exceed the cap."""
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        reasons = [
            f"evaluación {index}: motivo repetido Δειγμα-😀 "
            + "x" * 60
            for index in range(400)
        ]
        report = self.independent_replay_report("qualified_difference")
        report["evaluation"] = {"status": "complete", "reasons": reasons}
        report["first_divergence"]["limitations"] = list(reasons)
        report["first_divergence"]["reason"] = "y" * 5000
        report["first_divergence"]["root_cause"] = "z" * 20000
        report["first_divergence"]["next_checkpoint"] = {
            "event": 1, "reason": "z" * 30000}
        args = SimpleNamespace(
            replay_op="independent", rationale=RATIONALE, frame="frame",
            replay_out=str(self.root / "out"), shaders=None,
            disable_render_target_vflip=False, sampler_shift=None,
            sampler_shift_min=7,
        )
        for count in (0, 10, 37, 60, 200):
            report["artifacts"] = {
                f"artifact-{index}": f"path/{index}/" + "z" * 80
                for index in range(count)
            }
            with mock.patch.object(gfx_independent_replay, "replay",
                                   return_value=report):
                value = agent_tools.eu4_replay(args)
            size = agent_tools._utf8_len(value)
            self.assertLessEqual(size, agent_tools.COMPACT_OUTPUT_CAP, count)
            self.assertEqual(value["result"]["output_bytes"], size, count)
            self.assertEqual(value["result"]["report"],
                             str(self.root / "out" / "replay-report.json"))
            self.assertEqual(value["result"]["first_divergence"]["event"], 15)
            self.assertEqual(value["result"]["classification"],
                             "qualified_difference")
            self.assertTrue(value["truncated"])
            self.assertLessEqual(len(value["reason"].encode("utf-8")), 8192)
            if isinstance(value.get("failure"), dict):
                self.assertLessEqual(
                    len(str(value["failure"].get("detail") or "").encode(
                        "utf-8")), 8192)

    def test_invalid_envelope_with_huge_failure_detail_stays_bounded(self):
        from types import SimpleNamespace
        from automation import gfx_independent_replay

        reasons = ["Δειγμα 😀 " * 200
                   for _ in range(300)]
        report = self.independent_replay_report("invalid_provenance")
        report["evaluation"] = {"status": "invalid", "reasons": reasons}
        args = SimpleNamespace(
            replay_op="independent", rationale=RATIONALE, frame="frame",
            replay_out=str(self.root / "out"), shaders=None,
            disable_render_target_vflip=False, sampler_shift=None,
            sampler_shift_min=7,
        )
        with mock.patch.object(gfx_independent_replay, "replay",
                               return_value=report):
            value = agent_tools.eu4_replay(args)
        size = agent_tools._utf8_len(value)
        self.assertLessEqual(size, agent_tools.COMPACT_OUTPUT_CAP)
        self.assertEqual(value["result"]["output_bytes"], size)
        self.assertEqual(value["result"]["report"],
                         str(self.root / "out" / "replay-report.json"))
        self.assertLessEqual(
            len(str(value["failure"].get("detail") or "").encode("utf-8")),
            agent_tools.COMPACT_OUTPUT_CAP)

    def test_pathological_report_locator_cannot_break_the_cap(self):
        # The report locator, frame record and arbitrary outcome metadata are
        # part of the envelope.  A caller-supplied multi-kilobyte locator must
        # be reduced instead of bypassing the hard cap.
        from automation import agent_tools as tools
        huge = "report/" + "z" * 20_000
        envelope = tools._envelope(
            "eu4_replay", "ok", {
                "classification": "qualified_difference",
                "qualified": False,
                "report": huge,
                "frame": {"path": huge},
                "first_divergence": {
                    "kind": "pixel", "event": 15, "target": "0",
                    "resource": "fb:0", "operation": "draw",
                    "next_checkpoint": huge,
                },
                "checkpoint_validation": {"status": "ok",
                                          "requested": huge},
            },
            diagnostic="complete", verdict="different", evidence="valid",
            qualified=False)
        envelope["outcome"]["note"] = huge
        envelope["failure"]["detail"] = huge
        bounded = tools._bound_replay_compact(envelope)
        size = tools._utf8_len(bounded)
        self.assertLessEqual(size, tools.COMPACT_OUTPUT_CAP)
        self.assertEqual(bounded["result"]["output_bytes"], size)
        self.assertTrue(bounded["result"]["report"].startswith("report/"))
        first = bounded["result"]["first_divergence"]
        self.assertEqual(first["event"], 15)
        self.assertEqual(first["kind"], "pixel")
        self.assertEqual(first["resource"], "fb:0")

    def test_nested_metadata_still_preserves_core_evidence(self):
        # A legal nested outcome value must not force the evidence-free stub.
        from automation import agent_tools as tools
        huge = "report/" + "z" * 20_000
        nested = {
            f"a{index}": {f"b{item}": "value-0123456789" for item in range(64)}
            for index in range(64)
        }
        envelope = tools._envelope(
            "eu4_replay", "ok", {
                "schema": "gfx-independent-replay/v1",
                "classification": "qualified_difference",
                "qualified": False,
                "report": huge,
                "first_divergence": {
                    "kind": "pixel", "event": 15, "target": "0",
                    "resource": "fb:0", "operation": "draw",
                    "next_checkpoint": {"event": 99},
                },
            },
            diagnostic="complete", verdict="different", evidence="valid",
            qualified=False)
        envelope["outcome"]["evidence"] = nested
        bounded = tools._bound_replay_compact(envelope)
        size = tools._utf8_len(bounded)
        self.assertLessEqual(size, tools.COMPACT_OUTPUT_CAP)
        self.assertIn("result", bounded)
        self.assertEqual(bounded["result"]["output_bytes"], size)
        self.assertTrue(bounded["result"]["report"].startswith("report/"))
        first = bounded["result"]["first_divergence"]
        self.assertEqual(first["event"], 15)
        self.assertIsInstance(first["event"], int)
        self.assertEqual(first["kind"], "pixel")
        self.assertIn("next_checkpoint", first)

    def test_compact_replay_view_is_bounded(self):
        from automation import agent_tools as tools
        report = self.independent_replay_report("qualified_difference")
        view = tools._replay_compact_view(
            report, str(self.root / "out" / "replay-report.json"))
        envelope = tools._envelope(
            "eu4_replay", "ok", view, diagnostic="complete",
            verdict="different", evidence="valid", qualified=False)
        bounded = tools._bound_replay_compact(envelope)
        self.assertLessEqual(tools._utf8_len(bounded), tools.COMPACT_OUTPUT_CAP)

    def test_diff_run_without_ledger_is_an_error_envelope(self):
        scenario = self.root / "scenario.json"
        scenario.write_text(json.dumps({
            "schema": "gfx-dual-scenario/v1",
            "executable_sha256": json.loads(
                (ROOT / "reverse_engineering" / "target.json").read_text())["sha256"],
            "logical_extent": [64, 64],
            "comparison": {"regions": {"all": {"rect": [0, 64, 0, 64]}}},
        }))
        from automation import gfx_dual_run, gfx_launch

        identity = {
            "executable_sha256": json.loads(
                (ROOT / "reverse_engineering" / "target.json").read_text())["sha256"],
            "executable": "/tmp/eu4-fixture",
        }
        user_data = self.root / "user"
        user_data.mkdir(exist_ok=True)
        with mock.patch.object(gfx_dual_run.base_benchmark, "gog_identity",
                               return_value=identity), \
             mock.patch.object(gfx_launch, "preflight_user_data",
                               return_value={"writable": True,
                                             "user_data": str(user_data)}):
            code, value = self.call([
                "eu4_diff_run", "--scenario", str(scenario),
                "--run-root", str(self.root),
                "--ledger", str(self.root / "missing.json"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertIn("LedgerError", value["result"].get("error", ""))

    def run_fixture(self) -> Path:
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")
        run = self.root / "case-run"
        (run / "reference").mkdir(parents=True, exist_ok=True)
        (run / "candidate").mkdir(parents=True, exist_ok=True)
        reference = np.full((6, 8, 4), 20, dtype=np.uint8)
        reference[0:2, 0:2, :3] = 200
        candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
        reference_path = run / "reference" / "frame-2.bgra"
        reference_path.write_bytes(reference[::-1, :, [2, 1, 0, 3]].tobytes())
        candidate_path = run / "candidate" / "frame-2.bgra"
        candidate_path.write_bytes(candidate.tobytes())
        reference_meta = {"backend": "opengl", "frame_index": 2, "width": 8,
                          "height": 6, "origin": "lower_left",
                          "pixel_format": "BGRA8", "raw_file": str(reference_path),
                          "sha256": "a" * 64, "caller_signature": "0xabc", "draws": 20}
        candidate_meta = {"backend": "metal", "frame_index": 2, "width": 16,
                          "height": 12, "origin": "top_left",
                          "pixel_format": "BGRA8", "raw_file": str(candidate_path),
                          "sha256": "b" * 64, "caller_signature": "0xabc", "draws": 20}
        pair = {"frame_index": 2, "candidate_frame_index": 2, "status": "aligned",
                "dumps": {"reference": {"found": {"2": reference_meta}},
                          "candidate": {"found": {"2": candidate_meta}}},
                "comparison": {"status": "fail", "reasons": ["pixel"],
                               "alignment": {"status": "aligned"},
                               "statistics": {}, "regions": {},
                               "mirrored_regions": []}}
        result = {
            "schema": "gfx-dual-run/v1",
            "session_id": "case-run",
            "dumps": {
                role: {"captured": [
                    {"frame_index": 2, "sha256": digest, "valid": True,
                     "preserved": True, "reasons": [],
                     "meta": {"schema": "gfx-frame-dump/v1", "frame_index": 2,
                              "session": "case-run", "width": 8, "height": 6,
                              "origin": "lower_left", "pixel_format": "BGRA8",
                              "raw_file": "captured/frame-2.bgra",
                              "raw_bytes": 8 * 6 * 4, "sha256": digest,
                              "capture_valid": True,
                              "gpu_completed": True}},
                    {"frame_index": 3, "sha256": digest, "valid": True,
                     "preserved": True, "reasons": [],
                     "meta": {"schema": "gfx-frame-dump/v1", "frame_index": 3,
                              "session": "case-run", "width": 8, "height": 6,
                              "origin": "lower_left", "pixel_format": "BGRA8",
                              "raw_file": "captured/frame-3.bgra",
                              "raw_bytes": 8 * 6 * 4, "sha256": digest,
                              "capture_valid": True,
                              "gpu_completed": True}},
                ], "missing": []}
                for role, digest in (("reference", "a" * 64),
                                     ("candidate", "b" * 64))
            },
            "scenario": {"name": "case", "digest": "d" * 64,
                         "comparison": {"policy": "tolerant",
                                        "max_channel_error": 8,
                                        "max_mismatched_fraction": 0.01,
                                        "max_mean_abs_error": 1.5, "regions": {}}},
            "health": {"verdict": "healthy", "first_failure": None,
                       "instrumentation_failures": []},
            "frames": {"comparison": {"status": "fail", "first_failure": pair},
                       "last_pair": pair},
            "stop": {"reason": "frame_difference"},
            "classification": "rendering_difference",
            "qualification": {"schema": "gfx-campaign-qualification/v1",
                              "status": "rendering_difference",
                              "qualified": False},
            "artifacts": {"bytes": 10, "max_bytes": 100, "dropped": 0},
            "processes": {}, "ledger": {}, "preflight": {}, "terminal": {},
        }
        (run / "result.json").write_text(json.dumps(result))
        (run / "session.json").write_text(json.dumps({"session_id": "case-run"}))
        return run


if __name__ == "__main__":
    unittest.main()


class DiagnosticSurfaceTests(ToolTests):
    """Phases 1.2-2.4 and 5: unambiguous, compact, provenance-aware tools."""

    def observation(self, index: int, *, backend: str = "opengl") -> dict:
        return {
            "schema": "gfx-frame-observe/v1", "backend": backend,
            "frame_index": index, "game_day": 56456976 + index,
            "update_identity": f"0x{index:016x}", "caller_signature": "0xabc",
            "draws": 370, "distinct_callers": 17, "width": 8, "height": 6,
            "origin": "top_left", "pixel_format": "RGBA8",
            "sha256": f"{index:064x}", "preview_sha256": f"{index + 500:064x}",
            "preview_file": f"previews/{index}.bgra", "capture_valid": True,
            "gpu_completed": True, "skipped_before": 0, "ts_ms": 1000 + index,
            "error": "",
        }

    def frames_run(self, root: Path) -> Path:
        run = root / "frames-run"
        for role, indices in (("reference", range(10, 16)),
                              ("candidate", range(10, 13))):
            observe = run / role / "observe"
            observe.mkdir(parents=True)
            (observe / "frame-observe.jsonl").write_text("\n".join(
                json.dumps(self.observation(index, backend=role))
                for index in indices) + "\n")
        return run

    def session_result(self, directory: Path, *, session_id: str,
                       classification: str = "incomplete",
                       health_verdict: str = "healthy",
                       comparison_status: str = "fail") -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        result = {
            "schema": "gfx-dual-run/v1",
            "session_id": session_id,
            "classification": classification,
            "scenario": {
                "comparison": {"policy": "tolerant", "max_channel_error": 8,
                               "max_mismatched_fraction": 0.01,
                               "max_mean_abs_error": 1.5, "regions": {}},
                "settle": {"stable_frames": 2},
                "observation": {"max_frames": 1},
            },
            "health": {"verdict": health_verdict, "first_failure": None,
                       "instrumentation_failures": []},
            "frames": {"comparison": {"status": comparison_status,
                                      "compared": 1, "failed": 1}},
            "qualification": {"status": classification, "qualified": False,
                              "requested_frames": 1},
            "stop": {"reason": "frame_difference"},
            "processes": {},
            "dumps": {},
            "stability": {"status": "weak"},
        }
        (directory / "result.json").write_text(json.dumps(result))
        (directory / "session.json").write_text(
            json.dumps({"session_id": session_id}))
        return directory

    def test_session_list_is_active_only_by_default_with_history_opt_in(self):
        with tempfile.TemporaryDirectory() as registry:
            root = Path(registry)
            with mock.patch.dict(os.environ, {"EU4_GFX_SESSION_ROOT": registry}):
                active = gfx_session.Session.create(
                    root / "runs", workflow_id="active", profile="early",
                    session_root_path=root)
                closed = gfx_session.Session.create(
                    root / "runs", workflow_id="closed", profile="early",
                    session_root_path=root)
                closed.close()
                stale = root / "session-20260101T000000Z-stale.json"
                stale.write_text(json.dumps({
                    "schema": "gfx-session-marker/v1",
                    "session_id": "20260101T000000Z-stale",
                    "run_dir": str(root / "missing-run"),
                    "workflow_id": "stale", "profile": "early",
                    "created_utc": "2026-01-01T00:00:00Z",
                    "status": "active",
                }))
                try:
                    code, value = self.call(["eu4_session", "list"])
                    self.assertEqual(code, 0)
                    listing = value["result"]
                    self.assertEqual(listing["filter"], "active")
                    self.assertEqual([row["session_id"] for row in listing["sessions"]],
                                     [active.session_id])
                    self.assertEqual(listing["counts"],
                                     {"active": 1, "closed": 1, "stale": 1,
                                      "total": 3})
                    row = listing["sessions"][0]
                    self.assertEqual(row["classification"], "active")
                    self.assertNotIn("processes", row)
                    self.assertNotIn("coordinator", row)
                    self.assertNotIn("ledger_tokens", row)
                    code, value = self.call([
                        "eu4_session", "list", "--include-history", "--limit", "2"])
                    history = value["result"]
                    self.assertEqual(history["filter"], "all")
                    self.assertEqual(history["returned"], 2)
                    self.assertTrue(history["truncated"])
                    self.assertEqual(history["next_offset"], 2)
                    code, value = self.call([
                        "eu4_session", "list", "--status", "stale"])
                    self.assertEqual(value["result"]["sessions"][0]["session_id"],
                                     "20260101T000000Z-stale")
                    self.assertEqual(
                        value["result"]["sessions"][0]["classification"], "stale")
                finally:
                    active.close()
                    closed.close()

    def test_capture_frames_exact_role_neighbour_and_pagination(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.frames_run(Path(temporary))
            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run), "--frame", "12"])
            self.assertEqual(code, 0)
            frames = value["result"]
            self.assertEqual(frames["count"], 2)
            self.assertEqual({row["frame_index"] for row in frames["frames"]}, {12})
            self.assertEqual(frames["matched"], 2)
            self.assertEqual(frames["missing_frames"], [])
            self.assertEqual(frames["available_by_role"],
                             {"reference": 6, "candidate": 3})
            self.assertIn("preview_sha256", frames["frames"][0])
            self.assertNotIn("preview_file", frames["frames"][0])
            self.assertNotIn("content", frames["frames"][0])

            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run), "--frame", "12",
                "--role", "reference"])
            self.assertEqual(len(value["result"]["frames"]), 1)
            self.assertEqual(value["result"]["frames"][0]["role"], "reference")

            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run), "--frame", "12",
                "--around", "1"])
            self.assertEqual(
                sorted((row["role"], row["frame_index"])
                       for row in value["result"]["frames"]),
                [("candidate", 11), ("candidate", 12),
                 ("reference", 11), ("reference", 12), ("reference", 13)])

            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run), "--frame", "12",
                "--around", "2", "--limit", "2"])
            self.assertEqual(code, 0)
            page = value["result"]
            self.assertEqual(page["returned"], 2)
            self.assertGreater(page["matched"], 2)
            self.assertTrue(page["truncated"])
            self.assertEqual(page["next_offset"], 2)
            self.assertGreater(page["omitted"], 0)

            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run), "--frame", "99"])
            self.assertEqual(code, 2)
            self.assertFalse(value["ok"])
            self.assertEqual(value["status"], "incomplete")
            self.assertEqual(value["result"]["missing_frames"], [99])
            self.assertEqual(value["outcome"]["diagnostic"], "incomplete")

            code, value = self.call([
                "eu4_capture", "frames", "--run", str(run),
                "--frame", "10", "--full"])
            self.assertEqual(code, 0)
            self.assertIn("preview_file", value["result"]["frames"][0])
            self.assertTrue(value["result"]["raw"])

    def test_capture_health_reconciles_persisted_and_fresh_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            healthy = self.session_result(root / "healthy", session_id="healthy",
                                          health_verdict="healthy")
            code, value = self.call(["eu4_capture", "health", "--run", str(healthy)])
            self.assertEqual(code, 0)
            health = value["result"]["health"]
            self.assertEqual(health["verdict"], "healthy")
            self.assertEqual(health["agreement"], "persisted_only")
            self.assertTrue(health["sources"]["persisted"]["available"])
            self.assertEqual(health["sources"]["scanned"]["summary"]["verdict"],
                             "unknown")
            self.assertIn("persisted", health["note"])
            self.assertEqual(value["outcome"]["evidence"], "valid")

            scanned = self.session_result(root / "scanned", session_id="scanned")
            result = json.loads((scanned / "result.json").read_text())
            result["health"] = {"verdict": "unknown", "first_failure": None,
                                "instrumentation_failures": []}
            (scanned / "result.json").write_text(json.dumps(result))
            (scanned / "draw-refused-1.json").write_text(json.dumps({
                "reason": "pipeline creation failed", "variant": "_GfxDraw",
                "caller_rva": "0x1234"}))
            code, value = self.call(["eu4_capture", "health", "--run", str(scanned)])
            health = value["result"]["health"]
            self.assertEqual(health["verdict"], "failed")
            self.assertEqual(health["agreement"], "scan_only")
            self.assertFalse(value["result"]["gate"]["allow"])
            self.assertEqual(value["outcome"]["evidence"], "invalid")

            unknown = root / "unknown"
            unknown.mkdir()
            code, value = self.call(["eu4_capture", "health", "--run", str(unknown)])
            health = value["result"]["health"]
            self.assertEqual(health["verdict"], "unknown")
            self.assertIn("unknown, not healthy", health["note"])
            self.assertEqual(value["outcome"]["verdict"], "unknown")

    def test_diff_inspect_campaign_root_lists_sessions_without_selecting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            self.session_result(root / "session-a", session_id="a",
                                classification="rendering_difference")
            self.session_result(root / "session-b", session_id="b",
                                classification="alignment_failure",
                                comparison_status="not_compared")
            code, value = self.call(["eu4_diff_inspect", "--run", str(root)])
            self.assertEqual(code, 0)
            listing = value["result"]
            self.assertGreaterEqual(listing["counts"]["sessions"], 2)
            identifiers = {row["session_id"] for row in listing["sessions"]}
            self.assertEqual(identifiers, {"a", "b"})
            self.assertIsNone(listing.get("selected_session"))
            self.assertIn("never auto-selected", listing["next"])
            self.assertTrue(all(len(row["result_sha256"]) == 64
                                for row in listing["sessions"]))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(root / "session-a")])
            self.assertEqual(code, 0)
            self.assertEqual(value["result"]["classification"], "incomplete")
            self.assertEqual(value["result"]["recorded_classification"],
                             "rendering_difference")
            self.assertTrue(value["result"]["classification_change"])
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(Path(temporary) / "missing")])
            self.assertEqual(code, 2)
            self.assertFalse(value["ok"])
            self.assertEqual(value["status"], "not_found")

    def test_diff_inspect_temporal_recovers_canary_findings(self):
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "session-temporal"
            run.mkdir()
            base = np.full((6, 8, 4), 20, dtype=np.uint8)
            base[:, :, 3] = 255
            ref75 = base.copy()
            for column in range(4):
                ref75[0, column, 0] = 200
            for column in range(4):
                ref75[5, column, 0] = 200
            for column in range(4):
                ref75[2, column, 1] = 200
            cand74 = base.copy()
            for column in range(4):
                cand74[0, column, 0] = 160
            cand75 = cand74.copy()
            for column in range(4):
                cand75[1, column, 1] = 200
            captured = {}
            for role, indices in (("reference", (74, 75)),
                                  ("candidate", (74, 75))):
                directory = run / role / "observe" / "captured"
                directory.mkdir(parents=True)
                images = {("reference", 74): base, ("reference", 75): ref75,
                          ("candidate", 74): cand74, ("candidate", 75): cand75}
                metas = []
                found = {}
                for index in indices:
                    path = directory / f"frame-{index}.bgra"
                    payload = images[(role, index)].tobytes()
                    path.write_bytes(payload)
                    digest = hashlib.sha256(payload).hexdigest()
                    meta = {
                        "schema": "gfx-frame-dump/v1", "frame_index": index,
                        "raw_file": str(path), "width": 8, "height": 6,
                        "origin": "top_left", "pixel_format": "RGBA8",
                        "sha256": digest,
                        "caller_signature": "0xabc", "draws": 2,
                        "game_day": 56456976,
                        "update_identity": "0xd09cad25b3166452",
                    }
                    metas.append({"frame_index": index, "meta": meta,
                                  "sha256": meta["sha256"]})
                    found[str(index)] = meta
                captured[role] = {"captured": metas, "found": found}
            pair = {
                "frame_index": 74, "candidate_frame_index": 74,
                "status": "aligned",
                "reference": {"game_day": 56456976, "draws": 2,
                              "caller_signature": "0xabc",
                              "update_identity": "0xd09cad25b3166452"},
                "candidate": {"game_day": 56456976, "draws": 2,
                              "caller_signature": "0xabc",
                              "update_identity": "0xd09cad25b3166452"},
                "dumps": {
                    "reference": {"found": {"74": found["74"]}},
                    "candidate": {"found": {"74": found["74"]}},
                },
                "comparison": {
                    "status": "fail", "reasons": ["max_channel_error 200 > 8"],
                    "alignment": {"status": "aligned",
                                  "evidence": ["game_update",
                                               "caller_signature"]},
                    "statistics": {"pixels": 48, "mismatched_pixels": 4,
                                   "mismatched_fraction": 0.083333,
                                   "max_channel_error": 140,
                                   "mean_abs_error": 2.0,
                                   "difference_bbox": [0, 1, 0, 4]},
                    "regions": {}, "mirrored_regions": [],
                },
            }
            result = {
                "schema": "gfx-dual-run/v1", "session_id": "temporal",
                "classification": "rendering_difference",
                "scenario": {
                    "comparison": {"policy": "tolerant", "max_channel_error": 8,
                                   "max_mismatched_fraction": 0.01,
                                   "max_mean_abs_error": 1.5, "regions": {}},
                    "settle": {"stable_frames": 2},
                    "observation": {"max_frames": 1},
                },
                "health": {"verdict": "healthy", "first_failure": None,
                           "instrumentation_failures": []},
                "frames": {"comparison": {"status": "fail", "compared": 1,
                                          "failed": 1, "first_failure": pair},
                           "last_pair": pair},
                "qualification": {"status": "rendering_difference",
                                  "qualified": False, "requested_frames": 1},
                "stop": {"reason": "frame_difference"},
                "processes": {}, "dumps": captured,
                "stability": {"status": "weak"},
            }
            (run / "result.json").write_text(json.dumps(result))
            (run / "session.json").write_text(json.dumps({"session_id": "temporal"}))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "74", "--candidate-frame", "74"])
            self.assertEqual(code, 0)
            payload = value["result"]
            self.assertEqual(payload["classification"], "incomplete")
            self.assertFalse(payload["staticity"]["verified"])
            temporal = payload["temporal"]
            self.assertEqual(temporal["status"], "completed")
            relations = [row["relation"] for row in temporal["pairs"]]
            self.assertEqual(relations, ["reference_temporal",
                                         "candidate_temporal", "cross_process"])
            by_relation = {row["relation"]: row for row in temporal["pairs"]}
            self.assertGreater(
                by_relation["reference_temporal"]["statistics"][
                    "mismatched_fraction"],
                by_relation["cross_process"]["statistics"][
                    "mismatched_fraction"])
            reference_overlap = next(
                row for row in temporal["overlap"]["temporal"]
                if row["relation"] == "reference_temporal")
            self.assertEqual(reference_overlap["cross_also_temporal_fraction"],
                             1.0)
            self.assertTrue(any("staticity" in reason
                                for reason in payload["missing_evidence"]))
            self.assertIn("temporal", payload["next_steps"][0])
            self.assertEqual(value["outcome"]["diagnostic"], "complete")
            self.assertEqual(value["outcome"]["verdict"], "different")
            self.assertFalse(value["outcome"]["qualified"])

    def test_replay_missing_numpy_is_unavailable_and_not_a_success(self):
        run = self.run_fixture()
        with mock.patch.object(agent_tools.gfx_frame_compare, "np", None):
            code, value = self.call(["eu4_replay", "case", "--case", str(run)])
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(value["outcome"]["diagnostic"], "unavailable")
        self.assertFalse(value["outcome"]["diagnostic_complete"])
        self.assertIn("numpy", value["reason"])
        self.assertIn("interpreter", value["reason"])
        self.assertFalse(value["runtime"]["numpy"]["available"])

    def test_replay_case_with_a_verdict_is_a_completed_diagnostic(self):
        run = self.run_fixture()
        case = agent_tools.gfx_case.build_case(run)["case_dir"]
        code, value = self.call(["eu4_replay", "case", "--case", case])
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertIn(value["outcome"]["verdict"],
                      ("equal", "different", "unverified"))
        from automation import python_runtime
        self.assertEqual(value["runtime"]["executable"],
                         python_runtime.absolute(sys.executable))
        self.assertTrue(value["runtime"]["numpy"]["available"])

    def test_replay_does_not_mutate_the_case_directory(self):
        run = self.run_fixture()
        case = Path(agent_tools.gfx_case.build_case(run)["case_dir"])
        comparison = case / "repro" / "comparison.json"
        if comparison.is_file():
            comparison.unlink()
        code, value = self.call(["eu4_replay", "case", "--case", str(case)])
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertFalse(comparison.exists(),
                         "agent replay must not write into historical evidence")

    def temporal_session(self, root: Path, *, reference=(10, 11),
                         candidate=(10, 11), total=30) -> Path:
        import hashlib
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")
        run = self.session_result(root / "temporal-session",
                                  session_id="temporal-session")
        result = json.loads((run / "result.json").read_text())
        result["scenario"]["comparison"]["policy"] = "tolerant"
        result["scenario"]["comparison"]["max_channel_error"] = 8
        result["scenario"]["comparison"]["allow_scale_normalization"] = True
        (run / "result.json").write_text(json.dumps(result))
        for role, selected in (("reference", reference), ("candidate", candidate)):
            directory = run / role / "observe" / "captured"
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(1, total + 1):
                image = np.full((6, 8, 4), 20 + index, dtype=np.uint8)
                image[:, :, 3] = 255
                if index in selected:
                    image[0, 0, 0] = 200
                payload = image.tobytes()
                path = directory / f"frame-{index}.bgra"
                path.write_bytes(payload)
                meta = {
                    "schema": "gfx-frame-dump/v1",
                    "frame_index": index,
                    "session": "temporal-session",
                    "backend": role,
                    "width": 8,
                    "height": 6,
                    "origin": "top_left",
                    "pixel_format": "RGBA8",
                    "raw_file": str(path),
                    "raw_bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "capture_valid": True,
                    "gpu_completed": True,
                    "draws": 20,
                    "caller_signature": "0xabc",
                    "game_day": 1,
                    "update_identity": "0x10",
                }
                (directory / f"frame-{index}.json").write_text(json.dumps(meta))
        return run

    def temporal_fixture(self, root: Path, *, preferred=(1, 1),
                         payload_indices=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
                         broken_indices=(), digest_mismatch_indices=(),
                         frame_count=12) -> Path:
        import hashlib
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")
        run = self.session_result(root / "fixture-session",
                                  session_id="fixture-session")
        result = json.loads((run / "result.json").read_text())
        result["scenario"]["comparison"]["policy"] = "tolerant"
        result["scenario"]["comparison"]["max_channel_error"] = 8
        pair = {
            "frame_index": preferred[0],
            "candidate_frame_index": preferred[1],
            "status": "aligned",
            "reference": {"draws": 20, "caller_signature": "0xabc",
                          "game_day": 1, "update_identity": "0x10"},
            "candidate": {"draws": 20, "caller_signature": "0xabc",
                          "game_day": 1, "update_identity": "0x10"},
            "dumps": {"reference": {"found": {}}, "candidate": {"found": {}}},
            "comparison": {"status": "fail", "reasons": ["pixel"],
                           "alignment": {"status": "aligned"},
                           "statistics": {}, "regions": {},
                           "mirrored_regions": []},
        }
        result["frames"]["comparison"]["first_failure"] = pair
        result["frames"]["last_pair"] = pair
        (run / "result.json").write_text(json.dumps(result))
        for role in ("reference", "candidate"):
            directory = run / role / "observe" / "captured"
            directory.mkdir(parents=True, exist_ok=True)
            for index in range(1, frame_count + 1):
                image = np.full((6, 8, 4), 20, dtype=np.uint8)
                image[:, :, 3] = 255
                payload = image.tobytes()
                path = directory / f"frame-{index}.bgra"
                if index in payload_indices:
                    path.write_bytes(payload)
                digest = hashlib.sha256(payload).hexdigest()
                if index in digest_mismatch_indices:
                    digest = "0" * 64
                meta = {
                    "schema": "gfx-frame-dump/v1", "frame_index": index,
                    "session": "fixture-session", "backend": role,
                    "width": 8, "height": 6,
                    "origin": ("sideways" if index in broken_indices
                               else "top_left"),
                    "pixel_format": "RGBA8", "raw_file": str(path),
                    "raw_bytes": len(payload), "sha256": digest,
                    "capture_valid": True, "gpu_completed": True,
                }
                (directory / f"frame-{index}.json").write_text(json.dumps(meta))
        return run

    def test_empty_roles_account_for_every_requested_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.session_result(Path(temporary) / "empty",
                                      session_id="empty")
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "10", "--candidate-frame", "10"])
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "incomplete")
        self.assertEqual(temporal["completion"]["requested"], 3)
        self.assertEqual(temporal["completion"]["completed"], 0)
        self.assertEqual(temporal["completion"]["unavailable"], 3)
        self.assertFalse(temporal["completion"]["cross_process_complete"])
        self.assertFalse(temporal["completion"]["temporal_variability_complete"])
        self.assertEqual({item["relation"] for item in temporal["comparisons"]},
                         {"reference_temporal", "candidate_temporal",
                          "cross_process"})

    def test_explicit_missing_index_accounts_for_all_three_comparisons(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(Path(temporary))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "99", "--candidate-frame", "10"])
        self.assertEqual(code, 2)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["selected"]["reference"], 99)
        self.assertEqual(temporal["completion"]["requested"], 3)
        self.assertEqual(temporal["completion"]["completed"], 1)
        self.assertEqual(temporal["completion"]["unavailable"], 2)
        self.assertIn(99, temporal["missing"]["requested_reference"])
        by_relation = {item["relation"]: item for item in temporal["comparisons"]}
        self.assertEqual(by_relation["candidate_temporal"]["status"],
                         "completed")
        self.assertEqual(by_relation["reference_temporal"]["status"],
                         "unavailable")
        self.assertEqual(by_relation["cross_process"]["status"], "unavailable")

    def test_auto_selection_falls_back_to_usable_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(1, 1),
                payload_indices=(10, 11))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal"])
        self.assertEqual(code, 0)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "completed")
        self.assertEqual(temporal["selected"]["reference"], 10)
        self.assertEqual(temporal["selected"]["candidate"], 10)
        self.assertTrue(temporal["fallback_used"])
        self.assertTrue(temporal["selected"]["fallback"]["reference"])
        self.assertEqual(temporal["completion"]["completed"], 3)
        self.assertFalse(value["result"]["qualification"]["qualified"])

    def test_explicit_metadata_without_payload_is_not_substituted(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(1, 1),
                payload_indices=(10, 11))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "1", "--candidate-frame", "1"])
        self.assertEqual(code, 2)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["selected"]["reference"], 1)
        self.assertFalse(temporal["selected"]["fallback"]["reference"])
        self.assertEqual(temporal["status"], "incomplete")
        self.assertIn(1, temporal["missing"]["reference"])

    def test_auto_selection_finds_window_after_many_unusable_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(1, 1), frame_count=30,
                payload_indices=(20, 21))
            real = agent_tools._decode_frame_meta
            decoded = []

            def recording(inner_run, role, index, meta):
                decoded.append((role, index))
                return real(inner_run, role, index, meta)

            with mock.patch.object(agent_tools, "_decode_frame_meta",
                                   side_effect=recording):
                code, value = self.call([
                    "eu4_diff_inspect", "--run", str(run), "--temporal"])
        self.assertEqual(code, 0)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "completed")
        self.assertEqual(temporal["selected"]["reference"], 20)
        self.assertEqual(temporal["selected"]["candidate"], 20)
        self.assertTrue(temporal["fallback_used"])
        self.assertEqual(temporal["completion"]["completed"], 3)
        self.assertFalse(temporal["selection_search_exhausted"])
        for role in ("reference", "candidate"):
            self.assertLessEqual(temporal["selected"]["probes"][role],
                                 agent_tools.TEMPORAL_MAX_SELECTION_PROBES)
            self.assertEqual(
                temporal["selected"]["candidates_considered"][role], 20)
        self.assertEqual({index for _role, index in decoded}, {20, 21})
        self.assertLessEqual(len(decoded),
                             2 * agent_tools.TEMPORAL_MAX_SELECTION_PROBES)

    def test_explicit_unusable_frame_remains_incomplete_with_large_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(1, 1), frame_count=30,
                payload_indices=(20, 21))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "1", "--candidate-frame", "1"])
        self.assertEqual(code, 2)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["selected"]["reference"], 1)
        self.assertEqual(temporal["selected"]["candidate"], 1)
        self.assertEqual(temporal["status"], "incomplete")
        self.assertFalse(temporal["selected"]["fallback"]["reference"])
        self.assertIn(1, temporal["missing"]["reference"])

    def test_auto_selection_skips_an_undecodable_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(10, 10),
                payload_indices=(10, 11, 12),
                broken_indices=(10,))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal"])
        self.assertEqual(code, 0)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "completed")
        self.assertEqual(temporal["selected"]["reference"], 11)
        self.assertTrue(temporal["fallback_used"])
        self.assertTrue(temporal["invalid"])
        self.assertIn("cannot be decoded",
                      " ".join(item["reason"] for item in temporal["invalid"]))

    def test_digest_mismatch_is_unavailable_not_completed(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_fixture(
                Path(temporary), preferred=(10, 10),
                payload_indices=(10, 11),
                digest_mismatch_indices=(10,))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "10", "--candidate-frame", "10"])
        self.assertEqual(code, 2)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "incomplete")
        self.assertEqual(temporal["pairs"], [])
        self.assertIn("digest does not match",
                      " ".join(item["reason"] for item in temporal["invalid"]))

    def test_enumeration_does_not_hash_unselected_payloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary) / "run"
            frames = run / "frames"
            frames.mkdir(parents=True)
            for index in range(1, 13):
                (frames / f"reference-frame-{index}.bgra").write_bytes(
                    bytes([index]) * (8 * 6 * 4))
            with mock.patch.object(
                    agent_tools, "_sha256_file",
                    side_effect=AssertionError("enumeration must not hash")):
                metas, _notes = agent_tools._frame_meta_entries(run, "reference")
        self.assertEqual(sorted(metas), list(range(1, 13)))

    def test_temporal_loading_decodes_only_the_selected_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_session(Path(temporary))
            real = agent_tools._decode_frame_meta
            seen = []

            def recording(inner_run, role, index, meta):
                seen.append((role, index))
                return real(inner_run, role, index, meta)

            with mock.patch.object(agent_tools, "_decode_frame_meta",
                                   side_effect=recording):
                code, value = self.call([
                    "eu4_diff_inspect", "--run", str(run), "--temporal",
                    "--reference-frame", "10", "--candidate-frame", "10",
                    "--temporal-window", "1"])
        self.assertEqual(code, 0)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "completed")
        self.assertEqual(temporal["completion"]["requested"], 3)
        self.assertEqual({role for role, _ in seen}, {"reference", "candidate"})
        self.assertEqual({index for role, index in seen if role == "reference"},
                         {10, 11})
        self.assertEqual({index for role, index in seen if role == "candidate"},
                         {10, 11})
        self.assertLess(len(seen), 30)

    def test_explicit_missing_temporal_index_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_session(Path(temporary))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "99", "--candidate-frame", "10"])
        self.assertEqual(code, 2)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "incomplete")
        self.assertEqual(temporal["selected"]["reference"], 99)
        self.assertIn(99, temporal["missing"]["requested_reference"])
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")

    def test_temporal_window_over_the_documented_bound_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_session(Path(temporary))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal",
                "--reference-frame", "10", "--candidate-frame", "10",
                "--temporal-window", "5"])
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "refused")
        self.assertFalse(value["ok"])
        self.assertIn("exceeds the documented bound", value["reason"])
        self.assertEqual(value["result"]["temporal"]["status"], "error")

    def test_automatic_temporal_selection_uses_a_full_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.temporal_session(Path(temporary))
            code, value = self.call([
                "eu4_diff_inspect", "--run", str(run), "--temporal"])
        self.assertEqual(code, 0)
        temporal = value["result"]["temporal"]
        self.assertEqual(temporal["status"], "completed")
        self.assertEqual(temporal["selected"]["reference"], 1)
        self.assertEqual(temporal["selected"]["candidate"], 1)
        self.assertEqual(len(temporal["pairs"]), 3)
        self.assertTrue(all(isinstance(pair.get("statistics"), dict)
                            for pair in temporal["pairs"]))

    def test_session_discover_uses_configured_evidence_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.session_result(root / "live-a6" / "session-a6", session_id="a6",
                                classification="rendering_difference")
            self.session_result(root / "live-a21" / "session-a21", session_id="a21",
                                classification="alignment_failure")
            code, value = self.call([
                "eu4_session", "discover", "--evidence-root", str(root),
                "--classification", "alignment_failure"])
            self.assertEqual(code, 0)
            result = value["result"]
            self.assertEqual([row["session_id"] for row in result["sessions"]],
                             ["a21"])
            self.assertTrue(result["read_only"])
            self.assertEqual(result["roots"], [str(root.resolve())])
            self.assertEqual(
                result["discovered"]["by_classification"]["rendering_difference"],
                1)
            self.assertTrue(result["sessions"][0]["provenance"]["result_sha256"])

    def test_health_interface_agrees_between_capture_and_diff_inspect(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = self.session_result(Path(temporary) / "consistent",
                                      session_id="consistent")
            _, capture = self.call(["eu4_capture", "health", "--run", str(run)])
            _, inspect = self.call(["eu4_diff_inspect", "--run", str(run)])
            self.assertEqual(capture["result"]["health"]["verdict"],
                             inspect["result"]["health"]["verdict"])
            self.assertEqual(capture["result"]["health"]["agreement"],
                             inspect["result"]["health"]["agreement"])
            self.assertEqual(capture["outcome"]["verdict"],
                             inspect["result"]["health"]["verdict"])

    def test_capture_health_on_a_campaign_root_lists_each_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "campaign"
            self.session_result(root / "session-a", session_id="a")
            self.session_result(root / "session-b", session_id="b")
            code, value = self.call(["eu4_capture", "health", "--run", str(root)])
            self.assertEqual(code, 0)
            result = value["result"]
            self.assertEqual(result["counts"]["sessions"], 2)
            self.assertEqual({row["session_id"] for row in result["sessions"]},
                             {"a", "b"})
            self.assertTrue(all(row["health"]["verdict"] == "healthy"
                                for row in result["sessions"]))


class StrategyBCaseToolTests(ToolTests):
    """The agent surface recognizes v1 and v2 Strategy B cases identically.

    A v2 ordered-prefix case must be discoverable, inspectable, verifiable and
    replayable.  Incomplete or invalid verification is never promoted to a
    qualified result, and a replay is never started for an unverified case.
    """

    def make_v2_case(self) -> Path:
        case = self.root / "v2-agent-case"
        case.mkdir(exist_ok=True)
        (case / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SEQUENCE_SCHEMA,
            "case_id": "v2-agent-case",
            "title": "synthetic ordered prefix",
            "sequence": "sequence.json",
            "reproduction": {
                "status": "complete", "independently_reproducible": True,
            },
            "replay_command": ["python3", "-m", "automation.gfx_replay_case",
                               "replay", "--case", ".", "--out", "rerun"],
            "verify_command": ["python3", "-m", "automation.gfx_replay_case",
                               "verify", "--case", "."],
            "files": [{"path": "sequence.json", "bytes": 1, "sha256": "0" * 64}],
        }))
        (case / "sequence.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case-sequence/v1",
            "session_id": "session-v2", "pid": 7, "target_index": 2,
            "frames": [
                {"index": 1, "dir": "frames/000001",
                 "capture_frame_id": 3, "original_sequence_index": 3,
                 "shader_dir": "frames/000001/shaders",
                 "checkpoint_count": 2, "graph_status": "ok"},
                {"index": 2, "dir": "frames/000002",
                 "capture_frame_id": 4, "original_sequence_index": 4,
                 "shader_dir": "frames/000002/shaders",
                 "checkpoint_count": 2, "graph_status": "ok"},
            ],
            "minimal_prefix": True, "excluded_frames": 2,
            "carried_resources": ["500"],
            "reproduction": {
                "status": "complete", "independently_reproducible": True,
            },
        }))
        (case / "diagnostic-report.json").write_text(json.dumps({
            "schema": "gfx-frame-diagnosis/v1",
            "classification": "qualified_match",
            "qualification": {
                "qualified": True, "diagnostic_qualified": True,
                "axes": {"capture_validity": "valid"},
            },
            "pixels": {"final": {"status": "match"}},
            "first_divergence": {"kind": "none"},
        }))
        (case / "graph.json").write_text(json.dumps({
            "schema": "gfx-frame-graph/v1", "status": "ok",
        }))
        return case

    def verification(self, *, ok=True, reproducible=True, problems=None,
                     reproduction_status=None, verifier_exception=False):
        if reproduction_status is None:
            reproduction_status = ("complete" if ok or reproducible
                                   else "incomplete")
        return {
            "schema": "gfx-strategy-b-case-verification/v1",
            "case_id": "v2-agent-case", "ok": ok,
            "problems": list(problems or []), "files": 5,
            "graph_status": "ok",
            "sequence": {"status": "verified" if ok else "invalid",
                         "frames": 2, "target_index": 2},
            "reproduction_status": reproduction_status,
            "verifier_exception": verifier_exception,
            "independently_reproducible": reproducible,
        }

    def test_v2_case_is_recognized_by_inspect_and_verify(self):
        case = self.make_v2_case()
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=self.verification()):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
            self.assertEqual(code, 0, value)
            self.assertEqual(value["status"], "ok")
            self.assertEqual(value["result"]["schema"],
                             gfx_replay_case.SEQUENCE_SCHEMA)
            self.assertEqual(len(value["result"]["sequence"]["frames"]), 2)
            self.assertFalse(value["outcome"]["qualified"])
            code, value = self.call(["eu4_replay", "verify",
                                     "--case", str(case)])
            self.assertEqual(code, 0, value)
            self.assertEqual(value["status"], "ok")
            self.assertEqual(value["result"]["verification"]["case_id"],
                             "v2-agent-case")
            self.assertEqual(value["result"]["execution"], "not_run")
            self.assertFalse(value["outcome"]["qualified"])

    def test_v2_case_verification_only_does_not_replay(self):
        case = self.make_v2_case()
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=self.verification()) as verify, \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case") as replay:
            code, value = self.call(["eu4_replay", "case",
                                     "--case", str(case)])
        self.assertEqual(code, 0, value)
        self.assertEqual(value["result"]["execution"], "not_run")
        self.assertEqual(value["result"]["next"],
                         "pass --replay-out to re-execute this Strategy B case")
        self.assertFalse(value["outcome"]["qualified"])
        verify.assert_called_once()
        replay.assert_not_called()

    def test_v2_replay_result_is_bounded_by_its_classification(self):
        case = self.make_v2_case()
        report = {
            "classification": "qualified_match",
            "qualification": {"qualified": True, "diagnostic_qualified": True},
            "campaign": {"status": "complete", "frames": 2},
            "reproduction": {"status": "complete",
                             "independently_reproducible": True},
            "pixels": {"final": {"status": "match"}},
            "first_divergence": {"kind": "none"},
        }
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=self.verification()), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case",
                               return_value=report):
            code, value = self.call([
                "eu4_replay", "case", "--case", str(case),
                "--replay-out", str(self.root / "v2-replay"),
            ])
        self.assertEqual(code, 0, value)
        self.assertEqual(value["outcome"]["verdict"], "equal")
        self.assertTrue(value["outcome"]["qualified"])
        self.assertEqual(value["result"]["schema"],
                         gfx_replay_case.SEQUENCE_SCHEMA)
        self.assertEqual(len(value["result"]["sequence"]["frames"]), 2)

    def test_v2_partial_difference_is_not_qualified(self):
        case = self.make_v2_case()
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "diagnostic_qualified": False},
            "campaign": {"status": "stopped"},
            "reproduction": {"status": "complete",
                             "independently_reproducible": True},
            "pixels": {"final": {"status": "divergent"}},
            "first_divergence": {"kind": "final_only"},
        }
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=self.verification()), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case",
                               return_value=report):
            code, value = self.call([
                "eu4_replay", "case", "--case", str(case),
                "--replay-out", str(self.root / "v2-partial"),
            ])
        self.assertEqual(code, 0, value)
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["outcome"]["diagnostic"], "partial")
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertFalse(value["outcome"]["qualified"])

    def test_v2_incomplete_or_invalid_evidence_cannot_qualify(self):
        case = self.make_v2_case()
        incomplete = self.verification(
            ok=False, reproducible=False,
            problems=["reproduction is explicitly incomplete"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=incomplete), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case") as replay:
            code, value = self.call(["eu4_replay", "case",
                                     "--case", str(case),
                                     "--replay-out", str(self.root / "v2-incomplete")])
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["evidence"], "unverified")
        self.assertFalse(value["outcome"]["qualified"])
        replay.assert_not_called()

        invalid = self.verification(
            ok=False, reproducible=True, problems=["digest drift"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=invalid), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case") as replay:
            code, value = self.call(["eu4_replay", "case",
                                     "--case", str(case),
                                     "--replay-out", str(self.root / "v2-invalid")])
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertFalse(value["outcome"]["qualified"])
        replay.assert_not_called()

    def test_bounded_m7_sequence_execution_is_exposed(self):
        from automation import m7_continuous

        frames = [self.root / "seq" / f"frame-{index:06d}"
                  for index in (1, 2, 3)]
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": True, "validation": {"status": "ok"},
            "frames": [{"sequence": index, "classification": "qualified_match",
                        "diagnostic_completed": True,
                        "diagnostic_report": f"f{index}.json"}
                       for index in (1, 2, 3)],
            "metrics": {"wall_seconds": 1.0},
        }
        with mock.patch.object(m7_continuous, "run_campaign",
                               return_value=report) as run:
            code, value = self.call([
                "eu4_replay", "sequence",
                *[token for frame in frames
                  for token in ("--frame-dir", str(frame))],
                "--shaders", str(self.root / "shaders"),
                "--replay-out", str(self.root / "sequence-out"),
                "--max-frames", "3", "--case-max-bytes", "4096",
                "--frame-timeout", "30", "--timeout", "60",
            ])
        self.assertEqual(code, 0, value)
        self.assertTrue(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertEqual(value["outcome"]["verdict"], "equal")
        self.assertTrue(value["outcome"]["qualified"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["max_frames"], 3)
        self.assertEqual(kwargs["case_max_bytes"], 4096)
        self.assertEqual(kwargs["frame_timeout"], 30.0)
        self.assertEqual(kwargs["timeout"], 60.0)
        self.assertEqual(value["result"]["status"], "complete")

    def test_sequence_diagnostic_completeness_requires_evidence(self):
        report = {
            "status": "failed", "stop_reason": "gpu_execution_failure",
            "stopped_at": 2, "qualified": False,
            "frames": [
                {"sequence": 1, "classification": "qualified_match",
                 "history_status": 1, "diagnostic_completed": True,
                 "diagnostic_report": "f1.json"},
                {"sequence": 2, "ok": False, "history_status": 0,
                 "classification": "gpu_execution_failure",
                 "diagnostic_report": None, "diagnostic_completed": False,
                 "diagnostic_evidence": {
                     "completed": False, "missing": ["diagnosis"]},
                 "error": "command buffer failed"},
            ],
        }
        code, value, _ = self.run_sequence(
            report, extra=("--continue-on-difference",), frame_count=2)
        self.assertEqual(code, 2, value)
        result = value["result"]
        self.assertFalse(result["diagnostic_complete"])
        # The earlier completed diagnosis is preserved even though the later
        # frame failed.
        self.assertTrue(result["frames"][0]["diagnostic_completed"])
        self.assertEqual(result["frames"][0]["diagnostic_report"], "f1.json")
        self.assertFalse(result["frames"][1]["diagnostic_completed"])
        self.assertIsNone(result["frames"][1]["diagnostic_report"])
        self.assertEqual(value["status"], "failed")

    def test_successful_sequence_reports_evidence_based_completion(self):
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": True,
            "frames": [
                {"sequence": index, "classification": "qualified_match",
                 "history_status": 1, "diagnostic_completed": True,
                 "diagnostic_report": f"f{index}.json"}
                for index in (1, 2, 3)
            ],
        }
        code, value, _ = self.run_sequence(report)
        self.assertEqual(code, 0, value)
        self.assertTrue(value["result"]["diagnostic_complete"])
        self.assertTrue(value["outcome"]["qualified"])
        self.assertEqual(value["outcome"]["verdict"], "equal")

    def test_pixel_divergence_is_separate_from_unsupported_state(self):
        report = {
            "status": "failed", "stop_reason": "gpu_execution_failure",
            "stopped_at": 3, "qualified": False,
            "frames": [
                {"sequence": 1, "classification": "unsupported_state",
                 "history_status": 1, "diagnostic_completed": True,
                 "axes": {"pixel_comparison": "match",
                          "rendering_state_coverage": "invalid"},
                 "pixels": {"pixels": 100, "mismatched_pixels": 0},
                 "checkpoint_validation": {"status": "ok", "divergent": 0},
                 "first_divergence": {"kind": "unsupported_state",
                                      "event": 5}},
                {"sequence": 2, "classification": "qualified_difference",
                 "history_status": 1, "diagnostic_completed": True,
                 "axes": {"pixel_comparison": "divergent"},
                 "pixels": {"pixels": 100, "mismatched_pixels": 7},
                 "checkpoint_validation": {"status": "ok", "divergent": 2},
                 "first_divergence": {"kind": "final_only", "event": 17,
                                      "operation": "draw"}},
                {"sequence": 3, "ok": False, "history_status": 0,
                 "classification": "gpu_execution_failure",
                 "diagnostic_completed": False,
                 "error": "command buffer failed"},
            ],
        }
        code, value, _ = self.run_sequence(
            report, extra=("--continue-on-difference",))
        self.assertEqual(code, 2, value)
        result = value["result"]
        self.assertEqual(result["first_pixel_divergent_frame"], 2)
        self.assertEqual(result["first_checkpoint_divergent_frame"], 2)
        self.assertEqual(result["first_unqualified_frame"], 1)
        self.assertEqual(result["terminal_failure"]["kind"],
                         "gpu_execution_failure")
        self.assertFalse(result["qualified"])
        # ``first_difference`` stays the earliest non-equivalence (the
        # unsupported-state frame) but must not claim a pixel difference.
        self.assertEqual(result["first_difference"]["frame"], 1)
        self.assertFalse(result["first_difference"]["pixel_divergent"])
        self.assertFalse(result["first_difference"]["checkpoint_divergent"])

    @staticmethod
    def trusted_frame(sequence, classification, *, pixels=0,
                      pixel_comparison="match", checkpoint_divergent=0,
                      ok=True, diagnostic_completed=True, **extra):
        value = {
            "sequence": sequence,
            "classification": classification,
            "history_status": 1,
            "ok": ok,
            "diagnostic_completed": diagnostic_completed,
            "axes": {
                "capture_validity": "valid",
                "gpu_execution": "ok",
                "resource_provenance": "verified",
                "pixel_comparison": pixel_comparison,
                "rendering_state_coverage": (
                    "partial" if classification != "qualified_match"
                    else "complete"),
            },
            "pixels": {"pixels": 100, "mismatched_pixels": pixels},
            "checkpoint_validation": {
                "status": "ok", "divergent": checkpoint_divergent},
            "first_divergence": {
                "kind": "final_only" if pixel_comparison == "divergent"
                else "none"},
        }
        value.update(extra)
        return value

    def test_later_verified_mismatch_sets_the_aggregate_verdict(self):
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": False,
            "frames": [
                self.trusted_frame(1, "unsupported_state"),
                self.trusted_frame(2, "qualified_difference", pixels=7,
                                   pixel_comparison="divergent",
                                   checkpoint_divergent=2,
                                   first_divergence={
                                       "kind": "final_only", "event": 17,
                                       "operation": "draw"}),
                self.trusted_frame(3, "qualified_match"),
            ],
        }
        code, value, _ = self.run_sequence(report)
        self.assertEqual(code, 0, value)
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertEqual(value["outcome"]["evidence"], "valid")
        self.assertFalse(value["outcome"]["qualified"])
        result = value["result"]
        self.assertEqual(result["first_pixel_divergent_frame"], 2)
        self.assertEqual(result["first_checkpoint_divergent_frame"], 2)
        self.assertEqual(result["first_unqualified_frame"], 1)
        self.assertIsNone(result["terminal_failure"])
        self.assertEqual(result["first_difference"]["frame"], 1)
        self.assertFalse(result["first_difference"]["pixel_divergent"])
        self.assertFalse(result["qualified"])

    def test_incomplete_qualified_difference_cannot_establish_difference(self):
        frame = self.trusted_frame(
            1, "qualified_difference", pixels=7,
            pixel_comparison="divergent", checkpoint_divergent=1)
        frame["diagnostic_completed"] = False
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": True, "frames": [frame],
        }
        code, value, _ = self.run_sequence(report, frame_count=1)
        self.assertEqual(code, 2, value)
        self.assertIsNone(value["outcome"]["verdict"])
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")
        result = value["result"]
        self.assertIsNone(result["first_pixel_divergent_frame"])
        self.assertIsNone(result["first_checkpoint_divergent_frame"])
        self.assertFalse(result["qualified"])
        self.assertFalse(result["first_difference"]["pixel_divergent"])

    def test_complete_qualified_difference_still_reports_different(self):
        from automation import gfx_independent_replay

        classification, qualification = (
            gfx_independent_replay.classify_diagnosis(
                capture_validity="valid", identity="verified",
                execution="ok", provenance="verified",
                operation_coverage="complete", state_cov="complete",
                pixels="divergent", divergence="final_only",
                checkpoint_validation={"status": "ok"}))
        self.assertEqual(classification, "qualified_difference")
        frame = self.trusted_frame(
            1, classification, pixels=9, pixel_comparison="divergent")
        frame["axes"] = qualification["axes"]
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": False, "frames": [frame],
        }
        code, value, _ = self.run_sequence(report, frame_count=1)
        self.assertEqual(code, 0, value)
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertFalse(value["outcome"]["qualified"])
        self.assertEqual(
            value["result"]["first_pixel_divergent_frame"], 1)

    def test_later_terminal_failure_overrides_but_keeps_observations(self):
        report = {
            "status": "failed", "stop_reason": "gpu_execution_failure",
            "stopped_at": 4, "qualified": False,
            "frames": [
                self.trusted_frame(1, "unsupported_state"),
                self.trusted_frame(2, "qualified_difference", pixels=7,
                                   pixel_comparison="divergent",
                                   checkpoint_divergent=1),
                self.trusted_frame(3, "qualified_match"),
                self.trusted_frame(4, "gpu_execution_failure", ok=False,
                                   diagnostic_completed=False,
                                   error="command buffer failed"),
            ],
        }
        code, value, _ = self.run_sequence(report, frame_count=4)
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["outcome"]["diagnostic"], "failed")
        self.assertIsNone(value["outcome"]["verdict"])
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertFalse(value["outcome"]["qualified"])
        result = value["result"]
        self.assertEqual(result["first_pixel_divergent_frame"], 2)
        self.assertEqual(result["first_checkpoint_divergent_frame"], 2)
        self.assertEqual(result["first_unqualified_frame"], 1)
        self.assertEqual(result["terminal_failure"]["kind"],
                         "gpu_execution_failure")
        self.assertEqual(result["first_difference"]["frame"], 1)

    def test_invalid_provenance_pixels_are_not_promoted(self):
        report = {
            "status": "stopped", "stop_reason": "invalid_provenance",
            "stopped_at": 1, "qualified": False,
            "frames": [self.trusted_frame(
                1, "invalid_provenance", pixels=5,
                pixel_comparison="divergent")],
        }
        report["frames"][0]["axes"]["capture_validity"] = "invalid"
        code, value, _ = self.run_sequence(report, frame_count=1)
        self.assertEqual(code, 2, value)
        result = value["result"]
        self.assertIsNone(result["first_pixel_divergent_frame"])
        self.assertIsNone(result["first_checkpoint_divergent_frame"])
        self.assertEqual(result["terminal_failure"]["kind"],
                         "invalid_provenance")

    def run_sequence(self, report, *, extra=(), frame_count=3):
        from automation import m7_continuous

        frames = [self.root / "seq" / f"frame-{index:06d}"
                  for index in range(1, frame_count + 1)]
        with mock.patch.object(m7_continuous, "run_campaign",
                               return_value=report) as run:
            code, value = self.call([
                "eu4_replay", "sequence",
                *[token for frame in frames
                  for token in ("--frame-dir", str(frame))],
                "--shaders", str(self.root / "shaders"),
                "--replay-out", str(self.root / "sequence-out"),
                *extra,
            ])
        return code, value, run

    def test_terminal_gpu_failure_outranks_an_earlier_difference(self):
        report = {
            "status": "stopped", "stop_reason": "gpu_execution_failure",
            "stopped_at": 3, "qualified": False,
            "frames": [
                {"sequence": 1, "classification": "qualified_match",
                 "history_status": 1, "diagnostic_report": "f1.json"},
                {"sequence": 2, "classification": "qualified_difference",
                 "history_status": 1, "diagnostic_report": "f2.json",
                 "first_divergence": {"event": 17, "operation": "draw"},
                 "pixels": {"mismatched_pixels": 4}},
                {"sequence": 3, "ok": False, "history_status": 0,
                 "classification": "gpu_execution_failure",
                 "error": "command buffer failed"},
            ],
        }
        code, value, _ = self.run_sequence(
            report, extra=("--continue-on-difference",))
        self.assertEqual(code, 2, value)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["outcome"]["diagnostic"], "failed")
        self.assertIsNone(value["outcome"]["verdict"])
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertFalse(value["outcome"]["qualified"])
        self.assertEqual(value["result"]["campaign_execution"], "failed")
        self.assertEqual(value["result"]["terminal_failure"]["kind"],
                         "gpu_execution_failure")
        self.assertEqual(value["result"]["first_difference"]["frame"], 2)
        self.assertEqual(
            value["result"]["frames"][1]["classification"],
            "qualified_difference")
        self.assertFalse(value["result"]["qualified"])

    def test_sequence_forwards_the_selected_policy(self):
        policy = self.root / "custom-policy.json"
        policy.write_text(json.dumps({
            "schema": "gfx-frame-comparison-policy/v1",
            "name": "custom", "thresholds": {"max_channel_error": 0},
        }))
        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": True, "frames": [{
                "sequence": 1, "classification": "qualified_match",
                "history_status": 1, "diagnostic_report": "f1.json",
                "diagnostic_completed": True,
            }],
            "policy": {"path": str(policy), "sha256": "a" * 64,
                       "source": "explicit"},
        }
        code, value, run = self.run_sequence(
            report, extra=("--policy", str(policy)), frame_count=1)
        self.assertEqual(code, 0, value)
        self.assertEqual(run.call_args.kwargs["policy"], str(policy))
        self.assertEqual(value["result"]["policy"]["source"], "explicit")
        self.assertEqual(value["result"]["policy"]["sha256"], "a" * 64)

    def test_unavailable_or_invalid_policy_is_not_silently_defaulted(self):
        from automation import m7_continuous

        frames = [self.root / "seq" / "frame-000001"]
        with mock.patch.object(
                m7_continuous, "run_campaign",
                side_effect=m7_continuous.ContinuousError(
                    "policy_unavailable", "missing policy")):
            code, value = self.call([
                "eu4_replay", "sequence",
                "--frame-dir", str(frames[0]),
                "--shaders", str(self.root / "shaders"),
                "--replay-out", str(self.root / "sequence-out"),
                "--policy", str(self.root / "missing.json"),
            ])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "refused")
        self.assertEqual(value["outcome"]["diagnostic"], "refused")
        self.assertFalse(value["outcome"]["qualified"])
        self.assertIn("policy_unavailable", value["reason"])

    def test_replay_output_aliases_are_not_envelope_destinations(self):
        case = self.make_v2_case()
        report = {
            "classification": "qualified_match",
            "qualification": {"qualified": True},
            "campaign": {"status": "complete"},
            "reproduction": {"status": "complete",
                             "independently_reproducible": True},
            "pixels": {"final": {"status": "match"}},
            "first_divergence": {"kind": "none"},
        }
        for alias in ("--replay-out", "--out"):
            with self.subTest(alias=alias):
                out = self.root / f"alias-{alias.lstrip('-')}"
                with mock.patch.object(
                        agent_tools.gfx_replay_case, "verify",
                        return_value=self.verification()), \
                     mock.patch.object(
                        agent_tools.gfx_replay_case, "replay_case",
                        return_value=report) as replay:
                    code, value = self.call([
                        "eu4_replay", "case", "--case", str(case),
                        alias, str(out),
                    ])
                self.assertEqual(code, 0, value)
                self.assertEqual(value["schema"], agent_tools.SCHEMA)
                self.assertEqual(value["tool"], "eu4_replay")
                self.assertEqual(replay.call_args.args[1], out)
                self.assertFalse(out.exists())

    def test_envelope_destination_is_explicit_and_separate(self):
        case = self.make_v2_case()
        replay_out = self.root / "explicit-replay"
        envelope = self.root / "explicit-envelope.json"
        report = {
            "classification": "qualified_match",
            "qualification": {"qualified": True},
            "campaign": {"status": "complete"},
            "reproduction": {"status": "complete",
                             "independently_reproducible": True},
            "pixels": {"final": {"status": "match"}},
        }
        with mock.patch.object(
                agent_tools.gfx_replay_case, "verify",
                return_value=self.verification()), \
             mock.patch.object(
                agent_tools.gfx_replay_case, "replay_case",
                return_value=report) as replay, \
             contextlib.redirect_stdout(io.StringIO()):
            code = agent_tools.main([
                "eu4_replay", "case", "--case", str(case),
                "--out", str(replay_out),
                "--envelope-out", str(envelope),
                "--rationale", RATIONALE,
            ])
        self.assertEqual(code, 0)
        self.assertEqual(replay.call_args.args[1], replay_out)
        value = json.loads(envelope.read_text())
        self.assertEqual(value["schema"], agent_tools.SCHEMA)
        self.assertEqual(value["tool"], "eu4_replay")
        self.assertFalse(envelope.is_dir())

    def test_sequence_and_diagnose_accept_both_output_aliases(self):
        from automation import gfx_independent_replay, m7_continuous

        report = {
            "status": "complete", "stop_reason": "frame_limit",
            "qualified": True, "frames": [{
                "sequence": 1, "classification": "qualified_match",
                "history_status": 1, "diagnostic_report": "f1.json",
                "diagnostic_completed": True,
            }],
        }
        for alias in ("--replay-out", "--out"):
            with self.subTest(op="sequence", alias=alias):
                out = self.root / f"seq-{alias.lstrip('-')}"
                with mock.patch.object(m7_continuous, "run_campaign",
                                       return_value=report) as run:
                    code, value = self.call([
                        "eu4_replay", "sequence",
                        "--frame-dir", str(self.root / "frame"),
                        "--shaders", str(self.root / "shaders"),
                        alias, str(out), "--max-frames", "1",
                    ])
                self.assertEqual(code, 0, value)
                self.assertEqual(run.call_args.args[1], out)
        for alias in ("--replay-out", "--out"):
            with self.subTest(op="diagnose", alias=alias):
                out = self.root / f"diag-{alias.lstrip('-')}"
                diagnosis = {
                    "schema": "gfx-frame-diagnosis/v1",
                    "classification": "qualified_match",
                    "qualification": {"qualified": True},
                    "checkpoint_validation": {"status": "ok"},
                }
                with mock.patch.object(
                        gfx_independent_replay, "diagnose_frame",
                        return_value=diagnosis) as diagnose:
                    code, value = self.call([
                        "eu4_replay", "diagnose",
                        "--frame", str(self.root / "frame"),
                        "--metal", str(self.root / "metal"),
                        alias, str(out),
                    ])
                self.assertEqual(code, 0, value)
                self.assertEqual(diagnose.call_args.args[2], out)

    def test_unknown_strategy_b_schema_never_reaches_the_legacy_comparator(self):
        case = self.root / "unknown-v2"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v99", "case_id": "unknown",
        }))
        with mock.patch.object(agent_tools.gfx_case, "inspect_case") as legacy:
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertIn("unsupported Strategy B schema", value["reason"])
        legacy.assert_not_called()

    def test_malformed_case_manifest_is_invalid_not_legacy(self):
        case = self.root / "malformed-case"
        case.mkdir()
        (case / "case.json").write_text("{not json")
        with mock.patch.object(agent_tools.gfx_case, "inspect_case") as legacy:
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertIn("unreadable", value["reason"])
        legacy.assert_not_called()

    def test_verifier_exception_is_invalid_not_incomplete(self):
        case = self.make_v2_case()
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               side_effect=RuntimeError("corrupt manifest")):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertTrue(value["result"]["verification"]["verifier_exception"])
        self.assertIn("RuntimeError", value["reason"])

    def test_digest_corruption_is_invalid_and_never_replayed(self):
        case = self.make_v2_case()
        verification = self.verification(
            ok=False, reproducible=True, reproduction_status="complete",
            problems=["digest drift frames/000001/frame-events.jsonl"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=verification), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case") as replay:
            code, value = self.call([
                "eu4_replay", "case", "--case", str(case),
                "--replay-out", str(self.root / "corrupt-replay"),
            ])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertFalse(value["outcome"]["qualified"])
        replay.assert_not_called()

    def test_missing_required_payload_is_invalid(self):
        case = self.make_v2_case()
        verification = self.verification(
            ok=False, reproducible=True, reproduction_status="complete",
            problems=["missing required frames/000002/shaders/shaders.json"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=verification):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertFalse(value["outcome"]["qualified"])

    def test_v1_incomplete_is_incomplete_and_integrity_failure_is_invalid(self):
        case = self.make_v2_case()
        (case / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SCHEMA,
            "case_id": "v1-incomplete",
            "reproduction": {"status": "incomplete",
                             "independently_reproducible": False},
        }))
        verification = self.verification(
            ok=False, reproducible=False, reproduction_status="incomplete",
            problems=["reproduction is explicitly incomplete"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=verification):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["evidence"], "unverified")

        verification["problems"] = [
            "reproduction is explicitly incomplete",
            "digest drift diagnostic-report.json",
        ]
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=verification):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "invalid")
        self.assertEqual(value["outcome"]["evidence"], "invalid")

    def test_budget_incomplete_is_incomplete_and_never_qualified(self):
        case = self.make_v2_case()
        verification = self.verification(
            ok=False, reproducible=False,
            reproduction_status="incomplete",
            problems=["reproduction is explicitly incomplete"])
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=verification):
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 2, value)
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")
        self.assertEqual(value["outcome"]["evidence"], "unverified")
        self.assertFalse(value["outcome"]["qualified"])

    def test_legacy_image_comparison_case_is_still_recognized(self):
        case = self.root / "legacy-case"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({
            "schema": agent_tools.gfx_case.SCHEMA,
            "case_dir": str(case),
        }))
        expected = {"schema": agent_tools.gfx_case.SCHEMA,
                    "comparison_status": "fail"}
        with mock.patch.object(agent_tools.gfx_case, "inspect_case",
                               return_value=expected) as legacy:
            code, value = self.call(["eu4_case_inspect", "--case", str(case)])
        self.assertEqual(code, 0, value)
        self.assertEqual(value["result"]["comparison_status"], "fail")
        legacy.assert_called_once()

    def test_m7_diagnosis_does_not_promote_incomplete_evidence(self):
        from automation import gfx_independent_replay

        report = {
            "schema": "gfx-frame-diagnosis/v1",
            "classification": "qualified_difference",
            "qualification": {"qualified": False,
                              "diagnostic_qualified": True},
            "evaluation": {"reasons": ["pixel difference"]},
            "pixels": {"final": {"status": "divergent"}},
            "first_divergence": {"kind": "final_only"},
            "checkpoints": [],
            "checkpoint_validation": {"status": "ok"},
            "policy": {"sha256": "b" * 64, "source": "explicit"},
        }
        with mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               return_value=report) as diagnose:
            code, value = self.call([
                "eu4_replay", "diagnose",
                "--frame", str(self.root / "frame"),
                "--metal", str(self.root / "metal"),
                "--replay-out", str(self.root / "diagnosed"),
                "--require-seal",
            ])
        self.assertEqual(code, 0, value)
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertFalse(value["outcome"]["qualified"])
        self.assertTrue(diagnose.call_args.kwargs["require_seal"])
        self.assertEqual(value["result"]["policy"]["sha256"], "b" * 64)
        self.assertEqual(value["result"]["policy"]["source"], "explicit")

        report["classification"] = "incomplete_diagnostics"
        report["qualification"] = {"qualified": False}
        with mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               return_value=report):
            code, value = self.call([
                "eu4_replay", "diagnose",
                "--frame", str(self.root / "frame"),
                "--metal", str(self.root / "metal"),
                "--replay-out", str(self.root / "incomplete"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["evidence"], "unverified")

    def test_v2_replay_failure_is_structured_not_an_exception(self):
        case = self.make_v2_case()
        with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                               return_value=self.verification()), \
             mock.patch.object(agent_tools.gfx_replay_case, "replay_case",
                               side_effect=gfx_replay_case.CaseError(
                                   "sequence_replay", "campaign ended failed")):
            code, value = self.call([
                "eu4_replay", "case", "--case", str(case),
                "--replay-out", str(self.root / "v2-failed"),
            ])
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        self.assertIn("sequence_replay", value["reason"])


class RecordedExperimentATests(unittest.TestCase):
    """Phase 3: the recorded Experiment A session, when it is available locally.

    The evidence lives under the gitignored ``.local`` tree, so this test is
    skipped on a clean checkout; the synthetic temporal tests cover the same
    qualitative findings portably.
    """

    SESSION = (ROOT / ".local" / "infra" / "live-a6"
               / "session-20261114T181230Z-d28f6fee")

    def setUp(self):
        if not (self.SESSION / "result.json").is_file():
            self.skipTest("recorded Experiment A evidence is not present")
        try:
            import numpy  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")

    def test_temporal_mode_recovers_the_recorded_findings(self):
        from types import SimpleNamespace
        args = SimpleNamespace(
            run=str(self.SESSION), build_case=False, full=False, limit=20,
            offset=0, temporal=True, reference_frame=74, candidate_frame=74,
            temporal_window=1, temporal_artifacts=False,
            temporal_artifact_dir=None,
        )
        value = agent_tools.eu4_diff_inspect(args)
        self.assertTrue(value["ok"], value.get("reason"))
        payload = value["result"]
        temporal = payload["temporal"]
        self.assertEqual(temporal["status"], "completed")
        pairs = {(row["relation"], row["reference"]["frame_index"],
                  row["candidate"]["frame_index"]): row
                 for row in temporal["pairs"]}
        reference = pairs[("reference_temporal", 74, 75)]["statistics"]
        candidate = pairs[("candidate_temporal", 74, 75)]["statistics"]
        cross = pairs[("cross_process", 74, 74)]["statistics"]
        self.assertGreater(reference["mismatched_fraction"],
                           cross["mismatched_fraction"])
        self.assertGreater(candidate["mismatched_fraction"],
                           cross["mismatched_fraction"])
        self.assertAlmostEqual(cross["mismatched_fraction"], 0.013492, places=6)
        overlap = next(
            row for row in temporal["overlap"]["temporal"]
            if row["relation"] == "reference_temporal"
            and row["reference_frame"] == 74
            and row["candidate_frame"] == 75)
        self.assertGreater(overlap["cross_also_temporal_fraction"], 0.9)
        self.assertFalse(payload["staticity"]["verified"])
        self.assertEqual(payload["classification"], "incomplete")
        self.assertEqual(payload["recorded_classification"],
                         "rendering_difference")


class SubprocessDiagnosticTests(unittest.TestCase):
    """Task 3: preserve subprocess outcomes instead of trusting parseable JSON."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)

    def call(self, argv):
        output = self.root / "tool.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = agent_tools.main(
                ["--out", str(output), *_with_rationale(argv)])
        return code, json.loads(output.read_text())

    def completed(self, body, *, code=0, stderr="", command=None):
        stdout = body if isinstance(body, str) else json.dumps(body)
        return subprocess.CompletedProcess(command or [], code, stdout=stdout,
                                           stderr=stderr)

    def probe_call(self, body, *, code=0, stderr=""):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(
                    agent_tools.subprocess, "run",
                    return_value=self.completed(body, code=code, stderr=stderr)) as run:
                code_value, value = self.call([
                    "eu4_capture", "probe", "--checkpoint", "after-present",
                    "--output", str(root), "--campaign-run-root", str(root),
                    "--timeout", "5",
                ])
        return code_value, value, run

    def replay_call(self, body, *, code=0, stderr=""):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(
                    agent_tools.subprocess, "run",
                    return_value=self.completed(body, code=code, stderr=stderr)) as run:
                code_value, value = self.call([
                    "eu4_replay", "frame", "--frame", str(root / "frame"),
                    "--shaders", str(root / "shaders"),
                    "--out", str(root / "out"), "--only-event", "3",
                ])
        return code_value, value, run

    def test_probe_success_is_a_completed_diagnostic(self):
        code, value, run = self.probe_call({
            "overall": "presented",
            "status": {"pixels_captured": True, "presented": True},
            "error": None,
        })
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertEqual(value["outcome"]["verdict"], "presented")
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "automation.live_gfx", "probe"])
        self.assertIn("--checkpoint", command)
        self.assertEqual(value["result"]["subprocess"]["exit_code"], 0)

    def test_probe_nonzero_exit_is_not_success(self):
        code, value, run = self.probe_call(
            {"overall": "failed", "status": {}, "error": "capture failed"},
            code=4, stderr="launch failed")
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "unavailable")
        self.assertEqual(value["outcome"]["diagnostic"], "unavailable")
        self.assertIn("exited 4", value["reason"])
        self.assertEqual(value["result"]["subprocess"]["exit_code"], 4)

    def test_probe_malformed_output_is_a_failed_execution(self):
        code, value, run = self.probe_call("not-json", code=0)
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["outcome"]["diagnostic"], "failed")
        self.assertIn("not JSON", value["reason"])

    def test_frame_replay_difference_is_a_valid_diagnostic_result(self):
        code, value, run = self.replay_call({
            "status": "fail", "mismatched_pixels": 4,
            "pixels": 48, "max_channel_error": 20,
        }, code=2)
        self.assertEqual(code, 0)
        self.assertTrue(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertEqual(value["outcome"]["verdict"], "different")
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "automation.m6_frame", "replay"])
        self.assertEqual(value["result"]["subprocess"]["exit_code"], 2)

    def test_frame_replay_without_explicit_shaders_omits_the_argument(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(
                    agent_tools.subprocess, "run",
                    return_value=self.completed(
                        {"status": "pass", "pixels": 48,
                         "mismatched_pixels": 0}, code=0)) as run:
                code_value, value = self.call([
                    "eu4_replay", "frame", "--frame", str(root / "frame"),
                    "--out", str(root / "out"),
                ])
        self.assertEqual(code_value, 0)
        self.assertTrue(value["ok"], value.get("reason"))
        command = run.call_args.args[0]
        self.assertEqual(command[1:4], ["-m", "automation.m6_frame", "replay"])
        self.assertNotIn("--shaders", command)
        self.assertIn(str(root / "frame"), command)

    def test_frame_replay_invalid_is_a_failed_execution(self):
        code, value, run = self.replay_call(
            {"status": "error", "reason": "frame is invalid"}, code=1,
            stderr="traceback")
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["outcome"]["diagnostic"], "failed")
        self.assertIn("frame is invalid", value["reason"])

    def test_frame_replay_malformed_output_is_failed_not_complete(self):
        code, value, run = self.replay_call("", code=0, stderr="no output")
        self.assertEqual(code, 2)
        self.assertFalse(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "failed")
        self.assertIn("no output", value["reason"])
