#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates the tool reporting it used a different interface than requested.
echo '{"type":"result","timestamp":"2026-10-29T20:00:09Z","ping":{"jitter":1.0,"latency":12.0},"download":{"bandwidth":30000000,"bytes":90000000,"elapsed":4000},"upload":{"bandwidth":10000000,"bytes":30000000,"elapsed":4000},"interface":{"name":"eth0","externalIp":"72.50.201.199"},"server":{"id":2,"name":"Srv","location":"X","country":"Y","host":"h"},"result":{"url":"https://www.speedtest.net/result/c/eth"}}'
