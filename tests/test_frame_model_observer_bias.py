import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_bias_adjust_subtracts_components(self):
        model = {
            "calibration_version": observer_bias.OBSERVER_BIAS_CALIBRATION_VERSION,
            "slopes": {
                "tls_event_accounting": {"slope_ns_per_op": 10.0, "unit": "published_frame"},
            },
        }
        result = observer_bias.bias_adjust_inclusive_cpu(
            1_000_000,
            {"tls_event_accounting": 50},
            model,
        )
        self.assertEqual(result["estimated_bias_ns"], 500.0)
        self.assertEqual(result["bias_adjusted_cpu_ns"], 999_500.0)


class ObserverBiasSweepTests(unittest.TestCase):
    def test_differential_sweep_scale_zero_has_zero_delta(self):
        def measure(scale: int) -> tuple[int, int]:
            return (1000, 2000 if scale > 0 else 1000)

        summary = observer_bias.run_differential_sweep(
            "demo",
            unit="op",
            measure_low_high=measure,
            operations_at_scale=lambda scale: scale * 10,
        )
        zero = summary["scale_rows"][0]
        self.assertEqual(zero["scale"], 0)
        self.assertEqual(zero["cpu_delta_ns"], 0)


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
                    "slopes": {
                        "reference_frame_hooks": {"slope_ns_per_op": 42.0, "unit": "published_frame"},
                    },
                },
                "bias_table": [],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            output = model._analyze_intrusive_diagnostic_run(Path(directory), manifest)
        self.assertIsNotNone(output["observer_bias_calibration"])
        self.assertEqual(
            output["observer_bias_calibration"]["slopes"]["reference_frame_hooks"]["slope_ns_per_op"],
            42.0,
        )


if __name__ == "__main__":
    unittest.main()
