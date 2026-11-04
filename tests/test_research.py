import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

from reverse_engineering import index_db, research, renderer_extract
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import ReError
from reverse_engineering.renderer_scope import FROZEN_RENDERER_BATCH_SIZE, build_batch_plan, parse_batch_plan
from tests.test_renderer_extract import _census_row, _valid_static_row

SHA = "a"
REV = "rev-test"
BUNDLE = "bundle-digest"
FIXTURE = "0x0000000000001000"
SECOND = "0x0000000000002100"
EXTRA = "0x0000000000003000"
ABSENT = "0x0000000000009999"


def _summary() -> dict:
    return {
        "baseline_revision": REV,
        "selection_manifest_sha256": "manifest-digest",
        "binary_sha256": SHA,
        "ghidra_version": "12.1.4",
    }


def _provenance(**overrides: str) -> dict[str, str]:
    body = {
        "binary_sha256": SHA,
        "baseline_revision": REV,
        "inventory_bundle_digest": BUNDLE,
    }
    body.update(overrides)
    return body


def _function_row(entry: str, mangled: str, demangled: str | None = None) -> dict:
    return {
        "entry_rva": entry,
        "kind": "internal",
        "name_mangled": mangled,
        "name_demangled": demangled if demangled is not None else mangled,
        "namespace": "",
        "instruction_count": 1,
        "decoded_instruction_bytes": 1,
        "instruction_ranges": [{"start_rva": entry, "end_rva": format_rva(parse_rva(entry) + 1)}],
    }


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


class CorpusFixture:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.exp = self.root / "exports" / REV
        (self.exp / "inventory").mkdir(parents=True)
        (self.exp / "audits").mkdir()
        (self.exp / "decompiled").mkdir()
        (self.exp / "renderer_snapshot" / "v1" / "batches").mkdir(parents=True)
        (self.root / "ghidra_project.json").write_text("{}\n")

    def close(self) -> None:
        self.tmp.cleanup()

    def write_index(self, functions: list[dict], edges: list[dict] | None = None, provenance: dict | None = None) -> None:
        inventory = self.exp / "inventory"
        (inventory / "functions.jsonl").write_text(_jsonl(functions))
        if edges is not None:
            (inventory / "call_edges.jsonl").write_text(_jsonl(edges))
        index_db.rebuild_index(
            self.exp / "index.sqlite",
            inventory,
            self.exp / "decompiled",
            provenance if provenance is not None else _provenance(),
        )
        conn = index_db.connect(self.exp / "index.sqlite")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        for name in ("index.sqlite-wal", "index.sqlite-shm"):
            sidecar = self.exp / name
            if sidecar.exists():
                sidecar.unlink()

    def write_callsites(self, rows: list[dict]) -> None:
        (self.exp / "audits" / "callsites-v1.jsonl").write_text(_jsonl(rows))

    def snapshot(self) -> dict[str, bytes | None]:
        found: dict[str, bytes | None] = {}
        for name in ("index.sqlite", "index.sqlite-wal", "index.sqlite-shm"):
            path = self.exp / name
            found[name] = path.read_bytes() if path.exists() else None
        return found

    def context(self, ordered: list[str]):
        plan = build_batch_plan(
            summary=_summary(),
            bundle_digest=BUNDLE,
            ordered_rvas=ordered,
            batch_size=FROZEN_RENDERER_BATCH_SIZE,
        )
        return research.assemble_context(self.exp, _summary(), plan, BUNDLE, ordered, REV), plan


def _run(ctx, argv: list[str]) -> tuple[int, dict]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = research.main(argv, loader=lambda _baseline: (ctx, None))
    return code, json.loads(buf.getvalue())


def _simple_static(entry: str, byte_hex: str) -> tuple[dict, dict, dict]:
    length = len(byte_hex) // 2
    end = format_rva(parse_rva(entry) + length)
    ranges = [{"start_rva": entry, "end_rva": end}]
    inventory = {
        "entry_rva": entry,
        "kind": "internal",
        "name_mangled": f"fn_{entry}",
        "name_demangled": f"fn_{entry}",
        "namespace": "",
        "instruction_count": 1,
        "decoded_instruction_bytes": length,
        "instruction_ranges": ranges,
    }
    static = {
        "entry_rva": entry,
        "status": "success",
        "instruction_count": 1,
        "decoded_instruction_bytes": length,
        "body_ranges": ranges,
        "decoded_instruction_ranges": ranges,
        "instructions": [{
            "instruction_rva": entry,
            "length": length,
            "bytes_hex": byte_hex,
            "census_call": False,
            "pcode_kind": "none",
            "flow_type_is_call": False,
            "pcode_is_call": False,
        }],
    }
    decomp = {"entry_rva": entry, "status": "success", "pseudocode": f"/* {entry} */"}
    return inventory, static, decomp


def _many_static(entry: str, count: int, text: str) -> tuple[dict, dict, dict]:
    start = parse_rva(entry)
    instructions = []
    for index in range(count):
        instructions.append({
            "instruction_rva": format_rva(start + index),
            "length": 1,
            "bytes_hex": "90",
            "census_call": False,
            "pcode_kind": "none",
            "flow_type_is_call": False,
            "pcode_is_call": False,
        })
    ranges = [{"start_rva": entry, "end_rva": format_rva(start + count)}]
    inventory = {
        "entry_rva": entry,
        "kind": "internal",
        "name_mangled": "many",
        "name_demangled": "many",
        "namespace": "",
        "instruction_count": count,
        "decoded_instruction_bytes": count,
        "instruction_ranges": ranges,
    }
    static = {
        "entry_rva": entry,
        "status": "success",
        "instruction_count": count,
        "decoded_instruction_bytes": count,
        "body_ranges": ranges,
        "decoded_instruction_ranges": ranges,
        "instructions": instructions,
    }
    decomp = {"entry_rva": entry, "status": "success", "pseudocode": text}
    return inventory, static, decomp


class IdentityPatchMixin:
    def setUp(self) -> None:
        self._target = unittest.mock.patch(
            "reverse_engineering.renderer_extract.load_target",
            return_value={"sha256": SHA},
        )
        self._meta = unittest.mock.patch(
            "reverse_engineering.renderer_extract.load_ghidra_meta",
            return_value={"program_name": "eu4", "image_base": 1},
        )
        self._target.start()
        self._meta.start()

    def tearDown(self) -> None:
        self._meta.stop()
        self._target.stop()


class ReadOnlyConnectTests(unittest.TestCase):
    def test_missing_database_is_not_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "absent" / "index.sqlite"
            with self.assertRaises(FileNotFoundError):
                index_db.connect_readonly(missing)
            self.assertFalse(missing.parent.exists())

    def test_query_only_rejects_writes(self) -> None:
        fixture = CorpusFixture()
        self.addCleanup(fixture.close)
        fixture.write_index([_function_row(FIXTURE, "Only")])
        conn = index_db.connect_readonly(fixture.exp / "index.sqlite")
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE injected (id INTEGER)")
        conn.close()


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = CorpusFixture()
        self.exact = _function_row(FIXTURE, "__Exact", "Exact::Name()")
        self.other = _function_row(SECOND, "__Other", "Other::Name()")
        self.shared_a = _function_row("0x0000000000004100", "__SharedA", "Shared::Name()")
        self.shared_b = _function_row("0x0000000000004200", "__SharedB", "Shared::Name()")
        self.under = _function_row("0x0000000000004300", "Foo_Bar", "Foo_Bar")
        self.wild = _function_row("0x0000000000004400", "FooXBar", "FooXBar")
        self.percent = _function_row("0x0000000000004500", "rate100%", "rate100%")
        self.percent_other = _function_row("0x0000000000004600", "rate100X", "rate100X")
        parts = [
            _function_row(format_rva(0x5000 + index), f"Part{index}", f"Part{index}")
            for index in range(3)
        ]
        widgets = [
            _function_row(format_rva(0x6000 + index), f"Widget{index}", f"Widget{index}")
            for index in range(3)
        ]
        self.part_exact = _function_row("0x0000000000004700", "Part", "Part")
        functions = [
            self.exact, self.other, self.shared_a, self.shared_b, self.under, self.wild,
            self.percent, self.percent_other, self.part_exact, *parts, *widgets,
        ]
        self.fixture.write_index(functions)
        self.ctx, _plan = self.fixture.context([FIXTURE, SECOND, self.shared_a["entry_rva"]])

    def tearDown(self) -> None:
        self.fixture.close()

    def test_exact_function_name(self) -> None:
        code, doc = _run(self.ctx, ["resolve", "Exact::Name()"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["identity"]["entry_rva"], FIXTURE)
        self.assertEqual(doc["identity"]["name_mangled"], "__Exact")
        self.assertEqual(doc["identity"]["match"], "exact")
        self.assertTrue(doc["identity"]["in_snapshot"])
        self.assertFalse(doc["truncated"])

    def test_exact_name_beats_a_partial_extension(self) -> None:
        code, doc = _run(self.ctx, ["resolve", "Part"])
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(code, 0)
        self.assertEqual([row["entry_rva"] for row in doc["evidence"]["matches"]], ["0x0000000000004700"])

    def test_ambiguous_function_name(self) -> None:
        code, doc = _run(self.ctx, ["resolve", "Shared::Name()"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ambiguous")
        self.assertEqual(doc["identity"], {"query": "Shared::Name()"})
        self.assertEqual(
            {row["entry_rva"] for row in doc["evidence"]["matches"]},
            {"0x0000000000004100", "0x0000000000004200"},
        )
        self.assertFalse(doc["truncated"])

    def test_rva_normalization(self) -> None:
        code, doc = _run(self.ctx, ["resolve", "0x1000"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["identity"]["entry_rva"], FIXTURE)
        self.assertTrue(doc["identity"]["in_snapshot"])

    def test_literal_underscore_and_percent(self) -> None:
        under_code, under = _run(self.ctx, ["resolve", "Foo_B"])
        wild_code, wild = _run(self.ctx, ["resolve", "FooX"])
        percent_code, percent = _run(self.ctx, ["resolve", "100%"])
        self.assertEqual(under_code, wild_code, percent_code)
        self.assertEqual([row["name_demangled"] for row in under["evidence"]["matches"]], ["Foo_Bar"])
        self.assertEqual([row["name_demangled"] for row in wild["evidence"]["matches"]], ["FooXBar"])
        self.assertEqual([row["name_demangled"] for row in percent["evidence"]["matches"]], ["rate100%"])

    def test_ambiguous_result_above_the_bound_is_truncated(self) -> None:
        code, doc = _run(self.ctx, ["resolve", "Widget", "--limit", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ambiguous")
        self.assertTrue(doc["truncated"])
        self.assertEqual(len(doc["evidence"]["matches"]), 2)
        self.assertEqual(doc["evidence"]["match"], "partial")


class PackageTests(IdentityPatchMixin, unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture = CorpusFixture()
        fixture_inv = json.loads(Path(__file__).resolve().parent.joinpath(
            "re_fixtures", "export_mini", "inventory", "functions.jsonl"
        ).read_text().splitlines()[0])
        self.second_inv, self.second_static, self.second_decomp = _simple_static(SECOND, "aabbccdd")
        self.extra = _function_row(EXTRA, "__Extra", "Extra::Only()")
        functions = [fixture_inv, self.second_inv, self.extra]
        self.fixture.write_index(functions)
        self.fixture.write_callsites([_census_row("0x0000000000001005", FIXTURE)])
        self.ctx, self.plan = self.fixture.context([FIXTURE, SECOND])
        self.static_rows = [_valid_static_row(FIXTURE), self.second_static]
        self.decomp_rows = [
            {"entry_rva": FIXTURE, "status": "success", "pseudocode": "void exact();"},
            self.second_decomp,
        ]
        self._seal(self.static_rows, self.decomp_rows)

    def tearDown(self) -> None:
        self.fixture.close()
        super().tearDown()

    def _seal(self, static_rows: list[dict], decomp_rows: list[dict], *, mutate=None) -> Path:
        static_text = _jsonl(static_rows)
        decomp_text = _jsonl(decomp_rows)
        rvas = [FIXTURE, SECOND]
        receipt = renderer_extract.build_receipt(
            static_bytes=static_text.encode(),
            decomp_bytes=decomp_text.encode(),
            batch_id="gfx-batch-00001",
            rvas=rvas,
            plan=self.plan,
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": SHA},
            ghidra_version="12.1.4",
            binary_sha=SHA,
        )
        if mutate is not None:
            mutate(receipt)
        payload = renderer_extract.write_batch_zip(static_text, decomp_text, receipt)
        path = renderer_extract.batch_zip_path(self.fixture.exp, "gfx-batch-00001")
        path.write_bytes(payload)
        return path

    def test_one_function_from_a_valid_package(self) -> None:
        before_db = self.fixture.snapshot()
        path = renderer_extract.batch_zip_path(self.fixture.exp, "gfx-batch-00001")
        before_zip = path.read_bytes()
        code, doc = _run(self.ctx, ["function", "0x1000", "--view", "static"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["identity"]["entry_rva"], FIXTURE)
        self.assertEqual(doc["evidence"]["static_status"], "success")
        self.assertEqual(doc["evidence"]["instructions"][0]["bytes_hex"], "0" * 10)
        self.assertEqual(doc["evidence"]["batch_id"], "gfx-batch-00001")
        self.assertNotIn("pseudocode", doc["evidence"])
        self.assertEqual(path.read_bytes(), before_zip)
        self.assertEqual(self.fixture.snapshot(), before_db)

    def test_selects_the_requested_row_in_a_multi_function_package(self) -> None:
        code, doc = _run(self.ctx, ["function", SECOND, "--view", "static"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["evidence"]["instructions"][0]["bytes_hex"], "aabbccdd")
        self.assertNotEqual(doc["evidence"]["instructions"][0]["instruction_rva"], FIXTURE)

    def test_missing_package_and_missing_function(self) -> None:
        path = renderer_extract.batch_zip_path(self.fixture.exp, "gfx-batch-00001")
        path.unlink()
        missing_code, missing = _run(self.ctx, ["function", FIXTURE])
        self.assertEqual(missing_code, 0)
        self.assertEqual(missing["status"], "unavailable")
        self.assertEqual(missing["reason"], "missing_source")
        path.write_bytes(b"restored-later")
        self._seal(self.static_rows, self.decomp_rows)
        absent_code, absent = _run(self.ctx, ["function", ABSENT])
        outside_code, outside = _run(self.ctx, ["function", EXTRA])
        self.assertEqual(absent_code, outside_code, 0)
        self.assertEqual(absent["status"], "not_found")
        self.assertEqual(outside["status"], "not_in_snapshot")
        self.assertEqual(outside["identity"]["entry_rva"], EXTRA)

    def test_decompilation_failure_keeps_static_evidence(self) -> None:
        decomp = [
            {"entry_rva": FIXTURE, "status": "timeout", "error": "decompiler timeout"},
            self.second_decomp,
        ]
        self._seal(self.static_rows, decomp)
        code, doc = _run(self.ctx, ["function", FIXTURE, "--view", "all"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["evidence"]["decompile_status"], "timeout")
        self.assertEqual(doc["evidence"]["decompile_error"], "decompiler timeout")
        self.assertEqual(doc["evidence"]["static_status"], "success")
        self.assertGreaterEqual(len(doc["evidence"]["instructions"]), 1)

    def test_malformed_package_and_invalid_receipt(self) -> None:
        path = renderer_extract.batch_zip_path(self.fixture.exp, "gfx-batch-00001")
        path.write_bytes(b"this is not a zip")
        code, doc = _run(self.ctx, ["function", FIXTURE])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "identity_error")
        self.assertEqual(doc["reason"], "package")
        self._seal(self.static_rows, self.decomp_rows, mutate=lambda receipt: receipt.__setitem__("static_sha256", "0" * 64))
        code, doc = _run(self.ctx, ["function", FIXTURE])
        self.assertEqual(doc["status"], "identity_error")
        self.assertEqual(doc["reason"], "package")

    def test_binary_identity_mismatch(self) -> None:
        self._seal(self.static_rows, self.decomp_rows, mutate=lambda receipt: receipt.__setitem__("binary_sha256", "b" * 64))
        code, doc = _run(self.ctx, ["function", FIXTURE])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "identity_error")
        self.assertEqual(doc["reason"], "identity")

    def test_instruction_and_decompiler_pages_are_independent(self) -> None:
        text = "DECOMPILE-" * 400
        inventory, static, decomp = _many_static("0x0000000000006100", 30, text)
        self.fixture.write_index([inventory])
        self.fixture.write_callsites([])
        ctx, plan = self.fixture.context([inventory["entry_rva"]])
        static_text = _jsonl([static])
        decomp_text = _jsonl([decomp])
        receipt = renderer_extract.build_receipt(
            static_bytes=static_text.encode(),
            decomp_bytes=decomp_text.encode(),
            batch_id="gfx-batch-00001",
            rvas=[inventory["entry_rva"]],
            plan=plan,
            summary=_summary(),
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": SHA},
            ghidra_version="12.1.4",
            binary_sha=SHA,
        )
        path = renderer_extract.batch_zip_path(self.fixture.exp, "gfx-batch-00001")
        path.write_bytes(renderer_extract.write_batch_zip(static_text, decomp_text, receipt))
        seen: list[str] = []
        offset = 0
        for _ in range(40):
            code, doc = _run(ctx, [
                "function", inventory["entry_rva"], "--view", "static",
                "--static-offset", str(offset), "--max-bytes", "2200",
            ])
            self.assertEqual(code, 0)
            self.assertEqual(doc["status"], "ok")
            self.assertNotIn("pseudocode", doc["evidence"])
            page = [row["instruction_rva"] for row in doc["evidence"]["instructions"]]
            self.assertGreaterEqual(len(page), 1)
            seen.extend(page)
            if not doc["truncated"]:
                break
            self.assertGreater(doc["next_static_offset"], offset)
            offset = doc["next_static_offset"]
        self.assertEqual(seen, [row["instruction_rva"] for row in static["instructions"]])

        parts: list[str] = []
        text_offset = 0
        for _ in range(40):
            _code, doc = _run(ctx, [
                "function", inventory["entry_rva"], "--view", "decompiled",
                "--decompile-offset", str(text_offset), "--static-offset", "1000",
                "--max-bytes", "1800",
            ])
            self.assertNotIn("instructions", doc["evidence"])
            parts.append(doc["evidence"]["pseudocode"])
            if not doc["truncated"]:
                break
            self.assertGreater(doc["next_decompile_offset"], text_offset)
            text_offset = doc["next_decompile_offset"]
        self.assertEqual("".join(parts), text)


class XrefTests(unittest.TestCase):
    def test_direct_caller_external_callee_and_unresolved_indirect(self) -> None:
        fixture = CorpusFixture()
        self.addCleanup(fixture.close)
        functions = [
            _function_row(FIXTURE, "__Target", "Target::Run()"),
            _function_row(SECOND, "__Caller", "Caller::Run()"),
        ]
        edges = [
            {
                "callsite_rva": "0x0000000000002001",
                "caller_rva": SECOND,
                "callee_internal_rva": FIXTURE,
                "callee_external_symbol": None,
                "resolution": "direct_internal",
            },
            {
                "callsite_rva": "0x0000000000001001",
                "caller_rva": FIXTURE,
                "callee_internal_rva": None,
                "callee_external_symbol": "_glUseProgram",
                "resolution": "direct_external",
            },
            {
                "callsite_rva": "0x0000000000001002",
                "caller_rva": FIXTURE,
                "callee_internal_rva": None,
                "callee_external_symbol": None,
                "resolution": "unresolved_indirect",
            },
        ]
        fixture.write_index(functions, edges)
        fixture.write_callsites([{
            "callsite_rva": "0x0000000000001002",
            "caller_rva": FIXTURE,
            "instruction_class": "indirect",
            "flow_instruction_class": "indirect",
            "classifier_status": "agreement",
            "pcode_kind": "CALLIND",
        }])
        ctx, _plan = fixture.context([FIXTURE, SECOND])
        before = fixture.snapshot()
        code, doc = _run(ctx, ["xrefs", "0x1000"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "ok")
        self.assertEqual(doc["evidence"]["incoming"][0]["caller_rva"], SECOND)
        self.assertEqual(doc["evidence"]["incoming"][0]["callee_rva"], FIXTURE)
        self.assertEqual(doc["evidence"]["incoming"][0]["resolution"], "direct_internal")
        outgoing = {row["callsite_rva"]: row for row in doc["evidence"]["outgoing"]}
        self.assertEqual(outgoing["0x0000000000001001"]["external_symbol"], "_glUseProgram")
        self.assertFalse(outgoing["0x0000000000001001"]["unresolved"])
        self.assertTrue(outgoing["0x0000000000001002"]["unresolved"])
        self.assertEqual(outgoing["0x0000000000001002"]["resolution"], "unresolved_indirect")
        self.assertEqual(doc["evidence"]["callsites"][0]["instruction_class"], "indirect")
        self.assertTrue(doc["evidence"]["callsites"][0]["unresolved"])
        self.assertIn("tail call", " ".join(doc["evidence"]["limits"]))
        self.assertEqual(fixture.snapshot(), before)


class ProvenanceTests(unittest.TestCase):
    def test_inconsistent_batch_plan_is_an_identity_error(self) -> None:
        fixture = CorpusFixture()
        self.addCleanup(fixture.close)
        fixture.write_index([_function_row(FIXTURE, "Only")])
        _ctx, plan = fixture.context([FIXTURE, SECOND])
        plan["batches"]["gfx-batch-00001"] = [FIXTURE, "0x0000000000002eee"]
        plan_path = fixture.exp / "renderer_snapshot" / "v1" / "manifest" / "batch_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan))
        parsed = parse_batch_plan(plan_path)
        self.assertEqual(parsed["selected_function_count"], 2)

        def loader(_baseline: str | None):
            try:
                research.assemble_context(fixture.exp, _summary(), plan, BUNDLE, [FIXTURE, SECOND], REV)
            except ReError as exc:
                return None, exc
            return None, ReError("plan was accepted")

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = research.main(["status"], loader=loader)
        doc = json.loads(buf.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "identity_error")
        self.assertEqual(doc["reason"], "identity")

    def test_stale_index_is_rejected_and_sidecars_stay_unchanged(self) -> None:
        fixture = CorpusFixture()
        self.addCleanup(fixture.close)
        fixture.write_index(
            [_function_row(FIXTURE, "__Exact", "Exact::Name()")],
            provenance=_provenance(binary_sha256="stale"),
        )
        ctx, _plan = fixture.context([FIXTURE])
        before = fixture.snapshot()
        self.assertIsNone(before["index.sqlite-wal"])
        self.assertIsNone(before["index.sqlite-shm"])
        code, doc = _run(ctx, ["resolve", "Exact::Name()"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["status"], "identity_error")
        self.assertEqual(doc["reason"], "identity")
        self.assertIn("stale", doc["detail"])
        self.assertEqual(fixture.snapshot(), before)
        digest = hashlib.sha256(before["index.sqlite"] or b"")
        self.assertEqual(hashlib.sha256((fixture.exp / "index.sqlite").read_bytes()).digest(), digest.digest())

    def test_max_bytes_above_the_hard_cap_is_rejected(self) -> None:
        code = research.main(["status", "--max-bytes", str(research.HARD_MAX_BYTES + 1)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
