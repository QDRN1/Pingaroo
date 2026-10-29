#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates a hung test: emits progress then sleeps forever (must be killed by timeout).
echo '{"type":"testStart","timestamp":"2026-10-29T20:00:00Z","server":{"id":18531,"name":"US Internet","location":"Minneapolis, MN","country":"United States"}}'
echo '{"type":"download","timestamp":"2026-10-29T20:00:03Z","download":{"bandwidth":4000000,"bytes":8000000,"elapsed":2000,"progress":0.3}}'
# spawn a child so the process-group kill is exercised too
sleep 600 &
sleep 600
