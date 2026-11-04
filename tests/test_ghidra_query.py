import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reverse_engineering.ghidra_query import SCHEMA, assemble, main
from reverse_engineering.paths import load_target

SHA = load_target()["sha256"]
BASE = int(load_target()["image_base"])
ENTRY = "0x00000000015ec28e"
ADDRESS = BASE + 0x15EC28E


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def _identity():
    return {
        "ghidra_version": "12.2",
        "program_name": "eu4",
        "executable_sha256": SHA,
        "image_base": BASE,
        "query": ENTRY,
        "queried_rva": ENTRY,
        "queried_address": f"0x{ADDRESS:016x}",
    }


def _present(**overrides):
    raw = {
        "program_name": "eu4",
        "image_base": BASE,
        "executable_sha256": SHA,
        "presence": "present",
        "entry_rva": ENTRY,
        "name": "_GfxSetIndexBuffer",
        "body": [{"start_rva": ENTRY, "end_rva": "0x00000000015ec2bc"}],
        "thunk": False,
        "no_body": False,
        "decompile_status": "failed",
        "decompile_error": "decompilation failed",
        "pseudocode_total": 0,
        "pseudocode": "",
        "instruction_total": 1,
        "instructions": [{"rva": ENTRY, "bytes": "4885f6", "mnemonic": "TEST", "text": "TEST RSI,RSI"}],
        "caller_total": 0,
        "callers": [],
        "callee_total": 0,
        "callees": [],
        "hypothesis": {"calling_convention": "unknown", "return_type": "undefined", "parameters": []},
    }
    raw.update(overrides)
    return raw


class QuerySetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "corpus"
        self.project = Path(self.tmp.name) / "recorded"
        self.root.mkdir()
        self.project.mkdir()
        (self.project / "EU4_test.gpr").write_text("project", encoding="utf-8")
        (self.root / "ghidra_project.json").write_text(
            json.dumps(
                {
                    "project_dir": str(self.project),
                    "project_name": "EU4_test",
                    "program_name": "eu4",
                    "image_base": BASE,
                }
            ),
            encoding="utf-8",
        )
        self.env = mock.patch.dict(os.environ, {"EU4_RE_EXPORT": str(self.root)})

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv, side_effect):
        with self.env:
            with mock.patch("reverse_engineering.ghidra_query.analyze_headless", side_effect=side_effect) as headless:
                with mock.patch("sys.stdout") as stdout:
                    chunks = []
                    stdout.write.side_effect = chunks.append
                    code = main(argv)
        self.assertEqual(code, 0)
        return headless, json.loads("".join(chunks))

    def _write_raw(self, raw):
        def side_effect(project_dir, project_name, *extra, process_timeout=None):
            script = extra.index("QueryProgram.java")
            self.captured = {
                "project_dir": Path(project_dir),
                "project_name": project_name,
                "extra": extra,
                "process_timeout": process_timeout,
            }
            self.request = json.loads(Path(extra[script + 1]).read_text(encoding="utf-8"))
            Path(extra[script + 2]).write_text(json.dumps(raw), encoding="utf-8")

        return side_effect


class AddressAndProjectTests(QuerySetup):
    def test_rva_uses_recorded_image_base(self):
        headless, document = self._run(
            ["function", "0x15ec28e"],
            self._write_raw(_present()),
        )
        self.assertEqual(self.request["address"], ADDRESS)
        self.assertEqual(document["status"], "ok")
        self.assertEqual(document["evidence"]["presence"], "present")
        self.assertEqual(document["evidence"]["hypothesis"]["source"], "ghidra")
        headless.assert_called_once()

    def test_uses_recorded_project_dir_when_export_root_is_set(self):
        self._run(["function", "_GfxSetIndexBuffer"], self._write_raw(_present(presence="absent")))
        self.assertEqual(self.captured["project_dir"], self.project)
        self.assertEqual(self.captured["project_name"], "EU4_test")
        extra = self.captured["extra"]
        self.assertIn("-process", extra)
        self.assertIn("eu4", extra)
        self.assertIn("-noanalysis", extra)
        self.assertIn("-readOnly", extra)
        self.assertEqual([item for item in extra if str(item).endswith(".java")], ["QueryProgram.java"])
        self.assertNotIn("-import", extra)
        self.assertNotIn("-deleteProject", extra)
        self.assertFalse((self.root / "ghidra-project").exists())

    def test_missing_metadata_does_not_launch_or_create(self):
        (self.root / "ghidra_project.json").unlink()
        with self.env:
            with mock.patch("reverse_engineering.ghidra_query.analyze_headless") as headless:
                with mock.patch("sys.stdout"):
                    code = main(["function", ENTRY])
        self.assertEqual(code, 0)
        headless.assert_not_called()
        self.assertFalse(self.project.joinpath("created").exists())

    def test_missing_project_is_not_created(self):
        missing = self.project / "absent-dir"
        meta = json.loads((self.root / "ghidra_project.json").read_text())
        meta["project_dir"] = str(missing)
        (self.root / "ghidra_project.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.env:
            with mock.patch("reverse_engineering.ghidra_query.analyze_headless") as headless:
                with mock.patch("sys.stdout") as stdout:
                    chunks = []
                    stdout.write.side_effect = chunks.append
                    main(["function", ENTRY])
        headless.assert_not_called()
        self.assertFalse(missing.exists())
        document = json.loads("".join(chunks))
        self.assertEqual(document["status"], "unavailable")
        self.assertIn("recorded ghidra project is missing", document["detail"])

    def test_metadata_image_base_mismatch_does_not_launch(self):
        meta = json.loads((self.root / "ghidra_project.json").read_text())
        meta["image_base"] = 1
        (self.root / "ghidra_project.json").write_text(json.dumps(meta), encoding="utf-8")
        with self.env:
            with mock.patch("reverse_engineering.ghidra_query.analyze_headless") as headless:
                with mock.patch("sys.stdout") as stdout:
                    chunks = []
                    stdout.write.side_effect = chunks.append
                    main(["function", ENTRY])
        headless.assert_not_called()
        document = json.loads("".join(chunks))
        self.assertIn("image_base", document["detail"])

    def test_executable_mismatch_discards_evidence(self):
        raw = _present(executable_sha256="ab" * 32, pseudocode="SECRET_PSEUDO", pseudocode_total=13)
        _headless, document = self._run(["function", ENTRY], self._write_raw(raw))
        self.assertEqual(document["status"], "identity_mismatch")
        self.assertIsNone(document["evidence"])
        self.assertNotIn("SECRET_PSEUDO", json.dumps(document))

    def test_malformed_response(self):
        def side_effect(project_dir, project_name, *extra, process_timeout=None):
            Path(extra[extra.index("QueryProgram.java") + 2]).write_text("{", encoding="utf-8")

        _headless, document = self._run(["function", ENTRY], side_effect)
        self.assertEqual(document["reason"], "malformed_response")

    def test_headless_failure(self):
        from reverse_engineering.paths import ReError

        def side_effect(*args, **kwargs):
            raise ReError("analyzeHeadless failed")

        _headless, document = self._run(["function", ENTRY], side_effect)
        self.assertEqual(document["status"], "unavailable")
        self.assertIn("analyzeHeadless failed", document["detail"])

    def test_address_name_is_rejected(self):
        with mock.patch("reverse_engineering.ghidra_query.analyze_headless") as headless:
            with mock.patch("sys.stdout") as stdout:
                chunks = []
                stdout.write.side_effect = chunks.append
                main(["address", "_GfxSetIndexBuffer"])
        headless.assert_not_called()
        self.assertIn("address queries require an RVA", json.loads("".join(chunks))["detail"])

    def test_timeout_uses_process_group_cleanup(self):
        proc = mock.MagicMock()
        proc.pid = 4242
        proc.poll.return_value = None
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="ghidra", timeout=5)
        with self.env:
            with mock.patch("reverse_engineering.corpus.build_analyze_headless_argv", return_value=["ghidra"]):
                with mock.patch("reverse_engineering.corpus.subprocess.Popen", return_value=proc):
                    with mock.patch("reverse_engineering.corpus.terminate_process_group") as terminate:
                        with mock.patch("sys.stdout") as stdout:
                            chunks = []
                            stdout.write.side_effect = chunks.append
                            code = main(["function", ENTRY, "--process-timeout", "5"])
        self.assertEqual(code, 0)
        terminate.assert_called()
        self.assertEqual(json.loads("".join(chunks))["status"], "unavailable")


class PagingTests(unittest.TestCase):
    def test_zero_references_stay_present(self):
        document = assemble(
            "address",
            {
                "presence": "present",
                "initialized": True,
                "defined": True,
                "defined_kind": "data",
                "value_kind": "static_image",
                "static_bytes": "0000000000000000",
                "code_ref_total": 0,
                "code_refs": [],
                "data_ref_total": 0,
                "data_refs": [],
                "pcode_total": 0,
                "pcode": [],
            },
            identity=_identity(),
            offsets={"code_ref": 0, "data_ref": 0, "pcode": 0},
            budget=4096,
        )
        self.assertEqual(document["status"], "ok")
        self.assertEqual(document["evidence"]["presence"], "present")
        self.assertEqual(document["evidence"]["code_refs"], [])
        self.assertEqual(document["evidence"]["data_ref_total"], 0)
        self.assertNotIn("runtime_value", set(_keys(document)))
        self.assertEqual(document["evidence"]["value_kind"], "static_image")

    def test_unmapped_and_unresolved_are_distinct(self):
        unmapped = assemble(
            "address",
            {"presence": "unmapped"},
            identity=_identity(),
            offsets={"code_ref": 0, "data_ref": 0, "pcode": 0},
            budget=4096,
        )
        self.assertEqual(unmapped["status"], "unmapped")
        self.assertNotIn("code_refs", unmapped["evidence"])
        present = assemble(
            "address",
            {
                "presence": "present",
                "initialized": False,
                "defined": False,
                "defined_kind": "undefined",
                "value_kind": "unavailable",
                "code_ref_total": 1,
                "code_refs": [{"from_rva": ENTRY, "kind": "code", "ref_type": "COMPUTED_CALL", "unresolved": True}],
                "data_ref_total": 0,
                "data_refs": [],
                "pcode_total": 0,
                "pcode": [],
            },
            identity=_identity(),
            offsets={"code_ref": 0, "data_ref": 0, "pcode": 0},
            budget=4096,
        )
        self.assertEqual(present["evidence"]["value_kind"], "unavailable")
        self.assertTrue(present["evidence"]["code_refs"][0]["unresolved"])
        self.assertEqual(present["evidence"]["presence"], "present")

    def test_absent_function(self):
        document = assemble(
            "function",
            {"presence": "absent"},
            identity=_identity(),
            offsets={},
            budget=4096,
        )
        self.assertEqual(document["status"], "absent")
        self.assertEqual(document["evidence"], {"presence": "absent"})

    def test_continuation_follows_returned_rows(self):
        instructions = [
            {"rva": f"0x{index:016x}", "bytes": "90", "mnemonic": "NOP", "text": "X" * 180}
            for index in range(4)
        ]
        document = assemble(
            "function",
            _present(instruction_total=10, instructions=instructions, pseudocode_total=0, pseudocode=""),
            identity=_identity(),
            offsets={"instruction": 5, "decompile": 0, "caller": 0, "callee": 0},
            budget=1600,
        )
        returned = document["evidence"]["instruction_returned"]
        self.assertGreater(returned, 0)
        self.assertLess(returned, len(instructions))
        self.assertEqual(document["next_instruction_offset"], 5 + returned)

    def test_blocked_collection_has_no_next_offset(self):
        document = assemble(
            "function",
            _present(
                instructions=[{"rva": ENTRY, "bytes": "90", "mnemonic": "NOP", "text": "NOP"}],
                instruction_total=1,
                callers=[{"name": "N" * 4000, "external": False, "entry_rva": ENTRY}],
                caller_total=3,
            ),
            identity=_identity(),
            offsets={"instruction": 0, "decompile": 0, "caller": 0, "callee": 0},
            budget=1600,
        )
        self.assertEqual(document["status"], "ok")
        self.assertTrue(document["evidence"]["caller_blocked"])
        self.assertNotIn("next_caller_offset", document)
        self.assertNotIn("N" * 40, json.dumps(document))
        self.assertEqual(document["evidence"]["instruction_returned"], 1)

    def test_oversized_record_is_response_budget(self):
        document = assemble(
            "function",
            _present(
                instructions=[{"rva": ENTRY, "bytes": "90", "mnemonic": "M" * 5000, "text": "M" * 5000}],
                instruction_total=1,
                pseudocode="",
                pseudocode_total=0,
            ),
            identity=_identity(),
            offsets={"instruction": 0, "decompile": 0, "caller": 0, "callee": 0},
            budget=1024,
        )
        self.assertEqual(document["reason"], "response_budget")
        self.assertNotIn("M" * 40, json.dumps(document))

    def test_decompiler_text_pages_independently(self):
        document = assemble(
            "function",
            _present(
                instructions=[{"rva": ENTRY, "bytes": "90", "mnemonic": "NOP", "text": "NOP"}],
                instruction_total=1,
                pseudocode="abcdefghij" * 400,
                pseudocode_total=4000,
                decompile_status="success",
            ),
            identity=_identity(),
            offsets={"instruction": 0, "decompile": 0, "caller": 0, "callee": 0},
            budget=1600,
        )
        self.assertEqual(document["evidence"]["instruction_returned"], 1)
        self.assertNotIn("next_instruction_offset", document)
        returned = document["evidence"]["pseudocode_returned"]
        self.assertGreater(returned, 0)
        self.assertLess(returned, 4000)
        self.assertEqual(document["next_decompile_offset"], returned)
        self.assertEqual(document["evidence"]["pseudocode"], ("abcdefghij" * 400)[:returned])


class InterfaceTests(unittest.TestCase):
    def test_script_has_no_write_api_names(self):
        text = (Path(__file__).resolve().parents[1] / "reverse_engineering" / "ghidra" / "QueryProgram.java").read_text()
        for needle in ("save(", "startTransaction", "setImageBase"):
            self.assertNotIn(needle, text)

    def test_python_does_not_derive_a_project_path(self):
        text = (Path(__file__).resolve().parents[1] / "reverse_engineering" / "ghidra_query.py").read_text()
        self.assertNotIn("ghidra_project_dir", text)

    def test_response_fits_schema(self):
        document = assemble(
            "function",
            {"presence": "absent"},
            identity=_identity(),
            offsets={},
            budget=2048,
        )
        self.assertEqual(document["schema"], SCHEMA)


if __name__ == "__main__":
    unittest.main()
