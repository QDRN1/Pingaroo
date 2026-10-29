import os
import struct
import tempfile
import unittest
from unittest import mock

from crwb import clock, config, netinfo, probes, speedtest
from crwb.shell import RunResult
from crwb.touch import Calibration


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
