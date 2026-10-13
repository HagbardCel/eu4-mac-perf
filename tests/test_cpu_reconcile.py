import json
import plistlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import autonomous_runner as auto
import cpu_reconcile
import r0_measurement_session as session

ROOT = Path(__file__).resolve().parents[1]
OCT12_SIDECAR = Path(__file__).parent / "fixtures/oct12_epoch_a_process_cpu_samples.json"
OCT12_RUN = ROOT / "results/20261012T210806Z-r0-measurement"


def _running_producer() -> dict:
    return {
        "state_before_stop": "running",
        "exit_code_before_stop": None,
        "stop_completed": True,
    }


def _gate_cpu() -> dict:
    return {
        "reconcile_ok": True,
        "cpu_reconciliation_phase": "finalized",
        "main_thread_mapping_status": "verified",
        "tail_finalization_status": {"status": "clean", "trailing_partial_bytes": 0},
        "process_cpu_sample_count": 27,
        "process_cpu_sidecar_persisted": True,
        "powermetrics_producer": _running_producer(),
        "thread_cpu_ms_per_s": 100.0,
        "window_start_ns": 0,
        "window_end_ns": 1,
    }


class CpuReconcileTests(unittest.TestCase):
    def test_oct12_sidecar_retrospective_means(self) -> None:
        sidecar = json.loads(OCT12_SIDECAR.read_text())
        means = cpu_reconcile.retrospective_means_from_sidecar(sidecar)
        self.assertEqual(means["acquisition_sample_count"], 27)
        self.assertEqual(means["retrospective_sample_count"], 29)
        self.assertAlmostEqual(means["acquisition_mean_cputime_ms_per_s"], 1065.2781481481481)
        self.assertAlmostEqual(means["retrospective_mean_cputime_ms_per_s"], 1065.4555172413793)
        thread_cpu = sidecar["manifest_thread_cpu_ms_per_s"]
        self.assertAlmostEqual(
            means["retrospective_mean_cputime_ms_per_s"] - thread_cpu,
            0.3206939374974809,
            places=9,
        )

    def test_finalize_contract_gate_requires_phase(self) -> None:
        manifest = {"cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1}
        cpu = {"reconcile_ok": True}
        self.assertFalse(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, None))

    def test_tail_gate_rejects_missing_finalization_status(self) -> None:
        manifest = {"cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1}
        cpu = {
            "reconcile_ok": True,
            "cpu_reconciliation_phase": "finalized",
            "process_cpu_sample_count": 5,
            "process_cpu_sidecar_persisted": True,
            "powermetrics_producer": _running_producer(),
        }
        self.assertFalse(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, None))

    def test_tail_gate_rejects_bogus_status(self) -> None:
        manifest = {"cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1}
        cpu = _gate_cpu()
        cpu["tail_finalization_status"] = {"status": "bogus", "trailing_partial_bytes": 0}
        self.assertFalse(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, None))

    def test_reconcile_cpu_without_tail_fails(self) -> None:
        thread_report = {
            "thread_cpu_ms_per_s": 100.0,
            "window_start_ns": 0,
            "window_end_ns": 1,
        }
        result = cpu_reconcile.reconcile_cpu(
            thread_report,
            powermetrics_samples=[],
            clock_anchor={"monotonic_ns": 0, "wall_ns": 0},
            pid=1,
            reconcile_tolerance_ms_per_s=50.0,
            tail_finalization=None,
            powermetrics_producer=_running_producer(),
        )
        self.assertFalse(result["reconcile_ok"])

    def test_sidecar_arithmetic_invariants(self) -> None:
        observations = [
            {"monotonic_ns": 1, "cputime_ms_per_s": 1000.0},
            {"monotonic_ns": 2, "cputime_ms_per_s": 1020.0},
        ]
        mean, count = cpu_reconcile.mean_from_observations(observations)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(mean, 1010.0)

    def test_legacy_oct12_gate_without_contract(self) -> None:
        manifest = {"schema": "r0_measurement_session_v1"}
        cpu = {"reconcile_ok": True, "main_thread_mapping_status": "verified"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / cpu_reconcile.LEGACY_OCT12_RUN_DIR
            root.mkdir()
            self.assertTrue(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, root))

    def test_legacy_oct12_fingerprint_on_requalify_copy_dir(self) -> None:
        manifest = {
            "epoch_a_thread_cpu": {
                "window_start_ns": 378384359061916,
                "window_end_ns": 378414600898250,
                "main_thread_id": 6650541,
                "pid": 98200,
                "reconcile_ok": True,
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "working-copy"
            root.mkdir()
            self.assertTrue(cpu_reconcile.legacy_cpu_gate_eligible(manifest, root))

    def test_delayed_plist_increases_sample_count(self) -> None:
        anchor = {"monotonic_ns": 1_000_000_000, "wall_ns": 2_000_000_000}
        pid = 42

        def plist_at(ms_per_s: float, wall_offset_s: float) -> bytes:
            ts = datetime.fromtimestamp(2.0 + wall_offset_s, tz=timezone.utc)
            payload = {
                "timestamp": ts,
                "tasks": [{"pid": pid, "cputime_ms_per_s": ms_per_s}],
            }
            return plistlib.dumps(payload)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stream.plist"
            first = plist_at(1000.0, 0.0) + b"\0"
            second = plist_at(1100.0, 1.0) + b"\0"
            path.write_bytes(first)
            tail = auto.PowerTail(path)
            tail.poll()
            thread_report = {
                "thread_cpu_ms_per_s": 1050.0,
                "window_start_ns": anchor["monotonic_ns"],
                "window_end_ns": anchor["monotonic_ns"] + 3_000_000_000,
            }
            producer = _running_producer()
            first_pass = cpu_reconcile.reconcile_cpu(
                thread_report,
                powermetrics_samples=tail.samples,
                clock_anchor=anchor,
                pid=pid,
                reconcile_tolerance_ms_per_s=50.0,
                tail_finalization={"status": "clean", "trailing_partial_bytes": 0},
                powermetrics_producer=producer,
            )
            self.assertEqual(first_pass["process_cpu_sample_count"], 1)

            path.write_bytes(first + second)
            tail = auto.PowerTail(path)
            tail.poll()
            finish = tail.finish()
            second_pass = cpu_reconcile.reconcile_cpu(
                thread_report,
                powermetrics_samples=tail.samples,
                clock_anchor=anchor,
                pid=pid,
                reconcile_tolerance_ms_per_s=50.0,
                tail_finalization=finish,
                powermetrics_producer=producer,
            )
            self.assertEqual(second_pass["process_cpu_sample_count"], 2)
            self.assertGreater(second_pass["process_cpu_ms_per_s"], first_pass["process_cpu_ms_per_s"])

    def test_manifest_cpu_round_trip_consistency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {
                "schema": "r0_measurement_session_v1",
                "cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1,
                "epochs": [{"id": "A", "thread_cpu": {"thread_cpu_ms_per_s": 1.0}}],
            }
            cpu = _gate_cpu()
            session._persist_epoch_a_cpu(root, manifest, cpu)
            session.write_manifest(root / "manifest.json", manifest)
            loaded = json.loads((root / "manifest.json").read_text())
            file_cpu = json.loads((root / "epoch_a_thread_cpu.json").read_text())
            epoch_cpu = next(e["thread_cpu"] for e in loaded["epochs"] if e["id"] == "A")
            self.assertEqual(file_cpu, loaded["epoch_a_thread_cpu"])
            self.assertEqual(epoch_cpu, file_cpu)

    @unittest.skipUnless((OCT12_RUN / "manifest.json").is_file(), "missing Oct12 acquisition capsule")
    def test_oct12_requalify_cpu_legacy_gate(self) -> None:
        manifest = json.loads((OCT12_RUN / "manifest.json").read_text())
        cpu = manifest.get("epoch_a_thread_cpu", {})
        self.assertTrue(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, OCT12_RUN))


if __name__ == "__main__":
    unittest.main()
