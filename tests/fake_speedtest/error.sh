#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates a server-unreachable failure: log line, no result, non-zero exit.
echo '{"type":"log","timestamp":"2026-10-29T20:00:00Z","message":"Cannot open socket: Timeout occurred in connect.","level":"error"}'
echo '[error] Cannot open socket: Timeout occurred in connect.' >&2
exit 2
