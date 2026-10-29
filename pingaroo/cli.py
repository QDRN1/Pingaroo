"""Command line entry point: `pingaroo <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, clock, config as config_mod
from .storage import StateFile, Storage


def _cfg(args) -> dict:
    try:
        return config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _need_root(action: str) -> None:
    if os.geteuid() != 0:
        print(f"{action} needs root (sudo).", file=sys.stderr)
        raise SystemExit(1)


# ------------------------------------------------------------------ commands

def cmd_run(args) -> int:
    from .app import run_app
    cfg = _cfg(args)
    return run_app(cfg, headless=args.headless, png_path=args.png)


def cmd_once(args) -> int:
    """Run exactly one diagnostic cycle in the foreground, no UI, and print the row."""
    from .app import SingleInstance
    from .backend import RealBackend
    from .netinfo import pick_interface
    from .worker import Worker, WorkerState
    cfg = _cfg(args)
    lock = SingleInstance(os.path.join(cfg["logging"]["runtime_directory"], "lock"))
    if not lock.acquire():
        print("The pingaroo service is running. Stop it first (sudo systemctl stop pingaroo) so two tests "
              "do not overlap, or use `pingaroo start` to let the service run a cycle.", file=sys.stderr)
        return 3
    try:
        storage = Storage(cfg)
        storage.ensure_directory()
        state = WorkerState()
        state.interface, state.interface_note = pick_interface(cfg["wifi"]["interface"])
        if not state.interface:
            print("no Wi-Fi interface found", file=sys.stderr)
            return 1
        backend = RealBackend()
        state.speedtest_version = backend.speedtest_version(cfg)
        worker = Worker(cfg, backend, storage, state)
        storage.write_event("once_start", interface=state.interface)
        with state.lock:
            from .worker import Session
            import uuid
            sid = time.strftime("%Y%m%dT%H%M%S") + "-once" + uuid.uuid4().hex[:2]
            state.session = Session(id=sid, start_iso=clock.iso(), start_mono=time.monotonic())
            state.running = True
        print(f"interface {state.interface} ({state.interface_note}); running one cycle...", file=sys.stderr)
        worker._run_cycle()
        with state.lock:
            state.running = False
        row = state.last_row or {}
        if args.json:
            print(json.dumps(row, indent=2, default=str))
        else:
            for key in ("cycle_id", "label", "status", "failed_stage", "error", "duration_s", "ssid", "local_ip",
                        "signal_dbm", "icmp_ok", "dns_ok", "https_ok", "download_mbps", "upload_mbps", "latency_ms",
                        "server_name", "public_ip", "clock_synced"):
                print(f"{key:>16}: {row.get(key, '')}")
            if storage.status.last_error:
                print(f"\nLOG WRITE FAILED: {storage.status.last_error}", file=sys.stderr)
                return 4
        return 0 if row.get("status") == "SUCCESS" else 1
    finally:
        lock.release()


def cmd_status(args) -> int:
    cfg = _cfg(args)
    sf = StateFile(cfg["logging"]["runtime_directory"])
    snap = sf.read()
    if not snap:
        print("no state file: service not running (or never started)")
        return 1
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
        return 0
    age = None
    try:
        from datetime import datetime
        age = (clock.now() - datetime.fromisoformat(snap.get("time"))).total_seconds()
    except Exception:
        pass
    stale = age is not None and age > 10
    print(f"time:        {snap.get('time')}" + ("  (STALE: service may be stopped)" if stale else ""))
    print(f"running:     {snap.get('running')}   phase: {snap.get('phase')}   worker alive: {snap.get('worker_alive')}")
    print(f"label:       {snap.get('label')}")
    w = snap.get("wifi") or {}
    print(f"wifi:        {'connected' if w.get('connected') else 'not connected'}  ssid={w.get('ssid') or '-'} "
          f"ip={w.get('ip') or 'none'} signal={w.get('signal_dbm')} dBm  iface={snap.get('interface')}")
    i = snap.get("internet") or {}
    print(f"internet:    {i.get('ok')}  {i.get('summary')}  (checked {i.get('time_iso') or 'never'})")
    s = snap.get("session")
    if s:
        print(f"session:     {s['id']} cycles={s['cycles']} ok={s['successes']} failed={s['failures']} "
              f"interrupted={s['interrupted']} consecutive_failures={s['consecutive_failures']}")
    sp = snap.get("last_speed") or {}
    if sp:
        print(f"last speed:  down={sp.get('download_mbps')} up={sp.get('upload_mbps')} Mbps latency={sp.get('latency_ms')} ms "
              f"server={sp.get('server_name')} status={sp.get('status')}")
    if snap.get("countdown_s") is not None:
        print(f"next test:   in {clock.mmss(snap['countdown_s'])}")
    if snap.get("last_error"):
        print(f"last error:  {snap['last_error']}")
    st = snap.get("storage") or {}
    if st.get("last_error"):
        print(f"STORAGE:     {st['last_error']}")
    print(f"display:     {snap.get('display')}   asleep={snap.get('asleep')}")
    print(f"touch:       {snap.get('touch')}")
    print(f"clock sync:  {snap.get('clock_synced')}")
    return 0


def cmd_control(args) -> int:
    cfg = _cfg(args)
    sf = StateFile(cfg["logging"]["runtime_directory"])
    if not sf.read():
        print("service not running", file=sys.stderr)
        return 1
    sf.push_control(args.command)
    print(f"sent '{args.command}' to the running service")
    return 0


def cmd_results(args) -> int:
    cfg = _cfg(args)
    storage = Storage(cfg)
    rows = storage.read_last_rows(args.last)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print(f"no results in {storage.csv_path}")
        return 0
    cols = [("end_time", "end_time", 25), ("label", "label", 12), ("status", "status", 20),
            ("download_mbps", "down", 7), ("upload_mbps", "up", 7), ("latency_ms", "ping_ms", 7),
            ("signal_dbm", "dBm", 5), ("error", "error", 50)]
    print("  ".join(h.ljust(w) for _, h, w in cols))
    for r in rows:
        print("  ".join(str(r.get(c, ""))[:w].ljust(w) for c, _, w in cols))
    print(f"\n{storage.csv_path}")
    return 0


def cmd_events(args) -> int:
    cfg = _cfg(args)
    storage = Storage(cfg)
    if not os.path.exists(storage.jsonl_path):
        print(f"no events in {storage.jsonl_path}")
        return 0
    with open(storage.jsonl_path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines[-args.last:]:
        if args.raw:
            print(line.rstrip())
            continue
        try:
            d = json.loads(line)
        except ValueError:
            print(line.rstrip())
            continue
        d.pop("detail", None)
        print(f"{d.pop('time', '')}  {d.pop('event', ''):<20} " +
              " ".join(f"{k}={v}" for k, v in d.items() if v not in ("", None)))
    return 0


def _set(args, section: str, key: str, value) -> int:
    cfg = _cfg(args)
    path = cfg["_path"]
    config_mod.set_value(path, section, key, value)
    config_mod.load(path)  # re-validate
    print(f"{section}.{key} = {value!r}  ({path})")
    return 0


def cmd_label(args) -> int:
    cfg = _cfg(args)
    if args.name is None:
        print(f"active label: {config_mod.read_label(cfg)}")
        print("predefined:   " + ", ".join(cfg["app"]["labels"]))
        return 0
    rc = _set(args, "app", "label", args.name)
    print("Applies from the next cycle start; no restart needed.")
    return rc


def cmd_labels(args) -> int:
    cfg = _cfg(args)
    if not args.names:
        print("predefined labels: " + ", ".join(cfg["app"]["labels"]))
        return 0
    names = [n.strip() for n in args.names if n.strip()]
    config_mod.set_value(cfg["_path"], "app", "labels", names)
    if cfg["app"]["label"] not in names:
        config_mod.set_value(cfg["_path"], "app", "label", names[0])
        print(f"active label set to {names[0]!r}")
    config_mod.load(cfg["_path"])
    print("predefined labels: " + ", ".join(names))
    print("Restart the service so the Next Label button uses the new list: sudo systemctl restart pingaroo")
    return 0


def cmd_interval(args) -> int:
    if args.seconds is None:
        print(f"interval_seconds = {_cfg(args)['app']['interval_seconds']}")
        return 0
    rc = _set(args, "app", "interval_seconds", int(args.seconds))
    print("Restart the service to apply: sudo systemctl restart pingaroo")
    return rc


def cmd_server(args) -> int:
    if args.server_id is None:
        sid = _cfg(args)["speedtest"]["server_id"]
        print(f"speedtest.server_id = {sid} ({'automatic' if sid == 0 else 'pinned'})")
        return 0
    value = 0 if str(args.server_id).lower() in ("auto", "0") else int(args.server_id)
    rc = _set(args, "speedtest", "server_id", value)
    print("Applies to the next speed test (the config is re-read at service start; restart to be sure).")
    return rc


def cmd_sleep(args) -> int:
    if args.seconds is None:
        v = _cfg(args)["display"]["sleep_seconds"]
        print(f"display.sleep_seconds = {v} ({'disabled' if v == 0 else 'seconds'})")
        return 0
    value = 0 if str(args.seconds).lower() in ("off", "0", "disable", "disabled") else int(args.seconds)
    rc = _set(args, "display", "sleep_seconds", value)
    print("Restart the service to apply: sudo systemctl restart pingaroo")
    return rc


def cmd_autostart(args) -> int:
    if args.value is None:
        print(f"app.autostart = {_cfg(args)['app']['autostart']}")
        return 0
    return _set(args, "app", "autostart", args.value.lower() in ("on", "true", "yes", "1"))


def cmd_wifi(args) -> int:
    """Show or set which SSID / NetworkManager profile the test uses."""
    from . import shell
    cfg = _cfg(args)
    if args.switch:
        target = args.switch
        sf = StateFile(cfg["logging"]["runtime_directory"])
        if sf.read() and sf.read().get("worker_alive"):
            sf.push_control(f"switch:{target}")
            print(f"asked the running service to switch to the {target} Wi-Fi (watch: pingaroo status)")
            return 0
        _need_root("switching Wi-Fi")
        from .netinfo import pick_interface
        profile = cfg["wifi"]["home_profile"] if target == "home" else cfg["wifi"]["connection_name"]
        if not profile:
            print("no home profile configured: run `sudo pingaroo wifi --home-current` while on the home Wi-Fi", file=sys.stderr)
            return 1
        iface, _ = pick_interface(cfg["wifi"]["interface"])
        res = shell.run(["nmcli", "-w", "45", "connection", "up", profile, "ifname", iface], timeout=50)
        print(res.stdout.strip() or res.stderr.strip())
        return 0 if res.ok else 1
    if args.current or args.home_current:
        res = shell.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"], timeout=8)
        active = [l.split(":") for l in res.stdout.splitlines() if l.count(":") >= 2]
        wifi = [a for a in active if a[1] == "802-11-wireless"]
        if not wifi:
            print("no active Wi-Fi connection found (nmcli connection show --active)", file=sys.stderr)
            return 1
        profile, _, device = wifi[0][0].replace("\\:", ":"), wifi[0][1], wifi[0][2]
        res = shell.run(["nmcli", "-t", "-g", "802-11-wireless.ssid", "connection", "show", profile], timeout=8)
        ssid = res.stdout.strip().replace("\\:", ":") or profile
        print(f"active Wi-Fi: profile '{profile}' ssid '{ssid}' on {device}")
        if args.home_current:
            config_mod.set_value(cfg["_path"], "wifi", "home_profile", profile)
            config_mod.set_value(cfg["_path"], "wifi", "home_ssid", ssid)
            print(f"wifi.home_profile = {profile!r}, wifi.home_ssid = {ssid!r}  (management network)")
            print("Restart the service to apply: sudo systemctl restart pingaroo")
            return 0
        args.ssid, args.profile = ssid, profile
    if args.ssid is None and args.profile is None:
        print(f"wifi.ssid            = {cfg['wifi']['ssid']}")
        print(f"wifi.connection_name = {cfg['wifi']['connection_name']}")
        print(f"wifi.interface       = {cfg['wifi']['interface'] or '(auto)'}")
        print(f"wifi.home_profile    = {cfg['wifi']['home_profile'] or '(not set)'}  ssid {cfg['wifi']['home_ssid'] or '-'}")
        print("Use: --current | --home-current | --ssid NAME [--profile NAME] | --switch home|test")
        return 0
    path = cfg["_path"]
    if args.ssid is not None:
        config_mod.set_value(path, "wifi", "ssid", args.ssid)
    if args.profile is not None:
        config_mod.set_value(path, "wifi", "connection_name", args.profile)
    elif args.ssid is not None:
        config_mod.set_value(path, "wifi", "connection_name", args.ssid)
    cfg = config_mod.load(path)
    print(f"wifi.ssid = {cfg['wifi']['ssid']!r}, wifi.connection_name = {cfg['wifi']['connection_name']!r}  ({path})")
    print("Restart the service to apply: sudo systemctl restart pingaroo")
    return 0


def cmd_export(args) -> int:
    from . import export
    cfg = _cfg(args)
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl missing: sudo apt install -y python3-openpyxl", file=sys.stderr)
        return 1
    rows = export.load_rows(cfg["logging"]["directory"])
    if not rows:
        print("no results to export")
        return 1
    out = args.out or os.path.join(cfg["logging"]["directory"], "export",
                                   time.strftime("pingaroo-results-%Y%m%d-%H%M.xlsx"))
    export.write_xlsx(rows, out)
    print(f"wrote {len(rows)} rows to {out}")
    try:
        import socket
        host = socket.gethostname()
        print(f"copy to your PC:   scp {os.environ.get('SUDO_USER') or os.environ.get('USER', 'pingaroo')}@{host}.local:{out} .")
    except Exception:
        pass
    return 0


def cmd_router_password(args) -> int:
    import getpass
    _need_root("storing the router password")
    cfg = _cfg(args)
    path = cfg["router"]["password_file"]
    pw = getpass.getpass("Router admin password (not echoed): ")
    if not pw:
        print("empty password, nothing written")
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(pw + "\n")
    os.chmod(path, 0o600)
    print(f"stored in {path} (root only)")
    return 0


def cmd_router_probe(args) -> int:
    """Log in with the configured method and dump the JSON of the given paths (or the
    configured status_paths).  Used to discover the router's API field names."""
    from . import router
    cfg = _cfg(args)
    cfg["router"]["type"] = "http_json"
    if args.paths:
        cfg["router"]["status_paths"] = args.paths
    if args.no_login:
        cfg["router"]["login_path"] = ""
    r = router.HttpJsonRouter(cfg)
    err = r.login()
    if err:
        print(f"login: {err}", file=sys.stderr)
    import urllib.error
    rc = 0
    for path in cfg["router"]["status_paths"]:
        try:
            status, raw = r._request(path)
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            print(f"=== GET {path} -> cannot reach {cfg['router']['base_url']}: {reason}")
            print("    The Pi has no route to the gateway admin page. On this gateway the admin")
            print("    interface answers only on 2.4 GHz. Pin the Pi to 2.4 GHz, then retry:")
            print('      sudo nmcli connection modify "Kangaroo Labs" 802-11-wireless.band bg')
            print('      sudo nmcli connection up "Kangaroo Labs"')
            rc = 1
            break
        text = raw.decode("utf-8", "replace")
        print(f"=== GET {path} -> HTTP {status} ({len(raw)} bytes)")
        try:
            print(json.dumps(json.loads(text), indent=2)[:args.max_chars])
        except ValueError:
            print(text[:args.max_chars])
    return rc


def cmd_splash(args) -> int:
    """Replay the boot splash on the LCD (or write frames to PNG with --out)."""
    from .splash import render_splash, show_splash
    from .ui import Fonts
    cfg = _cfg(args)
    d = cfg["display"]
    fonts = Fonts(d["font"], d["font_bold"])
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for i in range(6):
            render_splash(d["width"], d["height"], i * 0.25, fonts).save(os.path.join(args.out, f"splash_{i}.png"))
        print(f"wrote frames to {args.out}")
        return 0
    from .display import Framebuffer, pick_framebuffer
    _need_root("drawing on the framebuffer")
    info, note = pick_framebuffer(d["framebuffer"], d["width"], d["height"])
    if not info:
        print(f"no framebuffer: {note}")
        return 1
    fb = Framebuffer(info)
    show_splash(fb, info.width, info.height, fonts, args.seconds)
    fb.close()
    print("done (the service redraws its screen within a second)")
    return 0


def cmd_band(args) -> int:
    """Lock the test Wi-Fi profile to 2.4 GHz, 5 GHz, or auto, then reconnect.
    On the AT&T NCM1120 the router admin page is reachable only on 2.4 GHz, while
    5 GHz gives higher throughput; this picks the trade-off."""
    from . import shell, netinfo
    cfg = _cfg(args)
    name = cfg["wifi"]["connection_name"]
    if args.choice is None:
        res = shell.run(["nmcli", "-t", "-g", "802-11-wireless.band", "connection", "show", name], timeout=8)
        cur = res.stdout.strip() or "(auto)"
        iface, _ = netinfo.pick_interface(cfg["wifi"]["interface"])
        st = netinfo.wifi_state(iface, cfg["wifi"]["ssid"])
        print(f"profile band setting: {cur}")
        print(f"currently associated: band={st.band or '-'} channel={st.channel} signal={st.signal_dbm} dBm")
        print("set with: pingaroo band 2.4 | 5 | auto")
        return 0
    _need_root("changing the Wi-Fi band")
    band = {"2.4": "bg", "2": "bg", "5": "a", "auto": "", "": ""}[args.choice]
    shell.run(["nmcli", "connection", "modify", name, "802-11-wireless.band", band], timeout=8)
    res = shell.run(["nmcli", "-w", "45", "connection", "up", name], timeout=50)
    label = {"bg": "2.4 GHz", "a": "5 GHz", "": "auto"}[band]
    if res.ok:
        print(f"{name} locked to {label} and reconnected")
        if band == "bg":
            print("Note: 2.4 GHz lowers the speed-test ceiling; use it for router stats, 5 GHz for throughput.")
    else:
        print(f"band set to {label}, but reconnect failed: {(res.stderr or res.stdout).strip()[:120]}")
    return 0 if res.ok else 1


def cmd_config(args) -> int:
    cfg = _cfg(args)
    path = cfg.pop("_path")
    print(f"# effective configuration from {path}")
    print(json.dumps(cfg, indent=2))
    return 0


def cmd_check_wifi(args) -> int:
    from . import netinfo, shell
    cfg = _cfg(args)
    iface, note = netinfo.pick_interface(cfg["wifi"]["interface"])
    print(f"Wi-Fi interface:   {iface or 'NONE'} ({note})")
    if not iface:
        return 1
    profiles = netinfo.connection_profiles()
    name = cfg["wifi"]["connection_name"]
    print(f"NM profile:        {name} -> {'present' if name in profiles else 'MISSING (run scripts/setup-wifi.sh)'}")
    res = shell.run(["nmcli", "-t", "-f", "802-11-wireless-security.key-mgmt,802-11-wireless-security.pmf,"
                     "connection.autoconnect,ipv4.route-metric,ipv4.dns-priority", "connection", "show", name], timeout=8)
    if res.ok:
        for line in res.stdout.strip().splitlines():
            print(f"                   {line}")
    res = shell.run(["iw", "list"], timeout=8)
    sae = "SAE" in res.stdout or "sae" in res.stdout
    ext = "external_auth" in res.stdout or "EXTERNAL_AUTH" in res.stdout
    mfp = "MFP" in res.stdout or "PMF" in res.stdout
    print(f"Driver advertises: SAE={'yes' if sae else 'not listed'}  external_auth={'yes' if ext else 'not listed'}  "
          f"MFP={'yes' if mfp else 'not listed'}")
    res = shell.run(["wpa_supplicant", "-v"], timeout=5)
    print(f"wpa_supplicant:    {(res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr).strip() else 'unknown'}")
    res = shell.run(["nmcli", "-t", "-f", "SSID,SECURITY,SIGNAL,FREQ", "device", "wifi", "list", "ifname", iface], timeout=20)
    target = cfg["wifi"]["ssid"]
    seen = [l for l in res.stdout.splitlines() if l.startswith(target + ":")]
    if seen:
        print(f"Scan:              {target} visible: {seen[0]}")
        if "WPA3" not in seen[0]:
            print("                   WARNING: router is not advertising WPA3 for this SSID")
    else:
        print(f"Scan:              {target} NOT visible")
    st = netinfo.wifi_state(iface, target)
    print(f"Current state:     connected={st.connected} ssid={st.ssid or '-'} ip={st.ip or 'none'} "
          f"signal={st.signal_dbm} dBm band={st.band} nm={st.nm_state}")
    for t in cfg["connectivity"]["ping_targets"]:
        r = netinfo.route_for(t)
        print(f"Route to {t:<10} dev={r.get('dev', '?')} via={r.get('gateway', '-')} "
              f"{'OK' if r.get('dev') == iface else 'NOT the test Wi-Fi'}" if "error" not in r else f"Route to {t}: {r['error']}")
    return 0


def cmd_check_display(args) -> int:
    from .display import Framebuffer, list_framebuffers, pick_framebuffer
    cfg = _cfg(args)
    fbs = list_framebuffers()
    if not fbs:
        print("no framebuffer devices found. Is the display overlay enabled? (dmesg | grep -i fb)")
        return 1
    for fb in fbs:
        print(f"{fb.path}: {fb.width}x{fb.height} {fb.bpp}bpp stride={fb.stride} name={fb.name}")
    info, note = pick_framebuffer(cfg["display"]["framebuffer"], cfg["display"]["width"], cfg["display"]["height"])
    print(f"selected: {info.path if info else 'NONE'} ({note})")
    if info and args.test:
        _need_root("drawing a test pattern")
        from PIL import Image, ImageDraw
        fb = Framebuffer(info)
        img = Image.new("RGB", (info.width, info.height), (0, 0, 0))
        d = ImageDraw.Draw(img)
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)]
        bw = info.width // 4
        for i, c in enumerate(colors):
            d.rectangle((i * bw, 0, (i + 1) * bw, info.height // 2), fill=c)
        d.rectangle((0, info.height // 2, info.width, info.height), outline=(255, 255, 0), width=4)
        d.text((10, info.height // 2 + 10), "TOP-LEFT is here. Red Green Blue White on top.", fill=(255, 255, 255))
        d.text((10, info.height // 2 + 30), f"{info.width}x{info.height} {info.bpp}bpp", fill=(255, 255, 255))
        fb.blit(img)
        fb.close()
        print("test pattern drawn: red/green/blue/white bars on top, yellow box below, text top-left of box")
    return 0


def cmd_check_touch(args) -> int:
    import queue
    from .touch import Calibration, TouchReader, find_touch_device
    cfg = _cfg(args)
    path, note = find_touch_device(cfg["touch"]["device"], cfg["touch"]["device_name_match"])
    print(f"touch device: {path or 'NONE'} ({note})")
    if not path:
        return 1
    q: "queue.Queue" = queue.Queue()
    reader = TouchReader(path, Calibration.from_config(cfg), q)
    reader.start()
    print(f"Touch the screen for {args.seconds} s; press/release coordinates print below (Ctrl+C to stop).")
    end = time.monotonic() + args.seconds
    try:
        while time.monotonic() < end:
            try:
                ev = q.get(timeout=0.5)
                if ev.kind == "move" and not args.moves:
                    continue
                print(f"{ev.kind:<5} x={ev.x:>3} y={ev.y:>3}")
            except queue.Empty:
                if reader.error:
                    print(reader.error)
                    return 1
    except KeyboardInterrupt:
        pass
    reader.stop()
    if reader.raw_range:
        print(f"raw ranges reported by driver: x {reader.raw_range[0]}..{reader.raw_range[1]}, y {reader.raw_range[2]}..{reader.raw_range[3]}")
    return 0


def _collect_taps(fb, reader_q, W, H, points, prompt_fn):
    """Show a target for each point and wait for one tap.  Returns mapped (x, y) per point."""
    import queue
    from PIL import Image, ImageDraw
    samples = []
    for idx, (px, py) in enumerate(points):
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        prompt_fn(d, idx, px, py)
        fb.blit(img)
        while True:
            try:
                reader_q.get_nowait()
            except queue.Empty:
                break
        last = None
        while True:
            ev = reader_q.get()
            if ev.kind in ("down", "move"):
                last = (ev.x, ev.y)
            elif ev.kind == "up" and last:
                samples.append(last)
                break
        time.sleep(0.4)
    return samples


def cmd_calibrate_touch(args) -> int:
    """Touch calibration on the framebuffer.  Default: 4 crosses with raw values.
    --corners: refine the existing calibration from taps at the four physical corners."""
    import queue
    from PIL import Image
    from .display import Framebuffer, pick_framebuffer
    from .touch import Calibration, TouchReader, find_touch_device, refine_axis
    _need_root("calibration")
    cfg = _cfg(args)
    info, note = pick_framebuffer(cfg["display"]["framebuffer"], cfg["display"]["width"], cfg["display"]["height"])
    if not info:
        print(f"no framebuffer: {note}")
        return 1
    path, tnote = find_touch_device(cfg["touch"]["device"], cfg["touch"]["device_name_match"])
    if not path:
        print(f"no touch device: {tnote}")
        return 1
    fb = Framebuffer(info)
    W, H = info.width, info.height
    cal_path = cfg["_path"]
    q: "queue.Queue" = queue.Queue()

    def write(values: dict) -> None:
        for key, val in values.items():
            config_mod.set_value(cal_path, "touch", key, val)
        print("calibration written to " + cal_path + ": " + " ".join(f"{k}={v}" for k, v in values.items()))

    try:
        if args.corners:
            cal = Calibration.from_config(cfg)
            reader = TouchReader(path, cal, q)
            reader.start()
            corners = [(0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1)]
            names = ["TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"]

            def prompt(d, idx, px, py):
                x0, y0 = (0 if px == 0 else W - 40), (0 if py == 0 else H - 40)
                d.rectangle((x0, y0, x0 + 39, y0 + 39), outline=(255, 200, 0), width=3)
                d.text((W // 2 - 110, H // 2 - 20), f"Tap the very {names[idx]} corner", fill=(255, 255, 255))
                d.text((W // 2 - 110, H // 2), f"({idx + 1}/4) use a fingernail or stylus", fill=(180, 180, 180))

            try:
                tl, tr, bl, br = _collect_taps(fb, q, W, H, corners, prompt)
            finally:
                reader.stop()
            left = (tl[0] + bl[0]) / 2
            right = (tr[0] + br[0]) / 2
            top = (tl[1] + tr[1]) / 2
            bottom = (bl[1] + br[1]) / 2
            print(f"corner taps landed at: left {left:.0f}, right {right:.0f}, top {top:.0f}, bottom {bottom:.0f}")
            x_min, x_max = refine_axis(cal.x_min, cal.x_max, cal.invert_x, W, left, right)
            y_min, y_max = refine_axis(cal.y_min, cal.y_max, cal.invert_y, H, top, bottom)
            write({"x_min": x_min, "x_max": x_max, "y_min": y_min, "y_max": y_max})
        else:
            raw_cal = Calibration(4096, 4096, 0, 4095, 0, 4095, False, False, False)
            reader = TouchReader(path, raw_cal, q)
            reader.start()
            m = 40
            points = [(m, m), (W - m, m), (m, H - m), (W - m, H - m)]

            def prompt(d, idx, px, py):
                d.line((px - 15, py, px + 15, py), fill=(255, 255, 255), width=2)
                d.line((px, py - 15, px, py + 15), fill=(255, 255, 255), width=2)
                d.ellipse((px - 8, py - 8, px + 8, py + 8), outline=(255, 200, 0), width=2)
                d.text((W // 2 - 90, H // 2 - 8), f"Tap the cross ({idx + 1}/4)", fill=(255, 255, 255))

            try:
                samples = _collect_taps(fb, q, W, H, points, prompt)
            finally:
                reader.stop()
            (tl, tr, bl, br) = samples
            dx_screen = abs(tr[0] - tl[0]) + abs(br[0] - bl[0])
            dy_screen = abs(tr[1] - tl[1]) + abs(br[1] - bl[1])
            swap = dx_screen < dy_screen

            def pick(p):
                return (p[1], p[0]) if swap else p

            tl, tr, bl, br = (pick(p) for p in (tl, tr, bl, br))
            rx_left = (tl[0] + bl[0]) / 2
            rx_right = (tr[0] + br[0]) / 2
            ry_top = (tl[1] + tr[1]) / 2
            ry_bottom = (bl[1] + br[1]) / 2
            invert_x = rx_left > rx_right
            invert_y = ry_top > ry_bottom
            lo_x, hi_x = sorted((rx_left, rx_right))
            lo_y, hi_y = sorted((ry_top, ry_bottom))
            sx = (hi_x - lo_x) / max(1, (W - 2 * m))
            sy = (hi_y - lo_y) / max(1, (H - 2 * m))
            write({"swap_xy": swap, "invert_x": invert_x, "invert_y": invert_y,
                   "x_min": int(lo_x - sx * m), "x_max": int(hi_x + sx * m),
                   "y_min": int(lo_y - sy * m), "y_max": int(hi_y + sy * m)})
            print("Next: sudo pingaroo calibrate-touch --corners   (refines the edges from corner taps)")
    except KeyboardInterrupt:
        print("cancelled")
        return 1
    finally:
        fb.blit(Image.new("RGB", (W, H), (0, 60, 0)))
        fb.close()
    print("Restart the service to apply: sudo systemctl restart pingaroo")
    return 0


def cmd_preview(args) -> int:
    """Render sample screens to PNG files (no hardware needed)."""
    from .ui import Renderer
    from .sample_data import sample_snapshot, sample_storage, sample_extra
    cfg = _cfg(args)
    r = Renderer(cfg)
    os.makedirs(args.out, exist_ok=True)
    scenes = sample_snapshot()
    for name, snap in scenes.items():
        img, _ = r.render_main(snap, sample_storage(name), "")
        img.save(os.path.join(args.out, f"main_{name}.png"))
    snap = scenes["success_waiting"]
    page = 0
    while True:
        img, _, pages = r.render_details(snap, sample_storage("success_waiting"), sample_extra(), page)
        img.save(os.path.join(args.out, f"details_p{page + 1}.png"))
        page += 1
        if page >= pages:
            break
    print(f"wrote previews to {args.out}")
    return 0


# ---------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pingaroo", description="Pingaroo")
    p.add_argument("--config", "-c", default=None, help=f"config file (default {config_mod.DEFAULT_CONFIG_PATH})")
    p.add_argument("--version", action="version", version=f"pingaroo {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="run the app (used by systemd)")
    s.add_argument("--headless", action="store_true", help="no framebuffer/touch; worker + state file only")
    s.add_argument("--png", default=None, help="with --headless, write each frame to this PNG")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("once", help="run one diagnostic cycle now (service must be stopped)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_once)

    s = sub.add_parser("status", help="show live status from the running service")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    for name in ("start", "stop"):
        s = sub.add_parser(name, help=f"{name} the test loop in the running service (same as the touch button)")
        s.set_defaults(func=cmd_control, command=name)

    s = sub.add_parser("results", help="show recent CSV results")
    s.add_argument("--last", "-n", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_results)

    s = sub.add_parser("events", help="show recent JSONL events")
    s.add_argument("--last", "-n", type=int, default=30)
    s.add_argument("--raw", action="store_true")
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("label", help="show or set the configuration label")
    s.add_argument("name", nargs="?")
    s.set_defaults(func=cmd_label)

    s = sub.add_parser("labels", help="show or set the list of predefined labels for the Next Label button")
    s.add_argument("names", nargs="*")
    s.set_defaults(func=cmd_labels)

    s = sub.add_parser("interval", help="show or set the completion-to-next-start interval in seconds")
    s.add_argument("seconds", nargs="?")
    s.set_defaults(func=cmd_interval)

    s = sub.add_parser("server", help="show or set the speedtest server id ('auto' to unpin)")
    s.add_argument("server_id", nargs="?")
    s.set_defaults(func=cmd_server)

    s = sub.add_parser("sleep", help="show or set screen sleep seconds ('off' to disable)")
    s.add_argument("seconds", nargs="?")
    s.set_defaults(func=cmd_sleep)

    s = sub.add_parser("autostart", help="show or set autostart of the test loop at boot (on/off)")
    s.add_argument("value", nargs="?")
    s.set_defaults(func=cmd_autostart)

    s = sub.add_parser("wifi", help="show or set the SSID / NetworkManager profile under test")
    s.add_argument("--current", action="store_true", help="use the Wi-Fi network the Pi is connected to right now")
    s.add_argument("--ssid", default=None)
    s.add_argument("--profile", default=None, help="NetworkManager connection name (defaults to the SSID)")
    s.add_argument("--home-current", action="store_true", help="remember the Wi-Fi the Pi is on now as the management (home) network")
    s.add_argument("--switch", choices=["home", "test"], default=None, help="move the radio to the home or test network now")
    s.set_defaults(func=cmd_wifi)

    s = sub.add_parser("export", help="write an Excel workbook (results + per-label summary)")
    s.add_argument("--out", default=None, help="output .xlsx path")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("router-password", help="store the router admin password (root-only file)")
    s.set_defaults(func=cmd_router_password)

    s = sub.add_parser("router-probe", help="fetch router status JSON for adapter discovery")
    s.add_argument("paths", nargs="*", help="URL paths to GET (default: configured status_paths)")
    s.add_argument("--no-login", action="store_true")
    s.add_argument("--max-chars", type=int, default=6000)
    s.set_defaults(func=cmd_router_probe)

    s = sub.add_parser("splash", help="replay the 8-bit boot splash")
    s.add_argument("--seconds", type=float, default=4.0)
    s.add_argument("--out", default=None, help="write PNG frames here instead of drawing")
    s.set_defaults(func=cmd_splash)

    s = sub.add_parser("band", help="lock the test Wi-Fi to 2.4/5 GHz or auto (NCM1120 admin needs 2.4)")
    s.add_argument("choice", nargs="?", choices=["2.4", "2", "5", "auto"])
    s.set_defaults(func=cmd_band)

    s = sub.add_parser("config", help="print the effective configuration")
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("check-wifi", help="interface, profile, WPA3 capability and route checks")
    s.set_defaults(func=cmd_check_wifi)

    s = sub.add_parser("check-display", help="list framebuffers; --test draws a pattern")
    s.add_argument("--test", action="store_true")
    s.set_defaults(func=cmd_check_display)

    s = sub.add_parser("check-touch", help="print mapped touch coordinates")
    s.add_argument("--seconds", type=int, default=20)
    s.add_argument("--moves", action="store_true", help="also print move events")
    s.set_defaults(func=cmd_check_touch)

    s = sub.add_parser("calibrate-touch", help="touch calibration (stop the service first)")
    s.add_argument("--corners", action="store_true", help="refine the current calibration from taps at the 4 corners")
    s.set_defaults(func=cmd_calibrate_touch)

    s = sub.add_parser("preview", help="render sample screens to PNG files")
    s.add_argument("--out", default="preview")
    s.set_defaults(func=cmd_preview)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
