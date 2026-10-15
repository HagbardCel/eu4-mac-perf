import argparse
import hashlib
import json
import shutil
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from reverse_engineering import renderer_scope
from reverse_engineering.manifest import inventory_bundle_digest, inventory_digest
from reverse_engineering.paths import load_target

COMMITTED_SCOPE = Path(__file__).resolve().parents[1] / "analysis" / "renderer-corpus" / "scope" / "v1"
FIXTURE_EXPORT = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CommittedScopeTests(unittest.TestCase):
    def test_committed_scope_v1_hashes_and_counts(self) -> None:
        summary = json.loads((COMMITTED_SCOPE / "manifest-summary.json").read_text())
        self.assertEqual(summary["selected_internal_functions"], 4450)
        self.assertEqual(summary["deep_shortlist_functions"], 260)
        self.assertEqual(summary["graphics_boundary_thunks"], 44)
        expected_hashes = renderer_scope.load_scope_hashes(COMMITTED_SCOPE)
        for name in renderer_scope.SCOPE_HASH_FILES:
            actual = renderer_scope.sha256_file(COMMITTED_SCOPE / name)
            self.assertEqual(actual, expected_hashes[name])
        self.assertEqual(
            renderer_scope.sha256_file(COMMITTED_SCOPE / "renderer-functions.jsonl"),
            summary["selection_manifest_sha256"],
        )


class BatchPlanPublicationTests(unittest.TestCase):
    def test_identical_plan_is_no_op_and_different_batch_size_fails(self) -> None:
        plan_a = {
            "schema": 1,
            "baseline_revision": "t",
            "selection_manifest_sha256": "a",
            "inventory_bundle_digest": "b",
            "batch_size": 100,
            "selected_function_count": 2,
            "batches": {"gfx-batch-00001": ["0x1", "0x2"]},
        }
        plan_b = {**plan_a, "batch_size": 200, "batches": {"gfx-batch-00001": ["0x1", "0x2"]}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch_plan.json"
            self.assertEqual(renderer_scope.publish_batch_plan(path, plan_a), "created")
            self.assertEqual(renderer_scope.publish_batch_plan(path, plan_a), "unchanged")
            with self.assertRaises(renderer_scope.ReError):
                renderer_scope.publish_batch_plan(path, plan_b)

    def test_malformed_existing_plan_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch_plan.json"
            path.write_text("{}\n")
            plan = {
                "schema": 1,
                "baseline_revision": "t",
                "selection_manifest_sha256": "a",
                "inventory_bundle_digest": "b",
                "batch_size": 1,
                "selected_function_count": 1,
                "batches": {"gfx-batch-00001": ["0x0000000000001000"]},
            }
            with self.assertRaises(renderer_scope.ReError):
                renderer_scope.publish_batch_plan(path, plan)


class CensusDriftTests(unittest.TestCase):
    def _build_scope_tree(self, tmp: Path, callsites_bytes: bytes) -> tuple[Path, Path]:
        export = tmp / "exports" / "fixture-renderer"
        inv = export / "inventory"
        inv.mkdir(parents=True)
        shutil.copytree(FIXTURE_EXPORT / "inventory", inv, dirs_exist_ok=True)
        audits = export / "audits"
        audits.mkdir(parents=True)
        (audits / "callsites-v1.jsonl").write_bytes(callsites_bytes)
        fn_digest = inventory_digest(inv / "functions.jsonl")
        bundle_digest = inventory_bundle_digest(inv)
        target = load_target()
        source_inputs = {
            "inventory/functions.jsonl": fn_digest,
            "inventory/call_edges.jsonl": renderer_scope.sha256_file(inv / "call_edges.jsonl"),
            "audits/callsites-v1.jsonl": _sha256_bytes(callsites_bytes),
        }
        scope = tmp / "scope"
        scope.mkdir()
        row = {
            "entry_rva": "0x0000000000001000",
            "kind": "internal",
            "name_mangled": "__ZN13SShaderOpenGL6SetAllEv",
            "name_demangled": "SShaderOpenGL::SetAll()",
            "namespace": "",
            "instruction_count": 2,
            "decoded_instruction_bytes": 8,
            "instruction_ranges": [{"start_rva": "0x0000000000001000", "end_rva": "0x0000000000001008"}],
            "tier": "core",
            "domain": "backend",
            "stage2_deep_candidate": True,
            "census_callsites": 0,
            "pcode_callind_sites": 0,
            "flow_only_sites": 0,
            "missing_indirect_sites": [],
        }
        inv_row = json.loads((inv / "functions.jsonl").read_text().splitlines()[0])
        self.assertEqual(renderer_scope.inventory_identity_row(inv_row), renderer_scope.inventory_identity_row(row))
        manifest_text = json.dumps(row, sort_keys=True) + "\n"
        (scope / "renderer-functions.jsonl").write_text(manifest_text)
        (scope / "renderer-entry-rvas.txt").write_text("0x0000000000001000\n")
        (scope / "stage2-deep-targets.jsonl").write_text(manifest_text)
        (scope / "stage2-deep-entry-rvas.txt").write_text("0x0000000000001000\n")
        (scope / "graphics-boundary-thunks.jsonl").write_text(
            json.dumps(
                {
                    "entry_rva": "0x0000000000003000",
                    "instruction_count": 1,
                    "kind": "thunk",
                    "name_mangled": "stub_glDrawElements",
                    "status": "boundary_only",
                }
            )
            + "\n"
        )
        for stub in ("README.md", "IMPLEMENTATION_PLAN.md", "renderer-functions.tsv"):
            (scope / stub).write_text(stub + "\n")
        summary = {
            "schema": 1,
            "baseline_revision": "fixture-renderer",
            "binary_sha256": target["sha256"],
            "ghidra_version": "12.1.4",
            "selected_internal_functions": 1,
            "deep_shortlist_functions": 1,
            "graphics_boundary_thunks": 1,
            "inventory_digest": fn_digest,
            "inventory_bundle_digest": bundle_digest,
            "selection_manifest_sha256": _sha256_bytes(manifest_text.encode()),
            "rva_list_sha256": _sha256_bytes(b"0x0000000000001000\n"),
            "deep_rva_list_sha256": _sha256_bytes(b"0x0000000000001000\n"),
            "source_inputs_sha256": source_inputs,
            "tiers": {"core": 1},
            "subsystem_counts": {"backend": 1},
            "decoded_instructions_selected": 2,
            "decoded_bytes_selected": 8,
            "all_92_gfx_helpers_present": False,
        }
        (scope / "manifest-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        hash_lines = []
        for name in renderer_scope.SCOPE_HASH_FILES:
            digest = renderer_scope.sha256_file(scope / name)
            hash_lines.append(f"{digest}  {name}")
        (scope / "scope-v1.sha256").write_text("\n".join(hash_lines) + "\n")
        (export / "provenance.json").write_text(
            json.dumps(
                {
                    "baseline_revision": "fixture-renderer",
                    "binary_sha256": target["sha256"],
                    "ghidra_version": "12.1.4",
                    "inventory_digest": fn_digest,
                    "inventory_bundle_digest": bundle_digest,
                }
            )
            + "\n"
        )
        return scope, export

    def test_census_drift_fails_before_prepare(self) -> None:
        good = b'{"callsite_rva":"0x1"}\n'
        bad = b'{"callsite_rva":"0x2"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            scope, export = self._build_scope_tree(tmp_path, good)
            patches = (
                unittest.mock.patch.object(
                    renderer_scope,
                    "PRIORITY_ENTRIES",
                    (("0x0000000000001000", "SShaderOpenGL::SetAll()"),),
                ),
                unittest.mock.patch.object(renderer_scope, "is_gfx_helper", return_value=False),
            )
            with patches[0], patches[1]:
                renderer_scope.validate_scope(
                    scope_dir=scope, exp=export, baseline_revision="fixture-renderer"
                )
            (export / "audits" / "callsites-v1.jsonl").write_bytes(bad)
            with patches[0], patches[1]:
                with self.assertRaises(renderer_scope.ReError):
                    renderer_scope.validate_scope(
                        scope_dir=scope, exp=export, baseline_revision="fixture-renderer"
                    )
            with unittest.mock.patch(
                "reverse_engineering.renderer_scope.binary_sha256",
                return_value=load_target()["sha256"],
            ):
                with unittest.mock.patch(
                    "reverse_engineering.renderer_scope.local_binary_root",
                    return_value=tmp_path,
                ):
                    with patches[0], patches[1]:
                        with self.assertRaises(renderer_scope.ReError):
                            renderer_scope.cmd_prepare(
                                argparse.Namespace(
                                    scope_dir=str(scope),
                                    baseline_revision="fixture-renderer",
                                    batch_size=100,
                                )
                            )


class PrepareFixtureTests(unittest.TestCase):
    def test_prepare_writes_plan_and_leaves_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            census = CensusDriftTests()
            scope, export = census._build_scope_tree(tmp_path, b'{"callsite_rva":"0x1"}\n')
            provenance_before = (export / "provenance.json").read_text()
            with unittest.mock.patch("reverse_engineering.renderer_scope.binary_sha256", return_value=load_target()["sha256"]):
                with unittest.mock.patch("reverse_engineering.renderer_scope.local_binary_root", return_value=tmp_path):
                    with unittest.mock.patch.object(renderer_scope, "PRIORITY_ENTRIES", (("0x0000000000001000", "SShaderOpenGL::SetAll()"),)):
                        with unittest.mock.patch.object(renderer_scope, "is_gfx_helper", return_value=False):
                            rc = renderer_scope.cmd_prepare(
                                argparse.Namespace(
                                    scope_dir=str(scope),
                                    baseline_revision="fixture-renderer",
                                    batch_size=1,
                                )
                            )
            self.assertEqual(rc, 0)
            plan_path = export / "renderer_snapshot" / "v1" / "manifest" / "batch_plan.json"
            self.assertTrue(plan_path.is_file())
            self.assertEqual((export / "provenance.json").read_text(), provenance_before)


if __name__ == "__main__":
    unittest.main()
