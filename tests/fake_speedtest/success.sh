#!/bin/sh
if [ "${1:-}" = "--version" ]; then echo "Speedtest by Ookla 1.2.0.84 (fake) Linux/aarch64"; exit 0; fi
# Simulates the Ookla CLI in --format=jsonl --progress=yes mode with a successful run.
echo '{"type":"testStart","timestamp":"2026-10-29T20:00:00Z","isp":"AT&T Wireless","interface":{"internalIp":"192.168.8.142","name":"wlan0","macAddr":"DC:A6:32:11:22:33","isVpn":false,"externalIp":"166.137.12.44"},"server":{"id":18531,"host":"speedtest.usinternet.com","port":8080,"name":"US Internet","location":"Minneapolis, MN","country":"United States","ip":"207.225.192.5"}}'
sleep 0.2
echo '{"type":"ping","timestamp":"2026-10-29T20:00:01Z","ping":{"jitter":3.2,"latency":41.3,"progress":1}}'
sleep 0.2
echo '{"type":"download","timestamp":"2026-10-29T20:00:03Z","download":{"bandwidth":5000000,"bytes":10000000,"elapsed":2000,"progress":0.5}}'
sleep 0.2
echo '{"type":"upload","timestamp":"2026-10-29T20:00:06Z","upload":{"bandwidth":1500000,"bytes":3000000,"elapsed":2000,"progress":0.5}}'
sleep 0.2
echo '{"type":"result","timestamp":"2026-10-29T20:00:09Z","ping":{"jitter":3.2,"latency":41.3,"low":39.1,"high":48.2},"download":{"bandwidth":10925000,"bytes":52000000,"elapsed":4800,"latency":{"iqm":55.1,"low":42.0,"high":90.3,"jitter":4.4}},"upload":{"bandwidth":2737500,"bytes":12300000,"elapsed":4500,"latency":{"iqm":60.2,"low":44.1,"high":101.9,"jitter":6.0}},"packetLoss":0,"isp":"AT&T Wireless","interface":{"internalIp":"192.168.8.142","name":"wlan0","macAddr":"DC:A6:32:11:22:33","isVpn":false,"externalIp":"166.137.12.44"},"server":{"id":18531,"host":"speedtest.usinternet.com","port":8080,"name":"US Internet","location":"Minneapolis, MN","country":"United States","ip":"207.225.192.5"},"result":{"id":"4d2a0c8b-0000-4000-8000-000000000001","url":"https://www.speedtest.net/result/c/4d2a0c8b-0000-4000-8000-000000000001","persisted":true}}'
