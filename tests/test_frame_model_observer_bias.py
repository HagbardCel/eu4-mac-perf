import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import eu4_frame_model as model  # noqa: E402
import frame_model_observer_bias as observer_bias  # noqa: E402


class ObserverBiasFitTests(unittest.TestCase):
    def test_fit_slope_zero_intercept_recovers_known_rate(self):
        slope = 12.5
        points = [(100.0, slope * 100), (200.0, slope * 200), (400.0, slope * 400)]
        fit = observer_bias.fit_slope_zero_intercept(points)
        self.assertEqual(fit["status"], "ok")
        self.assertAlmostEqual(fit["slope_ns_per_op"], slope, places=6)
        self.assertAlmostEqual(fit["residual_std_ns"], 0.0, places=6)
        self.assertIsNotNone(fit["slope_se_ns_per_op"])

    def test_bias_adjust_uses_decomposition_only(self):
        bias_model = observer_bias.build_bias_model(
            [
                {
                    "primitive_id": "counters_incremental",
                    "role": observer_bias.ROLE_AGGREGATE_CONTROL,
                    "unit": "published_frame",
                    "low_stage": "reference",
                    "high_stage": "counters",
                    "fit": {"slope_ns_per_op": 1000.0},
                },
                {
                    "primitive_id": "scope_pair_clocks",
                    "role": observer_bias.ROLE_DECOMPOSITION,
                    "unit": "scope_pair",
                    "low_stage": "counters_lite",
                    "high_stage": "counters",
                    "fit": {"slope_ns_per_op": 10.0},
                },
            ],
        )
        result = observer_bias.bias_adjust_inclusive_cpu(
            1_000_000,
            {
                "counters_incremental": 4,
                "scope_pair_clocks": 50,
            },
            bias_model,
        )
        self.assertEqual(result["estimated_bias_ns"], 500.0)
        self.assertEqual(result["bias_adjusted_cpu_ns"], 999_500.0)

    def test_forensic_slopes_excluded_from_default_bias_adjust(self):
        bias_model = observer_bias.build_bias_model(
            [
                {
                    "primitive_id": "timed_gl_sample",
                    "role": observer_bias.ROLE_FORENSIC_ADDON,
                    "unit": "timed_gl_sample",
                    "low_stage": "sampled",
                    "high_stage": "sampled",
                    "fit": {"slope_ns_per_op": 100.0},
                },
            ],
        )
        result = observer_bias.bias_adjust_inclusive_cpu(1_000_000, {"timed_gl_sample": 10}, bias_model)
        self.assertEqual(result["estimated_bias_ns"], 0.0)

    def test_reconcile_counters_ignores_forensic_primitives(self):
        bias_model = observer_bias.build_bias_model(
            [
                {
                    "primitive_id": "counters_incremental",
                    "role": observer_bias.ROLE_AGGREGATE_CONTROL,
                    "unit": "published_frame",
                    "low_stage": "reference",
                    "high_stage": "counters",
                    "fit": {"slope_ns_per_op": 1000.0},
                },
                {
                    "primitive_id": "scope_pair_clocks",
                    "role": observer_bias.ROLE_DECOMPOSITION,
                    "unit": "scope_pair",
                    "low_stage": "counters_lite",
                    "high_stage": "counters",
                    "fit": {"slope_ns_per_op": 10.0},
                },
                {
                    "primitive_id": "gpu_timestamp_segment",
                    "role": observer_bias.ROLE_FORENSIC_ADDON,
                    "unit": "gpu_timestamp_segment",
                    "low_stage": "sampled",
                    "high_stage": "sampled",
                    "fit": {"slope_ns_per_op": 500.0},
                },
            ],
        )
        report = observer_bias.reconcile_counters_decomposition(
            bias_model,
            operation_counts={
                "scope_pair_clocks": 10.0,
                "gpu_timestamp_segment": 100.0,
            },
            published_frames=4.0,
        )
        self.assertEqual(report["decomposed_sum_ns"], 100.0)

    def test_count_draw_timed_samples_from_f_rows(self):
        trace = [
            ["F"] + ["0"] * 14 + ["3"],
            ["F"] + ["0"] * 14 + ["2"],
        ]
        self.assertEqual(observer_bias.count_draw_timed_samples_from_trace(trace), 5)

    def test_bias_aware_interval_uses_slope_se_per_op(self):
        interval = observer_bias.bias_aware_interval(
            1_000_000.0,
            slope_ns_per_op=10.0,
            slope_se_ns_per_op=1.0,
            operation_count=50,
            z=1.96,
        )
        self.assertEqual(interval["bias_subtracted_ns"], 500.0)
        self.assertAlmostEqual(interval["margin_ns"], 1.96 * 1.0 * 50)


class ObserverBiasSpecTests(unittest.TestCase):
    def test_scope_pair_direction_counters_lite_to_counters(self):
        spec = next(p for p in observer_bias.PRIMITIVE_SPECS if p["primitive_id"] == "scope_pair_clocks")
        self.assertEqual(spec["low_stage"], "counters_lite")
        self.assertEqual(spec["high_stage"], "counters")

    def test_flush_direction_deferred_to_immediate(self):
        spec = next(p for p in observer_bias.PRIMITIVE_SPECS if p["primitive_id"] == "per_frame_counter_flush")
        self.assertEqual(spec["low_stage"], "counters_lite_deferred_flush")
        self.assertEqual(spec["high_stage"], "counters_lite")

    def test_gpu_timestamp_uses_sampled_window_with_records_off(self):
        spec = next(p for p in observer_bias.PRIMITIVE_SPECS if p["primitive_id"] == "gpu_timestamp_segment")
        self.assertEqual(spec["low_stage"], "sampled")
        self.assertEqual(spec["high_stage"], "sampled")
        self.assertEqual(spec["role"], observer_bias.ROLE_FORENSIC_ADDON)
        self.assertEqual(spec["low_env"]["EU4_TEST_FORENSIC_RECORDS"], "0")

    def test_timed_gl_sample_isolated_on_sampled_stage(self):
        spec = next(p for p in observer_bias.PRIMITIVE_SPECS if p["primitive_id"] == "timed_gl_sample")
        self.assertEqual(spec["low_stage"], "sampled")
        self.assertEqual(spec["high_env"]["EU4_TEST_DRAW_TIMED_SAMPLES"], "1")

    def test_telemetry_suffixes_are_unique_per_side(self):
        low = observer_bias.telemetry_suffix("scope_pair_clocks", 2, 3, "low")
        high = observer_bias.telemetry_suffix("scope_pair_clocks", 2, 3, "high")
        self.assertNotEqual(low, high)
        self.assertIn("obs-scope_pair_clocks-s2-r3", low)


class ObserverBiasMetadataTests(unittest.TestCase):
    def test_offline_policy_versions_recognize_observer_bias_payload(self):
        workloads = {
            "calibration_version": observer_bias.OBSERVER_BIAS_CALIBRATION_VERSION,
            "recipe": {"name": "mesh"},
            "scale_factors": [1, 2, 4],
            "repetitions": 5,
            "primitive_catalog": [{"primitive_id": "scope_pair_clocks"}],
        }
        versions = model._offline_policy_versions(workloads)
        self.assertEqual(
            versions["observer_bias_calibration"],
            observer_bias.OBSERVER_BIAS_CALIBRATION_VERSION,
        )

    def test_workloads_payload_prefers_observer_bias_block(self):
        block = {"calibration_version": observer_bias.OBSERVER_BIAS_CALIBRATION_VERSION, "status": "complete"}
        payload = model._offline_workloads_payload({"observer_bias_calibration": block})
        self.assertEqual(payload, block)


class ObserverBiasCliTests(unittest.TestCase):
    def test_run_observer_bias_calibration_requires_gl(self):
        with self.assertRaises(base.BenchmarkError):
            model.run_observer_bias_calibration(run_gl=False)


class IntrusiveAnalyzeBiasTests(unittest.TestCase):
    def test_analyze_surfaces_embedded_calibration(self):
        manifest = {
            "report_kind": "intrusive_diagnostic",
            "measurement_contract": {"intrusive": True},
            "observer_bias_calibration": {
                "calibration_version": observer_bias.OBSERVER_BIAS_CALIBRATION_VERSION,
                "bias_model": {
                    "additive_slopes": {
                        "scope_pair_clocks": {"slope_ns_per_op": 42.0, "unit": "scope_pair"},
                    },
                    "aggregate_slopes": {},
                },
                "bias_table": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            output = model._analyze_intrusive_diagnostic_run(Path(directory), manifest)
        self.assertIsNotNone(output["observer_bias_calibration"])
        self.assertEqual(
            output["observer_bias_calibration"]["additive_slopes"]["scope_pair_clocks"]["slope_ns_per_op"],
            42.0,
        )


class ObserverBiasWorkloadIdentityTests(unittest.TestCase):
    def test_paired_stage_passes_distinct_telemetry_suffixes(self):
        root = Path("/tmp/eu4-observer-bias-test")
        recipe = {"name": "mesh", "draws": 100, "path": "/dev/null"}
        calls: list[str] = []

        def fake_stage(*_args, **kwargs):
            calls.append(kwargs.get("telemetry_suffix", ""))
            return {"cpu_ns": 1000}, [["Q", "1", "1", "0", "0", "5"]]

        with mock.patch.object(model, "_offline_workload_stage", side_effect=fake_stage):
            model._observer_bias_paired_stage_cpu(
                root,
                recipe,
                primitive_id="scope_pair_clocks",
                low_stage="counters_lite",
                high_stage="counters",
                scale=1,
                repetition=0,
            )
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertTrue(all(call.startswith("obs-scope_pair_clocks-s1-r0-") for call in calls))


if __name__ == "__main__":
    unittest.main()
