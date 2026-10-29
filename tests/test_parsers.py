import os
import struct
import tempfile
import unittest
from unittest import mock

from pingaroo import clock, config, netinfo, probes, speedtest
from pingaroo.shell import RunResult
from pingaroo.touch import Calibration


class PingParse(unittest.TestCase):
    def test_normal(self):
        out = ("PING 1.1.1.1 (1.1.1.1) from 192.168.8.142 wlan0: 56(84) bytes of data.\n\n--- 1.1.1.1 ping statistics ---\n"
               "4 packets transmitted, 3 received, 25% packet loss, time 904ms\n"
               "rtt min/avg/max/mdev = 31.123/38.456/45.789/5.0 ms\n")
        p = probes.parse_ping("1.1.1.1", out)
        self.assertEqual((p.sent, p.received, p.loss_pct), (4, 3, 25.0))
        self.assertAlmostEqual(p.avg_ms, 38.456)
        self.assertTrue(p.ok)

    def test_total_loss(self):
        out = "--- 8.8.8.8 ping statistics ---\n4 packets transmitted, 0 received, 100% packet loss, time 3080ms\n"
        p = probes.parse_ping("8.8.8.8", out)
        self.assertFalse(p.ok)
        self.assertEqual(p.loss_pct, 100.0)
        self.assertIsNone(p.avg_ms)

    def test_garbage(self):
        p = probes.parse_ping("x", "ping: connect: Network is unreachable")
        self.assertFalse(p.ok)
        self.assertTrue(p.error)


class NmcliParse(unittest.TestCase):
    def test_device_show(self):
        text = ("GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:Kangaroo Labs\nIP4.ADDRESS[1]:192.168.8.142/24\n"
                "IP4.GATEWAY:192.168.8.1\nIP4.DNS[1]:192.168.8.1\nIP6.ADDRESS[1]:fe80\\:\\:1/64\n"
                "IP6.ADDRESS[2]:2600\\:1\\:\\:5/64\nDHCP4.OPTION[3]:domain_name_servers = 192.168.8.1 8.8.8.8\n")
        iw = "Connected to a4:3e:51:12:34:56 (on wlan0)\n\tSSID: Kangaroo Labs\n\tfreq: 5180\n\tsignal: -52 dBm\n\ttx bitrate: 390.0 MBit/s VHT-MCS 9\n"
        def fake_run(cmd, timeout, **kw):
            if cmd[0] == "nmcli":
                return RunResult(0, text, "")
            if cmd[0] == "iw":
                return RunResult(0, iw, "")
            return RunResult(1, "", "")
        with mock.patch.object(netinfo.shell, "run", side_effect=fake_run):
            st = netinfo.wifi_state("wlan0", "Kangaroo Labs")
        self.assertTrue(st.connected)
        self.assertEqual(st.ip, "192.168.8.142")
        self.assertEqual(st.prefix, 24)
        self.assertEqual(st.dhcp_dns, ["192.168.8.1", "8.8.8.8"])
        self.assertEqual(st.ipv6, ["2600:1::5/64"])
        self.assertEqual(st.signal_dbm, -52)
        self.assertEqual((st.channel, st.band), (36, "5 GHz"))
        self.assertEqual(st.tx_rate_mbps, 390.0)

    def test_wrong_ssid_not_connected(self):
        text = "GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:10.0.0.5/24\n"
        iw = "Connected to 00:11:22:33:44:55 (on wlan0)\n\tSSID: Other\n\tfreq: 2437\n\tsignal: -70 dBm\n"
        def fake_run(cmd, timeout, **kw):
            return RunResult(0, text if cmd[0] == "nmcli" else iw, "")
        with mock.patch.object(netinfo.shell, "run", side_effect=fake_run):
            st = netinfo.wifi_state("wlan0", "Kangaroo Labs")
        self.assertFalse(st.connected)
        self.assertTrue(st.associated)
        self.assertEqual((st.channel, st.band), (6, "2.4 GHz"))

    def test_freq_to_channel(self):
        self.assertEqual(netinfo.freq_to_channel(2412), (1, "2.4 GHz"))
        self.assertEqual(netinfo.freq_to_channel(5745), (149, "5 GHz"))
        self.assertEqual(netinfo.freq_to_channel(None), (None, ""))


class DnsCodec(unittest.TestCase):
    def test_roundtrip_a_record(self):
        qid = 0x1234
        q = probes._build_query("example.com", 1, qid)
        self.assertEqual(q[:2], b"\x12\x34")
        # craft response: header + question + one A answer with name pointer
        header = struct.pack(">HHHHHH", qid, 0x8180, 1, 1, 0, 0)
        question = q[12:]
        answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 60, 4) + bytes([93, 184, 216, 34])
        ips = probes.parse_dns_response(header + question + answer, qid, 1)
        self.assertEqual(ips, ["93.184.216.34"])

    def test_nxdomain(self):
        qid = 7
        header = struct.pack(">HHHHHH", qid, 0x8183, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            probes.parse_dns_response(header, qid, 1)


class HttpParse(unittest.TestCase):
    def _sock(self, payload: bytes):
        chunks = [payload[i:i + 7] for i in range(0, len(payload), 7)]
        class S:
            def recv(self_inner, n):
                return chunks.pop(0) if chunks else b""
        return S()

    def test_content_length(self):
        status, headers, body = probes._read_http_response(self._sock(b"HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nsuccess\n"))
        self.assertEqual(status, 200)
        self.assertEqual(body, b"success\n")

    def test_chunked(self):
        raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nsucc\r\n3\r\ness\r\n0\r\n\r\n"
        status, headers, body = probes._read_http_response(self._sock(raw))
        self.assertEqual(body, b"success")

    def test_redirect_status(self):
        status, headers, body = probes._read_http_response(self._sock(b"HTTP/1.1 302 Found\r\nLocation: http://portal\r\nContent-Length: 0\r\n\r\n"))
        self.assertEqual(status, 302)
        self.assertEqual(headers["location"], "http://portal")


class SpeedtestUnits(unittest.TestCase):
    def test_bytes_per_second_to_mbps(self):
        self.assertEqual(speedtest.bytes_per_second_to_mbps(10_925_000), 87.4)
        self.assertEqual(speedtest.bytes_per_second_to_mbps(0), 0.0)
        self.assertIsNone(speedtest.bytes_per_second_to_mbps("abc"))
        self.assertIsNone(speedtest.bytes_per_second_to_mbps(None))

    def test_build_command(self):
        cfg = {"speedtest": {"command": "speedtest", "server_id": 1234, "bind_interface": True, "extra_args": []}}
        cmd = speedtest.build_command(cfg, "wlan1")
        self.assertIn("--interface", cmd)
        self.assertEqual(cmd[cmd.index("--interface") + 1], "wlan1")
        self.assertEqual(cmd[cmd.index("--server-id") + 1], "1234")
        self.assertIn("--format=jsonl", cmd)
        cfg["speedtest"]["server_id"] = 0
        self.assertNotIn("--server-id", speedtest.build_command(cfg, "wlan0"))


class ConfigEdit(unittest.TestCase):
    def test_set_value_and_reload(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "Config A"\n\n# comment\n[wifi]\nssid = "Kangaroo Labs"\n')
            config.set_value(path, "app", "label", "APN test")
            config.set_value(path, "app", "interval_seconds", 600)
            config.set_value(path, "display", "sleep_seconds", 0)
            cfg = config.load(path)
            self.assertEqual(cfg["app"]["label"], "APN test")
            self.assertEqual(cfg["app"]["interval_seconds"], 600)
            self.assertEqual(cfg["display"]["sleep_seconds"], 0)
            self.assertEqual(cfg["wifi"]["ssid"], "Kangaroo Labs")
            with open(path) as fh:
                self.assertIn("# comment", fh.read())

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.toml")
            with open(path, "w") as fh:
                fh.write("[app]\nlabl = 'x'\n")
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.toml")
            with open(path, "w") as fh:
                fh.write("[speedtest]\ntimeout_seconds = 5\n")
            with self.assertRaises(config.ConfigError):
                config.load(path)

    def test_read_label_live(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "One"\n')
            cfg = config.load(path)
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "Two"\n')
            self.assertEqual(config.read_label(cfg), "Two")


class ClockFormat(unittest.TestCase):
    def test_mmss(self):
        self.assertEqual(clock.mmss(300), "05:00")
        self.assertEqual(clock.mmss(59.6), "01:00")
        self.assertEqual(clock.mmss(-3), "00:00")

    def test_age(self):
        self.assertEqual(clock.age_text(5), "5s ago")
        self.assertEqual(clock.age_text(125), "2m ago")
        self.assertEqual(clock.age_text(None), "-")

    def test_iso_has_timezone(self):
        self.assertRegex(clock.iso(), r"[+-]\d\d:\d\d$|Z$")


class CalibrationMap(unittest.TestCase):
    def test_identity_corners(self):
        c = Calibration(480, 320, 0, 4095, 0, 4095, False, False, False)
        self.assertEqual(c.map(0, 0), (0, 0))
        self.assertEqual(c.map(4095, 4095), (479, 319))

    def test_swap_invert_clamp(self):
        c = Calibration(480, 320, 200, 3900, 200, 3900, True, False, True)
        # raw (y=200, x=3900) -> swap -> x=200 (left), y=3900 inverted -> top
        self.assertEqual(c.map(3900, 200), (0, 0))
        self.assertEqual(c.map(-5, 5000), (479, 319))


if __name__ == "__main__":
    unittest.main()


class WifiCommand(unittest.TestCase):
    def test_wifi_sets_ssid_and_profile(self):
        from pingaroo import cli
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "x"\n')
            rc = cli.main(["--config", path, "wifi", "--ssid", "HomeNet", "--profile", "preconfigured"])
            self.assertEqual(rc, 0)
            cfg = config.load(path)
            self.assertEqual(cfg["wifi"]["ssid"], "HomeNet")
            self.assertEqual(cfg["wifi"]["connection_name"], "preconfigured")
            rc = cli.main(["--config", path, "wifi", "--ssid", "Kangaroo Labs"])
            self.assertEqual(config.load(path)["wifi"]["connection_name"], "Kangaroo Labs")


class TouchDecoding(unittest.TestCase):
    """ads7846 ordering: BTN_TOUCH=1 arrives before the new touch's ABS_X/ABS_Y."""

    def _tap(self, dec, rx, ry, t=0.0):
        out = []
        out += dec.feed(dec.EV_KEY, dec.BTN_TOUCH, 1, t)        # press, stale coords
        out += dec.feed(dec.EV_ABS, dec.ABS_X, rx, t)
        out += dec.feed(dec.EV_ABS, dec.ABS_Y, ry, t)
        out += dec.feed(dec.EV_SYN, 0, 0, t)
        out += dec.feed(dec.EV_ABS, dec.ABS_X, rx + 5, t + 0.05)  # tiny jitter
        out += dec.feed(dec.EV_SYN, 0, 0, t + 0.05)
        out += dec.feed(dec.EV_KEY, dec.BTN_TOUCH, 0, t + 0.1)
        out += dec.feed(dec.EV_SYN, 0, 0, t + 0.1)
        return out

    def test_press_uses_fresh_coordinates(self):
        from pingaroo.touch import TouchDecoder
        dec = TouchDecoder(Calibration(480, 320, 0, 4095, 0, 4095, False, False, False))
        first = self._tap(dec, 400, 400)
        second = self._tap(dec, 3600, 3600, t=1.0)
        kinds = [e.kind for e in second]
        self.assertEqual(kinds, ["down", "move", "up"])
        down, _, up = second
        self.assertGreater(down.x, 400, "press must not carry the previous touch position")
        self.assertLess(abs(down.x - up.x), 5)
        self.assertEqual([e.kind for e in first], ["down", "move", "up"])

    def test_release_without_coords(self):
        from pingaroo.touch import TouchDecoder
        dec = TouchDecoder(Calibration(480, 320, 0, 4095, 0, 4095, False, False, False))
        out = dec.feed(dec.EV_KEY, dec.BTN_TOUCH, 1, 0) + dec.feed(dec.EV_KEY, dec.BTN_TOUCH, 0, 0)
        self.assertEqual([e.kind for e in out], ["up"])


class CornerRefinement(unittest.TestCase):
    def _check(self, inverted):
        from pingaroo.touch import refine_axis
        truth = Calibration(480, 320, 450, 3700, 280, 3520, False, False, inverted)
        wrong = Calibration(480, 320, 296, 3911, 22, 3837, False, False, inverted)
        # where taps at the true physical edges (raw values of truth's edges) land through `wrong`
        # x axis
        lo_raw, hi_raw = truth.x_min, truth.x_max
        m_lo = wrong.map(lo_raw, 2000)[0]
        m_hi = wrong.map(hi_raw, 2000)[0]
        x_min, x_max = refine_axis(wrong.x_min, wrong.x_max, False, 480, m_lo, m_hi, 0, 479)
        self.assertAlmostEqual(x_min, truth.x_min, delta=12)
        self.assertAlmostEqual(x_max, truth.x_max, delta=12)
        # y axis (possibly inverted): the low screen edge is the high raw value when inverted
        top_raw, bottom_raw = (truth.y_max, truth.y_min) if inverted else (truth.y_min, truth.y_max)
        m_top = wrong.map(2000, top_raw)[1]
        m_bottom = wrong.map(2000, bottom_raw)[1]
        y_min, y_max = refine_axis(wrong.y_min, wrong.y_max, inverted, 320, m_top, m_bottom, 0, 319)
        self.assertAlmostEqual(y_min, truth.y_min, delta=12)
        self.assertAlmostEqual(y_max, truth.y_max, delta=12)

    def test_refine_plain(self):
        self._check(False)

    def test_refine_inverted(self):
        self._check(True)

    def test_refine_on_real_numbers(self):
        from pingaroo.touch import refine_axis
        # Collin's corner taps through the first calibration: x 24/447, y 30/294
        x_min, x_max = refine_axis(296, 3911, False, 480, (24 + 14) / 2, (447 + 447) / 2)
        y_min, y_max = refine_axis(22, 3837, True, 320, (38 + 28) / 2, (294 + 294) / 2)
        cal = Calibration(480, 320, x_min, x_max, y_min, y_max, False, False, True)
        old = Calibration(480, 320, 296, 3911, 22, 3837, False, False, True)
        # a tap that used to land at (19, 33) now lands near (3, 3)
        raw_x = 296 + 19 / 479 * (3911 - 296)
        raw_y = 22 + (1 - 33 / 319) * (3837 - 22)
        x, y = cal.map(int(raw_x), int(raw_y))
        self.assertLess(abs(x - 3), 4)
        self.assertLess(abs(y - 3), 4)
        self.assertEqual(old.map(int(raw_x), int(raw_y))[0], 19)


class RouterAdapter(unittest.TestCase):
    def test_redact(self):
        from pingaroo.router import redact
        data = {"5G_WLAN": "Kangaroo Labs/hunter2(sae-mixed)", "2_4G_WLAN": "Kangaroo Labs/hunter2(sae-mixed)",
                "Password": "x", "UserName": "", "Key": "abc", "APN_Name": "d50003.etr", "nested": {"psk": "z"},
                "list": [{"token": "t"}], "Fwver": "254311"}
        out = redact(data)
        self.assertEqual(out["5G_WLAN"], "Kangaroo Labs (sae-mixed)")
        self.assertEqual(out["Password"], "REDACTED")
        self.assertEqual(out["Key"], "REDACTED")
        self.assertEqual(out["nested"]["psk"], "REDACTED")
        self.assertEqual(out["list"][0]["token"], "REDACTED")
        self.assertEqual(out["APN_Name"], "d50003.etr")
        self.assertEqual(out["Fwver"], "254311")
        self.assertNotIn("hunter2", str(out))

    def test_dig_and_coerce(self):
        from pingaroo.router import dig, _coerce
        data = {"mobile": {"signal": {"rsrp": "-95 dBm", "cells": [{"pci": 301}]}}}
        self.assertEqual(dig(data, "mobile.signal.rsrp"), "-95 dBm")
        self.assertEqual(dig(data, "mobile.signal.cells.0.pci"), 301)
        self.assertIsNone(dig(data, "mobile.nope"))
        self.assertEqual(_coerce("-95 dBm"), -95)
        self.assertEqual(_coerce("12.5"), 12.5)
        self.assertEqual(_coerce("n77"), "n77")

    def test_ncm_sha256_login_and_fields(self):
        import http.server, json as js, threading, hashlib
        from pingaroo import router
        from tests.fakes import test_config

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = js.loads(self.rfile.read(n).decode())
                ok = body.get("Password") == hashlib.sha256(b"secret").hexdigest() and "token" in body
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(js.dumps({"Success": ok, "Login": 1 if ok else 0,
                                           "sessionID": "abc"} if ok else {"Success": False, "Login": 0}).encode())

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                if self.path.startswith("/cgi-bin/GetENDCInfo"):
                    self.wfile.write(js.dumps({"nrband": "N77", "nr5g_rsrp": "-72", "nr5g_snr": "2",
                                               "nr5g_rsrq": "-9", "nr5g_rssi": "-63", "nr5g_pci": "265",
                                               "nr5g_tac": "17939", "nr5g_gci": "83691327", "nrbandw": "9"}).encode())
                else:
                    self.wfile.write(js.dumps({"APN_Name": "5gcbroadband"}).encode())

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                pw = os.path.join(d, "secret")
                with open(pw, "w") as fh:
                    fh.write("secret\n")
                cfg = test_config()
                cfg["router"].update(type="http_json", base_url=f"http://127.0.0.1:{srv.server_port}",
                                     username="attadmin", password_file=pw, login_path="/cgi-bin/Login",
                                     login_method="ncm", password_hash="sha256",
                                     login_fields={"username": "UserName", "password": "Password"},
                                     status_paths=["/cgi-bin/GetENDCInfo", "/cgi-bin/GetLTEBasicSetting"],
                                     fields={"cell_5g_band": "nrband", "cell_5g_rsrp": "nr5g_rsrp",
                                             "cell_5g_sinr": "nr5g_snr", "cell_5g_rsrq": "nr5g_rsrq",
                                             "cell_pci": "nr5g_pci", "cell_tac": "nr5g_tac",
                                             "cell_cell_id": "nr5g_gci", "cell_apn": "APN_Name"})
                out = router.fetch(cfg)
                self.assertNotIn("error", out)
                self.assertEqual(out["cell_5g_band"], "N77")
                self.assertEqual(out["cell_5g_rsrp"], -72)
                self.assertEqual(out["cell_cell_id"], 83691327)
                self.assertEqual(out["cell_apn"], "5gcbroadband")
                with open(pw, "w") as fh:
                    fh.write("wrong\n")
                out = router.fetch(cfg)
                self.assertIn("rejected", out.get("login_error", ""))
                self.assertNotIn("error", out, "data served without a session must still be recorded")
                self.assertEqual(out["cell_5g_band"], "N77")
        finally:
            srv.shutdown()

    def test_fetch_none_and_http(self):
        import http.server, json as js, threading
        from pingaroo import router
        from tests.fakes import test_config
        cfg = test_config()
        self.assertEqual(router.fetch(cfg), {})

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n).decode()
                ok = "password=secret" in body
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"token":"abc"}' if ok else b'{}')

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if self.path == "/api/mobile":
                    self.wfile.write(js.dumps({"signal": {"rsrp": "-97 dBm", "sinr": 9}, "band": "B66", "apn": "broadband"}).encode())
                else:
                    self.wfile.write(b'{"model": "NCM1120"}')

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                pw = os.path.join(d, "secret")
                with open(pw, "w") as fh:
                    fh.write("secret\n")
                cfg["router"].update(type="http_json", base_url=f"http://127.0.0.1:{srv.server_port}",
                                     password_file=pw, login_path="/login", login_method="form",
                                     status_paths=["/api/mobile", "/api/system"],
                                     fields={"cell_rsrp": "signal.rsrp", "cell_sinr": "signal.sinr", "cell_band": "band",
                                             "cell_apn": "apn", "cell_firmware": "page:/api/system:model"})
                out = router.fetch(cfg)
                self.assertNotIn("error", out)
                self.assertEqual(out["cell_rsrp"], -97)
                self.assertEqual(out["cell_sinr"], 9)
                self.assertEqual(out["cell_band"], "B66")
                self.assertEqual(out["cell_firmware"], "NCM1120")
                self.assertIn("/api/mobile", out["raw"])
                with open(pw, "w") as fh:
                    fh.write("wrong\n")
                out = router.fetch(cfg)
                self.assertIn("login HTTP 401", out.get("login_error", ""))
        finally:
            srv.shutdown()
