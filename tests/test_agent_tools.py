"""PR 5: one JSON tool core for every agent surface."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import agent_tools, gfx_session  # noqa: E402
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
        self.assertIn(value["status"], ("pass", "fail", "unverified"),
                      msg=json.dumps(value)[:2000])

    def test_diff_run_without_ledger_is_an_error_envelope(self):
        scenario = self.root / "scenario.json"
        scenario.write_text(json.dumps({
            "schema": "gfx-dual-scenario/v1",
            "executable_sha256": json.loads(
                (ROOT / "reverse_engineering" / "target.json").read_text())["sha256"],
            "logical_extent": [64, 64],
            "comparison": {"regions": {"all": {"rect": [0, 64, 0, 64]}}},
        }))
        code, value = self.call([
            "eu4_diff_run", "--scenario", str(scenario),
            "--run-root", str(self.root), "--ledger", str(self.root / "missing.json"),
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
        (run / "reference").mkdir(parents=True)
        (run / "candidate").mkdir(parents=True)
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
            "scenario": {"name": "case", "digest": "d" * 64,
                         "comparison": {"policy": "tolerant",
                                        "max_channel_error": 8,
                                        "max_mismatched_fraction": 0.01,
                                        "max_mean_abs_error": 1.5, "regions": {}}},
            "health": {"verdict": "unknown", "first_failure": None,
                       "instrumentation_failures": []},
            "frames": {"comparison": {"status": "fail", "first_failure": pair},
                       "last_pair": pair},
            "stop": {"reason": "frame_difference"},
            "artifacts": {"bytes": 10, "max_bytes": 100, "dropped": 0},
            "processes": {}, "ledger": {}, "preflight": {}, "terminal": {},
        }
        (run / "result.json").write_text(json.dumps(result))
        (run / "session.json").write_text(json.dumps({"session_id": "case-run"}))
        return run


if __name__ == "__main__":
    unittest.main()
