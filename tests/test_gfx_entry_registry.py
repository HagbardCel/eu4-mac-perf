import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_metal_boot as probe  # noqa: E402
import eu4_metal_triage as triage  # noqa: E402


_P6 = bytes.fromhex("554889e54157")
_P5 = bytes.fromhex("554889e553")
_DESTROY = bytes.fromhex("4885ff742f")
_VIEW = bytes.fromhex("554889e5f30f6f06")
_INDEX_CREATE = bytes.fromhex("554889e54157")
_INDEX_UPDATE = bytes.fromhex("554889e54156")
_INDEX_DESTROY = bytes.fromhex("4885ff7450")
_INDEX_SET = bytes.fromhex("4885f67428")
_MULTI = bytes.fromhex("554889e541574156")
_SCISSOR = bytes.fromhex("554889e50fbf5604")
_FIVE = r"\x55\x48\x89\xe5\x53"
BASELINE = (
    ("settings", 0x15ED388, 0x15ED9FC, "eu4_gfx_settings", _P6),
    ("init", 0x15E8EA0, 0x15E9D11, "eu4_gfx_init", _P6),
    ("blend", 0x15EA0BF, 0x15EA261, "eu4_gfx_set_blend", _P6),
    ("depth", 0x15EA261, 0x15EA35A, "eu4_gfx_set_depth", _P6),
    ("raster", 0x15EA35A, 0x15EA42C, "eu4_gfx_set_raster", _P6),
    ("shutdown", 0x15EA42C, 0x15EA460, "eu4_gfx_shutdown", _P5),
    ("reset", 0x15EA46B, 0x15EA49B, "eu4_gfx_reset", _P5),
    ("on_reset_master", 0x15EA49B, 0x15EA4F2, "eu4_gfx_on_reset_master", _P5),
    ("create_vertex_buffer", 0x15EBCB3, 0x15EBD4C, "eu4_gfx_create_vertex_buffer", _P6),
    ("destroy_vertex_buffer", 0x15EBD4C, 0x15EBD81, "eu4_gfx_destroy_vertex_buffer", _DESTROY),
    ("update_vertex_buffer", 0x15EBD81, 0x15EBE5F, "eu4_gfx_update_vertex_buffer", _P6),
    ("_GfxUpdateVertexBufferMultiple", 0x15EBE5F, 0x15EBF41, "eu4_gfx_update_vertex_buffer_multiple", _MULTI),
    ("viewport", 0x15EA4F8, 0x15EA52C, "eu4_gfx_set_viewport", _VIEW),
    ("_GfxCreateIndexBuffer", 0x15EC154, 0x15EC1E6, "eu4_gfx_create_index_buffer", _INDEX_CREATE),
    ("_GfxDestroyIndexBuffer", 0x15EC1E6, 0x15EC23C, "eu4_gfx_destroy_index_buffer", _INDEX_DESTROY),
    ("_GfxUpdateIndexBuffer", 0x15EC23C, 0x15EC28E, "eu4_gfx_update_index_buffer", _INDEX_UPDATE),
    ("_GfxSetIndexBuffer", 0x15EC28E, 0x15EC2BC, "eu4_gfx_set_index_buffer", _INDEX_SET),
    ("_GfxSetVertexBuffers", 0x15EBF41, 0x15EC11C, "eu4_gfx_set_vertex_buffers", _P6),
    ("_GfxSetScissorRectangle", 0x15ED0AA, 0x15ED0E2, "eu4_gfx_set_scissor_rectangle", _SCISSOR),
)
_ROOT = Path(__file__).resolve().parents[1]
_PR5 = _ROOT / "results/20261102T100321Z-metal-init"
_PR6 = _ROOT / "results/20261102T120220Z-metal-init"
_DISCLAIMER = "A diagnostic stop does not show that a replacement ran or that a frame was drawn."
_RECEIPT = "Validated _GfxCreateVertexBuffer success receipt, recorded from this process."


def _line(name: str, entry: str, end: str, fn: str, prefix: str) -> str:
    return f'GFX_ENTRY({name}, {entry}, {end}, {fn}, "{prefix}")'


def _files(path: Path) -> dict[str, bytes]:
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


class GfxEntryRegistryTests(unittest.TestCase):
    def test_baseline_is_a_subset_in_install_order(self) -> None:
        entries = probe.GFX_ENTRIES
        self.assertTrue(set(BASELINE).issubset(entries))
        self.assertEqual(
            probe.REPLACED_BODIES,
            tuple((entry, end) for _name, entry, end, _fn, _prefix in entries),
        )
        names = [item[0] for item in entries]
        cursor = 0
        for name in names:
            if cursor < len(BASELINE) and name == BASELINE[cursor][0]:
                cursor += 1
        self.assertEqual(cursor, len(BASELINE))
        bodies = set(probe.REPLACED_BODIES)
        for _name, entry, end, _fn, _prefix in BASELINE:
            self.assertIn((entry, end), bodies)

    def test_registry_invariants(self) -> None:
        entries = probe.GFX_ENTRIES
        self.assertEqual(len({item[0] for item in entries}), len(entries))
        self.assertEqual(len({item[1] for item in entries}), len(entries))
        self.assertEqual(len({item[3] for item in entries}), len(entries))
        for _name, entry, end, _fn, prefix in entries:
            self.assertLess(entry, end)
            self.assertGreaterEqual(len(prefix), 5)
            self.assertLessEqual(len(prefix), 8)
        order = sorted(entries, key=lambda item: item[1])
        for prev, nxt in zip(order, order[1:]):
            self.assertGreaterEqual(nxt[1], prev[2])

    def test_rejects_malformed_records(self) -> None:
        samples = [
            _line("a", "0x1000", "0x1100", "fn_a", _FIVE) + "\n" + _line("a", "0x2000", "0x2100", "fn_b", _FIVE),
            _line("a", "0x1000", "0x1100", "fn_a", _FIVE) + "\n" + _line("b", "0x1000", "0x2100", "fn_b", _FIVE),
            _line("a", "0x1000", "0x1100", "fn_a", _FIVE) + "\n" + _line("b", "0x2000", "0x2100", "fn_a", _FIVE),
            _line("b", "0x2000", "0x2100", "fn_b", _FIVE) + "\n" + _line("a", "0x20f0", "0x2200", "fn_a", _FIVE),
            _line("a", "0x1100", "0x1100", "fn_a", _FIVE),
            _line("a", "0x1100", "0x1000", "fn_a", _FIVE),
            _line("a", "0x1000", "0x1100", "fn_a", r"\x55\x48\x89\xe5"),
            _line("a", "0x1000", "0x1100", "fn_a", r"\x55\x48\x89\xe5\x53\x90\x90\x90\x90"),
            _line("a", "0x1000", "0x1100", "fn_a", r"\x55\x48\n\x90\x90"),
            _line("a", "nope", "0x1100", "fn_a", _FIVE),
            _line("a", "0x1000", "0x1100", "fn_a", _FIVE) + " " + _line("b", "0x2000", "0x2100", "fn_b", _FIVE),
        ]
        for text in samples:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    probe.parse_gfx_entries(text)

    def test_zero_byte_prefix_parses(self) -> None:
        text = _line("z", "0x1000", "0x1010", "fn_z", r"\x55\x48\x00\x90\x90") + "\n"
        self.assertEqual(probe.parse_gfx_entries(text)[0][4], bytes.fromhex("5548009090"))

    def _dir(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, body in files.items():
            (root / name).write_text(body)
        return root

    def _copy(self, src: Path) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(shutil.copytree(src, Path(tmp.name) / src.name))

    def _diagnose(self, path: Path) -> dict:
        before = _files(path)
        with patch("eu4_metal_boot._quarantine", side_effect=AssertionError("quarantine")):
            diag = triage.diagnose(path)
        self.assertEqual(_files(path), before)
        return diag

    def test_recorded_sessions(self) -> None:
        expected = (
            (_PR5, 14511, "glViewport", 0x149B21F, None, "gl_diagnostic", False),
            (_PR6, 20282, "glGenTextures", 0x15EC4ED, 0x15EC4EC, "gl_diagnostic", False),
        )
        for src, pid, symbol, rva, preceding, kind, inside in expected:
            with self.subTest(src=src.name):
                diag = self._diagnose(self._copy(src))
                text = triage.format_diagnosis(diag)
                self.assertEqual(diag["kind"], kind)
                self.assertEqual(diag["d1"], "not_started")
                self.assertEqual(diag["process"], pid)
                self.assertEqual(diag["stop_symbol"], symbol)
                self.assertEqual(diag["return_rva"], rva)
                self.assertEqual(diag["exit_code"], 2)
                self.assertIs(diag["launcher_signaled"], False)
                self.assertIs(diag["inside_replaced_body"], inside)
                self.assertIs(diag["threads_agree"], True)
                self.assertIs(diag["pinned_image"], True)
                self.assertIs(diag["pinned_sha256"], True)
                self.assertEqual(diag["vertex_buffer_receipt"], "validated")
                self.assertIn(f"return_rva: {rva:#x}", text)
                self.assertIn("inside_replaced_body: " + ("yes" if inside else "no"), text)
                self.assertIn(_RECEIPT, text)
                self.assertIn("reached its success-reporting point", text)
                self.assertIn(_DISCLAIMER, text)
                self.assertNotIn("callsite", text.lower())
                self.assertNotIn("call byte", text.lower())
                self.assertNotIn("_GfxInitializeTexture", text)
                self.assertNotIn("CGraphics", text)
                if preceding is not None:
                    self.assertEqual(diag["preceding_byte_rva"], preceding)
                    self.assertIn(f"preceding_byte_rva: {preceding:#x}", text)

    def _pr6(self, edit) -> Path:
        names = ("metadata.json", "init.json", "stop.json", "create.json")
        events = {name: json.loads((_PR5 / name).read_text()) for name in names}
        edit(events)
        files = {
            name: json.dumps(event) + "\n" for name, event in events.items() if event is not None
        }
        return self._dir(files)

    def test_gl_diagnostic_requires_identity(self) -> None:
        def drop_all(events: dict) -> None:
            for name in ("metadata.json", "init.json", "stop.json"):
                events[name].pop("pid")
            events["create.json"] = None

        def drop_create_pid(events: dict) -> None:
            events["create.json"].pop("pid")

        def string_threads(events: dict) -> None:
            events["init.json"]["thread"] = "1856966"
            events["stop.json"]["thread"] = "1856966"

        def wrong_sha(events: dict) -> None:
            events["metadata.json"]["gog"]["executable_sha256"] = "0" * 64

        def absent_sha(events: dict) -> None:
            del events["metadata.json"]["gog"]["executable_sha256"]

        for edit in (drop_all, drop_create_pid, string_threads, wrong_sha):
            with self.subTest(edit=edit.__name__):
                self.assertEqual(self._diagnose(self._pr6(edit))["kind"], "incomplete")
        for name in ("metadata.json", "init.json", "stop.json"):
            with self.subTest(missing_pid=name):
                self.assertEqual(
                    self._diagnose(self._pr6(lambda events, name=name: events[name].pop("pid")))["kind"],
                    "incomplete",
                )
        kept = self._diagnose(self._pr6(absent_sha))
        self.assertEqual(kept["kind"], "gl_diagnostic")
        self.assertIsNone(kept["pinned_sha256"])

    def test_missing_create_receipt_stays_diagnostic(self) -> None:
        path = self._copy(_PR5)
        (path / "create.json").unlink()
        diag = self._diagnose(path)
        self.assertEqual(diag["kind"], "gl_diagnostic")
        self.assertEqual(diag["vertex_buffer_receipt"], "absent")
        self.assertIn("No vertex-buffer receipt was recorded.", triage.format_diagnosis(diag))

    def test_recorded_ranges_keep_pr6_outside_the_later_texture_body(self) -> None:
        recorded = self._diagnose(self._copy(_PR6))
        self.assertEqual(recorded["kind"], "gl_diagnostic")
        self.assertIs(recorded["inside_replaced_body"], False)
        path = self._copy(_PR6)
        meta = json.loads((path / "metadata.json").read_text())
        meta["gfx_entry_ranges"] = [[0x15EC43B, 0x15EC61D]]
        (path / "metadata.json").write_text(json.dumps(meta) + "\n")
        revalidated = self._diagnose(path)
        self.assertIs(revalidated["inside_replaced_body"], True)
        self.assertNotEqual(revalidated["kind"], "gl_diagnostic")

    def test_unknown_probe_cannot_become_a_diagnostic(self) -> None:
        def unknown(events: dict) -> None:
            events["metadata.json"]["probe_sha256"] = "0" * 64

        diag = self._diagnose(self._pr6(unknown))
        self.assertNotEqual(diag["kind"], "gl_diagnostic")
        self.assertIsNone(diag["inside_replaced_body"])

    def test_outcome_precedence(self) -> None:
        cases = {
            "timeout": {
                "metadata.json": '{"stop_reason":"deadline","pid":1,"exit_code":-15,"launcher_signaled":true}\n',
                "init.json": '{"success":1,"pid":1,"thread":7}\n',
            },
            "crash": {
                "metadata.json": '{"stop_reason":"exit","exit_code":-11,"pid":7,"launcher_signaled":false}\n',
                "init.json": '{"success":1,"pid":7}\n',
            },
            "clean_exit": {
                "metadata.json": '{"stop_reason":"exit","exit_code":0,"pid":4}\n',
                "init.json": '{"success":1,"pid":4}\n',
            },
        }
        for kind, files in cases.items():
            with self.subTest(kind=kind):
                self.assertEqual(self._diagnose(self._dir(files))["kind"], kind)
        for reason in ("unsupported", "reentry", "unresolved_forward"):
            with self.subTest(reason=reason):
                files = {
                    "metadata.json": '{"pid":7,"stop_reason":"stop","exit_code":2,"launcher_signaled":false}\n',
                    "init.json": '{"success":1,"pid":7,"thread":3}\n',
                    "stop.json": f'{{"reason":"{reason}","symbol":"glFoo","pid":7,"phase":"after_init"}}\n',
                }
                self.assertEqual(self._diagnose(self._dir(files))["kind"], "unsupported")

    def test_crash_after_recorded_stop(self) -> None:
        def session(signaled: bool) -> dict[str, str]:
            return {
                "metadata.json": (
                    '{"stop_reason":"stop","exit_code":-11,"pid":7,'
                    f'"launcher_signaled":{"true" if signaled else "false"}}}\n'
                ),
                "init.json": '{"success":1,"pid":7,"thread":3}\n',
                "stop.json": '{"reason":"gl","symbol":"glGenTextures","pid":7,"phase":"after_init"}\n',
            }

        self.assertEqual(self._diagnose(self._dir(session(False)))["kind"], "crash")
        self.assertNotEqual(self._diagnose(self._dir(session(True)))["kind"], "crash")

    def test_integrity_before_outcome(self) -> None:
        self.assertEqual(self._diagnose(self._dir({}))["kind"], "missing")
        truncated = self._diagnose(self._dir({"init.json": "{"}))
        self.assertEqual(truncated["kind"], "incomplete")
        string_pid = {
            "metadata.json": '{"pid":7,"stop_reason":"stop","exit_code":2,"launcher_signaled":false}\n',
            "init.json": '{"pid":"7","success":1}\n',
            "stop.json": '{"pid":7,"reason":"gl","phase":"after_init"}\n',
        }
        self.assertEqual(self._diagnose(self._dir(string_pid))["kind"], "incomplete")
        mismatched = {
            "metadata.json": '{"pid":1,"stop_reason":"stop","exit_code":2}\n',
            "init.json": '{"pid":1,"success":1,"thread":3}\n',
            "stop.json": '{"pid":2,"reason":"unsupported","phase":"after_init"}\n',
        }
        self.assertEqual(self._diagnose(self._dir(mismatched))["kind"], "incomplete")
        broken = self._copy(_PR6)
        (broken / "create.json").write_text("{")
        self.assertEqual(self._diagnose(broken)["kind"], "incomplete")

    def test_main_exit_is_not_the_game_result(self) -> None:
        path = self._copy(_PR6)
        before = _files(path)
        with patch("eu4_metal_boot._quarantine", side_effect=AssertionError("quarantine")):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(triage.main([str(path)]), 0)
        self.assertEqual(_files(path), before)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(triage.main([str(path / "init.json")]), 1)
            self.assertEqual(triage.main(["/no/such/metal-init-dir"]), 1)


class D1StateTests(unittest.TestCase):
    def _dir(self, files: dict[str, str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, body in files.items():
            (root / name).write_text(body)
        return root

    def _base(self) -> dict[str, str]:
        image = json.loads((_PR6 / "stop.json").read_text())["image"]
        sha = json.loads((_PR6 / "metadata.json").read_text())["gog"]["executable_sha256"]
        common = (
            f'"pid":7,"thread":3,"image":{json.dumps(image)}'
        )
        return {
            "metadata.json": (
                '{"pid":7,"stop_reason":"stop","exit_code":2,"launcher_signaled":false,'
                f'"gog":{{"executable_sha256":"{sha}"}},"gfx_entry_ranges":[[1,2]]}}\n'
            ),
            "init.json": '{"success":1,"pid":7,"thread":3}\n',
            "stop.json": '{"reason":"draw","symbol":"_GfxDraw","phase":"after_init",' + common + ',"rva":"0x1000"}\n',
            "draw_invocation.json": (
                '{"symbol":"_GfxDraw","invocation_id":1,"provenance":"game",'
                '"call_chain":["0x1000"],"caller_rva":"0x1000","context_want":0,'
                '"context_got":0,"context_complete":true,' + common + '}\n'
            ),
        }

    def test_draw_reached_requires_game_provenance(self) -> None:
        files = self._base()
        diag = triage.diagnose(self._dir(files))
        self.assertEqual(diag["d1"], "draw_reached")
        self.assertEqual(diag["kind"], "draw_reached")
        files["draw_invocation.json"] = files["draw_invocation.json"].replace(
            '"provenance":"game"', '"provenance":"rejected"'
        )
        rejected = triage.diagnose(self._dir(files))
        self.assertEqual(rejected["d1"], "error")
        self.assertNotEqual(rejected["kind"], "draw_reached")

    def test_draw_stop_without_a_receipt_is_not_reached(self) -> None:
        files = self._base()
        del files["draw_invocation.json"]
        diag = triage.diagnose(self._dir(files))
        self.assertEqual(diag["d1"], "error")
        self.assertNotEqual(diag["kind"], "draw_reached")

    def test_receipt_without_a_draw_stop_is_not_reached(self) -> None:
        files = self._base()
        del files["stop.json"]
        self.assertEqual(triage.diagnose(self._dir(files))["d1"], "error")
        files = self._base()
        files["stop.json"] = files["stop.json"].replace('"reason":"draw"', '"reason":"blocked_at"')
        diag = triage.diagnose(self._dir(files))
        self.assertNotEqual(diag["d1"], "draw_reached")
        self.assertNotEqual(diag["kind"], "draw_reached")

    def test_draw_requires_the_pinned_sha(self) -> None:
        files = self._base()
        meta = json.loads(files["metadata.json"])
        del meta["gog"]["executable_sha256"]
        files["metadata.json"] = json.dumps(meta) + "\n"
        missing = triage.diagnose(self._dir(files))
        self.assertEqual(missing["d1"], "error")
        self.assertNotEqual(missing["kind"], "draw_reached")
        files = self._base()
        meta = json.loads(files["metadata.json"])
        del meta["gog"]
        files["metadata.json"] = json.dumps(meta) + "\n"
        absent = triage.diagnose(self._dir(files))
        self.assertEqual(absent["d1"], "error")
        self.assertNotEqual(absent["kind"], "draw_reached")

    def test_draw_requires_a_terminal_launcher_stop(self) -> None:
        files = self._base()
        files["metadata.json"] = files["metadata.json"].replace('"exit_code":2', '"exit_code":0')
        diag = triage.diagnose(self._dir(files))
        self.assertEqual(diag["d1"], "error")
        self.assertNotEqual(diag["kind"], "draw_reached")

    def test_cross_pid_and_incomplete_context_are_errors(self) -> None:
        files = self._base()
        files["draw_invocation.json"] = files["draw_invocation.json"].replace('"pid":7', '"pid":8')
        self.assertEqual(triage.diagnose(self._dir(files))["d1"], "error")
        files = self._base()
        files["draw_invocation.json"] = files["draw_invocation.json"].replace(
            '"context_want":0,"context_got":0,"context_complete":true',
            '"context_want":10,"context_got":4,"context_complete":true',
        )
        self.assertEqual(triage.diagnose(self._dir(files))["d1"], "error")
        files = self._base()
        files["draw_invocation.json"] = files["draw_invocation.json"].replace(
            '"context_want":0,"context_got":0,"context_complete":true',
            '"context_want":10,"context_got":4,"context_complete":false',
        )
        reached = triage.diagnose(self._dir(files))
        self.assertEqual(reached["d1"], "draw_reached")
        self.assertEqual(reached["kind"], "draw_reached")

    def _blocker(self) -> dict[str, str]:
        files = self._base()
        del files["draw_invocation.json"]
        files["stop.json"] = files["stop.json"].replace(
            '"reason":"draw","symbol":"_GfxDraw"',
            '"reason":"blocked_at","symbol":"_GfxInitializeTexture"',
        )
        files["blocked_at.json"] = (
            '{"symbol":"_GfxInitializeTexture","caller_rva":"0x1000","pid":7,"thread":3}\n'
        )
        return files

    def test_blocked_at_is_not_init_success(self) -> None:
        diag = triage.diagnose(self._dir(self._blocker()))
        self.assertEqual(diag["d1"], "blocked_at")
        self.assertEqual(diag["kind"], "blocked_at")
        self.assertNotEqual(diag["kind"], "gl_diagnostic")

    def test_blocked_at_requires_the_receipt(self) -> None:
        files = self._blocker()
        del files["blocked_at.json"]
        missing = triage.diagnose(self._dir(files))
        self.assertEqual(missing["d1"], "error")
        self.assertNotEqual(missing["kind"], "blocked_at")
        files = self._blocker()
        del files["metadata.json"]
        self.assertEqual(triage.diagnose(self._dir(files))["d1"], "error")
        files = self._blocker()
        files["blocked_at.json"] = files["blocked_at.json"].replace(
            '"symbol":"_GfxInitializeTexture"', '"symbol":"_GfxDraw"'
        )
        mismatched = triage.diagnose(self._dir(files))
        self.assertEqual(mismatched["d1"], "error")
        self.assertNotEqual(mismatched["kind"], "blocked_at")
        files = self._blocker()
        files["blocked_at.json"] = files["blocked_at.json"].replace('"caller_rva":"0x1000"', '"caller_rva":"0x1001"')
        self.assertEqual(triage.diagnose(self._dir(files))["d1"], "error")

    def test_blocked_at_requires_the_pinned_sha(self) -> None:
        files = self._blocker()
        meta = json.loads(files["metadata.json"])
        del meta["gog"]["executable_sha256"]
        files["metadata.json"] = json.dumps(meta) + "\n"
        missing = triage.diagnose(self._dir(files))
        self.assertEqual(missing["d1"], "error")
        self.assertNotEqual(missing["kind"], "blocked_at")
        files = self._blocker()
        meta = json.loads(files["metadata.json"])
        meta["gog"]["executable_sha256"] = "0" * 64
        files["metadata.json"] = json.dumps(meta) + "\n"
        mismatched = triage.diagnose(self._dir(files))
        self.assertEqual(mismatched["d1"], "error")
        self.assertNotEqual(mismatched["kind"], "blocked_at")

    def test_optional_create_receipt_must_match_the_session_pid(self) -> None:
        receipt = '{"pid":999,"success":1,"symbol":"_GfxCreateVertexBuffer"}\n'
        files = self._base()
        files["create.json"] = receipt
        draw = triage.diagnose(self._dir(files))
        self.assertEqual(draw["d1"], "error")
        self.assertNotEqual(draw["kind"], "draw_reached")
        files = self._blocker()
        files["create.json"] = receipt
        blocked = triage.diagnose(self._dir(files))
        self.assertEqual(blocked["d1"], "error")
        self.assertNotEqual(blocked["kind"], "blocked_at")
        files = self._base()
        files["create.json"] = "{\n"
        malformed = triage.diagnose(self._dir(files))
        self.assertEqual(malformed["d1"], "error")
        self.assertNotEqual(malformed["kind"], "draw_reached")


if __name__ == "__main__":
    unittest.main()
