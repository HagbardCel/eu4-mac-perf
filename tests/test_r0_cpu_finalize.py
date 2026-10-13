import json
import plistlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import autonomous_runner as auto
import cpu_reconcile
import r0_measurement_session as session

ROOT = Path(__file__).resolve().parents[1]


class _FakePm:
    def __init__(self, exit_code_before_stop: int | None) -> None:
        self._exit = exit_code_before_stop

    def poll(self) -> int | None:
        return self._exit


class R0CpuFinalizeTests(unittest.TestCase):
    def test_shutdown_powermetrics_records_running_then_stop_completed(self) -> None:
        with mock.patch.object(auto, "stop_process", autospec=True) as stop:
            state = cpu_reconcile.shutdown_powermetrics(_FakePm(None), 1.0)
            stop.assert_called_once()
        self.assertEqual(state["state_before_stop"], "running")
        self.assertTrue(state["stop_completed"])

    def test_shutdown_failure_marks_stop_incomplete(self) -> None:
        with mock.patch.object(auto, "stop_process", side_effect=OSError("stop failed")):
            state = cpu_reconcile.shutdown_powermetrics(_FakePm(None), 1.0)
        self.assertFalse(state["stop_completed"])
        self.assertIn("stop_error", state)

    def test_early_exit_before_stop_fails_gate(self) -> None:
        state = cpu_reconcile.classify_powermetrics_producer_before_stop(_FakePm(0))
        self.assertEqual(state["state_before_stop"], "unexpected_early_exit")
        manifest = {"cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1}
        cpu = {
            "reconcile_ok": False,
            "cpu_reconciliation_phase": "finalized",
            "process_cpu_sample_count": 1,
            "process_cpu_sidecar_persisted": False,
            "powermetrics_producer": state,
            "tail_finalization_status": {"status": "clean", "trailing_partial_bytes": 0},
        }
        self.assertFalse(cpu_reconcile.cpu_reconcile_gate_passes(cpu, manifest, None))

    def test_sidecar_write_failure_clears_reconcile_ok(self) -> None:
        anchor = {"monotonic_ns": 1_000_000_000, "wall_ns": 2_000_000_000}
        pid = 42
        ts = datetime.fromtimestamp(2.0, tz=timezone.utc)
        payload = {"timestamp": ts, "tasks": [{"pid": pid, "cputime_ms_per_s": 1000.0}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stream.plist"
            path.write_bytes(plistlib.dumps(payload) + b"\0")
            tail = auto.PowerTail(path)
            tail.poll()
            finish = tail.finish()
            provisional = {
                "thread_cpu_ms_per_s": 1000.0,
                "window_start_ns": anchor["monotonic_ns"],
                "window_end_ns": anchor["monotonic_ns"] + 3_000_000_000,
            }
            manifest: dict = {"cpu_reconcile_tolerance_ms_per_s": 50.0}
            producer = {
                "state_before_stop": "running",
                "exit_code_before_stop": None,
                "stop_completed": True,
            }
            with mock.patch.object(
                cpu_reconcile,
                "write_epoch_a_process_cpu_sidecar_atomic",
                side_effect=OSError("disk full"),
            ):
                finalized = cpu_reconcile.persist_finalized_cpu_with_sidecar(
                    Path(temporary),
                    manifest,
                    provisional,
                    tail_samples=tail.samples,
                    tail_finalization=finish,
                    clock_anchor=anchor,
                    pid=pid,
                    powermetrics_producer=producer,
                    pm_stream_path=path,
                )
            self.assertFalse(finalized["reconcile_ok"])
            self.assertFalse(finalized["process_cpu_sidecar_persisted"])
            self.assertFalse((Path(temporary) / "epoch_a_process_cpu_samples.json").exists())
            self.assertNotIn("process_cpu_sidecar_sha256", finalized)

    def test_finalize_epoch_a_cpu_persists_consistent_artifacts(self) -> None:
        anchor = {"monotonic_ns": 1_000_000_000, "wall_ns": 2_000_000_000}
        pid = 42
        ts = datetime.fromtimestamp(2.0, tz=timezone.utc)
        payload = {"timestamp": ts, "tasks": [{"pid": pid, "cputime_ms_per_s": 1000.0}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "stream.plist"
            path.write_bytes(plistlib.dumps(payload) + b"\0")
            tail = auto.PowerTail(path)
            tail.poll()
            provisional = {
                "pid": pid,
                "thread_cpu_ms_per_s": 1000.0,
                "window_start_ns": anchor["monotonic_ns"],
                "window_end_ns": anchor["monotonic_ns"] + 3_000_000_000,
            }
            manifest: dict = {
                "cpu_accounting_contract": cpu_reconcile.CPU_ACCOUNTING_CONTRACT_V1,
                "cpu_reconcile_tolerance_ms_per_s": 50.0,
                "epochs": [{"id": "A", "thread_cpu": {}}],
            }
            producer = {
                "state_before_stop": "running",
                "exit_code_before_stop": None,
                "stop_completed": True,
            }
            session._finalize_epoch_a_cpu(
                root,
                manifest,
                tail,
                anchor,
                pid,
                provisional,
                producer,
                path,
            )
            file_cpu = json.loads((root / "epoch_a_thread_cpu.json").read_text())
            sidecar = json.loads((root / "epoch_a_process_cpu_samples.json").read_text())
            self.assertEqual(file_cpu, manifest["epoch_a_thread_cpu"])
            self.assertEqual(
                sidecar["sample_count"],
                file_cpu["process_cpu_sample_count"],
            )
            self.assertEqual(sidecar["reconcile_ok"], file_cpu["reconcile_ok"])
            self.assertAlmostEqual(
                sidecar["recomputed_mean_cputime_ms_per_s"],
                file_cpu["process_cpu_ms_per_s"],
            )
            self.assertEqual(manifest["epoch_a_process_cpu_samples_path"], "epoch_a_process_cpu_samples.json")
            self.assertIn("process_cpu_sidecar_sha256", file_cpu)
            epoch_a = next(e for e in manifest["epochs"] if e["id"] == "A")
            self.assertEqual(epoch_a["thread_cpu"], file_cpu)
            self.assertTrue(cpu_reconcile.cpu_reconcile_gate_passes(file_cpu, manifest, root))

    def test_persist_failed_finalize_does_not_clear_session_error(self) -> None:
        manifest = {
            "status": "incomplete",
            "error": "drawable probe failed",
            "epochs": [],
        }
        prior_error = manifest["error"]
        manifest["cpu_finalization_error"] = "finalize failed"
        failed_cpu = {
            "cpu_reconciliation_phase": "finalized",
            "reconcile_ok": False,
            "process_cpu_sidecar_persisted": False,
        }
        manifest["epoch_a_thread_cpu"] = failed_cpu
        self.assertEqual(manifest.get("error"), prior_error)
        self.assertFalse(failed_cpu["reconcile_ok"])


if __name__ == "__main__":
    unittest.main()
