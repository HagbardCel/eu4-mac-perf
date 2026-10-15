import hashlib
import io
import json
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

from reverse_engineering import renderer_extract
from reverse_engineering.paths import ReError

FIXTURE_INV = Path(__file__).resolve().parent / "re_fixtures" / "export_mini" / "inventory"


def _minimal_static_row(entry: str = "0x0000000000001000", ins_count: int = 2, nbytes: int = 8) -> dict:
    end = int(entry, 16) + nbytes
    return {
        "entry_rva": entry,
        "status": "success",
        "instruction_count": ins_count,
        "decoded_instruction_bytes": nbytes,
        "instruction_ranges": [{"start_rva": entry, "end_rva": f"0x{end:016x}"}],
        "decoded_instruction_ranges": [{"start_rva": entry, "end_rva": f"0x{end:016x}"}],
        "instructions": [
            {
                "instruction_rva": entry,
                "census_call": False,
            },
            {
                "instruction_rva": f"0x{int(entry, 16) + 5:016x}",
                "census_call": True,
            },
        ],
    }


class ZipPackageTests(unittest.TestCase):
    def _plan(self) -> dict:
        return {
            "baseline_revision": "t",
            "inventory_bundle_digest": "b",
            "batches": {"gfx-batch-00001": ["0x0000000000001000"]},
            "selected_function_count": 1,
        }

    def _inventory(self) -> dict:
        row = json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])
        return {row["entry_rva"]: row}

    def test_missing_zip_member_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(renderer_extract.ZIP_STATIC, "{}\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_bytes(buf.getvalue())
            with self.assertRaises(ReError):
                renderer_extract.read_zip_package(path)

    def test_receipt_hash_mismatch_rejected(self) -> None:
        static = json.dumps(_minimal_static_row()) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=self._plan(),
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        receipt["static_sha256"] = "0" * 64
        with self.assertRaises(ReError):
            renderer_extract.validate_batch_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                rvas=["0x0000000000001000"],
                inventory=self._inventory(),
                callsites_by_caller={"0x0000000000001000": [{"callsite_rva": f"0x{0x1000 + 5:016x}", "caller_rva": "0x0000000000001000"}]},
                plan=self._plan(),
            )

    def test_callsite_set_mismatch_rejected(self) -> None:
        static_row = _minimal_static_row()
        static_row["instructions"][1]["census_call"] = False
        static = json.dumps(static_row) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "success"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=self._plan(),
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        callsites = [{"callsite_rva": "0x0000000000001005", "caller_rva": "0x0000000000001000"}]
        with self.assertRaises(ReError):
            renderer_extract.validate_batch_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                rvas=["0x0000000000001000"],
                inventory=self._inventory(),
                callsites_by_caller={"0x0000000000001000": callsites},
                plan=self._plan(),
            )

    def test_static_success_decompile_timeout_valid_package(self) -> None:
        inv = self._inventory()
        entry = "0x0000000000001000"
        static_row = _minimal_static_row()
        static_row["instruction_count"] = inv[entry]["instruction_count"]
        static_row["decoded_instruction_bytes"] = inv[entry]["decoded_instruction_bytes"]
        static_row["decoded_instruction_ranges"] = inv[entry]["instruction_ranges"]
        static_row["instruction_ranges"] = inv[entry]["instruction_ranges"]
        static_row["instructions"] = [
            {"instruction_rva": entry, "census_call": False},
            {"instruction_rva": "0x0000000000001005", "census_call": True},
        ]
        static = json.dumps(static_row) + "\n"
        decomp = json.dumps({"entry_rva": entry, "status": "timeout", "error": "decompiler timeout"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=[entry],
            plan=self._plan(),
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        callsites = [{"callsite_rva": "0x0000000000001005", "caller_rva": entry}]
        renderer_extract.validate_batch_package(
            static_text=static,
            decomp_text=decomp,
            receipt=receipt,
            rvas=[entry],
            inventory=inv,
            callsites_by_caller={entry: callsites},
            plan=self._plan(),
        )

    def test_publish_identical_is_no_op(self) -> None:
        payload = b"zip-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            self.assertEqual(renderer_extract.publish_create_only(dest, payload), "created")
            self.assertEqual(renderer_extract.publish_create_only(dest, payload), "unchanged")

    def test_publish_different_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"
            dest.write_bytes(b"a")
            with self.assertRaises(ReError):
                renderer_extract.publish_create_only(dest, b"b")

    def test_concurrent_identical_package_accepted(self) -> None:
        payload = b"same-package"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"

            def fake_link(_src: str, dst: str) -> None:
                Path(dst).write_bytes(payload)
                raise FileExistsError

            with unittest.mock.patch("reverse_engineering.renderer_extract.os.link", fake_link):
                action = renderer_extract.publish_create_only(dest, payload)
            self.assertEqual(action, "unchanged")
            self.assertEqual(dest.read_bytes(), payload)
            staging = [p for p in dest.parent.iterdir() if p.name.startswith(".batch_pkg.")]
            self.assertEqual(staging, [])


class WrongEntryTests(unittest.TestCase):
    def test_wrong_static_rva_order_rejected(self) -> None:
        plan = {"baseline_revision": "t", "inventory_bundle_digest": "b"}
        static = json.dumps(_minimal_static_row("0x0000000000002000")) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "success"}) + "\n"
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=plan,
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        inv = {row["entry_rva"]: row for row in [json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])]}
        with self.assertRaises(ReError):
            renderer_extract.validate_batch_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                rvas=["0x0000000000001000"],
                inventory=inv,
                callsites_by_caller={},
                plan=plan,
            )

    def test_wrong_decompile_entry_rejected(self) -> None:
        entry = "0x0000000000001000"
        inv_row = json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])
        static_row = _minimal_static_row(entry)
        static_row.update(
            {
                "instruction_count": inv_row["instruction_count"],
                "decoded_instruction_bytes": inv_row["decoded_instruction_bytes"],
                "decoded_instruction_ranges": inv_row["instruction_ranges"],
                "instruction_ranges": inv_row["instruction_ranges"],
            }
        )
        static = json.dumps(static_row) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000002000", "status": "success"}) + "\n"
        plan = {"baseline_revision": "t", "inventory_bundle_digest": "b"}
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=[entry],
            plan=plan,
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        callsites = [{"callsite_rva": "0x0000000000001005", "caller_rva": entry}]
        with self.assertRaises(ReError):
            renderer_extract.validate_batch_package(
                static_text=static,
                decomp_text=decomp,
                receipt=receipt,
                rvas=[entry],
                inventory={entry: inv_row},
                callsites_by_caller={entry: callsites},
                plan=plan,
            )

    def test_exporter_hash_mismatch_on_existing_zip(self) -> None:
        static = json.dumps(_minimal_static_row()) + "\n"
        decomp = json.dumps({"entry_rva": "0x0000000000001000", "status": "success"}) + "\n"
        plan = {"baseline_revision": "t", "inventory_bundle_digest": "b"}
        receipt = renderer_extract.build_receipt(
            static_bytes=static.encode(),
            decomp_bytes=decomp.encode(),
            batch_id="gfx-batch-00001",
            rvas=["0x0000000000001000"],
            plan=plan,
            summary={"selection_manifest_sha256": "s"},
            meta={"program_name": "eu4", "image_base": 1, "executable_sha256": "a"},
            ghidra_version="12.1.4",
            binary_sha="a",
        )
        receipt["exporter_script_hashes"] = {"ExportRendererStatic.java": "dead", "ExportDecompilation.java": "dead"}
        payload = renderer_extract.write_batch_zip(static, decomp, receipt)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gfx-batch-00001.zip"
            path.write_bytes(payload)
            static_text, decomp_text, got_receipt = renderer_extract.read_zip_package(path)
            with self.assertRaises(ReError):
                renderer_extract.validate_receipt(got_receipt, static_text.encode(), decomp_text.encode(), plan)

    def test_link_failure_leaves_no_authoritative_zip(self) -> None:
        payload = b"package"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "gfx-batch-00001.zip"

            def boom(_src: str, _dst: str) -> None:
                raise OSError("link failed")

            with unittest.mock.patch("reverse_engineering.renderer_extract.os.link", boom):
                with self.assertRaises(OSError):
                    renderer_extract.publish_create_only(dest, payload)
            self.assertFalse(dest.exists())


class RangeMismatchTests(unittest.TestCase):
    def test_decoded_range_mismatch_rejected(self) -> None:
        inv = json.loads((FIXTURE_INV / "functions.jsonl").read_text().splitlines()[0])
        row = _minimal_static_row()
        row["instruction_count"] = inv["instruction_count"]
        row["decoded_instruction_bytes"] = inv["decoded_instruction_bytes"]
        row["decoded_instruction_ranges"] = [{"start_rva": "0x0000000000001000", "end_rva": "0x0000000000001009"}]
        with self.assertRaises(ReError):
            renderer_extract.validate_static_row(
                row,
                inv,
                [{"callsite_rva": "0x0000000000001005", "caller_rva": inv["entry_rva"]}],
            )


if __name__ == "__main__":
    unittest.main()
