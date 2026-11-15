"""Phase 1: the actual DSH bridge spawns the right interpreter and replays a case."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_case, python_runtime  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


def build_case(root: Path) -> Path:
    run = root / "run"
    (run / "reference").mkdir(parents=True)
    (run / "candidate").mkdir(parents=True)
    reference = np.full((6, 8, 4), 20, dtype=np.uint8)
    reference[0:2, 0:2, :3] = 200
    candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
    candidate[0, 0, 0] = 240
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
        "session_id": "bridge-case",
        "scenario": {"name": "bridge", "digest": "d" * 64,
                     "comparison": {"policy": "tolerant",
                                    "max_channel_error": 8,
                                    "max_mismatched_fraction": 0.01,
                                    "max_mean_abs_error": 1.5, "regions": {}},
                     "settle": {"stable_frames": 2}},
        "health": {"verdict": "unknown", "first_failure": None,
                   "instrumentation_failures": []},
        "frames": {"comparison": {"status": "fail", "first_failure": pair},
                   "last_pair": pair},
        "stop": {"reason": "frame_difference"},
        "stability": {"status": "verified"},
        "qualification": {"requested_frames": 1, "qualified": False},
        "artifacts": {"bytes": 10, "max_bytes": 100, "dropped": 0},
        "processes": {}, "ledger": {}, "preflight": {}, "terminal": {},
    }
    (run / "result.json").write_text(json.dumps(result))
    (run / "session.json").write_text(json.dumps({"session_id": "bridge-case"}))
    return Path(gfx_case.build_case(run)["case_dir"])


@unittest.skipUnless(np is not None, "numpy is required for the native replay")
@unittest.skipUnless(shutil.which("node"), "the tools bridge needs Node")
class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def run_node(self, tool: str, args: dict, *, env=None,
                 python: str | None = None) -> dict:
        bridge = (ROOT / "dsh" / "eu4-tools-plugin" / "lib" / "bridge.js").as_uri()
        selected = python or sys.executable
        environment = {"EU4_DSH_PYTHON": selected, **(env or {})}
        script = self.root / "native.mjs"
        script.write_text(
            "import { runTool } from " + json.dumps(bridge) + ";\n"
            "const value = await runTool(" + json.dumps(tool) + ", "
            + json.dumps(args) + ", { python: " + json.dumps(selected)
            + ", cwd: " + json.dumps(str(ROOT))
            + ", env: " + json.dumps(environment) + " });\n"
            "console.log(JSON.stringify(value));\n",
            encoding="utf-8",
        )
        ran = subprocess.run(["node", str(script)], cwd=self.root,
                             capture_output=True, text=True, check=False,
                             timeout=300)
        self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
        return json.loads(ran.stdout.strip().splitlines()[-1])

    def test_native_replay_uses_the_configured_interpreter_and_completes(self):
        case = build_case(self.root)
        value = self.run_node("eu4_replay", {"op": "case", "case": str(case)})
        self.assertEqual(value["schema"], "eu4-agent-tool/v2")
        self.assertIn(value["status"], ("ok", "incomplete"), value)
        self.assertEqual(value["runtime"]["executable"],
                         python_runtime.absolute(sys.executable))
        self.assertEqual(value["runtime"]["configured"], sys.executable)
        self.assertFalse(value["runtime"]["mismatch"])
        self.assertTrue(value["runtime"]["numpy"]["available"])
        self.assertEqual(value["outcome"]["execution"], "ok")
        self.assertEqual(value["outcome"]["diagnostic"], "complete")
        self.assertIn(value["outcome"]["verdict"],
                      ("equal", "different", "unverified"))
        self.assertIn("replay_status", value["result"])

    def test_a_configured_interpreter_mismatch_is_visible_in_the_envelope(self):
        value = self.run_node(
            "eu4_session", {"op": "list"},
            env={"EU4_DSH_PYTHON": "/no/such/python"})
        # The bridge ran the interpreter it was given; the envelope records that
        # the launcher's configured path is not the one that executed the core.
        self.assertTrue(value["ok"])
        self.assertTrue(value["runtime"]["mismatch"])
        self.assertEqual(value["runtime"]["configured"], "/no/such/python")
        self.assertEqual(value["runtime"]["executable"],
                         python_runtime.absolute(sys.executable))

    def make_evidence(self, root: Path, campaign: str, session: str) -> Path:
        directory = root / campaign / f"session-{session}"
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({
            "schema": "gfx-dual-run/v1",
            "session_id": session,
            "classification": "incomplete",
            "health": {"verdict": "healthy"},
            "scenario": {"name": campaign, "digest": "d" * 64},
            "qualification": {"status": "incomplete", "qualified": False},
            "finished_utc": "2026-11-14T18:12:56Z",
        }))
        return directory

    def test_native_discovery_honours_the_inherited_evidence_root(self):
        evidence = self.root / "evidence"
        self.make_evidence(evidence, "live-a6", "a6-native")
        value = self.run_node(
            "eu4_session", {"op": "discover"},
            env={"EU4_EVIDENCE_ROOT": str(evidence)})
        self.assertTrue(value["ok"], value)
        sessions = {row["session_id"] for row in value["result"]["sessions"]}
        self.assertEqual(sessions, {"a6-native"})

    def test_explicit_evidence_root_keeps_precedence(self):
        inherited = self.root / "inherited"
        explicit = self.root / "explicit"
        self.make_evidence(inherited, "live-a6", "inherited-session")
        self.make_evidence(explicit, "live-a9", "explicit-session")
        value = self.run_node(
            "eu4_session",
            {"op": "discover", "evidence_root": str(explicit)},
            env={"EU4_EVIDENCE_ROOT": str(inherited)})
        sessions = {row["session_id"] for row in value["result"]["sessions"]}
        self.assertEqual(sessions, {"explicit-session"})

    def test_native_and_cli_discovery_return_the_same_sessions(self):
        evidence = self.root / "same-evidence"
        self.make_evidence(evidence, "live-a6", "a6-same")
        self.make_evidence(evidence, "live-a21", "a21-same")
        native = self.run_node(
            "eu4_session", {"op": "discover"},
            env={"EU4_EVIDENCE_ROOT": str(evidence)})
        completed = subprocess.run(
            [sys.executable, "-m", "automation.agent_tools",
             "eu4_session", "discover"],
            cwd=ROOT, capture_output=True, text=True, check=False,
            env={**os.environ, "EU4_EVIDENCE_ROOT": str(evidence)},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        cli = json.loads(completed.stdout)["result"]["sessions"]
        self.assertEqual(
            {row["session_id"] for row in native["result"]["sessions"]},
            {row["session_id"] for row in cli})

    def test_unrelated_environment_variable_is_not_forwarded(self):
        evidence = self.root / "evidence"
        self.make_evidence(evidence, "live-a6", "a6-native")
        value = self.run_node(
            "eu4_session", {"op": "discover"},
            env={"EU4_EVIDENCE_ROOT": str(evidence),
                 "EU4_UNRELATED_SECRET": "must-not-leak"})
        # The command output cannot inspect the child env directly; the bridge
        # allowlist unit test covers exclusion. This confirms the tool still
        # works while an unrelated variable is present in the parent env.
        self.assertTrue(value["ok"], value)

    def test_virtualenv_bridge_acceptance(self):
        venv = self.root / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip",
             "--system-site-packages", str(venv)],
            check=True, capture_output=True, text=True)
        python = venv / "bin" / "python3"
        case = build_case(self.root)
        evidence = self.root / "evidence"
        self.make_evidence(evidence, "live-a6", "venv-a6")
        environment = {"EU4_DSH_PYTHON": str(python),
                       "EU4_EVIDENCE_ROOT": str(evidence)}
        replay = self.run_node(
            "eu4_replay", {"op": "case", "case": str(case)},
            env=environment, python=str(python))
        self.assertTrue(replay["ok"], replay)
        # macOS may canonicalize /var to /private/var; the virtualenv entry
        # point itself must still be the selected path, not the base binary.
        self.assertEqual(
            os.path.realpath(os.path.dirname(replay["runtime"]["executable"])),
            os.path.realpath(str(python.parent)))
        self.assertEqual(os.path.basename(replay["runtime"]["executable"]),
                         "python3")
        self.assertNotEqual(replay["runtime"]["executable"],
                            os.path.realpath(python))
        self.assertEqual(replay["outcome"]["diagnostic"], "complete")

        discovered = self.run_node(
            "eu4_session", {"op": "discover"},
            env=environment, python=str(python))
        self.assertEqual(
            {row["session_id"] for row in discovered["result"]["sessions"]},
            {"venv-a6"})

        broken = self.root / "empty-case"
        (broken / "repro").mkdir(parents=True)
        (broken / "repro" / "repro.json").write_text(json.dumps({
            "schema": "gfx-diagnostic-repro/v1",
            "frames": {"reference": {}, "candidate": {}},
            "policy": {"policy": "tolerant"},
        }))
        incomplete = self.run_node(
            "eu4_replay", {"op": "case", "case": str(broken)},
            env=environment, python=str(python))
        self.assertFalse(incomplete["ok"])
        self.assertEqual(incomplete["outcome"]["diagnostic"], "incomplete")
        self.assertEqual(incomplete["exit_code"], 2)

    def test_native_incomplete_replay_is_not_a_silent_success(self):
        case = self.root / "broken-case"
        (case / "repro").mkdir(parents=True)
        (case / "repro" / "repro.json").write_text(json.dumps({
            "schema": "gfx-diagnostic-repro/v1",
            "frames": {"reference": {}, "candidate": {}},
            "policy": {"policy": "tolerant"},
        }))
        value = self.run_node("eu4_replay", {"op": "case", "case": str(case)})
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")
        self.assertFalse(value["outcome"]["diagnostic_complete"])
        self.assertEqual(value["exit_code"], 2)


if __name__ == "__main__":
    unittest.main()
