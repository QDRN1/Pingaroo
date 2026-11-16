import http.client
import http.server
import json
import os
import tempfile
import threading
import time
import unittest

from PIL import Image

from pingaroo import router
from pingaroo.app import AlertSchedule, UIController, brightness_level, read_snooze, write_snooze
from pingaroo.config import DEFAULTS, parse_hhmm
from pingaroo.display import HeadlessFramebuffer
from pingaroo.remote import RemoteView
from pingaroo.storage import Storage
from pingaroo.touch import TouchEvent
from pingaroo.ui import Renderer
from pingaroo.worker import Worker, WorkerState
from tests.fakes import FakeBackend, test_config, wait_until

DISP = dict(DEFAULTS["display"])


class Schedule(unittest.TestCase):
    def test_shows_one_minute_every_fifteen(self):
        a = AlertSchedule(900, 60)
        self.assertTrue(a.update(True, False, 1000))        # starts at once
        self.assertTrue(a.update(True, False, 1059))
        self.assertFalse(a.update(True, False, 1060))       # one minute, then off
        self.assertFalse(a.update(True, False, 1899))
        self.assertTrue(a.update(True, False, 1900))        # fifteen minutes after the start
        self.assertFalse(a.update(True, False, 1961))

    def test_dismiss_waits_for_next_slot(self):
        a = AlertSchedule(900, 60)
        self.assertTrue(a.update(True, False, 1000))
        a.dismiss()
        self.assertFalse(a.update(True, False, 1001))
        self.assertFalse(a.update(True, False, 1899))
        self.assertTrue(a.update(True, False, 1900))

    def test_snooze_and_recovery(self):
        a = AlertSchedule(900, 60)
        self.assertFalse(a.update(True, True, 1000))        # snoozed: nothing
        self.assertTrue(a.update(True, False, 1001))        # snooze over: alert at once
        self.assertFalse(a.update(False, False, 1002))      # logged back in: alert gone
        self.assertTrue(a.update(True, False, 1003))        # a new logout alerts immediately

    def test_brightness_during_alert(self):
        self.assertEqual(brightness_level(9999, parse_hhmm("12:00"), DISP, alert=True), 1.0)
        self.assertEqual(brightness_level(9999, parse_hhmm("23:00"), DISP, alert=True), DISP["night_level"])
        self.assertEqual(brightness_level(9999, parse_hhmm("23:00"), DISP), DISP["night_dim_level"])

    def test_snooze_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            self.assertEqual(read_snooze(p), 0.0)
            write_snooze(p, 1234.5)
            self.assertEqual(read_snooze(p), 1234.5)
            with open(p, "w") as fh:
                fh.write("not json")
            self.assertEqual(read_snooze(p), 0.0)


class AlertOnScreen(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = test_config()
        self.cfg["logging"]["directory"] = self.tmp.name
        self.cfg["touch"]["debounce_ms"] = 300
        self.storage = Storage(self.cfg)
        self.state = WorkerState()
        self.state.interface = "wlan0"
        self.worker = Worker(self.cfg, FakeBackend(), self.storage, self.state)
        self.worker.start()
        self.fb = HeadlessFramebuffer(os.path.join(self.tmp.name, "frame.png"), 480, 320)
        self.ctl = UIController(self.cfg, self.worker, self.storage, Renderer(self.cfg), self.fb,
                                {"log_dir": self.tmp.name})
        self.ctl.render(force=True)

    def tearDown(self):
        self.worker.shutdown()
        wait_until(lambda: not self.worker.cycle_running, 5)
        self.tmp.cleanup()

    def tap(self, hit, t):
        x, y = (hit.x0 + hit.x1) // 2, (hit.y0 + hit.y1) // 2
        self.ctl.handle(TouchEvent("down", x, y, t))
        self.ctl.handle(TouchEvent("up", x, y, t + 0.05))

    def actions(self):
        return sorted(h.action for h in self.ctl.hits)

    def test_logged_in_shows_no_alert(self):
        self.state.router_login = "ok"
        self.ctl.render()
        self.assertFalse(self.ctl.alert_showing)
        self.assertIn("toggle_run", self.actions())

    def test_admin_in_use_does_not_alert(self):
        self.state.router_login = "busy"
        self.ctl.render()
        self.assertFalse(self.ctl.alert_showing)

    def test_logged_out_alert_dismiss_and_snooze(self):
        self.state.router_login = "rejected"
        self.ctl.render()
        self.assertTrue(self.ctl.alert_showing)
        self.assertEqual(self.actions(), ["alert_dismiss", "alert_snooze"])
        frame_before = self.ctl.frame[0]
        self.tap(next(h for h in self.ctl.hits if h.action == "alert_dismiss"), time.monotonic())
        self.assertFalse(self.ctl.alert_showing)
        self.assertIn("toggle_run", self.actions(), "the normal screen is back after Dismiss")
        self.assertGreater(self.ctl.frame[0], frame_before)
        # next slot: alert again, then snooze for 24 hours
        self.ctl.alert.next = 0.0
        self.ctl.render()
        self.assertTrue(self.ctl.alert_showing)
        self.tap(next(h for h in self.ctl.hits if h.action == "alert_snooze"), time.monotonic() + 1)
        self.assertFalse(self.ctl.alert_showing)
        self.assertAlmostEqual(self.ctl.snooze_until, time.time() + 24 * 3600, delta=10)
        self.assertAlmostEqual(read_snooze(self.ctl.snooze_path), self.ctl.snooze_until, delta=1)
        self.ctl.alert.next = 0.0
        self.ctl.render()
        self.assertFalse(self.ctl.alert_showing, "snoozed: no alert even at the next slot")

    def test_alert_clears_when_login_recovers(self):
        self.state.router_login = "locked"
        self.ctl.render()
        self.assertTrue(self.ctl.alert_showing)
        self.state.router_login = "ok"
        self.ctl.render()
        self.assertFalse(self.ctl.alert_showing)

    def test_chip_changes_the_main_screen(self):
        r = Renderer(self.cfg)
        snap, storage = self.ctl.snapshot()
        frames = {}
        for state in ("n/a", "ok", "rejected", "busy", "unreachable"):
            s = dict(snap, router_login=state)
            frames[state] = r.render_main(s, storage, "", now=1000.0)[0].crop((300, 58, 480, 84)).tobytes()
        self.assertNotEqual(frames["n/a"], frames["ok"])
        self.assertNotEqual(frames["ok"], frames["rejected"])
        self.assertNotEqual(frames["rejected"], frames["busy"])
        img, hits = r.render_alert(dict(snap, router_login_detail="login rejected (wrong password?)"), 24)
        self.assertEqual(img.size, (480, 320))
        for h in hits:
            self.assertGreaterEqual(h.y1 - h.y0, 60, "alert buttons are finger sized")


class GatewayLoginStates(unittest.TestCase):
    def _serve(self, reply):
        seen = {"logouts": 0}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                if self.path.startswith("/cgi-bin/Logout"):
                    seen["logouts"] += 1
                    body = {"Success": True}
                else:
                    body = reply
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, seen

    def _cfg(self, srv, d):
        pw = os.path.join(d, "secret")
        with open(pw, "w") as fh:
            fh.write("secret\n")
        cfg = test_config()
        cfg["router"].update(type="http_json", base_url=f"http://127.0.0.1:{srv.server_port}", username="attadmin",
                             password_file=pw, login_path="/cgi-bin/Login", login_method="ncm", password_hash="sha256",
                             login_fields={"username": "UserName", "password": "Password"}, status_paths=[], fields={})
        return cfg

    def check(self, reply, expected, logouts):
        srv, seen = self._serve(reply)
        try:
            with tempfile.TemporaryDirectory() as d:
                out = router.check_login(self._cfg(srv, d))
                self.assertEqual(out["login_state"], expected)
                self.assertEqual(seen["logouts"], logouts)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_accepted_logs_out_again(self):
        self.check({"Success": True, "Login": 1, "sessionID": "x"}, "ok", 1)

    def test_rejected(self):
        self.check({"Success": False, "Login": 0, "FailReason": "err_0001", "LoginAttempt": 1}, "rejected", 0)

    def test_locked_after_five(self):
        self.check({"Success": False, "Login": 0, "LoginAttempt": 5}, "locked", 0)

    def test_admin_page_in_use(self):
        self.check({"Success": False, "Login": 2}, "busy", 0)

    def test_unreachable_and_unconfigured(self):
        cfg = test_config()
        self.assertEqual(router.check_login(cfg), {})
        with tempfile.TemporaryDirectory() as d:
            srv, _ = self._serve({})
            c = self._cfg(srv, d)
            srv.shutdown()
            srv.server_close()
            c["router"]["timeout_seconds"] = 1
            self.assertEqual(router.check_login(c)["login_state"], "unreachable")


class BetweenCycleCheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.cfg = test_config()
        self.cfg["logging"]["directory"] = self.tmp.name
        self.cfg["router"]["type"] = "http_json"
        self.cfg["router"]["login_check_seconds"] = 600
        self.backend = FakeBackend()
        self.state = WorkerState()
        self.worker = Worker(self.cfg, self.backend, Storage(self.cfg), self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def checks(self):
        return self.backend.calls.count("router_login_check")

    def test_checks_only_when_due(self):
        self.state.router_login, self.state.router_login_mono = "ok", time.monotonic()
        self.worker._maybe_check_router_login(1200)
        self.assertEqual(self.checks(), 0, "checked recently")
        self.state.router_login_mono = time.monotonic() - 601
        self.worker._maybe_check_router_login(20)
        self.assertEqual(self.checks(), 0, "a cycle is about to start")
        self.backend.login_state = "rejected"
        self.worker._maybe_check_router_login(1200)
        self.assertEqual(self.checks(), 1)
        self.assertEqual(self.state.router_login, "rejected")

    def test_no_retries_while_rejected_or_on_home(self):
        self.state.router_login, self.state.router_login_mono = "rejected", time.monotonic() - 5000
        self.worker._maybe_check_router_login(1200)
        self.assertEqual(self.checks(), 0, "a wrong password is retried once per cycle, not in between")
        self.state.router_login, self.state.active_target = "ok", "home"
        self.worker._maybe_check_router_login(1200)
        self.assertEqual(self.checks(), 0)


class RemoteViewServer(unittest.TestCase):
    def setUp(self):
        self.taps = []
        self.img = Image.new("RGB", (480, 320), (10, 200, 30))
        self.rv = RemoteView("127.0.0.1", 0, 480, 320, lambda: (7, self.img), lambda x, y: self.taps.append((x, y)))
        self.assertEqual(self.rv.start(), "")

    def tearDown(self):
        self.rv.stop()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.rv.port, timeout=5)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), data

    def test_page_and_screen(self):
        status, ctype, body = self.req("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Pingaroo Remote", body)
        self.assertIn(b"480", body)
        status, ctype, png = self.req("GET", "/screen.png?123")
        self.assertEqual((status, ctype), (200, "image/png"))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.req("GET", "/etc/passwd")[0], 404)

    def test_tap_needs_header_and_valid_point(self):
        good = json.dumps({"x": 120, "y": 260})
        self.assertEqual(self.req("POST", "/tap", good, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.taps, [])
        hdr = {"Content-Type": "application/json", "X-Pingaroo": "1"}
        self.assertEqual(self.req("POST", "/tap", good, hdr)[0], 204)
        self.assertEqual(self.taps, [(120, 260)])
        for bad in ('{"x": 480, "y": 10}', '{"x": -1, "y": 10}', '{"x": "a"}', "nope", "x" * 300):
            self.assertEqual(self.req("POST", "/tap", bad, hdr)[0], 400, bad[:20])
        self.assertEqual(self.taps, [(120, 260)])

    def test_no_frame_yet(self):
        rv = RemoteView("127.0.0.1", 0, 480, 320, lambda: (0, None), lambda x, y: None)
        self.assertEqual(rv.start(), "")
        try:
            c = http.client.HTTPConnection("127.0.0.1", rv.port, timeout=5)
            c.request("GET", "/screen.png")
            self.assertEqual(c.getresponse().status, 503)
            c.close()
        finally:
            rv.stop()

    def test_port_in_use_is_reported(self):
        rv = RemoteView("127.0.0.1", self.rv.port, 480, 320, lambda: (0, None), lambda x, y: None)
        self.assertIn("cannot listen", rv.start())


if __name__ == "__main__":
    unittest.main()
