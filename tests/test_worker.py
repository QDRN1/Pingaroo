import json
import os
import tempfile
import threading
import time
import unittest

from pingaroo import config as config_mod
from pingaroo.app import check_orphaned_cycle
from pingaroo.speedtest import SpeedtestResult
from pingaroo.storage import Storage
from pingaroo.worker import Phase, Worker, WorkerState
from tests.fakes import (FakeBackend, connected_state, disconnected_state, good_connectivity, good_speed,
                         test_config, wait_until)


class WorkerHarness:
    def __init__(self, backend: FakeBackend, cfg: dict | None = None):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = cfg or test_config()
        self.cfg["logging"]["directory"] = self.tmp.name
        self.storage = Storage(self.cfg)
        self.state = WorkerState()
        self.state.interface = "wlan0"
        self.worker = Worker(self.cfg, backend, self.storage, self.state)
        self.worker.start()

    def rows(self):
        return self.storage.read_last_rows(1000)

    def events(self):
        with open(self.storage.jsonl_path) as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def close(self):
        self.worker.shutdown()
        self.tmp.cleanup()


class CycleOutcomes(unittest.TestCase):
    def run_one(self, backend, cfg=None):
        h = WorkerHarness(backend, cfg)
        try:
            self.assertTrue(h.worker.start_session())
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 8))
            h.worker.stop_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.IDLE and not h.state.running, 5))
            return h.rows()[0], h
        except Exception:
            h.close()
            raise

    def test_success(self):
        row, h = self.run_one(FakeBackend())
        try:
            self.assertEqual(row["status"], "SUCCESS")
            self.assertEqual(row["download_mbps"], "87.4")
            self.assertEqual(row["local_ip"], "192.168.8.142")
            self.assertEqual(row["https_ok"], "True")
            self.assertEqual(row["failed_stage"], "")
            self.assertTrue(row["next_attempt_time"])
            self.assertEqual(h.state.session.successes, 1)
            self.assertEqual(h.state.session.bytes_down, 52_000_000)
            kinds = [e["event"] for e in h.events()]
            for k in ("session_start", "cycle_start", "wifi_result", "route_result", "connectivity_result",
                      "speedtest_result", "cycle_end", "session_stop"):
                self.assertIn(k, kinds)
        finally:
            h.close()

    def test_wifi_missing(self):
        b = FakeBackend()
        b.states = [disconnected_state()]
        b.connect_results = [(False, disconnected_state(), "SSID not found (router Wi-Fi off or out of range)")]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "WIFI_FAILED")
            self.assertEqual(row["failed_stage"], "wifi")
            self.assertIn("SSID not found", row["error"])
            self.assertNotIn("speedtest", b.calls)
            self.assertNotIn("connectivity", b.calls)
            self.assertEqual(h.state.session.failures, 1)
            self.assertEqual(h.state.session.consecutive_failures, 1)
        finally:
            h.close()

    def test_rejected_credentials(self):
        b = FakeBackend()
        b.states = [disconnected_state()]
        b.connect_results = [(False, disconnected_state(), "authentication failed (rejected credentials or WPA3 unsupported)")]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "WIFI_FAILED")
            self.assertIn("authentication failed", row["error"])
        finally:
            h.close()

    def test_connected_no_internet(self):
        b = FakeBackend()
        b.conn_results = [good_connectivity(icmp=False, https=False, dns=False)]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "CONNECTIVITY_FAILED")
            self.assertEqual(row["failed_stage"], "connectivity")
            self.assertEqual(row["local_ip"], "192.168.8.142")
            self.assertEqual(row["https_ok"], "False")
            self.assertNotIn("speedtest", b.calls)
            self.assertEqual(row["download_mbps"], "")
        finally:
            h.close()

    def test_icmp_blocked_https_ok_runs_speedtest(self):
        b = FakeBackend()
        b.conn_results = [good_connectivity(icmp=False, https=True)]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "SUCCESS")
            self.assertEqual(row["icmp_ok"], "False")
            self.assertEqual(row["ping1_loss_pct"], "100.0")
            self.assertIn("speedtest", b.calls)
        finally:
            h.close()

    def test_route_failed_when_default_path_not_wifi(self):
        b = FakeBackend()
        b.route_results = [(False, "1.1.1.1 routes via eth0 not wlan0")]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "ROUTE_FAILED")
            self.assertIn("eth0", row["error"])
            self.assertNotIn("connectivity", b.calls)
        finally:
            h.close()

    def test_speedtest_error_partial_timeout(self):
        for status, extra in (("SPEEDTEST_FAILED", {"error": "Cannot open socket"}),
                              ("SPEEDTEST_TIMEOUT", {"error": "exceeded 180 s", "partial": True, "download_mbps": 12.5,
                                                     "download_bytes": 3_000_000})):
            b = FakeBackend()
            b.speed_results = [SpeedtestResult(status=status, **extra)]
            row, h = self.run_one(b)
            try:
                self.assertEqual(row["status"], status)
                self.assertEqual(row["failed_stage"], "speedtest")
                if extra.get("partial"):
                    self.assertEqual(row["download_mbps"], "12.5")
                    self.assertEqual(row["speedtest_partial"], "True")
                    self.assertEqual(h.state.session.incomplete_byte_cycles, 1)
                    self.assertEqual(h.state.session.bytes_down, 3_000_000)
                else:
                    self.assertEqual(row["download_mbps"], "")
            finally:
                h.close()

    def test_wifi_drop_during_speedtest_is_route_failed(self):
        b = FakeBackend()
        # state calls: initial, pre-speedtest, post-speedtest
        b.states = [connected_state(), connected_state(), disconnected_state()]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "ROUTE_FAILED")
            self.assertIn("Wi-Fi dropped", row["error"])
            self.assertEqual(row["download_mbps"], "87.4")  # retained but not SUCCESS
        finally:
            h.close()

    def test_tool_used_other_interface(self):
        b = FakeBackend()
        s = good_speed()
        s.tool_interface = "eth0"
        b.speed_results = [s]
        row, h = self.run_one(b)
        try:
            self.assertEqual(row["status"], "ROUTE_FAILED")
            self.assertIn("eth0", row["error"])
        finally:
            h.close()

    def test_recovery_on_later_cycle(self):
        b = FakeBackend()
        b.states = [disconnected_state(), connected_state()]
        b.connect_results = [(False, disconnected_state(), "SSID not found")]
        h = WorkerHarness(b)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 2, 10))
            rows = h.rows()
            self.assertEqual(rows[0]["status"], "WIFI_FAILED")
            self.assertEqual(rows[1]["status"], "SUCCESS")
            self.assertEqual(h.state.session.consecutive_failures, 0)
            self.assertEqual(rows[0]["session_id"], rows[1]["session_id"])
            self.assertEqual(rows[1]["cycle_id"], rows[0]["session_id"] + "-0002")
        finally:
            h.close()


class Scheduling(unittest.TestCase):
    def test_interval_measured_from_completion_and_no_overlap(self):
        b = FakeBackend()
        b.speed_delay = 0.4
        cfg = test_config()
        cfg["app"]["interval_seconds"] = 1
        h = WorkerHarness(b, cfg)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 3, 15))
            rows = h.rows()
            from datetime import datetime
            ends = [datetime.fromisoformat(r["end_time"]) for r in rows]
            starts = [datetime.fromisoformat(r["start_time"]) for r in rows]
            for i in range(1, 3):
                gap = (starts[i] - ends[i - 1]).total_seconds()
                self.assertGreaterEqual(gap, 0.9)   # 1 s after completion (second resolution)
                self.assertLess(gap, 3.0)
            self.assertEqual(b.max_speed_active, 1)
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 5))
            self.assertIsNotNone(h.state.snapshot()["countdown_s"])
        finally:
            h.close()

    def test_interval_after_failure_too(self):
        b = FakeBackend()
        b.conn_results = [good_connectivity(https=False)]
        h = WorkerHarness(b)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 3))
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 2, 5))
            self.assertEqual(h.rows()[1]["status"], "CONNECTIVITY_FAILED")
        finally:
            h.close()


class IntervalChange(unittest.TestCase):
    def test_set_interval_live_rebases_wait(self):
        b = FakeBackend()
        cfg = test_config()
        cfg["app"]["interval_seconds"] = 60
        h = WorkerHarness(b, cfg)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 5))
            cd1 = h.state.snapshot()["countdown_s"]
            self.assertGreater(cd1, 30)
            h.worker.set_interval(30)
            cd2 = h.state.snapshot()["countdown_s"]
            self.assertLessEqual(cd2, 31)
            self.assertEqual(h.state.snapshot()["interval_s"], 30)
            kinds = [e["event"] for e in h.events()]
            self.assertIn("interval_changed", kinds)
        finally:
            h.close()

    def test_shorter_interval_triggers_next_cycle_sooner(self):
        b = FakeBackend()
        cfg = test_config()
        cfg["app"]["interval_seconds"] = 300
        h = WorkerHarness(b, cfg)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 5))
            h.worker.set_interval(30)
            # with a 30s interval and ~0s elapsed, it should still be waiting, not overlapping
            self.assertEqual(b.max_speed_active, 1)
        finally:
            h.close()


class StartStop(unittest.TestCase):
    def test_duplicate_start_rejected(self):
        h = WorkerHarness(FakeBackend())
        try:
            self.assertTrue(h.worker.start_session())
            self.assertFalse(h.worker.start_session())
            self.assertFalse(h.worker.start_session())
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            self.assertEqual(len([e for e in h.events() if e["event"] == "session_start"]), 1)
        finally:
            h.close()

    def test_stop_during_speedtest_records_interrupted(self):
        b = FakeBackend()
        b.speed_delay = 5.0
        h = WorkerHarness(b)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: h.state.phase.startswith("Speed Test"), 5))
            t0 = time.monotonic()
            self.assertTrue(h.worker.stop_session())
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            self.assertLess(time.monotonic() - t0, 2.0)
            row = h.rows()[0]
            self.assertEqual(row["status"], "INTERRUPTED")
            self.assertEqual(row["next_attempt_time"], "")
            self.assertEqual(h.state.session.interrupted, 1)
            self.assertEqual(h.state.session.failures, 0)
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.IDLE, 3))
            self.assertIsNotNone(h.state.last_speed)   # last results retained
        finally:
            h.close()

    def test_stop_during_wait_no_extra_row(self):
        h = WorkerHarness(FakeBackend())
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 5))
            h.worker.stop_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.IDLE and not h.state.running, 3))
            time.sleep(1.3)
            self.assertEqual(len(h.rows()), 1)
            self.assertEqual(h.state.session.interrupted, 0)
        finally:
            h.close()

    def test_stop_during_connect(self):
        b = FakeBackend()
        b.states = [disconnected_state()]
        b.connect_delay = 5.0
        h = WorkerHarness(b)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: "connect" in b.calls, 3))
            h.worker.stop_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 3))
            self.assertEqual(h.rows()[0]["status"], "INTERRUPTED")
        finally:
            h.close()

    def test_new_session_resets_counters_keeps_logs(self):
        h = WorkerHarness(FakeBackend())
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            h.worker.stop_session()
            self.assertTrue(wait_until(lambda: not h.state.running and h.state.phase == Phase.IDLE, 3))
            first = h.state.session.id
            h.worker.start_session()
            self.assertNotEqual(h.state.session.id, first)
            self.assertEqual(h.state.session.cycles, 0)
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 2, 5))
            self.assertEqual(len({r["session_id"] for r in h.rows()}), 2)
        finally:
            h.close()

    def test_label_captured_at_cycle_start(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "Config A"\ninterval_seconds = 1\n[speedtest]\ntimeout_seconds = 30\n')
            cfg = config_mod.load(path)
            cfg["logging"]["directory"] = d
            cfg["_path"] = path
            h = WorkerHarness(FakeBackend(), cfg)
            try:
                h.worker.start_session()
                self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
                config_mod.set_value(path, "app", "label", "Config B")
                self.assertTrue(wait_until(lambda: len(h.rows()) >= 2, 5))
                self.assertEqual([r["label"] for r in h.rows()[:2]], ["Config A", "Config B"])
                h.worker.set_label_override("APN test")
                self.assertTrue(wait_until(lambda: len(h.rows()) >= 3, 5))
                self.assertEqual(h.rows()[2]["label"], "APN test")
            finally:
                h.close()


class StorageFailures(unittest.TestCase):
    def test_log_write_failure_is_visible(self):
        b = FakeBackend()
        h = WorkerHarness(b)
        try:
            # make the directory unwritable by replacing it with a file
            h.storage.directory = os.path.join(h.tmp.name, "blocked")
            with open(h.storage.directory, "w") as fh:
                fh.write("x")
            h.storage.csv_path = os.path.join(h.storage.directory, "results.csv")
            h.storage.jsonl_path = os.path.join(h.storage.directory, "events.jsonl")
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: "LOG WRITE FAILED" in h.state.last_error, 5))
            self.assertTrue(h.storage.status.last_error)
            self.assertEqual(h.state.session.successes, 1)  # the test itself still completed
        finally:
            h.close()

    def test_orphan_marker_recorded_on_start(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = test_config()
            cfg["logging"]["directory"] = d
            storage = Storage(cfg)
            storage.write_marker({"cycle_id": "S-0003", "session_id": "S", "label": "Config A",
                                  "start_time": "2026-10-29T10:00:00-05:00"})
            check_orphaned_cycle(storage)
            rows = storage.read_last_rows(5)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "INTERRUPTED")
            self.assertEqual(rows[0]["failed_stage"], "crash_recovery")
            self.assertIsNone(storage.read_marker())


if __name__ == "__main__":
    unittest.main()


class NetworkSwitch(unittest.TestCase):
    def test_switch_toggles_target_without_stopping(self):
        b = FakeBackend()
        cfg = test_config()
        cfg["wifi"]["home_profile"] = "preconfigured"
        cfg["wifi"]["home_ssid"] = "LAN Down Under"
        h = WorkerHarness(b, cfg)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: h.state.phase == Phase.WAITING, 5))
            self.assertEqual(h.state.active_target, "test")
            self.assertTrue(h.worker.switch_network("home"))
            self.assertTrue(wait_until(lambda: "activate:preconfigured" in b.calls, 5))
            self.assertTrue(wait_until(lambda: not h.state.switching, 5))
            self.assertTrue(h.state.running, "toggling network must not stop the loop")
            self.assertEqual(h.state.active_target, "home")
            self.assertTrue(h.worker.switch_network("test"))
            self.assertTrue(wait_until(lambda: "activate:Kangaroo Labs" in b.calls, 5))
            self.assertTrue(wait_until(lambda: h.state.active_target == "test" and not h.state.switching, 5))
        finally:
            h.close()

    def test_loop_tests_active_target_network(self):
        b = FakeBackend()
        b.states = [disconnected_state()]
        b.connect_results = [(True, connected_state(ssid="LAN Down Under", ip="192.168.7.11"), "")]
        cfg = test_config()
        cfg["wifi"]["home_profile"] = "preconfigured"
        cfg["wifi"]["home_ssid"] = "LAN Down Under"
        h = WorkerHarness(b, cfg)
        try:
            h.state.active_target = "home"
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            self.assertEqual(h.rows()[0]["ssid"], "LAN Down Under")
        finally:
            h.close()

    def test_switch_without_home_profile_reports(self):
        h = WorkerHarness(FakeBackend())
        try:
            self.assertFalse(h.worker.switch_network("home"))
            self.assertIn("no home profile", h.state.switch_note)
        finally:
            h.close()

    def test_router_info_recorded(self):
        b = FakeBackend()
        cfg = test_config()
        cfg["router"]["type"] = "http_json"
        h = WorkerHarness(b, cfg)
        try:
            h.worker.start_session()
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 5))
            row = h.rows()[0]
            self.assertEqual(row["cell_rsrp"], "-95")
            self.assertEqual(row["cell_apn"], "broadband")
            self.assertIn("router_info", b.calls)
        finally:
            h.close()
