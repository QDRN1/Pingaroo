#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates garbage output with a zero exit code.
echo 'this is not json'
echo '{"type":"result","download":{"bandwidth":"abc"}'
exit 0
