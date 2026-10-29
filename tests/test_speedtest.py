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
