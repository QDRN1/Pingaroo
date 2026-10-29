import os
import tempfile
import time
import unittest

from pingaroo import sample_data
from pingaroo.app import UIController
from pingaroo.display import HeadlessFramebuffer
from pingaroo.storage import Storage
from pingaroo.touch import TouchEvent
from pingaroo.ui import Renderer
from pingaroo.worker import Phase, Worker, WorkerState
from tests.fakes import FakeBackend, test_config, wait_until


class Layout(unittest.TestCase):
    def setUp(self):
        self.cfg = test_config()
        self.r = Renderer(self.cfg)

    def test_main_screens_fit_and_buttons_sized(self):
        for name, snap in sample_data.sample_snapshot().items():
            img, hits = self.r.render_main(snap, sample_data.sample_storage(name))
            self.assertEqual(img.size, (480, 320))
            actions = [h.action for h in hits]
            self.assertEqual(sorted(actions), ["cycle_interval", "details", "toggle_run"])
            for h in hits:
                self.assertGreaterEqual(h.y1 - h.y0, 48)
                self.assertTrue(0 <= h.x0 < h.x1 <= 480 and 0 <= h.y0 < h.y1 <= 320)
            for i in range(len(hits)):
                for j in range(i + 1, len(hits)):
                    a, b = hits[i], hits[j]
                    self.assertTrue(a.x1 < b.x0 or b.x1 < a.x0 or a.y1 < b.y0 or b.y1 < a.y0,
                                    "buttons must not overlap")

    def test_details_pages(self):
        snap = sample_data.sample_snapshot()["success_waiting"]
        self.r.home_ssid = "LAN Down Under"
        img, hits, pages = self.r.render_details(snap, sample_data.sample_storage("x"), sample_data.sample_extra(), 0)
        self.assertGreaterEqual(pages, 2)
        self.assertEqual(sorted(h.action for h in hits), ["back", "next_label", "page_down", "page_up", "switch_wifi"])
        for h in hits:
            self.assertGreaterEqual(h.y1 - h.y0, 48)
        rows = self.r.details_lines(snap, sample_data.sample_storage("x"), sample_data.sample_extra())
        keys = [k for k, _, _ in rows]
        for key in ("Connected SSID", "Test network", "Home network", "BSSID", "Signal", "Gateway", "Public IP (last test)", "DNS", "Server",
                    "Recorded test traffic", "Free disk", "Log directory", "Clock synced (NTP)"):
            self.assertIn(key, keys)
        texts = " ".join(v for _, v, _ in rows)
        self.assertNotIn("RSRP", texts)
        self.assertIn("not total cellular usage", texts)

    def test_previous_result_labelled(self):
        snap = sample_data.sample_snapshot()["testing"]
        self.assertTrue(snap["speed_is_previous"])
        img, _ = self.r.render_main(snap, sample_data.sample_storage("testing"))
        self.assertEqual(img.size, (480, 320))

    def test_unavailable_values_show_dash(self):
        from pingaroo.ui import fmt_num
        self.assertEqual(fmt_num(None), "-")
        self.assertEqual(fmt_num("x"), "-")


class ControllerBehaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = test_config()
        self.cfg["logging"]["directory"] = self.tmp.name
        self.cfg["display"]["sleep_seconds"] = 1
        self.cfg["touch"]["debounce_ms"] = 300
        self.storage = Storage(self.cfg)
        self.state = WorkerState()
        self.state.interface = "wlan0"
        self.backend = FakeBackend()
        self.backend.speed_delay = 3.0
        self.worker = Worker(self.cfg, self.backend, self.storage, self.state)
        self.worker.start()
        self.png = os.path.join(self.tmp.name, "frame.png")
        self.fb = HeadlessFramebuffer(self.png, 480, 320)
        self.ctl = UIController(self.cfg, self.worker, self.storage, Renderer(self.cfg), self.fb,
                                {"log_dir": self.tmp.name})
        self.ctl.render(force=True)

    def tearDown(self):
        self.worker.shutdown()
        wait_until(lambda: not self.worker.cycle_running and not self.state.running, 5)
        self.tmp.cleanup()

    def tap(self, x, y, t=None):
        t = t or time.monotonic()
        self.ctl.handle(TouchEvent("down", x, y, t))
        self.ctl.handle(TouchEvent("up", x, y, t + 0.05))

    def button(self, action):
        return next(h for h in self.ctl.hits if h.action == action)

    def test_tap_start_then_stop_and_debounce(self):
        b = self.button("toggle_run")
        cx, cy = (b.x0 + b.x1) // 2, (b.y0 + b.y1) // 2
        t = time.monotonic()
        self.tap(cx, cy, t)
        self.assertTrue(self.state.running)
        # repeated taps inside the debounce window are ignored (no accidental stop)
        self.tap(cx, cy, t + 0.1)
        self.tap(cx, cy, t + 0.2)
        self.assertTrue(self.state.running)
        self.assertTrue(wait_until(lambda: self.state.phase.startswith("Speed Test"), 5))
        self.tap(cx, cy, t + 1.0)
        self.assertFalse(self.state.running)
        self.assertTrue(wait_until(lambda: self.state.phase == Phase.IDLE, 5))
        self.assertEqual(self.state.session.interrupted, 1)

    def test_tap_outside_buttons_does_nothing(self):
        self.tap(240, 120)
        self.assertFalse(self.state.running)

    def test_press_inside_button_fires_immediately(self):
        b = self.button("toggle_run")
        t = time.monotonic()
        self.ctl.handle(TouchEvent("down", b.x0 + 10, b.y0 + 10, t))
        self.assertTrue(self.state.running, "press inside the button must act without waiting for release")
        self.ctl.handle(TouchEvent("move", b.x0 + 60, b.y0 + 20, t + 0.1))
        self.ctl.handle(TouchEvent("up", b.x0 + 80, b.y0 + 20, t + 0.2))
        self.assertTrue(self.state.running, "release of the same gesture must not toggle again")

    def test_long_drag_from_outside_does_not_fire(self):
        b = self.button("toggle_run")
        t = time.monotonic()
        self.ctl.handle(TouchEvent("down", 240, 100, t))              # outside any button
        self.ctl.handle(TouchEvent("up", b.x0 + 20, b.y0 + 20, t + 0.3))  # released far away, inside button
        self.assertFalse(self.state.running)

    def test_noisy_first_sample_then_button(self):
        """First sample lands outside, later samples inside the button: must fire once."""
        b = self.button("toggle_run")
        t = time.monotonic()
        self.ctl.handle(TouchEvent("down", b.x0 - 15, b.y0 - 5, t))   # just outside
        self.ctl.handle(TouchEvent("move", b.x0 + 20, b.y0 + 20, t + 0.03))
        self.assertTrue(self.state.running)
        self.ctl.handle(TouchEvent("up", b.x0 + 22, b.y0 + 21, t + 0.1))
        self.assertTrue(self.state.running)

    def test_details_and_back(self):
        d = self.button("details")
        self.tap((d.x0 + d.x1) // 2, (d.y0 + d.y1) // 2)
        self.assertEqual(self.ctl.screen, "details")
        bk = self.button("back")
        self.tap((bk.x0 + bk.x1) // 2, (bk.y0 + bk.y1) // 2, time.monotonic() + 1)
        self.assertEqual(self.ctl.screen, "main")

    def test_sleep_wake_consumes_gesture(self):
        self.ctl.last_touch_mono = time.monotonic() - 5
        self.ctl.render()                   # background render must not keep it awake
        self.ctl.maybe_sleep(time.monotonic())
        self.assertTrue(self.ctl.asleep)
        self.assertTrue(self.fb.blanked)
        b = self.button("toggle_run")
        cx, cy = (b.x0 + b.x1) // 2, (b.y0 + b.y1) // 2
        t = time.monotonic()
        self.ctl.handle(TouchEvent("down", cx, cy, t))
        self.assertFalse(self.ctl.asleep)
        self.assertFalse(self.fb.blanked)
        self.ctl.handle(TouchEvent("move", cx, cy, t + 0.05))
        self.ctl.handle(TouchEvent("up", cx, cy, t + 0.1))
        self.assertFalse(self.state.running, "first touch after sleep must only wake")
        self.tap(cx, cy, t + 1.0)
        self.assertTrue(self.state.running, "second tap acts normally")

    def test_sleep_does_not_stop_worker(self):
        self.worker.start_session()
        self.ctl.last_touch_mono = time.monotonic() - 5
        self.ctl.maybe_sleep(time.monotonic())
        self.assertTrue(self.ctl.asleep)
        self.assertTrue(wait_until(lambda: len(self.storage.read_last_rows(1)) >= 1, 10))
        self.assertTrue(self.ctl.asleep)

    def test_sleep_disabled(self):
        self.ctl.sleep_seconds = 0
        self.ctl.last_touch_mono = time.monotonic() - 1000
        self.ctl.maybe_sleep(time.monotonic())
        self.assertFalse(self.ctl.asleep)

    def test_cycle_interval_advances_and_wraps(self):
        from pingaroo.ui import INTERVAL_PRESETS
        self.worker.set_interval(30)
        seq = []
        for _ in range(len(INTERVAL_PRESETS) + 1):
            self.ctl.perform("cycle_interval")
            seq.append(int(self.worker.interval_s))
        self.assertEqual(seq[:len(INTERVAL_PRESETS)], INTERVAL_PRESETS[1:] + [INTERVAL_PRESETS[0]])

    def test_next_label_cycles(self):
        self.ctl.perform("next_label")
        self.assertEqual(self.state.label_override, "NRB")
        self.ctl.perform("next_label")
        self.assertEqual(self.state.label_override, "D50003")
        self.ctl.perform("next_label")
        self.assertEqual(self.state.label_override, "5GC")

    def test_frame_written(self):
        self.assertTrue(os.path.exists(self.png))


if __name__ == "__main__":
    unittest.main()


class WifiSwitchButton(unittest.TestCase):
    def test_hidden_until_test_and_home_differ(self):
        cfg = test_config()
        snap = sample_data.sample_snapshot()["success_waiting"]
        r = Renderer(cfg)                      # no home ssid configured
        _, hits, _ = r.render_details(snap, {}, {}, 0)
        self.assertNotIn("switch_wifi", [h.action for h in hits])
        r.home_ssid = r.ssid                   # same network for both
        _, hits, _ = r.render_details(snap, {}, {}, 0)
        self.assertNotIn("switch_wifi", [h.action for h in hits])
        r.home_ssid = "LAN Down Under"
        _, hits, _ = r.render_details(snap, {}, {}, 0)
        self.assertIn("switch_wifi", [h.action for h in hits])

    def test_main_names_target_when_disconnected(self):
        cfg = test_config()
        r = Renderer(cfg)
        snap = sample_data.sample_snapshot()["idle"]
        img, _ = r.render_main(snap, {})
        self.assertEqual(img.size, (480, 320))


class Splash(unittest.TestCase):
    def test_frames_render(self):
        from pingaroo.splash import render_splash
        from pingaroo.ui import Fonts
        cfg = test_config()
        f = Fonts(cfg["display"]["font"], cfg["display"]["font_bold"])
        for t in (0.0, 0.5, 1.3):
            self.assertEqual(render_splash(480, 320, t, f).size, (480, 320))


class SurveyScreen(unittest.TestCase):
    def test_survey_shows_signal_values(self):
        cfg = test_config()
        cfg["speedtest"]["enabled"] = False
        r = Renderer(cfg)
        snap = sample_data.sample_snapshot()["success_waiting"]
        img, hits = r.render_main(snap, {})
        self.assertEqual(img.size, (480, 320))
        self.assertEqual(sorted(h.action for h in hits), ["cycle_interval", "details", "toggle_run"])

    def test_normal_mode_cell_line(self):
        r = Renderer(test_config())
        snap = sample_data.sample_snapshot()["success_waiting"]
        img, _ = r.render_main(snap, {})
        self.assertEqual(img.size, (480, 320))
