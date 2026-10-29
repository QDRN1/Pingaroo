"""Reachability diagnostics, all bound to the test Wi-Fi interface.

* ICMP: iputils ping with -I <interface>.
* DNS:  a minimal UDP resolver on a socket bound to the interface (SO_BINDTODEVICE),
        so system resolver settings cannot leak the query onto another path.
* HTTPS: a raw TLS connection to the resolved address with SNI, bound to the
        interface.  Redirects are not followed; a captive portal shows up as an
        unexpected status or body and is a failure.
"""

from __future__ import annotations

import random
import re
import socket
import ssl
import struct
import threading
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from . import shell
from .shell import CancelToken

SO_BINDTODEVICE = getattr(socket, "SO_BINDTODEVICE", 25)


@dataclass
class PingResult:
    target: str
    sent: int = 0
    received: int = 0
    loss_pct: float | None = None
    min_ms: float | None = None
    avg_ms: float | None = None
    max_ms: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.received > 0


@dataclass
class DnsResult:
    name: str
    server: str = ""
    addresses: list[str] = field(default_factory=list)
    ms: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.addresses)


@dataclass
class HttpsResult:
    url: str
    ok: bool = False
    status: int | None = None
    ms: float | None = None
    address: str = ""
    error: str = ""


@dataclass
class ConnectivityResult:
    internet_ok: bool = False
    icmp_ok: bool = False
    dns_ok: bool = False
    https_ok: bool = False
    gateway: PingResult | None = None
    pings: list[PingResult] = field(default_factory=list)
    dns: list[DnsResult] = field(default_factory=list)
    https: list[HttpsResult] = field(default_factory=list)
    summary: str = ""
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def bind_device(sock: socket.socket, interface: str) -> None:
    if interface:
        sock.setsockopt(socket.SOL_SOCKET, SO_BINDTODEVICE, interface.encode() + b"\0")


# --------------------------------------------------------------------------- ping

PING_SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss")
PING_RTT_RE = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")


def parse_ping(target: str, output: str) -> PingResult:
    pr = PingResult(target=target)
    m = PING_SUMMARY_RE.search(output)
    if m:
        pr.sent = int(m.group(1))
        pr.received = int(m.group(2))
        pr.loss_pct = float(m.group(3))
    m = PING_RTT_RE.search(output)
    if m:
        pr.min_ms, pr.avg_ms, pr.max_ms = float(m.group(1)), float(m.group(2)), float(m.group(3))
    if pr.sent == 0 and not m:
        pr.error = "no ping summary in output"
    return pr


def ping(target: str, interface: str, count: int, interval: float, timeout: float,
         cancel: CancelToken | None = None) -> PingResult:
    cmd = ["ping", "-n", "-q", "-c", str(count), "-W", "2", "-i", f"{max(0.2, interval):.1f}"]
    if interface:
        cmd += ["-I", interface]
    cmd.append(target)
    res = shell.run(cmd, timeout=timeout, cancel=cancel)
    if res.error:
        return PingResult(target=target, error=res.error)
    pr = parse_ping(target, res.stdout)
    if res.timed_out:
        pr.error = pr.error or "ping timed out"
    elif res.cancelled:
        pr.error = "interrupted"
    elif not res.ok and pr.received == 0:
        err = (res.stderr or "").strip().splitlines()
        pr.error = (err[-1] if err else f"ping exit {res.returncode}")[:120]
        if pr.sent == 0 and not pr.loss_pct:
            pr.loss_pct = 100.0
    return pr


# ---------------------------------------------------------------------------- DNS

def _build_query(name: str, qtype: int, qid: int) -> bytes:
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode("idna") for p in name.strip(".").split("."))
    return header + qname + b"\0" + struct.pack(">HH", qtype, 1)


def _skip_name(data: bytes, offset: int) -> int:
    while True:
        if offset >= len(data):
            raise ValueError("truncated name")
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length


def parse_dns_response(data: bytes, qid: int, qtype: int) -> list[str]:
    if len(data) < 12:
        raise ValueError("short response")
    rid, flags, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
    if rid != qid:
        raise ValueError("id mismatch")
    rcode = flags & 0x0F
    if rcode != 0:
        raise ValueError({2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}.get(rcode, f"rcode {rcode}"))
    offset = 12
    for _ in range(qd):
        offset = _skip_name(data, offset) + 4
    out: list[str] = []
    for _ in range(an):
        offset = _skip_name(data, offset)
        rtype, _, _, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        offset += rdlen
        if rtype == qtype == 1 and rdlen == 4:
            out.append(socket.inet_ntop(socket.AF_INET, rdata))
        elif rtype == qtype == 28 and rdlen == 16:
            out.append(socket.inet_ntop(socket.AF_INET6, rdata))
    return out


def dns_lookup(name: str, server: str, interface: str, timeout: float,
               ipv6: bool = False) -> DnsResult:
    dr = DnsResult(name=name, server=server)
    qtype = 28 if ipv6 else 1
    qid = random.randint(1, 65535)
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    t0 = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            bind_device(sock, interface)
            sock.settimeout(timeout)
            sock.sendto(_build_query(name, qtype, qid), (server, 53))
            data, _ = sock.recvfrom(4096)
        dr.addresses = parse_dns_response(data, qid, qtype)
        dr.ms = round((time.monotonic() - t0) * 1000, 1)
        if not dr.addresses:
            dr.error = "no address records"
    except socket.timeout:
        dr.error = "timeout"
    except OSError as exc:
        dr.error = f"{exc.strerror or exc}"[:120]
    except ValueError as exc:
        dr.error = str(exc)[:120]
    return dr


# -------------------------------------------------------------------------- HTTPS

def _read_http_response(sock: ssl.SSLSocket, limit: int = 65536) -> tuple[int, dict, bytes]:
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, body = buf.partition(b"\r\n\r\n")
    lines = head.decode("iso-8859-1", "replace").split("\r\n")
    if not lines or not lines[0].startswith("HTTP/"):
        raise ValueError("not an HTTP response")
    try:
        status = int(lines[0].split()[1])
    except (IndexError, ValueError):
        raise ValueError(f"bad status line: {lines[0][:60]}")
    headers: dict = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        headers[k.strip().lower()] = v.strip()
    length = headers.get("content-length")
    chunked = "chunked" in headers.get("transfer-encoding", "").lower()
    want = int(length) if length and length.isdigit() else (None if chunked else 0)
    if want is not None:
        want = min(want, limit)
        while len(body) < want:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    else:
        while len(body) < limit:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            body += chunk
        if chunked:
            body = _dechunk(body)
    return status, headers, body


def _dechunk(data: bytes) -> bytes:
    out = b""
    pos = 0
    while True:
        end = data.find(b"\r\n", pos)
        if end < 0:
            break
        try:
            size = int(data[pos:end].split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out += data[end + 2:end + 2 + size]
        pos = end + 2 + size + 2
    return out


def https_probe(url: str, expect_status: int, expect_body: str, interface: str,
                address: str, timeout: float) -> HttpsResult:
    hr = HttpsResult(url=url, address=address)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    ctx = ssl.create_default_context()
    t0 = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as raw:
            bind_device(raw, interface)
            raw.settimeout(timeout)
            raw.connect((address, port))
            with ctx.wrap_socket(raw, server_hostname=host) as tls:
                tls.settimeout(timeout)
                req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: pingaroo/1.0\r\n"
                       f"Accept: */*\r\nConnection: close\r\n\r\n")
                tls.sendall(req.encode())
                status, headers, body = _read_http_response(tls)
        hr.ms = round((time.monotonic() - t0) * 1000, 1)
        hr.status = status
        text = body.decode("utf-8", "replace").strip()
        if status != expect_status:
            loc = headers.get("location", "")
            hint = " (redirect, captive portal?)" if 300 <= status < 400 else ""
            hr.error = f"HTTP {status}{hint}" + (f" -> {loc[:60]}" if loc else "")
        elif expect_body and expect_body not in text:
            hr.error = f"unexpected body: {text[:40]!r} (captive portal?)"
        else:
            hr.ok = True
    except ssl.SSLCertVerificationError as exc:
        hr.error = f"TLS certificate error (interception/captive portal?): {exc.verify_message}"[:140]
    except ssl.SSLError as exc:
        hr.error = f"TLS error: {exc}"[:140]
    except socket.timeout:
        hr.error = "timeout"
    except OSError as exc:
        hr.error = f"{exc.strerror or exc}"[:120]
    except ValueError as exc:
        hr.error = str(exc)[:120]
    return hr


# --------------------------------------------------------------------- orchestration

def run_connectivity(cfg: dict, interface: str, gateway: str, dhcp_dns: list[str],
                     cancel: CancelToken) -> ConnectivityResult:
    ccfg = cfg["connectivity"]
    total = float(ccfg["total_timeout_seconds"])
    t_start = time.monotonic()
    deadline = t_start + total
    result = ConnectivityResult()

    def remaining() -> float:
        return max(0.5, deadline - time.monotonic())

    # ICMP in parallel threads (bounded by per-target timeout and total budget)
    ping_out: dict[str, PingResult] = {}
    threads: list[threading.Thread] = []

    def _ping(target: str, count: int):
        ping_out[target] = ping(target, interface, count, float(ccfg["ping_interval_seconds"]),
                                min(float(ccfg["ping_timeout_seconds"]), remaining()), cancel)

    targets = list(ccfg["ping_targets"])
    for t in targets:
        th = threading.Thread(target=_ping, args=(t, int(ccfg["ping_count"])), daemon=True)
        th.start()
        threads.append(th)
    if gateway:
        th = threading.Thread(target=_ping, args=(gateway, int(ccfg["gateway_ping_count"])), daemon=True)
        th.start()
        threads.append(th)

    # DNS + HTTPS sequentially: primary, then fallback
    servers = [s for s in dhcp_dns if s] + [s for s in ccfg["dns_servers"] if s not in dhcp_dns]
    for ep_key in ("https_primary", "https_fallback"):
        if cancel.cancelled:
            break
        ep = ccfg[ep_key]
        host = urlsplit(ep["url"]).hostname or ""
        addresses: list[str] = []
        for server in servers:
            if cancel.cancelled or remaining() <= 0.5:
                break
            dr = dns_lookup(host, server, interface, min(float(ccfg["dns_timeout_seconds"]), remaining()))
            result.dns.append(dr)
            if dr.ok:
                addresses = dr.addresses
                break
        if not addresses:
            result.https.append(HttpsResult(url=ep["url"], error="DNS failed for host"))
            continue
        hr = https_probe(ep["url"], int(ep["expect_status"]), str(ep["expect_body"]), interface,
                         addresses[0], min(float(ccfg["https_timeout_seconds"]), remaining()))
        result.https.append(hr)
        if hr.ok:
            break

    for th in threads:
        th.join(timeout=max(0.1, remaining() + 1))
    result.gateway = ping_out.get(gateway) if gateway else None
    result.pings = [ping_out.get(t, PingResult(target=t, error="not run")) for t in targets]
    result.icmp_ok = any(p.ok for p in result.pings)
    result.dns_ok = any(d.ok for d in result.dns)
    result.https_ok = any(h.ok for h in result.https)
    result.internet_ok = result.https_ok
    result.duration_s = round(time.monotonic() - t_start, 2)

    if result.internet_ok:
        notes = []
        if not result.icmp_ok:
            notes.append("ICMP blocked")
        if not result.dns_ok:
            notes.append("DNS via router failed")
        result.summary = "OK" + (f" ({', '.join(notes)})" if notes else "")
    else:
        if not result.dns_ok:
            errs = {d.error for d in result.dns if d.error}
            result.summary = "DNS failed: " + (", ".join(sorted(errs))[:80] or "no servers")
        else:
            errs = [h.error for h in result.https if h.error]
            result.summary = "HTTPS failed: " + ("; ".join(errs)[:100] or "unknown")
        if result.icmp_ok:
            result.summary += " (ICMP ok)"
    return result
