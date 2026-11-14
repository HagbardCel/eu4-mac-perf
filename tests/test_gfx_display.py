"""Background virtual-display isolation (Phase 0).

All tests are offline: BetterDisplay, CoreGraphics and the window probe are
replaced by deterministic fakes.  A real qualification is recorded separately
in ``analysis/infra/background-display.md``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_display, gfx_session  # noqa: E402


def display_record(display_id=7, main=0, origin_x=-1920.0, width=1920.0,
                   height=1200.0, hidpi=0):
    return {
        "display_id": display_id,
        "main": main,
        "active": 1,
        "online": 1,
        "asleep": 0,
        "origin_x": origin_x,
        "origin_y": 0.0,
        "width": width,
        "height": height,
        "pixel_width": int(width),
        "pixel_height": int(height),
        "mode_width": int(width),
        "mode_height": int(height),
        "refresh_hz": 60.0,
        "hidpi": hidpi,
    }


class DisplayModuleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.calls: list[tuple[str, ...]] = []
        self.texts: list[dict] = []

    def tearDown(self):
        self.temporary.cleanup()

    def fake_identifiers(self, *, exists=True, name="EU4 Test",
                         display_id=7):
        value = [{"deviceType": "Display", "name": "Built-in Display",
                  "displayID": "1", "tagID": "2"}]
        if exists:
            value.append({"deviceType": "VirtualScreen", "name": name,
                          "displayID": str(display_id), "tagID": "3"})
        value.append({"deviceType": "DisplayGroup", "name": "Default Group",
                      "tagID": "-1001"})
        return value

    def patch(self, *, exists=True, displays=None, physical=None,
              modes=None, name="EU4 Test"):
        self.calls = []
        self.texts = []
        state = {"exists": exists}

        def run_cli(*arguments, timeout=30.0):
            self.calls.append(arguments)
            if arguments == ("get", "-identifiers"):
                return json.dumps(
                    self.fake_identifiers(exists=state["exists"], name=name))[1:-1]
            if arguments[:1] == ("create",):
                state["exists"] = True
            if arguments[:2] == ("get", f"-name={name}") and \
                    arguments[2:] == ("-displayModeList",):
                value = modes if modes is not None else [
                    {"mode": 4, "width": 1728, "height": 1117,
                     "refresh_hz": 60.0, "bpc": 8, "flags": []},
                    {"mode": 13, "width": 1920, "height": 1200,
                     "refresh_hz": 60.0, "bpc": 8, "flags": []},
                ]
                self.texts.append({"modes": value})
                return "\n".join(
                    f"{item['mode']} - {item['width']}x{item['height']} "
                    f"{item['refresh_hz']:.0f}Hz {item['bpc']}bpc "
                    + " ".join(item.get("flags") or [])
                    for item in value
                )
            if arguments[:2] == ("set", f"-name={name}") and \
                    any(str(a).startswith("-displayModeNumber=") for a in arguments):
                self.texts.append({"selected": True})
            return ""

        self._old_cli = gfx_display._run_cli
        gfx_display._run_cli = run_cli
        self._old_probe = gfx_display._probe
        snapshot_value = physical or {
            "main_display_id": 1,
            "displays": [display_record(1, main=1, origin_x=0.0,
                                        width=1728.0, height=1117.0,
                                        hidpi=1),
                         display_record(7)],
        }
        self.snapshots = displays if displays is not None else [snapshot_value]

        self.probe_index = 0

        def probe(operation, pid=None, timeout=15.0):
            self.calls.append(("probe", operation))
            value = self.snapshots[
                min(self.probe_index, len(self.snapshots) - 1)
            ] if self.snapshots else {}
            self.probe_index += 1
            return value

        gfx_display._probe = probe

    def restore(self):
        gfx_display._run_cli = self._old_cli
        gfx_display._probe = self._old_probe

    def test_ensure_existing_display_selects_mode_without_creating(self):
        self.patch(exists=True)
        try:
            value = gfx_display.ensure_display(timeout=2.0)
        finally:
            self.restore()
        self.assertTrue(value["physical_unchanged"])
        self.assertEqual(value["display"]["display_id"], 7)
        self.assertFalse(any(call and call[0] == "create" for call in self.calls))
        # The display already has the requested geometry: it is reused without
        # changing its mode or identifier.
        self.assertEqual(value["steps"], [{"action": "reuse"}])
        self.assertFalse(any(
            call[:1] == ("set",) and any(
                str(a).startswith("-resolutionList=") for a in call)
            for call in self.calls))

    def test_missing_display_is_created_once(self):
        self.patch(exists=False)
        try:
            value = gfx_display.ensure_display(timeout=2.0)
        finally:
            self.restore()
        created = [call for call in self.calls if call[:1] == ("create",)]
        self.assertEqual(len(created), 1)
        self.assertIn("-virtualScreenName=EU4 Test", created[0])
        self.assertIn("-virtualScreenHiDPI=off", created[0])

    def test_physical_main_change_detection(self):
        before = display_record(1, main=1, origin_x=0.0, width=1728.0,
                                height=1117.0, hidpi=1)
        self.assertFalse(gfx_display.physical_changed(before, dict(before)))
        moved = dict(before)
        moved["width"] = 1600.0
        self.assertTrue(gfx_display.physical_changed(before, moved))

    def test_missing_mode_is_refused(self):
        self.patch(exists=True, modes=[
            {"mode": 4, "width": 1728, "height": 1117,
             "refresh_hz": 60.0, "bpc": 8, "flags": []},
        ], displays=[{
            "main_display_id": 1,
            "displays": [display_record(1, main=1, origin_x=0.0,
                                        width=1728.0, height=1117.0, hidpi=1),
                         display_record(7, width=1280.0, height=720.0)],
        }])
        try:
            with self.assertRaises(gfx_display.DisplayError) as caught:
                gfx_display.ensure_display(timeout=2.0)
        finally:
            self.restore()
        self.assertEqual(caught.exception.kind, "mode_missing")

    def test_hidpi_mismatch_is_refused(self):
        self.patch(exists=True, displays=[{
            "main_display_id": 1,
            "displays": [display_record(1, main=1, origin_x=0.0,
                                        width=1728.0, height=1117.0,
                                        hidpi=1),
                         display_record(7, hidpi=1)],
        }])
        try:
            with self.assertRaises(gfx_display.DisplayError) as caught:
                gfx_display.ensure_display(hidpi=False, timeout=2.0)
        finally:
            self.restore()
        self.assertEqual(caught.exception.kind, "hidpi")

    def test_betterdisplay_cli_failure_is_reported(self):
        def broken(*_arguments, **_kwargs):
            raise gfx_display.DisplayError("betterdisplay_missing", "no cli")

        old = gfx_display._run_cli
        gfx_display._run_cli = broken
        try:
            with self.assertRaises(gfx_display.DisplayError) as caught:
                gfx_display.ensure_display(timeout=2.0)
        finally:
            gfx_display._run_cli = old
        self.assertEqual(caught.exception.kind, "betterdisplay_missing")


class WindowPlacementTests(unittest.TestCase):
    def test_window_is_placed_inside_the_display(self):
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=-1920.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )
        env = gfx_display.launch_environment(display)
        self.assertEqual(env["SDL_VIDEO_WINDOW_POS"], "-1896,24")
        self.assertEqual(env["EU4_DISPLAY_MODE"], "background")
        self.assertEqual(env["EU4_DISPLAY_ID"], "7")
        self.assertEqual(env["EU4_DISPLAY_WINDOW_SIZE"], "1728x1117")

    def test_too_large_window_is_refused(self):
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1280.0, height=720.0, pixel_width=1280, pixel_height=720,
            hidpi=False, main=False,
        )
        with self.assertRaises(gfx_display.DisplayError) as caught:
            gfx_display.window_origin(display)
        self.assertEqual(caught.exception.kind, "window_too_large")

    def _display(self):
        return gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )

    @staticmethod
    def _base(front_pid=100):
        return {
            "main_display_id": 1,
            "displays": [display_record(1, main=1, origin_x=0.0,
                                        width=1728.0, height=1117.0, hidpi=1),
                         display_record(7, origin_x=0.0)],
            "frontmost_pid": front_pid,
        }

    def test_window_verification_records_bounds_and_clean_focus(self):
        payload = self._base(front_pid=100)
        payload["windows"] = [{
            "owner_pid": 4242, "x": 24, "y": 24, "width": 1728,
            "height": 1145, "onscreen": 1, "layer": 0,
        }]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payload
        try:
            result = gfx_display.verify_window(4242, self._display(),
                                               timeout=1.0)
        finally:
            gfx_display.snapshot = original
        self.assertTrue(result["ok"])
        self.assertEqual(result["window_rect"], [24.0, 24.0, 1728.0, 1145.0])
        self.assertTrue(result["contained"])
        self.assertEqual(result["activations"], [])

    def test_unexpected_activation_fails_background_verification(self):
        payload = self._base(front_pid=4242)
        payload["windows"] = [{
            "owner_pid": 4242, "x": 24, "y": 24, "width": 1728,
            "height": 1145, "onscreen": 1, "layer": 0,
        }]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payload
        try:
            result = gfx_display.verify_window(4242, self._display(),
                                               timeout=1.0)
        finally:
            gfx_display.snapshot = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unexpected_activation")
        self.assertEqual(len(result["activations"]), 1)

    def test_window_outside_display_is_refused(self):
        payload = self._base()
        payload["windows"] = [{
            "owner_pid": 4242, "x": 3000, "y": 24, "width": 1728,
            "height": 1145, "onscreen": 1, "layer": 0,
        }]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payload
        try:
            result = gfx_display.verify_window(4242, self._display(),
                                               timeout=1.0)
        finally:
            gfx_display.snapshot = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "window_outside_display")

    def test_display_disconnect_is_refused(self):
        payload = self._base()
        payload["displays"] = [payload["displays"][0]]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payload
        try:
            result = gfx_display.verify_window(4242, self._display(),
                                               timeout=0.3)
        finally:
            gfx_display.snapshot = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "display_disconnected")


SETTINGS = """force_pow2_textures=no
language="l_english"
graphics={
\tsize={
\t\tx=1728
\t\ty=1117
\t}

\tmin_gui={
\t\tx=1280
\t\ty=720
\t}

\trefreshRate=60
\tfullScreen=yes
\tborderless=no
\tvsync=yes
}
sound_fx_volume=0.000000
"""


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.user_data = self.root / "user"
        self.user_data.mkdir()
        self.path = self.user_data / "settings.txt"
        self.path.write_text(SETTINGS, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_windowed_rewrite_changes_only_window_fields(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, width=1728, height=1117,
            run_dir=self.root / "run",
        )
        self.assertTrue(record["changed"])
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("\tfullScreen=no", text)
        self.assertIn("\t\tx=1728", text)
        self.assertIn("\t\tx=1280", text)  # min_gui untouched
        self.assertTrue(Path(record["backup"]).is_file())
        restored = gfx_display.restore_settings(record)
        self.assertTrue(restored["restored"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), SETTINGS)

    def test_windowed_rewrite_is_idempotent(self):
        gfx_display.prepare_windowed_settings(self.user_data, run_dir=self.root)
        second = gfx_display.prepare_windowed_settings(self.user_data)
        self.assertFalse(second["changed"])

    def test_restore_detects_corrupt_backup(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, run_dir=self.root / "run")
        Path(record["backup"]).write_text("corrupt", encoding="utf-8")
        with self.assertRaises(gfx_display.DisplayError) as caught:
            gfx_display.restore_settings(record)
        self.assertEqual(caught.exception.kind, "settings_backup_corrupt")

    def test_missing_settings_fails_closed(self):
        (self.user_data / "settings.txt").unlink()
        with self.assertRaises(gfx_display.DisplayError) as caught:
            gfx_display.prepare_windowed_settings(self.user_data)
        self.assertEqual(caught.exception.kind, "settings_missing")


class SessionDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.user_data = self.root / "user"
        self.user_data.mkdir()
        (self.user_data / "settings.txt").write_text(SETTINGS, encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_session_restores_display_settings_on_close(self):
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )
        background = gfx_display.BackgroundSession(display, (1728, 1117))
        session = gfx_session.Session.create(
            self.root / "runs", workflow_id="test", profile="early",
            session_root_path=self.root / "sessions",
        )
        background.prepare_settings(self.user_data, session.run_dir)
        session.attach_display(background)
        self.assertEqual(session.display_env()["EU4_DISPLAY_ID"], "7")
        self.assertIn("fullScreen=no",
                      (self.user_data / "settings.txt").read_text())
        terminal = session.close(reason="test")
        self.assertEqual((self.user_data / "settings.txt").read_text(), SETTINGS)
        self.assertTrue((session.run_dir / "display" /
                         "display-session.json").is_file())
        self.assertTrue(terminal["display_restore"]["restored"])

    def test_session_restores_settings_after_failure(self):
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )
        background = gfx_display.BackgroundSession(display, (1728, 1117))
        session = gfx_session.Session.create(
            self.root / "runs2", workflow_id="test", profile="early",
            session_root_path=self.root / "sessions2",
        )
        background.prepare_settings(self.user_data, session.run_dir)
        session.attach_display(background)
        session.fail("simulated")
        self.assertEqual((self.user_data / "settings.txt").read_text(), SETTINGS)


if __name__ == "__main__":
    unittest.main()
