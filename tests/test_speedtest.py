import os
import time
import unittest

from pingaroo import speedtest
from pingaroo.shell import CancelToken
from tests.fakes import test_config

FAKES = os.path.join(os.path.dirname(__file__), "fake_speedtest")


def cfg_for(script: str, timeout: float = 5) -> dict:
    cfg = test_config()
    cfg["speedtest"]["command"] = os.path.join(FAKES, script)
    cfg["speedtest"]["timeout_seconds"] = timeout
    return cfg


class SpeedtestRunner(unittest.TestCase):
    def test_success(self):
        phases = []
        r = speedtest.run(cfg_for("success.sh"), "wlan0", CancelToken(), lambda k, i: phases.append(k))
        self.assertEqual(r.status, "SUCCESS", r.error)
        self.assertEqual(r.download_mbps, 87.4)
        self.assertEqual(r.upload_mbps, 21.9)
        self.assertEqual(r.latency_ms, 41.3)
        self.assertEqual(r.jitter_ms, 3.2)
        self.assertEqual(r.packet_loss_pct, 0.0)
        self.assertEqual((r.download_bytes, r.upload_bytes), (52_000_000, 12_300_000))
        self.assertEqual(r.public_ip, "166.137.12.44")
        self.assertEqual(r.server_id, 18531)
        self.assertEqual(r.server_name, "US Internet")
        self.assertIn("Minneapolis", r.server_location)
        self.assertTrue(r.result_url.startswith("https://www.speedtest.net/result/"))
        self.assertEqual(r.tool_interface, "wlan0")
        self.assertFalse(r.partial)
        self.assertEqual(phases, ["ping", "download", "upload"])
        self.assertIsNotNone(r.raw)

    def test_error(self):
        r = speedtest.run(cfg_for("error.sh"), "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_FAILED")
        self.assertIn("Cannot open socket", r.error)
        self.assertIsNone(r.download_mbps)

    def test_pinned_server_error_is_labelled(self):
        cfg = cfg_for("error.sh")
        cfg["speedtest"]["server_id"] = 999
        r = speedtest.run(cfg, "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_FAILED")
        self.assertIn("pinned server 999 unavailable", r.error)
        self.assertTrue(r.server_pinned)

    def test_malformed(self):
        r = speedtest.run(cfg_for("malformed.sh"), "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_FAILED")
        self.assertIn("malformed", r.error)

    def test_partial(self):
        r = speedtest.run(cfg_for("partial.sh"), "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_FAILED")
        self.assertTrue(r.partial)
        self.assertEqual(r.download_mbps, 72.0)
        self.assertEqual(r.download_bytes, 30_000_000)
        self.assertIsNone(r.upload_mbps)
        self.assertEqual(r.latency_ms, 41.3)
        self.assertEqual(r.server_name, "US Internet")
        self.assertIn("Connection lost", r.error)

    def test_timeout_kills_process_group(self):
        t0 = time.monotonic()
        r = speedtest.run(cfg_for("hang.sh", timeout=1.5), "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_TIMEOUT")
        self.assertLess(time.monotonic() - t0, 6)
        self.assertTrue(r.partial)
        self.assertEqual(r.download_mbps, 32.0)
        # the orphan `sleep 600` child must be gone (killed with the group)
        time.sleep(0.3)
        out = os.popen("ps -eo args | grep -c '^sleep 600'").read().strip()
        self.assertEqual(out, "0")

    def test_cancel(self):
        cancel = CancelToken()
        import threading
        threading.Timer(0.5, cancel.cancel).start()
        t0 = time.monotonic()
        r = speedtest.run(cfg_for("hang.sh", timeout=30), "wlan0", cancel)
        self.assertEqual(r.status, "INTERRUPTED")
        self.assertLess(time.monotonic() - t0, 5)

    def test_zero_throughput_is_success_not_failure(self):
        r = speedtest.run(cfg_for("zero.sh"), "wlan0", CancelToken())
        self.assertEqual(r.status, "SUCCESS")
        self.assertEqual(r.download_mbps, 0.0)
        self.assertEqual(r.upload_mbps, 0.0)

    def test_missing_binary(self):
        r = speedtest.run(cfg_for("does-not-exist"), "wlan0", CancelToken())
        self.assertEqual(r.status, "SPEEDTEST_FAILED")
        self.assertIn("not found", r.error)


if __name__ == "__main__":
    unittest.main()


class LoadedLatency(unittest.TestCase):
    def test_result_keeps_latency_under_load(self):
        from pingaroo.speedtest import SpeedtestResult, apply_result_json
        raw = {"type": "result", "isp": "AT&T Wireless", "packetLoss": 4.9,
               "ping": {"jitter": 0.789, "latency": 80.019, "low": 79.54, "high": 82.698},
               "download": {"bandwidth": 2422580, "bytes": 28061968, "elapsed": 12204,
                            "latency": {"iqm": 528.245, "low": 88.966, "high": 2851.065, "jitter": 80.569}},
               "upload": {"bandwidth": 379891, "bytes": 4351856, "elapsed": 12314,
                          "latency": {"iqm": 1075.264, "low": 98.548, "high": 3426.639, "jitter": 93.674}},
               "server": {"id": 15869, "host": "h", "name": "n", "ip": "2605:8400:0:15::141"}, "interface": {}, "result": {}}
        r = SpeedtestResult()
        apply_result_json(r, raw)
        self.assertEqual((r.latency_low_ms, r.latency_high_ms), (79.54, 82.698))
        self.assertEqual((r.download_latency_ms, r.download_latency_high_ms, r.download_latency_jitter_ms), (528.245, 2851.065, 80.569))
        self.assertEqual((r.upload_latency_ms, r.upload_latency_high_ms, r.upload_latency_jitter_ms), (1075.264, 3426.639, 93.674))
        self.assertEqual((r.download_elapsed_ms, r.upload_elapsed_ms, r.isp, r.server_ip), (12204, 12314, "AT&T Wireless", "2605:8400:0:15::141"))
        # an older tool version without the latency blocks leaves them empty, not zero
        r2 = SpeedtestResult()
        apply_result_json(r2, {"download": {"bandwidth": 1}, "upload": {"bandwidth": 1}, "ping": {"latency": 5}})
        self.assertIsNone(r2.download_latency_ms)
        self.assertIsNone(r2.upload_latency_ms)

    def test_columns_reach_the_csv(self):
        from pingaroo.storage import CSV_COLUMNS
        for col in ("download_latency_ms", "upload_latency_ms", "latency_high_ms", "isp", "server_ip", "gw_clients",
                    "cell_month_total_kb", "cell_total_down_bytes", "cell_conn_time_s", "cell_5g_rssi", "gw_network_mode",
                    "cell_imei", "cell_imsi", "cell_iccid"):
            self.assertIn(col, CSV_COLUMNS)
        self.assertEqual(len(CSV_COLUMNS), len(set(CSV_COLUMNS)), "no duplicate columns")
