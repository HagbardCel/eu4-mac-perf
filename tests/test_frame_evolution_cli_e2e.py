import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
ANALYZER = ROOT / "analysis/tools/frame_evolution_analyze.py"
ROI_FIXTURE = Path(__file__).resolve().parent / "fixtures/e2e_panel_roi.json"

GEN_BASE = 100
GEN_PRE = 101
GEN_PASSIVE = 102
GEN_POST = 103
GEN_S1 = 104
WIDTH = HEIGHT = 64


def _f_line(
    frame_index: int,
    *,
    mode: int,
    scenario: int,
    generation: int,
    uptime_ns: int,
    crc: int = 1,
    memcmp: int = 1,
    census_attempted: int = 1,
) -> str:
    return (
        f"F,{frame_index},{mode},{scenario},1,1,0,{WIDTH},{HEIGHT},"
        f"{memcmp},{census_attempted},{crc},1,{uptime_ns},0,1,0,99,{generation},0\n"
    )


def _rgba(width: int = WIDTH, height: int = HEIGHT, mutate: tuple[int, int] | None = None) -> bytes:
    data = bytearray(width * height * 4)
    if mutate:
        x, y = mutate
        offset = (y * width + x) * 4
        data[offset] = 255
    return bytes(data)


def build_success_fixture(root: Path) -> dict[str, int]:
    log_lines: list[str] = []
    frame = 1
    for index in range(240):
        log_lines.append(
            _f_line(
                frame,
                mode=1,
                scenario=0,
                generation=GEN_BASE,
                uptime_ns=frame * 20_000_000,
                memcmp=1 if frame > 1 else 0,
            )
        )
        frame += 1
    pre_index = frame
    log_lines.append(
        _f_line(
            pre_index,
            mode=1,
            scenario=0,
            generation=GEN_PRE,
            uptime_ns=5_100_000_000,
            memcmp=0,
        )
    )
    frame += 1
    for _ in range(10):
        log_lines.append(
            _f_line(
                frame,
                mode=0,
                scenario=0,
                generation=GEN_PASSIVE,
                uptime_ns=frame * 20_000_000,
                census_attempted=0,
            )
        )
        frame += 1
    post_index = frame
    log_lines.append(
        _f_line(
            post_index,
            mode=1,
            scenario=1,
            generation=GEN_POST,
            uptime_ns=6_100_000_000,
            memcmp=0,
            crc=2,
        )
    )
    frame += 1
    for offset in range(55):
        log_lines.append(
            _f_line(
                frame,
                mode=1,
                scenario=1,
                generation=GEN_S1,
                uptime_ns=6_200_000_000 + offset * 20_000_000,
                memcmp=1 if offset else 0,
                crc=3,
            )
        )
        frame += 1
    log_text = "".join(log_lines)
    (root / "probe.log").write_text(log_text)
    (root / "r0_probe.log").write_text(log_text)
    events = [
        {"event": "pre_window_end", "event_uptime_ns": 5_200_000_000},
        {"event": "post_window_start", "event_uptime_ns": 6_000_000_000},
    ]
    (root / "events.jsonl").write_text("\n".join(json.dumps(item) for item in events) + "\n")
    captures = root / "captures"
    captures.mkdir()
    from r0_crc64 import crc64_ecma

    pre_pixels = _rgba()
    post_pixels = bytearray(pre_pixels)
    for y in range(32, 39):
        for x in range(32, 39):
            post_pixels[y * WIDTH * 4 + x * 4] = 255
    post_pixels = bytes(post_pixels)
    capture_crc: dict[int, int] = {}
    for generation, pixels, frame_index, scenario in (
        (GEN_PRE, pre_pixels, pre_index, 0),
        (GEN_POST, post_pixels, post_index, 1),
    ):
        crc = crc64_ecma(pixels)
        capture_crc[generation] = crc
        sidecar = {
            "write_ok": True,
            "frame_index": frame_index,
            "control_generation": generation,
            "width": WIDTH,
            "height": HEIGHT,
            "crc64": crc,
            "scenario_id": scenario,
            "uptime_ns": 5_100_000_000 if generation == GEN_PRE else 6_100_000_000,
        }
        (captures / f"capture_{generation}.json").write_text(json.dumps(sidecar))
        (captures / f"capture_{generation}.rgba").write_bytes(pixels)
    updated_lines: list[str] = []
    for line in log_lines:
        parts = line.strip().split(",")
        if parts[0] != "F":
            updated_lines.append(line)
            continue
        generation = int(parts[18])
        if generation in capture_crc:
            parts[11] = str(capture_crc[generation])
            line = ",".join(parts) + "\n"
        updated_lines.append(line)
    log_text = "".join(updated_lines)
    (root / "probe.log").write_text(log_text)
    (root / "r0_probe.log").write_text(log_text)
    return {
        "pre_generation": GEN_PRE,
        "post_generation": GEN_POST,
        "pre_index": pre_index,
        "post_index": post_index,
    }


def run_analyzer(root: Path, *, pre_gen: int, post_gen: int, roi_config: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(BENCHMARK)}
    out = root / "report.json"
    cmd = [
        sys.executable,
        str(ANALYZER),
        str(root / "probe.log"),
        "--output",
        str(out),
        "--events",
        str(root / "events.jsonl"),
        "--capture-dir",
        str(root / "captures"),
        "--pre-generation",
        str(pre_gen),
        "--post-generation",
        str(post_gen),
        "--roi-config",
        str(roi_config),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env, cwd=str(ROOT))
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return json.loads(out.read_text())


class FrameEvolutionCliE2ETests(unittest.TestCase):
    def test_success_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            self.assertTrue(report["qualified_windows"]["0"]["qualified"])
            self.assertTrue(report["qualified_windows"]["1"]["qualified"])
            self.assertEqual(report["positive_control"]["status"], "passed")
            self.assertEqual(report["positive_control"]["pre_generation"], GEN_PRE)
            self.assertEqual(report["positive_control"]["post_generation"], GEN_POST)
            self.assertTrue(report["candidate_for_lossless_skip"])
            json.dumps(report)

    def test_missing_scenario_1_not_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            log_lines = [
                line for line in (root / "probe.log").read_text().splitlines() if f",{GEN_S1}," not in line
            ]
            (root / "probe.log").write_text("\n".join(log_lines) + "\n")
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            self.assertFalse(report["qualified_windows"]["1"].get("qualified", False))

    def test_wrong_generation_insufficient_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            report = run_analyzer(root, pre_gen=GEN_PASSIVE, post_gen=gens["post_generation"], roi_config=ROI_FIXTURE)
            self.assertIn(report["positive_control"]["status"], {"failed", "insufficient_evidence"})

    def test_corrupt_rgba_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            rgba_path = root / "captures" / f"capture_{GEN_PRE}.rgba"
            data = bytearray(rgba_path.read_bytes())
            data[0] = 255
            rgba_path.write_bytes(bytes(data))
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            self.assertIn(report["positive_control"]["status"], {"failed", "insufficient_evidence"})

    def test_outside_roi_change_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            post_pixels = _rgba(mutate=(0, 0))
            from r0_crc64 import crc64_ecma

            crc = crc64_ecma(post_pixels)
            sidecar = json.loads((root / "captures" / f"capture_{GEN_POST}.json").read_text())
            sidecar["crc64"] = crc
            (root / "captures" / f"capture_{GEN_POST}.json").write_text(json.dumps(sidecar))
            (root / "captures" / f"capture_{GEN_POST}.rgba").write_bytes(post_pixels)
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            self.assertIn(report["positive_control"]["status"], {"failed", "insufficient_evidence"})

    def test_missing_post_window_start_insufficient_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            events = [{"event": "pre_window_end", "event_uptime_ns": 5_200_000_000}]
            (root / "events.jsonl").write_text(json.dumps(events[0]) + "\n")
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            self.assertEqual(report["positive_control"]["status"], "insufficient_evidence")

    def test_baseline_gap_not_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            lines = []
            for line in (root / "probe.log").read_text().splitlines():
                parts = line.split(",")
                if parts[0] == "F" and parts[1] == "120":
                    continue
                lines.append(line)
            (root / "probe.log").write_text("\n".join(lines) + "\n")
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            window = report["qualified_windows"]["0"]
            self.assertFalse(window.get("qualified", False) and window.get("frame_count", 0) >= 240)

    def test_acceptance_gates_eligible(self) -> None:
        import r0_measurement_session as session

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gens = build_success_fixture(root)
            report = run_analyzer(
                root, pre_gen=gens["pre_generation"], post_gen=gens["post_generation"], roi_config=ROI_FIXTURE
            )
            manifest = {
                "schema": "r0_measurement_session_v1",
                "epoch_a_thread_cpu": {
                    "reconcile_ok": True,
                    "main_thread_mapping_status": "verified",
                },
                "thread_cpu_preflight": {"status": "ready"},
                "event_clock_alignment": {"status": "passed"},
                "analyzer_results": {
                    "stack": {"exit_code": 0},
                    "frame_evolution": {"exit_code": 0},
                },
                "drawable_capability": {"status": "supported"},
                "frame_evolution_report": report,
                "acquisition_generations": {"baseline": GEN_BASE, "scenario_1_census": GEN_S1},
                "stack_report": {
                    "functional_caller": {"ingame_render": 0.5},
                    "attributed_sample_weight": 10,
                    "root_inclusive": 10,
                },
            }
            session.acceptance_gates(manifest, root)
            self.assertEqual(manifest["status"], "completed")


if __name__ == "__main__":
    unittest.main()
