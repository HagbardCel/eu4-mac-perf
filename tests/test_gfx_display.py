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
from unittest import mock

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
        physical = gfx_display.Display(
            display_id=1, name="Built-in Display", origin_x=0.0, origin_y=0.0,
            width=1728.0, height=1117.0, pixel_width=3456, pixel_height=2234,
            hidpi=True, main=True,
        )
        try:
            with mock.patch.object(gfx_display, "main_display",
                                   return_value=physical):
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
        self.assertEqual(env["SDL_VIDEO_WINDOW_POS"], "-1893,27")
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


class WindowMonitorTests(unittest.TestCase):
    def setUp(self):
        self.display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )

    def payload(self, rect, front=100):
        return {
            "main_display_id": 1,
            "displays": [display_record(1, main=1, origin_x=0.0,
                                        width=1728.0, height=1117.0, hidpi=1),
                         display_record(7, origin_x=0.0)],
            "frontmost_pid": front,
            "windows": [{"owner_pid": 4242, "x": rect[0], "y": rect[1],
                         "width": rect[2], "height": rect[3]}],
        }

    def test_late_relocation_and_dimension_change_fail_placement(self):
        payloads = [
            self.payload((24, 24, 1728, 1145)),
            self.payload((3000, 24, 1728, 1145)),
            self.payload((24, 24, 1920, 1200)),
        ]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payloads.pop(0)
        try:
            monitor = gfx_display.WindowMonitor(4242, self.display,
                                                (1728, 1117))
            for _ in range(3):
                monitor.sample_once()
        finally:
            gfx_display.snapshot = original
        summary = monitor.summary()
        self.assertTrue(summary["placement_failed"])
        kinds = {item["kind"] for item in summary["window_issues"]}
        self.assertIn("outside_display", kinds)
        self.assertIn("dimension_changed", kinds)

    def test_window_absence_after_first_seen_is_an_issue(self):
        payloads = [
            self.payload((24, 24, 1728, 1145)),
            {**self.payload((24, 24, 1728, 1145)), "windows": []},
        ]
        original = gfx_display.snapshot
        gfx_display.snapshot = lambda pid=None: payloads.pop(0)
        try:
            monitor = gfx_display.WindowMonitor(4242, self.display,
                                                (1728, 1117))
            monitor.sample_once()
            monitor.sample_once()
        finally:
            gfx_display.snapshot = original
        self.assertTrue(monitor.summary()["placement_failed"])


class SettingsLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.user_data = self.root / "user"
        self.user_data.mkdir()
        self.settings = self.user_data / "settings.txt"
        self.settings.write_text(SETTINGS, encoding="utf-8")
        placement = self.root / "placement.dylib"
        placement.write_bytes(b"test-placement")
        self._placement_patch = mock.patch.object(
            gfx_display, "ensure_placement_library", return_value=placement)
        self._placement_patch.start()

    def tearDown(self):
        self._placement_patch.stop()
        self.temporary.cleanup()

    def test_planning_does_not_touch_shared_settings(self):
        from unittest import mock
        calls = []
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=True,
        )
        with mock.patch.object(gfx_display, "_cli", return_value="/bin/true"), \
             mock.patch.object(gfx_display, "main_display", return_value=display), \
             mock.patch.object(gfx_display, "_run_cli",
                               side_effect=lambda *a, **k: calls.append(a) or ""):
            plan = gfx_display.plan_background(self.user_data)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), SETTINGS)
        self.assertEqual(calls, [])
        self.assertEqual(plan.window_size, gfx_display.DEFAULT_WINDOW)

    def test_failure_after_settings_write_rolls_back(self):
        from unittest import mock
        display = gfx_display.Display(
            display_id=7, name="EU4 Test", origin_x=0.0, origin_y=0.0,
            width=1920.0, height=1200.0, pixel_width=1920, pixel_height=1200,
            hidpi=False, main=False,
        )
        plan = gfx_display.BackgroundPlan(
            user_data=str(self.user_data), name="EU4 Test",
            window_size=(1728, 1117), width=1920, height=1200,
            focus_guard=False,
        )
        real_prepare = gfx_display.BackgroundSession.prepare_settings

        def prepare_then_fail(session, user_data, run_dir):
            real_prepare(session, user_data, run_dir)
            raise OSError("injected display-session write failure")

        with mock.patch.object(gfx_display, "ensure_display",
                               return_value={"display": display.as_dict()}), \
             mock.patch.object(gfx_display.BackgroundSession,
                               "prepare_settings", prepare_then_fail):
            with self.assertRaises(OSError):
                gfx_display.activate_background(plan, self.root / "run")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), SETTINGS)

    def test_restore_accepts_game_return_to_original_controlled_fields(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, run_dir=self.root / "run")
        self.settings.write_text(SETTINGS + "unrelated=1\n", encoding="utf-8")
        result = gfx_display.restore_settings(record)
        self.assertTrue(result["restored"])
        self.assertEqual(result["reason"], "controlled_fields_already_original")
        self.assertTrue(result["preserved_unrelated_changes"])
        self.assertIn("unrelated=1", self.settings.read_text(encoding="utf-8"))

    def test_restore_accepts_a_game_rewrite_of_our_windowed_intent(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, run_dir=self.root / "run")
        # The game rewrites settings at startup but preserves our intent.
        self.settings.write_text(SETTINGS.replace("fullScreen=yes",
                                                  "fullScreen=no")
                                 .replace("x=1728", "x=1728")
                                 .replace("y=1117", "y=1117")
                                 + "extra=1\n", encoding="utf-8")
        result = gfx_display.restore_settings(record)
        self.assertTrue(result["restored"])
        self.assertEqual(result["reason"], "controlled_fields_restored")
        self.assertTrue(result["preserved_unrelated_changes"])
        restored = self.settings.read_text(encoding="utf-8")
        self.assertIn("fullScreen=yes", restored)
        self.assertIn("extra=1", restored)

    def test_restore_accepts_already_original_file(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, run_dir=self.root / "run")
        self.settings.write_text(SETTINGS, encoding="utf-8")
        result = gfx_display.restore_settings(record)
        self.assertTrue(result["restored"])
        self.assertEqual(result["reason"], "already_original")

    def test_restore_refuses_to_overwrite_another_actor(self):
        record = gfx_display.prepare_windowed_settings(
            self.user_data, run_dir=self.root / "run")
        self.assertTrue(record["changed"])
        self.settings.write_text("actor=someone-else\n", encoding="utf-8")
        result = gfx_display.restore_settings(record)
        self.assertFalse(result["restored"])
        self.assertTrue(result["conflict"])
        self.assertEqual(self.settings.read_text(encoding="utf-8"),
                         "actor=someone-else\n")
        self.assertTrue(Path(record["backup"]).is_file())


class SessionDisplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.user_data = self.root / "user"
        self.user_data.mkdir()
        (self.user_data / "settings.txt").write_text(SETTINGS, encoding="utf-8")
        placement = self.root / "placement.dylib"
        placement.write_bytes(b"test-placement")
        self._placement_patch = mock.patch.object(
            gfx_display, "ensure_placement_library", return_value=placement)
        self._placement_patch.start()

    def tearDown(self):
        self._placement_patch.stop()
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
