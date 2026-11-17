"""Ethernet mode: the wired port carries the test, the Wi-Fi radio is left alone."""

import copy
import os
import tempfile
import unittest
from unittest import mock

from pingaroo import config as config_mod, netinfo, sample_data, wifi
from pingaroo.shell import CancelToken
from pingaroo.speedtest import SpeedtestResult
from pingaroo.ui import Renderer
from pingaroo.worker import Phase
from tests.fakes import (FakeBackend, connected_state, ethernet_connected, ethernet_unplugged, good_speed,
                         test_config, wait_until)
from tests.test_worker import WorkerHarness


def eth_harness(backend, mode="auto", **overrides):
    """A Pi with both a Wi-Fi radio (wlan0) and a wired port (eth0)."""
    cfg = test_config(**{"link.mode": mode, "wifi.home_profile": "Home", "wifi.home_ssid": "Home Net", **overrides})
    return WorkerHarness(backend, cfg, eth_interface="eth0")


def eth_speed():
    sp = good_speed()
    sp.tool_interface = "eth0"
    return sp


def run_one(testcase, h):
    testcase.assertTrue(h.worker.start_session())
    testcase.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 8))
    h.worker.stop_session()
    testcase.assertTrue(wait_until(lambda: h.state.phase == Phase.IDLE and not h.state.running, 5))
    return h.rows()[0]


class Config(unittest.TestCase):
    def test_default_is_auto(self):
        cfg = copy.deepcopy(config_mod.DEFAULTS)
        self.assertEqual(cfg["link"]["mode"], "auto")
        self.assertEqual(netinfo.link_mode(cfg), "auto")
        for mode in ("auto", "wifi", "ethernet"):
            cfg["link"]["mode"] = mode
            config_mod.validate(cfg)

    def test_old_config_without_link_table_loads_as_auto(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "5GC"\n')
            self.assertEqual(config_mod.load(path)["link"]["mode"], "auto")

    def test_bad_mode_rejected(self):
        cfg = copy.deepcopy(config_mod.DEFAULTS)
        cfg["link"]["mode"] = "fibre"
        with self.assertRaisesRegex(config_mod.ConfigError, "link.mode"):
            config_mod.validate(cfg)

    def test_set_value_adds_link_table(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w") as fh:
                fh.write('[app]\nlabel = "5GC"\n')
            config_mod.set_value(path, "link", "mode", "ethernet")
            cfg = config_mod.load(path)
            self.assertEqual(cfg["link"]["mode"], "ethernet")
            self.assertEqual(cfg["app"]["label"], "5GC")

    def test_pick_test_interface_follows_mode(self):
        with mock.patch.object(netinfo, "list_ethernet_interfaces", return_value=["eth0"]), \
                mock.patch.object(netinfo, "list_wifi_interfaces", return_value=["wlan0"]):
            self.assertEqual(netinfo.pick_test_interface(test_config())[0], "wlan0")
            self.assertEqual(netinfo.pick_test_interface(test_config(**{"link.mode": "wifi"}))[0], "wlan0")
            self.assertEqual(netinfo.pick_test_interface(test_config(**{"link.mode": "ethernet"}))[0], "eth0")
            self.assertEqual(netinfo.pick_test_interface(
                test_config(**{"link.mode": "ethernet", "link.ethernet_interface": "enx1"}))[0], "enx1")


class AutoDetect(unittest.TestCase):
    """link.mode = auto: Wi-Fi unless a cable to the gateway is live."""

    def test_no_wired_port_is_plain_wifi(self):
        b = FakeBackend()
        h = WorkerHarness(b)                       # no eth_interface at all
        try:
            row = run_one(self, h)
            self.assertEqual((row["status"], row["interface"]), ("SUCCESS", "wlan0"))
            self.assertNotIn("ethernet_state", b.calls)
            self.assertIn("speedtest:wlan0", b.calls)
            self.assertEqual(h.state.snapshot()["link_mode"], "wifi")
        finally:
            h.close()

    def test_no_cable_tests_over_wifi(self):
        b = FakeBackend()
        b.eth_states = [ethernet_unplugged()]
        h = eth_harness(b)
        try:
            row = run_one(self, h)
            self.assertEqual((row["status"], row["interface"], row["band"]), ("SUCCESS", "wlan0", "5 GHz"))
            self.assertEqual(row["ssid"], "Kangaroo Labs")
            self.assertIn("speedtest:wlan0", b.calls)
            self.assertNotIn("connect_ethernet", b.calls)
            self.assertEqual(h.state.link_mode, "wifi")
        finally:
            h.close()

    def test_live_cable_tests_over_ethernet(self):
        b = FakeBackend()
        b.speed_results = [eth_speed()]
        h = eth_harness(b)
        try:
            row = run_one(self, h)
            self.assertEqual((row["status"], row["interface"], row["band"]), ("SUCCESS", "eth0", "Ethernet"))
            self.assertEqual((row["ssid"], row["bssid"], row["signal_dbm"]), ("", "", ""))
            self.assertEqual(row["link_rate_mbps"], "1000.0")
            self.assertEqual(row["local_ip"], "192.168.8.77")
            self.assertIn("route_check:eth0", b.calls)
            self.assertIn("speedtest:eth0", b.calls)
            self.assertNotIn("route_check:wlan0", b.calls)
            self.assertNotIn("connect", b.calls)
            self.assertEqual(h.state.link_mode, "ethernet")
            self.assertIn("link_change", [e["event"] for e in h.events()])
        finally:
            h.close()

    def test_cable_in_then_out_switches_per_cycle(self):
        b = FakeBackend()
        h = eth_harness(b)
        try:
            b.eth_states = [ethernet_unplugged()]
            h.worker.refresh_wifi()
            self.assertEqual((h.state.link_mode, h.state.interface), ("wifi", "wlan0"))
            b.eth_states = [ethernet_connected()]
            h.worker.refresh_wifi()
            self.assertEqual((h.state.link_mode, h.state.interface), ("ethernet", "eth0"))
            self.assertEqual(h.state.wifi.kind, "ethernet")
            b.eth_states = [ethernet_unplugged()]
            h.worker.refresh_wifi()
            self.assertEqual((h.state.link_mode, h.state.interface), ("wifi", "wlan0"))
            self.assertEqual(h.state.wifi.kind, "wifi")
            self.assertIsNone(h.state.mgmt_wifi)
        finally:
            h.close()

    def test_cable_into_another_network_is_not_used(self):
        """A cable to some other router must not produce rows under the test label."""
        b = FakeBackend()
        other = ethernet_connected(ip="192.168.4.50")
        other.gateway = "192.168.4.1"
        b.eth_states = [other]
        h = eth_harness(b, **{"router.type": "http_json", "router.base_url": "http://192.168.8.1"})
        try:
            row = run_one(self, h)
            self.assertEqual(row["interface"], "wlan0")
            self.assertNotIn("speedtest:eth0", b.calls)
            self.assertIn("another network", h.state.link_note)
        finally:
            h.close()

    def test_cable_to_the_gateway_under_test_is_used(self):
        b = FakeBackend()
        b.speed_results = [eth_speed()]
        h = eth_harness(b, **{"router.type": "http_json", "router.base_url": "http://192.168.8.1"})
        try:
            row = run_one(self, h)
            self.assertEqual(row["interface"], "eth0")
            self.assertEqual(h.state.link_note, "")
        finally:
            h.close()

    def test_cable_without_address_stays_on_wifi(self):
        b = FakeBackend()
        waiting = ethernet_unplugged()
        waiting.carrier, waiting.associated = True, True
        b.eth_states = [waiting]
        h = eth_harness(b)
        try:
            row = run_one(self, h)
            self.assertEqual(row["interface"], "wlan0")
            self.assertIn("no address yet", h.state.link_note)
        finally:
            h.close()

    def test_link_is_held_for_the_whole_cycle(self):
        """A monitor refresh during a cycle must not move the link under it."""
        b = FakeBackend()
        b.eth_states = [ethernet_unplugged()]
        b.speed_delay = 1.0
        h = eth_harness(b)
        try:
            self.assertTrue(h.worker.start_session())
            self.assertTrue(wait_until(lambda: "speedtest" in b.calls, 5))
            b.eth_states = [ethernet_connected()]          # cable goes in mid-test
            h.worker.refresh_wifi()
            self.assertEqual(h.state.link_mode, "wifi")
            self.assertTrue(wait_until(lambda: len(h.rows()) >= 1, 8))
            h.worker.stop_session()
            self.assertEqual(h.rows()[0]["interface"], "wlan0")
        finally:
            h.close()


class FixedWifi(unittest.TestCase):
    def test_wifi_mode_ignores_a_live_cable(self):
        b = FakeBackend()
        h = eth_harness(b, mode="wifi")
        try:
            row = run_one(self, h)
            self.assertEqual(row["interface"], "wlan0")
            self.assertNotIn("ethernet_state", b.calls)
        finally:
            h.close()


class FixedEthernet(unittest.TestCase):
    def test_success_row_is_tagged_as_ethernet(self):
        b = FakeBackend()
        b.speed_results = [eth_speed()]
        h = eth_harness(b, mode="ethernet")
        try:
            row = run_one(self, h)
            self.assertEqual((row["status"], row["interface"], row["band"], row["target"]),
                             ("SUCCESS", "eth0", "Ethernet", "test"))
            self.assertNotIn("connect", b.calls)
        finally:
            h.close()

    def test_no_cable_is_ethernet_failed_and_never_falls_back_to_wifi(self):
        b = FakeBackend()
        b.eth_states = [ethernet_unplugged()]
        b.eth_connect_results = [(False, ethernet_unplugged(), "no cable detected on the Ethernet port")]
        h = eth_harness(b, mode="ethernet")
        try:
            row = run_one(self, h)
            self.assertEqual((row["status"], row["failed_stage"], row["interface"]), ("ETHERNET_FAILED", "ethernet", "eth0"))
            self.assertIn("no cable", row["error"])
            self.assertNotIn("speedtest", b.calls)
            self.assertNotIn("connectivity", b.calls)
            self.assertNotIn("connect", b.calls)          # the Wi-Fi join was not tried
        finally:
            h.close()

    def test_cable_pulled_during_speed_test(self):
        b = FakeBackend()
        b.speed_results = [eth_speed()]
        # link selection is skipped in fixed mode: initial state, pre-check, then gone
        b.eth_states = [ethernet_connected(), ethernet_connected(), ethernet_unplugged()]
        h = eth_harness(b, mode="ethernet")
        try:
            row = run_one(self, h)
            self.assertEqual(row["status"], "ROUTE_FAILED")
            self.assertIn("Ethernet dropped", row["error"])
        finally:
            h.close()

    def test_home_wifi_does_not_turn_rows_into_home(self):
        """With the radio on the home network, an Ethernet cycle is still a test row."""
        b = FakeBackend()
        b.states = [connected_state(ssid="Home Net")]
        b.speed_results = [eth_speed()]
        h = eth_harness(b, mode="ethernet")
        try:
            row = run_one(self, h)
            self.assertEqual(row["target"], "test")
            self.assertNotEqual(row["label"], "Home")
            self.assertEqual(h.state.active_target, "test")
        finally:
            h.close()


class WifiRadioInEthernetMode(unittest.TestCase):
    def test_switch_moves_only_the_radio(self):
        b = FakeBackend()
        h = eth_harness(b)
        try:
            h.worker.refresh_wifi()                # notice the live cable
            self.assertEqual(h.worker.next_switch_target(), "home")
            self.assertTrue(h.worker.switch_network("home"))
            self.assertTrue(wait_until(lambda: "activate:Home" in b.calls and not h.state.switching, 5))
            self.assertIn("activate_on:wlan0", b.calls)
            self.assertNotIn("activate_on:eth0", b.calls)
            self.assertEqual(h.state.active_target, "test")
        finally:
            h.close()

    def test_next_target_follows_where_the_radio_is(self):
        b = FakeBackend()
        b.states = [connected_state(ssid="Home Net")]
        h = eth_harness(b)
        try:
            h.worker.refresh_wifi()
            self.assertEqual(h.state.mgmt_wifi.ssid, "Home Net")
            self.assertEqual(h.state.wifi.kind, "ethernet")
            self.assertEqual(h.worker.next_switch_target(), "test")
        finally:
            h.close()

    def test_no_radio_is_reported(self):
        b = FakeBackend()
        h = eth_harness(b)
        try:
            h.state.wifi_interface = ""
            h.worker.refresh_wifi()
            self.assertFalse(h.worker.switch_network("home"))
            self.assertIn("no Wi-Fi interface", h.state.switch_note)
        finally:
            h.close()


class ConnectEthernet(unittest.TestCase):
    def test_no_cable_reports_without_calling_nmcli(self):
        cfg = test_config(**{"link.mode": "ethernet"})
        with mock.patch.object(netinfo, "ethernet_state", return_value=ethernet_unplugged()), \
                mock.patch.object(wifi.shell, "run") as run:
            ok, _, err = wifi.connect_ethernet(cfg, "eth0", CancelToken())
        self.assertFalse(ok)
        self.assertIn("no cable", err)
        run.assert_not_called()

    def test_already_up(self):
        cfg = test_config(**{"link.mode": "ethernet"})
        with mock.patch.object(netinfo, "ethernet_state", return_value=ethernet_connected()), \
                mock.patch.object(wifi.shell, "run") as run:
            ok, st, err = wifi.connect_ethernet(cfg, "eth0", CancelToken())
        self.assertTrue(ok)
        self.assertEqual(st.ip, "192.168.8.77")
        run.assert_not_called()

    def test_no_interface(self):
        cfg = test_config(**{"link.mode": "ethernet"})
        ok, _, err = wifi.connect_ethernet(cfg, "", CancelToken())
        self.assertFalse(ok)
        self.assertIn("no Ethernet interface", err)


class EthernetState(unittest.TestCase):
    def _state(self, fields, sysfs):
        with mock.patch.object(netinfo, "_nmcli_terse", return_value=fields), \
                mock.patch.object(netinfo, "_read_sys", side_effect=lambda p: sysfs.get(os.path.basename(p), "")):
            return netinfo.ethernet_state("eth0")

    def test_connected(self):
        st = self._state({"GENERAL.STATE": ["100 (connected)"], "IP4.ADDRESS": ["192.168.1.50/24"],
                          "IP4.GATEWAY": ["192.168.1.1"], "IP6.ADDRESS": ["fe80::1/64", "2600:383::5/64"],
                          "DHCP4.OPTION": ["domain_name_servers = 192.168.1.1"]},
                         {"carrier": "1", "speed": "1000"})
        self.assertTrue(st.connected)
        self.assertEqual((st.kind, st.band, st.ip, st.gateway), ("ethernet", "Ethernet", "192.168.1.50", "192.168.1.1"))
        self.assertEqual(st.tx_rate_mbps, 1000.0)
        self.assertEqual(st.ipv6, ["2600:383::5/64"])
        self.assertEqual(st.dhcp_dns, ["192.168.1.1"])
        self.assertEqual(st.ssid, "")

    def test_unplugged(self):
        st = self._state({"GENERAL.STATE": ["20 (unavailable)"]}, {"carrier": "0", "speed": "-1"})
        self.assertFalse(st.connected)
        self.assertIs(st.carrier, False)
        self.assertIsNone(st.tx_rate_mbps)

    def test_cable_but_no_address(self):
        st = self._state({"GENERAL.STATE": ["70 (connecting (getting IP configuration))"]}, {"carrier": "1", "speed": "1000"})
        self.assertTrue(st.associated)
        self.assertFalse(st.connected)


class Screens(unittest.TestCase):
    def _snap(self, **wifi_over):
        snap = dict(sample_data.sample_snapshot()["success_waiting"])
        snap["link_mode"] = "ethernet"
        snap["link_setting"] = "auto"
        snap["interface"] = "eth0"
        snap["wifi"] = {**ethernet_connected().to_dict(), **wifi_over}
        snap["mgmt_wifi"] = connected_state(ssid="Home Net").to_dict()
        return snap

    def test_main_renders_and_keeps_buttons(self):
        r = Renderer(test_config())
        for snap in (self._snap(), self._snap(connected=False, associated=False, carrier=False, ip="")):
            img, hits = r.render_main(snap, {})
            self.assertEqual(img.size, (480, 320))
            self.assertEqual(sorted(h.action for h in hits), ["cycle_interval", "details", "toggle_run"])

    def test_details_describe_the_cable_and_the_radio(self):
        r = Renderer(test_config())
        r.home_ssid = "Home Net"
        rows = r.details_lines(self._snap(), {}, sample_data.sample_extra())
        d = {k: v for k, v, _ in rows}
        self.assertIn("ETHERNET LINK (UNDER TEST)", d)
        self.assertIn("WI-FI (FOR REACHING THE PI)", d)
        self.assertEqual(d["Cable"], "connected")
        self.assertEqual(d["Link under test"], "auto (using Ethernet)")
        self.assertIn("1000", d["Link speed"])
        self.assertIn("192.168.8.77", d["Local IP"])
        self.assertIn("[home network]", d["Connected SSID"])
        self.assertNotIn("BSSID", d)

    def test_wifi_mode_details_unchanged(self):
        r = Renderer(test_config())
        rows = r.details_lines(sample_data.sample_snapshot()["success_waiting"], {}, sample_data.sample_extra())
        keys = [k for k, _, _ in rows]
        self.assertIn("WI-FI LINK", keys)
        self.assertIn("BSSID", keys)
        self.assertNotIn("Cable", keys)


if __name__ == "__main__":
    unittest.main()
