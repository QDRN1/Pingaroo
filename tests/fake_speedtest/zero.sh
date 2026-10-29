#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates a completed test with zero throughput (valid result, distinct from failure).
echo '{"type":"result","timestamp":"2026-10-29T20:00:09Z","ping":{"jitter":0.5,"latency":900.0},"download":{"bandwidth":0,"bytes":0,"elapsed":15000},"upload":{"bandwidth":0,"bytes":0,"elapsed":15000},"interface":{"name":"wlan0","externalIp":"166.137.12.44"},"server":{"id":1,"name":"Srv","location":"X","country":"Y","host":"h"},"result":{"url":"https://www.speedtest.net/result/c/zero"}}'
