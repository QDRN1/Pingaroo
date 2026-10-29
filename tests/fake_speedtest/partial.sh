#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates a run that produced download progress and then died (no result object).
echo '{"type":"testStart","timestamp":"2026-10-29T20:00:00Z","server":{"id":18531,"name":"US Internet","location":"Minneapolis, MN","country":"United States","host":"speedtest.usinternet.com"}}'
echo '{"type":"ping","timestamp":"2026-10-29T20:00:01Z","ping":{"jitter":3.2,"latency":41.3,"progress":1}}'
echo '{"type":"download","timestamp":"2026-10-29T20:00:03Z","download":{"bandwidth":9000000,"bytes":30000000,"elapsed":3000,"progress":0.8}}'
echo '{"type":"log","timestamp":"2026-10-29T20:00:04Z","message":"Connection lost during upload","level":"error"}'
exit 1
